# 解析后的观测进入全部预测缓冲

日期：2026-10-22

perceive 本体已返回解析后的真实观测，但三层预测包装仍遍历调用方原始输入。匿名观测没有 eid、默认传感器调用的输入为 None，世界历史已经记录真实帧，轨迹/配对误差/旋转缓冲却漏掉这些帧；实际探针为 3 个历史帧、1 个实体、0 个轨迹样本。

三层包装统一消费本体输出的 observations，使用同一已解析实体 ID、真实坐标与四元数。轨迹的上一位置从追加前缓冲读取，保留误差配对和只追加真实观测的契约；显式 ID、tick/timestamp 转发、空观测和工厂绑定方式保持原行为。

## 复现与判据

建议优先级 P1，判定为部分验证。公开 UnifiedWorldModel.perceive/generate 入口，分别用匿名输入、真实 SceneSimulator 默认传感器输入、显式 ID 对照。逐 tick 对账 history 与轨迹投影，检验真实静止预测后下一帧的配对误差，以及已启用旋转通道的匿名四元数；不模拟观测结果或预测器。
修前 main 固定为 `1eafb3f57a3f9a83d067c0a39568aca4588cfdff`；两平台验证提交 `d36a3cc5003aa22511394c559a17554882a1c662`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 4 failed, 2 passed in 0.38s | 6 passed in 0.22s | 3/3 指定用例及断言命中 |
| Linux / Python 3.13.12 | 4 failed, 2 passed in 0.47s | 6 passed in 0.33s | 3/3 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| use_unresolved_trajectory_input | test_anonymous_real_observations_reach_the_trajectory_buffer | anonymous observations missing from trajectory buffer |
| use_unresolved_paired_guard_input | test_anonymous_prediction_errors_reach_the_paired_guard | resolved observation missing from paired guard |
| use_unresolved_rotation_input | test_anonymous_quaternions_reach_the_resolved_rotation_track | resolved observation missing from rotation buffer |

- Windows：`47 passed in 1.58s`，0 skipped。
- Linux：`47 passed in 9.24s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_trajectory_resolved_observations.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/trajectory_resolved_observations_mutations.json --baseline 1eafb3f57a3f9a83d067c0a39568aca4588cfdff --mutate
python -X utf8 -m pytest tests/test_world_observation_contracts.py tests/test_world_inference_window.py tests/test_world_period_silent_wrong_guard.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

既有三端口世界模型、预测包装链与 [PR #516](https://github.com/FuRongJun-1999/lingshu/pull/516) 的签名/恢复轨迹修复保留原贡献。#516 解决转发与重启历史回填，本件补的是运行中包装仍读取未解析输入的失配；不重报原签名缺参或把周期预测算法记作本件成果。

轨迹仍受既有 256 样本上限约束，旋转开关及物理可选层默认值不变；不宣称真实场景准确率提升，先前已经漏写的缓冲需由既有历史恢复入口回填。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。
