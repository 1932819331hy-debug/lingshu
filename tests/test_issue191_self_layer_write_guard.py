# -*- coding: utf-8 -*-
"""#191 SELF 层被并入 IMMUTABLE_LAYERS ⇒ 写权限闸误伤本地层 —— 守卫
（lingshu/core/core.py）

缺陷（分诊表 `triage_lingshu_core_front.md` 行 191 判定「成立」）：
  `LayeredStore.IMMUTABLE_LAYERS` 含 `MemoryLayer.SELF`（v1.16 扮演论：自我层
  在**遗忘面**不可删），而 `add_node` 的**写权限闸**直接复用了同一个集合：
      `if node.layer in self.IMMUTABLE_LAYERS and self.role != Role.PRIMARY: raise`
  ⇒ `Role.SUB` 写自我层 100% `PermissionError`。后果（实读复现）：
  `SpacetimeMemoryEngine(role=Role.SUB).update_self({...})` 先改内存
  `self_model`（`self_model.update(**updates)`，core.py:3568），随后在快照落库处
  （`self.store.add_node(snapshot)`，core.py:3587）抛 `PermissionError`
  ⇒ **内存已改、快照 0 条**，静默分叉。

判据来源（两侧同源，非经验标定）：
  · `Role.SUB` 注释（core.py:585）：「子节点/辅助实例：仅本地层（知识层、情境层、
    **自我层**）」——自我层明确属 SUB 可写的本地层。
  · `LayeredStore` docstring（core.py:708-710）：「共享层（锚点层、结构层）……
    本地层（知识层、情境层、**自我层**）」。
  · `IMMUTABLE_LAYERS` 的语义在 core.py:714 的注释里只声明「不可遗忘」
    （遗忘面），未声明「仅 PRIMARY 可写」。
修法：写权限闸改用新增的 `SHARED_LAYERS = {ANCHOR, STRUCTURE}`；
`IMMUTABLE_LAYERS` 保持含 SELF，供删除/审计/导入闸使用。
边界（削掉的判别力，如实标注）：本件**只**放行「SUB 写自我层」；SELF 仍在
`IMMUTABLE_LAYERS` 内 ⇒ `delete_node` 拒删的遗忘面**未放宽**。副作用：SUB 现可
新建自我层节点（该层不可删），本件不为该层新增配额；自我层堆积的既有界由 #201
的 `SELF_SNAPSHOT_MAX` 管（仅 `self_snapshot` 标签快照），任意 tag 的 SELF 节点
不在本件范围。

断言组（抽掉修复——两处闸改回 `IMMUTABLE_LAYERS`——Q1/Q4 必红）：
  Q1 SUB `update_self` 成功且**内存与落库一致**（self_model 已改 + SELF 快照 ≥1）。
  Q2 SUB `update_trust_state` 路径同样可写自我层（同源缺陷的第二个入口）。
  Q3 共享层闸未松动：SUB 新 id 写 anchor/structure ⇒ PermissionError。
  Q4 遗忘面未放宽：SELF 节点 `delete_node` 仍返回 False（IMMUTABLE 语义保住）。
  Q5 #127 护栏未松动：SUB 同 id 覆盖锚点行 ⇒ PermissionError；#127 的
     「同 id 禁换层」对自我层行仍生效（SUB 把 SELF 行改成 knowledge ⇒ 拒）。
  Q6 防误杀：SUB 写知识层/情境层（本地层）放行（正对照）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue191_self_layer_write_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import (  # noqa: E402
    SpacetimeMemoryEngine, STNode, MemoryLayer, Role, ConditionSpace,
)


def _cs():
    return ConditionSpace("守卫", "守卫", (0.0, 1e12), "运行中")


def _node(nid, layer, content="被改写内容"):
    return STNode(id=nid, content=content, modality="text",
                  spatial_coordinates={}, temporal_coordinate=1.0,
                  condition_space=_cs(), importance=0.1, confidence=0.1,
                  layer=layer, tags=[])


def _self_node_ids(e):
    c = e.store.conn.cursor()
    c.execute("SELECT id FROM nodes WHERE layer='self'")
    return [r[0] for r in c.fetchall()]


def test_q1_sub_update_self_memory_and_store_consistent():
    """Q1：SUB 的 update_self 不抛错，内存已改 **且** SELF 快照落库（旧：抛错、0 条）。"""
    e = SpacetimeMemoryEngine(":memory:", role=Role.SUB)
    e.update_self({"current_goal": "SUB 的自我目标"})
    assert e.get_self_model().current_goal == "SUB 的自我目标", "内存自我模型未改"
    ids = _self_node_ids(e)
    assert ids, "内存已改但 SELF 快照 0 条（#191 的静默分叉仍在）"
    got = e.store.get_node(ids[0])
    assert got.layer is MemoryLayer.SELF, "落库行 layer=%s" % got.layer.value
    assert "SUB 的自我目标" in got.content, "快照内容未含本次更新"


def test_q2_sub_update_trust_state_reaches_self_layer():
    """Q2：同源第二入口——SUB 的 update_trust_state 亦须可写自我层。"""
    e = SpacetimeMemoryEngine(":memory:", role=Role.SUB)
    e.update_trust_state(0.8, 1)
    assert e.get_self_model().trust_state, "信任状态未记录"
    assert _self_node_ids(e), "update_trust_state 未落自我层快照"


def test_q3_shared_layer_write_still_primary_only():
    """Q3：共享层闸未松动——SUB 新 id 写 anchor/structure 仍拒。"""
    e = SpacetimeMemoryEngine(":memory:", role=Role.SUB)
    for i, layer in enumerate((MemoryLayer.ANCHOR, MemoryLayer.STRUCTURE)):
        try:
            e.store.add_node(_node("shared_new_%d" % i, layer))
            raise AssertionError("SUB 新 id 写 %s 未被拒（共享层闸松动）" % layer.value)
        except PermissionError:
            pass


def test_q4_self_layer_still_undeletable():
    """Q4：遗忘面未放宽——SELF 节点 delete_node 仍为 False（IMMUTABLE 语义）。"""
    e = SpacetimeMemoryEngine(":memory:", role=Role.SUB)
    e.update_self({"current_goal": "不可删的自我锚点"})
    sid = _self_node_ids(e)[0]
    assert e.store.delete_node(sid) is False, "SELF 层被放行删除（遗忘面被放宽）"
    assert e.store.get_node(sid) is not None
    assert MemoryLayer.SELF in e.store.IMMUTABLE_LAYERS, "SELF 已移出 IMMUTABLE_LAYERS"


def test_q5_issue127_guard_intact():
    """Q5：#127 护栏未松动——SUB 覆盖锚点行仍拒；同 id 禁换层对 SELF 行仍生效。"""
    e = SpacetimeMemoryEngine(":memory:")          # PRIMARY 建锚点/自我行
    anchor = e.set_anchor("锚点原文")
    e.update_self({"current_goal": "自我锚点"})
    sid = _self_node_ids(e)[0]
    e.store.role = Role.SUB                        # 换 SUB 后再试写
    try:
        e.store.add_node(_node(anchor.id, MemoryLayer.KNOWLEDGE))
        raise AssertionError("SUB 同 id 覆盖锚点行未被拒（#127 回归）")
    except PermissionError:
        pass
    assert e.store.get_node(anchor.id).layer is MemoryLayer.ANCHOR
    # 同 id 禁换层：把自我层行改成 knowledge ⇒ 拒（换层判据与层集合无关）
    try:
        e.store.add_node(_node(sid, MemoryLayer.KNOWLEDGE))
        raise AssertionError("SELF 行被同 id 换成 knowledge（#127 换层闸对 SELF 失效）")
    except PermissionError:
        pass
    assert e.store.get_node(sid).layer is MemoryLayer.SELF


def test_q6_sub_local_layers_writable():
    """Q6：防误杀——SUB 写知识层/情境层（本地层）放行（正对照）。"""
    e = SpacetimeMemoryEngine(":memory:", role=Role.SUB)
    e.store.add_node(_node("local_k_1", MemoryLayer.KNOWLEDGE))
    e.store.add_node(_node("local_c_1", MemoryLayer.CONTEXT))
    assert e.store.get_node("local_k_1").layer is MemoryLayer.KNOWLEDGE
    assert e.store.get_node("local_c_1").layer is MemoryLayer.CONTEXT
