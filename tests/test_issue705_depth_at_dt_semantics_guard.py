# -*- coding: utf-8 -*-
"""守卫 · issue #705 —— `sweep_aabb.depth_at_dt` 的**口径**与名实不符
============================================================================
判据来源
--------
· **issue #705**：`sweep_aabb` 的返回键 `depth_at_dt`，其**字段名**与
  `physics.sweep_aabb` 的 **docstring** 都把它定义为「若不拦、**dt 时刻**的嵌入深度」，
  但旧实现在**同一函数内**有两个互不相同的口径：

  - **通路①**（起始不重叠、命中来自 sweep 判据）：逐轴算
    `gap_i = |Δc_i(dt)| − (ra_i + rb_i)`，取三轴中**负得最多**者（`max(−gap_i)`）。
    它**不要求三轴同时重叠**（同节 `aabb_overlap` 要求），也**不取最小逃逸距离**
    （同节 `aabb_mtv` 的口径）。⇒ 沿运动轴已分离、横向仍重叠时，dt 时刻两箱并不
    重叠却仍报正数（报的是**横向**逃逸距离）。
  - **通路②**（起始已重叠 ⇒ 早退分支）：`depth_at_dt` 直接取 `aabb_mtv(a, b)` 的
    **t=0** 值，**与 `dt`、速度无关** ⇒ 既会少报（dt 时刻真嵌入 0.6 时报 0.1），
    也会在 dt 时刻无嵌入时报正数。

· **同节 `physics.aabb_mtv` 的口径**（本守卫锁定的定义）：深度 = 两箱**三轴同时**
  重叠时，各轴**逃逸距离** `(ra_i + rb_i) − |Δc_i|` 的**最小值**（把两箱推开所需的
  最小距离，包含情形同样是"推出去"的距离）。
· **同节 `physics.aabb_overlap` 的口径**：本仓全节「**贴着不算重叠**」（严格 `<`）
  ⇒ 任一轴相切/分离 ⇒ 不重叠 ⇒ 嵌入深度 `0.0`。
· `physics.py` 中 `sweep_aabb` 的 docstring（issue #705 修复后）：`depth_at_dt` =
  「若不拦、`dt` 时刻两箱的嵌入深度，口径同 `aabb_mtv`；不重叠（含相切/三轴非同时
  重叠）⇒ `0.0`；不参与任何决策」。

真值的独立性
------------
本文件所有真值均由**独立算式** `_truth_depth_at_dt` 给出（只读 box dict 的
`center`/`half` 字段与速度、dt，**不调用 `physics` 的任何碰撞函数**），以免把守卫写成
「回读实现」的恒真断言。另在 `test_2` 内附一条**口径一致性**机械核对：对 dt 时刻的
两箱直接调用同节 `aabb_mtv`，读数必须与独立真值一致（证明「与 `aabb_mtv` 同口径」）。

断言组（抽掉修复 ⇒ 哪条红）
--------------------------
    ① 通路①：dt 时刻沿运动轴已分离（横向仍重叠）⇒ `0.0`     ── 旧实现报 1.0
    ② 通路①：三轴同时重叠、各轴逃逸距离不等 ⇒ `min` 逃逸距离 ── 旧实现报横向 1.1
    ③ 通路②：起始已重叠 ⇒ 随 `dt` 变化且 == 各自真值       ── 旧实现恒报 t=0 的 MTV
    ④ 通路②：起始已重叠 + 高速相离（dt 时刻已分离）⇒ `0.0`   ── 旧实现报 0.1
    ⑤ 正对照：对角形态（三轴逃逸距离相等）⇒ == 独立真值
    ⑥ 正对照：dt 时刻三轴全分离 ⇒ `0.0`（且 `hit` 语义不变）

运行（仓根）
------------
    python3 -X utf8 -m pytest tests/test_issue705_depth_at_dt_semantics_guard.py -q
"""
from __future__ import annotations

import pytest

from lingshu.world import physics as P


def _box(c, h):
    return P.aabb_of([tuple(c[i] - h[i] for i in range(3)),
                      tuple(c[i] + h[i] for i in range(3))])


def _truth_depth_at_dt(a, va, b, vb, dt):
    """**独立真值**：dt 时刻两箱的嵌入深度。

    逐轴渗透 `pen_i = (ra_i + rb_i) − |Δc_i(dt)|`；三轴**全部** `pen_i > 0`（严格，
    即「贴着不算重叠」）⇒ 取 `min_i pen_i`（= 同节 `aabb_mtv` 的最小逃逸距离）；
    否则 ⇒ `0.0`。**不调用 physics 的任何碰撞函数**（只读 box dict 的字段）。
    """
    ca = [float(a["center"][i]) for i in range(3)]
    ra = [float(a["half"][i]) for i in range(3)]
    cb = [float(b["center"][i]) for i in range(3)]
    rb = [float(b["half"][i]) for i in range(3)]
    pen = [(ra[i] + rb[i]) - abs((ca[i] + va[i] * dt) - (cb[i] + vb[i] * dt))
           for i in range(3)]
    return (min(pen) if all(p > 0.0 for p in pen) else 0.0), pen


