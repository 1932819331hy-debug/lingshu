# 激活实例固定构造时的数据库路径

日期：2026-10-22

ActivationEngine 把相对 db_path 保存在实例中，每次读写都按当前工作目录重新连接。同一实例在进程切换目录后会改连另一同名文件：没有激活表时报错，有同名工作态时可能覆盖另一个库的会话。

仅把自有文件连接路径在构造时固定为绝对路径。初始相对路径仍按构造时 cwd 选择原位置；store/conn 借用路径和 :memory: 的既有显式契约保持不变。所有重连统一使用这一已固定路径。

## 复现与判据

建议优先级 P1，判定为部分验证。两个真实目录各有同名 SQLite 图库和不同会话数据；构造一个相对路径激活实例后真实 chdir，对比工作态查询、快照导入及 activate 的读写落在哪个文件。独立连接读取两库，另测借用连接事务归属和构造期路径选择。
修前 main 固定为 `1eafb3f57a3f9a83d067c0a39568aca4588cfdff`；两平台验证提交 `e87fd4f`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 3 failed, 2 passed in 1.61s | 5 passed in 1.52s | 2/2 指定用例及断言命中 |
| Linux / Python 3.13.12 | 3 failed, 2 passed in 4.14s | 5 passed in 4.24s | 2/2 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| retain_relative_database_path | test_import_after_chdir_does_not_overwrite_the_other_database | activation write moved to another cwd database |
| reconnect_by_basename | test_all_workset_readers_stay_on_the_original_database | activation read moved to another cwd database |

- Windows：`22 passed in 2.80s`，0 skipped。
- Linux：`22 passed in 9.40s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_activation_bound_path.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/activation_bound_path_mutations.json --baseline 1eafb3f57a3f9a83d067c0a39568aca4588cfdff --mutate
python -X utf8 -m pytest tests/test_activation_transaction_ownership.py tests/test_activation_empty_graph.py tests/test_issue382_relative_dbpath_guard.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

关联 [#293](https://github.com/FuRongJun-1999/lingshu/issues/293) 的激活连接契约、[#382](https://github.com/FuRongJun-1999/lingshu/issues/382) 的 core 路径固定先例。#293 的内存库支持/父目录创建、#382 的 LayeredStore 修复保留原贡献；本件只补独立 ActivationEngine 文件连接在构造后跨 cwd 漂移的残留，不改默认数据目录。

不新增 SQLite URI 支持或改变默认数据根；未把审计输出路径纳入数据库路径固定策略，未宣称线程共享、跨进程同步或吞吐问题同时解决。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。

时间核现有登记只从 activation.py:335 同步到 336，23 条登记与数学语句不变；同步后仅重跑 lint，复用已通过的守卫、变异、相关回归和清单检查。
