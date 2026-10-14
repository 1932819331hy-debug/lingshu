# 世界过程 · 使用说明

本文说明时间会意、示教迁移、运动组合体（第 1 组）与事件回声、过程补全、过程重组
（第 2 组）六项能力**在 core 与记忆通路里的用法**。
判据本身在 world 模块；本文只写怎么调、历史从哪来、记录落在哪、演示怎么跑。
术语按 [docs/world_process_terms.yaml](world_process_terms.yaml) 写。

## 一、代码位置

| 面 | 文件 |
|---|---|
| 时间会意 · 示教迁移 · 事件回声 | lingshu/world/world_process.py |
| 运动组合体 | lingshu/world/motion_groups.py |
| 过程补全 · 过程重组 | lingshu/world/process_ops.py |
| 显式注册 | lingshu/world/__init__.py · register_world_process |
| core 门面 | lingshu/core/core.py · SpacetimeMemoryEngine.world_process |
| 单测 | tests/test_world_process.py · tests/test_motion_groups.py · tests/test_event_echo.py · tests/test_process_ops.py · tests/test_world_process_core.py |
| 演示 | tools/demo_world_process.py |

三个 world 模块是**本仓原生开发**（非 AEIS 导出件），来源标注见
[docs/architecture.md](architecture.md) §七，指纹登记见本文 §八。

## 二、模块公开接口

```python
recognize_processes(history, *, thresholds=None, entities=None) -> list
extract_relative_trajectory(history, actor, ref, *, process=None) -> dict
transfer_demonstration(record, actor, ref, *, translation=(0, 0, 0),
                       yaw=0, scale=1, tick_offset=0) -> dict
motion_events(history, eid, *, thresholds=None) -> list
event_echo_readings(history, *, thresholds=None, entities=None) -> list
echo_lag_associations(events, *, max_lag=12, min_support=2,
                      min_consistency=0.7) -> list
echo_split(events, holdout_fraction=0.3) -> dict
evaluate_event_echo(dev_events, holdout_events, *,
                    max_lag=12, min_support=2, min_consistency=0.7) -> dict
event_echo_report(history, *, thresholds=None, max_lag=12, min_support=2,
                  min_consistency=0.7, holdout_fraction=0.3,
                  entities=None) -> dict

detect_motion_groups(history, window=8, tol_pos=0.05,
                     min_samples=2, min_speed=1e-6) -> dict
get_motion_group(model, eid) -> dict | None
predict_group_members(group, observed, tick=None) -> dict
predict_with_groups(history, window=8, tol_pos=0.05,
                    min_samples=2, min_speed=1e-6, tick=None) -> dict

complete_process(history, actor, ref, records, *,
                 thresholds=None) -> dict
compose_processes(first, second, *, roles=None, translation=(0, 0, 0),
                  yaw=0, scale=1, tick_gap=1, thresholds=None) -> dict
```

history 的形状是 `[{"tick": int, "entities": {eid: [x, y, z]}}]`，与
UnifiedWorldModel.perceive 记录的 4D 历史同形。模块只读该列表。

detour_return 的判据只看水平位置：actor 自身**绝对轨迹**的闭合回路等效半径、
actor 绕 ref 的方位角扫描、相对轨迹不穿越参照物（最近距离 > crossing_tol 0.05）。
三项证据缺一不可，绝对回路判据与穿越容差都不随参照物移动改变。

transfer_demonstration 的 yaw 与 scale 是**整体场景变换**，锚点是记录首帧的 ref
位置：参照物与执行者走同一条变换。参照物静止时位移为 0，只有相对偏移随 yaw 与
scale 变化。scale 缩放的是**相对偏移**，不是物体的渲染尺寸。compose_processes 的
yaw 与 scale 沿用同一条变换，两段共用。

