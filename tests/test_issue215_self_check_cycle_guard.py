# -*- coding: utf-8 -*-
"""#215 `self_check` 的环检测自相矛盾（环长 > max_depth 时报 cycles_found=0）—— 守卫

缺陷（分诊表 `triage_lingshu_ALL.md` 行 215 判定「成立」；issue 原文 `.tmp/issues_p3.json` #215）：
  `self_check` 里 `has_causal_cycle()` 是**存在性**探测（三色 DFS，O(V+E)，**无深度
  限制**），`find_cycles(max_depth=8)` 是**有上限的枚举**（`max_depth` 是环长上限）。
  环长 > max_depth 时前者 True、后者 [] ⇒ 旧实现报 `cycles_found=0`，**同一份自检
  报告里「有环」与「0 个环」并存**；调用方把 0 读成「无环」——正是 #145 想消除的
  「错误的放心信号」，修复后仍在（形态更隐蔽）。issue 实测边界精确落在
  「环长 > max_depth」（环长 2/6/8/9 检出、10/11/13/16 报 0）。

修法（最小 · 只增字段，不改既有量）：
  `cycles_found` **保持原语义**（已枚举环数，避免波及
  `tests/test_issue145_causal_depth_cycles.py` C 组既有断言）；新增
  `has_causal_cycle`（探测结论）、`cycles_truncated`（探测到环但一个都没枚举出 ⇒
  环长可能超过 max_depth=8）、`cycle_warning`（人读告警）——使 0 不再可被读成
  「无环」。

判据来源：issue #215「问题 1」及其建议 1（「在 has_causal_cycle() 为 True 而
find_cycles 为空时**明确告警**『检测到环但未能枚举（环长可能超过 max_depth=8）』，
而不是报 0」）＋ 分诊表行 215。
边界（削掉的判别力，如实标注）：本件**不**改 `find_cycles` 的深度上限（会改 #145
的耗时判据），也**不**把 `cycles_found` 改成别的量（会波及既有断言）；#215「问题 2」
（有环大图仍走整趟深搜枚举）**不在本件**——那属复杂度预算取舍，须先裁定。

断言组（把修复改回缺陷形态 ⇒ Q1/Q2 红）：
  Q1 矛盾消解：环长 10/11/13/16（> max_depth=8）的图上 `cycles_found==0` 时，
     必须同时给出 `has_causal_cycle=True` 与 `cycles_truncated=True` + 告警文案。
  Q2 边界精确：环长 ≤ 8 的图上 `cycles_truncated` 必须为 False 且告警为 None
     （不得把「真无环」与「超出检测深度」混同）。
  Q3 独立判据对照：`has_causal_cycle` 与自写 Kahn 拓扑排序结论一致（不用被测函数
     作判据）。
  Q4 非回归：环长 2/6/8/9 的既有读数（`cycles_found` ≥1）与 #145 C 组的
     A⇄B + 自环读数（≥2）保持不变。

运行（仓根）：python -X utf8 -m pytest tests/test_issue215_self_check_cycle_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import EdgeType, SpacetimeMemoryEngine  # noqa: E402

MAX_DEPTH = 8  #: self_check 内部硬编码的 max_depth（环长上限）


def _mk(e, n):
    return [e.add_perception("事件节点%d：独立观察记录%d" % (i, i * 7919),
                             skip_dedup=True).id for i in range(n)]


def _ring_engine(span, n=40):
    """n 个节点排成链、末尾一条回边闭合 ⇒ 唯一环，环长 = span+1（issue 同款构造）。"""
    e = SpacetimeMemoryEngine(":memory:")
    ids = _mk(e, n)
    for i in range(n - 1):
        e.add_edge(ids[i], ids[i + 1], EdgeType.CAUSAL)
    e.add_edge(ids[span], ids[0], EdgeType.CAUSAL)
    return e, ids


def _kahn_has_cycle(e) -> bool:
    """独立判据：Kahn 拓扑排序（不用被测的 has_causal_cycle / find_cycles）。"""
    rows = e.store.conn.execute(
        "SELECT source_id, target_id FROM edges WHERE relation_type IN (?, ?)",
        (EdgeType.CAUSAL.value, EdgeType.CYCLIC.value)).fetchall()
    nodes, outdeg, indeg = set(), {}, {}
    for s, t in rows:
        nodes.add(s); nodes.add(t)
        outdeg[s] = outdeg.get(s, 0) + 1
        indeg[t] = indeg.get(t, 0) + 1
    for n in nodes:
        indeg.setdefault(n, 0)
    queue = [n for n in nodes if indeg[n] == 0]
    peeled = 0
    while queue:
        cur = queue.pop()
        peeled += 1
        for s, t in rows:
            if s == cur:
                indeg[t] -= 1
                if indeg[t] == 0:
                    queue.append(t)
    return peeled < len(nodes)


def test_q1_truncated_cycles_are_flagged():
    """Q1：环长 > max_depth 时不得只报 0——须同时给出探测结论与截断告警。"""
    for span in (9, 10, 12, 15):            # 环长 10/11/13/16
        e, _ = _ring_engine(span)
        rep = e.self_check()
        assert rep["cycles_found"] == 0, (
            "前置：环长 %d 应未被枚举（find_cycles max_depth=8）——实测 %r"
            % (span + 1, rep["cycles_found"]))
        assert rep.get("has_causal_cycle") is True, (
            "#215：环长 %d 时 has_causal_cycle 未如实回报——%r"
            % (span + 1, rep.get("has_causal_cycle")))
        assert rep.get("cycles_truncated") is True, (
            "#215：环长 %d 时 cycles_found=0 而 cycles_truncated 未置位 ⇒ 0 仍会被"
            "读成「无环」（旧形态：自检自相矛盾）——report=%r"
            % (span + 1, {k: rep.get(k) for k in
                          ("cycles_found", "has_causal_cycle", "cycles_truncated")}))
        assert rep.get("cycle_warning"), (
            "#215：截断时未给出人读告警——cycle_warning=%r" % rep.get("cycle_warning"))


def test_q2_boundary_not_conflated():
    """Q2：环长 ≤ max_depth（含真无环）时不得置 truncated（两类 0 必须可分）。"""
    for span in (1, 5, 7, 8):               # 环长 2/6/8/9
        e, _ = _ring_engine(span)
        rep = e.self_check()
        assert rep["cycles_found"] >= 1, (
            "非回归：环长 %d 应被枚举——实测 %r" % (span + 1, rep["cycles_found"]))
        assert rep["cycles_truncated"] is False, (
            "#215：环长 %d 被误标截断——report=%r" % (span + 1, rep))
        assert rep["cycle_warning"] is None, (
            "#215：环长 %d 给出了多余告警——%r" % (span + 1, rep["cycle_warning"]))

    # 真无环图：两类 0 必须可分
    e = SpacetimeMemoryEngine(":memory:")
    ids = _mk(e, 5)
    for a, b in zip(ids, ids[1:]):
        e.add_edge(a, b, EdgeType.CAUSAL)
    rep = e.self_check()
    assert rep["cycles_found"] == 0 and rep["has_causal_cycle"] is False \
        and rep["cycles_truncated"] is False and rep["cycle_warning"] is None, (
        "#215：真无环图被误判——report=%r" % rep)


def test_q3_agrees_with_independent_kahn():
    """Q3：`has_causal_cycle` 与独立 Kahn 判据逐图一致（判据本身先自证）。"""
    for span in (1, 5, 7, 8, 9, 10, 12, 15):
        e, _ = _ring_engine(span)
        assert _kahn_has_cycle(e) is True, (
            "前置（独立判据）：环长 %d 的图按 Kahn 应有环" % (span + 1))
        assert e.self_check()["has_causal_cycle"] is True
    e = SpacetimeMemoryEngine(":memory:")
    ids = _mk(e, 5)
    for a, b in zip(ids, ids[1:]):
        e.add_edge(a, b, EdgeType.CAUSAL)
    assert _kahn_has_cycle(e) is False and e.self_check()["has_causal_cycle"] is False


def test_q4_existing_readings_unchanged():
    """Q4：非回归——#145 C 组读数（A⇄B + C→C ⇒ ≥2）与深环读数保持。"""
    e = SpacetimeMemoryEngine(":memory:")
    a, b, c = _mk(e, 3)
    e.add_edge(a, b, EdgeType.CAUSAL)
    e.add_edge(b, a, EdgeType.CAUSAL)
    e.add_edge(c, c, EdgeType.CAUSAL)
    rep = e.self_check()
    assert rep["cycles_found"] >= 2, (
        "#215 非回归：A⇄B + 自环应 ≥2 个环——实测 %r" % rep["cycles_found"])
    assert rep["cycles_truncated"] is False
