# 连接库保存失败保留原有知识文件

日期：2026-10-23

ConnectionLibrary.save 先截断现有 JSON 再序列化。真实连接库新增一个不可序列化的 tags 值后，保存抛 TypeError，原本可读的连接库已被半截 JSON 替代；普通写入异常也会破坏旧文件。

每次保存独占同目录短名临时文件，写完 flush/fsync 后原子替换；异常只清理自己创建的临时件并保留旧文件。保留原返回值、编码、链接指向和旧文件权限位，不新建缺失父目录。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 ConnectionLibrary.add_record/save/load 往返；不可序列化值、受控写入异常及 replace 拒绝分别核对旧文件字节。两线程通过 Event 控制真正的 replace 次序，检验临时件隔离；有效保存、新目标、名字碰撞作对照。
修前 main 固定为 `cad16dc5e142a850c86a88915b76a8aca054d84d`；两平台验证提交 `105b49006c0e546611278df472708365470e54dd`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 6 failed, 2 passed in 3.54s | 8 passed in 0.40s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 6 failed, 2 passed in 3.86s | 8 passed in 0.92s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| truncate_the_target_while_serializing | test_serialization_failure_preserves_existing_library | failed serialization destroyed the previous connection library |
| allow_temp_name_reuse | test_exclusive_name_collision_never_consumes_another_file | DID NOT RAISE |
| leave_failed_first_save_published | test_failed_new_save_leaves_no_target | failed first save published a partial library |

- Windows：`10 passed in 0.86s`，0 skipped。
- Linux：`10 passed in 6.50s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_connection_library_atomic_save.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/connection_library_atomic_save_mutations.json --baseline cad16dc5e142a850c86a88915b76a8aca054d84d --mutate
python -X utf8 -m pytest tests/test_hex_query.py tests/test_query_order_invariance.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

本件独立核对 ConnectionLibrary.save 的当前实现。原子替换及独占临时件沿用仓内 hex_cnn.save_consolidated 的既有模式，保留原贡献；不是重报 #393 的固化核保存，也不改 save_report 的报告输出。

不承诺并发内容合并、调用顺序或断电后目录项持久化；成功 replace 的最后一次结果为准。权限只保留 mode，不保留属主/ACL；未实测磁盘满或断电。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。

首批定点变异在建立旧文件夹具时提前触发缺文件异常，未命中指定断言，不计作通过。旧文件夹具改为独立写入合法 JSON，保留保存行为断言后重新验证；生产补丁未变，首批失败证据保留本地。
