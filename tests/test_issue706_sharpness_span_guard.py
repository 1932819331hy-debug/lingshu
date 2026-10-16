# -*- coding: utf-8 -*-
"""周期「谷够尖」判据的**邻居跨度**守卫 —— issue #706

缺陷（回归 · PR #470 `5134f8f` 引入 · `sharp_min_ratio` **出厂默认开**）：
  「谷够尖」判据原先写成
      `sharpness = resid(P) / min(resid(P-1), resid(P+1))`，`> 0.5` 即判「平滑假象」并拒绝。
  **判据基数只有相邻两点**，于是对两类**真周期**都失效：
    ① **半整数周期**（峰谷落在采样点之间，如 40.5 / 47.5 帧）：P 与 P±1 对称地「各差半帧」
       ⇒ 比值恰为 1.0 > 0.5 ⇒ 被拒（**无噪声也触发**）。
    ② **整数周期 + 噪声**（σ=6%）：长周期的相邻整数只差一帧，残差本来就接近 resid(P)，
       噪声把 resid(P) 抬离 0 ⇒ 比值 0.53~0.64 > 0.5 ⇒ 被拒。
  被拒后 `smooth` 分支（等加速度外推）接管往复运动，5 步预测误差从 0.075 涨到 2.5~3.1
  （单位圆），**比没有周期层的改前基准 cb3a6ea（0.99~1.27）还差**。

修法（最小改动）：谷宽与周期**同尺度**——真周期在残差曲线上的谷宽 ≈ P/4
  （`|P−真周期| = P/4` 处残差 ≈ 0.707·amp），故邻居取 `P ± max(1, round(P/4))`。
  判据式、阈值、返回体形状一律不动；`P = min_period` 时 `_k = 1`，与旧口径**逐位相同**
  （所以「短周期捡漏」这类 #470 的原目标形态仍被拦：捡漏恰好总是检出 `P = min_period`）。
  另在诊断里补 `sharp_span`（本次实际用的邻居跨度），让判据**可复核**。

判据来源：issue #706（本仓 `FuRongJun-1999/lingshu`）+ 本守卫自测的残差曲线读数。
  真周期侧实测 `sharpness`：半整数 40.5 → 0.053、σ=6% 的 T=40 → 0.088；
  坏形态侧（真 118 / 窗 96）`sharpness` 0.801（`P=4`，`_k=1`，与旧口径同值）。
  两侧余量充足（0.09 vs 阈值 0.5 vs 0.80）。
  误差读数（本守卫口径 = 真值取同一带噪序列；喂 160 帧、统计第 110~159 帧的 5 步误差）：
    缺陷态 T=40.5 → 0.1284（smooth 接管）、T=40 σ=6% → 2.7942；
    修复态两例均 0.0776 / 0.1142，与「判据关」逐位相同。
    （issue 正文的 0.075 是「真值取无噪声理想圆」口径：修复态 0.0737 / 0.0708，
     理论下限 σ·√(π/2) = 0.0752，本机同口径可复现。）

喂法（与 issue 的最小复现一致）：`perceive` 每帧都调，`generate(5)` **只从第 110 帧起**调，
统计第 110~159 帧的 5 步预测误差（这样喂出来的读数与 issue 正文逐位可比）。

断言组（把 `_k` 回退成 `1` ⇒ A/B 组按标注转红；把 `sharp_span` 抽掉 ⇒ F 组红）：
  A **半整数周期不得被判「平滑假象」**（无噪声，`T=40.5`）——缺陷态红
  B **带 6% 噪声的整数周期不得被判「平滑假象」**（`T=40`）——缺陷态红
  C **正对照：#470 的原目标形态仍须被拦**（真 118 / 窗 96 ⇒ `P=4` 捡漏必须拒绝）
    —— 防**过修**；并证明「光靠残差阈值拦不住」（该候选 resid 本身就低于阈值）
  D **正对照：量具有判别力**（同一读数 `sharpness` 在真周期侧 < 0.2、坏形态侧 > 0.5）
  E **正对照：真·平滑与真·随机不得被判周期**（防过修另一侧）
  F 诊断 `sharp_span` 必须等于 `max(1, round(P/4))`；短周期捡漏时必须是 1（与旧口径同）
  G **已知残余不掩饰**：真周期比枚举上限大 1 帧（T=49/上限 48）时新跨度会接受 `P=48`，
    该情形必须带 `clipped=True` + `clip_note`（诚实标记），不钉「接受/拒绝」这个取舍

运行（仓根）：python -X utf8 -m pytest tests/test_issue706_sharpness_span_guard.py -q --no-header
"""
from __future__ import annotations