event_echo_report 的事件源是 motion_events 的停止与转向事件。开发段用来学习固定签名对
(source_eid, source_event, target_eid, target_event) 的延迟关联：count = 主 lag 的计数，
须达到 min_support；consistency = count ÷ 该源签名在 1..max_lag 内有任意响应的触发数，
须达到 min_consistency。consistency 混合目标选择与 lag 集中两种成分；lag_consistency =
count ÷ 该签名对全部 lag 计数之和，只作原始读数。留出段条数 = ceil(事件数 ×
holdout_fraction)，从事件流尾部切出，同一 tick 的事件整组进同一段；留出段用来比较延迟
模型（预测 tick = 当前 tick + lag）与无延迟模型（预测 tick = 当前 tick）。
**关联只表示两个事件在时间上的先后与间隔，不表示因果作用**：报告字段里没有因果结论。

complete_process 的判据把观测前缀的 actor 相对 ref 水平偏移逐帧与记录的采样比较。
命中同时要求四件事：逐帧偏移差不超过 match_tol、对比帧相对首帧的 elapsed tick 逐帧
相同、对比帧数达到 min_match_samples、记录还有 min_tail_samples 条未被前缀覆盖。
match_score 是几何与时序的**联合**命中率：同一帧的几何与时序都命中才计一帧。首版
不做重采样、时间缩放与插值，判据要求对比帧的 elapsed tick 逐帧相等；绝对 tick 不进
生成时间轴，两端整体平移不改变命中结果。

补全轨迹由 transfer_demonstration 生成：平移量 = 观测末帧 ref − 已匹配记录末帧 ref，
首帧 tick 接在前缀末 tick 之后，后续按记录的 dt 逐帧还原。**不命中不算失败**：
match 为 None、frames 为空列表，候选列表仍逐条给出来源、几何读数 geometry、时序读数
timing 与匹配分数。actor 与 ref 同名时模块抛 ValueError。

compose_processes 首版只连接 2 个过程，不做多路串联。连接点取参照物：后段的 ref
锚定到前段末帧的 ref 位置，两段的 actor 衔接距离写进 joint.actor_gap，判据是
joint_tol。**连接点不达标时照常生成新轨迹**，joint.continuous 记 False。
joint.continuous 只按水平 x/z 判定；joint.ref_y_gap 与 joint.actor_y_gap 是连接点
两端的原始高度差，只作读数，不参与判定。本能力只给几何衔接读数，**不宣称两段动作
在可执行性上兼容**。roles 解析后 actor 与 ref 同名（含省略 roles 时沿用前段角色名）
时模块抛 ValueError。

## 三、显式注册

core 不导入 world（依赖方向是 world → core）。调用 engine.world_process 前，该引擎
实例须先注册一次三个 world 模块；新建引擎与重开引擎都要各注册一次。

```python
from lingshu.core.core import SpacetimeMemoryEngine
from lingshu.world import register_world_process

engine = SpacetimeMemoryEngine(db_path="store.db")
register_world_process(engine)         # 只作用于这一个实例
engine.world_process("recognize", {})  # 未注册时这一步返回 error
```

边界：

- 注册只在调用时发生：import lingshu.world 不注册任何引擎，也不改引擎状态；
- 注册面转交三个模块引用（world_process、motion_groups、process_ops）；核心侧
  register_world_process 的第三个参数可省略，省略时第 2 组三动作返回 error、第 1 组
  七动作照常可用；
- 只作用于传入的实例：别的引擎保持未注册，模块级不放模块引用；
- 未注册时 world_process 返回 `{"status": "error", "error": "世界过程模块未注册…"}`，
  error 里写明上面两行初始化步骤，不做静默导入，也不回退到别处的模块；
- 注册入口只接引擎对象：非引擎对象上没有本入口，当场抛 AttributeError。

## 四、core 门面

```python
engine.world_process(action, params)   # 返回 {"status": "ok", ...} 或 {"status": "error", ...}
```

