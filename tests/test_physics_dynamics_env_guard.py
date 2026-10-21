# -*- coding: utf-8 -*-
"""#物理深化 A（task-10）守卫：`physics.py` 三类环境动力学 + 碰撞族 + Environment/simulate。

缺陷 / 修法
----------
缺陷：`physics.py` 原本只有**运动学 + 线性黏滞** 6 类（static/uniform/accelerated/
      projectile/damped/circular），**没有任何环境作用**（重力之外无介质、无地形、无摩擦、
      无质量、无碰撞）⇒ 世界模型只能"对观测做运动学拟合"，不能"按环境演化"。
修法：追加三类**有环境作用**的时间模型（`drag2` 二次阻力 / `slope_friction` 斜面库仑摩擦 /
      `rolling` 纯滚动）+ 碰撞**事件族**（`collide_1d`/`collision_truth`，**不进**
      `MODEL_KINDS`）+ 环境上下文 `make_environment` + 演化入口 `simulate` +
      闭式真值 `*_truth` / `environment_truth`。既有 6 类顺序与判定**逐位不变**。

判据来源（可机械核对）
--------------------
· 闭式解 vs **独立 RK4 数值积分**（本文件自带 `_rk4`，不复用模块代码）；
· 碰撞：动量守恒（应为 0 偏差）+ 能量损失闭式 `ΔE = −½·μ_r(1−e²)Δv²`；
· `rolling`：`a = g sinθ/(1+β)`，实心球 ⇒ `a/(g sinθ) = 5/7`；能量分配 `1/(1+β)+β/(1+β)=1`；
· `simulate(env=None)` 与既有 `predict_at` **逐字段一致**（向后兼容硬判据）；
· 锁定态：`held ⇒ s ≡ 0 ∧ v ≡ 0`；`v_s0`（标量）/`v0`（三维）命名口径。

断言组（抽掉修复 ⇒ 哪组红）
------------------------
    A 闭式自洽（RK4 对拍 / t99 / 终端速度收敛）  ── 改错 t99 系数 ⇒ A 红
    B 碰撞（动量守恒 / ΔE 公式 / e=1 / e=0）    ── 改错碰撞公式 ⇒ B 红
    C 拟合→预测闭环（clean + noisy 三模型）     ── 让 fit 的 rms 恒 0 ⇒ C 红
    D 分类 + 混淆矩阵（no_env 与既有 6 类一致） ── 放宽新类接入 ⇒ D 红
    E simulate 向后兼容 + env 对拍 + dt 语义    ── 让 closed 分支走积分 ⇒ E 红
    F 锁定/held 语义 + v_s0/v0 命名口径         ── 回退 held / 忽略 v_s0 ⇒ F 红
    G rolling 慢于滑动 + 5/7                    ── 去掉 /(1+β) ⇒ G 红
    H（复核收尾）① `fit.locked` 单义（匀速滑动 ≠ 锁定）② 积分器步数上限 fail-closed
                                                ── 回退旧 `|a_fit|<=1e-12` / 去掉上限 ⇒ H 红

运行命令
--------
    pytest tests/test_physics_dynamics_env_guard.py -q

回退即红（实测读数见交付回报；本文件每个 `test_*_mutation_*` 都断言"回退态读数"，
未回退时同一断言取反值 ⇒ 判据有判别力）
"""
from __future__ import annotations

import math

import pytest

from lingshu.world import physics as P

G = P.DEFAULT_G


def _rk4(f, y0, t0, t1, dt):
    """独立 RK4（真值对拍用，不复用模块实现）。"""
    n = max(1, int(math.ceil(abs(t1 - t0) / dt)))
    h = (t1 - t0) / n
    y = list(y0)
    t = t0
    for _ in range(n):
        k1 = f(t, y)
        k2 = f(t + h / 2, [y[i] + h / 2 * k1[i] for i in range(len(y))])
        k3 = f(t + h / 2, [y[i] + h / 2 * k2[i] for i in range(len(y))])
        k4 = f(t + h, [y[i] + h * k3[i] for i in range(len(y))])
        y = [y[i] + h / 6 * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]) for i in range(len(y))]
        t += h
    return y


def _samples(fn, n, dt):
    return [(i * dt, tuple(fn(i * dt))) for i in range(n)]


# ============================================================ A 闭式自洽
def test_A1_model_kinds_appended_in_order():
    """A1：新 kind **只追加**在末尾，既有 6 类顺序逐位不变（既有分类行为不变的前提）。"""
    assert P.MODEL_KINDS[:6] == ("static", "uniform", "accelerated", "projectile",
                                 "damped", "circular")
    assert P.MODEL_KINDS[6:] == ("drag2", "slope_friction", "rolling")
    assert "collision" not in P.MODEL_KINDS


