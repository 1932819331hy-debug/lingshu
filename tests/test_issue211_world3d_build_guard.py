# -*- coding: utf-8 -*-
"""#211 `world3d("build", multiview=True)` 编造相机位姿 + `objects` 计数口径 —— 守卫

缺陷（分诊表 `triage_lingshu_ALL.md` 行 211 判定「成立」；issue 原文
`.tmp/issues_p3.json` #211）：
  ① 记忆里的 vprim 只有 bbox，**没有拍摄时的相机位姿**。旧实现按索引凭空给第 i 帧
     造一台相机（`yaw = i*30°-30°`、`cx = ±1.5`，注释「模拟环绕观测」）再交给
     `add_view` 三角化 ⇒ 得到的 3D 位置与真实世界无关（issue 实测：几乎相同的三个
     bbox 被造成两棵树，一棵贴在相机光心上）。
  ② 两种模式返回的 `"objects": added` 都是**调用次数 / 三角化成功次数**：单视角下
     3 次同类同位置的 `add_vprim` 被合并成 1 个物体，接口仍报 3。

修法沿革（两批）：
  · 第一批（本文件原有断言面）：不再编造视角——vprim 节点未携带位姿 ⇒ 不做三角化
    （当时仓内无位姿载体格式，故多视角**恒**退回单视角）；
    `objects` 改报**世界物体数**，本趟观测数挪到新增字段 `observations`。
  · 第二批 F04（本文件随之更新——是有意的接口变更，见
    `docs/second_third_batch_plan.md` F04「缺元数据明确失败，不得自动退回单视角或
    伪造位姿；显式 multiview=False 兼容旧观测」）：`multiview=True` 而位姿缺失 ⇒
    **status="error" + reason="no_camera_pose"**（不再报成一次「成功的单视角重建」），
    失败**不写** `self._world3d`（旧世界不被半成品覆盖）；带真实位姿的落库/读回通路与
    六参数保真见新增 `tests/test_second_batch_camera.py`（C1~C10）。
  · R6 返工（A 包第一次返工）：显式 `camera` 的**摄取**入口必须六参数齐备——空/缺项
    一律 status=error + bad_camera_pose，不落库、不融合、不污染旧世界（旧实现用
    `Camera3D.from_meta(strict=False)` 补默认值后当真实位姿落库 ⇒ `camera={}` 返回 ok）。

断言组（把修复改回缺陷形态 ⇒ 对应组红）：
  Q1 不编造视角、不静默回退：multiview=True（无位姿）⇒ status=error、
     reason=no_camera_pose，且**不**带 mode="single_view"/objects；世界仍为空
     （旧形态会造出与单视角不同的假位置物体）。
  Q2 objects = 世界物体数：3 个近似同位置 vprim ⇒ objects=1 且 objects ==
     detail["count"]（旧：objects=3）。
  Q3 observations 承载旧口径：observations == 3（本趟处理的观测数）。
  Q4 显式 multiview=False 兼容旧观测：不给 multiview 与显式 false 得到同一 scene；
     requested_mode 如实回报；multiview=True 仍如实回报 requested_mode 并失败。
  Q5 显式位姿路径仍在：`add_view` 给**完整六参数已知相机**（`Camera3D.to_meta()`）两次
     ⇒ 第二次 triangulated=True 且重建位置贴真值（误差 < 0.05 m，阈值未放宽）；
     空/缺项 camera（含只给 yaw/pitch/cx/cy 的旧调用形）⇒ status=error +
     reason=bad_camera_pose，不落库、不进内存世界（R6 返工：不得补默认值当真实位姿）。
  Q6 位姿不被截断：`add_view` 给**六参数**位姿（含 cz≠0 与离轴 fov=75）⇒ 融合位置
     贴近真值（旧门面只留 yaw/pitch/cx/cy，实测偏 0.25~0.4 m ⇒ 本组红）。

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


def test_q1_no_fabricated_camera_pose_no_silent_fallback():
    """Q1：无位姿 ⇒ 明确失败（旧形态：凭空造 yaw/cx 得到假位置；第一批形态：静默退回单视角）。"""
    e = _engine_with_tree()
    r = e.world3d("build", {"multiview": True})
    assert r["status"] == "error", (
        "#211①：multiview=True 无位姿却报成功——mode=%r（不得编造视角，也不得静默回退）"
        % r.get("mode"))
    assert r["reason"] == "no_camera_pose", (
        "#211①：未如实回报 reason——reason=%r" % r.get("reason"))
    assert r.get("mode") != "single_view", (
        "#211①：缺位姿被报成一次成功的单视角重建（不自动退回单视角）：%r" % (r.get("mode"),))
    assert "objects" not in r and "scene" not in r, (
        "#211①：失败返回带上了成功字段，调用方会当成重建结果：%r" % (r,))
    assert e.world3d("status")["objects"] == [], (
        "#211①：无位姿的多视角 build 造出了物体（编造视角的旧形态）")
    # 旧观测仍可显式走单视角
    r2 = e.world3d("build", {"multiview": False})
    assert r2["status"] == "ok" and r2["mode"] == "single_view"
    assert r2["objects"] == 1


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


def test_q4_explicit_single_view_compatible():
    """Q4：显式 multiview=False（及默认入参）与旧单视角行为一致；多视角如实回报。"""
    e = _engine_with_tree()
    r_default = e.world3d("build", {})
    r_sv = e.world3d("build", {"multiview": False})
    assert r_default["scene"] == r_sv["scene"], (
        "#211：默认入参与 multiview=False 的 scene 不一致——%r vs %r"
        % (r_default["scene"], r_sv["scene"]))
    assert r_sv["status"] == "ok" and r_sv["mode"] == "single_view"
    assert r_sv["requested_mode"] == "single_view"
    r_mv = e.world3d("build", {"multiview": True})
    assert r_mv["requested_mode"] == "multiview" and r_mv["status"] == "error", r_mv


def test_q5_explicit_camera_multiview_still_works():
    """Q5：显式位姿路径仍在——给**完整六参数**已知相机两次 ⇒ 第二次三角化且位置贴真值；
    空/缺项 camera（旧调用形）在摄取入口即被明确拒绝（R6 返工：不再补默认值当真实位姿）。"""
    e = SpacetimeMemoryEngine(":memory:")
    truth = (0.0, 1.2, 5.0)
    cams = [Camera3D(), Camera3D(cx=3.0, cz=-2.0, yaw=0.5)]
    results = []
    for cam in cams:
        px, py = cam.project(truth, SW, SH)
        results.append(e.world3d("add_view", {
            "category": "person",
            "bbox": [px - 20, py - 30, px + 20, py + 30],
            "camera": cam.to_meta(),          # 六参数全量（R6 返工后的摄取口径）
            "screen_w": SW, "screen_h": SH, "eid": "person-1", "tick": 1,
        }))
    assert results[0]["status"] == "ok" and results[0]["triangulated"] is False, \
        "首帧不应三角化（仅记录）：%r" % (results[0],)
    assert results[1]["status"] == "ok" and results[1]["triangulated"] is True, (
        "#211：显式位姿路径被本件误伤——第二视角未三角化：%r" % (results[1],))
    # 真实误差验证（阈值与 Q6/C2 同口径，未放宽）：两视角重建须贴近已知真值
    objects = e.world3d("status")["objects"]
    assert len(objects) == 1, objects
    err = max(abs(a - b) for a, b in zip(objects[0]["center"], truth))
    assert err < 0.05, (
        "#211：完整六参数位姿下的两视角重建偏了 %.4f m（%r vs 真值 %r）"
        % (err, objects[0]["center"], truth))
    # 空/缺项 camera（旧调用形）⇒ 明确拒绝，且不动已建好的世界与记忆
    nodes_before = [n.id for n in e.store.get_nodes_by_tag("vprim")]
    for bad in ({}, {"yaw": cams[1].yaw, "pitch": cams[1].pitch,
                 "cx": cams[1].cx, "cy": cams[1].cy}):
        r_bad = e.world3d("add_view", {
            "category": "person",
            "bbox": [300, 250, 340, 310],
            "camera": bad, "screen_w": SW, "screen_h": SH,
            "eid": "person-2", "tick": 2})
        assert r_bad["status"] == "error", (bad, r_bad)
        assert r_bad["reason"] == "bad_camera_pose", (bad, r_bad)
        assert not r_bad.get("persisted"), (bad, r_bad)
    assert [n.id for n in e.store.get_nodes_by_tag("vprim")] == nodes_before, \
        "#211/F04：缺项 camera 被拒后仍落了库（伪造位姿）"
    assert len(e.world3d("status")["objects"]) == 1, "被拒的观测进了内存世界"


def test_q6_camera_pose_not_truncated_in_fusion():
    """Q6：六参数位姿（cz≠0、离轴 fov）必须全量进融合——截断即偏（旧门面偏 0.25~0.4 m）。"""
    e = SpacetimeMemoryEngine(":memory:")
    truth = (0.6, 1.0, 6.0)
    cams = [Camera3D.look_at((-2.0, 1.4, -1.0), truth),
            Camera3D.look_at((2.5, 1.6, 0.5), (1.6, 1.0, 6.0), fov_deg=75.0)]
    for cam in cams:
        px, py = cam.project(truth, SW, SH)
        r = e.world3d("add_view", {
            "category": "person", "bbox": [px - 20, py - 30, px + 20, py + 30],
            "camera": cam.to_meta(), "screen_w": SW, "screen_h": SH,
            "eid": "person-1", "tick": 1})
        assert r["status"] == "ok", r
    objects = e.world3d("status")["objects"]
    assert len(objects) == 1, objects
    err = max(abs(a - b) for a, b in zip(objects[0]["center"], truth))
    assert err < 0.05, (
        "#211/F04：融合位置偏了 %.4f m（%r vs 真值 %r）——相机位姿被截断（旧形态丢 "
        "cz/fov_deg）" % (err, objects[0]["center"], truth))
