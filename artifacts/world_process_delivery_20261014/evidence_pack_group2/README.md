# 世界过程第 2 组公开证据包

本包覆盖事件回声、过程补全与过程重组。
最终全量：1707 passed、17 skipped、88 subtests passed。
23 个脚本式测试全部通过，补全与重组动画已补齐。

- [最终验收](<VALIDATION.md>)：冻结文件指纹、全量输出、干净检出验证。
- [动画证据](<ANIMATION.md>)：两段 GIF、抽样帧与逐帧读数。
- 下列来源表保留候选提交 `71b6c00` 的行为审查快照。

## 1. 来源与绑定

原行为审查材料来自仓库内三个目录：

- `artifacts/world_process_delivery_20261014/group2_echo_fix2`：事件回声第 2 次返修。
- `artifacts/world_process_delivery_20261014/group2_ops_fix1`：process_ops 第 1 次返修。
- `artifacts/world_process_delivery_20261014/group2_final_audit`：最终独立终审。

原行为终审绑定核对：

- `git rev-parse HEAD` 得 `71b6c000e8dd44937f69e0c2672473e5e03a7064`。
- `git log --oneline` 得链：`71b6c00` ← `4e8ea6e`（合并第 1 组 `eba6575`）← `4c6e6e2`。
- `readouts/final_audit/audit_echo.json` 的 `source_sha256.git_head` 记为 `71b6c00`。

三个模块的 sha256 全值：

| 文件 | sha256 |
|---|---|
| lingshu/world/world_process.py | 950f3d9669ffaf5cc9ee63c71e3f55cc21ebba00ede6d70649454cacf8a74659 |
| lingshu/world/process_ops.py | 59cde8f925fac077764acbba713c1de13432a22c6f8904d7fbe290019a93f2e7 |
| lingshu/core/core.py | 5fc2ad929702c9b8a2087e755faa2835354ce974f09c0bffe1c46de02403be82 |

71b6c00 的提交流水线原始记录在 `artifacts/world_process_delivery_20261014/group2_settle/REPORT.md`，该文件未收入本包，理由见第 7 节。

本包不收数据库、脑端 home、test_home、tmp、`_mutants`、stages 与个人配置。

## 2. 文件清单与用途

| 包内路径 | 来源路径 | 用途 | sha256（前 16 位） |
|---|---|---|---|
| reports/echo_fix2_REPORT.md | group2_echo_fix2/REPORT.md | 回声返修的接口变化、逐项读数与限制 | 2f47353aa7bd3ede |
| reports/ops_fix1_REPORT.md | group2_ops_fix1/REPORT.md | process_ops 三项返修的读数与限制 | 49ef0f6242d71f23 |
| reports/final_audit_REPORT.md | group2_final_audit/REPORT.md | 独立终审的逐项验收、变异、未满足项 H1 | c5f1be770ac050bf |
| readouts/echo_fix2/readings.json | group2_echo_fix2/logs/readings.json | 回声切分、coverage、目标评分真实 JSON | d3459e4f01cf5298 |
| readouts/echo_fix2/calibration.json | group2_echo_fix2/logs/calibration.json | 门槛 0.7 的固定 seed 校准读数 | cb0b9ca852124eae |
| readouts/echo_fix2/mutation_validation.json | group2_echo_fix2/logs/mutation_validation.json | 回声 6 项隔离变异读数 | 4053cef329e92be7 |
| readouts/ops_fix1/probe_fix1_readings.json | group2_ops_fix1/logs/probe_fix1_readings.json | 移动 ref 尾部还原、dt 拒绝、joint、同名角色 | bc5a784405a64c91 |
| readouts/ops_fix1/probe_isolated_mutation_fix1.json | group2_ops_fix1/logs/probe_isolated_mutation_fix1.json | ops 9 项隔离变异读数 | 6b793bb7f2039cd6 |
| readouts/final_audit/audit_echo.json | group2_final_audit/logs/audit_echo.json | 终审自造输入的回声全部读数 | 6fff3a8fec34804f |
| readouts/final_audit/audit_ops.json | group2_final_audit/logs/audit_ops.json | 终审自造输入的补全与重组读数 | 53c007e4a0a3a333 |
| readouts/final_audit/audit_mutation.json | group2_final_audit/logs/audit_mutation.json | 终审 4 项变异基线通过、变异失败的完整读数 | 7e5f27752351cdba |
| logs/final_audit/audit_mutation_stdout.txt | group2_final_audit/logs/audit_mutation_stdout.txt | 终审变异的 4 行摘要 | e0fa354013252172 |
| logs/final_audit/pytest_event_echo.txt | group2_final_audit/logs/pytest_event_echo.txt | 回声测试原始输出 | 560b21009b13082a |
| logs/final_audit/pytest_process_ops.txt | group2_final_audit/logs/pytest_process_ops.txt | process_ops 测试原始输出 | 57338ac85ac08570 |
| logs/final_audit/pytest_world_process.txt | group2_final_audit/logs/pytest_world_process.txt | 第 1 组过程模块测试原始输出 | c4a24b6fce21111e |
| logs/final_audit/pytest_world_process_core.txt | group2_final_audit/logs/pytest_world_process_core.txt | core 门面测试原始输出与告警原文 | b53bc942c4df85ef |
| probes/audit_echo.py | group2_final_audit/probe/audit_echo.py | 终审回声探针（自造输入） | 50ee22af80ba1716 |
| probes/audit_ops.py | group2_final_audit/probe/audit_ops.py | 终审补全与重组探针（自造输入） | 42d9f6481a01b367 |
| probes/audit_mutation.py | group2_final_audit/probe/audit_mutation.py（本次修订，见 3.1） | 终审隔离变异探针（内存改源码串） | ce9b782a7e540ca9 |
| readouts/final_audit/audit_mutation_clean.json | 本次运行新出（见 3.1） | 修订后探针的完整读数：4 条基线断言全过、4 项变异目标断言全红 | 9e9f0571ee94e31f |