def test_A2_drag2_truth_vs_independent_rk4():
    """A2：`drag2_truth` 与独立 RK4 对拍（t=0.5/1/2/5），偏差 <= 1e-9。"""
    m, c, beta = 2.0, 0.5, 0.25
    vt = math.sqrt(G / beta)
    worst = 0.0
    for t in (0.5, 1.0, 2.0, 5.0):
        tr = P.drag2_truth(m, c, t=t)
        s, v = _rk4(lambda _t, y: [y[1], G - beta * y[1] * abs(y[1])], [0.0, 0.0], 0.0, t, 1e-3)
        worst = max(worst, abs(s - tr["y"]), abs(v - tr["v"]))
    assert worst <= 1e-9, worst
    assert P.drag2_truth(m, c)["terminal_v"] == pytest.approx(vt, rel=1e-12)
    # t99 定义：v(t99)/v_t == 0.99
    tr99 = P.drag2_truth(m, c, t=P.drag2_truth(m, c)["t99"])
    assert tr99["v"] / vt == pytest.approx(0.99, abs=1e-9)
    # 终端收敛：5 倍 t99 时几乎到 v_t
    assert P.drag2_truth(m, c, t=5 * tr99["t"])["v"] / vt == pytest.approx(1.0, abs=1e-9)


def test_A3_slope_and_rolling_truth_vs_independent_rk4():
    """A3：`slope_friction_truth` / `rolling_truth` 与独立 RK4 对拍（常数加速度 ⇒ 近逐位）。"""
    st = P.slope_friction_truth(30.0, mu_k=0.2, mu_s=0.6, v_s0=5.0, t=1.0)
    s, v = _rk4(lambda _t, y: [y[1], st["accel"]], [0.0, 5.0], 0.0, 1.0, 1e-4)
    assert abs(s - st["s"]) <= 1e-9 and abs(v - st["v"]) <= 1e-9
    assert st["theta_c_deg"] == pytest.approx(math.degrees(math.atan(0.6)), rel=1e-12)
    rt = P.rolling_truth(30.0, beta=P.SOLID_SPHERE_BETA, t=1.0)
    s2, v2 = _rk4(lambda _t, y: [y[1], rt["accel"]], [0.0, 0.0], 0.0, 1.0, 1e-4)
    assert abs(s2 - rt["s"]) <= 1e-9 and abs(v2 - rt["v"]) <= 1e-9


# ============================================================ B 碰撞
def test_B1_momentum_conserved_and_energy_formula():
    """B1：动量守恒（0 偏差）+ ΔE == −½μ_r(1−e²)Δv²（含 Lead 的 10.1250 那组）。"""
    cases = [(2.0, 3.0, 1.0, -1.0, 0.5), (1.5, 3.0, 1.5, -3.0, 0.5),
             (1.0, 1.0, 3.0, 2.0, 0.3), (2.0, 3.0, 1.0, -1.0, 0.0), (2.0, 3.0, 1.0, -1.0, 1.0)]
    for m1, v1, m2, v2, e in cases:
        d = P.collision_truth(m1, v1, m2, v2, e)
        assert abs(d["momentum_identity_err"]) <= 1e-12, d
        assert d["delta_e"] == pytest.approx(d["predicted_delta_e"], abs=1e-12), d
        assert d["delta_e"] <= 1e-12, d                       # 动能不增
    d = P.collision_truth(1.5, 3.0, 1.5, -3.0, 0.5)
    assert d["energy_loss"] == pytest.approx(10.125, abs=1e-9), d


def test_B2_elastic_and_perfectly_inelastic_ends():
    """B2：`e=1` ⇒ ΔE==0 且相对速度反号；`e=0` ⇒ 共速。"""
    d1 = P.collision_truth(2.0, 3.0, 1.0, -1.0, 1.0)
    assert abs(d1["delta_e"]) <= 1e-12 and d1["elastic"]
    assert d1["rel_v_after"] == pytest.approx(-d1["rel_v_before"], rel=1e-12)
    d0 = P.collision_truth(2.0, 3.0, 1.0, -1.0, 0.0)
    assert d0["perfectly_inelastic"]
    assert d0["v1_after"] == pytest.approx(d0["v2_after"], abs=1e-12)
    with pytest.raises(P.PhysicsError):
        P.collide_1d(1.0, 0.0, 1.0, 0.0, 1.5)                 # e ∉ [0,1] fail-closed


def test_B3_mutation_collision_formula_turns_red(monkeypatch):
    """B3/变异①：碰撞公式去掉一个 `(1+e)` ⇒ 动量守恒立刻破（判据能红）。"""

    def broken(m1, v1, m2, v2, e=1.0):
        m1, m2 = float(m1), float(m2)
        M = m1 + m2
        v1p = ((m1 - e * m2) * v1 + m2 * float(v2)) / M        # ← 少乘 (1+e)
        v2p = ((m2 - e * m1) * v2 + (1.0 + e) * m1 * v1) / M
        p_b = m1 * v1 + m2 * v2
        p_a = m1 * v1p + m2 * v2p
        return v1p, v2p, {"momentum_before": p_b, "momentum_after": p_a,
                          "delta_e": 0.0, "predicted_delta_e": 0.0}
    monkeypatch.setattr(P, "collide_1d", broken)
    _v1, _v2, info = P.collide_1d(2.0, 3.0, 1.0, -1.0, 0.5)
    assert abs(info["momentum_after"] - info["momentum_before"]) > 1e-9, info


