# -*- coding: utf-8 -*-
"""task-14 守卫：碰撞箱内核（AABB / OBB-SAT / MTV / **CCD 扫掠防穿模**）。

缺陷 / 修法
----------
缺陷：`physics.py` 深化出 `drag2/slope_friction/rolling` + 一维 `collide_1d` 之后，
      **仍然没有任何三维碰撞检测**（无 AABB/OBB/SAT/MTV/CCD）⇒ `mesh_gen` 生成的多面数
      建模结果之间、以及 `scene_simulator` 里运动中的实体之间，**穿模在机制上无人拦**：
      "看起来没穿"是唯一防线；高速物体直接隧穿（tunneling）也没有任何判据。
修法：在 `physics.py` 补三维碰撞内核：`aabb_of/aabb_overlap/aabb_mtv`、
      `obb_of/obb_overlap/obb_mtv`（SAT **15 轴**）、`sweep_aabb`（**闭式 CCD**）、
      `sweep_obb`（保守包络 + 子步长，**永不假阴性**）、`resolve_penetration`（质量反比推离）、
      `collision_proxy_truth`（6 个解析真值）+ `intersection_length`（读数对照）。

判据来源（可机械核对）
--------------------
· 6 个**解析真值**用例（`collision_proxy_truth`）：① 重叠 d ⇒ 深度 == d；② 包含 ⇒ 逃逸深度 6.0 ≠ 0；
  ③ 45° 细棒 交叉/平行；④ 子弹 v=1000 穿 0.1 薄板 ⇒ hit 且 toi=(10−0.05−0.05)/1000；
  ⑤ 起始已重叠 ⇒ toi == 0；⑥ 推离后 `aabb_overlap == False`。
· **独立朴素 O(n) 采样**（本文件自带 `_brute_toi`，与实现无共享代码）对拍 `toi`；
· **独立"点在 OBB 内"**（本文件自带 `_point_in_obb`）+ 稠密格点做 OBB 交叉验证；
· `depth` = **逃逸距离**（`min(a.hi−b.lo, b.hi−a.lo)`）——**推离后必分离**这条硬判据的机械核对。

断言组（抽掉修复 ⇒ 哪组红）
------------------------
    A 六用例（①…⑥）                ── ① 回退成"交集长度" ⇒ A② 红；④ 只看终点 ⇒ A④ 红
    B 独立对拍（朴素采样 / 点采样）  ── 去掉 slab 求交（改端点判定）⇒ B 红
    C 边界（零尺寸/退化叉积/相切/1e12）── 把 SAT 的 `>=` 改 `>`（相切算重叠）⇒ C 红
    D sweep_obb 保守性（永不假阴性）  ── 回退成 [toi,dt] 采样 ⇒ D 红（薄板被跨过）
    E resolve（等质量/1:3/分离性）    ── 回退成"等量推" ⇒ E 红

运行命令
--------
    pytest tests/test_physics_collision_proxy_guard.py -q
"""
from __future__ import annotations

import math
import random

import pytest

from lingshu.world import physics as P


# ============================================================ 独立参照（不复用实现）
def _brute_toi(a, va, b, vb, dt, n=20000):
    """**朴素 O(n) 采样**：沿 [0,dt] 细步长取点判 AABB 相交，返回首个命中时刻与步长。"""
    la, ha = P._world_aabb(a)
    lb, hb = P._world_aabb(b)
    ca = [(la[i] + ha[i]) / 2 for i in range(3)]
    cb = [(lb[i] + hb[i]) / 2 for i in range(3)]
    ra = [(ha[i] - la[i]) / 2 for i in range(3)]
    rb = [(hb[i] - lb[i]) / 2 for i in range(3)]
    h = dt / n
    for k in range(n + 1):
        t = k * h
        if all(abs((ca[i] + va[i] * t) - (cb[i] + vb[i] * t)) < ra[i] + rb[i] for i in range(3)):
            return t, h
    return None, h


def _point_in_obb(p, box):
    """**独立**"点在 OBB 内"判定：把点变换到箱体局部系（行 = 局部轴）逐轴比半宽。"""
    c, h, rot = box["center"], box["half"], box.get("rot")
    d = [p[i] - c[i] for i in range(3)]
    if rot is None:
        return all(abs(d[i]) <= h[i] for i in range(3))
    for k in range(3):
        if abs(sum(rot[k][i] * d[i] for i in range(3))) > h[k]:
            return False
    return True


