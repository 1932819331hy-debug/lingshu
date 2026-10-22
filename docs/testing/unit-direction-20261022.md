# 单元头增益不再翻转声明方向

日期：2026-10-22

UnitHeadNet 初始化同时把符号存入 dir 和 gain，前向再相乘，使负连接变为正连接。当前 main 的真实 HexHierNet 上，36 条选择连接有 17 条方向错误；训练的有符号增益还可再次翻转已声明方向。

增益只保存非负幅度，方向由既有 dir 单独承担；set_vec 将增益限定在 0..GAIN_BOUND，偏差仍按原语义保存。保留冻结连接、前端与有限差分训练入口。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 HexHierNet、UnitHeadNet 与 finetune_units；检验选中负连接的抑制作用、初始化方向、显式载入边界、训练后方向，以及无连接和前端冻结对照。不用桩模型替代网络前向。
修前 main 固定为 `ef6f413dd5ffaa91b419b359dc6edcfc872bffed`；两平台验证提交 `95772933f7b6f9430bcfafdba48a7fa33547cd82`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 4 failed, 2 passed in 0.50s | 6 passed in 0.37s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 4 failed, 2 passed in 0.52s | 6 passed in 0.43s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| restore_signed_initial_gain | test_initial_connection_directions_match_the_real_base_network | initial inhibitory connections changed direction |
| allow_negative_loaded_gain | test_loading_negative_gain_cannot_flip_a_declared_direction | negative loaded gain reversed a declared connection |
| discard_declared_direction_in_forward | test_inhibitory_feature_lowers_its_card_logit | declared inhibitory feature raised its card logit |

- Windows：`25 passed in 185.32s (0:03:05)`，0 skipped。
- Linux：`25 passed in 147.55s (0:02:27)`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_unit_direction.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/unit_direction_mutations.json --baseline ef6f413dd5ffaa91b419b359dc6edcfc872bffed --mutate
python -X utf8 -m pytest tests/test_issue734_hex_unit_optional_import.py tests/test_hex_train.py tests/test_hex_hier.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

修复依据来自 @EricEricEr 的 [原 Issue #129](https://github.com/FuRongJun-1999/lingshu/issues/129)，维护者已独立确认但本轮 main 尚未修复。原发现、最小反例及方向/幅度建议保留原贡献；本件提供当前基线的真实网络复现、补丁和定向验证，不把原报告计作新发现。Optional 注解及参数复制既有修复不重报。

不宣称分类准确率或真实任务质量提升；既有已训练负 gain 的向量在新契约下会钳制为 0，若需保留原有效权重须由调用方显式迁移方向声明。本件不提供旧向量自动迁移。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。
