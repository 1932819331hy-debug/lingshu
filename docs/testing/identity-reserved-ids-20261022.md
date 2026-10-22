# 身份分配避开本帧全部保留 ID

日期：2026-10-22

身份跟踪虽把本帧显式 ID 加进 taken，随机分配新 ID 时却只查 self.nodes。匿名观测先于尚未入库的显式观测时，种子相同的两个正常实例即可确定性分到同一个 ID，observed=2 而实体仅剩 1；八次重试后的 12 位兜底也没有查重。

新 ID 同时避开现有节点和本帧 taken。原八次随机短 ID 重试策略保留，长 ID 兜底也查重，碰撞后用确定性后缀寻找空位，不依赖继续随机碰运气。最近邻匹配和正常无碰撞 ID 格式保持原行为。

## 复现与判据

建议优先级 P1，判定为部分验证。正常双模型同 seed，先从一个模型取得真实显式 ID，再向另一个模型输入匿名在前的混合帧，观察两条观测是否仍为两实体。仅重试耗尽腿替换随机数源构造必然碰撞，不替换身份方法、观测写入或结果；包含既有 ID、保留长 ID 和合法匹配对照。
修前 main 固定为 `1eafb3f57a3f9a83d067c0a39568aca4588cfdff`；两平台验证提交 `c3dfa73ef2c496c0afa03f2a9e24a82682a2cac4`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 3 failed, 3 passed in 0.36s | 6 passed in 0.23s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 3 failed, 3 passed in 0.46s | 6 passed in 0.33s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| ignore_reserved_short_ids | test_anonymous_allocation_cannot_consume_a_later_explicit_id | anonymous allocation consumed a reserved explicit ID |
| ignore_existing_long_ids | test_long_fallback_cannot_overwrite_an_existing_entity | long fallback ID overwrote an existing entity |
| ignore_reserved_long_ids | test_long_fallback_cannot_consume_a_later_explicit_id | long fallback consumed a reserved explicit ID |

- Windows：`35 passed in 1.00s`，0 skipped。
- Linux：`35 passed in 8.69s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_identity_reserved_ids.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/identity_reserved_ids_mutations.json --baseline 1eafb3f57a3f9a83d067c0a39568aca4588cfdff --mutate
python -X utf8 -m pytest tests/test_track_identity_collision.py::test_matched_branch_still_reuses_nearby_eid tests/test_wm_identity_one_to_one.py tests/test_world_observation_contracts.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

补齐 [#137](https://github.com/FuRongJun-1999/lingshu/issues/137) 的 ID 唯一性和 [#226](https://github.com/FuRongJun-1999/lingshu/issues/226) 的同帧一一身份契约。既有短 ID 查 self.nodes、taken 预留和身份匹配实现保留原贡献；本件不重报生日悖论或原始多对一发现，只修分配阶段漏查保留集及兜底。

最近邻仍是既有启发式，未引入全局多目标分配；调用方重复声明同一显式 ID 的语义未扩展，未用大规模随机试验冒充实际碰撞频率。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。
