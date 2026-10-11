# -*- coding: utf-8 -*-
"""#207 decay_cycle 情境层只处理 `importance > 阈值` 的节点 —— 守卫
（lingshu/core/core.py）

缺陷（分诊表 `triage_lingshu_core_front.md` 行 207 判定「成立」；issue 原文
`.tmp/_b4_issue_207.json`）：
  `LayeredStore.decay_cycle` 情境层段的注释写「CONTEXT 层随时间淡出——低于阈值
  删除（自然遗忘）」，但 SQL 只选 `WHERE layer='context' AND importance > ?`
  （? = `min_confidence`，默认 0.1）⇒ importance **已 ≤ 阈值**的节点根本不被
  选出，既不衰减也不删除、**永远留在库里**。结果与设计相反：importance 0.5 的
  短期记忆会被遗忘，0.05 / 0.0 的反而永不过期（越不重要越长寿）。
  `add_context` 允许传任意 importance；`forget_advisor` 归档时把 importance
  精确设为 0.1 ⇒ 这些节点都落进死区。

修法（最小 · 单一方向）：情境层段的 SQL 去掉 `AND importance > ?`，选出**全部**
情境节点，先 `cred_step(imp, factor)` 衰减、再以 `new_imp < min_confidence` 判删；
非有限/NULL 的存量坏行按同函数边衰减段的既有口径兜底为 0.0（#185/#184 不回归）。
判据来源：issue #207「建议修复」（逐字同款做法）＋ 该段既有注释「低于阈值删除
（自然遗忘）」＋ 分诊表行 207（经验标定，理论章节追不到）。
边界（削掉的判别力，如实标注）：**阈值本体未改**（仍是 `min_confidence`）；
受保护件仍经 `_is_forget_protected` 跳过（#36 不变）；本件只把「≤ 阈值的低价值
情境节点」纳入遗忘面——这正是死区本身。副作用：每轮衰减现在要扫全部情境节点
（旧 SQL 借 `importance > ?` 顺带做了过滤）——issue 已给出同一做法，未另设索引。

断言组（把 SQL 改回 `AND importance > ?` ⇒ Q1/Q2/Q3 红）：
  Q1 死区不再：importance=0.0 / 0.05 / 0.1 的情境节点多轮后被删除（旧：永在）。
  Q2 单调性：低重要度不得比高重要度「更长寿」——0.05 的删除轮数 ≤ 0.5 的。
  Q3 端到端 + 回报：`forget_advisor` 归档出的 importance=0.1 节点不再永生。
  Q4 防误杀（#36）：受保护情境节点（protections 登记 / `no_forget` 标签）
     在多轮衰减后**仍在**。
  Q5 防误杀（高价值）：importance 接近 1.0 的情境节点在少数轮内不被删除。

运行（仓根）：python -X utf8 -m pytest tests/test_issue207_context_decay_deadzone_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _alive(e, nid) -> bool:
    return e.store.get_node(nid) is not None


def test_q1_deadzone_nodes_are_forgotten():
    """Q1：importance 0.0/0.05/0.1 的情境节点必须能被自然遗忘（旧：永生）。"""
    e = SpacetimeMemoryEngine(":memory:")
    ids = {}
    for imp in (0.0, 0.05, 0.1):
        ids[imp] = e.add_context("情境 importance=%s" % imp, importance=imp).id
    for _ in range(1000):
        e.store.decay_cycle()
    for imp, nid in ids.items():
        assert not _alive(e, nid), (
            "importance=%s 的情境节点 1000 轮后仍在（越不重要越长寿的死区）" % imp)


def test_q2_lower_importance_not_longer_lived():
    """Q2：低重要度不得比高重要度更长寿（删除轮数单调）。"""
    e = SpacetimeMemoryEngine(":memory:")
    low = e.add_context("低", importance=0.05).id
    high = e.add_context("高", importance=0.5).id
    low_rounds = high_rounds = None
    for i in range(1, 2000):
        e.store.decay_cycle()
        if low_rounds is None and not _alive(e, low):
            low_rounds = i
        if high_rounds is None and not _alive(e, high):
            high_rounds = i
        if low_rounds and high_rounds:
            break
    assert low_rounds is not None, "低重要度情境节点 2000 轮内未被删除"
    assert high_rounds is not None, "高重要度情境节点 2000 轮内未被删除"
    assert low_rounds <= high_rounds, (
        "低重要度比高重要度更长寿（低=%s 轮，高=%s 轮）——#207 死区仍在"
        % (low_rounds, high_rounds))


def test_q3_forget_advisor_archived_node_not_immortal():
    """Q3：`forget_advisor` 归档（importance=0.1）的情境节点不再永生。"""
    e = SpacetimeMemoryEngine(":memory:")
    n = e.add_context("待归档的情境记忆", importance=0.15)
    res = e.forget_advisor(stale_days=0.0, low_value=0.5, archived_imp=0.1)
    assert res["archived"] >= 1, "前置：未归档（%s）" % res
    got = e.store.get_node(n.id)
    assert got is not None and abs(got.importance - 0.1) < 1e-9, (
        "前置：归档后 importance 不是 0.1（实测 %s）" % (None if got is None else got.importance))
    for _ in range(1000):
        e.store.decay_cycle()
    assert not _alive(e, n.id), "归档（importance=0.1）节点 1000 轮后仍在（死区）"


def test_q4_protected_context_nodes_survive():
    """Q4：防误杀——受保护情境节点（#36）不因本件改动被删。"""
    e = SpacetimeMemoryEngine(":memory:")
    by_row = e.add_context("protections 登记", importance=0.05)
    e.protect_node(by_row.id, "守卫：不可遗忘")
    by_tag = e.add_context("no_forget 标签", importance=0.05, tags=["no_forget"])
    for _ in range(1000):
        e.store.decay_cycle()
    assert _alive(e, by_row.id), "protections 登记的情境节点被衰减删除（#36 回归）"
    assert _alive(e, by_tag.id), "no_forget 标签的情境节点被衰减删除（#36 回归）"


def test_q5_high_value_context_survives_short_run():
    """Q5：防误杀——高价值情境节点在少数轮内不被删除。"""
    e = SpacetimeMemoryEngine(":memory:")
    n = e.add_context("高价值情境", importance=0.95)
    for _ in range(5):
        e.store.decay_cycle()
    assert _alive(e, n.id), "importance=0.95 的情境节点 5 轮即被删（阈值口径被改坏）"
