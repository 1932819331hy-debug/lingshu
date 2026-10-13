# -*- coding: utf-8 -*-
"""F04 真实相机元数据通路：观测绑定位姿进记忆 → build(multiview=True) 读回重建 —— 守卫

目标（第二批计划 `docs/second_third_batch_plan.md` F04）：观测侧把**真实 camera
元数据**连同 eid/tick/screen_w/h 绑定保存进**既有脑存储**（既有 vprim 节点 + 既有
`add_perception`/`condition_space` 摄取接口，不新建库/表），由
`core.world3d("build", multiview=True)` 重新读回并喂给**既有融合器**
`MultiViewFusion`（经 `World3D.add_view`，不另写三角化）；缺/畸形元数据
fail-fast（status=error + 具体 reason），**不**自动退回单视角、**不**伪造位姿；
显式 `multiview=False` 保持旧单视角观测兼容。

判据来源：第二批计划 F04 与 F08 节 ＋ 父任务书「元数据缺失时返回 status=error 并给出
具体原因（如 no_camera_pose），不得自动退回单视角、不得伪造位姿」＋ 公开 issue #211
（不编造视角）＋ 理论 `docs/theory/世界模型与语义时空图_完整理论整理与实现路线.md`
§3.2（已知相机参数下的映射，非猜测）。

断言组（把通路改回缺陷形态 ⇒ 对应组变红）：
  C1 跨引擎持久化：带位姿观测落库；**新引擎**读回后 `build(multiview=True)` 得到
     同一 3D 位置（缺陷形态：门面不落库 ⇒ 新引擎只能报 no_camera_pose）。
  C2 已知两相机 + 位置误差可复算：真值 (0.6,1.0,6.0)、两台已知位姿相机（含 cz≠0
     与离轴 fov=75）⇒ 重建误差 < 0.05 m；并同场算出「丢 cz / 丢 fov」的重建误差
     （0.25 / 0.37 m）证明该阈值确有判别力。
  C3 六参数全量：块内位姿与入参逐字段相等（cz/fov_deg 不得被截掉），build 的
     `cameras` 回显亦然；`fov` 别名可用；观测条件进既有 condition_space。
  C4 同类两实例分别重建：同类 chair 两个 eid 各 2 视角 ⇒ 2 个物体，各自贴近自己的
     真值、互不混合。
  C5 缺位姿 fail-fast 且不污染旧 _world3d：全缺 ⇒ no_camera_pose；部分缺 ⇒
     camera_pose_incomplete；两次失败后 `status` 仍报旧世界（同一批物体/位置）。
  C6 畸形元数据 fail-fast：坏 JSON / 未闭合 / 空块 / 版本不认识 / 缺字段 / 位姿缺
     cz / NaN / screen_w=0 / bbox 非四元 / 一节点两锚点 / 位姿非对象 ⇒
     bad_camera_pose，且 detail 报出问题节点 id；同样不污染旧世界。
  C7 摄取侧 fail-fast：`add_view` **与 `add`** 的显式 camera 必须**六参数齐备**
     （cx/cy/cz/yaw/pitch/fov_deg，`fov` 为别名）——空/缺项/非法 ⇒ status=error +
     bad_camera_pose（消息点名缺哪个字段），且**不落库、不融合、不动旧世界**；
     无 camera 的旧单视角内存行为逐位保持（R6 返工：旧实现用
     `from_meta(strict=False)` 的默认值补齐，`camera={}` 也返回 ok 且 persisted）。
  C8 单视角兼容：同一存储上显式 `multiview=False` 仍按旧锚点路径建物。
  C9 `world3d("add")` 也是观测入口：给 camera ⇒ 同一条落库通路（反投影按该位姿、
     世界相机不被永久改写）；不给 ⇒ 与第一批逐位一致（只进内存、不落库）；
     给**空/缺项** camera ⇒ 与 add_view 同判据的明确拒绝（不落库、不进世界）。
  C10 只有一条带位姿观测 ⇒ 融合器「首次观测仅记录」：objects=0、observations=1
     （不报错、不伪造位置）。

边界（如实标注，本件**未**覆盖）：不做位姿标定/外参求解；不做时域平滑；一个记忆节点
只绑定一条观测（多锚点 + 一个位姿块会被拒，不猜归属）；旧无可位姿观测不做自动补位姿。

运行（仓根）：python -X utf8 -m pytest tests/test_second_batch_camera.py -q --no-header
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402
from lingshu.world.vprim import parse_anchor  # noqa: E402
from lingshu.world.world3d import (  # noqa: E402
    CAMERA_BLOCK_TAG, CAMERA_META_VERSION, Camera3D, World3D,
    parse_camera_observation)

SW, SH = 800, 600
#: 已知真值（米）——C2 的「位置误差可复算」基线。
TRUTH = (0.6, 1.0, 6.0)
EYE_A = (-2.0, 1.4, -1.0)
EYE_B = (2.5, 1.6, 0.5)
#: 相机 B **不正对真值**（视线偏轴）——这样 fov 才真正参与射线方向，
#: 丢掉 fov_deg 才会把目标准星带偏（正对时两条射线都过真值，fov 不可辨）。
GAZE_B = (1.6, 1.0, 6.0)
#: 旧观测（无位姿块）的三个近似同位置锚点——#211 的最小复现输入。
LEGACY_ANCHORS = ["tree@(380,250,420,350) 40x100 conf=0.90",
                  "tree@(381,250,421,350) 40x100 conf=0.90",
                  "tree@(382,250,422,350) 40x100 conf=0.90"]


def _cam_a() -> Camera3D:
    return Camera3D.look_at(EYE_A, TRUTH)


def _cam_b() -> Camera3D:
    return Camera3D.look_at(EYE_B, GAZE_B, fov_deg=75.0)


def _bbox(cam: Camera3D, point, half_w: float = 20.0, half_h: float = 30.0):
    """在 cam 里把 point 投成屏幕 bbox（bbox 中心 = 该点的投影，射线过真值）。"""
    px, py = cam.project(point, SW, SH)
    return [px - half_w, py - half_h, px + half_w, py + half_h]


def _ingest(engine, cam, point, *, category="tree", eid="tree-1", tick=4,
            ts=100.5, confidence=0.9):
    """经公开门面记录一条**带真实位姿**的观测。"""
    r = engine.world3d("add_view", {
        "category": category, "bbox": _bbox(cam, point), "camera": cam.to_meta(),
        "eid": eid, "tick": tick, "screen_w": SW, "screen_h": SH,
        "confidence": confidence, "timestamp": ts})
    assert r["status"] == "ok", r
    return r


def _center_error(center, point) -> float:
    return max(abs(a - b) for a, b in zip(center, point))


def _status_centers(engine):
    return sorted(tuple(o["center"]) for o in engine.world3d("status")["objects"])


def _block(body: str, anchor: str = "chair@(100,100,140,160) 40x60 conf=0.50") -> str:
    return anchor + "\n" + "```" + CAMERA_BLOCK_TAG + "\n" + body + "\n```"


def _ok_meta() -> dict:
    return {"v": CAMERA_META_VERSION, "category": "chair",
            "bbox": [100.0, 100.0, 140.0, 160.0], "eid": "chair-1", "tick": 2,
            "screen_w": SW, "screen_h": SH, "confidence": 0.5,
            "camera": _cam_a().to_meta()}


# ===========================================================================
# C1 跨引擎持久化 → 重新 build 的多视角重建
# ===========================================================================

def test_c1_camera_meta_persists_and_rebuilds_in_a_new_engine(tmp_path):
    """观测 → 既有脑存储 → 新引擎 build(multiview=True)：公开链完整。"""
    db = tmp_path / "camera.db"
    a = SpacetimeMemoryEngine(db_path=str(db))
    try:
        first = _ingest(a, _cam_a(), TRUTH)
        second = _ingest(a, _cam_b(), TRUTH)
        # 单视角只记录、第二视角才三角化（融合器既有语义）
        assert first["triangulated"] is False and second["triangulated"] is True
        # 观测已绑定落库：返回落库节点 id（缺陷形态：无 persisted）
        assert first["persisted"] and second["persisted"]
        assert first["persisted"] != second["persisted"]
        nodes = a.store.get_nodes_by_tag("vprim")
        assert len(nodes) == 2, "两条带位姿观测必须各留一个节点（不得被去重并成一条）"
        metas = [parse_camera_observation(n.content) for n in nodes]
        assert all(m is not None for m in metas), "落库正文里必须带相机元数据块"
        assert [m["eid"] for m in metas] == ["tree-1", "tree-1"]
        assert [m["tick"] for m in metas] == [4, 4]
        assert [m["screen_w"] for m in metas] == [SW, SW]
        assert [m["screen_h"] for m in metas] == [SH, SH]
        assert first["persisted"] in {n.id for n in nodes}
        node_ids = {n.id for n in nodes}
    finally:
        a.store.conn.close()

    b = SpacetimeMemoryEngine(db_path=str(db))
    try:
        assert not hasattr(b, "_world3d"), "新引擎不该预置内存世界（重建只能来自记忆）"
        r = b.world3d("build", {"multiview": True})
        assert r["status"] == "ok", r
        assert r["mode"] == "multiview" and r["requested_mode"] == "multiview"
        assert r["reason"] is None
        assert r["objects"] == 1 and r["observations"] == 2
        center = r["detail"]["objects"][0]["center"]
        assert _center_error(center, TRUTH) < 0.05, (
            "跨引擎重建偏了：%r vs 真值 %r" % (center, TRUTH))
        # 读回的正是记忆里那两条观测
        assert {v["node"] for v in r["views"]} == node_ids
        assert [v["triangulated"] for v in r["views"]] == [False, True]
        # 重建的世界确实是新引擎当前世界（不是空壳）
        assert b.world3d("status")["count"] == 1
    finally:
        b.store.conn.close()


# ===========================================================================
# C2 已知两相机 + 位置误差（含阈值判别力自证）
# ===========================================================================

def test_c2_known_two_cameras_error_is_recomputable(tmp_path):
    """两台已知位姿相机 ⇒ 重建位置误差 < 0.05 m（且阈值能区分错位姿）。"""
    a, b_cam = _cam_a(), _cam_b()
    e = SpacetimeMemoryEngine(db_path=str(tmp_path / "two.db"))
    try:
        _ingest(e, a, TRUTH)
        _ingest(e, b_cam, TRUTH)
        r = e.world3d("build", {"multiview": True})
        center = r["detail"]["objects"][0]["center"]
        err = _center_error(center, TRUTH)
        assert err < 0.05, (
            "已知两相机下的重建误差 %.4f m 超阈（真值 %r，得到 %r）"
            % (err, TRUTH, center))

        # 判别力自证：同一对 bbox 若按「丢 cz」「丢 fov」的位姿融合（旧门面形态），
        # 结果会明显偏 —— 故 0.05 不是空阈值。
        def _fuse(cam_b_used):
            w = World3D()
            for cam_proj, cam_use in ((a, a), (b_cam, cam_b_used)):
                w.add_view("tree", _bbox(cam_proj, TRUTH), SW, SH,
                           camera=cam_use, eid="t", tick=1)
            return _center_error(w.objects[0].center, TRUTH)

        cz_lost = Camera3D(fov_deg=b_cam.fov_deg, cx=b_cam.cx, cy=b_cam.cy, cz=0.0,
                           yaw=b_cam.yaw, pitch=b_cam.pitch)
        fov_lost = Camera3D(fov_deg=60.0, cx=b_cam.cx, cy=b_cam.cy, cz=b_cam.cz,
                            yaw=b_cam.yaw, pitch=b_cam.pitch)
        assert _fuse(cz_lost) > 0.2 and _fuse(fov_lost) > 0.2, (
            "阈值判别力自证失败：丢 cz/丢 fov 的误差没有明显超出 0.05 "
            "（cz=%.4f fov=%.4f）" % (_fuse(cz_lost), _fuse(fov_lost)))
    finally:
        e.store.conn.close()


# ===========================================================================
# C3 六参数全量 + 条件空间
# ===========================================================================

def test_c3_six_camera_params_survive_round_trip(tmp_path):
    """cx/cy/cz/yaw/pitch/fov_deg 逐字段往返；`fov` 别名可用；条件进 condition_space。"""
    e = SpacetimeMemoryEngine(db_path=str(tmp_path / "six.db"))
    cam = _cam_b()
    try:
        _ingest(e, cam, TRUTH)
        node = e.store.get_nodes_by_tag("vprim")[0]
        meta = parse_camera_observation(node.content)
        assert meta["camera"] == cam.to_meta(), "落库位姿与入参不一致（被截断/改写）"
        for key in ("cx", "cy", "cz", "yaw", "pitch", "fov_deg"):
            assert key in meta["camera"], "六参数缺 %r" % key
        assert meta["camera"]["cz"] == pytest.approx(EYE_B[2])
        assert meta["camera"]["fov_deg"] == pytest.approx(75.0)
        assert meta["bbox"] == pytest.approx(_bbox(cam, TRUTH)), (
            "块内 bbox 必须是未截断的浮点真值（正文锚点行的整数文本只给单视角用）")

        # 观测条件走既有 condition_space（同一份观测的「在什么条件下观测」声明）
        cs = node.condition_space
        assert cs.observation_position.startswith("camera@(")
        assert "lingshu-camera" in cs.existence_constraint
        assert tuple(cs.time_window) == (100.5, 100.5)

        r = e.world3d("build", {"multiview": True})
        assert r["cameras"] == [cam.to_meta()], (
            "build 回显的位姿与落库位姿不一致：%r" % (r["cameras"],))

        # fov 是 fov_deg 的别名（调用方常用写法）
        alias = {"cx": 1.0, "cy": 1.2, "cz": -2.0, "yaw": 0.25, "pitch": -0.1, "fov": 70.0}
        r2 = e.world3d("add_view", {"category": "tree", "bbox": [10, 10, 50, 60],
                                    "camera": alias, "eid": "tree-2", "tick": 5,
                                    "screen_w": SW, "screen_h": SH})
        assert r2["status"] == "ok", r2
        metas = [parse_camera_observation(n.content)
                 for n in e.store.get_nodes_by_tag("vprim")]
        alias_meta = [m for m in metas if m and m["eid"] == "tree-2"][0]
        assert alias_meta["camera"]["fov_deg"] == pytest.approx(70.0)
        assert alias_meta["camera"]["cz"] == pytest.approx(-2.0)
    finally:
        e.store.conn.close()


# ===========================================================================
# C4 同类两个实例分别重建
# ===========================================================================

def test_c4_two_same_category_instances_rebuild_separately(tmp_path):
    """同类 chair 两个 eid：各自 2 视角 ⇒ 2 个物体，互不混合、各自贴真值。"""
    truths = {"chair-a": TRUTH, "chair-b": (-1.5, 0.8, 3.0)}
    db = tmp_path / "instances.db"
    e = SpacetimeMemoryEngine(db_path=str(db))
    try:
        for eid, point in truths.items():
            for cam in (_cam_a(), _cam_b()):
                _ingest(e, cam, point, category="chair", eid=eid, tick=9)
    finally:
        e.store.conn.close()

    b = SpacetimeMemoryEngine(db_path=str(db))
    try:
        r = b.world3d("build", {"multiview": True})
        assert r["status"] == "ok", r
        assert r["objects"] == 2, (
            "同类两实例必须各自成物（缺陷形态：并成一桶/丢一个）：%r"
            % [(o["eid"], o["center"]) for o in r["detail"]["objects"]])
        objs = {o["eid"]: o for o in r["detail"]["objects"]}
        assert set(objs) == set(truths), "实例身份在重建里丢了：%r" % (list(objs),)
        for eid, point in truths.items():
            err = _center_error(objs[eid]["center"], point)
            assert err < 0.05, "%s 重建偏了 %.4f m（%r vs %r）" % (
                eid, err, objs[eid]["center"], point)
        # 不混合：a 的物体离 b 的真值很远（反之亦然）
        assert math.dist(objs["chair-a"]["center"], truths["chair-b"]) > 1.0
        assert math.dist(objs["chair-b"]["center"], truths["chair-a"]) > 1.0
    finally:
        b.store.conn.close()


# ===========================================================================
# C5 缺位姿 fail-fast 且不污染旧 _world3d
# ===========================================================================

def test_c5_missing_camera_pose_fails_fast_without_polluting_world(tmp_path):
    """全缺 ⇒ no_camera_pose；部分缺 ⇒ camera_pose_incomplete；两次都不动旧世界。"""
    e = SpacetimeMemoryEngine(db_path=str(tmp_path / "missing.db"))
    try:
        for text in LEGACY_ANCHORS:
            e.add_perception(content=text, tags=["vprim"])
        base = e.world3d("build", {"multiview": False})
        assert base["status"] == "ok" and base["mode"] == "single_view"
        assert base["objects"] == 1 and base["observations"] == 3
        baseline = _status_centers(e)

        r = e.world3d("build", {"multiview": True})
        assert r["status"] == "error", r
        assert r["reason"] == "no_camera_pose", r
        assert r.get("mode") != "single_view", (
            "缺位姿不得被报成一次成功的单视角重建：%r" % (r,))
        assert "objects" not in r and "scene" not in r, (
            "失败返回不得携带成功字段（会被调用方当成重建结果）：%r" % (r,))
        assert r["requested_mode"] == "multiview"
        assert _status_centers(e) == baseline, "失败的多视角 build 污染了旧 _world3d"

        # 部分缺位姿：只重建带位姿的子集＝静默丢掉另一半世界 ⇒ 整趟拒绝
        _ingest(e, _cam_a(), TRUTH, eid="tree-1", tick=7)
        assert _status_centers(e) == baseline, "单视角观测不该改动世界（此时尚未三角化）"
        r2 = e.world3d("build", {"multiview": True})
        assert r2["status"] == "error" and r2["reason"] == "camera_pose_incomplete", r2
        assert r2["detail"]["posed"] == 1 and r2["detail"]["unposed"], r2
        assert _status_centers(e) == baseline, "部分缺位姿的失败同样不得动旧世界"
    finally:
        e.store.conn.close()


def test_c5b_empty_store_reports_no_camera_pose():
    """库里一条 vprim 都没有 ⇒ 同样是 no_camera_pose（不是空世界成功）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        r = e.world3d("build", {"multiview": True})
        assert r["status"] == "error" and r["reason"] == "no_camera_pose", r
        assert r["detail"]["nodes"] == 0
    finally:
        e.store.conn.close()


