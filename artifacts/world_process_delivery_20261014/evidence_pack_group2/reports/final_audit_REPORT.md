# 第 2 组（事件回声 + 过程补全与重组）独立终审报告

终审对象：主仓 <repo> 的收口候选提交 `71b6c00`。
终审方式：只读仓库源码，全部测试数据由本次审计自己构造；不复用原测试、返修报告或其它代理的读数作为通过依据。
本目录是本轮唯一写入位置，未执行 git 写操作，未改生产文件。

## 0. 结论

修复后的事件回声与过程补全／重组，本次自造反例全部通过。回声 8 组、补全重组 10 组、隔离变异 4 项都成立。
阻断与中等问题为 0。

未满足交接 1 项：演示没有补全与重组的功能动画，只有静态 PNG（见第 6 节）。
另有 2 项低级别问题与 3 项待确认项，都不影响上述行为结论。

| 分级 | 编号 | 问题 | 状态 |
|---|---|---|---|
| 未满足交接 | H1 | 演示只有 completion.png 与 composition.png，没有能看出补全接续与两段重绑定的 GIF | 未解决 |
| 低 | L1 | joint.ref_y_gap 在现有接口下恒为 0；actor_y_gap 只是两端相对竖直偏移之差，不是两段原始高度差 | 未解决，只影响文字与解释 |
| 低 | L2 | docs/world_process_usage.md:78 仍写“锚点取前缀末帧的 ref 位置”，与现行生成语义不符 | 未解决，仅文档 |
| 待确认 | C1 | 未在真实 UnifiedWorldModel 历史上复现多实体同 tick 与无关实体插入 | 未验证 |
| 待确认 | C2 | 门槛 0.7 与固定构造上界 0.666667 只差 0.033，换随机构造能否分开没有验证 | 未验证 |
| 待确认 | C3 | 保留 holdout 段末条的误差不可评估（预测超出观察尾部时不计命中与偏差） | 已声明边界 |

## 1. 审查对象与指纹

sha256 取原始字节。git 头为 `71b6c00`（合入第 1 组后的收口提交）。

| 文件 | sha256 |
|---|---|
| lingshu/world/world_process.py | 950f3d9669ffaf5cc9ee63c71e3f55cc21ebba00ede6d70649454cacf8a74659 |
| lingshu/world/process_ops.py | 59cde8f925fac077764acbba713c1de13432a22c6f8904d7fbe290019a93f2e7 |
| lingshu/core/core.py | 5fc2ad929702c9b8a2087e755faa2835354ce974f09c0bffe1c46de02403be82 |
| tests/test_event_echo.py | dcfd70527551294c2e370e78673679a4016b65c4898482867b9b0a7eb9f6b1d2 |
| tests/test_world_process.py | 6d71c89f2662acf6d99f7804f2bae45c9e3854469796b121761ddfd0b64a5b13 |
| tests/test_process_ops.py | 88990b118a2aa361a630259c01d01cd24a2b77ff6489179f0712df034e01437f |
| tests/test_world_process_core.py | c069c0e8b1876f64982ba495a03db2ca49cc76bd1bbf67af7d4e2e8f4dd7e42a |
| docs/world_process_usage.md | 7ba7fbc3c3ae858771faa9badffab47ad04362b8e6448a0d290ef0d776b77be6 |
| tools/demo_world_process.py | bd9188e5d28bd883f3bb48487b692afb7a4d849d89fbe67a52b0a3404c5d1407 |

两条绑定关系已核对：

- 返修报告登记的 world_process.py 指纹 950f3d9（前 16 位）与本轮实测一致；process_ops.py 的 59cde8f 同样一致。
- 会话开始时的 core.py 指纹是 ac1aed4；收口提交与第 1 组合并后变为 5fc2ad9。本轮全部探针是在 5fc2ad9 上复跑的，结论按该指纹绑定。world_process.py 与 process_ops.py 在这两次之间没有变化。

复现解释器：`./.venv/Scripts/python.exe`（Python 3.12.13），全部命令在仓根执行。

## 2. 事件回声逐项验收

探针：probe/audit_echo.py，读数 logs/audit_echo.json，标准输出 logs/audit_echo_stdout.txt。

