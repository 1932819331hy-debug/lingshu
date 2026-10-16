# -*- coding: utf-8 -*-
"""#720 单点 `get_node` 漏网：一行坏 `tags` / 坏 `layer` 毒化 `decay_cycle` 与
`delete_node` —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #720，本守卫独立复现，读数见交付报告）：
  #267 的逐行容错只收在**批量装载面** `_tolerant_nodes`；`decay_cycle` 情境段 /
  知识段与 `delete_node` 的 peer 段走**单点裸解析**：
    · `json.loads(_tags)`（情境段 / 知识段 / peer 冲突标签清理）；
    · `MemoryLayer(_lay)`（`_strip_incident_edges` 的对端 layer 批量读数）。
  `nodes.tags` 有一行是非法 JSON（`'not-json'`）时：
    ① `decay_cycle()` 抛 `JSONDecodeError` ⇒ 整轮回滚、`importance` 零进展、
       后台衰减线程首轮退出（**遗忘循环永久停摆**）；
    ② `delete_node(a)` 抛同一异常（坏的是 **peer b**）⇒ 合法节点 a 仍在库
       （**因邻接坏行而删不掉**）。
  对照：同一坏行走容错面 `query_nodes_by_time` 被正确跳过并记入 `last_skipped_rows`。

修法（与 `_tolerant_nodes` 同口径「逐行容错、如实计数」）：三处裸解析改逐行
  try/except，坏行**跳过**（不衰减/不删除/原文留在库——不去猜它的内容），
  跳过 id 记入 `last_skipped_rows`，主流程不中断。捕获类型按 `json.loads` /
  `MemoryLayer(...)` 实际可能抛的 `(TypeError, ValueError)` 枚举（不用宽 except）。

断言组（把任一处回退到裸解析 ⇒ 对应组变红）：
  A `decay_cycle` 遇坏 `tags` 行：不抛、好节点照常衰减（importance 下降）、
    坏行 id 如实记入 `last_skipped_rows`（旧实现抛 JSONDecodeError ⇒ 红）。
  B `delete_node` 遇 peer 坏 `tags`：返回 True、被删节点确已不在库
    （旧实现抛 ⇒ 红）。
  C `_strip_incident_edges` 遇对端坏 `layer`：`delete_node` 仍返回 True。
  D 正对照（无坏行）：`decay_cycle` 正常衰减、`last_skipped_rows` 为空
    （防「跳过」逻辑把好行也跳掉）。
  E 正对照（#115 语义未被改坏）：peer 的 `tags` 合法时，删节点仍清 peer 的
    `conflict` 标签并落 `conflict_tag_cleared` 审计（修法不得吞掉原有行为）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue720_get_node_tolerance_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import EdgeType, SpacetimeMemoryEngine  # noqa: E402


def _bad_tags(engine, node_id):
    engine.store.conn.execute("UPDATE nodes SET tags=? WHERE id=?", ("not-json", node_id))
    engine.store.conn.commit()


def test_a_decay_cycle_survives_bad_tags_row():
    """A：坏 `tags` 行在场 ⇒ `decay_cycle` 不抛、好节点照常衰减、坏行如实计数。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        good = e.add_context("情境甲 关于橙子的记忆", importance=0.5)
        bad = e.add_context("情境乙 关于柠檬的记忆", importance=0.5)
        assert good is not None and bad is not None, "A 前置：两个情境节点须落库"
        before = e.store.get_node(good.id).importance
        assert before == 0.5, "A 前置：importance 初值应为 0.5，实测 %r" % (before,)
        _bad_tags(e, bad.id)

        e.store.decay_cycle()      # 旧缺陷：此处抛 JSONDecodeError

        after = e.store.get_node(good.id).importance
        assert after < before, (
            "#720 坏 tags 行让整轮遗忘停摆：好节点 importance %r → %r（应下降）"
            % (before, after))
        assert bad.id in (e.store.last_skipped_rows or []), (
            "#720 未如实计数跳过的坏行：last_skipped_rows=%r"
            % (e.store.last_skipped_rows,))
    finally:
        e.close()