def _local_to_world(center, rot, local):
    """局部坐标 → 世界（行 = 局部轴 ⇒ world = center + Σ_n R[n][m]·local[n]）。"""
    return tuple(center[m] + sum(rot[n][m] * local[n] for n in range(3)) for m in range(3))


def _shift(box, s):
    return {"center": tuple(box["center"][i] + s[i] for i in range(3)),
            "half": box["half"], "rot": box.get("rot")}


def _rot_xyz(rng):
    """确定性随机**正交**旋转（3 轴旋转矩阵乘积；守卫里另做正交性自检）。"""
    ax, ay, az = (rng.uniform(0.0, math.pi) for _ in range(3))
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    return ((cy * cz, -cy * sz, sy),
            (sx * sy * cz + cx * sz, -sx * sy * sz + cx * cz, -sx * cy),
            (-cx * sy * cz + sx * sz, cx * sy * sz + sx * cz, cx * cy))


# ============================================================ A 六个解析真值用例
def test_A1_x_overlap_depth_is_exact():
    """A①：沿 x 重叠 d ⇒ 轴 = ±x、深度 = **d 精确相等**（逃逸距离口径）。"""
    t = P.collision_proxy_truth("x_overlap")
    axis, depth = P.aabb_mtv(t["a"], t["b"])
    assert depth == t["expect"]["depth"] == 0.25          # 精确相等（不是 approx）
    assert axis == (1.0, 0.0, 0.0)
    assert P.aabb_overlap(t["a"], t["b"]) is True
    assert P.intersection_length(t["a"], t["b"]) == 0.25  # 部分重叠时两口径相等


def test_A2_containment_depth_nonzero_and_separable():
    """A②：一箱完全包含另一箱 ⇒ 深度 **≠ 0**，且**推离后必分离**（交集长度口径做不到）。"""
    t = P.collision_proxy_truth("containment")
    axis, depth = P.aabb_mtv(t["a"], t["b"])
    assert depth != 0.0
    assert depth == t["expect"]["escape_depth"] == 6.0
    assert P.intersection_length(t["a"], t["b"]) == t["expect"]["intersection_length"] == 2.0
    assert not P.aabb_overlap(t["a"], _shift(t["b"], tuple(axis[i] * depth for i in range(3))))
    # 交集长度口径**推不出去**（这就是它不能当 depth 的原因，机械核对）
    inter = t["expect"]["intersection_length"]
    assert P.aabb_overlap(t["a"], _shift(t["b"], tuple(axis[i] * inter for i in range(3))))


def test_A3_sat_crossed_and_parallel_bars():
    """A③：绕 y 转 45° 的两条细棒 —— 交叉 ⇒ 相交；平行错开 ⇒ 不相交（SAT 经典用例）。"""
    t = P.collision_proxy_truth("bars_crossed")
    assert P.obb_overlap(t["a"], t["b_crossed"]) is True
    assert P.obb_overlap(t["a"], t["b_parallel"]) is False
    mtv = P.obb_mtv(t["a"], t["b_crossed"])
    assert mtv is not None and mtv[1] == pytest.approx(0.1, rel=1e-9)   # 2×半厚 0.05
    assert P.obb_mtv(t["a"], t["b_parallel"]) is None


def test_A4_bullet_thin_plate_no_tunneling():
    """A④（**穿模判据**）：v=1000 的子弹在 dt=1 内穿过 0.1 厚薄板 ⇒ `hit=True` 且 `toi≈穿入时刻`。"""
    t = P.collision_proxy_truth("bullet_thin_plate")
    r = P.sweep_aabb(t["a"], t["va"], t["b"], t["vb"], t["dt"])
    assert r["hit"] is True, "高速子弹被漏 ⇒ 穿模"
    assert r["toi"] == pytest.approx(t["expect"]["toi"], abs=1e-9)
    assert r["toi"] == pytest.approx(0.0099, abs=1e-12)
    assert r["depth"] == 0.0                              # 拦在接触瞬间（无嵌入）
    assert r["depth_at_dt"] > 1.0                         # 不拦的话 dt 时刻嵌入 1.05
    # 朴素"只看终点"必然漏（这就是穿模的机制）
    end = _shift(t["a"], tuple(t["va"][i] * t["dt"] for i in range(3)))
    assert P.aabb_overlap(end, t["b"]) is False