| 项 | 判定 | 最小可执行输入与实测读数 |
|---|---|---|
| holdout 比例 n=20，f=0/.1/.3/.5/.7/.99/1 | 通过 | 20 条周期流（stop@2+10k、turn@7+10k）。实测 dev/holdout/boundary：0→20/0/None、0.1→18/2/92、0.3→14/6/72、0.5→10/10/52、0.7→6/14/32、0.99→0/20/2、1→0/20/2。每次留出段都等于流的最后 ceil(20×f) 条，dev 与 holdout 是同一列表的不重叠划分 |
| 同 tick 整组、不回退过界 | 通过 | 组首：tick 10/20/30×3/40、f=0.65 → dev 2、holdout 4，边界 30，无 tick 同时出现在两段；组中：同流 f=0.50 → dev 2、holdout 4；组末：tick 10/20×3/30/40、f=0.50 → dev 1、holdout 5（多于按比例 3 条，属声明过的整组结果）。f=0 得 dev 6，f=1 得 dev 0 |
| 错误类型不计 hit | 通过 | dev = x-stop@2+5k（8 条）；留出 = x-stop@100、x-turn@105、x-stop@110。第 0 行预测 (105,x,stop)、实际 (110,x,stop)、hit=False、end_state=missed、abs_tick_error=5 |
| 错误实体不计 hit | 通过 | 同一留出段把 105 换成 y-turn：第 0 行响应取 x-stop@110，hit=False；同流其它行无命中 |
| 源自身不计 hit | 通过 | 单条 x-stop@10 流、lag=0 的自关联：predictions 0、hits 0；同 tick 两条（x-stop@10 与 x-turn@10）时 hits 0、coverage 0 |
| 插入无关事件不改固定目标命中与误差 | 通过 | dev = x-stop@2+5k。干净留出 5 条，噪音留出在同签名插入 103、另有 y-stop@107。同一源事件的 predicted/actual/hit/end_state/abs_tick_error 逐条相同，无差异行 |
| actual 三键完整同源 | 通过 | 源事件 x-stop@100、实际响应 x-stop@105：actual_tick/actual_eid/actual_event 都取自 105 那条事件；actual_next 传 None 与传 (9999,x,turn) 两次返回逐字相同；预测只由开发段关联决定 |
| 两模型同触发同目标 | 通过 | 逐条比较 lag 与 no_delay 读数：触发集合、predicted_eid、predicted_event 全部相同，只有预测 tick 不同 |
| coverage = pred/n | 通过 | n=6 时 predictions 5、events 6、coverage 0.833333 = round(5/6,6)；两个模型都成立；scored_rate = scored ÷ events 单独给出 |
| 右侧未决单列 | 通过 | 该构造 predictions 5、prediction_beyond_window 2、scored 3、no_prediction 1；恒等式 scored + no_prediction + prediction_beyond_window = events 成立；超窗行 hit=False、abs_tick_error=None |
| 已观测目标与预测不同记 miss | 通过 | missed 行全部满足 hit=False、actual_tick 非空、abs_tick_error = 预测与实际的 tick 差 |
| 固定签名跨实体配对 | 通过 | alpha-stop@10,20,30,40 配 beta-turn@15,25,35,45。关联 (alpha,stop)→(beta,turn) 得 lag 5、count 4。consistency 与 lag_consistency 都是 1.0，与本次独立复算逐项相同 |
| 两套一致性公式 | 通过 | 10 条事件构造：consistency = count ÷ triggers、lag_consistency = count ÷ sum(lag_counts)，11 条关联逐条相等；存在两者不等的行（consistency 0.2 对 lag_consistency 0.5） |
| 固定 seed 校准只作启发式 | 通过（按启发式判定） | 自写线性同余生成，40 条事件、tick 0..399、eid e0/e1/e2，seed 1000..1299：min_consistency=0 时 300/300 出关联、最大 consistency 0.666667；默认 0.7 时 0/300。与返修报告同值，本报告不据此给统计结论 |

## 3. 过程补全与重组逐项验收

探针：probe/audit_ops.py，读数 logs/audit_ops.json，标准输出 logs/audit_ops_stdout.txt。

