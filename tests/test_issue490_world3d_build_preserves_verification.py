# -*- coding: utf-8 -*-
"""守卫 · issue #490 · `world3d("build")` 不得丢弃既有锚点核验状态
============================================================================
缺陷（公开 issue #490，P1）：
  门面 `SpacetimeMemoryEngine.world3d("build", ...)` 与其它动词（`render`/`status`/
  `verify`/`graph`）不同——它**整体替换** `self._world3d`：

      world = World3D()          # 新对象
      self._world3d = world      # 旧对象（连同其 _anchor_graph._verifier）被回收

  #262 的修复（`World3D.build_anchor_graph` 里把旧图的 `_verifier` 搬到新图）只覆盖
  「同一个 World3D 上的 graph 刷新」；换对象这条路径没被覆盖。而 `SemanticAnchor.id`
  由内容确定性导出（#262 的另一半）⇒ 重建出的锚点 id **逐字相同**、记录却是干净的：
  被两个强通道驳斥过的锚点可以一路升到 ACCEPT_strong / ACCEPT_stable，门面照样
  返回 status="ok"。

  实测（HEAD e673279，逐字跑 issue 复现脚本）：
      control  graph-refresh : conflicts=['audio', 'tactile'] -> NOT_ACCEPTED
      bypass   build+graph   : same anchor id=True conflicts=[] -> ACCEPT_strong
      bypass   +3 rounds/61s : conflicts=[] rounds=4 -> ACCEPT_stable

修法（最小面，只动 `lingshu/core/core.py` 的 build 分支，不改任何签名）：
  build 分支的两处 `world = World3D()` 改为经就地迁移助手
  `world = carry_anchor_verification(World3D())`：旧世界已有核验器时，把旧图交给新
  对象后**按新世界重建一次图**——核验器经 #262 既有迁移路径搬到新图上，锚点集合仍是
  新世界的。旧世界从未核验过（无 `_verifier`）时助手是**空操作**，与修前逐位一致。

断言组（把修复抽掉 ⇒ 对应组红并点名 #490）：
  A 冲突存活：两强通道驳斥 → build → 同一 id 仍保留 channel_conflicts 且
    confirmation=NOT_ACCEPTED（缺陷态：conflicts=[] → ACCEPT_strong）。
  B 证据存活：build 后未被重新喂入的通道证据（audio）仍在（缺陷态：只剩新喂的两条）。
  C 核验器同一实例：build 后 `_world3d._anchor_graph._verifier` 就是 build 前那一个
    （身份断言——缺陷态是新实例，且注入的假时钟一并失效 ⇒ verified_rounds 归零）。
  D 稳定轮数存活：build 前后连续累计，不倒退。
  E 多视角路径同样承载（`build(multiview=True)` 是另一条赋值点）。
  F 反过修：build 仍须按记忆**重建世界**（不得为了保住状态而整体沿用旧世界/旧图）——
    未落库的内存物体在 build 后消失、新物体进得来、旧锚点冲突不泄漏到新锚点。

运行（仓根）：python -X utf8 -m pytest tests/test_issue490_world3d_build_preserves_verification.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402
from lingshu.world.world3d import Camera3D  # noqa: E402

SW, SH = 800, 600
#: 一条视觉原语（记忆里的 vprim 节点）——issue #490 复现脚本的最小输入。
CHAIR_VPRIM = "[视觉原语] chair@(100,200,300,500)"
TABLE_VPRIM = "[视觉原语] table@(600,300,700,500)"
#: 强通道（tactile/audio）——两个独立强通道驳斥即构成「被驳斥」的锚点。
CONFLICT_CHANNELS = ("tactile", "audio")


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _engine(vprim: str = CHAIR_VPRIM) -> SpacetimeMemoryEngine:
    e = SpacetimeMemoryEngine(":memory:")
    e.add_perception(vprim, tags=["vprim"])
    return e


def _build(e, **params) -> dict:
    return e.world3d("build", dict(params))


def _anchor_ids(e) -> list:
    return [a["id"] for a in e.world3d("graph", {})["graph"]["anchors"]]


def _conflict(e, anchor_id: str) -> None:
    """两个强通道驳斥该锚点（expected != actual ⇒ 真矛盾，#401）。"""
    for ch in CONFLICT_CHANNELS:
        e.world3d("verify_conflict", {"anchor_id": anchor_id, "channel": ch,
                                      "expected": "chair", "actual": "box"})


def _support(e, anchor_id: str) -> dict:
    """视觉 + 触觉支持一轮，返回该锚点的验证状态（门面公开读数）。"""
    e.world3d("verify", {"anchor_id": anchor_id, "channel": "visual", "evidence": 0.9})
    return e.world3d("verify", {"anchor_id": anchor_id, "channel": "tactile",
                                "evidence": 0.9})["verification"]


