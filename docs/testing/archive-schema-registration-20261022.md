# 归档登记表参与迁移与备份恢复

日期：2026-10-22

PR #850 新增 archive_state 及其删除/归档清理调用，但未登记迁移结构与版本。旧 v3 库重开仍被判为结构齐全，delete_node 随后报 no such table: archive_state。全库备份也漏掉该表，恢复后已保存的归档前 importance 消失，原先可逆的 .15→.1→.15 往返变成 .15→.1→.1。

将 schema 升为 v4，把 archive_state 登记进启动结构对照和 M13 备份清单，复用现有幂等建表及事务内恢复路径。新增表恢复时复用 store 的共享层角色判据，防止新恢复入口改写 SUB 无权还原的共享节点。旧备份没有该表时保留原先清标签且不猜原值的降级语义。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 SQLite 文件库：建立旧 v3 等价结构后重开，实际执行删除；另测版本号已匹配而结构缺表的修复。公开 forget_advisor/export_all/import_all/restore_archived 完成归档往返，真实 PRIMARY/SUB 实例验证恢复权限；不替换数据库或返回结果。
修前 main 固定为 `1eafb3f57a3f9a83d067c0a39568aca4588cfdff`；两平台验证提交 `29f7baaf5da171998afd82ab7c0975637a823a2d`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 3 failed, 3 passed in 1.15s | 6 passed in 1.18s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 3 failed, 3 passed in 3.54s | 6 passed in 3.50s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| ignore_archive_schema_structure | test_current_version_with_missing_table_is_repaired | archive_state missing after structural migration check |
| omit_archive_backup_rows | test_backup_roundtrip_preserves_pre_archive_importance | backup omitted the pre-archive restoration state |
| ignore_archive_import_role | test_shared_archive_state_cannot_be_imported_by_sub | SUB import wrote shared archive state |

- Windows：`23 passed, 169 warnings in 4.79s`，0 skipped。
- Linux：`23 passed, 169 warnings in 15.12s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值、断言或豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_archive_schema_registration.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/archive_schema_registration_mutations.json --baseline 1eafb3f57a3f9a83d067c0a39568aca4588cfdff --mutate
python -X utf8 -m pytest tests/test_issue448_archive_reversibility_guard.py tests/test_core_legacy_schema_migration.py tests/test_issue270_m13_table_coverage_guard.py tests/test_issue279_startup_migration_shortcircuit_guard.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

本件补齐 [PR #850](https://github.com/FuRongJun-1999/lingshu/pull/850) 新增归档表的注册遗漏；[原 #448](https://github.com/FuRongJun-1999/lingshu/issues/448) 的归档/还原 API 和已有守卫保持其原贡献。本件不重报原先没有逆操作的发现，也不把已有迁移器、备份器或角色判据计作新实现。与 [#270](https://github.com/FuRongJun-1999/lingshu/issues/270) 的旧表备份覆盖同用既有清单，不另造导出协议。

旧备份此前已经丢失的归档前原值无法凭空恢复，仍须源库或其它记录；本件只登记这张内建表，不扩展证据账本/TMS/激活等可选模块的全库备份策略。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。

首次权限夹具缺少 STNode 必填参数，修正为真实 set_anchor 入口后重跑定向批；失败记录保留本地，不计作通过或业务缺陷。
