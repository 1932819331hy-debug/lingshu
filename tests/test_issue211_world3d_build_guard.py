# -*- coding: utf-8 -*-
"""#211 `world3d("build", multiview=True)` 编造相机位姿 + `objects` 计数口径 —— 守卫

缺陷（分诊表 `triage_lingshu_ALL.md` 行 211 判定「成立」；issue 原文 `.tmp/issues_p3.json` #211）：
  ① 记忆里的 vprim 只有 bbox，**没有拍摄时的相机位姿**。旧实现按索引凭空给第 i 帧
     造一台相机（`yaw = i*30°-30°`、`cx = ±1.5`，注释「模拟环绕观测」）再交给
     `add_view` 三角化 ⇒ 得到的 3D 位置与真实世界无关（issue 实测：几乎相同的三个
     bbox 被造成两棵树，一棵贴在相机光心上）。
  ② 两种模式返回的 `"objects": added` 都是**调用次数 / 三角化成功次数**：单视角下
     3 次同类同位置的 `add_vprim` 被合并成 1 个物体，接口仍报 3。

修法（最小 · 两条）：
  ① 不再编造视角——vprim 节点未携带位姿 ⇒ 退回单视角，返回
     `mode="single_view"`、`reason="no_camera_pose"`；带真实位姿的多视角仍可经
     `world3d("add_view", {"camera": {...}})` 显式驱动（不受本件影响）。
  ② `objects` 改报**世界中的物体数** `len(world.objects)`；本趟处理的观测数挪到
     新增字段 `observations`。

判据来源：issue #211「期望行为：没有真实相机位姿时不做三角化（回退单视角，或明确
返回 reason="no_camera_pose"），不编造视角。objects 等于世界中的物体数
len(world.objects)，或拆成 observations / objects 两个字段」＋ 分诊表行 211
（经验标定）＋ 理论 `docs/theory/世界模型与语义时空图_完整理论整理与实现路线.md`
§3.2「三视图/双目视差——**已知相机参数**下的映射，非猜测」。
边界（削掉的判别力，如实标注）：本件**不**新增位姿载体格式（把 yaw/pitch/cx/cy/cz
塞进 tags / condition_space 属新机制，须先裁定格式）——因此 `multiview=True` 在
当前仓内**恒**退回单视角；多视角能力仍经显式 `add_view` 保留（Q5 钉住）。
`objects` 字段语义变更（调用次数 → 世界物体数）是本件要求的破坏性口径修正，
仓内无其它调用方（全仓 grep：`world3d(` 仅出现在 tests 与本模块）。

断言组（把修复改回缺陷形态 ⇒ Q1/Q2/Q3 红）：
  Q1 不编造视角：multiview=True 时 mode=single_view、reason=no_camera_pose，
     且世界物体与单视角 build 的**同一 3D 位置**（旧：造出不同位置的假物体）。
  Q2 objects = 世界物体数：3 个近似同位置 vprim ⇒ objects=1 且
     objects == detail["count"]（旧：objects=3）。
  Q3 observations 承载旧口径：observations == 3（处理的观测数）。
  Q4 非回归：multiview 与单视角两种入参得到相同的 scene/物体集合。
  Q5 显式位姿路径仍在：`add_view` 带真实相机两次 ⇒ 第二次 triangulated=True
     （本件未砍多视角能力）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue211_world3d_build_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402
from lingshu.world.world3d import Camera3D  # noqa: E402

SW, SH = 800, 600
#: 几乎相同的三个 bbox（同一棵树）——issue #211 的最小复现输入。
_TREE_FRAMES = ["tree@(380,250,420,350)", "tree@(381,250,421,350)", "tree@(382,250,422,350)"]


def _engine_with_tree():
    e = SpacetimeMemoryEngine(":memory:")
    for i, t in enumerate(_TREE_FRAMES):
        e.add_perception(content="帧%d 视觉锚点 %s" % (i, t), tags=["vprim"])
    return e


def test_q1_no_fabricated_camera_pose():
    """Q1：无位姿 ⇒ 不三角化、不编造视角（旧：凭空造 yaw/cx 得到假位置）。"""
    e = _engine_with_tree()
    r = e.world3d("build", {"multiview": True})
    assert r["mode"] == "single_view", (
        "#211①：multiview=True 仍走了三角化路径——mode=%r（无位姿时不得编造视角）"
        % r["mode"])
    assert r["reason"] == "no_camera_pose", (
        "#211①：未如实回报 reason——reason=%r" % r.get("reason"))
    r2 = e.world3d("build", {"multiview": False})
    centers_mv = sorted(o["center"] for o in r["detail"]["objects"])
    centers_sv = sorted(o["center"] for o in r2["detail"]["objects"])
    assert centers_mv == centers_sv, (
        "#211①：multiview 造出与单视角不同的 3D 位置——%r vs %r"
        % (centers_mv, centers_sv))


def test_q2_objects_is_world_object_count():
    """Q2：objects 报世界物体数（旧：报调用次数 3，实际只有 1 个物体）。"""
    e = _engine_with_tree()
    r = e.world3d("build", {"multiview": False})
    assert r["objects"] == 1, (
        "#211②：objects=%r 是调用次数而非世界物体数（3 个近似同位置 vprim 合并成 1 个）"
        % r["objects"])
    assert r["objects"] == r["detail"]["count"], (
        "#211②：objects=%r 与 detail['count']=%r 不一致"
        % (r["objects"], r["detail"]["count"]))


def test_q3_observations_keeps_old_reading():
    """Q3：旧口径（处理/三角化次数）挪到 observations，仍可读且为 3。"""
    e = _engine_with_tree()
    r = e.world3d("build", {"multiview": False})
    assert r.get("observations") == 3, (
        "#211②：observations=%r（应为本趟处理的 3 条 vprim 观测）" % r.get("observations"))


def test_q4_both_modes_agree_on_scene():
    """Q4：非回归——两种入参得到相同 scene（多视角不再产出额外假物体）。"""
    e = _engine_with_tree()
    r_mv = e.world3d("build", {"multiview": True})
    r_sv = e.world3d("build", {"multiview": False})
    assert r_mv["scene"] == r_sv["scene"], (
        "#211：multiview 与单视角 scene 不一致——%r vs %r"
        % (r_mv["scene"], r_sv["scene"]))
    assert r_mv["requested_mode"] == "multiview" and r_mv["mode"] == "single_view"


def test_q5_explicit_camera_multiview_still_works():
    """Q5：本件未砍多视角能力——`add_view` 带真实相机两次 ⇒ 第二次三角化成物。"""
    e = SpacetimeMemoryEngine(":memory:")
    truth = (0.0, 1.2, 5.0)
    cams = [Camera3D(), Camera3D(cx=3.0, cz=-2.0, yaw=0.5)]
    results = []
    for cam in cams:
        px, py = cam.project(truth, SW, SH)
        results.append(e.world3d("add_view", {
            "category": "person",
            "bbox": [px - 20, py - 30, px + 20, py + 30],
            "camera": {"yaw": cam.yaw, "pitch": cam.pitch, "cx": cam.cx, "cy": cam.cy},
        }))
    assert results[0]["triangulated"] is False, "首帧不应三角化（仅记录）"
    assert results[1]["triangulated"] is True, (
        "#211：显式位姿路径被本件误伤——第二视角未三角化：%r" % results[1])
