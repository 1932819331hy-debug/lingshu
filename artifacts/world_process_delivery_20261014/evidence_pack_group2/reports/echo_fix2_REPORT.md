# 事件回声第 2 次返修证据报告（世界过程第 2 组）

本报告只写当前工作区的实测读数。全部读数由本目录的脚本复算，日志在 `logs/`。

## 1. 改动范围与文件指纹

sha256 取原始字节的完整值，下表只列前 16 位；完整值见 `logs/fingerprints.json`。

| 文件 | sha256（前 16 位） | 本轮改了什么 |
|---|---|---|
| lingshu/world/world_process.py | 950f3d9669ffaf5c | 事件回声段：切分方向、评分判据、指标口径、consistency 说明 |
| lingshu/core/core.py | ac1aed4b06c84b26 | echo 动作转发 min_consistency，动作说明补该参数 |
| tests/test_event_echo.py | dcfd70527551294c | 原 26 条写反的期望、10 条新增极简反例、6 条隔离变异 |
| tests/test_world_process_core.py | e49f0ee8992db4f7 | I1 逐值比较与独立计数断言；新增 I4 透传判据 |
| docs/world_process_usage.md | c4470a3a29cc0923 | 事件回声的签名、公式、字段与局限 |
| tools/demo_world_process.py | bd9188e5d28bd883 | echo 时序图读数与两模型原始读数打印 |

未改：lingshu/world/process_ops.py、tests/test_process_ops.py（另一代理返修中）、
第 1 组 worktree、导出清单、模块计数、其它模块测试。未执行 git 写操作。

## 2. 接口变化

### 2.1 参数与门面

| 位置 | 变化 | 语义 |
|---|---|---|
| core 的 echo 动作 | 新增转发 min_consistency | 缺省 0.7；原来只能直接调用模块函数改这一档 |
| echo_split | holdout_fraction 语义纠正 | 留出段占事件数的比例；原来表示切分下标 |
| echo_lag_associations / evaluate_event_echo / event_echo_report | min_consistency 含义不变 | 主 lag 计数 ÷ 该源签名有任意响应的触发数 |

### 2.2 关联记录新键

echo_lag_associations 每条新增 lag_consistency。原键（after、before、source_eid、
source_event、target_eid、target_event、lag、count、lag_counts、triggers、
sources_total、sources_with_pair、consistency）全部保留原名与原义。

### 2.3 逐条读数新键

- echo_lag_prediction 与第 6 节读数表新增 actual_eid、actual_event；actual_tick 语义改为
  取同一个响应对象。
- 逐条读数新增 beyond_window 布尔键。
- end_state 取值改为 hit / missed / beyond_window / no_prediction 四者之一；
  原来的 window_end 不再出现。

### 2.4 读数与语义变化

| 键 | 变化 |
|---|---|
| coverage | 改为 predictions ÷ events；原口径是 scored ÷ events |
| scored_rate | 新增，scored ÷ events |
| prediction_beyond_window | 新增，预测超出观察尾部的条数 |
| unresolved_window_end | 与 prediction_beyond_window 同值，保留旧键名 |
| scored | 含义不变（判据分辨出结果的行数），但因 beyond_window 单列而数值下降 |

## 3. 判据与实测读数

### 3.1 B1 切分

holdout_count = ceil(事件数 × holdout_fraction)，split_index = 事件数 − holdout_count。
边界落在同 tick 组内部（前一条与本条同 tick）时向前移到该组首；后一条同 tick 不再触发
回退。

n=20、无同 tick 的周期流（`logs/readings.json` 的 split_sweep_n20）：

| 比例 | dev_events | holdout_events | boundary_tick | dev_last_tick | holdout_first_tick |
|---|---|---|---|---|---|
| 0.00 | 20 | 0 | None | 97 | None |
| 0.10 | 18 | 2 | 92 | 87 | 92 |
| 0.30 | 14 | 6 | 72 | 67 | 72 |
| 0.50 | 10 | 10 | 52 | 47 | 52 |
| 0.70 | 6 | 14 | 32 | 27 | 32 |
| 0.99 | 0 | 20 | 2 | None | 2 |
| 1.00 | 0 | 20 | 2 | None | 2 |