def test_1_path1_motion_axis_separated_at_dt_is_zero():
    """① 通路①：dt 时刻**沿运动轴已分离**（横向仍重叠）⇒ `depth_at_dt == 0.0`。

    A 快 B 慢同向 +x（9 与 2），dt=1.0：起始 x 向分离（通路①），dt 时刻 A 中心 9.0、
    B 中心 7.0，Δx = 2.0 > ra+rb = 1.0 ⇒ **真嵌入 0.0**；横向 pen_y = pen_z = 1.0。
    旧实现取 `max(−gap)` ⇒ 报 **1.0**（横向逃逸距离），与字段名/ docstring 不符。
    """
    a = _box((0.0, 0.0, 0.0), (0.5, 0.5, 0.5))
    b = _box((5.0, 0.0, 0.0), (0.5, 0.5, 0.5))
    va, vb, dt = (9.0, 0.0, 0.0), (2.0, 0.0, 0.0), 1.0
    truth, pen = _truth_depth_at_dt(a, va, b, vb, dt)
    assert pen[0] < 0.0 and pen[1] > 0.0 and pen[2] > 0.0      # 仅横向重叠
    assert truth == 0.0
    r = P.sweep_aabb(a, va, b, vb, dt)
    assert r["hit"] is True                                    # 区间内确实曾重叠（拦得住）
    assert r["depth_at_dt"] == 0.0                             # ★旧实现报 1.0
    assert r["depth_at_dt"] == truth