# ===========================================================================
# C6 畸形元数据 fail-fast（逐类给具体原因 + 不污染旧世界）
# ===========================================================================

def _bad_cases():
    ok = _ok_meta()
    cam = ok["camera"]
    no_cam = {k: v for k, v in ok.items() if k != "camera"}
    no_tick = {k: v for k, v in ok.items() if k != "tick"}
    cam_no_cz = {k: v for k, v in cam.items() if k != "cz"}
    cam_nan = dict(cam, fov_deg=float("nan"))
    return [
        ("坏 JSON", _block("{不是 JSON")),
        ("块未闭合", "chair@(100,100,140,160) 40x60 conf=0.50\n```"
                     + CAMERA_BLOCK_TAG + "\n" + json.dumps(ok)),
        ("空块", _block("")),
        ("版本不认识", _block(json.dumps(dict(ok, v="lingshu.camera.v0")))),
        ("缺 camera 字段", _block(json.dumps(no_cam))),
        ("缺 tick 字段", _block(json.dumps(no_tick))),
        ("位姿缺 cz", _block(json.dumps(dict(ok, camera=cam_no_cz)))),
        ("位姿 NaN", _block(json.dumps(dict(ok, camera=cam_nan)))),
        ("位姿非对象", _block(json.dumps(dict(ok, camera="cam-1")))),
        ("screen_w=0", _block(json.dumps(dict(ok, screen_w=0)))),
        ("bbox 非四元", _block(json.dumps(dict(ok, bbox=[1, 2, 3])))),
        ("eid 非字符串", _block(json.dumps(dict(ok, eid=7)))),
        ("一节点两锚点", _block(json.dumps(ok),
                            anchor="chair@(1,2,3,4)；chair@(5,6,7,8)")),
    ]


