# -*- coding: utf-8 -*-
"""#223 `add_perception` M5 去重命中时丢弃新输入字段 —— 守卫（lingshu/core/core.py）

缺陷（分诊表 `triage_lingshu_ALL.md` 行 223 判定「成立」；issue 原文 `.tmp/issues_p3.json` #223）：
  M5 去重命中后只 `increment_access` + 置信度处理 + 加 `duplicate` 标签就 `return best`，
  新输入携带的内容全部丢弃：`entities` 不挂 `ent:<id>` 标签、`tags`（如 `urgent`）丢失、
  `importance` 0.95 重复写入而原节点仍是 0.3、`modality` 不同的 audio 输入被并入 text 节点
  且无任何记录；返回值与「新建节点」不可区分。

修法（最小 · 按 issue「期望行为」三条）：
  ① 候选集按 `modality` 过滤——**modality 不同的不参与去重**；
  ② 命中后 `entities`/`tags` 取**并集**并入、`importance` 取**两者较大值**；
  ③ 返回值带 `merged=True`（新建路径 `merged=False`），使调用方可区分两种结果。

判据来源：issue #223「期望行为」（逐字三条）＋ 分诊表行 223（经验标定；仓内理论章节
未规定去重命中的字段合并口径）。
边界（削掉的判别力，如实标注）：`merged` 以返回对象属性承载（不新增返回类型/签名）；
只并入、不删除既有标签；**跨模态**同内容不再合并（旧行为：并入 text 节点并静默丢模态）
——这是本件要求的判据变更。

断言组（把修复改回缺陷形态 ⇒ Q1/Q2/Q3/Q4 红）：
  Q1 entities/tags 并入：同模态重复命中后原节点带上 `ent:王五` 与 `urgent`。
  Q2 importance 取大：原 0.3 被 0.95 的重复写入抬到 0.95（旧：仍 0.3）。
  Q3 modality 不参与去重：audio 重复内容另立节点（旧：并入 text 节点）。
  Q4 返回值可区分：合并路径 `merged is True`、新建路径 `merged is False`。
  Q5 端到端：`consolidate_learning_result` 第二次的实体 E2 不再丢失。
  Q6 防误杀（#29/#142 不回归）：同源重复仍**不增信**、仍带 `duplicate` 标签。

运行（仓根）：python -X utf8 -m pytest tests/test_issue223_dedup_merge_fields_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import MemoryLayer, SpacetimeMemoryEngine  # noqa: E402

_BASE = "用户说明天上午十点去机场接人"


def test_q1_entities_and_tags_are_merged():
    """Q1：命中合并时新输入的 entities/tags 取并集并入（旧：全丢）。"""
    e = SpacetimeMemoryEngine(":memory:")
    a = e.add_perception(_BASE, importance=0.3)
    b = e.add_perception(_BASE, importance=0.95, tags=["urgent"], entities=["王五"])
    assert a.id == b.id, "前置：同模态同内容应被去重合并"
    tags = e.store.get_node(a.id).tags
    assert "ent:王五" in tags, (
        "#223：合并时 entities 丢失——tags=%r" % tags)
    assert "urgent" in tags, (
        "#223：合并时 tags 丢失——tags=%r" % tags)


def test_q2_importance_takes_max():
    """Q2：importance 取两者较大值（旧：以 0.95 重复写入，原节点仍是 0.3）。"""
    e = SpacetimeMemoryEngine(":memory:")
    a = e.add_perception(_BASE, importance=0.3)
    e.add_perception(_BASE, importance=0.95)
    got = e.store.get_node(a.id).importance
    assert abs(got - 0.95) < 1e-9, (
        "#223：importance 未取较大值——实测 %r（旧：仍 0.3）" % got)
    # 反向：较低值不得下调原节点
    e2 = SpacetimeMemoryEngine(":memory:")
    a2 = e2.add_perception(_BASE, importance=0.9)
    e2.add_perception(_BASE, importance=0.2)
    assert abs(e2.store.get_node(a2.id).importance - 0.9) < 1e-9, (
        "#223：低 importance 的重复写入把原节点下调了")


def test_q3_modality_not_deduped_across():
    """Q3：modality 不同的不参与去重（旧：audio 输入被并入 text 节点）。"""
    e = SpacetimeMemoryEngine(":memory:")
    a = e.add_perception(_BASE, importance=0.3)
    b = e.add_perception(_BASE, importance=0.95, modality="audio")
    assert b.id != a.id, (
        "#223：audio 输入被并入 text 节点（modality 未参与去重）")
    assert e.store.get_node(b.id).modality == "audio", "audio 节点的模态被改写"
    assert e.store.count_layer(MemoryLayer.KNOWLEDGE) == 2, "跨模态重复被合并"


def test_q4_return_value_distinguishes_merged():
    """Q4：返回值可区分「新建」与「已合并」（旧：返回值与新建不可区分）。"""
    e = SpacetimeMemoryEngine(":memory:")
    a = e.add_perception(_BASE, importance=0.3)
    assert getattr(a, "merged", None) is False, (
        "#223：新建路径未标 merged=False——merged=%r" % getattr(a, "merged", None))
    b = e.add_perception(_BASE, importance=0.3)
    assert getattr(b, "merged", None) is True, (
        "#223：合并路径未标 merged=True——merged=%r" % getattr(b, "merged", None))


def test_q5_learning_result_entities_not_lost():
    """Q5：端到端——`consolidate_learning_result` 第二次的实体不再丢失。"""
    e = SpacetimeMemoryEngine(":memory:")
    r1 = e.consolidate_learning_result({"summary": "机场接人流程", "entities": ["E1"]})
    r2 = e.consolidate_learning_result({"summary": "机场接人流程", "entities": ["E2"]})
    assert r1.id == r2.id, "前置：同一摘要应合并"
    tags = e.store.get_node(r2.id).tags
    assert "ent:E2" in tags, (
        "#223：第二次学习结果的实体丢失——tags=%r（旧：['learning_result', 'ent:E1', "
        "'duplicate']）" % tags)
    assert "ent:E1" in tags, "原实体被本件改动丢失"


def test_q6_repeat_does_not_raise_confidence():
    """Q6：防误杀（#29/#142 不回归）——同源重复仍不增信、仍留 `duplicate`。"""
    e = SpacetimeMemoryEngine(":memory:")
    node = None
    for _ in range(31):
        node = e.add_perception(_BASE)
    assert e.store.count_layer(MemoryLayer.KNOWLEDGE) == 1, "同源重复未合并"
    assert e.store.get_node(node.id).confidence == 0.5, (
        "重复写入抬升了置信度（#29 回归）：%r" % e.store.get_node(node.id).confidence)
    assert "duplicate" in e.store.get_node(node.id).tags, "重复命中未留痕"