| action | 作用 | 主要参数 | 返回键 |
|---|---|---|---|
| recognize | 识别过程（时间会意） | history 或 source，entities，thresholds | processes，history_len |
| events | 单实体的停止与转向事件 | eid，history 或 source，thresholds | eid，events |
| extract | 提取相对轨迹并写入记忆 | actor，ref，history 或 source，process，importance，brain | memory_id，record |
| transfer | 绑定新 actor/ref 生成帧 | actor，ref，record 或 memory_id，translation，yaw，scale，tick_offset | transfer |
| load | 按 memory_id 取回记录 | memory_id，brain | memory_id，record |
| groups | 检测运动组合体 | history 或 source，window，tol_pos，min_samples，min_speed | model |
| predict_groups | 遮蔽成员推算 | hide，history 或 source，observed，tick | tick，group_ids，predictions，unresolved |
| echo | 事件回声报告 | history 或 source，max_lag，min_support，min_consistency，holdout_fraction，entities，thresholds | report，history_len |
| complete | 过程补全 | actor，ref，history 或 source，record 或 record_ids 或 memory_id，thresholds | completion；同名角色返回 error |
| compose | 过程重组 | first/second 或 first_id/second_id，roles，translation，yaw，scale，tick_gap，thresholds，brain | composition；非法输入返回 error |

未列出的 action 返回 `{"status": "error", "error": "未知动作 …"}`，不做回退。
complete 与 compose 把模块抛出的 ValueError 就地转成
`{"status": "error", "error": "…"}`，error 文案与模块逐字相同。这两处只捕 ValueError，
TypeError 等其它异常照旧上抛，第 1 组七动作的异常契约不变。
thresholds 按模块对象原样转发：第 1 组与事件回声传 ProcessThresholds 实例
（lingshu/world/world_process.py），过程补全与过程重组传 ProcessOpsThresholds 实例
（lingshu/world/process_ops.py）；门面不转换、不吞参数。
组检测参数、回声参数与补全重组参数的缺省和全部判据都在 world 模块，门面只转发调用方
显式给出的键；`translation` 缺省 (0, 0, 0)，`yaw` 缺省 0，`scale` 缺省 1，
`tick_gap` 缺省 1，`max_lag` 缺省 12，`min_support` 缺省 2、
`holdout_fraction` 缺省 0.3。

`load`、`transfer`、`compose` 不读观测历史：这三个动作在没有世界模型的实例上也能用，
只要记录能从 record 参数或 memory_id 取到。其余动作缺 history 与来源时返回 error。

### 观测历史从哪来

省略 history 时，门面读**当前已有模型**的历史：

- source=world_model（缺省）：读 UnifiedWorldModel.history；
- source=world_learner：读 WorldLearner.history。

WorldLearner.history 的实体值是 `{"category": …, "pos": (x, y, z)}`，与
UnifiedWorldModel 的 `[x, y, z]` 不同；这个形状差异**只在门面适配一次**
（_world_process_history），world 模块只认扁平形状。

模型不存在且未给 history 时返回 error：门面不新建场景、不伪造观测。要造场景先用
world_model(action="init"/"create"/"entity"/"perceive")。

### 记录落在哪

提取走既有摄取通路 add_perception，落进既有 SQLite store（nodes 表），标签为
world_process 与 relative_trajectory，返回的 memory_id 即节点 id。不新建 JSON 库、
不新建表。

- load 用 store.get_node(memory_id) 读回正文并解析成记录；
- 关引擎后用**同一个 db 文件**新建引擎，load 仍取回同一条记录，transfer 结果与直接
  传 record 逐位相同；
- 给 brain（lingshu.world.brain_store.connect 的返回值）时，extract 同写脑端
  （BrainEngine.add_perception），load 与 transfer 经 BrainStore.read_content 从脑端
  按 memory_id 读回。协议、连接白名单与超时未改。