def test_c6_malformed_camera_meta_fails_fast_without_polluting(tmp_path):
    """13 类畸形 ⇒ bad_camera_pose + 点名节点；每类都不得动旧世界。"""
    for i, (name, text) in enumerate(_bad_cases()):
        e = SpacetimeMemoryEngine(db_path=str(tmp_path / ("bad-%02d.db" % i)))
        try:
            e.add_perception(content=LEGACY_ANCHORS[0], tags=["vprim"])
            assert e.world3d("build", {"multiview": False})["status"] == "ok"
            baseline = _status_centers(e)
            node = e.add_perception(content=text, tags=["vprim"], skip_dedup=True)
            r = e.world3d("build", {"multiview": True})
            assert r["status"] == "error", (name, r)
            assert r["reason"] == "bad_camera_pose", (name, r)
            hit = [d for d in r["detail"]["malformed"] if d["node"] == node.id]
            assert hit, ("%s：错误 detail 未点名问题节点 %s" % (name, node.id), r)
            assert hit[0]["error"], (name, r)
            assert _status_centers(e) == baseline, (
                "%s：畸形的多视角 build 污染了旧 _world3d" % name)
        finally:
            e.store.conn.close()


# ===========================================================================
# C7 摄取侧 fail-fast：显式 camera 必须六参数齐备（不落库、不融合、不污染）
# ===========================================================================