# ============================================================ C 拟合→预测闭环
def _drag2_points(m, c, n=41, dt=0.1):
    return [(i * dt, (0.0, -P.drag2_truth(m, c, t=i * dt)["y"], 0.0)) for i in range(n)]


def _slope_points(th, mu_k, v_s0, n=41, dt=0.05):
    u = (math.cos(math.radians(th)), -math.sin(math.radians(th)), 0.0)
    out = []
    for i in range(n):
        t = i * dt
        s = P.slope_friction_truth(th, mu_k=mu_k, v_s0=v_s0, t=t)["s"]
        out.append((t, tuple(u[k] * s for k in range(3))))
    return out


def _roll_points(th, beta, n=41, dt=0.05):
    u = (math.cos(math.radians(th)), -math.sin(math.radians(th)), 0.0)
    out = []
    for i in range(n):
        t = i * dt
        s = P.rolling_truth(th, beta=beta, t=t)["s"]
        out.append((t, tuple(u[k] * s for k in range(3))))
    return out


def test_C1_fit_predict_closed_loop_clean():
    """C1：闭式造样本 ⇒ fit ⇒ predict 与真值一致（clean 应到 1e-6 量级）。"""
    mdl = P.fit_drag2(_drag2_points(2.0, 0.5))
    assert mdl["beta"] == pytest.approx(0.25, rel=1e-4)
    assert mdl["terminal_v"] == pytest.approx(math.sqrt(G / 0.25), rel=1e-5)
    assert mdl["residual_rms"] <= 1e-5, mdl["residual_rms"]
    s_mdl = P.fit_slope_friction(_slope_points(30.0, 0.2, 5.0), slope_deg=30.0)
    assert s_mdl["mu_k"] == pytest.approx(0.2, rel=1e-6), s_mdl["mu_k"]
    assert s_mdl["residual_rms"] <= 1e-6, s_mdl["residual_rms"]
    r_mdl = P.fit_rolling(_roll_points(30.0, P.SOLID_SPHERE_BETA), slope_deg=30.0)
    assert r_mdl["beta"] == pytest.approx(P.SOLID_SPHERE_BETA, rel=1e-6), r_mdl["beta"]


def test_C2_fit_predict_closed_loop_noisy():
    """C2：可控噪声（σ=0.01）下参数仍稳（drag2 β、slope μ_k、rolling β）。"""
    import random

    def noisy(pts, seed):
        rnd = random.Random(seed)
        return [(t, tuple(p[i] + rnd.gauss(0.0, 0.01) for i in range(3))) for t, p in pts]
    mdl = P.fit_drag2(noisy(_drag2_points(2.0, 0.5), 20261015))
    assert mdl["beta"] == pytest.approx(0.25, rel=0.05), mdl["beta"]
    s_mdl = P.fit_slope_friction(noisy(_slope_points(30.0, 0.2, 5.0), 20261016), slope_deg=30.0)
    assert s_mdl["mu_k"] == pytest.approx(0.2, abs=0.02), s_mdl["mu_k"]
    r_mdl = P.fit_rolling(noisy(_roll_points(30.0, P.SOLID_SPHERE_BETA), 20261017), slope_deg=30.0)
    assert r_mdl["beta"] == pytest.approx(P.SOLID_SPHERE_BETA, abs=0.05), r_mdl["beta"]


def test_C3_fail_closed_without_slope():
    """C3：坡度未知时 `fit_slope_friction`/`fit_rolling` 必须抛错（不猜、不静默降级）。"""
    pts = _slope_points(30.0, 0.2, 5.0)
    with pytest.raises(P.PhysicsError):
        P.fit_slope_friction(pts)
    with pytest.raises(P.PhysicsError):
        P.fit_rolling(_roll_points(30.0, P.SOLID_SPHERE_BETA))


def test_C4_mutation_zero_rms_turns_red(monkeypatch):
    """C4/变异②：让 `_fit_rms` 恒返回 0 ⇒ C1 的残差断言立刻失去判别力（能红）。"""
    monkeypatch.setattr(P, "_fit_rms", lambda model, samples: 0.0)
    mdl = P.fit_slope_friction(_slope_points(30.0, 0.2, 5.0), slope_deg=30.0)
    assert mdl["residual_rms"] == 0.0
    assert not (mdl["residual_rms"] <= 1e-6 and mdl["residual_rms"] > 0.0)   # 与 C1 的真读数不同


