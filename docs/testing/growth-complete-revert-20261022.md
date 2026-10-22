# 训练生长撤销恢复输出层与实际损失

日期：2026-10-22

train_infogap 生长验证会更新全部参数，REVERT 却只恢复 conv、mix 和 K，留下输出层 fc 漂移。当前 main 的真实单步训练触发 REVERT 后有 6 个 fc 参数仍变化；记录的 D 也来自被否决候选。

在生长前一并快照输出层，否决时完整还原；REVERT 的 D 记录还原后状态的既有 d_before，另保留 attempt_D 记录被否决候选。训练更新、验证阈值、抽样与 GROW 语义不变。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 HexNet 与 train_infogap，小型确定性合成晶格；只在 grow_kernel 外记录真实调用前状态、不替换损失或训练结果。用公开 retain_gain 显式强制否决/采纳，逐参数与预测、报告读数对账。
修前 main 固定为 `ef6f413dd5ffaa91b419b359dc6edcfc872bffed`；两平台验证提交 `e23b63d6ccd07a48911155f52c5103285e5f1c35`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 4 failed, 2 passed in 0.50s | 6 passed in 0.39s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 4 failed, 2 passed in 0.52s | 6 passed in 0.44s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| leave_output_updates_after_revert | test_revert_restores_the_output_parameters | rejected growth left output parameter drift |
| restore_an_unrelated_output_snapshot | test_revert_restores_the_predictions_at_the_growth_boundary | rejected growth changed restored predictions |
| report_the_rejected_candidate_loss | test_revert_reports_the_restored_loss_and_keeps_the_rejected_reading | REVERT reported the rejected candidate loss |

- Windows：`6 passed in 4.12s`，0 skipped。
- Linux：`6 passed in 14.15s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_growth_complete_revert.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/growth_complete_revert_mutations.json --baseline ef6f413dd5ffaa91b419b359dc6edcfc872bffed --mutate
python -X utf8 -m pytest tests/test_hex_train.py::test_branch_deadzone_freezes tests/test_hex_train.py::test_growth_verified_and_retained tests/test_hex_train.py::test_growth_reverted_on_random_labels tests/test_hex_train.py::test_convergence_deadzone_stops_recursion tests/test_hex_conv_fusion.py tests/test_hex_conv_fusion_matrix.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

修复依据来自 @One1turn 的 [原 Issue #41](https://github.com/FuRongJun-1999/lingshu/issues/41)，维护者已确认输出层漏回滚。原发现保留其贡献；本件在当前 main 的完整训练入口复现并交付补丁/守卫。参数复制、核族重复和 Rust 采样问题不计作本件工作。

只修一次正常返回的 REVERT 边界，不承诺训练异常中断的自动回滚、并发训练隔离或完整 RNG 状态回退；合成任务不是分类能力评测。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。

首批重复撤销夹具默认停滞条件只触发 2 次，未满足测试要求的 3 次；改为公开 stall_eps=1.0 明确触发三次，保持三次撤销、参数和预测完整回滚断言。失败批保留本地，不计作通过；没有更改生产默认参数或放宽既有断言。
