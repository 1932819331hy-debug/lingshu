# 经验恢复校验完成后再替换运行态

日期：2026-10-22

load_priors 逐字段直接改写活动世界，再读取后续字段。收据缺少 motion_estimates 等后段字段时抛 KeyError，但原世界已经被替换：实际原实体 existing 在恢复失败后变为 new，调用方无法继续使用原运行态。

在独立候选视图上构建节点、关系、历史投影与运行字段；全部恢复步骤成功后才更新原模型及循环字段，保留原模型对象身份。失败继续按既有异常面抛出，原运行态逐字段保留。

## 复现与判据

建议优先级 P1，判定为部分验证。真实 SimulationLoop/UnifiedWorldModel 的 flush_payloads→load_priors 往返；隔离文件提供 read_content 收据协议，不替换世界恢复方法。分别扰动后段字段/历史投影/假设记录，检验异常后模型、预测和循环状态；有效收据与 limit 截断作对照。
修前 main 固定为 `ef6f413dd5ffaa91b419b359dc6edcfc872bffed`；两平台验证提交 `02693e3bd5caae2aba08434045abcaa977807a16`。

| 平台 | 同一守卫在修前 | 修复后 | 具名变异 |
|---|---|---|---|
| Windows / Python 3.12.10 | 5 failed, 3 passed in 0.55s | 8 passed in 0.37s | 4/4 指定用例及断言命中 |
| Linux / Python 3.13.12 | 5 failed, 3 passed in 0.84s | 8 passed in 0.64s | 4/4 指定用例及断言命中 |

全部最终守卫、修前对照与变异无 error、SKIP 或超时；变异恰好命中指定单条用例及断言。

| 变异 | 指定用例 | 指定断言 |
|---|---|---|
| stage_directly_on_the_running_model | test_failed_restore_preserves_the_entire_running_state[missing_motion] | failed prior restore changed running state |
| publish_before_hypothesis_validation | test_failed_restore_preserves_the_entire_running_state[bad_hypothesis] | failed prior restore changed running state |
| publish_loop_state_before_final_field | test_failed_restore_preserves_the_entire_running_state[missing_rates] | failed prior restore changed running state |
| replace_the_externally_held_model | test_valid_restore_preserves_the_held_model_object_and_observation_boundary | restoration replaced the held model object |

- Windows：`55 passed in 8.61s`，0 skipped。
- Linux：`55 passed in 14.54s`，0 skipped。

严格登记 64/64 MATCH，时间核无新增违规；未改既有门禁、阈值或断言，未新增豁免。本地仅跑上述受影响面，完整门禁交由最终 PR head 的远端 CI。不为整理材料重复跑稳定测试。以下只统计最终有效定向批次，未为整理证据重跑稳定测试。

安装 `.[full,dev]` 后，在上述固定验证提交的仓根执行：

```text
python -X utf8 -m pytest tests/test_world_prior_atomic_restore.py -q
python -X utf8 tools/testing/replay_save_guards.py --spec tests/fixtures/world_prior_atomic_restore_mutations.json --baseline ef6f413dd5ffaa91b419b359dc6edcfc872bffed --mutate
python -X utf8 -m pytest tests/test_wm_simloop_guards.py tests/test_world_observation_contracts.py tests/test_world_inference_window.py -q
python -X utf8 tools/verify_manifest.py --strict --quiet
python -X utf8 tools/time_core_lint.py --json
```

同目录公开两平台逐腿原始日志及来源校验。公开日志只替换工作区前缀、统一换行和行尾空白，完整原始输出保留本地；时间核完整 JSON 保留本地，其 SHA256 见相关日志。

## 来源与边界

保留 [PR #461](https://github.com/FuRongJun-1999/lingshu/pull/461) 经验格式/恢复入口及 [PR #516](https://github.com/FuRongJun-1999/lingshu/pull/516) 的历史投影贡献。本件修的是恢复失败时半替换运行态，不重报格式、BOM、路径闸或历史回填。

只保证同步恢复失败时的运行态不变，不提供并发读写隔离或收据真实性验证；传输使用本地收据协议，未验证真实脑端 MCP、DSH 宿主及生产数据。 未修改 dsh-memory 大脑区；未验证 DSH 宿主、生产记忆、线上发生频率、吞吐、断电/磁盘满或所有其它锁。维护者人审待完成。
