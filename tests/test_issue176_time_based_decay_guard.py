# -*- coding: utf-8 -*-
"""#176 时间核章程未兑现：遗忘按「调用次数」而非流逝时间 —— 守卫（lingshu/core/core.py）

缺陷（issue #176）：
  `core/time_core.py` 章程写「全库一切时间演化必须经本模块……X(t)=X∞+(X0−X∞)·e^(−γ·t)」，
  但连续核 `cred(x0,x_inf,gamma,dt)` 全仓**零调用**；唯一遗忘路径
  `LayeredStore.decay_cycle` 用离散 `cred_step(x, factor)`——**每调用一次乘一次
  (1−0.02)**，与流逝时间无关。后果：`start_auto_decay(interval=1)` 与 `interval=60`
  在相同墙钟时间里遗忘量相差 60 倍、手动多调几次 = 时间快进、**停机一周重启零遗忘**。

修法（issue 建议 1+2，最小）：
  · `decay_cycle(..., now=None)`：`now` 给出时按 `dt = now − 上次衰减时刻`
    （持久化于 `engine_meta["_last_decay_at"]`）用**连续核** `cred(x,0,γ,dt)`
    演化，γ = −ln(1−factor)/周期；`now=None` 保持旧步进（每次=一个周期）。
  · 引擎注入时钟：`start_auto_decay(interval, clock=…)` + `decay_cycle` 透传 `now`。
  · 向后兼容：签名新增参数一律带默认值；`now=None` 时行为逐字不变。

断言组（抽掉 `_decay_value` 的连续核（改回 cred_step）⇒ A/B 变红；
          抽掉 `_decay_dt` 的时间记账（恒返回周期）⇒ A/B 变红）：
  A 调用频率无关：相同 Δt 下调用 1 / 6 / 60 次结果在误差内相等（issue 判据逐字）。
  B 停机补足：停机 Δt 后一次性衰减量 == 同等 Δt 分多次衰减的累计量。
  C 向后兼容：`now=None` 时每轮 == 旧 `cred_step(x, factor)`（x·(1−factor)）。
  D 连续核确被调用：`_decay_value` 与 `time_core.cred` 公式逐值相等，且源码含 `cred(`。
  E 时钟注入：`start_auto_decay(clock=…)` 后手动 `decay_cycle()` 按假时钟流逝时间衰减。
  F 对照：`interval` 不再决定遗忘量——不同 interval 的时钟模式下同 Δt 同结果。

运行（仓根）：python -X utf8 -m pytest tests/test_issue176_time_based_decay_guard.py -q --no-header
"""
from __future__ import annotations

import io
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core import time_core as T  # noqa: E402
from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "lingshu", "core", "core.py")


def _ratio_over_span(calls, span, t0=1000.0):
    """在 span 秒内分 `calls` 次衰减，返回「测量相位」的保持比 c1/c0。

    预热一次（钉下 `last_decay_at`），使测量相位 dt 严格等于 span。
    """
    e = SpacetimeMemoryEngine(":memory:")
    n = e.add_perception("探针知识层", skip_dedup=True)
    e.store.decay_cycle(now=t0)                    # 预热：钉 last_decay_at = t0
    c0 = e.store.get_node(n.id).confidence
    step = span / calls
    for i in range(calls):
        e.store.decay_cycle(now=t0 + step * (i + 1))
    c1 = e.store.get_node(n.id).confidence
    return c1 / c0


def test_a_call_frequency_independent():
    """A：相同 Δt 下调用 1 / 6 / 60 次结果在误差内相等（issue 判据逐字）。"""
    r1 = _ratio_over_span(1, 3600.0)
    r6 = _ratio_over_span(6, 3600.0)
    r60 = _ratio_over_span(60, 3600.0)
    assert abs(r1 - r60) < 1e-9, (
        "#176 调用频率改变结果：1 次=%r vs 60 次=%r（应相等）" % (r1, r60))
    assert abs(r1 - r6) < 1e-9, (
        "#176 调用频率改变结果：1 次=%r vs 6 次=%r" % (r1, r6))