def test_b_delete_node_survives_bad_peer_tags():
    """B：peer 行坏 `tags` ⇒ 合法节点仍可删（旧缺陷：抛异常、节点留在库）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        a = e.add_context("情境丙 关于葡萄的记忆", importance=0.5)
        b = e.add_context("情境丁 关于西瓜的记忆", importance=0.5)
        e.store.register_conflict(a.id, b.id)
        assert len(e.store.get_outgoing_edges(a.id)) == 1, "B 前置：OPPOSITE 边须存在"
        _bad_tags(e, b.id)

        ok = e.store.delete_node(a.id)     # 旧缺陷：此处抛 JSONDecodeError

        assert ok is True, "#720 合法节点因邻接坏行删不掉：delete_node 返回 %r" % (ok,)
        assert e.store.get_node(a.id) is None, "#720 被删节点仍在库"
        assert b.id in (e.store.last_skipped_rows or []), (
            "#720 未如实计数跳过的坏 peer：last_skipped_rows=%r"
            % (e.store.last_skipped_rows,))
    finally:
        e.close()


def test_c_delete_node_survives_bad_peer_layer():
    """C：对端行 `layer` 非法 ⇒ `_strip_incident_edges` 不中断 `delete_node`。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        a = e.add_context("情境戊 关于柚子的记忆", importance=0.5)
        b = e.add_context("情境己 关于草莓的记忆", importance=0.5)
        e.add_edge(a.id, b.id, EdgeType.CAUSAL, confidence=0.7)
        e.store.conn.execute("UPDATE nodes SET layer=? WHERE id=?", ("not-a-layer", b.id))
        e.store.conn.commit()
        assert len(e.store.get_outgoing_edges(a.id)) >= 1, "C 前置：边须存在"

        ok = e.store.delete_node(a.id)     # 旧缺陷：MemoryLayer('not-a-layer') 抛 ValueError

        assert ok is True, "#720 对端坏 layer 令删除中断：delete_node 返回 %r" % (ok,)
        assert e.store.get_node(a.id) is None, "#720 被删节点仍在库"
    finally:
        e.close()


def test_d_positive_control_clean_db_skips_nothing():
    """D（正对照）：无坏行 ⇒ 照常衰减且 `last_skipped_rows` 为空（量具非恒绿）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        good = e.add_context("情境庚 无坏行", importance=0.5)
        assert good is not None, "D 前置：节点须落库"
        e.store.decay_cycle()
        after = e.store.get_node(good.id).importance
        assert after < 0.5, "D 正对照失败：无坏行时也应衰减，实测 importance=%r" % (after,)
        assert not e.store.last_skipped_rows, (
            "D 正对照失败：无坏行却报了跳过：%r" % (e.store.last_skipped_rows,))
    finally:
        e.close()


def test_e_positive_control_conflict_tag_still_cleared():
    """E（正对照）：peer `tags` 合法 ⇒ #115 的 conflict 标签清理与审计照旧。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        a = e.add_context("情境辛 冲突甲", importance=0.5)
        b = e.add_context("情境壬 冲突乙", importance=0.5)
        e.store.register_conflict(a.id, b.id)
        peer_tags = json.loads(e.store.conn.execute(
            "SELECT tags FROM nodes WHERE id=?", (b.id,)).fetchone()[0])
        assert "conflict" in peer_tags, "E 前置：peer 应带 conflict 标签，实测 %r" % (peer_tags,)

        assert e.store.delete_node(a.id) is True, "E 前置：删除应成功"
        after_tags = json.loads(e.store.conn.execute(
            "SELECT tags FROM nodes WHERE id=?", (b.id,)).fetchone()[0])
        assert "conflict" not in after_tags, (
            "#720 修法把 #115 的 conflict 标签清理吞掉了：%r" % (after_tags,))
        n = e.store.conn.execute(
            "SELECT COUNT(*) FROM action_logs WHERE action_type='conflict_tag_cleared'"
        ).fetchone()[0]
        assert n >= 1, "#720 修法把 #115 的 conflict_tag_cleared 审计吞掉了"
    finally:
        e.close()