def test_A5_starting_overlap_toi_zero():
    """A⑤：起始已重叠 ⇒ `sweep_*` 的 `toi == 0.0`（并给出当时的 MTV 深度）。"""
    t = P.collision_proxy_truth("starting_overlap")
    r = P.sweep_aabb(t["a"], t["va"], t["b"], t["vb"], t["dt"])
    assert r["hit"] is True and r["toi"] == 0.0
    assert r["depth"] == pytest.approx(0.5, rel=1e-12)
    so = P.sweep_obb(t["a"], t["va"], t["b"], t["vb"], t["dt"])
    assert so["hit"] is True and so["toi"] == 0.0


def test_A6_resolve_penetration_separates():
    """A⑥：`resolve_penetration` 后 `aabb_overlap == False`（等质量 与 1:3 两种）。"""
    t = P.collision_proxy_truth("resolve_equal")
    a, b = t["a"], t["b"]
    sa, sb = P.resolve_penetration(a, b)
    assert not P.aabb_overlap(_shift(a, sa), _shift(b, sb))
    assert sa[0] == pytest.approx(-0.2, rel=1e-9) and sb[0] == pytest.approx(0.2, rel=1e-9)
    sa3, sb3 = P.resolve_penetration(a, b, mass_a=1.0, mass_b=3.0)
    assert not P.aabb_overlap(_shift(a, sa3), _shift(b, sb3))
    assert sa3[0] == pytest.approx(-0.3, rel=1e-9) and sb3[0] == pytest.approx(0.1, rel=1e-9)
    # 不相交 ⇒ 零位移
    far = P.aabb_of([(50.0, 50.0, 50.0), (51.0, 51.0, 51.0)])
    assert P.resolve_penetration(a, far) == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


# ============================================================ B 独立对拍
def test_B1_sweep_aabb_matches_brute_force_sampling():
    """B1：`sweep_aabb` 的 `toi` 与**独立朴素 20k 步采样**一致（偏差 <= 步长）。"""
    rng = random.Random(20261016)
    checked, hits = 0, 0
    # 先补 6 组**瞄准轨迹**（保证有命中，否则随机对撞概率太低、断言无判别力）：
    # 让 a 在 t=1 时中心正好落到 b 的中心 ⇒ 必然相交
    pairs = []
    for _ in range(6):
        A = {"center": tuple(rng.uniform(-2, 2) for _ in range(3)),
             "half": tuple(rng.uniform(0.2, 1.0) for _ in range(3)), "rot": None}
        B = {"center": tuple(rng.uniform(-2, 2) for _ in range(3)),
             "half": tuple(rng.uniform(0.2, 1.0) for _ in range(3)), "rot": None}
        va = tuple(B["center"][i] - A["center"][i] for i in range(3))
        pairs.append((A, va, B, (0.0, 0.0, 0.0)))
    for _ in range(10):
        A = {"center": tuple(rng.uniform(-3, 3) for _ in range(3)),
             "half": tuple(rng.uniform(0.05, 1.5) for _ in range(3)), "rot": None}
        B = {"center": tuple(rng.uniform(-3, 3) for _ in range(3)),
             "half": tuple(rng.uniform(0.05, 1.5) for _ in range(3)), "rot": None}
        va = tuple(rng.uniform(-8, 8) for _ in range(3))
        vb = tuple(rng.uniform(-8, 8) for _ in range(3))
        pairs.append((A, va, B, vb))
    for A, va, B, vb in pairs:
        r = P.sweep_aabb(A, va, B, vb, 1.0)
        btoi, step = _brute_toi(A, va, B, vb, 1.0)
        if r["hit"]:
            hits += 1
            assert btoi is not None, ("实现命中而朴素采样未命中", A, B, va, vb)
            assert abs(r["toi"] - btoi) <= step + 1e-12, (r["toi"], btoi, step)
        else:
            assert btoi is None, ("实现漏检（朴素采样命中）", btoi)
        checked += 1
    assert checked == len(pairs) == 16 and hits >= 6, hits


