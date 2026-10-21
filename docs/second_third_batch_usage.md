# 第二、第三批功能使用说明

实施范围与决策见 [实施计划](second_third_batch_plan.md)，实测与限制见 [验收记录](second_third_batch_validation.md)。本说明对应当前工作树，最终验收结果以验收记录为准。

## F04：真实位姿记忆与多视角重建

观测侧通过既有 `world3d("add_view", params)` 输入 bbox 与 camera。相机六参数为 `cx/cy/cz/yaw/pitch/fov_deg`，角度 yaw/pitch 单位弧度，fov_deg 单位度；`Camera3D.to_meta()` 可生成完整载荷。每条观测绑定 eid、tick、screen_w/h 与 timestamp，写入既有 vprim 记忆，不增加数据库。请务必传设备/场景实际参数，示例中的相机是显式构造的已知测试相机。

```python
from lingshu.core.core import SpacetimeMemoryEngine
from lingshu.world.world3d import Camera3D

engine = SpacetimeMemoryEngine(":memory:")
truth = (0.6, 1.0, 6.0)
cameras = [Camera3D(), Camera3D.look_at((3.0, 1.2, -2.0), (0.0, 1.0, 6.0), fov_deg=75.0)]
for camera in cameras:
    px, py = camera.project(truth, 800, 600)
    result = engine.world3d("add_view", {
        "category": "person", "bbox": [px-20, py-30, px+20, py+30],
        "camera": camera.to_meta(), "eid": "person-1", "tick": 1,
        "screen_w": 800, "screen_h": 600, "timestamp": 1.0,
    })
    assert result["status"] == "ok", result
rebuilt = engine.world3d("build", {"multiview": True, "limit": 20})
assert rebuilt["status"] == "ok", rebuilt
print(rebuilt["detail"])
```

显式 camera 摄取必须包含 cx/cy/cz/yaw/pitch/fov_deg（最后一项可用 fov 别名），空对象/缺项/布尔或非有限字段明确拒绝且不落库、不融合。camera 缺省或 None 的低层即时兼容不形成持久化真实位姿；需要从记忆重建时不能使用该路径替代真实观测。

多视角 build 不再对缺位姿数据自动降级：全缺/空库为 `no_camera_pose`，混有缺元数据观测为 `camera_pose_incomplete`，畸形载荷为 `bad_camera_pose`；失败不替换已有世界。旧锚点数据可明确调用 `build(multiview=False)`。同类别多实例应显式提供稳定 eid；没有真实位姿的数据不应宣称为三角化输入。只有一条带完整位姿的观测时，融合器仅记录射线，`objects=0`，`views` 中 `triangulated=False`；成功读入位姿不等于已经得到可靠三维物体。

## F06：角色与显式关系参与查询

沿用配方 `connections` 的 `role` 与 `tags.relations` 声明。无关系配方保持旧评分/返回形状；带关系的结果给出角色绑定及逐条关系证据。

```python
from lingshu.nn.hex_query import ConnectionLibrary, query_match

lib = ConnectionLibrary()
lib.add_record("red_above_green", [
    {"type": "circle", "color": "red", "zone": "*", "role": "head"},
    {"type": "circle", "color": "green", "zone": "*", "role": "body"},
], tags={"relations": [{"from": "head", "to": "body", "rel": "above"}]})
print(query_match([
    {"obj": "circle|red", "pos": "r1"},
    {"obj": "circle|green", "pos": "r7"},
], lib))
```

关系评分是约束支持程度，不是通用图匹配器。采用规范检测序的最大一对一匹配；换序后 score/verdict/roles/relations 稳定，role_detections 仅作为原输入下标映射随换序改变。斜向关系按各自行列判据消费（可同时 above 与 left_of）；facts 是判定依据，fact 保留旧单一主关系投影。模糊角色采用稳定规范绑定，不求全图关系最优。未知、畸形、未绑定角色的关系必须明确不满足，而非推测为真。

## F07：有限生成反馈修正

默认有限修正，仍保持原文、编译 parts 意图及 seed 不变，仅修改渲染参数。每轮记录 diff_before/diff_after、changes 和停止原因；最终指标来自真实最终图像的像素读回，构造验证仍独立进行。

```python
from lingshu.nn.hex_gen import generate_from_text

text = "左上有红色小点纹圆形"
off = generate_from_text(text, size=96, seed=7, refine_rounds=0)
on = generate_from_text(text, size=96, seed=7, refine_rounds=2)
print(on["text"], on["refine"], on["constructive"], on["readback"])
```

