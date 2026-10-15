#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_issue662_physics_time_finite_guard.py —— #662 的守卫断言集。

缺陷（改前实测读数，2026-10-15，分支 fix/physics-check-samples-time）
    `lingshu/world/physics.py` 的 `_check_samples` 校验不对称：位置查
    `math.isfinite`，时间只做 `float()` 不查有限性；而严格递增检查
    `ts[i] <= ts[i-1]` 对 NaN 恒 False ⇒ **NaN 时间戳静默通过**：
      · `fit_uniform`/`fit_accelerated` 产出全 NaN 模型（p0/v0/residual_rms 全 nan）；
      · `classify` 随后抛语义无关的 `TypeError: unsupported operand type(s)
        for -: 'NoneType' and 'float'`（`fit_damped` 扫描里 best_k 停留 None，
        `best_k - step` 炸）。
    时间含 +inf 会被递增检查兜住（对照，`inf <= inf` 为 True），唯 NaN 漏。
    与 #459（钳位表达式）不同根因不同位置。

修法
    `_check_samples` 里 `ts` 转 float 后补 `math.isfinite` 检查（raise
    `PhysicsError`，消息含序号与非法值，风格对齐位置错误消息「样本位置非法：%r」）。

分组
    G1 时间含 NaN ⇒ PhysicsError：fit_static / fit_uniform / fit_accelerated
       三入口各断言一次（NaN 居中——正是 `<=` 恒 False 的旁路形态；另补
       NaN 居首的 t_ref 旁路形态），消息含非法值与序号。
    G2 防过修（既有行为零回归）：位置 NaN 仍被拦（「样本位置非法」）；
       时间 +inf 仍被拦（fail-closed 不变——修后拦截面从递增检查前移到
       isfinite，消息仍含「时间」）；正常样本三入口 + classify 全部照常。
    G3 下游恢复：含 NaN 时间的输入不再产出全 NaN 模型——被拦在入口
       （fit_uniform 不再返回 residual_rms=nan 的模型），classify 抛
       PhysicsError 而不是 TypeError。

变异哨兵（两向）
    回退 isfinite 检查（恢复成只 `float()`）⇒ G1/G3 必红（fit_static 不抛、
    fit_uniform 返回全 NaN 模型、classify 抛 TypeError）；恢复 ⇒ 全绿。
    G2 是防过修反哨兵：新检查误伤位置 NaN 拦截或正常样本时它必红。
"""
from __future__ import annotations

import math

import pytest

from lingshu.world import physics as P

NAN = float("nan")
INF = float("inf")

# 正常匀速样本：t=0..3，p=(t,0,0)——三入口与 classify 的零回归对照。
GOOD = [(float(i), (float(i), 0.0, 0.0)) for i in range(4)]
# 时间 NaN 居中（第 1 项）：改前静默通过——NaN 参与递增比较恒 False。
T_NAN = [(0.0, (0.0, 0.0, 0.0)), (NAN, (1.0, 0.0, 0.0)), (2.0, (2.0, 0.0, 0.0))]
# 时间 NaN 居首（第 0 项）：另一旁路形态，还会污染 t_ref=ts[0]。
T_NAN0 = [(NAN, (0.0, 0.0, 0.0)), (1.0, (1.0, 0.0, 0.0)), (2.0, (2.0, 0.0, 0.0))]
# 位置 NaN（第 0 项）：既有 :242 拦截，须零回归。
POS_NAN = [(0.0, (NAN, 0.0, 0.0)), (1.0, (1.0, 0.0, 0.0)), (2.0, (2.0, 0.0, 0.0))]
# 时间 +inf（重复 inf）：改前被递增检查兜住（inf <= inf），修后被 isfinite 前移拦截。
T_INF = [(0.0, (0.0, 0.0, 0.0)), (INF, (1.0, 0.0, 0.0)), (INF, (2.0, 0.0, 0.0))]

ENTRYPOINTS = [
    ("fit_static", P.fit_static),
    ("fit_uniform", P.fit_uniform),
    ("fit_accelerated", P.fit_accelerated),
]


# ------------------------------------------------------------------ G1
@pytest.mark.parametrize("name,fit", ENTRYPOINTS, ids=[n for n, _ in ENTRYPOINTS])
def test_g1_time_nan_raises_at_entrypoint(name, fit):
    """三入口对时间 NaN 必须显式 PhysicsError（消息含非法值 nan 与序号）。"""
    with pytest.raises(P.PhysicsError) as ei:
        fit(T_NAN)
    assert "nan" in str(ei.value)
    assert "第 1 项" in str(ei.value)


def test_g1_time_nan_at_first_sample_also_raises():
    """NaN 居首（t_ref 旁路形态）同样必须在入口被拦。"""
    for _, fit in ENTRYPOINTS:
        with pytest.raises(P.PhysicsError) as ei:
            fit(T_NAN0)
        assert "nan" in str(ei.value)
        assert "第 0 项" in str(ei.value)


# ------------------------------------------------------------------ G2
def test_g2_position_nan_still_blocked():
    """防过修：位置 NaN 的既有拦截（「样本位置非法」）零回归。"""
    for _, fit in ENTRYPOINTS:
        with pytest.raises(P.PhysicsError, match="样本位置非法"):
            fit(POS_NAN)
    with pytest.raises(P.PhysicsError, match="样本位置非法"):
        P.classify(POS_NAN)


def test_g2_time_inf_still_blocked():
    """防过修：时间 +inf 仍被拦（修前递增检查、修后 isfinite，均含「时间」的
    PhysicsError——回退修复本守卫也须保持绿）。"""
    for _, fit in ENTRYPOINTS:
        with pytest.raises(P.PhysicsError, match="时间"):
            fit(T_INF)
    with pytest.raises(P.PhysicsError, match="时间"):
        P.classify(T_INF)


def test_g2_normal_samples_zero_regression():
    """防过修：正常样本三入口 + classify 照常（residual_rms 有限、判别不翻转）。"""
    m = P.fit_static(GOOD)
    assert m["kind"] == "static" and math.isfinite(m["residual_rms"])
    m = P.fit_uniform(GOOD)
    assert m["kind"] == "uniform" and math.isfinite(m["residual_rms"])
    assert all(math.isfinite(x) for x in m["p0"] + m["v0"])
    m = P.fit_accelerated(GOOD)
    assert m["kind"] == "accelerated" and math.isfinite(m["residual_rms"])
    assert P.classify(GOOD)["kind"] == "uniform"


# ------------------------------------------------------------------ G3
def test_g3_no_more_all_nan_model_downstream():
    """下游恢复：改前 fit_uniform(T_NAN) 返回 p0/v0/residual_rms 全 nan 的模型、
    classify(T_NAN) 抛 TypeError（NoneType - float）；修后二者均被拦在入口。"""
    with pytest.raises(P.PhysicsError):
        P.fit_uniform(T_NAN)          # 改前：RETURNED residual_rms=nan
    with pytest.raises(P.PhysicsError):
        P.classify(T_NAN)             # 改前：TypeError: NoneType - float