```python
got = engine.world_process("extract", {"actor": "walker", "ref": "landmark"})
moved = engine.world_process("transfer", {"memory_id": got["memory_id"],
                                          "actor": "a2", "ref": "r2",
                                          "translation": (4, 0, 0), "yaw": -0.785,
                                          "scale": 2.0})
```

### 组检测与遮蔽成员推算

groups 返回模块的组模型（成员、锚点、偏移、共同位移、窗口与证据）。predict_groups
先用同一窗口检测组，再把 hide 里的实体从本帧可见观测里去掉，由组模型推算被遮蔽
成员的落点：预测项带 source="motion_group" 与 actual=False，组内没有可见成员时落进
unresolved，不给坐标。

```python
seen = history[:6]                       # 三名成员仍成组的观察窗口
got = engine.world_process("predict_groups",
                           {"history": seen, "window": 6, "hide": ["m_c"]})
got["predictions"]["m_c"]["pos"]         # 推算落点
```

### 事件回声

echo 返回 report：事件流 events、开发段与留出段切分 split、延迟关联 associations、
两模型对照 evaluation。留出段只用来对照，不参与关联学习。

```python
report = engine.world_process("echo", {})["report"]
report["split"]["holdout_events"]             # 留出条数 = ceil(事件数 × holdout_fraction)
report["split"]["dev_events"]                 # 开发条数 = 事件数 − 留出条数
report["associations"][0]                     # after/before/lag/count/lag_counts/
                                              # consistency/lag_consistency/triggers/
                                              # sources_total/sources_with_pair
report["evaluation"]["lag_model"]["hits"]     # 延迟模型在留出段的命中数
report["evaluation"]["lag_model"]["coverage"] # = predictions ÷ events
report["evaluation"]["lag_model"]["prediction_beyond_window"]   # 右侧删失条数
report["evaluation"]["no_delay_model"]["hits"]
```

关联口径：签名对 = (source_eid, source_event, target_eid, target_event)。对每个源事件按
(target_eid, target_event) 分桶，每桶只记最早的一条响应，lag = 目标 tick − 源 tick，取
1..max_lag。count 是主 lag 的计数，须达到 min_support；consistency = count ÷ 该源签名在
1..max_lag 内至少有一条响应的源事件数（sources_with_pair），须达到 min_consistency。
consistency 混合两种成分：分子只覆盖一个目标签名（目标选择），分母覆盖该源签名对所有
目标签名的响应（lag 集中），不单独衡量其中任何一项。lag_consistency = count ÷ 该签名对
全部 lag 计数之和，只作原始读数，不参与门槛。sources_total 是该源签名的事件总条数，
sources_with_pair ÷ sources_total 是响应覆盖率。

命中口径：真实响应取固定目标签名在源事件之后最早的一条事件；命中要求 tick、eid、event
三者与预测全等。源事件自身不算响应。两个模型共用同一触发集合、同一目标签名与同一响应
对象，差别只有 lag：延迟模型用学到的 lag，无延迟模型用 0。

留出段读数分四类：predictions（给出预测的条数）、no_prediction（没有预测的条数）、
scored（判据分辨出结果的条数，含命中与落空）、prediction_beyond_window（预测 tick 超出
已观察尾部的条数）。predictions + no_prediction = events，scored + unscored = events，
scored + no_prediction + prediction_beyond_window = events，hits ≤ predictions ≤ events。
coverage = predictions ÷ events，scored_rate = scored ÷ events。偏差只统计判据分辨出结果、
且响应落在观察窗口内的预测。

右侧删失范围：观察窗口止于事件流最后一条事件的 tick。预测 tick 大于该 tick 的行记
end_state = "beyond_window"，不计命中、不计偏差，按 prediction_beyond_window 单列计数，
不与普通落空混在一起。

读数只说明事件在时间上相隔几个 tick。要主张因果，须另给对照实验，本能力不做这件事。

### 过程补全

