# -*- coding: utf-8 -*-
"""#115 冲突（OPPOSITE）边生命周期守卫 —— lingshu/core/core.py

缺陷（lingshu issue #115 · 判定成立）：
  `LayeredStore.register_conflict` 建的 OPPOSITE 边是 `verified=False`、`confidence=0.3`
  的普通未验证边 ⇒ `decay_cycle` 的边衰减查询（`WHERE e.verified = 0`）把它一并纳入
  ⇒ 约 55 轮后 confidence < min_confidence(0.1) 被 `DELETE FROM edges`，**冲突关系静默
  消失**；而 `register_conflict` 打在双方节点上的 `conflict` 标签**永不移除**
  （全仓无任何移除点）⇒ 标签与边生命周期断裂（标签成"孤儿"，边没了标签还在）。

修法（最小 · 两处）：
  ① `decay_cycle` 的边衰减查询排除 `relation_type='opposite'`（免疫普通衰减）；
  ② `delete_node` 删节点时，对**因本次删除而失去全部 OPPOSITE 边**的对端节点移除
     `conflict` 标签，并落一条 `action_logs` 审计（B-5：冲突边消失须留痕）。

判据来源：
  · `docs/plans/待裁清单_v0.1.md` **D-38**「OPPOSITE 边是否免疫普通衰减 ⇒ 免疫（直到
    结案）」＋ **D-37 / B-4**「冲突标签生命周期 ⇒ 随 OPPOSITE 边消失而移除」
    ＋ **D-39 / B-5**「冲突边被删/结案后留审计」。
  · 理论章节追不到（仓内无规定冲突边生命周期的理论稿）；本条为待裁清单裁定口径。

断言组（抽掉修复即红）：
  A 建边形态：`register_conflict` 建 OPPOSITE、`verified=False`、`confidence=0.3`，
    双方节点均带 `conflict` 标签。
  B 免疫衰减（核心）：200 轮 `decay_cycle` 后 OPPOSITE 边**仍在**、confidence 未变；
    对照：同库一条普通未验证边（同一衰减轮数）**已被删除**（证明衰减确实跑了）。
  C 删除期标签清理：`delete_node(a)` ⇒ OPPOSITE 边消失、`b` 的 `conflict` 标签被移除、
    且 `action_logs` 新增一条 `conflict_tag_cleared`（留痕可查）。
  D 防误杀：`b` 同时与 `c` 存在冲突边时，删除 `a` **不得**移除 `b` 的 `conflict` 标签；
    无关标签（如 `no_forget` 之外的普通标签）不被误删。
  E 静态有牙：AST 扫 `decay_cycle` 的 SQL 字面量必须含 `relation_type != ?` 之类的
    冲突边排除判据（注入一条无排除的查询即判红，自证有牙）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue115_conflict_edge_lifecycle_guard.py -q --no-header
"""
from __future__ import annotations

import ast
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    ConditionSpace, EdgeType, SpacetimeMemoryEngine, STEdge, STNode,
)

ROUNDS = 200  # 0.3·0.98^n < 0.1 ⇒ n≈55 轮即被删（缺陷形态）；200 轮留足余量


def _pair(engine, ca="命题甲", cb="命题乙"):
    a = engine.add_perception(ca, skip_dedup=True)
    b = engine.add_perception(cb, skip_dedup=True)
    return a, b


def _tags(engine, nid):
    return list(engine.store.get_node(nid).tags or [])


def _audit_types(engine):
    c = engine.store.conn.cursor()
    c.execute("SELECT action_type FROM action_logs ORDER BY id")
    return [r[0] for r in c.fetchall()]


# ---------- A 建边形态 ----------

def test_a_conflict_edge_shape_and_tags():
    e = SpacetimeMemoryEngine(":memory:")
    try:
        a, b = _pair(e)
        edge = e.store.register_conflict(a.id, b.id)
        assert edge is not None, "A 冲突边未建立"
        assert edge.relation_type is EdgeType.OPPOSITE, "A 关系类型非 OPPOSITE：%r" % (edge.relation_type,)
        assert edge.verified is False, "A 冲突边被标为已验证：%r" % (edge.verified,)
        assert edge.confidence == 0.3, "A 冲突边置信度非 0.3：%r" % (edge.confidence,)
        assert "conflict" in _tags(e, a.id) and "conflict" in _tags(e, b.id), \
            "A 双方节点未带 conflict 标签：%r / %r" % (_tags(e, a.id), _tags(e, b.id))
    finally:
        e.close()


# ---------- B 免疫衰减（核心） ----------