def _bad_camera_cases():
    """(标签, camera 入参, 错误消息里必须点名的字段或 None)。"""
    six = _cam_a().to_meta()
    drop = lambda k: {kk: vv for kk, vv in six.items() if kk != k}  # noqa: E731
    return [
        ("空对象", {}, "fov_deg"),
        ("只给 yaw/cx（旧调用形）", {"yaw": 0.5, "cx": 3.0}, "fov_deg"),
        ("缺 cz", drop("cz"), "cz"),
        ("缺 cy", drop("cy"), "cy"),
        ("缺 fov_deg", drop("fov_deg"), "fov_deg"),
        ("非对象", "not-a-camera", None),
        ("NaN 分量", dict(six, yaw=float("nan")), "yaw"),
        ("字符串分量", dict(six, cx="east"), "cx"),
        ("fov 越界", dict(six, fov_deg=200.0), "fov_deg"),
        ("fov 与 fov_deg 不一致", dict(six, fov=75.0), None),
    ]


def test_c7_ingest_rejects_incomplete_camera_without_persisting(tmp_path):
    """空/缺项/非法 camera ⇒ status=error + bad_camera_pose（点名字段），**两个入口**都
    不落库、不融合、不污染旧世界；无 camera 的旧单视角内存行为保持不变。

    判据：用户已确认「缺真实相机位姿明确失败、不伪造位姿」。旧实现走
    `Camera3D.from_meta(strict=False)` 把缺项补成默认值后当真实位姿落库+融合
    （实测 `camera={}` 返回 ok 且 `persisted=node_*`），本组即锁住该缺陷形态。
    """
    cases = _bad_camera_cases()
    params = {"category": "chair", "bbox": [10, 10, 50, 60], "eid": "chair-1",
              "tick": 1, "screen_w": SW, "screen_h": SH}

    # ① 空库：被拒的观测一条都不留（不落 vprim、不进内存世界、世界相机不动）
    e = SpacetimeMemoryEngine(db_path=str(tmp_path / "reject.db"))
    try:
        default_cam = dict(e.world3d("status")["camera"])
        for label, bad, field in cases:
            for entry in ("add_view", "add"):
                r = e.world3d(entry, dict(params, camera=bad))
                assert r["status"] == "error", (entry, label, r)
                assert r["reason"] == "bad_camera_pose", (entry, label, r)
                assert not r.get("persisted"), (
                    "%s/%s：被拒的观测不得报告落库：%r" % (entry, label, r))
                assert r.get("triangulated") is None, (
                    "%s/%s：被拒的观测不得报告融合成功：%r" % (entry, label, r))
                if field is not None:
                    assert field in r["error"], (
                        "%s/%s：错误消息未点名问题字段 %r：%r"
                        % (entry, label, field, r))
        assert e.store.get_nodes_by_tag("vprim") == [], "被拒的观测不得落库"
        assert e.world3d("status")["objects"] == [], "被拒的观测不得进内存世界"
        assert dict(e.world3d("status")["camera"]) == default_cam, "世界相机被改动"
        # 无 camera：两条入口的旧行为逐位保持（add_view 只进内存视图、add 只建内存物体）
        r_nc = e.world3d("add_view", dict(params, eid="chair-nc"))
        assert r_nc["status"] == "ok" and r_nc.get("persisted") is None, r_nc
        r_nc2 = e.world3d("add", dict(params, eid="chair-nc2"))
        assert r_nc2["status"] == "ok" and r_nc2.get("persisted") is None, r_nc2
        assert e.store.get_nodes_by_tag("vprim") == [], "无位姿观测不得落库"
    finally:
        e.store.conn.close()

    # ② 有真实世界的引擎：拒绝必须**原地不动**（既有物体/位置/记忆/相机都不变）
    a = SpacetimeMemoryEngine(db_path=str(tmp_path / "witness.db"))
    try:
        _ingest(a, _cam_a(), TRUTH, eid="tree-1", tick=4)
        _ingest(a, _cam_b(), TRUTH, eid="tree-1", tick=4)
        assert a.world3d("status")["count"] == 1, "见证世界没建起来（前置失败）"
        base_centers = _status_centers(a)
        base_camera = dict(a.world3d("status")["camera"])
        base_nodes = [n.id for n in a.store.get_nodes_by_tag("vprim")]
        for label, bad, _field in cases:
            for entry in ("add_view", "add"):
                r = a.world3d(entry, dict(params, camera=bad))
                assert r["status"] == "error", (entry, label, r)
                assert r["reason"] == "bad_camera_pose", (entry, label, r)
        assert _status_centers(a) == base_centers, "被拒的观测污染了旧世界（物体/位置变了）"
        assert dict(a.world3d("status")["camera"]) == base_camera
        assert [n.id for n in a.store.get_nodes_by_tag("vprim")] == base_nodes, (
            "被拒的观测污染了记忆（多了/少了 vprim 节点）")
    finally:
        a.store.conn.close()