def test_b_downtime_catches_up():
    """B：停机 Δt 后一次性补足。

    B1（值层，无阈值干扰）：连续核在**一周** Δt 上「一次大步」== 「168 次小步」
        ——这是纯核性质，`cred` 的指数族保证。
    B2（引擎层，中等 Δt）：停机 1800s 一次性衰减 == 分 30 次衰减的累计量。
    ★边界（如实标注）：引擎层不取「一周」——知识层衰减段带既有选择门
      `WHERE confidence > min_confidence`（默认 0.1），**分多次**衰减时一旦跌破
      0.1 即不再被选中（停在阈值附近），而**一次大步**可越过阈值继续下降 ⇒ 极端
      Δt 下两路径不再逐值相等。该门槛是本函数既有语义（非本件引入），本件不
      放宽；判据「调用频率无关」在**同一门槛分支内**成立，故 B2 取中等 Δt。
    """
    # B1：值层 —— 一周 Δt 的核等价（无阈值）
    e = SpacetimeMemoryEngine(":memory:")
    week = 7 * 86400.0
    one = e.store._decay_value(0.5, 0.02, week)
    many = 0.5
    for _ in range(168):
        many = e.store._decay_value(many, 0.02, week / 168.0)
    assert abs(one - many) < 1e-12, (
        "#176 连续核在长 Δt 上不可组合：一次=%r 分 168 次=%r" % (one, many))
    assert one < 0.5, "#176 停机一周几乎零遗忘（值层）：%r" % (one,)
    # B2：引擎层 —— 1800s 一次性 vs 分 30 次（都在同一门槛分支内）
    a = _ratio_over_span(1, 1800.0)
    b = _ratio_over_span(30, 1800.0)
    assert abs(a - b) < 1e-9, (
        "#176 停机补足不等：一次=%r 分 30 次=%r" % (a, b))
    assert a < 0.999, "#176 停机后几乎零遗忘：ratio=%r" % (a,)


def test_c_backward_compatible_without_now():
    """C：`now=None` 时每轮 == 旧 `cred_step(x, factor)`（行为逐字不变）。"""
    e = SpacetimeMemoryEngine(":memory:")
    n = e.add_perception("探针知识层", skip_dedup=True)
    before = e.store.get_node(n.id).confidence
    e.decay_cycle()                                  # 默认 now=None
    got = e.store.get_node(n.id).confidence
    assert abs(got - T.cred_step(before, 0.02)) < 1e-12, (
        "#176 now=None 时行为漂移：%r != cred_step(%r, 0.02)=%r"
        % (got, before, T.cred_step(before, 0.02)))
    assert abs(got - before * 0.98) < 1e-12, (before, got)


def test_d_continuous_kernel_is_actually_used():
    """D：`_decay_value` 与 `time_core.cred` 公式逐值相等；源码确含 `cred(` 调用。"""
    e = SpacetimeMemoryEngine(":memory:")
    dt = 123.0
    got = e.store._decay_value(0.5, 0.02, dt)
    gamma = -math.log(1.0 - 0.02) / e.store._DECAY_PERIOD_SECONDS
    expect = T.cred(0.5, 0.0, gamma, dt)
    assert abs(got - expect) < 1e-12, (
        "#176 未按连续核 cred 演化：%r != cred(...)=%r" % (got, expect))
    src = io.open(_CORE, encoding="utf-8").read()
    assert re.search(r"\bcred\s*\(", src), (
        "#176 core.py 内未调用连续核 cred（issue 实测：全仓零调用）")


def test_e_injected_clock_drives_manual_decay():
    """E：`start_auto_decay(clock=…)` 后手动 `decay_cycle()` 按假时钟流逝时间衰减。"""
    e = SpacetimeMemoryEngine(":memory:")
    fake = {"t": 5000.0}
    e.start_auto_decay(interval=3600.0, clock=lambda: fake["t"])
    try:
        n = e.add_perception("探针知识层", skip_dedup=True)
        e.decay_cycle()                     # 首轮：dt=周期 ⇒ 一个周期步进
        c0 = e.store.get_node(n.id).confidence
        fake["t"] += 60.0                   # 流逝恰好一个周期
        e.decay_cycle()
        c1 = e.store.get_node(n.id).confidence
        assert abs(c1 - c0 * 0.98) < 1e-9, (
            "#176 注入时钟未驱动衰减：一个周期后 %r != %r" % (c1, c0 * 0.98))
    finally:
        e.stop_auto_decay()


def test_f_interval_does_not_change_forgetting():
    """F：对照——`interval` 不再决定遗忘量（同 Δt 下不同 interval 同结果）。"""
    # 直接比较 store 层：interval 不进入 decay_cycle，遗忘只由 now 决定。
    a = _ratio_over_span(10, 600.0)
    b = _ratio_over_span(10, 600.0)
    assert abs(a - b) < 1e-12, (a, b)
    # 且与「一次大步」相等（跨调用次数）
    one = _ratio_over_span(1, 600.0)
    assert abs(a - one) < 1e-9, (a, one)


if __name__ == "__main__":
    fns = [test_a_call_frequency_independent, test_b_downtime_catches_up,
           test_c_backward_compatible_without_now,
           test_d_continuous_kernel_is_actually_used,
           test_e_injected_clock_drives_manual_decay,
           test_f_interval_does_not_change_forgetting]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as exc:
            failed += 1
            print("FAIL %s | %s" % (fn.__name__, exc))
    print("-- %d passed, %d failed --" % (len(fns) - failed, failed))
    print("VERDICT=%s" % ("PASS" if not failed else "FAIL"))