两段都非空时，dev_last_tick 严格小于 holdout_first_tick；留出段始终是事件流的最后
holdout_events 条。0.99 与 1.00 都得 20 条，因为 ceil(20 × 0.99) = 20。

同 tick 组三种边界位置（`split_tick_group_cases`）：

| 位置 | 输入 | 比例 | dev_events | holdout_events | dev_last_tick | holdout_first_tick |
|---|---|---|---|---|---|---|
| 组首 | tick 10/20/30×3/40 | 0.65 | 2 | 4 | 20 | 30 |
| 组中 | 同上 | 0.50 | 2 | 4 | 20 | 30 |
| 组末 | tick 10/20×3/30/40 | 0.50 | 1 | 5 | 10 | 20 |

组首一例是旧口径多退一组的反例：旧版代码会把 dev 压到 1 条，本版给 2 条。
组末一例的留出条数是 5，多于按比例算出的 3 条；这是同 tick 整组进同一段的直接结果。

### 3.2 M1 评分

真实响应取固定目标签名 (target_eid, target_event) 在源事件之后最早的一条；命中要求
tick、eid、event 三者与该响应全等。源事件自身不算响应。两个模型共用同一响应对象，
差别只有预测 tick。

无关实体事件插入或删除（`unrelated_event_invariance`）：

| 留出段 | hits | abs_tick_errors | mean_abs_tick_error |
|---|---|---|---|
| 无 w 事件（4 条） | 3 | [0, 0, 0] | 0.0 |
| 插入 w@102、w@112（6 条） | 3 | [0, 0, 0] | 0.0 |

错误类型与真实目标分居两处：dev 为 x-stop 每 5 tick 一条；留出段 x-stop@100、
x-stop@105、x-turn@110。第 1 行预测 (105, x, turn)，105 上确有一条 x-stop，
真正目标是 110 的 x-turn，该行 hit=False、end_state=missed、abs_tick_error=5。

actual_next 入参兼容（`actual_next_compatibility`）：同一源事件下 actual_next 取 None
与取 (9999, x, turn)，四次返回逐字相同（`identical` = true）；actual_tick 都是 45。

### 3.3 M3 coverage 与右侧删失

恒等式在周期夹具上成立（`periodic_fixture`）：

| 恒等式 | 读数 |
|---|---|
| predictions + no_prediction | 6 + 0 = 6 |
| scored + unscored | 5 + 1 = 6 |
| scored + no_prediction + prediction_beyond_window | 5 + 0 + 1 = 6 |
| coverage = predictions ÷ events | 1.0 = 6 ÷ 6 |

三类计数各自的夹具读数：

| 夹具 | events | predictions | no_prediction | scored | unscored | prediction_beyond_window | coverage |
|---|---|---|---|---|---|---|---|
| 无关联、无预测 | 4 | 0 | 4 | 0 | 4 | 0 | 0.0 |
| 预测全部超窗 | 2 | 2 | 0 | 0 | 2 | 2 | 1.0 |

超窗夹具逐条读数：行 0 预测 110、流里还有 101 的停，end_state=beyond_window、
abs_tick_error=None。旧口径把这一行记成 scored=True 与偏差 None；本版把这一行单列计数，
偏差为 None 的行不再混进普通落空。

真实目标已观察但与预测不同：留出段 x-stop@100、x-stop@111、x-stop@120，行 0 预测 110、
响应 111，end_state=missed、abs_tick_error=1、abs_tick_errors=[1]。

右侧删失范围：观察窗口止于事件流最后一条事件的 tick。预测 tick 大于该 tick、或该行没有
预测，都不进 scored；前者进 prediction_beyond_window。

### 3.4 M2 consistency 与 lag_consistency

默认门槛与支持度未改：echo_lag_associations 的 min_consistency 缺省 0.7、min_support
缺省 2。300 次固定 seed（1000..1299，40 条事件、tick 0..399、3 个实体）校准见
`logs/calibration.json`：

| 门槛 | 出关联的试验数 | 最大 consistency |
|---|---|---|
| 0（仅看读数上界） | 300 / 300 | 0.666667 |
| 0.7（默认） | 0 / 300 | — |