# ===========================================================================
# C8 单视角路径兼容（显式 multiview=False）
# ===========================================================================

def test_c8_explicit_single_view_still_works_over_camera_bound_observations(tmp_path):
    """同一存储上 multiview=False 仍按旧锚点路径建物（带位姿块不碍事）。"""
    e = SpacetimeMemoryEngine(db_path=str(tmp_path / "both.db"))
    try:
        _ingest(e, _cam_a(), TRUTH, tick=4)
        _ingest(e, _cam_b(), TRUTH, tick=4)
        r = e.world3d("build", {"multiview": False})
        assert r["status"] == "ok", r
        assert r["mode"] == "single_view" and r["requested_mode"] == "single_view"
        assert r["reason"] is None
        assert r["observations"] == 2
        # 单视角路径只读正文锚点、**忽略位姿块**：用同一批锚点文本独立重建一遍，
        # 世界物体必须逐个一致（若单视角偷偷用了相机位姿，位置就会不同）。
        ref = World3D()
        for n in e.store.get_nodes_by_tag("vprim"):
            for token in (n.content or "").split("；"):
                vp = parse_anchor(token)
                if vp is not None:
                    ref.add_vprim(vp, SW, SH)
        assert r["objects"] == len(ref.objects) == 2
        assert sorted(tuple(o["center"]) for o in r["detail"]["objects"]) == \
            sorted(o.center for o in ref.objects)
        # 默认入参（不给 multiview）也走同一条旧单视角路径
        r2 = e.world3d("build", {})
        assert r2["status"] == "ok" and r2["mode"] == "single_view"
        assert r2["scene"] == r["scene"]
    finally:
        e.store.conn.close()