import math
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.world.world_model import (  # noqa: E402
    UnifiedWorldModel, _PC, period_caliber_status, set_period_caliber)

STAT = 110          # 统计起点（与 issue 最小复现同口径）
HORIZON = 5


def _circ(period, n, sigma=0.0, seed=12345):
    """单位圆轨迹（自写，不引用 tests/ 或 repro/ 的任何实现）。"""
    rng = random.Random(seed)
    out = []
    for t in range(n):
        x = math.cos(2 * math.pi * t / period)
        z = math.sin(2 * math.pi * t / period)
        if sigma > 0.0:
            x += rng.gauss(0.0, sigma)
            z += rng.gauss(0.0, sigma)
        out.append((x, 0.0, z))
    return out


def _smooth_line(n, v0=0.02, a=0.0005):
    """真·平滑序列（等加速直线，非往复）—— 必须仍走 smooth，不得被判周期。"""
    return [(v0 * t + 0.5 * a * t * t, 0.0, 0.0) for t in range(n)]


def _rand_walk(n, seed=99, step=0.05):
    """真·随机噪声（随机游走）—— 必须仍判噪声，不得被判周期。"""
    rng = random.Random(seed)
    p = [0.0, 0.0, 0.0]
    out = []
    for _ in range(n):
        p = [p[0] + rng.gauss(0.0, step), 0.0, p[2] + rng.gauss(0.0, step)]
        out.append(tuple(p))
    return out


def _run(seq, truth=None, horizon=HORIZON, stat=STAT):
    """喂 `seq`，从第 `stat` 帧起每帧取一次 `horizon` 步预测。

    返回 `(err_mean, mode_hist, diag, model)`；`truth` 缺省 = `seq` 自身（用于不量误差的组）。
    """
    truth = seq if truth is None else truth
    m = UnifiedWorldModel(size=24)
    errs, modes = [], {}
    for t, p in enumerate(seq):
        m.perceive([{"eid": "a", "pos": p, "category": "c"}])
        if t >= stat:
            pr = (m.generate(horizon).get("predictions") or {}).get("a") or {}
            modes[pr.get("mode")] = modes.get(pr.get("mode"), 0) + 1
            if pr.get("predicted") is not None and t + horizon < len(truth):
                errs.append(math.dist(tuple(pr["predicted"]), truth[t + horizon]))
    d = (getattr(m, "_periodic_diag", {}) or {}).get("a") or {}
    assert modes, "mode 直方图为空 —— 守卫不得空转成假绿"
    assert d, "周期诊断为空 —— 周期检测根本没跑到"
    err = (sum(errs) / len(errs)) if errs else float("nan")
    return err, modes, d, m


def _restore(cfg):
    set_period_caliber(sharp_min_ratio=cfg["sharp_min_ratio"])


def _with_default_sharp():
    cfg = period_caliber_status()["config"]
    set_period_caliber(sharp_min_ratio=0.5)
    return cfg


def test_a_半整数周期不得被判平滑假象():
    """A：真周期 40.5（峰谷落在采样点之间）**无噪声**也不得被判「平滑假象」。"""
    cfg = _with_default_sharp()
    try:
        seq = _circ(40.5, 160 + HORIZON + 1)
        err, modes, d, _m = _run(seq[:160], seq)
        assert d.get("P") is not None, "半整数周期 40.5 未被检出：%r" % (d,)
        assert d.get("suspect_spurious") is not True, (
            "半整数周期被误判为「平滑假象」（判据基数只有 P±1 时 sharpness 恰为 1.0）：%r" % (d,))
        assert modes.get("periodic"), "半整数真周期应进入 periodic：%r" % (modes,)
        assert err < 0.10, (
            "半整数真周期 5 步误差 %.4f 偏高（判据误杀 ⇒ 退回 smooth 外推）：mode=%r" % (err, modes))
    finally:
        _restore(cfg)


