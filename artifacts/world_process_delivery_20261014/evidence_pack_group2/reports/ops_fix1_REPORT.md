# process_ops 第 1 次返修报告

返修对象：[process_ops.py](lingshu/world/process_ops.py)、[test_process_ops.py](tests/test_process_ops.py)。
写入范围：以上 2 个文件与本目录。world_process.py、core.py、清单、文档、演示未改动，未执行 git 写操作。

## 0. 结论

3 项返修已完成，各有隔离副本变异自证。

| 项 | 结论 | 关键读数 |
|---|---|---|
| 尾部平移锚点 | 已改 | 观测等于记录前 6 帧时，生成尾部逐帧还原记录其余帧，ref 与 actor 都相等 |
| 采样节奏判据 | 已加 | 几何 6/6、时序 1/6 的记录匹配分数 0.166667，不命中 |
| 同名角色拒绝 | 已加 | complete 与 compose 两条入口都抛 ValueError |
| 原语义测试 | 保留 | 22 项通过（16 项原场景 + 6 项新增场景） |
| 隔离变异 | 9 项全红 | M7、M8、M9 分别指向本次 3 项新语义 |

模块指纹：process_ops.py = 59cde8f925fac077764acbba713c1de13432a22c6f8904d7fbe290019a93f2e7（24062 字节）；
tests/test_process_ops.py = 88990b118a2aa361a630259c01d01cd24a2b77ff6489179f0712df034e01437f（48023 字节）。

## 1. 接口增量

### 1.1 complete_process

前置条件新增 1 条：actor 与 ref 同名字符串时抛 ValueError，消息为
`actor 与 ref 不得同名：帧里的 entities 会互相覆写`。检查在生成任何帧之前执行。

`candidates[i]` 新增 2 组读数：

| 键 | 含义 |
|---|---|
| geometry.hit_frames / geometry.score | 只按水平偏移差 ≤ match_tol 计的命中帧数与比例 |
| timing.matched | 对比帧的 elapsed tick 逐帧全等 |
| timing.hit_frames / timing.score | elapsed tick 相等的帧数与比例 |
| timing.max_abs_delta_ticks | 各对比帧 elapsed 差的最大绝对值 |
| timing.deltas | 各对比帧的 elapsed 差（观测减记录，逐帧） |

语义变化：match_score 由几何命中率改为联合命中率，hit_frames 同步改为联合命中帧数。
候选排序键、对比帧数下限分支、tail_samples 分支、no-match 返回都保持原样。

`match` 新增 2 个键：matched_record_index（已匹配记录末帧在记录采样里的下标）、
matched_record_tick。`seam` 新增同名 2 个键，`seam.ref_anchor`、`seam.step` 沿用。

生成语义：平移量 = 观测末帧 ref − 已匹配记录末帧 ref；首帧 tick = 前缀末 tick +
已匹配末帧到下一采样的真实 tick 差。dt 按记录原值写入生成帧，不换算。

### 1.2 compose_processes

前置条件新增 1 条：roles 解析后 actor 与 ref 同名时抛 ValueError，消息同上。省略 roles 时
沿用前段角色名，前段记录本身同名同样拒绝。

`joint` 新增 2 个键：ref_y_gap、actor_y_gap，值为连接点两端原始竖直高度差的绝对值。
joint.continuous 判据不变，仍按水平 x/z 计算。relations 字段集不变。

## 2. 三项返修的判定与读数

### 2.1 尾部平移锚点

场景（`probe/probe_fix1_readings.py` 的 A1）：leader 每 tick 走 +1.0，walker 相对偏移逐帧
收窄 0.25，记录 12 条采样、1 tick 一帧，观测前缀 = 记录前 6 帧。

    frames_ticks = [7, 8, 9, 10, 11, 12]
    generated_ref_x = [16, 17, 18, 19, 20, 21] = observed_ref_x（逐帧相等）
    ref_equal = true, actor_equal = true
    match_translation = [0.0, 0.0, 0.0]
    match_matched_record_index = 5, match_matched_record_tick = 6

反例取同一场景，按改前口径计算：把首条未观测采样的 ref 移到观测末帧 ref 的位置。
此时平移量变成 [-1.0, 0.0, 0.0]，
首帧 ref.x = 15.0，而该 tick 的真值是 16.0，整段尾部慢一帧。读数键
`old_anchor_translation`、`old_anchor_first_ref_x`、`truth_first_ref_x` 记录这三个值。
ref 静止时两种口径同值，所以原测试覆盖不到这条。

平移不变量（A2）：只把观测场景加 (100.0, 5.0, -30.0)、记录不动时，translation 等于该位移，
生成尾部与平移后的观测逐帧相等（ref_equal、actor_equal 都为 true）。

### 2.2 采样节奏判据