# ============================================================ D 分类 + 混淆矩阵
def _gens():
    th = 30.0
    u = (math.cos(math.radians(th)), -math.sin(math.radians(th)), 0.0)
    return {
        "static": lambda t: (1.0, 2.0, 3.0),
        "uniform": lambda t: (0.5 + 1.3 * t, 2.0 - 0.4 * t, 3.0 + 0.2 * t),
        "accelerated": lambda t: (0.2 * t * t + 1.1 * t, 2.0, 0.5 - 0.3 * t * t),
        "projectile": lambda t: (4.0 * t, 1.5 + 8.0 * t - 0.5 * G * t * t, 0.0),
        "damped": lambda t: tuple(P.predict_at(
            {"kind": "damped", "k": 2.4, "p0": (0, 0, 0), "p_inf": (2.0, 0, -1.0),
             "t_ref": 0.0}, t)[0]),
        "circular": lambda t: tuple(P.predict_at(
            {"kind": "circular", "up": 1, "center": (0.3, 0.8, -0.4), "radius": 1.5,
             "omega": 2.2, "theta0": 0.4, "v_up": 0.0, "t_ref": 0.0}, t)[0]),
        "drag2": lambda t: (0.0, -P.drag2_truth(2.0, 0.5, t=t)["y"], 0.0),
        "slope_friction": lambda t: tuple(
            u[k] * P.slope_friction_truth(th, mu_k=0.2, mu_s=0.6, v_s0=5.0, t=t)["s"]
            for k in range(3)),
        "rolling": lambda t: tuple(
            u[k] * P.rolling_truth(th, beta=P.SOLID_SPHERE_BETA, t=t)["s"] for k in range(3)),
    }


def test_D1_existing_six_kinds_unchanged_without_env():
    """D1（向后兼容硬判据）：**不给 env** 时既有 6 类的判定结果与今天一致。"""
    expect = {"static": "static", "uniform": "uniform", "accelerated": "accelerated",
              "projectile": "projectile", "damped": "damped", "circular": "circular"}
    for name, fn in _gens().items():
        if name not in expect:
            continue
        got = P.classify(_samples(fn, 30, 1.0 / 24.0))["kind"]
        assert got == expect[name], (name, got)


def test_D2_confusion_matrix_with_env_context():
    """D2：给对上下文时新类被认出；给错上下文时**不**被误认（混淆矩阵）。"""
    th = 30.0
    env_med = P.make_environment(medium={"drag_c": 0.5, "mass": 2.0})
    env_ter = P.make_environment(terrain={"slope_deg": th, "mu_s": 0.6, "mu_k": 0.2})
    gens = _gens()
    got = {}
    for name, fn in gens.items():
        smp = _samples(fn, 30, 1.0 / 24.0)
        got[name] = (P.classify(smp, env=env_med)["kind"], P.classify(smp, env=env_ter)["kind"])
    assert got["drag2"][0] == "drag2", got["drag2"]        # medium 上下文 ⇒ drag2
    assert got["slope_friction"][1] == "slope_friction", got["slope_friction"]
    assert got["rolling"][1] == "rolling", got["rolling"]
    # 既有类在 env 上下文下**不被新类抢走**
    for name in ("uniform", "accelerated", "projectile", "damped", "circular"):
        assert got[name][0] == name and got[name][1] == name, (name, got[name])


def test_D3_locked_slope_is_reported_as_locked_slope():
    """D3：terrain 锁定 + 观测静止 ⇒ `slope_friction` 且 `locked=True`（比"静止"更强的断言）。"""
    env = P.make_environment(terrain={"slope_deg": 20.0, "mu_s": 0.7, "mu_k": 0.5})
    assert env["terrain"]["locks"] is True
    smp = _samples(lambda t: (1.0, 2.0, 3.0), 30, 1.0 / 24.0)
    got = P.classify(smp, env=env)
    assert got["kind"] == "slope_friction" and got["model"]["locked"] is True, got["reason"]
    assert P.classify(smp)["kind"] == "static"             # 不给 env ⇒ 仍是 static（不变）


def test_D4_mutation_loose_gate_turns_red(monkeypatch):
    """D4/变异③：把新类接入的"1.5 倍近似"放宽成"永远接受" ⇒ 既有类被抢走（D2 能红）。"""
    orig = P.classify

    def loose(samples, gravity=P.DEFAULT_G, up=1, rel_gain=1.6, abs_tol=1e-9,
              gravity_tol=0.10, env=None):
        got = orig(samples, gravity=gravity, up=up, rel_gain=rel_gain, abs_tol=abs_tol,
                   gravity_tol=gravity_tol, env=env)
        if env is not None and env.get("medium") is not None:
            try:
                got = dict(got)
                got["kind"] = "drag2"
            except Exception:                                   # noqa: BLE001
                pass
        return got
    monkeypatch.setattr(P, "classify", loose)
    env_med = P.make_environment(medium={"drag_c": 0.5, "mass": 2.0})
    got = P.classify(_samples(_gens()["uniform"], 30, 1.0 / 24.0), env=env_med)["kind"]
    assert got == "drag2", "放宽接入后 uniform 应被 drag2 抢走（证明 D2 有判别力）"


