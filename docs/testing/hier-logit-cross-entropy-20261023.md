# 分层交叉熵在 logits 域保留纠错信号

日期：2026-10-23

ce_loss 在 softmax 概率上加 1e-12，把高置信错判损失截在约 27.631。真类/错类 logits 从 -50/+50 到 -100/+100 时读数不再增加；真实 train_hier 在这类有限参数状态下会出现零更新。形状辅助项存在相同截断。

使用减最大值的 logsumexp 在 logits 域计算物体交叉熵；形状项按既有连续三颜色分组，取全部 logits 与真形状组的 logsumexp 差。保持 softmax 推断接口、分组、辅助项权重、训练采样和更新规则。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 ce_loss 与 HexHierNet/train_hier；极端有限 logits 的数学参考、平移不变性、普通范围对照；真实 RGB 晶格高置信错判训练逐参数和最终数学目标对账，独立核对形状边缘化项。
修前 main 固定为 `cad16dc5e142a850c86a88915b76a8aca054d84d`；两平台验证提交 `7c0e004f287cca561cc55d939eb340f914641f99`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 7 failed, 2 passed in 0.54s | 9 passed in 0.39s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 7 failed, 2 passed in 0.75s | 9 passed in 0.57s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| restore_probability_epsilon_ce | test_confident_wrong_logits_do_not_saturate[100.0] | cross entropy saturated instead of tracking the wrong-logit gap |
| clip_shape_group_probability | test_shape_auxiliary_loss_retains_low_probability_group | shape auxiliary objective was clipped or grouped incorrectly |
| use_interleaved_shape_classes | test_shape_auxiliary_loss_retains_low_probability_group | shape auxiliary objective was clipped or grouped incorrectly |

- Windows：`5 passed in 92.78s (0:01:32)`，0 skipped。
- Linux：`5 passed in 77.63s (0:01:17)`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_hier_logit_cross_entropy.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/hier_logit_cross_entropy_mutations.json --baseline cad16dc5e142a850c86a88915b76a8aca054d84d --mutate
python -X utf8 -m pytest tests/test_hex_hier.py::test_scene_labels_complete tests/test_hex_hier.py::test_l1_energy_has_spatial_structure tests/test_hex_hier.py::test_l4_position_accuracy_above_random tests/test_hex_hier.py::test_hier_training_learns_object tests/test_hex_hier.py::test_shape_marginal_groups_contiguous_classes -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

原报告、最小反例和 logsumexp 建议来自 @Eru07734 的 [Issue #332](https://github.com/FuRongJun-1999/lingshu/issues/332)，维护者已独立确认。本件交付当前基线的补丁与守卫/定点变异，不把原反例计作新发现，不重报 #73 的整体准确率或 #335 的 final_D 报告问题。

不宣称默认初始化必然进入饱和状态或任务准确率提升；数学目标在饱和区恢复真实值，旧损失曲线数值不能直接混比。既有 final_D 仍记录更新前采样损失，本件不改变该独立报告语义。未做长训练/真实任务评测。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。