场景（B1）：同一轨迹的 4 tick 一帧观测（ticks [1, 5, 9, 13, 17, 21]）配 1 tick 一帧的记录。

    geometry = {hit_frames: 6, score: 1.0}
    timing = {matched: false, hit_frames: 1, score: 0.166667,
              max_abs_delta_ticks: 15, deltas: [0, 3, 6, 9, 12, 15]}
    hit_frames = 1, match_score = 0.166667, matched = false
    reason = 采样节奏不匹配：elapsed tick 有 5/6 帧与记录不同
    match = None, frames = []

阈值放宽到 min_hit_ratio = 0.5 后仍不命中（0.166667 < 0.5）。几何逐帧相同也挡不住这条：
采样节奏不同时匹配分数到不了 1.0。

同 dt 平移（B2）：观测 tick 整体 +100 时命中，生成 tick = [107..112]；记录 tick 整体 +500 时
命中，match.source.ticks = [501, 512]，生成 tick 仍是 [7..12]，match.tick_offset = -500。
判据只比相对首帧的 elapsed，绝对 tick 不进生成时间轴。

非均匀 dt 与缺帧（C1）：walker 在第 4、11 tick 缺观测，记录 16 条采样，
dt = [0, 1, 1, 2, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1]。

    前缀 = 6 条采样（ticks [1, 7]），timing.matched = true，matched = true
    frames_ticks = [8, 9, 10, 12, 13, 14, 15, 16, 17, 18]
    tick 4 与 tick 11 没有生成帧：模块不插值补帧
    同一前缀配 dt 全为 1 的记录：timing 3/6，不命中，reason 报采样节奏
    前缀只有 2 条采样：reason = 对比帧数 2 少于下限 3（原逻辑）

### 2.3 同名角色拒绝

`probe/probe_fix1_readings.py` 的 E1 覆盖 3 条路径，都抛 ValueError，消息逐字相同：

    complete(history, "walker", "walker", records)
    compose(first, second, roles={"actor": "hero", "ref": "hero"})
    compose(同名记录, second)              # 省略 roles，沿用前段角色名

角色不同名时照常产出帧，帧里含 2 个角色名。

### 2.4 joint 竖直读数

场景（D1）：后段记录的 rel.y 比前段大 50，两段水平偏移相同，连接点位置重合。

    actor_gap = 0.0, ref_gap = 0.0, actor_y_gap = 50.0, ref_y_gap = 0.0
    joint_tol = 0.5, continuous = true

横向连续不再被读成三维连续：高度差由 actor_y_gap 单独上报，判据不变。

## 3. 精确命令与读数

每条命令从仓根运行，`RSM` 代表 `artifacts/world_process_delivery_20261014/group2_ops_fix1`。

| 命令 | 读数 |
|---|---|
| `./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_process_ops.py -q -p no:cacheprovider` | 22 passed in 0.56s（`RSM/logs/pytest_process_ops.txt`） |
| `./.venv/Scripts/python.exe -X utf8 -B RSM/probe/probe_fix1_readings.py` | 8 组读数写入 `RSM/logs/probe_fix1_readings.json` |
| `./.venv/Scripts/python.exe -X utf8 -B RSM/probe/probe_isolated_mutation_fix1.py` | baseline pass、M1 至 M9 全部 assertion_error、module_unchanged = true（`RSM/logs/probe_isolated_mutation_fix1.json`） |
| `./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_core.py -q -p no:cacheprovider` | 1 failed, 24 passed, 1 skipped（失败项见第 5 节，不属本组） |
| `./.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_manifest_integrity.py -q -p no:cacheprovider` | 2 failed, 18 passed（process_ops.py 指纹待重算，见第 6 节） |
| `./.venv/Scripts/python.exe -X utf8 -B tools/verify_manifest.py --strict --quiet` | SUMMARY entries=64 match=62 mismatch=2 missing=0；exit=3 |

隔离变异逐项读数（M7、M8、M9 对应本次新语义）：

    M1 形状不同的前缀不得命中            红
    M2 补全首帧 tick 必须接在前缀末 tick 之后          红
    M3 偏移差超容差的连接点必须判为不连续            红
    M4 后段 ref 必须锚定到前段末帧 ref            红
    M5 重组帧必须只带新绑定的角色名            红
    M6 候选必须按匹配分数降序给出            红
    M7 移动 ref 的补全尾部必须逐帧还原记录的后续帧            红
    M8 elapsed tick 不同的记录不得命中            红
    M9 同名角色的重组必须被拒绝            红

9 个变异点的源码串都唯一（point_unique_in_source = true），变异包落在系统临时目录，
仓库源码 sha256 调用前后不变。本次未使用“把水平距离改 3D”的变异。