# ============================================================ E simulate
def _all_models():
    return {
        "static": {"kind": "static", "p0": (1.0, 2.0, 3.0), "t_ref": 0.0},
        "uniform": {"kind": "uniform", "p0": (0.0, 0.0, 0.0), "v0": (1.0, -0.5, 0.25), "t_ref": 0.0},
        "accelerated": {"kind": "accelerated", "p0": (0.0, 0.0, 0.0), "v0": (1.0, 0.0, 0.0),
                        "a": (0.2, -0.3, 0.1), "t_ref": 0.0},
        "projectile": {"kind": "projectile", "p0": (0.0, 0.0, 0.0), "v0": (2.0, 3.0, 0.0),
                       "g_vec": (0.0, -G, 0.0), "a": (0.0, -G, 0.0), "t_ref": 0.0},
        "damped": {"kind": "damped", "k": 2.4, "p0": (0, 0, 0), "p_inf": (2.0, 0, -1.0),
                   "t_ref": 0.0},
        "circular": {"kind": "circular", "up": 1, "center": (0.3, 0.8, -0.4), "radius": 1.5,
                     "omega": 2.2, "theta0": 0.4, "v_up": 0.0, "t_ref": 0.0},
        "drag2": {"kind": "drag2", "up": 1, "gravity": G, "beta": 0.25,
                  "terminal_v": math.sqrt(G / 0.25), "p0": (0.0, 0.0, 0.0),
                  "v0": (0.0, 0.0, 0.0), "drift": (0.0, 0.0, 0.0), "t_ref": 0.0},
        "slope_friction": {"kind": "slope_friction", "up": 1, "gravity": G, "slope_deg": 30.0,
                           "slope_rad": math.radians(30.0), "mu_k": 0.2, "mu_s": 0.6,
                           "accel": G * (math.sin(math.radians(30.0)) - 0.2 * math.cos(math.radians(30.0))),
                           "incline_dir": (math.cos(math.radians(30.0)), -math.sin(math.radians(30.0)), 0.0),
                           "v_s0": 5.0, "drift": (0.0, 0.0, 0.0), "locked": False,
                           "p0": (0.0, 0.0, 0.0), "v0": (0.0, 0.0, 0.0), "t_ref": 0.0},
        "rolling": {"kind": "rolling", "up": 1, "gravity": G, "slope_deg": 30.0,
                    "slope_rad": math.radians(30.0), "beta": P.SOLID_SPHERE_BETA,
                    "accel": G * math.sin(math.radians(30.0)) / (1.0 + P.SOLID_SPHERE_BETA),
                    "incline_dir": (math.cos(math.radians(30.0)), -math.sin(math.radians(30.0)), 0.0),
                    "v_s0": 0.0, "drift": (0.0, 0.0, 0.0),
                    "p0": (0.0, 0.0, 0.0), "v0": (0.0, 0.0, 0.0), "t_ref": 0.0},
    }


def test_E1_simulate_env_none_matches_predict_at_fieldwise():
    """E1（向后兼容硬判据）：`env=None` ⇒ `simulate` 与既有 `predict_at` **逐字段一致**。"""
    for name, mdl in _all_models().items():
        for t in (0.5, 2.0):
            pos, vel = P.predict_at(mdl, t)
            got = P.simulate(mdl, None, t)
            assert got["pos"] == pos and got["vel"] == vel, (name, t, got, pos, vel)
            assert got["integrated"] is False and got["steps"] == 0, (name, got)


def test_E2_env_scenarios_match_environment_truth():
    """E2：有 medium/terrain 时 `simulate` 与 `environment_truth` 对到紧容差（1e-12）。"""
    med = P.make_environment(medium={"drag_c": 0.5, "mass": 2.0})
    st = {"p0": (0.0, 10.0, 0.0), "v0": (0.0, 0.0, 0.0), "t_ref": 0.0}
    for t in (1.0, 5.0):
        sim = P.simulate(st, med, t)
        tr = P.environment_truth(med, "drag2", y0=0.0, v_s0=0.0, t=t)
        assert sim["kind"] == "drag2" and sim["integrated"] is False
        assert abs(sim["pos"][1] - (10.0 - tr["y"])) <= 1e-12, (t, sim, tr)
        assert abs(sim["vel"][1] - (-tr["v"])) <= 1e-12
    ter = P.make_environment(terrain={"slope_deg": 30.0, "mu_s": 0.6, "mu_k": 0.2})
    st2 = {"p0": (0.0, 0.0, 0.0), "v0": (0.0, 0.0, 0.0), "t_ref": 0.0, "v_s0": 5.0}
    mdl = P.environment_model(ter, "slope_friction", v_s0=5.0)
    for t in (0.2, 0.5):
        sim = P.simulate(st2, ter, t)
        tr = P.environment_truth(ter, "slope_friction", v_s0=5.0, t=t)
        p_at, _v = P.predict_at(mdl, t)
        assert abs(sim["pos"][0] - tr["disp"][0]) <= 1e-12
        assert abs(p_at[0] - tr["disp"][0]) <= 1e-12, (t, p_at, tr["disp"])   # 口径一致（三维位移）


