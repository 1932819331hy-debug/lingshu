# 第 2 组最终验收

测试时冻结生产模块、测试和演示文件。
运行前后 333 个文件的 SHA-256 全部相等。
[冻结文件指纹](<readouts/frozen_sources.json>) 保留测量结果。

## 实跑结果

| 项目 | 结果 |
|---|---|
| 全量 pytest，真实脑端前置 | 1707 passed、17 skipped、88 subtests passed |
| gate 脚本式测试 | 23 个全部退出 0 |
| 动画与真实脑端 core 联跑 | 37 passed |
| 回声原场景与 CRLF 回归 | 42 passed、0 skipped |
| 干净 LF 副本 | 42 passed、0 skipped |
| 干净 CRLF 副本 | 42 passed、0 skipped |
| 清单严格核验 | 64 MATCH、0 MISMATCH，3 个原生模块 |
| 导入方向 | core→world 28，无超基线 |

[完整全量输出](<logs/full_pytest_final.txt>)。
[23 个脚本输出](<logs/script_tests_final.txt>)。

脑端固定提交为 `98d6b899533a82fb1047f952987229c62a9929fb`。
外层测试使用 Python 3.12.13，脑端使用本地 Python 3.13。
全量按该提交的 gate 配置排除 23 个脚本式测试，另行逐件执行。

## 复跑

```text
python -X utf8 -B -m pytest tests/test_event_echo.py tests/test_process_ops.py tests/test_world_process_core.py tests/test_world_process_demo.py -q -p no:cacheprovider
python -X utf8 -B tools/verify_manifest.py --strict --quiet
python -X utf8 -B tools/import_direction_lint.py --json
python -X utf8 -B artifacts/world_process_delivery_20261014/evidence_pack_group2/probes/audit_mutation.py
python -X utf8 -B tools/demo_world_process.py --out-dir artifacts/world_process_group2_repro
```

完整门禁使用仓库的 [gate 配置](<../../../.github/workflows/gate.yml>)。
脑端可达时，按工作流设置 `MDCG_BRAIN_PYTHONPATH`。
缺少脑端前置时，脑端往返用例明确跳过。

回声测试自行在 pytest 临时目录生成 6 份变异。
LF 与 CRLF 源文件产生相同的变异行为，不依赖作者本地目录。
公开变异探针当前结果：4 条基线全部通过、4 项变异全部失败。
[当前变异读数](<readouts/final_audit/audit_mutation_clean.json>)。

## 动画与独立复审

[动画证据](<ANIMATION.md>)：补全 12 帧、重组 10 帧。
去掉标题后，两段各有 9 种实体画面。
旧终审报告中的 H1 是动画补齐前的快照，现已关闭。

行为独立审查绑定候选提交 `71b6c00` 的三个模块。
后续只补了动画、文案和变异测试可复跑性，三个生产模块未改。
作者侧独立复审不代表维护者正式验收。

## 脱敏

公开日志只替换本机工作区和临时目录前缀。
数字、错误、逐条读数和测试结果保留。
生产文件指纹是原字节测量，换行不同可能改变原字节 hash。
清单工具另按 LF 归一口径核验。
原始本地日志不改写，数据库、密钥和脑端目录不放入本包。
