# -*- coding: utf-8 -*-
"""#205 decay_cycle 边衰减的不可变层排除硬编码、漏 SELF —— 守卫（lingshu/core/core.py）

缺陷（分诊表 `triage_lingshu_core_front.md` 行 205 判定「成立」）：
  `LayeredStore.decay_cycle` 的未验证边衰减查询把不可变层端点**硬编码**成
  `sn.layer NOT IN ('anchor', 'structure') AND tn.layer NOT IN ('anchor', 'structure')`，
  而模块真源 `IMMUTABLE_LAYERS` 是 `{ANCHOR, STRUCTURE, SELF}`（core.py:723-724，
  「SELF 层=自我锚点（扮演依据）不可遗忘」）⇒ SELF 端点不在排除集内。
  `update_self` 建的「自我快照 → 经历」因果边（`add_edge(..., confidence=1.0)`
  但 `verified=False`，core.py:3591）因此走常规衰减，约 114 轮后被
  `DELETE FROM edges` 删除；而两端节点都还在（SELF 层 `delete_node` 拒删）
  ⇒ 自我与经历的来源关系被**静默剥光**。

修法（最小 · 单一真源）：排除集改取 `IMMUTABLE_LAYERS`（SQL 占位符由该集合生成），
不再写字面量 `'anchor','structure'`。

判据来源：`IMMUTABLE_LAYERS` 定义处注释（core.py:723-724）
＋ 本函数 docstring「锚点层和结构层的节点不受影响（其关联边也不衰减）」的同款语义
＋ 分诊表行 205（经验标定，理论章节追不到）。
边界（削掉的判别力，如实标注）：本件只让**不可变层端点的边**不被衰减删除；
这些边仍随**对端节点**在情境层自然遗忘/FIFO 淘汰中被整条删除（经
`_strip_incident_edges` 落审计，见 #168）——即「边免疫衰减」不等于「边永不消失」。

断言组（把排除集改回字面量 `('anchor','structure')` ⇒ Q1/Q2 必红）：
  Q1 SELF→知识 的未验证边经多轮衰减后**仍在**，且置信度未被下调。
  Q2 锚点/结构端点同样被排除（三不可变层一致，防只补 SELF 漏其它）。
  Q3 防误杀：知识↔知识 的未验证边**照常**被衰减删除（本件不放宽普通遗忘面）。
  Q4 端到端：SELF 节点与其经历节点在多轮衰减后**都在**，且边也在。

运行（仓根）：python -X utf8 -m pytest tests/test_issue205_decay_immutable_edge_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import (  # noqa: E402
    SpacetimeMemoryEngine, MemoryLayer, ConditionSpace, STNode,
)


def _self_node_id(e):
    c = e.store.conn.cursor()
    c.execute("SELECT id FROM nodes WHERE layer='self'")
    return c.fetchone()[0]


def _decay(e, rounds=6, factor=0.3):
    for _ in range(rounds):
        e.store.decay_cycle(factor=factor)


def test_q1_self_endpoint_edge_survives_decay():
    """Q1：SELF→知识 的未验证边不得被衰减删除（旧：数轮即被 DELETE）。"""
    e = SpacetimeMemoryEngine(":memory:")
    e.update_self({"current_goal": "自我锚点"})
    sid = _self_node_id(e)
    mem = e.add_perception("一段经历", skip_dedup=True)
    ed = e.add_edge(sid, mem.id, confidence=0.2)
    before = e.store.get_edge(ed.id).confidence
    _decay(e)
    got = e.store.get_edge(ed.id)
    assert got is not None, "SELF→经历 的边被衰减删除（不可变层排除漏 SELF）"
    assert got.confidence == before, (
        "SELF 端点边的置信度被衰减：%s -> %s" % (before, got.confidence))


def test_q2_anchor_and_structure_endpoints_also_excluded():
    """Q2：锚点/结构端点同被排除（三不可变层一致）。"""
    e = SpacetimeMemoryEngine(":memory:")
    anchor = e.set_anchor("锚点")
    struct = e.add_structure_node("结构节点")
    m1 = e.add_perception("经历一", skip_dedup=True)
    m2 = e.add_perception("经历二", skip_dedup=True)
    ea = e.add_edge(anchor.id, m1.id, confidence=0.2)
    es = e.add_edge(struct.id, m2.id, confidence=0.2)
    _decay(e)
    assert e.store.get_edge(ea.id) is not None, "锚点端点边被衰减删除"
    assert e.store.get_edge(es.id) is not None, "结构端点边被衰减删除"
    assert set(e.store.IMMUTABLE_LAYERS) == {
        MemoryLayer.ANCHOR, MemoryLayer.STRUCTURE, MemoryLayer.SELF}, (
        "IMMUTABLE_LAYERS 集合与守卫预期不符（真源被改动）")


def test_q3_normal_edges_still_decay():
    """Q3：防误杀——知识↔知识 未验证边照常衰减删除。"""
    e = SpacetimeMemoryEngine(":memory:")
    k1 = e.add_perception("k1", skip_dedup=True)
    k2 = e.add_perception("k2", skip_dedup=True)
    ed = e.add_edge(k1.id, k2.id, confidence=0.2)
    _decay(e)
    assert e.store.get_edge(ed.id) is None, "普通未验证边不再衰减（遗忘面被放宽）"


def test_q4_self_and_memory_both_survive_with_edge():
    """Q4：端到端——SELF 节点、经历节点、边三者多轮后都在。"""
    e = SpacetimeMemoryEngine(":memory:")
    e.update_self({"current_goal": "端到端"})
    sid = _self_node_id(e)
    mem = e.add_perception("端到端经历", skip_dedup=True)
    ed = e.add_edge(sid, mem.id, confidence=0.2)
    _decay(e)
    assert e.store.get_node(sid) is not None, "SELF 节点消失"
    assert e.store.get_node(mem.id) is not None, "经历节点消失"
    assert e.store.get_edge(ed.id) is not None, "来源关系被剥光"