## 4. 证据文件

    RSM/REPORT.md                                       本文件
    RSM/logs/pytest_process_ops.txt                     22 项通过的原始输出
    RSM/logs/pytest_core.txt                            core 组测试原始输出
    RSM/logs/pytest_manifest.txt                        清单测试原始输出
    RSM/logs/probe_fix1_readings.json                   第 2 节全部读数
    RSM/logs/probe_isolated_mutation_fix1.json          9 个隔离变异逐项读数
    RSM/logs/verify_manifest_fix1.json                  清单 --json 全量输出
    RSM/logs/probe_fix1_stdout.txt                      读数探针的标准输出
    RSM/logs/probe_mutation_stdout.txt                  隔离变异探针的标准输出
    RSM/logs/stc_check.txt                              REPORT.md 的 STC 检查（0 处需修改）
    RSM/logs/stc_check_module.txt                       process_ops.py 的 STC 检查
    RSM/logs/stc_check_tests.txt                        test_process_ops.py 的 STC 检查
    RSM/logs/stc_check_probe.txt                        两个探针的 STC 检查（0 处需修改）
    RSM/logs/stc_review_notes.md                        STC 待确认项的逐条复核与保留理由
    RSM/probe/probe_fix1_readings.py                    读数探针
    RSM/probe/probe_isolated_mutation_fix1.py           隔离变异探针

## 5. 限制

1. min_hit_ratio 放宽到 1.0 以下时，elapsed tick 不同的候选也可能命中：只要联合命中帧
   比例达到该阈值。默认值 1.0 要求每一帧的几何与时序同时命中，放宽后调用方须读
   timing.matched 判定。首版不做重采样、时间缩放与插值，判据要求对比帧的 elapsed 逐帧相等。
2. 平移量取已匹配末帧的单点差，不做多点平均或最小二乘。观测末帧 ref 或记录末帧 ref
   带噪声时，该误差整段平移尾部。
3. 未跑仓库全量 pytest 与 18 个脚本式测试，只跑本组单测、core 组测试与清单测试。
4. 未重跑真实脑端往返；core 组测试覆盖本地记忆与 record 两条路径，脑端路径沿用旧结论。
5. 非均匀 dt 场景用手工构造的缺帧观测，真实运行数据的 dt 分布未统计。
6. STC 检查在 process_ops.py 与 test_process_ops.py 上各留下 23 处与 123 处待确认项：
   除 2 处边界说明外都是检查器把 Python 的 `"""` 与 `"` 当成中文引号。逐条理由见
   `RSM/logs/stc_review_notes.md`。

## 6. 需要同步的语义（本任务不修改这些文件）

### 6.1 core.py

- 第 6864 行起的 `compose_processes` 与第 6946 行起的 `complete_process` 直接转发调用。
  模块新增的同名角色 ValueError 会穿透 `engine.world_process`，门面宜捕获后返回
  `{"status": "error", "error": …}`，与其它参数错误的返回形状一致。
- 门面返回值随模块返回值变化：completion 的 candidates 多出 geometry 与 timing 两组读数，
  match 与 seam 多出 matched_record_index、matched_record_tick；composition 的 joint 多出
  ref_y_gap、actor_y_gap。门面只转发，无需改代码，但 usage 的返回键表要补。
- 未注册模块时 complete 与 compose 的 error 文案不变。

### 6.2 docs/world_process_usage.md

- 第 69 至 73 行：判据描述缺“相对首帧的 elapsed tick 逐帧相同”这一条；匹配分数现在是
  几何与时序的联合命中率；候选多出 geometry 与 timing 两组读数；锚点句子写的是
  “锚点取前缀末帧的 ref 位置”，应改成“观测末帧 ref 与已匹配记录末帧 ref 之差”。
  同名角色抛 ValueError 也要写进前置条件。
- 第 75 至 77 行：joint 缺 ref_y_gap 与 actor_y_gap 两个读数；需写明 continuous 只覆盖水平面。
- 第 110 至 121 行的 action 表：complete 与 compose 两行需补“同名角色返回 ValueError”的语义。

### 6.3 清单与其它任务

- `docs/intake/body-export-v0.1/_export_manifest.json` 的 process_ops.py 条目指纹过期：
  登记 16a2a5c1f7d876b4，实算 59cde8f925fac077。该条须由整合任务重算。
- 同一次核验还有 1 条失配：world_process.py 登记 5f4521798a3a74e6、实算 052b567ca1c3466c
  （51250 字节），来源是并行回声改动，不是本组写入。
- `tests/test_world_process_core.py::test_i1_echo_forwards_report_like_module` 失败在
  `report["split"]`：期望 holdout_events = 5、boundary_tick = 74，实得 holdout_events = 11、
  boundary_tick = 35。该断言只碰 event_echo_report，process_ops 不参与 echo 计算。
- `tests/test_world_process_core.py` 的 I3、I5 覆盖 complete 与 compose 的转发与 memory_id
  路径，本次未改。新增语义（同名角色拒绝、时序判据、joint 高度读数）在该文件没有断言，
  整合任务可按需补。