本段读数只用于复现上轮校准，不构成统计检验，也不保证随机序列不出现偶然候选。

consistency 与 lag_consistency 的实测对照（`consistency_fixture`，源签名都是 x-stop，
触发数 5）：

| 目标签名 | count | lag_counts | consistency | lag_consistency |
|---|---|---|---|---|
| (x, stop) | 4 | {10: 4} | 0.8 | 1.0 |
| (y, turn) | 3 | {1: 3, 2: 1} | 0.6 | 0.75 |
| (w, stop) | 1 | {1: 1, 11: 1} | 0.2 | 0.5 |

(y, turn) 这一行是两个口径分开的例子：consistency 0.6 低于默认门槛，lag_consistency
0.75 不低于。差别来自分母：consistency 的分母是 5 次有响应的源事件，lag_consistency 的
分母是该签名对的 4 次 lag 计数。

## 4. 测试与隔离变异

| 命令 | 结果 |
|---|---|
| .venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_event_echo.py tests/test_world_process.py -q -p no:cacheprovider | 75 passed（36 条事件回声 + 39 条第 1 组） |
| .venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_core.py -q -p no:cacheprovider | 26 passed、1 skipped |
| .venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_motion_groups.py -q -p no:cacheprovider | 19 passed |
| .venv/Scripts/python.exe -X utf8 -B tools/import_direction_lint.py --json | exit_code 0，core_to_world 28 与基线一致，new_over_baseline 为空 |
| .venv/Scripts/python.exe -X utf8 -B artifacts/.../group2_echo_fix2/make_mutants.py | 6 份副本，每项替换命中 1 次 |
| .venv/Scripts/python.exe -X utf8 -B artifacts/.../group2_echo_fix2/validate_mutations.py | 6 项全部隔离成立 |
| .venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py --out-dir artifacts/.../group2_echo_fix2/demo_out | 退出码 0；日志 demo_stdout.log |
| .venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_manifest_integrity.py -q -p no:cacheprovider | 2 failed、18 passed，原因见第 5 节 |

事件回声测试从 26 条增到 36 条。新增的 10 条是切分比例扫描、同 tick 组首/组中/组末、
无关事件不变性、coverage 口径、超窗计数、真实目标不符、consistency 双读数、以及 3 条
新变异断言。

隔离变异用目标语义断言判定，不做源码字符串检查（`logs/mutation_validation.json`）：

| 副本 | sha256（前 16 位） | 替换次数 | 目标语义断言 | 仓库 | 变异 |
|---|---|---|---|---|---|
| wp_m1_no_eid | fd331016fed3815a | 2 | 目标签名 (y, turn) 不存在时不得命中 | 成立 | 不成立 |
| wp_m2_adjacent | f3cd87de0d37d596 | 1 | 插入无关事件后同一签名对的 [lag, count] 不变 | 成立 | 不成立 |
| wp_m3_count_split | 7837875645f913e4 | 1 | 同 tick 组不被劈开（dev 2 / 留出 4） | 成立 | 不成立 |
| wp_m4_split_reversed | 9e1c08f364f83afd | 1 | 留出段是流尾 ceil(n × 0.3) = 6 条 | 成立 | 不成立 |
| wp_m5_next_event_scoring | 23560529d817e613 | 1 | 无关事件插入不改变 hits 与 MAE | 成立 | 不成立 |
| wp_m6_coverage_scored | 3497122b768d1469 | 1 | coverage = predictions ÷ events | 成立 | 不成立 |

三项必做变异的关键读数（仓库 → 变异）：

- M4 颠倒切分方向：dev 14 / 留出 6 / boundary 72 → dev 6 / 留出 14 / boundary 32。
- M5 全流下一事件评分：hits 3、MAE 0.0 → hits 1、MAE 2.0（无关事件插入后 [3, 0, 3]）。
- M6 coverage 原错口径：1.0 → 0.833333（scored 5 ÷ events 6）。

### 4.1 演示读数

demo 的 echo 段与事件时序图都读同一个 `event_report`（`demo_out/readings.json` 的
`echo.timeline`）：