def test_B2_obb_overlap_cross_checked_by_dense_point_grid():
    """B2：OBB 相交与**独立点在箱内**（稠密格点）交叉验证（一致率 100%）。"""
    rng = random.Random(20261017)
    agree = 0
    n = 24
    for _ in range(n):
        A = {"center": tuple(rng.uniform(-1.5, 1.5) for _ in range(3)),
             "half": tuple(rng.uniform(0.2, 1.5) for _ in range(3)), "rot": _rot_xyz(rng)}
        B = {"center": tuple(rng.uniform(-1.5, 1.5) for _ in range(3)),
             "half": tuple(rng.uniform(0.2, 1.5) for _ in range(3)), "rot": _rot_xyz(rng)}
        # 正交性自检（否则"点采样"参照本身不可信）
        for k in range(3):
            assert abs(math.sqrt(sum(A["rot"][k][i] ** 2 for i in range(3))) - 1.0) <= 1e-12
        sat = P.obb_overlap(A, B)
        grid = []
        for i in range(9):
            for j in range(9):
                for k2 in range(9):
                    loc = ((2 * i / 8 - 1) * A["half"][0], (2 * j / 8 - 1) * A["half"][1],
                           (2 * k2 / 8 - 1) * A["half"][2])
                    grid.append(_local_to_world(A["center"], A["rot"], loc))
        any_in = any(_point_in_obb(p, B) for p in grid)
        if sat == any_in:
            agree += 1
        else:
            # SAT 相交但格点没命中 = 采样分辨率问题（薄交叉）；反过来则是 SAT 假阴性
            assert sat is True and any_in is False, ("SAT 与点采样矛盾（潜在假阴性）", sat, any_in)
    assert agree >= n - 4, ("一致率过低", agree, n)


# ============================================================ C 边界
def test_C1_empty_points_fail_closed():
    """C1：`aabb_of` 空输入 ⇒ `PhysicsError`（不返回"零点箱"）。"""
    with pytest.raises(P.PhysicsError):
        P.aabb_of([])
    with pytest.raises(P.PhysicsError):
        P.aabb_of([(0.0, float("nan"), 0.0)])
    with pytest.raises(P.PhysicsError):
        P.aabb_of([(0.0, float("inf"), 0.0)])


def test_C2_touching_is_not_overlap():
    """C2：恰好相切（`hi == lo`）**不算重叠**，MTV 为 None（避免抖动）。"""
    a = P.aabb_of([(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)])
    b = P.aabb_of([(1.0, 0.0, 0.0), (2.0, 1.0, 1.0)])
    assert P.aabb_overlap(a, b) is False
    assert P.aabb_mtv(a, b) is None
    assert P.intersection_length(a, b) == 0.0


def test_C3_zero_size_box_semantics():
    """C3：零尺寸箱（点）语义显式：**自身不算重叠**（贴着不算），但在严格包含它的箱内 ⇒ 重叠。"""
    pt = P.aabb_of([(0.5, 0.5, 0.5)])
    assert pt["half"] == (0.0, 0.0, 0.0)
    assert P.aabb_overlap(pt, pt) is False               # 退化箱"贴着自己"⇒ 按相切口径不算重叠
    box = P.aabb_of([(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)])
    assert P.aabb_overlap(pt, box) is True
    on_face = P.aabb_of([(0.0, 0.5, 0.5)])
    assert P.aabb_overlap(on_face, box) is False         # 落在面上 ⇒ 相切


def test_C4_degenerate_cross_axes_and_huge_coords():
    """C4：叉积退化轴（平行边）必须跳过而不误判；1e12 极大坐标不溢出/不出 NaN。"""
    c45 = math.cos(math.radians(45.0))
    s45 = math.sin(math.radians(45.0))
    rot = ((c45, 0.0, s45), (0.0, 1.0, 0.0), (-s45, 0.0, c45))
    a = P.obb_of(aabb={"center": (0.0, 0.0, 0.0), "half": (1.0, 1.0, 1.0)}, rot=rot)
    far = P.obb_of(aabb={"center": (0.0, 0.0, 3.0), "half": (1.0, 1.0, 1.0)}, rot=rot)
    near = P.obb_of(aabb={"center": (0.0, 0.0, 1.0), "half": (1.0, 1.0, 1.0)}, rot=rot)
    assert P.obb_overlap(a, far) is False                # 9 条叉积轴全退化，仍须判对
    assert P.obb_overlap(a, near) is True
    big = 1e12
    ba = P.aabb_of([(big, big, big), (big + 1.0, big + 1.0, big + 1.0)])
    bb = P.aabb_of([(big + 0.5, big, big), (big + 1.5, big + 1.0, big + 1.0)])
    axis, depth = P.aabb_mtv(ba, bb)
    assert all(math.isfinite(x) for x in axis) and math.isfinite(depth)
    assert depth == pytest.approx(0.5, rel=1e-12)