## 3. 脱敏范围

14 个文件与来源逐字节相同，4 个文件只改本机路径前缀，1 个公开副本已修订（见 3.1）：

| 包内路径 | 改动处数 | 原文 | 公开副本 |
|---|---|---|---|
| reports/echo_fix2_REPORT.md | 1 | 本机仓库根目录 | <repo> |
| reports/final_audit_REPORT.md | 1 | 本机仓库根目录 | <repo> |
| logs/final_audit/pytest_world_process_core.txt | 9 | 本机仓库根目录（原生路径） | <repo> |
| readouts/ops_fix1/probe_isolated_mutation_fix1.json | 1 | 本机临时目录\ops_fix1_mutants_54ztwb3s | <tmp>\ops_fix1_mutants_54ztwb3s |

脱敏只替换文件名前缀。数字、字段名、布尔值、错误文案与断言消息未改。

本地已核对前缀还原后与来源逐字节相同，原始日志未改写。公开包不提供原本机路径；复跑时将 `<repo>` 换为当前仓库根目录即可。

### 3.1 公开副本修订：probes/audit_mutation.py

原始探针 `group2_final_audit/probe/audit_mutation.py` 未改动。
其 sha256 仍是 65e9106855e03ccd3e4d263066c809bbbd559fc3d666df082fcc0342587ef7d3。
包内副本本次修订为 sha256 ce9b782a7e540ca95bb3426de87272f62c8075689e7b968b7e2471e9fc10ca89。
四处改动：

1. 仓根改为从脚本自身位置向上找 `lingshu/world/world_process.py`，不再写死向上 4 层。
2. 临时变异副本落系统临时目录，跑完删除，不再写 `<pack>/work/`。
3. 读数 JSON 落 `<pack>/readouts/final_audit/audit_mutation_clean.json`，不再写 `<pack>/logs/`。
   临时目录只是模式串 `<临时目录>/audit_mutation_*`，所以同一份读数连跑两次得同一个 sha256。
4. 修正 `assert_unrelated_invariance` 的构造与断言，见下。

该断言原先把无关事件写成 `x@103`。`x@103` 与目标签名 `(x, stop)` 相同，
按现行判据“固定目标签名在源事件之后最早的一条”，`x@100` 的真实响应就是 103。
断言写的是 105，所以原读数里该条记为 `fail`，`detail` 为 `((100, 'x'), True, False)`。
该失败属探针断言错误，不是被测模块的缺陷。
该模块的无关不变性另有两条独立核对：本包 `audit_echo.py` 的 `unrelated_invariance` 构造
（插入 w 实体事件，读数在 `audit_echo.json`），
以及 `tests/test_event_echo.py::test_unrelated_entity_events_do_not_change_hits_or_mae`。
修订后的断言把无关插入换成 `y` 实体的事件，目标签名 `(x, stop)` 的候选集合逐条不变；
另加一条对照断言：同一位置插入同目标签名的事件时 `actual` 必须改到 103、命中必须转落空。

