# -*- coding: utf-8 -*-
"""#225 M5 去重无视成败极性 —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #225，判定「成立」）：
  `record_action_sequence` 把成败写在两处——正文前缀 `成功：`/`失败：`（outcome
  截到 `[:40]`）与标签 `success`/`failure`。随后走 `add_perception`，而 M5 去重
  **只比正文二元组 Jaccard，不看标签、不看极性**：动作序列相同时相似度越过阈值
  ⇒ 失败经验被并进「成功」节点（`increment_access` + 加 `duplicate` 标签 +
  `return best`），`failure` 标签被整个丢掉。反向亦然（成功被并进失败节点）。
  后果：失败经验在节点层（`recall`/`ActivationEngine` 只读 nodes）**消失**，
  并变成对应成功经验的「增信票」。

修法（最小 · 按 issue「建议修复 1」）：
  M5 去重候选必须**极性一致**——候选节点与新写入的极性标签集（∩ 极性词表）不等
  即跳过该候选。极性词表 = `{"success", "failure"}`（`_POLARITY_TAGS`，只取两个
  既有标签）。

判据来源：公开 issue #225「建议修复 1：M5 去重的候选必须极性一致：`success`/
`failure` 不同的节点不得合并」＋ issue 标题/复现（经验标定；仓内理论章节未规定
M5 判同键的极性口径，追不到更早出处）。
边界（削掉的判别力，如实标注）：①词表只含 `success`/`failure` 两个既有标签，
不引入 `negative`/`conflict` 等尚不存在的极性标签；②只挡「合并」，不改
`record_action_sequence` 的 outcome 截断口径；③极性不同的两条写入**不**自动建
`OPPOSITE` 边/落冲突（fail-loud 属策略取舍，本件只做「不合并」）。

断言组（抽掉修复——删掉 `add_perception` 内的极性闸——Q1/Q2/Q3 必红）：
  Q1 极性隔离：同一动作序列先成功、再失败 5 次 ⇒ 失败返回**另一个**节点，
     图中 success 与 failure 各一条，成功节点置信度**不因失败上升**。
  Q2 反向：先失败、再成功 ⇒ 成功另立节点，失败节点置信度不因成功上升。
  Q3 端到端：`recall` 同时召回 success 与 failure 两个节点。
  Q4 防误杀（#29/#223 不回归）：**同极性**逐字重复仍去重合并、仍不增信。

运行（仓根）：python -X utf8 -m pytest tests/test_issue225_polarity_dedup_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import MemoryLayer, SpacetimeMemoryEngine  # noqa: E402

_ACTS = ["备份数据库", "校验哈希", "上传到远端", "通知管理员", "记录日志", "清理临时文件"]
_OUT = "全部完成，退出码 0"


def _seq(e, success):
    return e.record_action_sequence(_ACTS, _OUT, success=success)


def _nodes(e):
    return [n for n in e.store.query_nodes(layer=MemoryLayer.KNOWLEDGE, limit=100)
            if n.content.startswith("[action_seq]")]


def test_q1_failure_not_merged_into_success():
    """Q1：失败不得被并进成功节点，成功节点置信度不因失败上升。"""
    e = SpacetimeMemoryEngine(":memory:")
    s = _seq(e, True)
    conf0 = e.store.get_node(s.id).confidence
    fails = [_seq(e, False) for _ in range(5)]
    assert all(f.id != s.id for f in fails), (
        "#225：失败经验被并进成功节点（极性闸失效）——失败返回 id == 成功 id")
    nodes = _nodes(e)
    tagsets = [set(n.tags) for n in nodes]
    assert any("success" in t for t in tagsets), "成功节点消失：%r" % tagsets
    assert any("failure" in t for t in tagsets), (
        "#225：图中不存在 failure 节点（失败经验被抹掉）——实测 %r" % tagsets)
    assert e.store.get_node(s.id).confidence == conf0, (
        "#225：失败经验给成功节点增信——conf %r -> %r"
        % (conf0, e.store.get_node(s.id).confidence))


def test_q2_success_not_merged_into_failure():
    """Q2（反向）：成功不得被并进失败节点，失败节点置信度不因成功上升。"""
    e = SpacetimeMemoryEngine(":memory:")
    f = _seq(e, False)
    conf0 = e.store.get_node(f.id).confidence
    s = _seq(e, True)
    assert s.id != f.id, "#225（反向）：成功被并进失败节点"
    assert e.store.get_node(f.id).confidence == conf0, (
        "#225（反向）：成功给失败节点增信——conf %r -> %r"
        % (conf0, e.store.get_node(f.id).confidence))
    tagsets = [set(n.tags) for n in _nodes(e)]
    assert any("failure" in t for t in tagsets) and any("success" in t for t in tagsets), (
        "反向场景成败节点未各立一条：%r" % tagsets)


def test_q3_recall_returns_both_polarities():
    """Q3：`recall` 同时召回 success 与 failure 两条经验节点。"""
    e = SpacetimeMemoryEngine(":memory:")
    _seq(e, True)
    _seq(e, False)
    got = [n for n, _sc in e.recall("备份数据库 校验哈希 上传到远端", limit=10)
           if n.content.startswith("[action_seq]")]
    assert len(got) == 2, (
        "#225：recall 只返回 %d 条经验节点（成败应各一条）——%r"
        % (len(got), [(n.id, n.tags) for n in got]))
    tagsets = [set(n.tags) for n in got]
    assert any("success" in t for t in tagsets), "recall 未返回成功经验：%r" % tagsets
    assert any("failure" in t for t in tagsets), (
        "#225：recall 未返回失败经验（失败经验在节点层消失）——实测 %r" % tagsets)


def test_q4_same_polarity_still_dedups():
    """Q4：防误杀（#29/#223 不回归）——同极性逐字重复仍合并、仍不增信。"""
    e = SpacetimeMemoryEngine(":memory:")
    first = _seq(e, True)
    for _ in range(5):
        again = _seq(e, True)
    assert again.id == first.id, "同极性重复未被去重（极性闸误伤正常去重）"
    assert e.store.count_layer(MemoryLayer.KNOWLEDGE) == 1, "同极性重复另立了节点"
    assert e.store.get_node(first.id).confidence == 0.5, (
        "重复写入抬升置信度（#29 回归）：%r" % e.store.get_node(first.id).confidence)
    assert "duplicate" in e.store.get_node(first.id).tags, "重复命中未留痕"