`refine.coverage` 给出 covered/uncovered/judged 与原因：全覆盖外（例如橙色圆）、空部件或缺日志时 verifiable=False、before/after=None，不能把空集合当零差异；混合输入只报告已覆盖部件的差异。

能力边界：属性读回仍只在既有红/绿/蓝词表与面积可判范围内；尺寸项只覆盖 small/large 圆。48px 低于该属性标定口径，不修正、不假报成功。默认候选是确定性花纹采样参数，尚不能自动消除任意形状、遮挡、配色、布局或尺寸失配；无严格改善会停止，不通过降低阈值获得成功。

## F08：沙盒预测驱动行动

`configure_action(entity_id, target, step_size=1.0, policy="predictive", fixed_action="east")`；target 为实体 id 或三维坐标。离散动作 east/west/north/south/stay 在 x/z 平面移动：east=+x、north=-z，y 不变。

```python
from lingshu.world.seven_layer_loop import SevenLayerLoop

loop = SevenLayerLoop(size=24, seed=42, budget=2)
loop.create_scene(trees=0, water=False)
agent = loop.add_entity("agent", behavior="wander", pos=(2, 1.5, 2), speed=0.0)
target = loop.add_entity("target", behavior="wander", pos=(18, 1.5, 18), speed=0.0)
loop.configure_action(agent, target, step_size=1.0, policy="predictive")
for _ in range(40):
    result = loop.step()
    print(result["L7_action"])
loop.clear_action()
```

核心门面也提供 `seven_layer_loop("configure_action", {"entity_id": agent, "target": target, "step_size": 1.0, "policy": "predictive"})` 与 `clear_action`；请先 init/create/entity，再配置行动，后续 init 会重建循环并清除旧配置。行动 policy 与原观察 policy（curiosity/random/round_robin）是两个参数面，不相互覆盖。

决策只能使用观测与学得预测；真实位置用于执行器及外部裁判，不能回流为候选评分输入。为确保受控体与目标后续观测，必要观测优先；budget 不足时会明确报告扩展后的实际观测数。`fixed` 是固定行动对照，非通用强基线；沙盒成功不代表通用规划或真实设备控制。动作仅沿xz平面、y不变，不保证能到达不同高度的目标，也没有一般避障规划。目标陈旧观测结算为 `stale_target_observation`，不更新误差样本或预测修正；新鲜观测才反馈。坐标常量目标另外标记 `static_coordinate`，不混作目标观测验证。

## 本机复现

请在新开的测试PowerShell会话执行，结束后关闭该会话；临时HOME/USERPROFILE仅在该进程生效。确认系统 `python` 是已验证的3.13解释器，再赋给BRAIN_PYTHON；本机主体用已有3.12虚拟环境。

```powershell
# 脑包不随本PR提交。请先按CI钉定SHA获取，并显式设置其绝对目录。
if (-not $env:MDCG_BRAIN_PYTHONPATH) { throw "先设置 MDCG_BRAIN_PYTHONPATH（见 gate.yml 的固定脑端来源）" }
$env:MDCG_BRAIN_PYTHONPATH = (Resolve-Path $env:MDCG_BRAIN_PYTHONPATH).Path
# 本机主体 Python 3.12(.venv)，脑端使用已验证的系统 Python 3.13；按本机实际选择。
$env:BRAIN_PYTHON = (Get-Command python).Source
$testHome = Join-Path (Get-Location) "artifacts/second_third_batch/test_home"
New-Item -ItemType Directory -Force -Path $testHome | Out-Null
$env:HOME = $testHome; $env:USERPROFILE = $testHome
& "./.venv/Scripts/python.exe" -X utf8 -m pytest tests/test_second_batch_camera.py tests/test_second_batch_relations_feedback.py tests/test_third_batch_actions.py tests/test_third_batch_core_action.py tests/test_world_memory_first_batch.py -q --no-header
& "./.venv/Scripts/python.exe" -X utf8 tests/test_third_batch_actions.py --report
& "./.venv/Scripts/python.exe" -X utf8 -B tools/verify_manifest.py --strict --quiet
```

真实脑端来源与钉定SHA见[CI配置](../.github/workflows/gate.yml)；脑包克隆不随PR提交。无需脑端的基线/反面对照与小型数值复跑见[公开证据脚本](../bench/results/world_memory/20261013_second_third/README.md)。本机完整CI Python/依赖矩阵未承诺，远端CI和维护者验收以PR线程为准，详见验收记录。