def test_b_opposite_edge_survives_decay_while_ordinary_edge_dies():
    """核心：冲突边免疫普通衰减；普通未验证边照旧衰减至删除（证明衰减真跑了）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        a, b = _pair(e)
        edge = e.store.register_conflict(a.id, b.id)

        # 对照：同库一条普通未验证边（同类节点、同 layer 面），必须仍被衰减删除。
        c1 = e.add_perception("普通源", skip_dedup=True)
        c2 = e.add_perception("普通目标", skip_dedup=True)
        ordinary = STEdge(
            id="edge_ordinary_guard", source_id=c1.id, target_id=c2.id,
            relation_type=EdgeType.CAUSAL,
            condition_space=ConditionSpace("对照", "对照", (0.0, 1e12), "运行中"),
            confidence=0.3, verified=False)
        e.store.add_edge(ordinary)

        for _ in range(ROUNDS):
            e.store.decay_cycle()

        got = e.store.get_edge(edge.id)
        assert got is not None, (
            "B 冲突边被普通衰减删除（缺陷形态：约 %d 轮后 confidence<0.1 即 DELETE）" % ROUNDS)
        assert got.confidence == 0.3, "B 冲突边置信度被衰减：%r" % (got.confidence,)
        assert e.store.get_edge(ordinary.id) is None, (
            "B 对照边未被衰减删除——衰减循环未真正生效，本守卫失去判别力")
    finally:
        e.close()


# ---------- C 删除期标签清理 + 审计 ----------

def test_c_delete_node_clears_peer_conflict_tag_and_audits():
    e = SpacetimeMemoryEngine(":memory:")
    try:
        a, b = _pair(e)
        edge = e.store.register_conflict(a.id, b.id)
        assert "conflict" in _tags(e, b.id)
        before = _audit_types(e)

        assert e.store.delete_node(a.id) is True, "C 删除未成功"
        assert e.store.get_edge(edge.id) is None, "C 冲突边未随节点删除而消失"
        assert "conflict" not in _tags(e, b.id), (
            "C 冲突边已消失但 b 的 conflict 标签残留（缺陷形态：标签永不移除）：%r"
            % (_tags(e, b.id),))
        after = _audit_types(e)
        assert after[len(before):].count("conflict_tag_cleared") == 1, (
            "C 未落冲突标签清理审计（B-5）：新增 %r" % (after[len(before):],))
    finally:
        e.close()


# ---------- D 防误杀 ----------

def test_d_peer_with_other_conflict_keeps_tag_and_other_tags_survive():
    """b 与 c 另有冲突边 ⇒ 删 a 不得移除 b 的 conflict 标签；无关标签不被误删。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        a = e.add_perception("命题甲", skip_dedup=True)
        b = e.add_perception("命题乙", skip_dedup=True)
        c = e.add_perception("命题丙", skip_dedup=True)
        e.store.register_conflict(a.id, b.id)
        e.store.register_conflict(b.id, c.id)
        e.store.tag_node(b.id, "keep_me")

        assert e.store.delete_node(a.id) is True
        tags = _tags(e, b.id)
        assert "conflict" in tags, (
            "D 对端仍有其它冲突边，conflict 标签被误删：%r" % (tags,))
        assert "keep_me" in tags, "D 无关标签被误删：%r" % (tags,)
        # c 仍与 b 冲突 ⇒ c 的标签也保留
        assert "conflict" in _tags(e, c.id), "D 无关节点标签被误删：%r" % (_tags(e, c.id),)
    finally:
        e.close()


# ---------- E 静态有牙 ----------

def _decay_sql_literals(tree):
    """取 decay_cycle 内所有 SQL 字符串字面量。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decay_cycle":
            return [n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return None


def _has_conflict_exclusion(literals):
    """SQL 字面量里是否含冲突边排除判据（relation_type 与 opposite/占位符）。"""
    for s in literals or ():
        low = s.lower()
        if "relation_type" in low and ("!=" in low or "not in" in low or "opposite" in low):
            return True
    return False


def test_e_decay_query_has_conflict_exclusion():
    src = open(os.path.join(REPO, "lingshu", "core", "core.py"), encoding="utf-8").read()
    lits = _decay_sql_literals(ast.parse(src))
    assert lits is not None, "E 未定位到 decay_cycle（AST 扫描面为空）"
    assert _has_conflict_exclusion(lits), (
        "E decay_cycle 的边衰减查询无 OPPOSITE 排除判据（缺陷形态）")
    # 有牙自证：注入一条无排除的边衰减查询 ⇒ 判据必须判为「无排除」
    probe = ast.parse(
        "def decay_cycle(self):\n"
        "    c.execute('SELECT e.* FROM edges e WHERE e.verified = 0')\n")
    assert not _has_conflict_exclusion(_decay_sql_literals(probe)), \
        "E 有牙自证失败：无排除判据的查询被判为「有排除」"