def test_b_带噪声的整数周期不得被判平滑假象():
    """B：真周期 40 + 单位圆每轴 σ=6% 高斯噪声，不得被判「平滑假象」。"""
    cfg = _with_default_sharp()
    try:
        seq = _circ(40.0, 160 + HORIZON + 1, sigma=0.06)
        err, modes, d, _m = _run(seq[:160], seq)
        assert d.get("P") is not None, "带噪声真周期 40 未被检出：%r" % (d,)
        assert d.get("suspect_spurious") is not True, (
            "带 6%% 噪声的真周期被误判为「平滑假象」：%r" % (d,))
        assert modes.get("periodic"), "带噪声真周期应进入 periodic：%r" % (modes,)
        assert err < 0.20, (
            "带噪声真周期 5 步误差 %.4f 偏高（误杀后退回 smooth 外推 ⇒ 2.8）：mode=%r" % (err, modes))
    finally:
        _restore(cfg)


def test_c_正对照_470目标形态仍须被拦():
    """C（防过修）：真周期 118 / 分析窗 96 ⇒ 短周期捡漏（P=4）必须**仍被拒绝**。"""
    cfg = _with_default_sharp()
    try:
        m = UnifiedWorldModel(size=24)
        for p in _circ(118, 96):
            m.perceive([{"eid": "a", "pos": p, "category": "c"}])
            m.generate(1)
        d = (getattr(m, "_periodic_diag", {}) or {}).get("a") or {}
        assert d, "周期诊断为空 —— 周期检测根本没跑到"
        assert d.get("suspect_spurious") is True, (
            "#470 的原目标形态（真 118 / 窗 96 的短周期捡漏）漏过了 —— 修法过松：%r" % (d,))
        assert d.get("reject_reason"), "拒绝必须带人话原因（边界诚实，不静默）"
        g = m.generate(1) or {}
        mode = ((g.get("predictions") or {}).get("a") or {}).get("mode")
        assert mode != "periodic", "被拒的候选不得进入 periodic 分支：mode=%r" % (mode,)
        # 关键对照：**光靠残差阈值拦不住** —— 该候选 resid 本身就低于接受阈值。
        assert d.get("resid") is not None and d["resid"] < _PC["resid_ratio"], (
            "本用例的意义在于「残差够低但谷不尖」：resid=%r 应低于阈值 %r"
            % (d.get("resid"), _PC["resid_ratio"]))
    finally:
        _restore(cfg)


def test_d_正对照_量具有判别力():
    """D（正对照）：同一读数 `sharpness` 必须把真周期侧与坏形态侧**分开**。

    防「量具恒绿/恒红」：若判据被改成恒接受，C 会红；若被改成恒拒绝，A/B 会红；
    本组进一步要求两侧的 `sharpness` 本身真的分离（且两侧 resid 都低于残差阈值，
    免得退化成「在比残差」）。
    """
    cfg = _with_default_sharp()
    try:
        d_bad = _diag_only(_circ(118, 96))
        d_good = _diag_only(_circ(40.0, 160, sigma=0.06))
        th = _PC["resid_ratio"]
        assert d_bad.get("sharpness") is not None, "坏形态侧没给出 sharpness：%r" % (d_bad,)
        assert d_good.get("sharpness") is not None, "真周期侧没给出 sharpness：%r" % (d_good,)
        assert d_bad["resid"] < th and d_good["resid"] < th, (
            "两侧 resid 都应低于阈值 %r（否则本组退化成在比残差）：%r / %r"
            % (th, d_bad.get("resid"), d_good.get("resid")))
        assert d_good["sharpness"] < 0.2, (
            "真周期侧 sharpness=%.4f 未与阈值 0.5 拉开余量" % d_good["sharpness"])
        assert d_bad["sharpness"] > 0.5, (
            "坏形态侧 sharpness=%.4f 未与阈值 0.5 拉开余量" % d_bad["sharpness"])
        assert d_bad["sharpness"] > d_good["sharpness"] * 3.0, (
            "两侧 sharpness 未分离：坏 %.4f vs 真 %.4f"
            % (d_bad["sharpness"], d_good["sharpness"]))
    finally:
        _restore(cfg)