complete 用观测前缀匹配已记录过程，命中后输出该记录的后续轨迹。记录给 record（单个）
或 record_ids（序列），也可给 memory_id 按记忆读回；两条路径结果一致。

```python
got = engine.world_process("complete", {
    "history": history[:6], "actor": "walker", "ref": "landmark",
    "memory_id": record_id})
got["completion"]["match"]["source"]      # 命中候选的来源（index / kind / ticks）
got["completion"]["match"]["match_score"] # 联合命中率（几何与时序）
got["completion"]["match"]["timing"]      # 时序读数（matched / hit_frames / score / deltas）
got["completion"]["match"]["translation"] # 观测末帧 ref − 已匹配记录末帧 ref
got["completion"]["match"]["matched_record_index"]  # 已匹配记录末帧的下标
got["completion"]["match"]["matched_record_tick"]   # 已匹配记录末帧的 tick
got["completion"]["candidates"][0]["geometry"]      # 只按水平偏移差计的命中帧数与比例
got["completion"]["ticks"]                # 补全轨迹的 tick 区间
got["completion"]["seam"]                 # 首帧接缝读数（actor_gap / step / ref_anchor）
miss = engine.world_process("complete", {...形状不同的前缀...})
miss["completion"]["match"]               # None：不命中
miss["completion"]["frames"]              # []：不给轨迹
```

不命中不是错误：前缀形状与记录不符时返回 match=None 与空 frames，候选列表仍给出每条
记录的来源、几何与时序读数、匹配分数与不命中原因（reason）。对比帧数不足、记录没有
采样同样走这条路。补全帧按记录的 dt 逐帧还原，不做时间缩放与插值：记录缺帧的位置不
生成帧。

seam.ref_anchor 是观测末帧的 ref 位置，seam 的 matched_record_index 与
matched_record_tick 与 match 同名两键一致。actor 与 ref 同名时门面返回 error，error
文案是「actor 与 ref 不得同名：帧里的 entities 会互相覆写」。

### 过程重组

compose 连接 2 个已记录过程，重新绑定角色，并给出连接点与过程关系读数。

```python
got = engine.world_process("compose", {
    "first_id": first_record_id, "second_id": second_record_id,
    "roles": {"actor": "hero", "ref": "mark"}, "translation": (10.0, 0.0, 5.0)})
got["composition"]["joint"]        # ref_gap / actor_gap / ref_y_gap / actor_y_gap /
                                   # joint_tol / continuous / tick
got["composition"]["relations"]    # 两段的类型、首末阶段与角色关系
got["composition"]["frames"]       # 两段帧，tick 相接（间隔 = tick_gap）
```

首版只连 2 段，接口不接受 3 段以上的串联。连接点不达标（actor_gap 大于 joint_tol）时
照常生成新轨迹，joint.continuous 记 False，判据与读数都在结果里。记录没有过程标注时
relations.same_type 为 None，表示未知，不猜。

joint.continuous 只看水平 x/z；joint.ref_y_gap 与 joint.actor_y_gap 是连接点两端的
原始高度差，只作读数，高度差再大也不改 continuous。本能力给的是几何衔接读数，
**不表示两段动作在可执行性上兼容**。

同名角色是非法输入：roles 解析后 actor 与 ref 同名，或省略 roles 而前段记录本身同名，
门面都返回 error。roles 缺键、tick_gap 小于 1、记录没有采样同样返回 error。

## 五、演示与对照图

```bash
.venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py \
    --out-dir artifacts/world_process_demo
```

参数：--out-dir（缺省 artifacts/world_process_demo）、--width / --height
（对照图尺寸，缺省 720x480；GIF 按 2/3 缩小到 480x320）。

演示做五件事：

1. 经 core 的 world_model 门面把观测逐 tick 送进 UnifiedWorldModel，再从该模型的
   真实历史识别过程（本例识别出 approach_stay_leave）；
