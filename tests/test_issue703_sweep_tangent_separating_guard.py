# -*- coding: utf-8 -*-
"""issue #703 守卫：`sweep_aabb` 在「t=0 恰好相切且随后相离/切向滑移」时不得报 hit。

缺陷 / 修法
----------
缺陷（两条通路，均来自 PR #697 的碰撞内核）：
  · 通路①：两箱在某轴**恰好相切**（间隙 0）且相对速度**相离** ⇒ 闭式 slab 求交解出
    `t_entry = -1.0`、`t_exit = -0.0`（重叠窗口整体落在过去），但收尾判据用严格
    `t_exit < 0.0` ⇒ `-0.0 < 0.0` 为 False ⇒ 继续往下走，`toi = max(0.0, t_entry) = 0.0`
    ⇒ 报 `hit=True / toi=0.0`（且 `normal` 指向对方一侧，与「让 a 离开 b」相反）。
  · 通路②：某轴相切但该轴**无相对运动**（`abs(v[i]) <= _EPS`）⇒ 原实现用严格
    `ca[i] < lo or ca[i] > hi` 只排「分离」，把「恒相切」当成「区间内全时段重叠」
    ⇒ 仅 z 相切、沿 x 切向滑移的箱对被报 `hit=True / toi=0.0`
    （实测 `entry=-0.5`、`exit=+0.5`，窗口含未来）。
后果：`scene_simulator._collide_pair` 对 `toi=0` 先把两实体回退到 tick 起点，随后按
「本 tick 实际位移」把速度重算为 0 并重播种 ⇒ 两实体**永久冻在接触点**（200 tick 不恢复；
作者守卫场景下「命令离开 3 tick」也不动）。

修法（最小，全在 `physics.sweep_aabb`）：
  · 无相对运动的轴：`ca[i] <= lo or ca[i] >= hi` ⇒ 相切即分离（全节统一口径「贴着不算
    重叠」，与 `aabb_overlap` 的严格 `<` 同源）；
  · 收尾判据：`t_entry >= dt or t_exit <= 0.0` ⇒ 闭区间窗口须在 `(0, dt)` 内有非空内部
    才算真重叠（`t_exit == -0.0` 与 `t_entry == dt` 两个端点相切均不算）。

不适用条件
----------
· 真重叠（`aabb_overlap=True`）与「相切且相向」起步（作者 C4 形态）**不受影响**——
  本件断言它们的读数一字不变（防过修）。
· `sweep_obb` 的保守承诺（包络不命中 ⇒ 不命中）不受影响：它走 `sweep_aabb` 的包络，
  包络更严 ⇒ 只会更保守，不会假阴性。

运行命令
--------
    pytest tests/test_issue703_sweep_tangent_separating_guard.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from lingshu.world import physics as P                      # noqa: E402
from lingshu.world.scene_simulator import SceneSimulator     # noqa: E402


def _box(cx, cy, cz, h=0.5):
    return P.aabb_of([(cx - h, cy - h, cz - h), (cx + h, cy + h, cz + h)])


# ============================================================ ① 通路①：x 相切 + 相离
def test_issue703_x_tangent_separating_no_hit():
    """★①：x 轴恰好相切（A=[-1,0]、B=[0,1]）且相对速度**相离** ⇒ 不得报 hit。

    改前读数（缺陷）：`hit=True, toi=0.0, entry=-1.0, exit=-0.0, normal=(+1,0,0)`。
    """
    a, b = _box(-0.5, 0.0, 0.0), _box(0.5, 0.0, 0.0)
    assert P.aabb_overlap(a, b) is False          # 口径：相切不算重叠
    r = P.sweep_aabb(a, (-2.0, 0.0, 0.0), b, (2.0, 0.0, 0.0), 1.0)
    assert r["hit"] is False, r
    assert r["toi"] is None, r


def test_issue703_axis_symmetric_tangent_separating_no_hit():
    """①（对称面）：y / z / 角（x+z 同时相切）相切 + 相离 ⇒ 同样不得报 hit。"""
    cases = [
        ((0.0, -0.5, 0.0), (0.0, 0.5, 0.0), (0.0, -2.0, 0.0), (0.0, 2.0, 0.0)),   # y 轴
        ((0.0, 0.0, -0.5), (0.0, 0.0, 0.5), (0.0, 0.0, -2.0), (0.0, 0.0, 2.0)),   # z 轴
        ((-0.5, 0.0, -0.5), (0.5, 0.0, 0.5), (-2.0, 0.0, -2.0), (2.0, 0.0, 2.0)),  # 角
    ]
    for pa, pb, va, vb in cases:
        a, b = _box(*pa), _box(*pb)
        assert P.aabb_overlap(a, b) is False
        r = P.sweep_aabb(a, va, b, vb, 1.0)
        assert r["hit"] is False, (pa, pb, va, vb, r)
        assert r["toi"] is None, (pa, pb, va, vb, r)


# ============================================================ ② 通路②：z 相切 + 切向滑移
def test_issue703_z_tangent_tangential_sliding_no_hit():
    """★②：仅 z 相切（z 轴无相对运动）、速度沿 x 切向滑移 ⇒ 不得报 hit。

    改前读数（缺陷）：`hit=True, toi=0.0, entry=-0.5, exit=+0.5, normal=(-1,0,0)`。
    """
    a, b = _box(0.0, 0.0, -0.5), _box(0.0, 0.0, 0.5)
    assert P.aabb_overlap(a, b) is False
    r = P.sweep_aabb(a, (2.0, 0.0, 0.0), b, (0.0, 0.0, 0.0), 1.0)
    assert r["hit"] is False, r
    assert r["toi"] is None, r
    # 相对速度沿 x（两侧同速）⇒ 也相切
    r2 = P.sweep_aabb(a, (2.0, 0.0, 0.0), b, (2.0, 0.0, 0.0), 1.0)
    assert r2["hit"] is False, r2


# ============================================================ ③ 场景层：200 tick 能分离
def _scene(p0a, p0b, va, vb):
    sim = SceneSimulator(size=64, ground_level=1, seed=42)
    sim.create_scene(trees=0, water=False)
    a = sim.add_entity("ball", "wander", pos=p0a)
    b = sim.add_entity("ball", "wander", pos=p0b)
    for eid, p0, v0 in ((a, p0a, va), (b, p0b, vb)):
        sim.entities[eid].attrs["physics"] = "dynamics"
        sim.set_entity_physics(eid, p0=p0, v0=v0, t_ref=0.0)
        sim.set_entity_collision_box(eid, (0.5, 0.5, 0.5))
    return sim, a, b


def test_issue703_scene_tangent_separating_separates_within_200_ticks():
    """★③：t=0 恰好相切、速度相离 ⇒ 200 tick 内必须**分开**（改前冻在接触点）。"""
    for p0a, p0b, va, vb, axis in (
            ((20.0, 1.5, 20.0), (21.0, 1.5, 20.0), (-2.0, 0.0, 0.0), (2.0, 0.0, 0.0), 0),   # x
            ((20.0, 1.5, 20.0), (20.0, 1.5, 21.0), (0.0, 0.0, -2.0), (0.0, 0.0, 2.0), 2),   # z
            ((20.0, 1.5, 20.0), (20.0, 1.5, 21.0), (2.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0)):   # z 相切切向滑移
        sim, a, b = _scene(p0a, p0b, va, vb)
        for _ in range(200):
            sim.step(1)
        sep0 = abs(p0b[axis] - p0a[axis])
        sep1 = abs(sim.entities[b].pos[axis] - sim.entities[a].pos[axis])
        assert sep1 > sep0 + 1.0, (p0a, p0b, va, vb, "200 tick 后仍未分离", sep0, sep1)


def test_issue703_scene_tangent_separating_contact_report_not_hit():
    """③（读数）：相切相离的第一 tick 碰撞读数 `hit=False`（改前 `hit=True/toi=0.0`）。"""
    sim, a, b = _scene((20.0, 1.5, 20.0), (21.0, 1.5, 20.0), (-2.0, 0.0, 0.0), (2.0, 0.0, 0.0))
    sim.step(1)
    rep = sim.collision_report()
    assert rep["n_contacts"] == 1, rep
    assert rep["contacts"][0]["hit"] is False, rep["contacts"]
    assert rep["contacts"][0]["toi"] is None, rep["contacts"]


def test_issue703_scene_command_away_moves():
    """③（续走）：作者守卫场景（HALF=1、A=20、B=40、v=±30）相撞停住后，命令离开必须能动。

    改前：`sep` 恒 2.0、`vA=vB=0`（命令离开 3 tick 也不动）。
    """
    sim = SceneSimulator(size=64, ground_level=1, seed=42)
    sim.create_scene(trees=0, water=False)
    a = sim.add_entity("ball", "wander", pos=(20.0, 1.5, 20.0))
    b = sim.add_entity("ball", "wander", pos=(40.0, 1.5, 20.0))
    for eid, p0, v0 in ((a, (20.0, 1.5, 20.0), (30.0, 0.0, 0.0)),
                        (b, (40.0, 1.5, 20.0), (-30.0, 0.0, 0.0))):
        sim.entities[eid].attrs["physics"] = "dynamics"
        sim.set_entity_physics(eid, p0=p0, v0=v0, t_ref=0.0)
        sim.set_entity_collision_box(eid, (1.0, 1.0, 1.0))
    for _ in range(4):
        sim.step(1)
    assert abs((sim.entities[b].pos[0] - sim.entities[a].pos[0]) - 2.0) < 1e-9   # 停在接触面
    sim.set_entity_physics(a, v0=(-5.0, 0.0, 0.0))
    sim.set_entity_physics(b, v0=(5.0, 0.0, 0.0))
    for _ in range(3):
        sim.step(1)
    sep = sim.entities[b].pos[0] - sim.entities[a].pos[0]
    assert sep > 2.0 + 1.0, ("命令离开后仍冻在接触面", sep)


# ============================================================ ④ 防过修：原读数一字不变
def test_issue703_real_overlap_readings_unchanged():
    """★④（防过修）：真重叠 `A=[0,1]`、`B=[0.5,1.5]` ⇒ `hit=True/toi=0.0/depth=0.5` 不变。"""
    a, b = _box(0.5, 0.0, 0.0), _box(1.0, 0.0, 0.0)     # [0,1] 与 [0.5,1.5]
    assert P.aabb_overlap(a, b) is True
    r = P.sweep_aabb(a, (0.0, 0.0, 0.0), b, (0.0, 0.0, 0.0), 1.0)
    assert r["hit"] is True and r["toi"] == 0.0, r
    assert r["depth"] == pytest.approx(0.5, rel=1e-12), r
    assert r["entry"] == 0.0, r


def test_issue703_true_approach_readings_unchanged():
    """★④（防过修）：真相向命中（间隙 1.0、速度 ±1）⇒ `toi=0.5`、`normal=(-1,0,0)` 不变。"""
    a, b = _box(-0.5, 0.0, 0.0), _box(1.5, 0.0, 0.0)    # 间隙 1.0
    r = P.sweep_aabb(a, (1.0, 0.0, 0.0), b, (-1.0, 0.0, 0.0), 1.0)
    assert r["hit"] is True, r
    assert r["toi"] == pytest.approx(0.5, abs=1e-12), r
    assert r["entry"] == pytest.approx(0.5, abs=1e-12), r
    assert r["exit"] == pytest.approx(1.0, abs=1e-12), r
    assert tuple(r["normal"]) == (-1.0, 0.0, 0.0), r


def test_issue703_tangent_start_approaching_still_hit():
    """★④（防过修，作者 C4 形态）：起始恰好相切且**相向** ⇒ 仍 `hit=True / toi=0.0`。

    这是 PR #697 特意补的漏检点（`:1872` 的 `or axis_i is None`），不得因本件回退。
    """
    def box(cx):
        return P.aabb_of([(cx + dx, 0.5 + dy, 19.0 + dz)
                          for dx in (-1, 1) for dy in (-1, 1) for dz in (-1, 1)])
    r = P.sweep_aabb(box(29.0), (9.0, 0.0, 0.0), box(31.0), (-9.0, 0.0, 0.0), 1.0)
    assert r["hit"] is True and float(r["toi"]) == 0.0, r
    assert tuple(r["normal"]) == (-1.0, 0.0, 0.0), r


def test_issue703_separated_by_epsilon_not_hit():
    """④（判别力）：把 B 平移 1e-9（有极小间隙）⇒ 与相切同为 `hit=False`（量具有判别力）。"""
    a, b = _box(-0.5, 0.0, 0.0), _box(0.5 + 1e-9, 0.0, 0.0)
    assert P.aabb_overlap(a, b) is False
    r = P.sweep_aabb(a, (-2.0, 0.0, 0.0), b, (2.0, 0.0, 0.0), 1.0)
    assert r["hit"] is False and r["toi"] is None, r


# ============================================================ 独立对拍（仓内参照）
def test_issue703_matches_brute_force_reference():
    """★⑤：用仓内独立参照 `_brute_toi`（20k 步朴素采样）对拍本件全部相切用例。

    判据照 `tests/test_physics_collision_proxy_guard.py` 的 B1 原文：实现 hit ⇒ 采样
    必命中且 `|toi − brute| <= step`；实现不 hit ⇒ 采样必不命中。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "guard_physics_ref",
        os.path.join(REPO_ROOT, "tests", "test_physics_collision_proxy_guard.py"))
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)
    cases = [
        (_box(-0.5, 0.0, 0.0), (-2.0, 0.0, 0.0), _box(0.5, 0.0, 0.0), (2.0, 0.0, 0.0)),    # x 相切相离
        (_box(0.0, -0.5, 0.0), (0.0, -2.0, 0.0), _box(0.0, 0.5, 0.0), (0.0, 2.0, 0.0)),    # y
        (_box(0.0, 0.0, -0.5), (0.0, 0.0, -2.0), _box(0.0, 0.0, 0.5), (0.0, 0.0, 2.0)),    # z
        (_box(0.0, 0.0, -0.5), (2.0, 0.0, 0.0), _box(0.0, 0.0, 0.5), (0.0, 0.0, 0.0)),     # 切向滑移
        (_box(-1.5, 0.0, 0.0), (1.0, 0.0, 0.0), _box(1.5, 0.0, 0.0), (-1.0, 0.0, 0.0)),    # 末端相切
        (_box(-0.5, 0.0, 0.0), (1.0, 0.0, 0.0), _box(1.5, 0.0, 0.0), (-1.0, 0.0, 0.0)),    # 真命中
        (_box(29.0, 0.5, 19.0, 1.0), (9.0, 0.0, 0.0),
         _box(31.0, 0.5, 19.0, 1.0), (-9.0, 0.0, 0.0)),                                   # 相切相向
    ]
    for a, va, b, vb in cases:
        r = P.sweep_aabb(a, va, b, vb, 1.0)
        btoi, step = ref._brute_toi(a, va, b, vb, 1.0)
        if r["hit"]:
            assert btoi is not None, ("实现命中而朴素采样未命中", a, b, va, vb)
            assert abs(r["toi"] - btoi) <= step + 1e-12, (r["toi"], btoi, step)
        else:
            assert btoi is None, ("实现漏检（朴素采样命中）", btoi)


def test_guard_is_collectable():
    """哨兵：本文件是 pytest 式且可被自动收集（进门禁）。"""
    assert hasattr(P, "sweep_aabb")
