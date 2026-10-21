# -*- coding: utf-8 -*-
"""test_issue673_physics_damped_v0_guard —— `fit_damped` 的 v0 符号守卫（#673）

缺陷 / 修法
----------
    · 缺陷（#673，已修）：`fit_damped` 返回的 v0 多乘了一个 `-1.0`
      （旧式 `(p0−p∞)·(−k)·−1.0`），符号翻转 ⇒ 它是六类模型中唯一破坏
      不变量 `model["v0"] == predict_at(model, t_ref)[1]` 的类；下游
      `damped_truth(p0, model["v0"], k)` 假设 v0 是真实初速（`p_inf = p0 + v0/k`），
      喂错符号则恢复出的 p∞ 精确落到对侧（实测 (3,1,−2)→(−3,−1,2)）。
      修法：去掉多余的 `* -1.0`，v0 = −k·(p0−p∞)（指向 p∞），与
      `predict_at` 的 damped 速度口径、`damped_truth` 的 p_inf 口径一致。
      注意：`predict_at` 的 damped 分支不用 `model["v0"]`（位置预测不受影响），
      本文件 physics.py 曾无专属测试（只有包装层 test_physics_predict.py）——
      本守卫补上这个盲区。

运行命令
--------
    cd <仓根>
    python -X utf8 -m pytest tests/test_issue673_physics_damped_v0_guard.py -q

断言组（抽掉修复 ⇒ 哪组红）
--------------------------------
    G1 六类 v0 不变量  static/uniform/accelerated/projectile/damped 各合成一条轨迹，
                       断言 `model["v0"] == predict_at(model, t_ref)[1]`
                       （circular 按 schema 豁免：模型无 'v0' 键，速度由
                       radius/omega/v_up 闭式给出）；damped 另精确断言 v0 指向 p∞。
                       → 加回 `* -1.0` ⇒ G1[damped] 红
    G2 防过修          fit_damped 的位置预测 / (p_inf, k) 参数拟合精度零回归
                       （本修复不得动到拟合路径本身）。
    G3 damped_truth 往返  fit_damped 的 v0 喂 `damped_truth(p0, v0, k)` 必须恢复 p∞。
                       → 加回 `* -1.0` ⇒ G3 红（p∞ 落到对侧）
"""
from __future__ import annotations

import math

import pytest

from lingshu.world import physics as P

# —— 合成参数（与 issue #673 读数一致）：k=1.7，p0=原点，p∞=(3,1,−2)，12 点 ——
K = 1.7
P0 = (0.0, 0.0, 0.0)
P_INF = (3.0, 1.0, -2.0)
_TS = [i * (3.0 / 11.0) for i in range(12)]  # t ∈ [0, 3]，e^{−kt} 覆盖 ~3 个时间常数


def _damped_samples(k=K, p0=P0, p_inf=P_INF, ts=_TS):
    """无噪声阻尼轨迹 p(t) = p∞ + (p0−p∞)·e^{−k·τ}（τ 从首样本起算）。"""
    return [(t, tuple(p_inf[i] + (p0[i] - p_inf[i]) * math.exp(-k * t) for i in range(3)))
            for t in ts]


def _cases():
    """六类各一条合成轨迹 + 对应 fitter（circular 的豁免见 G1 注释）。"""
    g = P.default_g_vec(1)  # lingshu Y-up ⇒ (0, −g, 0)
    return {
        "static": (P.fit_static, [(t, (2.0, 3.0, -1.0)) for t in _TS]),
        "uniform": (P.fit_uniform,
                    [(t, (1.0 + 0.5 * t, 2.0 - 0.25 * t, -1.0 + 0.75 * t)) for t in _TS]),
        "accelerated": (P.fit_accelerated,
                        [(t, (0.5 * t * t, 1.0 + 0.3 * t, -0.2 * t * t)) for t in _TS]),
        "projectile": (P.fit_projectile,
                       [(t, (5.0 * t, 8.0 * t - 0.5 * P.DEFAULT_G * t * t, 1.0)) for t in _TS]),
        "damped": (P.fit_damped, _damped_samples()),
    }


