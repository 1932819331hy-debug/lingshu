# 世界过程第 1 组 · 使用说明

本文说明时间会意、示教迁移、运动组合体三项能力**在 core 与记忆通路里的用法**。
判据本身在 world 模块；本文只写怎么调、历史从哪来、记录落在哪、演示怎么跑。
术语按 [docs/world_process_terms.yaml](world_process_terms.yaml) 写。

## 一、代码位置

| 面 | 文件 |
|---|---|
| 时间会意 · 示教迁移 | lingshu/world/world_process.py |
| 运动组合体 | lingshu/world/motion_groups.py |
| 显式注册 | lingshu/world/__init__.py · register_world_process |
| core 门面 | lingshu/core/core.py · SpacetimeMemoryEngine.world_process |
| 单测 | tests/test_world_process.py · tests/test_motion_groups.py · tests/test_world_process_core.py |
| 演示 | tools/demo_world_process.py |

两个 world 模块是**本仓原生开发**（非 AEIS 导出件），来源标注见
[docs/architecture.md](architecture.md) §七，指纹登记见本文 §七。

## 二、模块公开接口

```python
recognize_processes(history, *, thresholds=None, entities=None) -> list
extract_relative_trajectory(history, actor, ref, *, process=None) -> dict
transfer_demonstration(record, actor, ref, *, translation=(0, 0, 0),
                       yaw=0, scale=1, tick_offset=0) -> dict
motion_events(history, eid, *, thresholds=None) -> list

detect_motion_groups(history, window=8, tol_pos=0.05,
                     min_samples=2, min_speed=1e-6) -> dict
get_motion_group(model, eid) -> dict | None
predict_group_members(group, observed, tick=None) -> dict
predict_with_groups(history, window=8, tol_pos=0.05,
                    min_samples=2, min_speed=1e-6, tick=None) -> dict
```

history 的形状是 `[{"tick": int, "entities": {eid: [x, y, z]}}]`，与
UnifiedWorldModel.perceive 记录的 4D 历史同形。模块只读该列表。

detour_return 的判据只看水平位置：actor 自身**绝对轨迹**的闭合回路等效半径、
actor 绕 ref 的方位角扫描、相对轨迹不穿越参照物（最近距离 > crossing_tol 0.05）。
三项证据缺一不可，绝对回路判据与穿越容差都不随参照物移动改变。

transfer_demonstration 的 yaw 与 scale 是**整体场景变换**，锚点是记录首帧的 ref
位置：参照物与执行者走同一条变换。参照物静止时位移为 0，只有相对偏移随 yaw 与
scale 变化。scale 缩放的是**相对偏移**，不是物体的渲染尺寸。

## 三、显式注册

core 不导入 world（依赖方向是 world → core）。调用 engine.world_process 前，该引擎
实例须先注册一次两个 world 模块；新建引擎与重开引擎都要各注册一次。

```python
from lingshu.core.core import SpacetimeMemoryEngine
from lingshu.world import register_world_process

engine = SpacetimeMemoryEngine(db_path="store.db")
register_world_process(engine)         # 只作用于这一个实例
engine.world_process("recognize", {})  # 未注册时这一步返回 error
```

边界：

- 注册只在调用时发生：import lingshu.world 不注册任何引擎，也不改引擎状态；
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

未列出的 action 返回 `{"status": "error", "error": "未知动作 …"}`，不做回退。
thresholds 传 ProcessThresholds 实例（lingshu/world/world_process.py），门面不转换。
组检测参数的缺省与全部判据在 world 模块，门面只转发。

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

## 五、演示与对照图

```bash
.venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py \
    --out-dir artifacts/world_process_demo
```

参数：--out-dir（缺省 artifacts/world_process_demo）、--width / --height
（对照图尺寸，缺省 720x480；GIF 按 2/3 缩小到 480x320）。

演示做三件事：

1. 经 core 的 world_model 门面把观测逐 tick 送进 UnifiedWorldModel，再从该模型的
   真实历史识别过程（本例识别出 approach_stay_leave）；
2. 提取相对轨迹并落库，关引擎后按 memory_id 取回同一条记录，再出三张对照图；
3. 三名成员成组、其中一名离开，出三张画面与遮蔽成员的推算位置。

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
- camera：panel（对照图）、gif、group（组画面）三台相机的六参数。

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
- 未验证：演示只跑通接近→停留→离开一例，其余过程类型的渲染效果未逐一验证；脑端
  MCP 往返需要 MDCG_BRAIN_PYTHONPATH 前置，缺前置跳过。

## 七、复跑命令

```bash
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_motion_groups.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_core.py -q -p no:cacheprovider
.venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py --out-dir artifacts/world_process_demo
.venv/Scripts/python.exe -X utf8 -B tools/import_direction_lint.py --json
```

上述脚本与演示都按第三节先注册再调用；import_direction_lint 的 core→world 计数只减
不增（本次删除 core 侧两行动态导入后计数不变，未抬基线）。

## 八、清单登记与核验

两件模块的指纹登记在 [导出清单](intake/body-export-v0.1/_export_manifest.json) 的
`blocks.world.files`，文件名为 `world_process.py` 与 `motion_groups.py`。

- 两条记录只写 `origin: "lingshu_native_development"`，**不写 `src`**：`src` 是 AEIS
  导出源字段，两件是本仓开发，不伪装上游来源；
- `tools/verify_manifest.py` 逐条核验 LF 归一后的 sha256。两份清单合计 **63 条**
  （v0.1 54 条、v0.2 9 条），本次实跑 63 条全部 MATCH；
- 顶层 `world_process_first_batch` 只留批次元数据与 `verified_records` 指向，不重复登记
  指纹，避免出现第二份不被核验的副本；
- [tests/test_manifest_integrity.py](../tests/test_manifest_integrity.py) 断言 18 至 20 钉住
  这三点，并保留坏指纹与缺失文件反例；
- 工具的附加字段 `origin_class` 把条目分为 origin-only / src-only / both / neither，
  只作上报，不改判定与退出码。

改动这两个模块后必须重算登记指纹，否则严格核验退出 3（MISMATCH）。
指纹口径是 LF 归一后的 sha256，清单记前 16 位。

```bash
.venv/Scripts/python.exe -X utf8 -B tools/verify_manifest.py --strict --quiet
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_manifest_integrity.py -q -p no:cacheprovider
```
