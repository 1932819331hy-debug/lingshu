#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_issue387_second_difference_guard —— 门控二阶特征必须是**二阶差分**（lingshu #387）

缺陷（triage 判「成立」，取证 `lingshu/core/longterm_gate.py:60` 与 `:78`）：
    `_d_second` / `_trust_second` 的文件头声明（`:10-11`）与函数 docstring 都写
    「二阶差分（加速/减速）」「二阶差分（信任跃升）」，但实作是
        return max(-0.5, min(0.5, d1[-1] - d1[0]))
    其中 `d1 = [vals[i+1]-vals[i] …]` —— 这是**窗口两端一阶差之差**
    （跨整窗的趋势量，取决于窗口起点），不是最近一点的二阶差分。
    后果：只要窗口内 slope 曾变过，该量就按**窗口跨度**取满，`min(0.5, …)`
    常被钳到 ±0.5 档，与「最近一步的加速度」无关。

修复：取 `d1[-1] - d1[-2]`（= `vals[-1] - 2·vals[-2] + vals[-3]`，标准二阶差分），
    并补 `len(d1) < 2 → 0.0` 的边界。

判据来源：
  · 文件头 `lingshu/core/longterm_gate.py:10-11`（「Δ²D 信息差二阶 → gap_history
    二阶差分（加速/减速）」「Δ²T 信任二阶 → trust_history 二阶差分（信任跃升）」）
    —— 属**文件内声明与实现的自洽性**修复。
  · 二阶差分的标准定义 Δ²x_n = x_n − 2·x_{n−1} + x_{n−2}。
  · 理论章节追不到（本仓无 longterm_gate 独立理论稿；`docs/theory/` 下无该门控
    的二阶特征章节）⇒ 判据仅以上两条。

断言组（抽掉修复即红；与既有 `tests/test_longterm_gate_p1_defects.py`（#114 等）
不重叠——本件只钉二阶特征，且自带不依赖既有守卫的夹具）：
  G1 与标准二阶差分逐字一致：构造 d1[-1]-d1[0] 与 d1[-1]-d1[-2] 取值不同的序列，
     断言 `_d_second()` == vals[-1] - 2·vals[-2] + vals[-3]（旧式在此处给出
     d1[-1]-d1[0] = −0.15 ⇒ 红）。
  G2 **窗口起点无关**：在历史前面追加更早样本（使窗口首元素变化）后，最近三点的
     二阶差分不变；旧式随窗口首元素变化 ⇒ 红。
  G3 Δ²T 同根：`_trust_second()` 亦为标准二阶差分（经 trust_history 真写入路径）。
  G4 钳制边界仍在：构造超出 ±0.5 的加速度 → 结果仍被钳到 ±0.5（不得因改动放宽）。
  G5 样本不足边界：< 3 样本 → 0.0。

运行（仓根）：python -X utf8 -m pytest tests/test_issue387_second_difference_guard.py -q --no-header
             / python -X utf8 tests/test_issue387_second_difference_guard.py
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _gate_with_gap(vals):
    e = SpacetimeMemoryEngine(":memory:")
    e._gap_history = [{"d_norm": v} for v in vals]
    return e, e._ensure_gate()


def _textbook(vals):
    """最近一点的标准二阶差分。"""
    return vals[-1] - 2.0 * vals[-2] + vals[-3]


def test_g1_matches_textbook_second_difference():
    """G1：Δ²D 必须等于最近一点的标准二阶差分（旧式给出跨窗差 ⇒ 红）。"""
    vals = [0.0, 0.2, 0.4, 0.45, 0.5]
    d1 = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    old = d1[-1] - d1[0]
    new = d1[-1] - d1[-2]
    assert abs(old - new) > 0.1, (old, new)  # 夹具前提：两式确可区分
    _e, g = _gate_with_gap(vals)
    got = g._d_second()
    assert abs(got - _textbook(vals)) < 1e-12, (
        "Δ²D 不是最近一点的二阶差分：got=%r textbook=%r（旧式跨窗值=%r）"
        % (got, _textbook(vals), max(-0.5, min(0.5, old))))
    assert abs(got - max(-0.5, min(0.5, old))) > 1e-9, (
        "Δ²D 仍等于窗口两端一阶差之差（#387 缺陷形态）：%r" % (got,))


def test_g2_independent_of_window_start():
    """G2：二阶差分只依赖最近三点——在历史前面追加更早样本不得改变结果。"""
    tail = [0.4, 0.45, 0.5]
    _e1, g1 = _gate_with_gap([0.0, 0.2] + tail)      # 窗口首元素 0.0
    _e2, g2 = _gate_with_gap([0.35, 0.36] + tail)    # 窗口首元素 0.35（更近）
    v1, v2 = g1._d_second(), g2._d_second()
    assert abs(v1 - v2) < 1e-12, (
        "二阶差分随窗口起点变化（旧式跨窗形态）：%r vs %r" % (v1, v2))
    assert abs(v1 - _textbook(tail)) < 1e-12, (v1, _textbook(tail))


def test_g3_trust_second_is_also_local_second_difference():
    """G3：Δ²T 同根——经 trust_history 真写入路径，须为标准二阶差分。"""
    e = SpacetimeMemoryEngine(":memory:")
    for i, t in enumerate([0.1, 0.3, 0.5, 0.55, 0.6]):
        e.self_model.update_trust_state(t, i)
    vals = [h["t_total"] for h in e.self_model.trust_history[-6:]]
    d1 = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    g = e._ensure_gate()
    got = g._trust_second()
    assert abs(got - _textbook(vals)) < 1e-12, (
        "Δ²T 不是最近一点的二阶差分：got=%r textbook=%r" % (got, _textbook(vals)))
    assert abs(got - (d1[-1] - d1[0])) > 1e-9, (
        "Δ²T 仍等于窗口两端一阶差之差（#387 缺陷形态）：%r" % (got,))


def test_g4_clamp_boundary_still_enforced():
    """G4：钳制不得因本次改动放宽——超界加速度仍钳到 ±0.5。"""
    _e1, g1 = _gate_with_gap([0.0, 0.5, 0.5, 0.0, 0.5])
    assert g1._d_second() == 0.5, g1._d_second()
    _e2, g2 = _gate_with_gap([0.5, 0.0, 0.0, 0.5, 0.0])
    assert g2._d_second() == -0.5, g2._d_second()


def test_g5_insufficient_samples_returns_zero():
    """G5：< 3 样本 → 0.0（边界不因改动而抛异常或返回非零）。"""
    for vals in ([], [0.3], [0.3, 0.6]):
        _e, g = _gate_with_gap(vals)
        assert g._d_second() == 0.0, (vals, g._d_second())


def main():
    fns = [test_g1_matches_textbook_second_difference,
           test_g2_independent_of_window_start,
           test_g3_trust_second_is_also_local_second_difference,
           test_g4_clamp_boundary_still_enforced,
           test_g5_insufficient_samples_returns_zero]
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