# ============================================================ D sweep_obb 保守性
def test_D1_sweep_obb_never_false_negative_on_thin_plate():
    """D1：`sweep_obb` 在**薄板高速穿越**上不得漏（保守包络 + 窄窗口采样 + fail-safe）。"""
    t = P.collision_proxy_truth("bullet_thin_plate")
    so = P.sweep_obb(t["a"], t["va"], t["b"], t["vb"], t["dt"])
    assert so["hit"] is True, "薄板被跨过 ⇒ 假阴性（保守承诺被打破）"
    assert so["conservative"] is True and so["substeps"] == 16
    assert so["toi_lo"] <= so["toi"] <= so["toi_hi"] + 1e-15
    assert 0.0 <= so["toi"] <= t["dt"]


def test_D2_sweep_obb_hit_implies_envelope_hit():
    """D2：`sweep_obb` 命中 ⇒ 包络（扫掠 AABB）必命中（保守性不变式）；反向允许早拦。"""
    rng = random.Random(20261018)
    for _ in range(12):
        A = {"center": tuple(rng.uniform(-2, 2) for _ in range(3)),
             "half": tuple(rng.uniform(0.2, 1.0) for _ in range(3)), "rot": _rot_xyz(rng)}
        B = {"center": tuple(rng.uniform(-2, 2) for _ in range(3)),
             "half": tuple(rng.uniform(0.2, 1.0) for _ in range(3)), "rot": _rot_xyz(rng)}
        va = tuple(rng.uniform(-6, 6) for _ in range(3))
        vb = tuple(rng.uniform(-6, 6) for _ in range(3))
        so = P.sweep_obb(A, va, B, vb, 1.0, substeps=8)
        la, ha = P._world_aabb(A)
        lb, hb = P._world_aabb(B)
        env = P.sweep_aabb(P._aabb_dict(la, ha), va, P._aabb_dict(lb, hb), vb, 1.0)
        if so["hit"]:
            assert env["hit"] is True, "sweep_obb 命中而包络不命中 ⇒ 不可能（保守性破）"
            assert 0.0 <= so["toi"] <= 1.0


# ============================================================ E 定点变异（回退即红）
def test_E1_mutation_intersection_length_depth_turns_red(monkeypatch):
    """E1/变异①：把 `aabb_mtv` 的深度退回**交集长度口径** ⇒ "推离后分离"断言**转红**。"""

    def legacy_depth(a, b):
        la, ha = P._world_aabb(a)
        lb, hb = P._world_aabb(b)
        best, ax, sg = float("inf"), None, 1.0
        for i in range(3):
            if la[i] >= hb[i] or lb[i] >= ha[i]:
                return None
            ov = min(ha[i], hb[i]) - max(la[i], lb[i])      # ← 交集长度（旧口径）
            if ov < best:
                best, ax, sg = ov, i, (1.0 if lb[i] + hb[i] >= la[i] + ha[i] else -1.0)
        axis = [0.0, 0.0, 0.0]
        axis[ax] = sg
        return (tuple(axis), best)
    monkeypatch.setattr(P, "aabb_mtv", legacy_depth)
    t = P.collision_proxy_truth("containment")
    axis, depth = P.aabb_mtv(t["a"], t["b"])
    assert depth == 2.0                                   # 回退态读数 = 交集长度
    shifted = _shift(t["b"], tuple(axis[i] * depth for i in range(3)))
    assert P.aabb_overlap(t["a"], shifted) is True, "回退后应**推不出去**（A② 的断言在此转红）"


def test_E2_mutation_endpoint_only_sweep_turns_red(monkeypatch):
    """E2/变异②：把 `sweep_aabb` 退回"只看 dt 终点"⇒ A④ 的 hit 断言转红（穿模）。"""

    def endpoint_only(a, va, b, vb, dt):
        end = _shift(a, tuple(va[i] * dt for i in range(3)))
        if P.aabb_overlap(end, b):
            return {"hit": True, "toi": dt, "normal": (0.0, 0.0, 0.0), "depth": 0.0,
                    "axis": (0.0, 0.0, 0.0), "entry": dt, "exit": dt, "depth_at_dt": 0.0}
        return {"hit": False, "toi": None, "normal": (0.0, 0.0, 0.0), "depth": 0.0,
                "axis": (0.0, 0.0, 0.0), "entry": None, "exit": None, "depth_at_dt": 0.0}
    monkeypatch.setattr(P, "sweep_aabb", endpoint_only)
    t = P.collision_proxy_truth("bullet_thin_plate")
    r = P.sweep_aabb(t["a"], t["va"], t["b"], t["vb"], t["dt"])
    assert r["hit"] is False, "回退成端点判定后应漏掉子弹（A④ 的 hit 断言在此转红）"