2. 提取相对轨迹并落库，关引擎后按 memory_id 取回同一条记录，再出三张对照图；
3. 三名成员成组、其中一名离开，出三张画面与遮蔽成员的推算位置；
4. 事件回声场景（每 13 步一轮，第 3 步停一步、第 8 步左转 90°，共 8 轮）出事件
   时序图与两模型读数；
5. 接近→停留→离开场景出“观测末帧 / 补全末帧”对照图，两段记录出“前段终点 /
   连接点 / 后段终点”对照图。

### 输出文件

| 文件 | 内容 |
|---|---|
| compare.png | 源 / 平移 / 旋转缩放三张同视角并排对照图（缺省三张 720 并排 = 2160x480，不缩放即可比对） |
| source.png · source.gif | 原坐标回放（translation (0,0,0)、yaw 0、scale 1） |
| translated.png · translated.gif | 平移回放（translation (4,0,0)） |
| rotated_scaled.png · rotated_scaled.gif | 关系迁移（yaw −0.785 rad、scale 2.0） |
| groups_together.png | 三名成员成组（第 6 tick） |
| groups_leave.png | 一名成员离开（第 12 tick） |
| groups_hidden.png | 同一成组帧去掉被遮蔽成员，红色十字为组模型的推算位置 |
| groups.gif | 组场景全过程动画 |
| echo_timeline.png | 事件时序图：实线按事件类型分色，红色标记是延迟模型在留出段的预测落点 |
| completion.png | 过程补全对照：观测前缀末帧与补全轨迹末帧（同一台相机） |
| composition.png | 过程重组对照：前段终点、连接点与后段终点（同一台相机） |
| readings.json | 机器读数 |
| world_process.db · motion_groups.db | 两次演示写入的既有 SQLite store |

三张对照图与三张组画面都用 World3D 的既有渲染路径（World3D.render）出图；对照图
三张共用一台相机，GIF 另用一台，避免运动出画。GIF 由 PIL 把同一相机的帧按序保存，
不新增动画模块（world_generator 的 video 动作从文字生成帧，与本演示的帧来源不同）。

### 机器读数

readings.json 的键：

- processes：模块识别出的过程列表（类型、角色、时段、阶段、依据）；
- memory：memory_id、记录的角色与样本数，以及重建引擎后取回是否一致；
- panels[]：每个变体的变换参数、生成帧来源、tick_shown、逐 tick 位置（entities）与
  该帧实际画出的物体（objects_in_shown_frame：eid、类别、颜色、形状、中心）；
- groups：成组窗口与全窗口的组模型，以及组画面的实体位置；
- masked_prediction：被遮蔽实体、推算值、真实值、误差、可见成员清单；
- echo：事件回声读数（事件流、切分、延迟关联、两模型命中数与偏差）与事件时序图；
- completion：补全命中的来源与匹配分数、接缝读数、候选列表，以及不命中反例的读数；
- composition：连接点读数（ref_gap、actor_gap、continuous）、过程关系与两段 tick；
- camera：panel（对照图）、gif、group（组画面）、echo（补全对照图）、joint（重组
  对照图）五台相机的六参数。

图上实体与读数一一对应：进渲染的 entities 就是同一份读数里的 entities，颜色取自
World3D 的类别先验（例如 landmark 用 tree 的绿色，m_c 用 fox 的橙色）。图上标签是
ASCII（默认字体无中文字形），中文说明只在读数与本文档里。

## 六、边界

- 门面只做参数适配与转发，不复制判据；换 world 模块只改门面一层；
- 门面只读历史：生成帧只出现在返回值里，不写进 actual 历史，读当前模型不触发模式
  重算，已有预测缓存不变；
- 不做多层回退：未知 action 与缺参返回 error dict，不静默落到别的动作；
- 演示里的过程类型、组模型与推算值全部取自模块返回，脚本不写死答案；
- 绕行判据的已声明限制：路径贴近参照物（0.05 以上）经过、而回路实际在远处的对抗
  构造可能误报。穿越容差 0.05 是绝对量（真穿越的读数为 0，容差只覆盖浮点与采样
  表述误差），不随参照物尺寸变化；回路等效半径取 actor 自身绝对轨迹，不随参照物
  移动改变；