def _verifier_of(e):
    """当前世界的核验器（无则 None）。"""
    graph = getattr(e._world3d, "_anchor_graph", None)
    return getattr(graph, "_verifier", None)


# ---------------------------------------------------------------------------
# A 冲突存活（#490 的主断言）
# ---------------------------------------------------------------------------

def test_conflict_survives_build_with_same_anchor_id():
    """驳斥 → build → 同一 id 仍带冲突、仍 NOT_ACCEPTED（缺陷态：清零并升 ACCEPT_strong）。"""
    e = _engine()
    _build(e)
    a1 = _anchor_ids(e)[0]
    _conflict(e, a1)

    _build(e)                              # ← 缺陷点：整体换 World3D
    ids = _anchor_ids(e)
    assert len(ids) == 1, ids
    a2 = ids[0]
    assert a2 == a1, (
        "#490 前置：锚点 id 应由内容确定性导出（#262）——%r != %r" % (a1, a2))

    r = _support(e, a2)
    assert sorted(r["channel_conflicts"]) == sorted(CONFLICT_CHANNELS), (
        "#490：build 整体换掉 World3D，锚点核验状态（channel_conflicts）随之丢失——"
        "同一 id=%r 的冲突记录变干净：%r" % (a2, r))
    assert r["confirmation"] == "NOT_ACCEPTED", (
        "#490：被两个强通道驳斥过的锚点在 build 后可升档——confirmation=%r"
        % r["confirmation"])


def test_conflict_survives_repeated_build():
    """连续两次 build 也不得把冲突洗掉（换对象路径的重复触发）。"""
    e = _engine()
    _build(e)
    a1 = _anchor_ids(e)[0]
    _conflict(e, a1)
    _build(e)
    _build(e)
    a2 = _anchor_ids(e)[0]
    r = _support(e, a2)
    assert sorted(r["channel_conflicts"]) == sorted(CONFLICT_CHANNELS), (
        "#490：连续 build 后冲突丢失——%r" % (r,))
    assert r["confirmation"] == "NOT_ACCEPTED", r["confirmation"]


# ---------------------------------------------------------------------------
# B 通道证据存活（build 后未被重新喂入的通道）
# ---------------------------------------------------------------------------

def test_channel_evidence_survives_build():
    """build 后未被重新喂入的通道证据（audio）仍在——不是「被重新写入」而是「被保住」。"""
    e = _engine()
    _build(e)
    a1 = _anchor_ids(e)[0]
    e.world3d("verify", {"anchor_id": a1, "channel": "audio", "evidence": 0.9})

    _build(e)
    a2 = _anchor_ids(e)[0]
    r = e.world3d("verify", {"anchor_id": a2, "channel": "visual",
                             "evidence": 0.9})["verification"]
    assert r["channel_evidence"].get("audio") == 0.9, (
        "#490：build 后既有通道证据丢失——channel_evidence=%r" % (r["channel_evidence"],))


# ---------------------------------------------------------------------------
# C 核验器同一实例（身份断言：最直接的「状态是否跟着走」读法）
# ---------------------------------------------------------------------------

def test_verifier_instance_is_the_same_after_build():
    """build 后核验器就是 build 前那一个实例（缺陷态：新实例 ⇒ 全部记录归零）。"""
    e = _engine()
    _build(e)
    a1 = _anchor_ids(e)[0]
    e.world3d("verify", {"anchor_id": a1, "channel": "audio", "evidence": 0.9})
    before = _verifier_of(e)
    assert before is not None, "#490 前置：验证器未建起来"

    _build(e)
    _anchor_ids(e)                          # 触发新世界的图（新对象上）
    after = _verifier_of(e)
    assert after is not None, "#490：build 后新世界没有核验器"
    assert after is before, (
        "#490：build 换对象时核验器被丢掉——新实例 %r 不是 build 前的 %r"
        % (id(after), id(before)))
    assert before.anchor_state(a1)["channel_evidence"].get("audio") == 0.9, (
        "#490：核验器实例相同但记录丢了：%r" % (before.anchor_state(a1),))


# ---------------------------------------------------------------------------
# D 稳定轮数跨 build 不倒退
# ---------------------------------------------------------------------------