| 读数 | 延迟模型 | 无延迟模型 |
|---|---|---|
| predictions | 5 | 5 |
| hits | 4 | 0 |
| coverage | 1.0 | 1.0 |
| prediction_beyond_window | 1 | 0 |
| 逐条预测读数条数 | 5 | 5 |

两模型的逐条预测读数都留在 `timeline.lag_predictions` 与
`timeline.no_delay_predictions` 里；脚本不写固定结论，打印的数字全部来自模块返回值。

### 4.2 中文审查

用 stc 的检查命令（for-document 场景）复核本轮文字：

| 文件 | 需修改 | 待确认 |
|---|---|---|
| group2_echo_fix2/REPORT.md | 0 | 0 |
| docs/world_process_usage.md | 0 | 1 |
| lingshu/world/world_process.py | 0 | 18 |

world_process.py 的 18 处待确认全是 G17 中文引号提示，触发文字是 Python 文档字符串的
三引号分隔符，不是中文引号；保留不改。docs/world_process_usage.md 的 1 处待确认是
`no_prediction（没有预测的条数）` 的括号说明，保留理由是两个键名容易混，括号给出判据。

## 5. 清单需要重算路径

本轮改了两个已登记文件，清单与实际文件的 sha256 不再一致。清单本身与模块计数文件
不在本轮可改范围，需要由清单所有者重算：

| 清单文件 | 记录路径 | 现值（前 16 位） | 现在应为 |
|---|---|---|---|
| docs/intake/body-export-v0.1/_export_manifest.json | `blocks["world"].files` 里 `file == "world_process.py"` | 5f4521798a3a74e6 | 950f3d9669ffaf5c |
| docs/intake/body-export-v0.1/_export_manifest.json | `blocks["core"].files` 里 `file == "core.py"` | 49fd9f159ff65593 | ac1aed4b06c84b26 |

工具实测（`logs/verify_manifest_strict.log`）：entries 64、match 61、mismatch 3。
第 3 条 mismatch 是 lingshu/world/process_ops.py，属另一代理的在改文件。
tests/test_manifest_integrity.py 的 test_4 与 test_18 因这份未同步的清单变红；两条都读
工具的 MATCH 结果，与事件回声代码本身无关。

未登记文件（docs/world_process_usage.md、tools/demo_world_process.py、
tests/test_event_echo.py、tests/test_world_process_core.py）不需要重算清单。

## 6. 限制与未验证范围

- 300 次固定 seed 只用于复现门槛校准，不含统计检验，不保证随机序列不出现偶然候选。
  门槛 0.7 与实测上界 0.666667 之间只有 0.033 的余量，换构造后是否仍能分开没有验证。
- consistency 混合目标选择与 lag 集中两种成分，不单独衡量其中任何一项；
  lag_consistency 只作原始读数，不参与门槛，也没有做检验。
- 同一 tick 组大于按比例算出的留出条数时，留出条数会多于比例值（实测 3 → 5）。
- 预测超出观察尾部的行不计命中与偏差，因此留出段末条的误差无法评估。
- 未在真实 UnifiedWorldModel 历史上复现多实体同 tick 与无关实体插入的场景；
  本报告的读数都来自构造输入。
- 未运行全仓测试，只跑了第 4 节列出的几组；tests/test_process_ops.py 属另一代理，
  未运行。
- lingshu/world/process_ops.py 只从 world_process 导入 GENERATED、pair_samples、
  transfer_demonstration 三个名字，不含事件回声函数，本轮改动不影响该模块。

## 7. 复跑命令

全部在仓根 <repo> 执行：

```bash
.venv/Scripts/python.exe -X utf8 -B artifacts/world_process_delivery_20261014/group2_echo_fix2/make_mutants.py
.venv/Scripts/python.exe -X utf8 -B artifacts/world_process_delivery_20261014/group2_echo_fix2/validate_mutations.py
.venv/Scripts/python.exe -X utf8 -B artifacts/world_process_delivery_20261014/group2_echo_fix2/calibrate_fix2.py
.venv/Scripts/python.exe -X utf8 -B artifacts/world_process_delivery_20261014/group2_echo_fix2/collect_fix2.py
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_event_echo.py tests/test_world_process.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_core.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py --out-dir artifacts/world_process_delivery_20261014/group2_echo_fix2/demo_out
```