# ============================================================ G1 六类 v0 不变量
@pytest.mark.parametrize("kind", ["static", "uniform", "accelerated", "projectile", "damped"])
def test_G1_v0_equals_predict_at_velocity(kind):
    """不变量：model["v0"] 必须就是 t_ref 时刻的瞬时速度（predict_at 的闭式返回）。

    #673 修前实测：damped 为 False（v0 = (−5.1,−1.7,3.4) 而 predict_at 给
    (5.1,1.7,−3.4)，恰为反号）；其余四类恒 True —— damped 是唯一破裂者。
    circular 按 schema 豁免：fit_circular 的模型字典没有 'v0' 键，速度由
    radius·omega（切向）+ v_up（漂移）闭式给出，不存在该不变量。
    """
    fitter, smp = _cases()[kind]
    m = fitter(smp)
    v_pred = P.predict_at(m, m["t_ref"])[1]
    if kind == "damped":
        # damped 的 v0 与 predict_at 速度由同一组 (p0, p_inf, k) 经同一闭式给出，
        # 修后必须逐位相等（修前恰为逐位相反）。
        assert tuple(m["v0"]) == tuple(v_pred), (
            "damped 的 v0 必须等于 predict_at(t_ref) 的速度（#673）："
            "v0=%r vs %r" % (m["v0"], v_pred))
    else:
        assert all(abs(m["v0"][i] - v_pred[i]) < 1e-12 for i in range(3)), (
            "%s 的 v0 必须等于 predict_at(t_ref) 的速度：v0=%r vs %r" % (kind, m["v0"], v_pred))


def test_G1_damped_v0_points_to_p_inf():
    """damped 专项：真实初速 v0 = −k·(p0−p∞) = k·(p∞−p0)，必须指向 p∞。"""
    m = P.fit_damped(_damped_samples())
    k, p0, p_inf, v0 = m["k"], m["p0"], m["p_inf"], m["v0"]
    # 与拟合自身参数的解析关系（同一组量，逐位可复算）
    assert all(v0[i] == (p0[i] - p_inf[i]) * (-k) for i in range(3))
    # 方向：v0 与 (p∞−p0) 同向且长度 = k·|p∞−p0|
    assert P.vdot(v0, P.vsub(p_inf, p0)) > 0.0, "v0 必须指向 p∞（#673）"
    assert abs(P.vlen(v0) - k * P.vlen(P.vsub(p_inf, p0))) < 1e-9
    # 与合成真值一致（确定性扫描恢复精度 ~1e-13，见 G2）
    for i in range(3):
        assert abs(v0[i] - (-K * (P0[i] - P_INF[i]))) < 1e-9


# ============================================================ G2 防过修（零回归）
def test_G2_fit_accuracy_no_regression():
    """修的是 v0 的**拼装**，不是拟合路径：p_inf / k / p0 / 位置预测精度必须原样。

    无噪声合成轨迹上，确定性一维扫描 + 三分细化的恢复精度实测 ~1e-13；
    这里放 1e-9 余量。若未来本修复牵连拟合路径（过修），此组先红。
    """
    smp = _damped_samples()
    m = P.fit_damped(smp)
    assert abs(m["k"] - K) < 1e-9
    for i in range(3):
        assert abs(m["p_inf"][i] - P_INF[i]) < 1e-9
        assert abs(m["p0"][i] - P0[i]) < 1e-9
    assert m["residual_rms"] < 1e-9
    # 位置预测逐点贴合合成轨迹（predict_at 的 damped 分支不用 model["v0"]，
    # 修前修后都应零差异 —— 这是"符号错误不污染位置预测"的回归锚点）
    for t, p in smp:
        q = P.predict_at(m, t)[0]
        assert P.vlen(P.vsub(q, p)) < 1e-9


def test_G2_classify_still_picks_damped():
    """分类链零回归：合成阻尼史仍判 damped（B 组口径的补充锚点）。"""
    got = P.classify(_damped_samples())
    assert got["kind"] == "damped", got["reason"]


# ============================================================ G3 damped_truth 往返
def test_G3_damped_truth_roundtrip_p_inf():
    """往返恒等：fit_damped 的 (p0, v0, k) 喂 damped_truth 必须恢复 p∞。

    damped_truth 的 p_inf = p0 + v0/k 假设 v0 是真实初速；#673 修前喂的是
    反号 v0，恢复出 (−3,−1,2) —— 精确落到真值对侧。
    """
    m = P.fit_damped(_damped_samples())
    tr = P.damped_truth(m["p0"], m["v0"], m["k"])
    for i in range(3):
        assert abs(tr["p_inf"][i] - P_INF[i]) < 1e-9, (
            "damped_truth 往返未恢复 p∞（#673）：得到 %r，期望 %r"
            % (tr["p_inf"], P_INF))
    # 附带真值口径：总滑行距离 |v0|/k 与首段位移一致
    assert abs(tr["stop_distance"] - P.vlen(m["v0"]) / m["k"]) < 1e-12
