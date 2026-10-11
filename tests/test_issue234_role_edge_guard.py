# -*- coding: utf-8 -*-
"""#234 边、标签、保护、冲突登记不做角色检查 —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #234，判定「成立」）：
  `Role` 约定「SUB 仅可写本地层」，`LayeredStore` docstring 亦写明共享层（锚点/结构）
  「跨实例同步、不可遗忘」；但角色检查只在 `add_node`。边表、标签、保护、冲突登记的
  写接口都不看 `self.role`：
    ① SUB 可在两个结构层节点间 `add_edge` 再 `verify_edge`——`decay_cycle` 跳过两端属
       anchor/structure 的边 ⇒ 该边**永不衰减**，立即参与 PRIMARY 的因果推理；
    ② `add_edge` 是 `INSERT OR REPLACE`，SUB 用同 id 可改写 PRIMARY 的共享层边；
    ③ 先把边一端改挂到 SUB 的知识层节点、再删该节点，级联删掉 PRIMARY 的共享层边；
    ④ `tag_node` / `protect_node` / `register_conflict` 可对结构层节点打标签（伪造
       `promotion_pending`/`conflict`）、登记保护。

修法（最小 · 按 issue「建议修复 1/2/4」）：
  · `add_edge`：非 PRIMARY 时先判新边两端 layer（共享层即拒），并查**同 id 旧边**端点；
  · `verify_edge`：非 PRIMARY 时判该边两端 layer；
  · `tag_node` / `protect_node` / `register_conflict`：非 PRIMARY 时对共享层节点拒绝。
  统一走 `_assert_edge_role`（边）与内联节点判据，判据集合 = `SHARED_LAYERS`
  （anchor/structure）。

判据来源：公开 issue #234「期望行为：非 PRIMARY 角色对任一端属于 `IMMUTABLE_LAYERS`
  的边做新增、修改、verify、删除，都应得到 `PermissionError`；共享层节点的标签、保护、
  冲突登记也只允许 PRIMARY 修改」＋「建议修复 1/2/4」＋ `Role` 注释（core.py:631-632）。
边界（削掉的判别力，如实标注）：边闸用 `SHARED_LAYERS` 而非 issue 字面的
  `IMMUTABLE_LAYERS`——#191 已裁定 SELF 属**本地层**（SUB 可写），按 `IMMUTABLE_LAYERS`
  收口会回归 #191 的 SUB `update_self` 自我快照路径；故按仓内既有「写权限 = 共享层」
  单一真源收口，**不对 SELF 端点的边做角色限制**。issue 建议 3（`delete_node` 级联删边
  前判对端层）**未单列**：改挂面已在 `add_edge` 堵死，且共享层节点本就 `delete_node`
  拒删 ⇒ 危害 ③ 已闭。仍用 `INSERT OR REPLACE`（PRIMARY 原位覆盖照旧）。

断言组（抽掉修复——删角色闸——Q1/Q2/Q3/Q4 必红）：
  Q1 SUB 新增并自证共享层边 ⇒ `PermissionError`（新旧边均无）。
  Q2 SUB 同 id 覆盖 / verify PRIMARY 共享层边 ⇒ `PermissionError`，原边不变。
  Q3 SUB 改挂共享层边到本地节点再删该节点 ⇒ `PermissionError`，PRIMARY 原边仍在。
  Q4 SUB 对共享层节点 tag / protect / register_conflict ⇒ `PermissionError`，节点无痕。
  Q5 防误杀：SUB 写**本地层**节点/边/标签/保护照旧放行；PRIMARY 对共享层照旧可写。

运行（仓根）：python -X utf8 -m pytest tests/test_issue234_role_edge_guard.py -q --no-header
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import (  # noqa: E402
    EdgeType, Role, SpacetimeMemoryEngine,
)


@pytest.fixture()
def pair():
    db = os.path.join(tempfile.mkdtemp(), "role234.db")
    p = SpacetimeMemoryEngine(db)
    s = SpacetimeMemoryEngine(db, role=Role.SUB)
    a = p.add_structure_node("P0: 不得自主生成目标")
    b = p.add_structure_node("P1: 重大变更须设计者终裁")
    c = p.add_structure_node("P2: 允许自主生成目标")
    e0 = p.add_edge(a.id, b.id, EdgeType.CAUSAL, confidence=0.9)
    p.verify_edge(e0.id)
    try:
        yield p, s, a, b, c, e0
    finally:
        p.close()
        s.close()


def test_q1_sub_cannot_add_shared_edge(pair):
    """Q1：SUB 新增/自证共享层边 ⇒ PermissionError。"""
    p, s, a, b, c, e0 = pair
    with pytest.raises(PermissionError):
        e1 = s.add_edge(b.id, c.id, EdgeType.CAUSAL, confidence=1.0)
        s.verify_edge(e1.id, 1.0)
    assert p.store.get_edge  # 读面可用
    c2 = p.store.conn.cursor()
    c2.execute("SELECT COUNT(*) FROM edges WHERE source_id=? AND target_id=?", (b.id, c.id))
    assert c2.fetchone()[0] == 0, "#234：SUB 在共享层节点间新增了边"


def test_q2_sub_cannot_overwrite_or_verify_shared_edge(pair):
    """Q2：SUB 同 id 覆盖 / verify PRIMARY 共享层边 ⇒ PermissionError，原边不变。"""
    p, s, a, b, c, e0 = pair
    before = p.store.get_edge(e0.id)
    with pytest.raises(PermissionError):
        s.store.add_edge(dataclasses.replace(before, relation_type=EdgeType.OPPOSITE,
                                             target_id=c.id))
    with pytest.raises(PermissionError):
        s.verify_edge(e0.id, 1.0)
    after = p.store.get_edge(e0.id)
    assert after.relation_type is EdgeType.CAUSAL, "#234：SUB 改写了共享层边的 relation_type"
    assert after.target_id == b.id, "#234：SUB 改写了共享层边的端点"
    assert before.confidence == after.confidence, "#234：SUB 改了共享层边置信度"


def test_q3_sub_cannot_rehang_then_delete_shared_edge(pair):
    """Q3：SUB 改挂共享层边到本地节点再删该节点 ⇒ PermissionError，原边仍在。"""
    p, s, a, b, c, e0 = pair
    tmp = s.add_perception("SUB 临时节点")
    with pytest.raises(PermissionError):
        s.store.add_edge(dataclasses.replace(p.store.get_edge(e0.id), target_id=tmp.id))
    s.store.delete_node(tmp.id)
    assert p.store.get_edge(e0.id) is not None, "#234：PRIMARY 的共享层边被级联删除"


def test_q4_sub_cannot_tag_protect_conflict_shared_node(pair):
    """Q4：SUB 对共享层节点 tag / protect / register_conflict ⇒ PermissionError。"""
    p, s, a, b, c, e0 = pair
    with pytest.raises(PermissionError):
        s.store.tag_node(a.id, "promotion_pending")
    with pytest.raises(PermissionError):
        s.protect_node(c.id, "SUB 自行登记")
    with pytest.raises(PermissionError):
        s.register_conflict(a.id, b.id)
    assert p.store.get_node(a.id).tags == ["structure"], (
        "#234：共享层节点被 SUB 打了标签——tags=%r" % p.store.get_node(a.id).tags)
    assert c.id not in p.store.get_protected_nodes(), "#234：共享层节点被 SUB 登记保护"


def test_q5_local_layer_still_writable(pair):
    """Q5：防误杀——SUB 写本地层节点/边/标签/保护照旧放行；PRIMARY 共享层照旧可写。"""
    p, s, a, b, c, e0 = pair
    k1 = s.add_perception("SUB 本地知识 1")
    k2 = s.add_perception("SUB 本地知识 2")
    e = s.add_edge(k1.id, k2.id, EdgeType.CAUSAL)
    s.store.tag_node(k1.id, "local_tag")
    s.protect_node(k1.id, "SUB 自保")
    assert p.store.get_edge(e.id) is not None, "SUB 本地层边未落库"
    assert "local_tag" in p.store.get_node(k1.id).tags
    assert k1.id in p.store.get_protected_nodes()
    # PRIMARY 对共享层照旧可写
    p.store.tag_node(a.id, "primary_tag")
    p.protect_node(c.id, "PRIMARY 登记")
    assert "primary_tag" in p.store.get_node(a.id).tags
    assert c.id in p.store.get_protected_nodes()
