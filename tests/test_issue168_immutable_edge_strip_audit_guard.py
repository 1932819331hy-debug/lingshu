# -*- coding: utf-8 -*-
"""#168 不可变层节点的关联边被常规遗忘静默剥光 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #168 · 判定成立，实读复现见下）：
  `decay_cycle` docstring 声称「锚点层和结构层的节点不受影响（其关联边也不衰减）」，
  但该说法只对**置信度衰减**成立。结构层/锚点层节点的关联边仍会随**对端节点**
  在情境层自然遗忘（`decay_cycle` 情境层删除段）或 `enforce_context_cap` FIFO
  （经 `delete_node`）中被 `DELETE FROM edges WHERE source_id=? OR target_id=?`
  整条删除，且**无日志无审计** ⇒ 不可变层节点的因果关系在常规运行中被静默剥光。
  实测（HEAD，修前）：结构层节点入边 1 → 删情境端后 0，`action_logs` 无任何记录。

修法（最小 · 收口唯一真源）：新增 `LayeredStore._strip_incident_edges(c, node_id)`，
  在删边前对「对端 ∈ `IMMUTABLE_LAYERS`」的每条边落一条 `immutable_edge_stripped`
  审计；三处删边路径（`delete_node` / `decay_cycle` 情境段 / `_trim_self_snapshots`）
  统一改调该助手。**不**改变删边语义（保留边会造悬空引用、破坏 `verify_integrity`）。

判据来源：分诊表 `.tmp/triage_lingshu_ALL.md` 行 168（与 `triage_round2.md` §3.2
  同条）；仓内无规定该留痕面的理论章节，追不到更早出处（经验标定，本件 #168）。
边界（削掉的判别力）：本件只把「静默」改为「留痕」，**不**阻止不可变层节点失去
  关联边（删对端是合法遗忘路径）；`forget_advisor` 的 archived 降权路径不改。

断言组（抽掉 `_strip_incident_edges` 里的审计 INSERT 即 A/B 变红）：
  A `delete_node` 路径：删情境端后不可变层节点失去入边 ⇒ 有 `immutable_edge_stripped` 审计。
  B `decay_cycle` 情境段路径：同上（节点被自然遗忘删除）。
  C 防误杀：两端**均非**不可变层的删边**不**产生该审计（留痕面不扩散）。
  D 防误杀：删边语义不变（边确实消失，不得为留痕而保留悬空边）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue168_immutable_edge_strip_audit_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine, EdgeType  # noqa: E402


def _db(name):
    return os.path.join(tempfile.mkdtemp(prefix="lingshu168_"), name + ".db")


def _audit_count(e, action_type="immutable_edge_stripped"):
    c = e.store.conn.cursor()
    c.execute("SELECT COUNT(*) FROM action_logs WHERE action_type=?", (action_type,))
    return c.fetchone()[0]


def test_a_delete_node_path_audited():
    e = SpacetimeMemoryEngine(_db("delete"))
    s = e.add_structure_node("结构层规定 A", importance=1.0)
    ctx = e.add_context("情境记忆 1", importance=0.5)
    e.add_edge(ctx.id, s.id, EdgeType.CAUSAL, confidence=0.7)
    assert len(e.store.get_incoming_edges(s.id)) == 1
    assert e.store.delete_node(ctx.id) is True
    assert len(e.store.get_incoming_edges(s.id)) == 0, "前置：边应已随节点删除而消失"
    assert _audit_count(e) >= 1, "不可变层节点失去关联边却无审计（静默剥光）"


def test_b_decay_cycle_path_audited():
    e = SpacetimeMemoryEngine(_db("decay"))
    s = e.add_structure_node("结构层规定 B", importance=1.0)
    ctx = e.add_context("情境记忆 2", importance=0.15)
    e.add_edge(ctx.id, s.id, EdgeType.CAUSAL, confidence=0.7)
    e.decay_cycle(factor=0.9, min_confidence=0.1)
    assert e.store.get_node(ctx.id) is None, "前置：情境端应已被自然遗忘删除"
    assert len(e.store.get_incoming_edges(s.id)) == 0
    assert _audit_count(e) >= 1, "情境层自然遗忘剥光不可变层关联边却无审计"


def test_c_non_immutable_peer_not_audited():
    """防误杀：两端均非不可变层 ⇒ 不留该审计（留痕面不扩散）。"""
    e = SpacetimeMemoryEngine(_db("plain"))
    a = e.add_context("情境 A", importance=0.5)
    b = e.add_context("情境 B", importance=0.5)
    e.add_edge(a.id, b.id, EdgeType.SEQUENTIAL, confidence=0.7)
    e.store.delete_node(a.id)
    assert _audit_count(e) == 0, "非不可变层对端的删边不应产生 immutable_edge_stripped"


def test_d_edge_still_removed():
    """防误杀：删边语义不变（不得为留痕而保留悬空边）。"""
    e = SpacetimeMemoryEngine(_db("removed"))
    s = e.add_structure_node("结构层规定 C", importance=1.0)
    ctx = e.add_context("情境记忆 3", importance=0.5)
    e.add_edge(ctx.id, s.id, EdgeType.CAUSAL, confidence=0.7)
    e.store.delete_node(ctx.id)
    c = e.store.conn.cursor()
    c.execute("SELECT COUNT(*) FROM edges WHERE source_id=? OR target_id=?", (ctx.id, ctx.id))
    assert c.fetchone()[0] == 0, "边未删除（悬空引用）"