- 已声明限制（漏报方向）：参照物在窗口内去回振幅超过 actor 回路半径约 18 倍时，
  合法绕行可能漏报（复审实测翻转点：ref 去回振幅 55.1、actor 回路半径 2.97）；
- 事件回声只认定延迟关联：报告不含因果结论，留出段的命中数只说明延迟模型更贴近
  事件的真实时间间隔；consistency 混合目标选择与 lag 集中两种成分，不单独衡量其中
  一项；min_consistency 默认 0.7 只在固定构造上校准过，不保证随机序列不出现偶然候选；
- 过程补全不命中不算错误：match 为 None、frames 为空列表，候选列表仍逐条给出读数；
- 过程重组首版只连 2 段：接口与判据都不接受 3 段以上的串联；连接点不达标照常出轨迹，
  joint.continuous 记 False；joint.continuous 只覆盖水平面，ref_y_gap 与 actor_y_gap
  只是读数，不构成动作可执行性兼容的结论；
- 过程补全的匹配只比相对首帧的 elapsed tick：不做重采样、时间缩放与插值，采样节奏不同
  的记录不命中；平移量取单点差，观测末帧或记录末帧带噪声时误差会整段平移尾部；
- 同名角色是 complete 与 compose 的非法输入：模块抛 ValueError，门面转成 error dict；
  这两处只捕 ValueError，TypeError 等其它异常照旧上抛；
- 未验证：演示只跑通接近→停留→离开一例，其余过程类型的渲染效果未逐一验证；脑端
  MCP 往返需要 MDCG_BRAIN_PYTHONPATH 前置，缺前置跳过。

## 七、复跑命令

```bash
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_motion_groups.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_event_echo.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_process_ops.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_core.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py --out-dir artifacts/world_process_demo
.venv/Scripts/python.exe -X utf8 -B tools/import_direction_lint.py --json
```

上述脚本与演示都按第三节先注册再调用；import_direction_lint 的 core→world 计数只减
不增（本次删除 core 侧两行动态导入后计数不变，未抬基线）。

## 八、清单登记与核验

三件模块的指纹登记在 [导出清单](intake/body-export-v0.1/_export_manifest.json) 的
`blocks.world.files`，文件名为 `world_process.py`、`motion_groups.py` 与
`process_ops.py`。

- 三条记录只写 `origin: "lingshu_native_development"`，**不写 `src`**：`src` 是 AEIS
  导出源字段，三件是本仓开发，不伪装上游来源；
- `tools/verify_manifest.py` 逐条核验 LF 归一后的 sha256。两份清单合计 **64 条**
  （v0.1 55 条、v0.2 9 条），本次实跑 64 条全部 MATCH；
- 顶层 `world_process_first_batch` 只留批次元数据与 `verified_records` 指向，不重复登记
  指纹，避免出现第二份不被核验的副本；
- [tests/test_manifest_integrity.py](../tests/test_manifest_integrity.py) 断言 18 至 20 钉住
  这三点，并保留坏指纹与缺失文件反例；
- 工具的附加字段 `origin_class` 把条目分为 origin-only / src-only / both / neither，
  只作上报，不改判定与退出码。

改动这三个模块后必须重算登记指纹，否则严格核验退出 3（MISMATCH）。
世界过程第 2 组同时改了 `core.py` 与 `world_process.py`，并新增 `process_ops.py` 的登记；三条记录的指纹都已重算。
指纹口径是 LF 归一后的 sha256，清单记前 16 位。

```bash
.venv/Scripts/python.exe -X utf8 -B tools/verify_manifest.py --strict --quiet
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_manifest_integrity.py -q -p no:cacheprovider
```
