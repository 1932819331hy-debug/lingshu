# 旧子树枚举不完整时拒绝替换

日期：2026-10-21

subgraph_replace 把有 500 个后代、64 层预算的 traverse 结果直接当成完整删除集合。501 个直接孩子、被预算漏掉的受保护孙节点、65 层链三种正常图，都能先删旧祖先再返回成功，遗漏节点成为孤儿；单纯把挂入和删除放进同一事务无法发现这种成功但不完整的替换。

在任何新树写入和旧点删除之前，逐批读取已收集节点的 hierarchical 入边，确认所有孩子仍在待删集合内。发现集合外孩子即抛 ValueError，整笔拒绝；闭合的 500 后代或 64 层边界图仍可完整替换。复用 _SQL_IN_CHUNK 与入口写快照，不增加无界遍历，不改只读 traverse 的查询语义。

## 复现与判据

建议优先级 P1，判定为部分验证。文件 SQLite 库、公开 add_perception/add_edge/protect_node 与真实 subgraph_replace 入口，无替换结果的 mock。失败判据要求在任何挂入前明确拒绝，nodes/edges/protections/action_logs/archive_state 的逐行快照和交互计数均不变；正面对照验证恰好 500 后代、64 层，以及非 hierarchical 关联不被误拒绝。
修前 main 固定为 `1eafb3f57a3f9a83d067c0a39568aca4588cfdff`；两平台验证提交 `2ce9ae35059d5041b20acd20c07911f7f965b41e`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 3 failed, 3 passed in 1.32s | 6 passed in 0.95s | 2/2 指定用例及断言命中 |
| Linux / Python 3.13.12 | 3 failed, 3 passed in 4.03s | 6 passed in 3.41s | 2/2 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| ignore_incomplete_closure | test_fanout_beyond_500_is_rejected_without_any_writes | incomplete subtree was replaced instead of rejected |
| check_only_old_root_boundary | test_depth_65_is_rejected_and_all_ancestor_edges_survive | incomplete subtree was replaced instead of rejected |

- Windows：`30 passed, 153 warnings in 4.52s`，0 skipped。
- Linux：`30 passed, 153 warnings in 19.66s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值、断言或豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。两平台各完成一批基线/修复/变异和相关回归，未为公开证据整理追加本地测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_subtree_closure_guard.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/subtree_closure_mutations.json --baseline 1eafb3f57a3f9a83d067c0a39568aca4588cfdff --mutate
python -X utf8 -m pytest tests/test_issue139_subgraph_replace_guard.py tests/test_issue139_subgraph_replace_validate_guard.py tests/test_subtree_write_snapshot.py tests/test_issue665_nested_tx_savepoint.py tests/test_transaction_exit_rollback.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

本件交付的是 cyd471 [lingshu #139](https://github.com/FuRongJun-1999/lingshu/issues/139) 已报告、当前方法仍明确标注未修的 #139-G，不声称重新发现。采用仓内《删除恢复持久化完整性_修复设计_v0.1.md》S-4=(b) 推荐的超界整体拒绝形状。前置字段失败已由 LarryE135/维护者修复并在 bounty #684 交付；[PR #843](https://github.com/FuRongJun-1999/lingshu/pull/843) / bounty #1362 只把范围预检与写入置于同一快照，其公开边界明确排除 500/64 截断，本件保留并依赖该事务，不重复交付或计分其竞态修复，也不关闭涵盖共享后代等更广问题的 #139。

这是有界拒绝策略：超出当前枚举预算的旧树需缩小替换范围，尚未支持任意大树的分页替换。补充检查只读最多 501 个已收节点的邻接边，边数及原 traverse 高扇出开销仍随实际图增长，未测吞吐。父范围查询仍有既有预算，未修共享后代删除策略、跨父环语义、进程内计数回滚等其它 #139 边界。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。