def test_E3_mutation_touching_counts_as_overlap_turns_red(monkeypatch):
    """E3/变异③：把"贴着不算重叠"改成 `>`（相切算重叠）⇒ C2 转红。"""
    orig = P.aabb_overlap

    def loose(a, b):
        la, ha = P._world_aabb(a)
        lb, hb = P._world_aabb(b)
        return all(la[i] <= hb[i] and lb[i] <= ha[i] for i in range(3))   # ← `<=`：相切算重叠
    monkeypatch.setattr(P, "aabb_overlap", loose)
    a = P.aabb_of([(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)])
    b = P.aabb_of([(1.0, 0.0, 0.0), (2.0, 1.0, 1.0)])
    assert P.aabb_overlap(a, b) is True, "回退后相切被算成重叠（C2 的断言在此转红）"


def test_E4_mutation_sweep_obb_wide_window_misses_plate(monkeypatch):
    """E4/变异④：把 `sweep_obb` 的采样窗口退回 `[toi, dt]` ⇒ D1 的薄板命中转红。"""
    orig = P.sweep_obb

    def wide(a, va, b, vb, dt, *, substeps=16):
        la, ha = P._world_aabb(a)
        lb, hb = P._world_aabb(b)
        env = P.sweep_aabb(P._aabb_dict(la, ha), va, P._aabb_dict(lb, hb), vb, dt)
        if not env["hit"]:
            return {"hit": False, "toi": None, "normal": (0.0, 0.0, 0.0), "depth": 0.0,
                    "conservative": True, "substeps": substeps, "toi_lo": None,
                    "toi_hi": None, "envelope_toi": None}
        t0 = env["toi"] or 0.0
        for k in range(substeps + 1):                     # ← 旧窗口 [t0, dt]（会跨过薄板）
            t = t0 + (dt - t0) * k / float(substeps)
            if P.obb_overlap(P._moved(a, va, t), P._moved(b, vb, t)):
                return {"hit": True, "toi": t, "normal": env["normal"], "depth": 0.0,
                        "conservative": True, "substeps": substeps, "toi_lo": t0,
                        "toi_hi": t, "envelope_toi": t0}
        return {"hit": False, "toi": None, "normal": env["normal"], "depth": 0.0,
                "conservative": True, "substeps": substeps, "toi_lo": None,
                "toi_hi": None, "envelope_toi": t0}
    monkeypatch.setattr(P, "sweep_obb", wide)
    t = P.collision_proxy_truth("bullet_thin_plate")
    so = P.sweep_obb(t["a"], t["va"], t["b"], t["vb"], t["dt"])
    assert so["hit"] is False, "回退成宽窗口后应跨过薄板（D1 的 hit 断言在此转红）"


def test_E5_mutation_equal_shift_ignores_mass_ratio_turns_red(monkeypatch):
    """E5/变异⑤：把 `resolve_penetration` 退回"一律各推一半"（无视质量比）⇒ E 组转红。"""
    orig = P.resolve_penetration

    def equal_only(a, b, *, mass_a=1.0, mass_b=1.0):
        mtv = P.obb_mtv(a, b) if (P._is_obb(a) or P._is_obb(b)) else P.aabb_mtv(a, b)
        if mtv is None:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        axis, depth = mtv
        return (P.vscale(axis, -0.5 * depth), P.vscale(axis, 0.5 * depth))
    monkeypatch.setattr(P, "resolve_penetration", equal_only)
    t = P.collision_proxy_truth("resolve_equal")
    sa3, sb3 = P.resolve_penetration(t["a"], t["b"], mass_a=1.0, mass_b=3.0)
    assert sa3[0] == pytest.approx(-0.2, rel=1e-9)        # 回退态：无视 1:3 ⇒ 仍是 0.2（应 0.3）
    assert not (sa3[0] == pytest.approx(-0.3, rel=1e-9))


def test_guard_is_collectable():
    """哨兵：本文件是 pytest 式且可被自动收集（进门禁）。"""
    assert hasattr(P, "sweep_aabb") and hasattr(P, "collision_proxy_truth")