def test_E3_no_closed_form_requires_explicit_dt():
    """E3：闭式不可得（横向速度≠0）⇒ 缺 `dt` 必须抛错；给了 `dt` 则 `integrated=True`。"""
    med = P.make_environment(medium={"drag_c": 0.5, "mass": 2.0})
    st = {"p0": (0.0, 0.0, 0.0), "v0": (1.0, 0.0, 0.0), "t_ref": 0.0}
    with pytest.raises(P.PhysicsError):
        P.simulate(st, med, 1.0)
    got = P.simulate(st, med, 1.0, dt=1e-4)
    assert got["integrated"] is True and got["steps"] == 10000, got


def test_E4_mutation_forced_integration_turns_red(monkeypatch):
    """E4/变异④：把 `simulate` 的闭式分支强制走积分 ⇒ E1 的逐字段一致立刻破（能红）。"""
    orig = P.simulate

    def forced(state, env, t, *, dt=None):
        out = orig(state, env, t, dt=(dt if dt is not None else 1e-3))
        out = dict(out)
        if out["integrated"] is False:
            out["integrated"] = True                     # 谎报：闭式也标成积分
        return out
    monkeypatch.setattr(P, "simulate", forced)
    got = P.simulate(_all_models()["projectile"], None, 1.0)
    assert got["integrated"] is True, "回退态应报 integrated=True（证明 E1 的断言有判别力）"


# ============================================================ F 锁定 / 命名口径
def test_F1_held_state_has_zero_displacement_and_zero_velocity():
    """F1（#②）：锁定态（`locks` 且 `v_s0<=0`）⇒ `held=True`、`s ≡ 0`、`v ≡ 0`（truth 与 model 都对齐）。"""
    env = P.make_environment(terrain={"slope_deg": 25.0, "mu_s": 0.5, "mu_k": 0.3})
    assert env["terrain"]["locks"] is True and math.tan(math.radians(25.0)) < 0.5
    tr = P.environment_truth(env, "slope_friction", t=0.5)
    assert tr["held"] is True and tr["motion_state"] == "held"
    assert tr["s"] == 0.0 and tr["v"] == 0.0, tr
    assert tr["stop_dist"] == 0.0 and tr["stop_time"] == 0.0
    mdl = P.environment_model(env, "slope_friction")
    assert mdl["locked"] is True
    p, v = P.predict_at(mdl, 0.5)
    assert p == mdl["p0"] and v == (0.0, 0.0, 0.0), (p, v)
    sim = P.simulate({"p0": (0.0, 0.0, 0.0), "v0": (0.0, 0.0, 0.0)}, env, 0.5)
    assert sim["pos"] == (0.0, 0.0, 0.0) and sim["vel"] == (0.0, 0.0, 0.0), sim


def test_F2_held_state_but_already_moving_is_kinetic():
    """F2（#② 边界，如实标注）：`locks=True` 但 `v_s0>0` ⇒ 物体已在滑 ⇒ 走**动摩擦**
    （`held=False`、`motion_state="sliding"`）——静摩擦管不住已在运动的物体，读数显式给出。"""
    env = P.make_environment(terrain={"slope_deg": 25.0, "mu_s": 0.5, "mu_k": 0.3})
    tr = P.environment_truth(env, "slope_friction", v_s0=4.0, t=0.5)
    assert tr["held"] is False and tr["motion_state"] == "sliding", tr
    a = tr["accel"]
    assert tr["s"] == pytest.approx(4.0 * 0.5 + 0.5 * a * 0.25, rel=1e-12)
    assert tr["stops"] is False and tr["stop_dist"] is None
    # 同 state 下 model 与 truth **口径一致**（三维位移逐分量相等）
    mdl = P.environment_model(env, "slope_friction", v_s0=4.0)
    p, _v = P.predict_at(mdl, 0.5)
    assert p == pytest.approx(tr["disp"], abs=1e-12), (p, tr["disp"])


def test_F3_naming_v_s0_scalar_v0_vector():
    """F3（#①）：`v_s0` 恒为**标量**（沿坡）、`v0` 恒为**三维向量**；给错一律 `PhysicsError`。"""
    assert P.slope_friction_truth(25.0, v_s0=4.0)["v_s0"] == 4.0
    assert P.slope_friction_truth(25.0, v0=4.0)["v_s0"] == 4.0          # 数值别名
    assert P.rolling_truth(25.0, v_s0=4.0)["v_s0"] == 4.0
    with pytest.raises(P.PhysicsError):
        P.slope_friction_truth(25.0, v_s0=(4.0, 0.0, 0.0))              # 序列给标量位
    with pytest.raises(P.PhysicsError):
        P.slope_friction_truth(25.0, v0=(4.0, 0.0, 0.0))                # 序列给 v0（truth 无三维位）
    env = P.make_environment(terrain={"slope_deg": 25.0, "mu_s": 0.5, "mu_k": 0.3})
    mdl = P.environment_model(env, "slope_friction", v_s0=4.0)           # 不再被静默忽略
    assert mdl["locked"] is False and mdl["v_s0"] == pytest.approx(4.0, rel=1e-12)
    with pytest.raises(P.PhysicsError):
        P.environment_model(env, "slope_friction", v_s0=4.0, v0=(4.0, 0.0, 0.0))


