# -*- coding: utf-8 -*-
"""#659 守卫：`add_node` 同 id 同层原位覆盖不得静默抹掉 `no_forget` 保护标记。

缺陷态（修复前 main，issue #659 复现实测）：
  `LayeredStore.add_node` 对已存在同 id 同层行走 `INSERT OR REPLACE` **整行替换**；`#127`
  的闸只拦「换层」与「旧行属共享层且非 PRIMARY」，**同层覆盖完全放行** ⇒ 新行漏带 tags 时
  旧行的 `no_forget` 标签被静默抹掉。`no_forget` ∈ tags 是 `_is_forget_protected`（#36 自述
  「唯一真源」）的两个真源之一 ⇒ 下一步 `decay_cycle` 按设计把「不可遗忘」节点正常删除。
  跨角色同库文件：SUB 也能一步覆盖 PRIMARY 的受保护节点（无 PermissionError、无审计）。

修法（落点 `lingshu/core/core.py`，温和版）：
  `add_node` 命中同 id 同层时——
  ① 旧行 tags 含 `no_forget` ⇒ **并入**新行 tags（保护标记不随覆盖消失）；
  ② 旧行受保护（`_is_forget_protected`：`no_forget` 标签 ∪ `protections` 行登记）⇒ 落一条
     `action_logs`（`action_type='protected_node_overwritten'`），使「受保护节点被改写」不再静默。
  **不**改成「拒绝覆写受保护节点」——那会打掉 `apply_shared_sync(overwrite=True)` 这条合法
  原地更新路径（该路径走 `store.add_node`）。取舍如实标注在提交正文与回帖稿。

组（回退即红）：
  G1 同 id 同层覆盖带 `no_forget` 的节点 ⇒ 覆盖后 tags 仍含 `no_forget`，且落审计行。
  G2 覆盖后 `decay_cycle` **不**删除该节点（保护语义仍生效）。
  G3 覆盖带 `protections` 行的节点 ⇒ 覆盖后 `protections` 行仍在、`delete_node` 仍拒删、
     且落审计（保护对象/理由不再错位且**可观测**）。
  G4 普通（无保护）节点覆盖行为**逐位不变**（tags 不被注入、无审计行）——防误杀正对照。
  G5 跨角色：SUB 覆盖 PRIMARY 同库的受保护节点 ⇒ `no_forget` 仍在、审计行在。
  G6 文案/行为可判：审计 `summary` 含节点 id 与「受保护」字样，`outcome.old_tags` 记旧标签。

判别力（变异自证）：把 `add_node` 里 #659 的「并入 no_forget ＋ 落审计」段整体摘掉 ⇒ G1/G2/G3/G5
  必红；还原 ⇒ 全绿、`git diff` 为空。

运行：python -X utf8 -m pytest tests/test_issue659_protected_overwrite_guard.py -q
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from lingshu.core.core import (
    ConditionSpace, LayeredStore, MemoryLayer, Role, STNode, SpacetimeMemoryEngine,
)

_CS = ConditionSpace("守卫", "守卫", (time.time(), time.time() + 3600), "运行中")


def _node(nid, content, layer=MemoryLayer.KNOWLEDGE, tags=None, importance=0.5):
    return STNode(id=nid, content=content, modality="text",
                  spatial_coordinates={}, temporal_coordinate=0.0,
                  condition_space=_CS, layer=layer, importance=importance,
                  tags=list(tags or []))


def _audit_count(store, nid):
    c = store.conn.cursor()
    c.execute("SELECT COUNT(*) FROM action_logs WHERE action_type='protected_node_overwritten'"
              " AND node_ids LIKE ?", ("%" + nid + "%",))
    return c.fetchone()[0]


# ------------------------------------------------------------- G1 标签保真＋审计

def test_g1_overwrite_keeps_no_forget_and_audits():
    s = LayeredStore(":memory:")
    s.add_node(_node("keep1", "重要事实", tags=["no_forget"]))
    s.add_node(_node("keep1", "意外重写（漏带 tags）"))     # 同 id 同层：放行
    got = s.get_node("keep1")
    assert got is not None and "no_forget" in got.tags, (
        "#659：同 id 同层覆盖把 no_forget 标签静默抹掉了——tags=%r" % (got.tags if got else None))
    assert got.content == "意外重写（漏带 tags）", "内容仍按调用方传入落库（本件不改内容口径）"
    assert _audit_count(s, "keep1") >= 1, "#659：受保护节点被改写却无审计行"
    s.close()


# ------------------------------------------------------------- G2 保护语义仍生效

def test_g2_protected_node_survives_decay_after_overwrite():
    eng = SpacetimeMemoryEngine(":memory:")
    eng.store.add_node(_node("keep2", "不可遗忘事实", layer=MemoryLayer.CONTEXT,
                             tags=["no_forget"], importance=0.1))
    eng.store.add_node(_node("keep2", "重写后内容", layer=MemoryLayer.CONTEXT,
                             importance=0.1))
    eng.store.decay_cycle(factor=0.5, min_confidence=0.3)
    assert eng.store.get_node("keep2") is not None, (
        "#659：覆盖后 no_forget 丢失，decay_cycle 把「不可遗忘」节点删除了")
    eng.close()


# ------------------------------------------------------------- G3 protections 行面

def test_g3_protections_row_node_overwrite_still_protected_and_audited():
    s = LayeredStore(":memory:")
    s.add_node(_node("pk1", "关键证据"))
    s.protect_node("pk1", "P0")
    s.add_node(_node("pk1", "SUB 改写受保护节点"))       # 同 id 同层覆盖
    n = s.get_node("pk1")
    assert n is not None and n.content == "SUB 改写受保护节点"
    assert "pk1" in s.get_protected_nodes(), "#659：protections 行被覆盖抹掉"
    assert s.delete_node("pk1") is False, "#659：受保护节点被 delete_node 删除"
    assert _audit_count(s, "pk1") >= 1, "#659：受保护节点被改写却无审计行"
    s.close()


# ------------------------------------------------------------- G4 防误杀正对照

def test_g4_plain_node_overwrite_unchanged_no_injected_tags():
    s = LayeredStore(":memory:")
    s.add_node(_node("plain1", "普通节点甲", tags=["ordinary"]))
    s.add_node(_node("plain1", "普通节点乙", tags=["ordinary"]))
    n = s.get_node("plain1")
    assert n.tags == ["ordinary"], "#659：普通节点覆盖被注入了 no_forget（过修）"
    assert n.content == "普通节点乙"
    assert _audit_count(s, "plain1") == 0, "#659：普通节点覆盖被误记审计"
    # 全新节点写入亦不受影响
    s.add_node(_node("plain2", "全新节点", tags=["x"]))
    assert s.get_node("plain2").tags == ["x"]
    s.close()


# ------------------------------------------------------------- G5 跨角色

def test_g5_sub_overwrite_primary_protected_node_keeps_tag():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "cross659.db")
    p = SpacetimeMemoryEngine(db)
    p.store.add_node(_node("pk_cross", "PRIMARY 建的受保护节点",
                           layer=MemoryLayer.KNOWLEDGE, tags=["no_forget"]))
    s = SpacetimeMemoryEngine(db, role=Role.SUB)
    try:
        s.store.add_node(_node("pk_cross", "SUB 改写内容", layer=MemoryLayer.KNOWLEDGE))
        got = p.store.get_node("pk_cross")
        assert got is not None and "no_forget" in got.tags, (
            "#659：SUB 覆盖 PRIMARY 受保护节点后 no_forget 丢失")
        assert _audit_count(p.store, "pk_cross") >= 1, "#659：跨角色覆盖无审计"
    finally:
        p.close()
        s.close()


# ------------------------------------------------------------- G6 文案可判

def test_g6_audit_summary_and_outcome_are_judgeable():
    s = LayeredStore(":memory:")
    s.add_node(_node("keep6", "审计文案节点", tags=["no_forget"]))
    s.add_node(_node("keep6", "二次写入"))
    c = s.conn.cursor()
    c.execute("SELECT summary, outcome FROM action_logs"
              " WHERE action_type='protected_node_overwritten'")
    rows = c.fetchall()
    assert rows, "#659：缺审计行"
    summary, outcome = rows[-1]
    assert "keep6" in summary and "受保护" in summary, (
        "#659 审计文案不可判：%r" % summary)
    assert "no_forget" in outcome, "#659 审计 outcome 未记旧标签：%r" % outcome
    s.close()