def _diag_only(pts):
    """只喂、只取诊断（不量误差），用于纯判据对照。"""
    m = UnifiedWorldModel(size=24)
    for p in pts:
        m.perceive([{"eid": "a", "pos": p, "category": "c"}])
        m.generate(1)
    d = (getattr(m, "_periodic_diag", {}) or {}).get("a") or {}
    assert d, "周期诊断为空 —— 周期检测根本没跑到"
    return d


def test_e_正对照_真平滑与真随机不得被判周期():
    """E（防过修另一侧）：真·平滑（等加速直线）仍走 smooth；真·随机游走仍判噪声。"""
    cfg = _with_default_sharp()
    try:
        err1, modes1, d1, _m1 = _run(_smooth_line(160 + HORIZON + 1))
        assert "smooth" in modes1, "真·平滑序列未走 smooth：%r" % (modes1,)
        assert not modes1.get("periodic"), "真·平滑序列被判成周期：%r" % (modes1,)
        assert err1 < 1e-6, "真·平滑序列的预测误差应近 0，实测 %.6f" % err1
        err2, modes2, d2, _m2 = _run(_rand_walk(160 + HORIZON + 1))
        assert not modes2.get("periodic"), "真·随机噪声被判成周期：%r" % (modes2,)
        assert d2.get("suspect_spurious") is not True, (
            "随机噪声不该走「谷够尖」拒绝路径（它先被往复性判据拦下）：%r" % (d2,))
        assert err2 > 0.05, "随机游走的误差不该近 0（说明真值对齐有误）：%.4f" % err2
    finally:
        _restore(cfg)


def test_f_诊断给出邻居跨度且与周期同尺度():
    """F：诊断必须给出本次实际使用的邻居跨度 `sharp_span`，且等于 max(1, round(P/4))。"""
    cfg = _with_default_sharp()
    try:
        d = _diag_only(_circ(40.0, 160, sigma=0.06))
        assert d.get("P") is not None, "真周期 40 未被检出：%r" % (d,)
        assert d.get("sharp_span") is not None, (
            "诊断缺 `sharp_span` —— 判据邻居跨度必须可复核：%r" % (d,))
        expect = max(1, int(round(float(d["P"]) / 4.0)))
        assert int(d["sharp_span"]) == expect, (
            "sharp_span=%r ≠ max(1, round(P/4))=%d（P=%r）" % (d["sharp_span"], expect, d["P"]))
        # 短周期（P=min_period）必须退回旧口径跨度 1（「短周期捡漏」形态逐位不变）
        d2 = _diag_only(_circ(118, 96))
        assert d2.get("P") == _PC["min_period"], (
            "本用例的前提是检出 P=min_period：%r" % (d2,))
        assert int(d2.get("sharp_span")) == 1, (
            "短周期捡漏的邻居跨度必须是 1（与旧口径逐位相同）：%r" % (d2,))
    finally:
        _restore(cfg)


def test_g_已知残余_上限边界必须保持诚实标记():
    """G（已知残余 · 不掩饰）：真周期比枚举上限大 1 帧（T=49 / 上限 48）时，
    新跨度**会接受** `P=48`（旧判据靠 0.501 的 0.001 余量拒绝，属侥幸而非稳健）。

    本组**不**钉住「接受还是拒绝」这个有争议的取舍，只钉住**诚实性**：
    这种情形必须 `clipped=True` + `clip_note`，让调用方知道「真周期可能更大」。
    （若日后要连这 3 例也拒，应另立判据——例如 `clipped` 且 `resid` 明显高于噪声底——
    那属另一条修法，不在 #706 范围。）
    """
    cfg = _with_default_sharp()
    try:
        m = UnifiedWorldModel(size=24)
        for p in _circ(49.0, 96):
            m.perceive([{"eid": "a", "pos": p, "category": "c"}])
            m.generate(1)
        d = (getattr(m, "_periodic_diag", {}) or {}).get("a") or {}
        assert d, "周期诊断为空 —— 周期检测根本没跑到"
        assert d.get("P") is not None and d["P"] >= _PC["max_period"], (
            "本用例的前提是检出 P 撞上限：%r" % (d,))
        assert d.get("clipped") is True, (
            "上限边界必须显式标记 clipped（真周期可能更大，不得静默）：%r" % (d,))
        assert d.get("clip_note"), "截断必须带可读提示：%r" % (d,)
    finally:
        _restore(cfg)