# ===========================================================================
# C9 `world3d("add")` 也是观测入口：给位姿 ⇒ 同一条落库通路；不给 ⇒ 旧行为
# ===========================================================================

def test_c9_add_ingestion_binds_camera_and_keeps_legacy_behavior(tmp_path):
    """add 给 camera ⇒ 绑定位姿落库、反投影按该位姿；不给 ⇒ 与第一批逐位一致。"""
    cam = _cam_a()
    params = {"category": "chair", "bbox": _bbox(cam, TRUTH), "eid": "chair-1",
              "tick": 4, "screen_w": SW, "screen_h": SH, "confidence": 0.9,
              "timestamp": 7.0}

    # ① 不给 camera：只进内存世界，不落库（第一批的既有行为）
    e = SpacetimeMemoryEngine(db_path=str(tmp_path / "add-legacy.db"))
    try:
        r0 = e.world3d("add", dict(params))
        assert r0["status"] == "ok" and r0.get("persisted") is None, r0
        assert e.store.get_nodes_by_tag("vprim") == [], "无位姿观测不得落库"
        center_no_cam = e.world3d("status")["objects"][0]["center"]
    finally:
        e.store.conn.close()

    # ② 给 camera：落库同一块格式；反投影按该位姿；世界相机不被这次观测永久改写
    db = tmp_path / "add-camera.db"
    a = SpacetimeMemoryEngine(db_path=str(db))
    try:
        r = a.world3d("add", dict(params, camera=cam.to_meta()))
        assert r["status"] == "ok" and r["persisted"], r
        node = a.store.get_nodes_by_tag("vprim")[0]
        meta = parse_camera_observation(node.content)
        assert meta["camera"] == cam.to_meta(), "add 落库的位姿被截断/改写"
        assert meta["eid"] == "chair-1" and meta["tick"] == 4
        status = a.world3d("status")
        assert len(status["objects"]) == 1
        center_cam = status["objects"][0]["center"]
        assert center_cam != center_no_cam, (
            "给了位姿却与默认相机反投影同一位置——位姿没参与反投影")
        assert status["camera"]["cx"] == 0.0 and status["camera"]["cz"] == 0.0, (
            "一次观测的位姿被留成了世界默认相机：%r" % (status["camera"],))
        # ②b `add` 的显式 camera 同样必须六参数齐备：空/缺项（含旧调用形）在入口即拒，
        #     既不落库、也不进内存世界、不改世界相机（R6 返工，与 C7 同一判据）
        base_nodes = [n.id for n in a.store.get_nodes_by_tag("vprim")]
        base_centers = _status_centers(a)
        for bad in ({}, {"yaw": cam.yaw, "pitch": cam.pitch,
                         "cx": cam.cx, "cy": cam.cy}):
            r_bad = a.world3d("add", dict(params, camera=bad, eid="chair-bad"))
            assert r_bad["status"] == "error", (bad, r_bad)
            assert r_bad["reason"] == "bad_camera_pose", (bad, r_bad)
            assert not r_bad.get("persisted"), (bad, r_bad)
        assert [n.id for n in a.store.get_nodes_by_tag("vprim")] == base_nodes, (
            "缺项 camera 的 add 落了库")
        assert _status_centers(a) == base_centers, (
            "缺项 camera 的 add 进了内存世界：%r" % (_status_centers(a),))
        assert dict(a.world3d("status")["camera"]) == dict(status["camera"])
        # ③ 两条入口（add / add_view）写的是同一种载体：混用也能重建
        _ingest(a, _cam_b(), TRUTH, category="chair", eid="chair-1", tick=4)
    finally:
        a.store.conn.close()

    b = SpacetimeMemoryEngine(db_path=str(db))
    try:
        r = b.world3d("build", {"multiview": True})
        assert r["status"] == "ok", r
        assert r["objects"] == 1 and r["observations"] == 2
        err = _center_error(r["detail"]["objects"][0]["center"], TRUTH)
        assert err < 0.05, "add 落库 + add_view 落库混用重建偏了 %.4f m" % err
    finally:
        b.store.conn.close()


# ===========================================================================
# C10 单条带位姿观测：多视角 build 成功但不成物（不报错、不伪造）
# ===========================================================================

def test_c10_single_posed_observation_reports_no_triangulation(tmp_path):
    """只有一条带位姿观测 ⇒ 融合器「首次观测仅记录」：objects=0、observations=1。"""
    db = tmp_path / "single-view.db"
    e = SpacetimeMemoryEngine(db_path=str(db))
    try:
        _ingest(e, _cam_a(), TRUTH, eid="tree-1", tick=3)
    finally:
        e.store.conn.close()
    b = SpacetimeMemoryEngine(db_path=str(db))
    try:
        r = b.world3d("build", {"multiview": True})
        assert r["status"] == "ok", r
        assert r["mode"] == "multiview" and r["objects"] == 0 and r["observations"] == 1
        assert [v["triangulated"] for v in r["views"]] == [False]
        assert r["detail"]["count"] == 0
    finally:
        b.store.conn.close()