def test_F4_mutation_ignoring_v_s0_turns_red(monkeypatch):
    """F4/变异⑤：把 `_slide_state_v0` 退回"只读 v0"（旧 bug：静默忽略 v_s0）⇒ F3 能红。"""
    def old_bug(env_n, kw):
        return P._vec3(kw.get("v0", (0.0, 0.0, 0.0)), "v0")
    monkeypatch.setattr(P, "_slide_state_v0", old_bug)
    env = P.make_environment(terrain={"slope_deg": 25.0, "mu_s": 0.5, "mu_k": 0.3})
    mdl = P.environment_model(env, "slope_friction", v_s0=4.0)
    assert mdl["v_s0"] == 0.0, "回退后 v_s0 被忽略（应为 0）⇒ F3 的 v_s0 断言会红"
    assert mdl["locked"] is True, "回退后模型被当成静止 ⇒ 与 environment_truth 漂移"


def test_F5_mutation_held_always_false_turns_red(monkeypatch):
    """F5/变异⑥：把 `held` 恒置 False（回退锁定语义）⇒ F1 的 `s≡0` 断言能红。"""
    orig = P.slope_friction_truth

    def no_held(*a, **kw):
        out = dict(orig(*a, **kw))
        out["held"] = False
        if "s" in out:
            out["s"] = out["v_s0"] * out["t"] + 0.5 * out["accel"] * out["t"] ** 2
        return out
    monkeypatch.setattr(P, "slope_friction_truth", no_held)
    env = P.make_environment(terrain={"slope_deg": 25.0, "mu_s": 0.5, "mu_k": 0.3})
    tr = P.environment_truth(env, "slope_friction", t=0.5)
    assert tr["held"] is False and tr["s"] != 0.0, "回退后锁定态仍给出非零位移（F1 会红）"


# ============================================================ G rolling 慢于滑动 + 5/7
def test_G1_rolling_slower_than_sliding_and_energy_split():
    """G1：`a_roll = g sinθ/(1+β)`；实心球 ⇒ `a/(g sinθ) = 5/7`；能量分配和为 1。"""
    rt = P.rolling_truth(30.0, beta=P.SOLID_SPHERE_BETA, t=1.0, radius=0.2)
    assert rt["accel"] == pytest.approx(G * math.sin(math.radians(30.0)) * 5.0 / 7.0, rel=1e-12)
    assert rt["accel"] / rt["slide_accel"] == pytest.approx(5.0 / 7.0, rel=1e-12)
    assert rt["slower_than_slide"] is True
    assert rt["trans_fraction"] + rt["rot_fraction"] == pytest.approx(1.0, rel=1e-12)
    assert rt["trans_fraction"] == pytest.approx(1.0 / (1.0 + P.SOLID_SPHERE_BETA), rel=1e-12)
    assert rt["pure_rolling_residual"] <= 1e-12          # v = ω r（纯滚动条件）


def test_G2_mutation_rolling_ignores_inertia_turns_red(monkeypatch):
    """G2/变异⑦：把 `a_roll` 的 `/(1+β)` 去掉（当成滑动）⇒ G1 的 5/7 与"更慢"断言能红。"""
    orig = P.rolling_truth

    def slide_like(*a, **kw):
        out = dict(orig(*a, **kw))
        out["accel"] = out["slide_accel"]                # 回退：无转动惯量
        out["slower_than_slide"] = False
        return out
    monkeypatch.setattr(P, "rolling_truth", slide_like)
    rt = P.rolling_truth(30.0, beta=P.SOLID_SPHERE_BETA)
    assert rt["accel"] == rt["slide_accel"] and rt["slower_than_slide"] is False
    assert rt["accel"] / rt["slide_accel"] != pytest.approx(5.0 / 7.0, rel=1e-9)



# --------------------------------------------------------------------------- #
# H 组（第三方复核收尾）：`locked` 单义 + 积分器步数上限
# --------------------------------------------------------------------------- #
def _uniform_slide_samples(theta_deg=20.0, v_s0=1.5, n=41, dt=0.05):
    """**匀速滑动**样本：μ_k = tanθ ⇒ a = 0（复核探针的靶）。"""
    mu = math.tan(math.radians(theta_deg))
    u = (math.cos(math.radians(theta_deg)), -math.sin(math.radians(theta_deg)), 0.0)
    out = []
    for i in range(n):
        t = i * dt
        s = P.slope_friction_truth(theta_deg, mu_k=mu, mu_s=0.6, v_s0=v_s0, t=t)["s"]
        out.append((t, tuple(u[k] * s for k in range(3))))
    return out, mu