本次运行的完整读数在 `readouts/final_audit/audit_mutation_clean.json`：
4 条基线断言全部 `pass`，4 项变异全部 `mutant=fail`、`isolated=true`，
`all_baseline_pass` 与 `all_mutants_isolated` 都是 true，`source_unchanged` 是 true。
旧读数 `readouts/final_audit/audit_mutation.json` 原样保留作历史，数字未改。

## 4. 关键读数

回声切分、coverage、目标评分（`readouts/echo_fix2/readings.json`）：

- `split_sweep_n20`：n=20，f=0/.1/.3/.5/.7/.99/1 七档的 dev_events 为 20/18/14/10/6/0/0，boundary_tick 为 null/92/72/52/32/2/2。
- `split_tick_group_cases`：组首 f=0.65 得 dev 2、留出 4；组末 f=0.50 得 dev 1、留出 5。
- `periodic_fixture.identities`：`coverage_equals_predictions_over_events` 为 true，三项分区计数都等于 6。
- coverage 口径：`periodic_fixture.lag_model` 得 predictions 6、scored 5、coverage 1.0、scored_rate 0.833333。
- 目标评分：`honest_miss_fixture` 行 0 得 predicted 110、actual 111、hit false、end_state missed、abs_tick_error 1；`actual_next_compatibility.identical` 为 true。
- `unrelated_event_invariance`：干净留出与插入 w 事件的噪音留出都得 hits 3、abs_tick_errors [0,0,0]。

回声隔离变异（`readouts/echo_fix2/mutation_validation.json`）：`all_isolated` 为 true，6 项都满足 `repo_target_holds` true 且 `mutant_target_holds` false。

门槛校准（`readouts/echo_fix2/calibration.json`）：300 次试验，`min_consistency` 取 0 时 300 次出关联、最大 consistency 0.666667；取默认 0.7 时 0 次出关联。

补全与重组（`readouts/ops_fix1/probe_fix1_readings.json`）：

- `A1_moving_ref_restore`：prefix 6 帧、生成 6 帧、ticks [7,12]，`ref_equal` 与 `actor_equal` 都是 true，`match_translation` 为 [0,0,0]；改前口径的 `old_anchor_translation` 为 [-1,0,0]。
- `B1_dt_mismatch`：几何 6/6 命中，`timing.deltas` 为 [0,3,6,9,12,15]，`match_score` 0.166667，`matched` false，帧为空。
- `C1_irregular_dt_and_gaps`：非均匀 dt 下命中，生成 ticks [8,18]，tick 4 与 11 不补帧。
- `E1_same_named_roles`：complete、compose 传 roles、compose 省略 roles 三条路径都抛 ValueError，消息为“actor 与 ref 不得同名：帧里的 entities 会互相覆写”。

ops 隔离变异（`readouts/ops_fix1/probe_isolated_mutation_fix1.json`）：`baseline.outcome` 为 pass，M1 至 M9 的 `outcome` 都是 assertion_error，`module_unchanged` 为 true。

终审独立读数：`readouts/final_audit/audit_echo.json`、`readouts/final_audit/audit_ops.json`。
终审变异：`readouts/final_audit/audit_mutation.json` 与 `logs/final_audit/audit_mutation_stdout.txt`，4 项都满足 baseline=pass、mutant=fail、isolated=True，`source_unchanged` 为 true。该读数是修订前探针的原始读数，保留作历史。

修订后探针（`readouts/final_audit/audit_mutation_clean.json`）：4 条基线断言 `assert_split_direction`、`assert_unrelated_invariance`、`assert_coverage_and_self_response`、`assert_tail_restore` 全部 `pass`；4 项变异的 `mutant` 都是 `assertion_error`，`isolated` 都是 true；`all_baseline_pass` 与 `all_mutants_isolated` 都是 true；`source_unchanged` 是 true。`world_process.py` 与 `process_ops.py` 的 sha256 与第 1 节表内一致。

测试读数原始输出：`logs/final_audit/` 下四个文件记为 36 passed（回声）、39 passed（第 1 组过程模块）、22 passed（process_ops）、27 passed 与 1 skipped（core）。

## 5. 复跑命令

全部在仓根执行。`<pack>` 代表 `artifacts/world_process_delivery_20261014/evidence_pack_group2`。
解释器为 `./.venv/Scripts/python.exe`（Python 3.12.13）。

测试件：

```bash
./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_event_echo.py -q -p no:cacheprovider
./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_process_ops.py -q -p no:cacheprovider
./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process.py -q -p no:cacheprovider
./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_core.py -q -p no:cacheprovider
```