| 项 | 判定 | 最小可执行输入与实测读数 |
|---|---|---|
| 原记录前缀再补全：逐帧恢复尾部 actor 与移动 ref | 通过 | 记录 12 条采样（ref 每 tick +1，actor 偏移逐帧 −0.25），观测前缀 5 帧：matched_samples 5、translation (0,0,0)、生成 7 帧、ticks [6,12]。第 0 帧 ref (15,0,0) = 记录第 5 条 ref，actor (18.75,0,0) = ref + rel，逐帧与独立复算相等；改前口径的锚点会给出 translation −1.0，与现行不同 |
| 整体平移 | 通过 | 观测前缀整体加 (100,5,−30)：translation 等于该位移，生成尾部逐帧等于记录尾部加该位移 |
| 非均匀 dt 与缺帧 | 通过 | 记录采样 tick [1,2,3,5,6,7,8,9,11..18]，观测前缀 tick [1,2,3,5,6,7]。timing.deltas 全 0、matched=True，生成 10 帧 ticks [8,18]；tick 4 与 11 不补帧。同几何但逐 tick 一帧的记录 timing 3/6、不命中、frames 为空 |
| 不同 elapsed 默认不命中 | 通过 | 逐 tick 记录配每 4 tick 一帧的前缀：geometry.score 1.0、timing.deltas [0,3,6,9,12,15]、match_score 0.166667、match=None、frames=[]；把 min_hit_ratio 放宽到 0.5 后 match_score 与 timing.matched 都不变 |
| 同名角色抛 ValueError | 通过 | 三条入口都抛 ValueError：complete 传同名 actor 与 ref、compose 传同名 roles、compose 省略 roles 而记录本身同名。消息逐字为“actor 与 ref 不得同名：帧里的 entities 会互相覆写”。不同名照常出帧。core 门面把该异常转成 status=error 的返回 |
| 候选来源与分数正确 | 通过 | 两条记录：下标 0 形状不符，下标 1 与前缀同形。候选按下标 1、0 排序。命中候选 match_score、geometry、timing 都是 1.0，来源字段（index/kind/actor/ref/ticks/samples）如实。未命中候选 reason 为“匹配分数 0.167 低于下限 1.000” |
| compose 只连两段 | 通过 | 函数只接 2 个位置参数：`compose_processes(first, second, {"actor":"a","ref":"r"})` 抛 TypeError；结果 segments 长度为 2 |
| 角色重绑定 | 通过 | roles={"actor":"hero","ref":"mark"}：两段生成帧的 entities 只含 hero 与 mark，记录里的 leader/walker 不再出现；joint.tick 为前段末帧 tick，后段首帧 tick = 前段末帧 tick + tick_gap |
| 水平 joint 与 y_gap 如实 | 通过 | joint.ref_gap/actor_gap 与本次独立复算相等（ref 0.0、actor 2.5）；actor_y_gap 50.0 = 两段相对竖直偏移之差；ref_y_gap 0.0，由连接点锚定推出；continuous 恰等于 actor_gap ≤ joint_tol，只看水平 x/z；生成帧保留第二段的 +50 高度差 |
| 关系只报告 | 通过 | relations 只有 actor/ref/roles_rebound/shared_actor/shared_ref/same_type/first/second；没有 compat、executable、semantic、safe、causal 任一字段，也不是判据；same_type 只比过程类型，标注缺失时为 None |
| 入参不被改写 | 通过 | 补全与重组调用后，history、records 与记录的嵌套列表与调用前深比较相等 |

## 4. 隔离变异

脚本：probe/audit_mutation.py，读数 logs/audit_mutation.json，标准输出 logs/audit_mutation_stdout.txt。
每个副本只替换一处源码串（替换命中 1 次），在 work/ 里落盘后用 importlib 单独加载；仓库源码在运行前后 sha256 相同（source_unchanged=true）。

| 副本 | 改了什么 | 行为断言 | baseline | mutant | 隔离成立 |
|---|---|---|---|---|---|
| wp_m1_split_reversed | echo_split 的切分下标改成从流首切出 | 留出段是流尾 6 条、dev 14、boundary 72 | pass | fail（assertion_error） | 是 |
| wp_m2_coverage_scored | coverage 改回 scored ÷ events | coverage = predictions ÷ events 且与 scored_rate 不同 | pass | fail | 是 |
| wp_m3_no_self_exclusion | 真实响应不再排除源事件自身 | 预测落在自己 tick 上时 actual 为 None、hit=False | pass | fail | 是 |
| po_m3_old_anchor | 补全平移锚点改成首条未观测采样的 ref | 平移量 = 观测末帧 ref − 已匹配记录末帧 ref，且第 0 帧逐帧还原记录尾部 | pass | fail | 是 |

四项都是“基线绿、变异红”，没有出现基线红或变异绿。

## 5. 原测试与工具实跑

| 命令 | 读数 |
|---|---|
| `-m pytest tests/test_event_echo.py -q` | 36 passed（logs/pytest_event_echo.txt） |
| `-m pytest tests/test_world_process.py -q` | 39 passed（logs/pytest_world_process.txt） |
| `-m pytest tests/test_process_ops.py -q` | 22 passed（logs/pytest_process_ops.txt） |
| `-m pytest tests/test_world_process_core.py -q` | 27 passed、1 skipped（logs/pytest_world_process_core.txt） |
| `python tools/import_direction_lint.py --json` | exit 0，core_to_world 28 与基线一致，new_over_baseline 为空（logs/import_direction_lint.json） |
| `python tools/verify_manifest.py --strict --quiet` | exit 0，entries 64、match 64、mismatch 0、missing 0（logs/verify_manifest.log） |

工具实跑只覆盖以上几项，未重跑全仓 pytest 与 18 个脚本式测试。

## 6. 演示与动画：未满足交接（H1）

实跑 `python -X utf8 -B tools/demo_world_process.py --out-dir <本目录>/demo_out`，退出码 0，原始输出 logs/demo_audit_stdout.txt。