def test_H1_uniform_slide_is_not_reported_as_locked():
    """H1（复核 ②）：`locked` 只有**一个**含义 = 静摩擦锁定（`locks ∧ v_s0<=0`）。

    匀速滑动（μ_k=tanθ ⇒ a≈0）**不是**锁定 —— 旧式 `|a_fit|<=1e-12` 会把它标成
    `locked=True`，而 `slope_friction_truth` 说 `held=False / motion_state="sliding"` ⇒ 双语义冲突。
    拟合出的"零加速度"另立 `fitted_zero_accel`（诊断位，不影响预测）。
    """
    smp, mu = _uniform_slide_samples()
    ter = P.make_environment(terrain={"slope_deg": 20.0, "mu_s": 0.6, "mu_k": mu})
    fit = P.fit_slope_friction(smp, env=ter)
    assert abs(fit["accel"]) <= 1e-9, fit["accel"]                 # 确实匀速
    assert fit["fitted_zero_accel"] is True, fit                    # 旧含义改到诊断位
    assert fit["locked"] is False, "匀速滑动被标成 locked ⇒ 与 truth.held 口径冲突"
    tr = P.slope_friction_truth(20.0, mu_k=mu, mu_s=0.6, v_s0=1.5)
    assert tr["held"] is False and tr["motion_state"] == "sliding"
    # 真正锁定（静摩擦托住、无初速）才允许 locked=True
    still = [(i * 0.05, (1.0, 2.0, 3.0)) for i in range(41)]
    locked_fit = P.fit_slope_friction(
        still, env=P.make_environment(terrain={"slope_deg": 20.0, "mu_s": 0.7, "mu_k": 0.5}))
    assert locked_fit["locked"] is True and locked_fit["fitted_zero_accel"] is True


def test_H2_mutation_old_locked_rule_turns_red(monkeypatch):
    """H2/变异①：把 `locked` 退回旧式 `|a_fit| <= 1e-12` ⇒ H1 的断言**转红**。"""
    orig = P.fit_slope_friction

    def legacy_locked(*a, **kw):
        out = dict(orig(*a, **kw))
        out["locked"] = bool(abs(out["accel"]) <= 1e-12)           # ← 旧口径
        return out
    monkeypatch.setattr(P, "fit_slope_friction", legacy_locked)
    smp, mu = _uniform_slide_samples()
    fit = P.fit_slope_friction(smp, env=P.make_environment(
        terrain={"slope_deg": 20.0, "mu_s": 0.6, "mu_k": mu}))
    assert fit["locked"] is True, "回退态应把匀速滑动标成 locked（H1 在此转红）"


def test_H3_integration_step_cap_fails_closed():
    """H3（复核 ③）：定步长积分**步数超限 ⇒ 显式抛错**（不静默截断、不挂住进程）。"""
    med = P.make_environment(medium={"drag_c": 0.5, "mass": 2.0})
    st = {"p0": (0.0, 0.0, 0.0), "v0": (1.0, 0.0, 0.0), "t_ref": 0.0}   # 横向≠0 ⇒ 必须积分
    cap = P.MAX_INTEGRATION_STEPS
    assert isinstance(cap, int) and cap > 0
    with pytest.raises(P.PhysicsError) as ei:
        P.simulate(st, med, 1.0, dt=1.0 / (cap + 10))
    assert "步数超限" in str(ei.value) and str(cap) in str(ei.value)
    # 上限之内照常工作（不误伤）
    got = P.simulate(st, med, 1.0, dt=5e-3)
    assert got["integrated"] is True and got["steps"] == 200, got["steps"]


def test_H4_mutation_no_step_cap_turns_red(monkeypatch):
    """H4/变异②：把上限抬到不可能触发的值（=去掉上限）⇒ H3 的 `raises` 断言**转红**。"""
    monkeypatch.setattr(P, "MAX_INTEGRATION_STEPS", 10 ** 12)
    med = P.make_environment(medium={"drag_c": 0.5, "mass": 2.0})
    st = {"p0": (0.0, 0.0, 0.0), "v0": (1.0, 0.0, 0.0), "t_ref": 0.0}
    got = P.simulate(st, med, 1.0, dt=1.0 / (200000 + 10))          # 20 万步，快（~0.5s）
    assert got["integrated"] is True and got["steps"] == 200010, got["steps"]
    # 若 H3 的 `pytest.raises` 在本态下执行 ⇒ 无异常 ⇒ 断言转红（这里显式自证）
    raised = False
    try:
        P.simulate(st, med, 1.0, dt=1.0 / (200000 + 10))
    except P.PhysicsError:
        raised = True
    assert raised is False, "去掉上限后不该抛错（H3 的 raises 在此转红）"



def test_guard_is_collectable():
    """哨兵：本文件是 pytest 式且可被自动收集（进门禁）。"""
    assert P.MODEL_KINDS[-3:] == ("drag2", "slope_friction", "rolling")
