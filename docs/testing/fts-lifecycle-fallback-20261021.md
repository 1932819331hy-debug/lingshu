# FTS 跟踪失效时回退真实检索

日期：2026-10-21

启用 FTS 的实例仍存活时，另一个使用默认配置的实例会拆除共享库的 FTS 触发器并清空派生索引。前者仍持有 FTS 对象，后续检索把空索引结果当成正常空命中，连库内逐字存在的内容也不可见。

在 FTS 同步和读取前核对三条跟踪触发器，并以 SQLite schema_version 检查一次读取期间的派生结构生命周期变化。失效时抛出既有 SQLite 错误，由现有检索入口回退 LIKE；不重建或抢回另一实例关闭的全库索引，不新增依赖或改排序阈值。

## 复现与判据

建议优先级 P1，判定为部分验证。使用正常引擎实例、真实 SQLite FTS5、文件库和公开 search_content 入口；拆除来自默认实例的实际初始化，局部失效腿直接拆一条派生触发器。
修前 main 固定为 `f5dcd58165cbf64393d1f2d4dfc61f781485ec29`；两平台验证提交 `b049148d628fa0bf9cf1f4e411b62dc202c98507`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 4 failed, 2 passed in 1.02s | 6 passed in 0.98s | 4/4 指定用例及断言命中 |
| Linux / Python 3.13.12 | 4 failed, 2 passed in 3.19s | 6 passed in 3.30s | 4/4 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| trust_detached_index | test_default_client_does_not_hide_old_or_new_memories[indexed] | detached FTS hid a persisted new memory |
| accept_partial_tracking | test_partial_tracking_loss_falls_back_for_updated_content | partial FTS tracking loss hid corrected content |
| ignore_lifecycle_epoch | test_lifecycle_change_during_candidate_read_is_rejected | FTS lifecycle change was accepted as a valid index read |
| commit_caller_before_sync | test_index_read_preserves_caller_transaction | FTS read committed its caller |

- Windows：`39 passed, 16 warnings in 6.64s`，0 skipped。
- Linux：`39 passed, 16 warnings in 19.26s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改门禁、阈值、断言或豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。FTS 生产文件、守卫与规格在合入上游上下文、子树和查看器更新后指纹均不变，本地证据复用，最终 head 的 CI 单列整合结果。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_fts_lifecycle_fallback.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/fts_lifecycle_fallback_mutations.json --baseline f5dcd58165cbf64393d1f2d4dfc61f781485ec29 --mutate
python -X utf8 -m pytest tests/test_fts_retrieval.py tests/test_fts_context.py tests/test_retrieval_no_touch.py tests/test_issue707_retrieval_doc_shape_guard.py tests/test_issue613_retrieval_determinism_guard.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

FTS5/BM25 来自 WASD258-jpg 的 [PR #447](https://github.com/FuRongJun-1999/lingshu/pull/447)，已在 bounty #284 交付。对照 [lingshu #797](https://github.com/FuRongJun-1999/lingshu/issues/797)、[PR #811](https://github.com/FuRongJun-1999/lingshu/pull/811) 的写入后索引行数问题。本件是存活实例跨开关的派生状态失效；默认配置实例的写入没有 `_fts` 对象，单在启用实例的 add_node 同步不能覆盖本情形。保留既有实现贡献，不重报 #797 或把新索引算法作为本件成果。

回退沿用既有 LIKE 候选与排序，未改善其大库召回上限，未实现跨进程开关协调或自动重建，未声明 FTS 吞吐提升。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。