现场产物：

| 文件 | 类型 | 内容 |
|---|---|---|
| completion.png | PNG 1440×480 | 观测前缀末帧与补全轨迹末帧两张静态对照 |
| composition.png | PNG 2160×480 | 前段终点、连接点、后段终点三张静态对照 |
| echo_timeline.png | PNG 960×220 | 事件时序图 |
| groups.gif | GIF 12 帧 480×320 | 组合体场景，属第 1 组能力 |
| source.gif / translated.gif / rotated_scaled.gif | GIF 各 12 帧 480×320 | 示教迁移三个变体，属第 1 组能力 |

判定：演示对第 2 组的三项能力只出静态 PNG。补全动画应能看到尾部缺失段被逐帧接续，重组动画应能看到两段角色重绑定后运动连续；这两者都没有产出。
演示脚本可查：`tools/demo_world_process.py` 只在第 1 组的三张变体与组场景调用 save_gif，补全段与重组段只调 .save(png)。
交接要求交付形态含动画，最低可接受线是至少一份能看出补全与重组过程的动态演示。本项判为未满足交接，不因文档未称动画而放过。

## 7. 低级别问题与待确认

### L1 joint 竖直读数的实际含义

- 最小可执行输入：第一段记录 ref 每 tick +1 且各采样 rel 竖直分量为 0；第二段记录各采样 ref 竖直分量 50、rel 竖直分量 50；`compose_processes(first, second, roles={"actor":"hero","ref":"mark"}, translation=(10,0,5))`。
- 实测：joint.ref_y_gap 0.0、joint.actor_y_gap 50.0、joint.actor_gap 2.5、continuous False；生成帧保留第二段的 +50 高度差。
- 机制：`_tail_frames` 与 `compose_processes` 把后段首帧 ref 定位到前段末帧 ref 的三维位置，因此连接点两端的 ref 竖直差恒为 0；actor_y_gap 只等于两端相对竖直偏移之差。
- 两个读数正确反映生成结果，也如实上报。“原始竖直高度差”这个说法会被读成两段各自的绝对高度差。该读数并不反映记录的绝对高度。宜把说明改成“连接点两端生成帧的竖直差”，或“后段连接点相对前段连接点的竖直残差”。

### L2 补全锚点的文档文字

docs/world_process_usage.md:78 仍写“补全轨迹由 transfer_demonstration 生成，锚点取前缀末帧的 ref 位置”。现行语义是“平移量 = 观测末帧 ref − 已匹配记录末帧 ref”。ref 静止时两种说法同值，ref 移动时不同。属纯文档项。

### C1 至 C3 未验证范围

- 未在真实 UnifiedWorldModel 历史上复现多实体同 tick 与无关实体插入；第 2 节读数全部来自构造输入。
- 默认门槛 0.7 与固定构造上界 0.666667 只差 0.033，换随机构造能否分开没有验证；该段读数不构成统计检验。
- 保留段末条的预测超出观察尾部时不计命中与偏差，该位置误差无法评估。

## 8. 代码最小性与输入不变

- 分支相对第 1 组基线（eba6575）的收口提交 `4c6e6e2` 改了 13 个文件；其中 process_ops.py 是新增模块（452 行），world_process.py 加 540 行、core.py 加 165 行、demo 加 263 行，其余是测试、文档与清单。
- 第 2 组功能只经 `event_echo_report`、`complete_process`、`compose_processes` 三个入口进入 core；core 侧对应 3 个调用点（6978、6994、6913 行），只做参数适配与转发，没有复制判据、没有静默回退。
- 未发现与功能无关的通用框架、回退或额外校验层。
- 输入不变：探针里补全与重组调用后，观测历史、记录列表与记录内嵌列表都与调用前深比较相等；事件回声模块对传入事件流只读取并建新列表。

## 9. 复跑命令

全部在仓根执行，`AUDIT` 代表 `artifacts/world_process_delivery_20261014/group2_final_audit`。

```bash
./.venv/Scripts/python.exe -X utf8 -B AUDIT/probe/audit_echo.py
./.venv/Scripts/python.exe -X utf8 -B AUDIT/probe/audit_ops.py
./.venv/Scripts/python.exe -X utf8 -B AUDIT/probe/audit_mutation.py
./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_event_echo.py -q -p no:cacheprovider
./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process.py -q -p no:cacheprovider
./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_process_ops.py -q -p no:cacheprovider
./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_core.py -q -p no:cacheprovider
./.venv/Scripts/python.exe -X utf8 -B tools/import_direction_lint.py --json
./.venv/Scripts/python.exe -X utf8 -B tools/verify_manifest.py --strict --quiet
./.venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py --out-dir AUDIT/demo_out
```