本代理在 2026-10-14 实跑前两条，得 36 passed 与 22 passed，退出码都是 0。后两条的读数取自 `logs/final_audit/` 下的原始日志，本次未重跑。core 的 1 skipped 是脑端用例，缺 `MDCG_BRAIN_PYTHONPATH` 前置时跳过。

工具与演示：

```bash
./.venv/Scripts/python.exe -X utf8 -B tools/verify_manifest.py --strict --quiet
./.venv/Scripts/python.exe -X utf8 -B tools/import_direction_lint.py --json
./.venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py --out-dir <临时输出目录>
```

演示命令的输出目录必须是临时目录，演示会把 world_process.db、motion_groups.db 与图片写进该目录。
本代理未重跑演示：`tools/demo_world_process.py` 正由另一代理修改，当前工作树版本不绑定 71b6c00。

终审探针（本包内）：

```bash
./.venv/Scripts/python.exe -X utf8 -B <pack>/probes/audit_echo.py
./.venv/Scripts/python.exe -X utf8 -B <pack>/probes/audit_ops.py
./.venv/Scripts/python.exe -X utf8 -B <pack>/probes/audit_mutation.py
```

三个探针只依赖标准库与 `lingshu` 包，不需要本机其它数据。
`audit_echo.py` 与 `audit_ops.py` 按 `dirname(__file__)/../../../../` 算仓根，必须留在 `<pack>/probes/` 这一深度。
`audit_mutation.py` 改为向上找 `lingshu/world/world_process.py` 定位仓根，位置不限。
`audit_mutation.py` 的副本落系统临时目录、跑完删除；读数写 `readouts/final_audit/audit_mutation_clean.json`。
输出路径可用环境变量 `AUDIT_MUTATION_OUT` 覆盖。
`audit_echo.py` 与 `audit_ops.py` 仍写 `<pack>/logs/` 与 `<pack>/work/`，不覆盖 `readouts/` 下的冻结副本。

## 6. 限制与未验证范围

- 旧终审的动画缺口 H1 已关闭。GIF、逐帧读数和测试依据见[动画证据](<ANIMATION.md>)。
- 首轮全量的覆盖集差异发生在新增动画测试期间。文件冻结后重跑已通过，结果见[最终验收](<VALIDATION.md>)；覆盖守卫未改。
- `tests/test_event_echo.py` 的 6 个 `test_mutation_*` 用例改为现场生成副本。
锚点替换表随测试件携带，副本落 pytest 会话临时目录。
源码文件只读不写，副本缺失不再 skip。
缺任何本地 artifacts 目录时这 6 条照常执行并通过。
核对读数在 `artifacts/world_process_delivery_20261014/group2_clean_mutations/`。
- `readouts/final_audit/audit_mutation.json` 是修订前探针的原始读数，原样保留作历史，数字未改。
其中 `assert_unrelated_invariance` 记为 `result: fail`、`detail` 为 `((100, 'x'), True, False)`。
该失败属探针断言错误，不是被测模块的缺陷。
断言插入的 `x@103` 与目标签名 `(x, stop)` 相同。
按现行判据，`x@100` 的真实响应就是 103；断言写的是 105。
修订后的副本把无关插入换成 `y` 实体。
副本另加同签名插入必须改变 `actual` 的对照断言，4 条基线断言全部通过，
读数在 `readouts/final_audit/audit_mutation_clean.json`。
- 终审报告的三项未验证（C1 至 C3）：真实 UnifiedWorldModel 历史未复现多实体同 tick 与无关实体插入；门槛 0.7 与固定构造上界 0.666667 只差 0.033，未验证随机构造能否分开；保留段末条的预测超出观察尾部时不计命中与偏差，该位置误差不可评估。
- 低级别文字问题 L1/L2 已修正：ref_y_gap 是锚定后的残差，actor_y_gap 是接点两 actor 的高度差；补全平移量统一为观测末帧 ref 减记录已匹配末帧 ref。continuous 仍只覆盖水平面。
- `probes/audit_ops.py` 通过 core 门面建引擎，在 `<pack>/work/facade_audit.db` 落盘。若该引擎读取 HOME 下的脑端目录，复跑前宜把 HOME 指向临时目录。
- 原来源表中 14 份文件保留原字节，4 份只替换本机前缀；公开探针另行修订。最终全量与动画材料另见首页入口。

## 7. 当前交付

动画、全量回归和干净检出验证均已完成，详见首页入口。
原始来源报告与读数保留，不把历史快照改写成最新结果。
集成流水线报告保留本地，不属于本包来源白名单。
维护者正式验收仍待裁定。