def test_2_path1_thin_plate_takes_min_escape_distance():
    """② 通路①：dt 时刻三轴同时重叠且各轴逃逸距离**不等** ⇒ 读数 = `min` 逃逸距离。

    薄板 A（half = 0.1/1.0/1.0）沿 +x 以 1 走 dt=1.35，B 为 0.1 的立方停在 1.5：
    dt 时刻 Δx = 0.15 ⇒ pen = (0.05, 1.1, 1.1) ⇒ **真嵌入 = min = 0.05**。
    旧实现取 `max(−gap)` ⇒ 报 **1.1**（横向）。附一条**口径一致性**核对：
    对 dt 时刻的两箱调用同节 `aabb_mtv`，读数必须 == 独立真值。
    """
    a = _box((0.0, 0.0, 0.0), (0.1, 1.0, 1.0))
    b = _box((1.5, 0.0, 0.0), (0.1, 0.1, 0.1))
    va, vb, dt = (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.35
    truth, pen = _truth_depth_at_dt(a, va, b, vb, dt)
    assert all(p > 0.0 for p in pen) and pen[0] < pen[1] == pen[2]
    assert truth == pytest.approx(0.05, rel=1e-9)
    r = P.sweep_aabb(a, va, b, vb, dt)
    assert r["hit"] is True
    assert r["depth_at_dt"] == pytest.approx(truth, rel=1e-9)
    assert r["depth_at_dt"] == pytest.approx(0.05, rel=1e-9)   # ★旧实现报 1.1
    # 口径一致性：dt 时刻两箱的 `aabb_mtv` 深度 == 独立真值（「与同节 aabb_mtv 同口径」）
    ea = _box(tuple(a["center"][i] + va[i] * dt for i in range(3)), a["half"])
    eb = _box(tuple(b["center"][i] + vb[i] * dt for i in range(3)), b["half"])
    mtv = P.aabb_mtv(ea, eb)
    assert mtv is not None and mtv[1] == pytest.approx(truth, rel=1e-9)


def test_3_path2_starting_overlap_tracks_dt():
    """③ 通路②：**起始已重叠** ⇒ `depth_at_dt` 随 `dt` 变化，且各自 == dt 时刻真值。

    A = [0,1]³，B 起始 x ∈ [0.9,1.9]（重叠 0.1）且以 vb = −0.5 逼近 ⇒ 走早退分支。
    dt=0.0 ⇒ 真嵌入 0.1；dt=1.0 ⇒ Δx = 0.4 ⇒ 真嵌入 0.6。两值**必须不同**
    （旧实现直接取 t=0 的 MTV ⇒ 两个 dt 恒报 0.1，与 dt/速度无关）。
    """
    a = _box((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
    b = _box((1.4, 0.5, 0.5), (0.5, 0.5, 0.5))
    va, vb = (0.0, 0.0, 0.0), (-0.5, 0.0, 0.0)
    assert P.aabb_overlap(a, b) is True                        # 起始已重叠 ⇒ 通路②
    r0 = P.sweep_aabb(a, va, b, vb, 0.0)
    r1 = P.sweep_aabb(a, va, b, vb, 1.0)
    assert r0["hit"] is True and r0["toi"] == 0.0
    assert r1["hit"] is True and r1["toi"] == 0.0
    t0 = _truth_depth_at_dt(a, va, b, vb, 0.0)[0]
    t1 = _truth_depth_at_dt(a, va, b, vb, 1.0)[0]
    assert r0["depth_at_dt"] == pytest.approx(t0, rel=1e-9) == pytest.approx(0.1, rel=1e-9)
    assert r1["depth_at_dt"] == pytest.approx(t1, rel=1e-9) == pytest.approx(0.6, rel=1e-9)
    assert r0["depth_at_dt"] != r1["depth_at_dt"]              # ★旧实现两值恒等（均 0.1）


def test_4_path2_starting_overlap_but_separated_at_dt_is_zero():
    """④ 通路②：起始已重叠 + **高速相离**（dt 时刻已分离）⇒ `depth_at_dt == 0.0`。

    同 ③ 的 A/B，但 B 以 vb = (10,0,0) 远离：dt=1.0 时 Δx = 10.9 ≫ 1.0 ⇒ 真嵌入 0.0。
    旧实现取 t=0 的 MTV ⇒ **无嵌入却报 0.1**。
    """
    a = _box((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
    b = _box((1.4, 0.5, 0.5), (0.5, 0.5, 0.5))
    va, vb, dt = (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), 1.0
    truth, pen = _truth_depth_at_dt(a, va, b, vb, dt)
    assert pen[0] < 0.0 and truth == 0.0
    r = P.sweep_aabb(a, va, b, vb, dt)
    assert r["hit"] is True and r["toi"] == 0.0                # 起始重叠 ⇒ hit/toi 语义不变
    assert r["depth"] == pytest.approx(0.1, rel=1e-9)          # 起始 MTV 深度（`depth` 语义不变）
    assert r["depth_at_dt"] == 0.0                             # ★旧实现报 0.1
    assert r["depth_at_dt"] == truth


def test_5_positive_control_diagonal_equal_escape_distances():
    """⑤ 正对照：**对角形态**（三轴逃逸距离相等）⇒ 读数 == 独立真值。

    起始恰好对角相切（A 的 hi == B 的 lo，三轴同时）⇒ 通路①；沿 (1,1,1) 相向，
    dt=1.0 时 Δc = 0.4（各轴同）⇒ pen = 0.6（三轴相等）⇒ 真嵌入 0.6。
    """
    a = _box((0.0, 0.0, 0.0), (0.5, 0.5, 0.5))
    b = _box((1.0, 1.0, 1.0), (0.5, 0.5, 0.5))
    va, vb, dt = (0.6, 0.6, 0.6), (0.0, 0.0, 0.0), 1.0
    truth, pen = _truth_depth_at_dt(a, va, b, vb, dt)
    assert pen[0] == pen[1] == pen[2] and truth == pytest.approx(0.6, rel=1e-12)
    r = P.sweep_aabb(a, va, b, vb, dt)
    assert r["hit"] is True
    assert r["depth_at_dt"] == pytest.approx(truth, rel=1e-12)
    assert r["depth_at_dt"] == pytest.approx(0.6, rel=1e-12)


def test_6_positive_control_all_axes_separated_at_dt_is_zero():
    """⑥ 正对照：dt 时刻**三轴全分离** ⇒ `0.0`（且 `hit` 语义不变）。

    薄板 A（half = 0.05/1.0/1.0）沿 +x 以 1 走；B（0.05 立方）停在 0.3。
      · dt = 1.0 ⇒ Δx = 0.7 ≫ 0.1 ⇒ 真嵌入 0.0（A 已越过 B）；
      · dt = 0.25 ⇒ Δx = 0.05 ⇒ 真嵌入 = min(0.05, 1.05, 1.05) = **0.05**
        （旧实现取 `max(−gap)` ⇒ 报 1.05）。
    两 dt 下 `hit` 均为 True（区间内曾重叠）——`hit`/`toi` 语义不受本件影响。
    """
    a = _box((0.0, 0.0, 0.0), (0.05, 1.0, 1.0))
    b = _box((0.3, 0.0, 0.0), (0.05, 0.05, 0.05))
    va, vb = (1.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    truth_far, pen_far = _truth_depth_at_dt(a, va, b, vb, 1.0)
    assert pen_far[0] < 0.0 and truth_far == 0.0
    r_far = P.sweep_aabb(a, va, b, vb, 1.0)
    assert r_far["hit"] is True and r_far["depth"] == 0.0
    assert r_far["depth_at_dt"] == 0.0
    assert r_far["depth_at_dt"] == truth_far
    truth_near, pen_near = _truth_depth_at_dt(a, va, b, vb, 0.25)
    assert all(p > 0.0 for p in pen_near) and truth_near == pytest.approx(0.05, rel=1e-9)
    r_near = P.sweep_aabb(a, va, b, vb, 0.25)
    assert r_near["hit"] is True                               # 同一输入、更小 dt ⇒ 区间内重叠
    assert r_near["depth_at_dt"] == pytest.approx(truth_near, rel=1e-9)
    assert r_near["depth_at_dt"] == pytest.approx(0.05, rel=1e-9)   # ★旧实现报 1.05
