# 真值传播不从残留证据恢复已删除前提

日期：2026-10-22

TMS 对闭包外缺失前提返回 0，对作为传播根的同一已删除前提却重新读取残留证据，恢复出 0.666667 的信念。实际删除后显式 propagate([前提])，下游仍为 0.5，报告却同时标记 undermined，持久值与裁决不一致。

传播初值先识别不在当前节点表中的根/闭包成员，缺失节点的 own confidence 按既有外部前提口径归 0；保留账本和依据审计记录、合取/析取、独立佐证及事务边界。

## 复现与判据

建议优先级 P1，判定为部分验证。真实文件 SQLite、公开引擎写入/依据登记/删除及 TruthMaintenance.propagate；保留删除前证据，核对返回值、文件重开后的下游置信度和报告。包括缺失根、缺失链、存活替代依据与独立佐证对照。
修前 main 固定为 `ef6f413dd5ffaa91b419b359dc6edcfc872bffed`；两平台验证提交 `d3c825cd57d44d6dd0c53fd9325a2301d8ae90ee`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 4 failed, 2 passed in 1.00s | 6 passed in 0.86s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 4 failed, 2 passed in 2.87s | 6 passed in 2.78s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| trust_retained_evidence_for_missing_roots | test_deleted_root_cannot_recover_confidence_from_retained_evidence | deleted propagation root recovered stale evidence confidence |
| give_unknown_roots_the_ledger_prior | test_unknown_propagation_root_has_no_implicit_prior | unknown propagation roots received a prior |
| persist_a_default_missing_belief | test_reopened_database_agrees_with_the_undermined_report | persisted conclusion retained a deleted premise belief |

- Windows：`49 passed in 6.93s`，0 skipped。
- Linux：`49 passed in 17.87s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_tms_missing_roots.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/tms_missing_roots_mutations.json --baseline ef6f413dd5ffaa91b419b359dc6edcfc872bffed --mutate
python -X utf8 -m pytest tests/test_tms.py tests/test_tms_truth_maintenance_guard.py tests/test_extension_transaction_ownership.py tests/test_evidence_ledger.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

基于既有证据账本/TMS（[PR #447](https://github.com/FuRongJun-1999/lingshu/pull/447)）及 [PR #820](https://github.com/FuRongJun-1999/lingshu/pull/820) 的事务原语。原算法、阈值与事务归属均保留原贡献；本件只收口已删除节点在闭包内外的信念口径，不重报原置信度放大或事务越权。

未新增节点删除自动触发 TMS 的钩子：调用方仍须显式请求传播；不清除审计证据/依据行，不处理跨进程通知。未验证真实记忆库或 DSH 宿主。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。
