# -*- coding: utf-8 -*-
"""test_issue777_projectile_no_penetration · 弹跳抛体「不穿地」守卫（#777）
================================================================================
缺陷（issue #777，三条主张经独立复核全部成立）
------------------------------------------------
`lingshu/world/physics.py::projectile_state` 的弹跳分支在弹跳循环 `break` 之后
**无条件**用剩余时间 `rem` 做自由抛体外推（`pu + vu·rem + ½·gz·rem²`）。两条通路会让
该外推把 up 轴坐标推到 `ground_z` **以下**、且随 `t` 增大**无界下降**——与同一
docstring 的「不会穿透」相反，且不报错、不置标志位：

  ① **弹跳预算用尽**：`bounces >= max_b`（`max_b = int(decl.get("max_bounces") or 8)`，
     缺省 8）在第 `max_b+1` 次落地时刻成立，此时 `rem` 仍是大正数。
     实测（`ground_z=0.0`、`e=0.8`、`p0_up=10`）：`t=15.0` ⇒ `up = -90.5310307364`。
  ② **贴地初值**：`ground_z == p0_up` 且 `v0_up <= 0` ⇒ 第 1 轮 `disc = 0 ⇒ flight = 0`
     ⇒ `flight <= 0` 即 `break`，此后 `pu == ground`、`vu <= 0` ⇒ 对**任意** `t > 0`
     都在面下（与 `max_bounces` 取值无关）。

★ 修法陷阱（本守卫的判别力核心）
--------------------------------
`or` 里 **`rem < flight`（正常空中腿：球仍在空中、窗口在落地前结束）排在
`bounces >= max_b` 之前**，而后者在正常空中腿上**往往也成立**（例：默认 cap 8、
`t=10.9` 时 `bounces == 8` 而 `rem < flight`）。⇒ 不能写成「只要 `bounces >= max_b`
就钳位」，否则会把「球还在空中」的正常读数一并改掉（回归）。必须判**退出时是否仍处空中**。

本守卫的断言面
--------------
  1. 通路①（缺省 `max_b=8`）在 `t ∈ {10.9, 11.0, 15.0, 30.0, 300.0}` 上 `up >= ground - 1e-9`；
     `t >= 11.0` 还须**恰好贴地**（`up == ground_z`）且**竖直速度为 0**。
  2. 通路②（贴地静止 `v0_up=0` / 贴地下压 `v0_up=-5`）在 `t ∈ {1e-6, 1.0, 5.0, 30.0, 300.0}`
     上不穿地，且竖直速度为 0。
  3. **正对照（防过修）**：正常空中腿（`rem < flight`）与 `disc < 0` 腿的**位置与速度逐位相同**
     （硬编码期望值取自修前 `origin/main` 实现）。
  4. 无弹跳腿（`bounce` 缺省）读数逐位不变。
  5. `max_bounces` 取**负值**的边界臂不穿地；`max_b` 的 `or 8` 回落口径（`0`/`False`/`None`
     ⇒ 8）逐位不变。
  6. 钳位只在 up 轴生效：水平两分量仍按匀速外推（贴地不冻结水平运动）。
  7. 镜像实现（`bridges/blender/.../anim/eval.py::_projectile_state`，Z-up 同判据）同口径不穿地。

判别力自证（回退即红）
----------------------
    git checkout -- lingshu/world/physics.py      # 回退到缺陷形态
    python -X utf8 -m pytest tests/test_issue777_projectile_no_penetration.py -q
        ⇒ 断言 1 / 2 / 5 / 6 / 7 红（实测 5 failed）
    git checkout -- lingshu/world/physics.py      # 还原（或从补丁重放）
        ⇒ 全绿且 `git diff` 为空

运行（lingshu 仓根）
    python -X utf8 -m pytest tests/test_issue777_projectile_no_penetration.py -q
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:                     # 直跑本件（不经 `python -m pytest`）时兜底
    sys.path.insert(0, REPO)

from lingshu.world import physics as P      # noqa: E402

UP = 1
GROUND = 0.0
TOL = 1e-9

#: 通路①（弹跳预算用尽）观测时刻集。`10.9` 是**正常空中腿**（仍在面以上，不该被改），
#: 自 `11.0` 起（第 9 次落地 `t_9 = 10.936036931` 之后）属穿地形态。
CAP_TIMES = (10.9, 11.0, 15.0, 30.0, 300.0)

#: 通路②（贴地初值）观测时刻集。`t=1e-6` 也在内——通路②对**任意** `t > 0` 都成立，
#: 若只采样大 `t`，一个「先放行再钳位」的半吊子修法仍会漏网。
REST_TIMES = (1e-6, 1.0, 5.0, 30.0, 300.0)

#: 正常空中腿（`rem < flight`）的**修前**读数（逐位硬编码，来自 `origin/main` 实现）。
#: 这些读数在任何修法下都必须逐位不变——它们就是「过修」的探测器。
AIRBORNE_EXPECTED = {
    0.5: ((0.0, 8.77416875, 0.0), (0.0, -4.903325, 0.0)),
    1.0: ((0.0, 5.096675, 0.0), (0.0, -9.80665, 0.0)),
    2.0: ((0.0, 4.803797100090957, 0.0), (0.0, 5.595248550045479, 0.0)),
    10.0: ((0.0, 0.318389369317051, 0.0), (0.0, 1.5431637458149061, 0.0)),
    10.9: ((0.0, 0.07830487214920867, 0.0), (0.0, -1.9962054533026001, 0.0)),
}

#: 无弹跳腿的修前读数（`bounce` 缺省 ⇒ 纯抛体分支，没有地面概念）。
NO_BOUNCE_EXPECTED = {
    1.0: ((0.0, 5.096675, 0.0), (0.0, -9.80665, 0.0)),
    3.0: ((0.0, -34.129925, 0.0), (0.0, -29.41995, 0.0)),
}

#: `disc < 0` 腿（初值已在 `ground_z` 以下 ⇒ 永远回不到地面）的修前读数：
#: 该腿**不属于**「落到地面停住」的情形，修法不得把它一并钳位（钳到 `ground` 会把
#: 一个本就在面下的初值凭空抬到面上）。
DISC_NEG_EXPECTED = {
    1.0: ((0.0, -5.903325, 0.0), (0.0, -9.80665, 0.0)),
    5.0: ((0.0, -123.583125, 0.0), (0.0, -49.033249999999995, 0.0)),
}


def _decl(**over):
    """缺省 = 通路①输入：`p0_up=10`、静止释放、`e=0.8`、`ground_z=0`、`max_bounces` 缺省。"""
    d = {"p0": (0.0, 10.0, 0.0), "v0": (0.0, 0.0, 0.0),
         "bounce": True, "ground_z": GROUND, "restitution": 0.8}
    d.update(over)
    return d


# ------------------------------------------------------------------ 1 通路①
def test_1_bounce_budget_exhausted_does_not_penetrate():
    """通路①：弹跳次数用尽后必须落到 `ground_z` 停住，不得继续下探、不得随 t 下降。"""
    d = _decl()
    for t in CAP_TIMES:
        p, v = P.projectile_state(d, t)
        assert p[UP] >= GROUND - TOL, (
            "t=%r 时 up=%.17g 落到 ground_z=%r 以下（弹跳预算用尽后仍做自由抛体外推）"
            % (t, p[UP], GROUND))
    # `t >= 11.0` 已越过第 9 次落地（t_9 = 10.936036931）⇒ 必须**恰好**贴地且竖直速度 0
    for t in (11.0, 15.0, 30.0, 300.0):
        p, v = P.projectile_state(d, t)
        assert p[UP] == GROUND, "t=%r 贴地腿的 up 应为 ground_z=%r，实得 %.17g" % (
            t, GROUND, p[UP])
        assert v[UP] == 0.0, "t=%r 贴地腿的竖直速度应为 0，实得 %.17g" % (t, v[UP])
    # 「随 t 无界下降」的直接判据：进入贴地段之后，任意两个时刻的读数必须相等
    # （穿地形态下 t=30 → t=300 会再降约 3.6e5，本条即红）
    ups = [P.projectile_state(d, t)[0][UP] for t in (11.0, 15.0, 30.0, 300.0)]
    assert len(set(ups)) == 1 and ups[0] == GROUND, (
        "贴地段读数随 t 变化（无界下降）：%r" % (ups,))


# ------------------------------------------------------------------ 2 通路②
@pytest.mark.parametrize("v0_up", [0.0, -5.0])
def test_2_resting_initial_state_does_not_penetrate(v0_up):
    """通路②：初值贴地（`ground_z == p0_up`）且 `v0_up <= 0` ⇒ 任意 t>0 都贴地静止。"""
    d = _decl(p0=(0.0, GROUND, 0.0), v0=(0.0, v0_up, 0.0))
    for t in REST_TIMES:
        p, v = P.projectile_state(d, t)
        assert p[UP] >= GROUND - TOL, (
            "v0_up=%r t=%r 时 up=%.17g 落到 ground_z 以下" % (v0_up, t, p[UP]))
        assert p[UP] == GROUND, (
            "v0_up=%r t=%r 贴地腿的 up 应恰为 ground_z=%r，实得 %.17g"
            % (v0_up, t, GROUND, p[UP]))
        assert v[UP] == 0.0, (
            "v0_up=%r t=%r 贴地腿的竖直速度应为 0，实得 %.17g" % (v0_up, t, v[UP]))


def test_2b_cap_value_does_not_change_resting_leg():
    """通路②与 `max_bounces` 取值无关：`8` / 负值 / `0`（回落成 8）三档读数一致。"""
    base = None
    for mb in (None, -1, 0):
        d = _decl(p0=(0.0, GROUND, 0.0), v0=(0.0, 0.0, 0.0))
        if mb is not None:
            d["max_bounces"] = mb
        got = tuple(P.projectile_state(d, t) for t in REST_TIMES)
        if base is None:
            base = got
        else:
            assert got == base, "max_bounces=%r 改变了贴地腿读数" % (mb,)


# --------------------------------------------------------- 3 正对照（防过修）
def test_3_positive_control_airborne_legs_bit_identical():
    """正常空中腿（`rem < flight`）读数必须与修前**逐位相同**（硬编码期望值）。

    这是本守卫的**过修探测器**：任何「只要 `bounces >= max_b` 就钳位」的修法都会
    让 `t=10.9`（`bounces == 8` 但 `rem < flight`，球仍在空中）从 `0.0783…` 变成 `0.0`，
    本条即红。
    """
    d = _decl()
    for t in sorted(AIRBORNE_EXPECTED):
        ep, ev = AIRBORNE_EXPECTED[t]
        p, v = P.projectile_state(d, t)
        assert p == ep, "t=%r 正常空中腿位置被改动：期望 %r，实得 %r" % (t, ep, p)
        assert v == ev, "t=%r 正常空中腿速度被改动：期望 %r，实得 %r" % (t, ev, v)


def test_3b_disc_negative_leg_bit_identical():
    """`disc < 0` 腿（初值已在面下、永远回不到地面）读数必须与修前逐位相同。

    该腿不属「落到地面停住」，不得被钳位（否则会把面下的初值凭空抬到 `ground_z`）。
    """
    d = _decl(p0=(0.0, -1.0, 0.0))
    for t in sorted(DISC_NEG_EXPECTED):
        ep, ev = DISC_NEG_EXPECTED[t]
        p, v = P.projectile_state(d, t)
        assert p == ep, "t=%r disc<0 腿位置被改动：期望 %r，实得 %r" % (t, ep, p)
        assert v == ev, "t=%r disc<0 腿速度被改动：期望 %r，实得 %r" % (t, ev, v)


# ------------------------------------------------------------- 4 无弹跳腿
def test_4_no_bounce_leg_bit_identical():
    """无弹跳腿（`bounce` 缺省）读数必须与修前逐位相同。"""
    d = _decl()
    d.pop("bounce")
    for t in sorted(NO_BOUNCE_EXPECTED):
        ep, ev = NO_BOUNCE_EXPECTED[t]
        p, v = P.projectile_state(d, t)
        assert p == ep, "t=%r 无弹跳腿位置被改动：期望 %r，实得 %r" % (t, ep, p)
        assert v == ev, "t=%r 无弹跳腿速度被改动：期望 %r，实得 %r" % (t, ev, v)


# --------------------------------------------- 5 `max_bounces` 边界（负值 / 回落）
def test_5_negative_max_bounces_does_not_penetrate():
    """`max_bounces` 取负值：第 1 轮即 `bounces(0) >= max_b` ⇒ 首次落地后停住，不穿地。"""
    d = _decl(max_bounces=-1)
    for t in (1.0, 2.0, 10.0, 30.0, 300.0):
        p, v = P.projectile_state(d, t)
        assert p[UP] >= GROUND - TOL, (
            "max_bounces=-1 t=%r 时 up=%.17g 落到 ground_z 以下" % (t, p[UP]))
    # `t=1.0` 时 `rem=1.0 < t_1=1.42808698123` ⇒ 正常空中腿，读数必须与纯自由落体一致
    p1, v1 = P.projectile_state(d, 1.0)
    assert (p1, v1) == ((0.0, 5.096675, 0.0), (0.0, -9.80665, 0.0)), (p1, v1)
    # 落地之后（`t >= t_1`）必须贴地且竖直速度为 0
    for t in (2.0, 10.0, 30.0, 300.0):
        p, v = P.projectile_state(d, t)
        assert p[UP] == GROUND, (t, p)
        assert v[UP] == 0.0, (t, v)


def test_5b_or8_fallback_semantics_unchanged():
    """`max_bounces` 的 `or 8` 回落口径（`0`/`False`/`None` ⇒ 8）逐位不变。

    这三档都必须与**不传** `max_bounces` 的读数逐位相同（含穿地区间被修掉之后的读数）。
    """
    ref = {t: P.projectile_state(_decl(), t) for t in CAP_TIMES}
    for mb in (0, False, None):
        d = _decl(max_bounces=mb)
        for t in CAP_TIMES:
            assert P.projectile_state(d, t) == ref[t], (
                "max_bounces=%r 与缺省口径不一致（t=%r）：%r != %r"
                % (mb, t, P.projectile_state(d, t), ref[t]))


# ------------------------------------------- 6 钳位只动 up 轴（水平不冻结）
def test_6_clamp_only_touches_up_axis():
    """贴地钳位只在 up 轴生效：水平两分量仍按匀速外推，速度的 up 分量为 0。"""
    p0, v0, t = (1.0, 10.0, 2.0), (3.0, 0.0, -1.0), 30.0
    p, v = P.projectile_state(_decl(p0=p0, v0=v0), t)
    assert p[UP] == GROUND, p
    assert v[UP] == 0.0, v
    assert abs(p[0] - (p0[0] + v0[0] * t)) < 1e-9, p
    assert abs(p[2] - (p0[2] + v0[2] * t)) < 1e-9, p
    assert (v[0], v[2]) == (v0[0], v0[2]), v


# ------------------------------------------------------- 7 up 轴泛化 / 镜像面
def test_7_up_axis_is_generic():
    """钳位按 `up` 轴泛化：`up=2`（Z-up 口径）同样不穿地，且空中腿逐位不变。"""
    d = _decl(p0=(0.0, 0.0, 10.0), up=2)
    p_air, v_air = P.projectile_state(d, 1.0)
    assert (p_air, v_air) == ((0.0, 0.0, 5.096675), (0.0, 0.0, -9.80665)), (p_air, v_air)
    p_far, v_far = P.projectile_state(d, 30.0)
    assert p_far[2] == GROUND, p_far
    assert v_far[2] == 0.0, v_far


def test_7b_mirror_implementation_same_clamp():
    """镜像实现（Blender 桥 · Z-up 同判据）必须同口径：通路①/②不穿地、空中腿逐位不变。

    镜像与主实现是**同一算法的两份副本**（主实现 Y-up、镜像 Z-up），issue #777 要求两侧同改。
    该包明确「不 import bpy」，可在无 Blender 的机器上单独跑；若环境确实导不进来
    （例如未随仓部署该子树）则显式 SKIP 并打印原因——**不静默通过**。
    """
    bridge_root = os.path.join(REPO, "bridges", "blender")
    if not os.path.isdir(bridge_root):
        pytest.skip("镜像子树不在场：%s" % os.path.relpath(bridge_root, REPO))
    if bridge_root not in sys.path:
        sys.path.insert(0, bridge_root)
    try:
        mirror = importlib.import_module("wm_bridge.factory.anim.eval")
    except Exception as exc:                                  # pragma: no cover
        pytest.skip("镜像模块导入失败（环境面）：%s: %s" % (type(exc).__name__, exc))
    fn = getattr(mirror, "_projectile_state", None)
    assert fn is not None, "镜像模块里没有 _projectile_state（改名了？本守卫需同步）"

    def mdecl(p0, v0, **over):
        d = {"p0": p0, "v0": v0, "bounce": True, "ground_z": GROUND, "restitution": 0.8}
        d.update(over)
        return d

    # 通路①（镜像 Z-up ⇒ up 轴 = 第 3 分量）
    d = mdecl((0.0, 0.0, 10.0), (0.0, 0.0, 0.0))
    for t in CAP_TIMES:
        p, v = fn(d, t)
        assert p[2] >= GROUND - TOL, ("镜像 t=%r 穿地：up=%.17g" % (t, p[2]))
    for t in (1.0, 10.9):
        assert fn(d, t) == P.projectile_state(
            _decl(p0=(0.0, 0.0, 10.0), up=2), t), (
            "镜像 t=%r 的空中腿读数与主实现不一致（两侧已不同源）" % (t,))
    # 通路②
    for v0u in (0.0, -5.0):
        d = mdecl((0.0, 0.0, GROUND), (0.0, 0.0, v0u))
        for t in REST_TIMES:
            p, v = fn(d, t)
            assert p[2] == GROUND, ("镜像贴地腿 t=%r up=%.17g" % (t, p[2]))
            assert v[2] == 0.0, ("镜像贴地腿 t=%r vz=%.17g" % (t, v[2]))