def test_verified_rounds_survive_build():
    """跨时间稳定轮数跨 build 继续累计（缺陷态：新核验器 ⇒ 轮数从 0 重新开始）。"""
    e = _engine()
    _build(e)
    a1 = _anchor_ids(e)[0]
    # 核验器是**懒建**的（首次 verify/verify_conflict 时才有）——先用一条弱通道把它建出来，
    # 再把时钟换成确定性的假钟（同 #160 测试口径：跨时间稳定轮数须时间分离）。
    e.world3d("verify", {"anchor_id": a1, "channel": "prediction", "evidence": 0.9})
    verifier = _verifier_of(e)
    assert verifier is not None
    fake = [1000.0]
    verifier._clock = lambda: fake[0]

    r = None
    for _ in range(3):
        fake[0] += 61.0
        r = _support(e, a1)
    assert r["verified_rounds"] == 3, r

    _build(e)
    a2 = _anchor_ids(e)[0]
    assert a2 == a1
    after = _verifier_of(e)
    assert after is not None, (
        "#490：build 后新世界没有核验器（稳定轮数随之归零）")
    after._clock = lambda: fake[0]             # 缺陷态：这里是新核验器，假时钟一并失效
    fake[0] += 61.0
    r = _support(e, a2)
    assert r["verified_rounds"] == 4, (
        "#490：build 后稳定轮数倒退/归零——verified_rounds=%r（应为 4）"
        % r["verified_rounds"])


# ---------------------------------------------------------------------------
# E 多视角 build 路径（另一处赋值点）
# ---------------------------------------------------------------------------

def _engine_with_posed_views() -> SpacetimeMemoryEngine:
    """两条**带真实相机位姿**的观测 → 供 `build(multiview=True)` 三角化（同 #211 Q5 口径）。"""
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
    return e


def test_multiview_build_also_carries_verification():
    """`build(multiview=True)` 是另一条换对象路径——同样不得丢核验状态。"""
    e = _engine_with_posed_views()
    r = _build(e, multiview=True)
    assert r["status"] == "ok", r
    a1 = _anchor_ids(e)[0]
    _conflict(e, a1)

    r2 = _build(e, multiview=True)
    assert r2["status"] == "ok", r2
    a2 = _anchor_ids(e)[0]
    assert a2 == a1, (a1, a2)
    rv = _support(e, a2)
    assert sorted(rv["channel_conflicts"]) == sorted(CONFLICT_CHANNELS), (
        "#490：多视角 build 路径同样丢弃核验状态——%r" % (rv,))
    assert rv["confirmation"] == "NOT_ACCEPTED", rv["confirmation"]


# ---------------------------------------------------------------------------
# F 反过修：build 仍须按记忆重建世界（不得为保状态而整体沿用旧对象/旧图）
# ---------------------------------------------------------------------------

def test_build_still_rebuilds_world_from_memory():
    """build 仍按记忆重建：未落库的内存物体消失、新 vprim 进得来、锚点数与世界一致。"""
    e = _engine()
    e.world3d("add", {"category": "box", "bbox": [10, 10, 50, 60]})   # 只进内存，不落库
    assert e.world3d("status")["count"] == 1

    r = _build(e)
    assert r["status"] == "ok" and r["objects"] == 1, r
    assert [o["category"] for o in e.world3d("status")["objects"]] == ["chair"], (
        "#490 反过修：build 未按记忆重建世界（旧世界被整体沿用）——%r"
        % ([o["category"] for o in e.world3d("status")["objects"]],))
    assert len(_anchor_ids(e)) == 1


def test_carried_verifier_does_not_leak_conflicts_to_new_anchor():
    """核验器整体沿用 ≠ 旧图沿用：build 后世界上的锚点图是**新世界**的。

    读法：直接看世界对象上的 `_anchor_graph.anchors`（不经门面 `graph` 动作重建——
    那条路会顺手重建一次图，掩盖「旧图被整体沿用」）。
    """
    e = _engine()
    _build(e)
    a1 = _anchor_ids(e)[0]
    _conflict(e, a1)

    e.add_perception(TABLE_VPRIM, tags=["vprim"])
    r = _build(e)
    assert r["status"] == "ok" and r["objects"] == 2, r
    graph = getattr(e._world3d, "_anchor_graph", None)
    assert graph is not None, "#490：build 后世界没有锚点图（核验器无处安放）"
    ids = sorted(graph.anchors)
    assert len(ids) == 2 and a1 in ids, (
        "#490：build 后世界上的锚点图不是新世界的（旧图被整体沿用）——%r" % (ids,))
    new_id = [i for i in ids if i != a1][0]
    rv = e.world3d("verify", {"anchor_id": new_id, "channel": "visual",
                              "evidence": 0.9})["verification"]
    assert rv["channel_conflicts"] == {}, (
        "#490：旧锚点的冲突泄漏到新锚点——%r" % (rv,))


# ---------------------------------------------------------------------------
# 自身可收集（防落入 test_gate_coverage 的「同族盲区」）
# ---------------------------------------------------------------------------

def test_guard_is_collectable():
    assert os.path.basename(__file__).startswith("test_")
    assert SpacetimeMemoryEngine is not None and Camera3D is not None


def _main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print("FAIL %-58s %s" % (t.__name__, e))
        else:
            passed += 1
            print("ok   %s" % t.__name__)
    print("\n%d/%d passed" % (passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_main())
