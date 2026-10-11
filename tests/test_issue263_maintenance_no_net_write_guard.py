# -*- coding: utf-8 -*-
"""test_issue263_maintenance_no_net_write_guard · #263 维护周期净写入守卫
============================================================================
缺陷（lingshu issue #263 · P1）：`run_maintenance_cycle`（衰减 → 巩固 → 归纳）
本应「该忘的忘、该并的并」，实测却**往不衰减的知识层写新节点**：
  ① `consolidate_cycle` 结尾 `self.add_perception("[consolidation] 演练 …",
     importance=0.4, tags=["consolidation"])` —— `add_perception`「自动进入
     **知识层**」，知识层不衰减；每跑一轮维护就多一条自己的统计日志（本机实测
     300 轮后知识层 571 条中 173 条是这种日志），还会被 `induce_concepts`
     聚成「概念」、被 `recall` 召回。
  ② 归纳出的概念以 `importance=0.75` 起步（> 演练阈值 0.7），虽一直带
     `pending_verification`，却被同一维护周期逐轮 `increment_access`、约 250 轮
     抬到 importance 1.0，越过一切已确认事实。

修法（最小 · 取 issue「建议修复 1 + 建议 3 前半」）：
  ① `consolidate_cycle` 的统计改落 `action_logs`（只追加审计，不进检索面），
     不再调用 `add_perception`；统计数字仍经返回值给调用方。
  ② 演练分支跳过带 `pending_verification` 的节点（复核摘标签后才参与演练）。
判据来源：公开 issue #263（「期望行为：维护周期本身不往记忆图写节点」＋
「pending_verification 的概念在复核前不进入演练提权」）＋ 分诊表
`triage_lingshu_ALL.md` 行 263。

守卫断言组：
  A 组（硬判据 · 无净写入）：文件库、无新输入，连续 10 轮
     `run_maintenance_cycle`，节点总数必须**不变**，且库中不得出现
     `[consolidation]` 开头的知识层节点（旧实现 ⇒ 每轮 +1 ⇒ 必红）。
  B 组（硬判据 · 未验证概念不自抬）：`pending_verification` 高重要度概念连跑
     20 轮巩固，importance 与 access_count 必须**都不变**（旧实现 ⇒ 每轮
     access_count +1、约 250 轮抬到 1.0 ⇒ 必红）。
  C 组（对照 · 统计仍可查）：巩固统计必须仍落在 `action_logs`（审计面未丢）。
  D 组（对照 · 已复核节点照旧演练）：无 `pending_verification` 的高重要度节点
     仍走演练分支（修法未把演练改坏）。

运行（仓根）：
    python -X utf8 -m pytest tests/test_issue263_maintenance_no_net_write_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _count(eng, where="1=1"):
    return eng.store.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE " + where).fetchone()[0]


def test_maintenance_writes_no_nodes_without_new_input(tmp_path):
    """A 组：无新输入时连续维护不得增加节点，也不得留下 [consolidation] 节点。"""
    eng = SpacetimeMemoryEngine(str(tmp_path / "a.db"))
    try:
        before = _count(eng)
        for _ in range(10):
            eng.run_maintenance_cycle()
        after = _count(eng)
        assert after == before, (
            "#263：无新输入时连续 10 轮维护不得增加节点（旧实现每轮写一条 "
            "[consolidation] 知识节点）；before=%d after=%d" % (before, after))
        logs = _count(eng, "content LIKE '[consolidation]%'")
        assert logs == 0, (
            "#263：维护统计不得写成记忆节点（实测库中 %d 条 [consolidation] 节点）" % logs)
        assert _count(eng, "tags LIKE '%\"consolidation\"%'") == 0, (
            "#263：库中不得再有 consolidation 标签的知识节点")
    finally:
        eng.close()


def test_pending_verification_concept_not_rehearsed(tmp_path):
    """B 组：未复核（pending_verification）概念不得被维护周期演练/自抬。"""
    eng = SpacetimeMemoryEngine(str(tmp_path / "b.db"))
    try:
        node = eng.add_perception(
            "[概念] 由 3 条记忆归纳（代表：测试）…", skip_dedup=True,
            importance=0.75, tags=["concept", "induced", "pending_verification"])
        before_imp = eng.store.get_node(node.id).importance
        before_acc = eng.store.get_node(node.id).access_count
        for _ in range(20):
            eng.consolidate_cycle()
        after = eng.store.get_node(node.id)
        assert after.importance == before_imp, (
            "#263：未复核概念不得被自抬（旧实现 0.75 起步、高于阈值 ⇒ 逐轮演练）；"
            "importance %s → %s" % (before_imp, after.importance))
        assert (after.access_count or 0) == (before_acc or 0), (
            "#263：未复核概念不得被演练刷新访问计数（access_count %s → %s）"
            % (before_acc, after.access_count))
    finally:
        eng.close()


def test_consolidation_stats_still_audited(tmp_path):
    """C 组（对照）：巩固统计必须仍落 action_logs（审计面未因改动丢失）。"""
    eng = SpacetimeMemoryEngine(str(tmp_path / "c.db"))
    try:
        stats = eng.consolidate_cycle()
        rows = eng.store.conn.execute(
            "SELECT summary FROM action_logs WHERE action_type='consolidation'"
        ).fetchall()
        assert rows, "C 组对照：巩固统计应落一条 action_logs 审计（实测无）"
        assert "演练" in rows[-1][0] and "降权" in rows[-1][0], (
            "C 组对照：审计行应含统计明细；实测=%r" % (rows[-1][0],))
        assert stats["rehearsed"] == 0 and stats["degraded"] == 0
    finally:
        eng.close()


def test_verified_high_importance_still_rehearsed(tmp_path):
    """D 组（对照）：已复核（无 pending_verification）高重要度节点照旧演练。"""
    eng = SpacetimeMemoryEngine(str(tmp_path / "d.db"))
    try:
        eng.add_perception("高重要度已确认记忆", skip_dedup=True, importance=0.85)
        stats = eng.consolidate_cycle()
        assert stats["rehearsed"] >= 1, (
            "D 组对照：无 pending_verification 的高重要度节点应被演练；stats=%s" % stats)
    finally:
        eng.close()
