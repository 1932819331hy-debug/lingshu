# -*- coding: utf-8 -*-
"""#216 负记忆（`rejected_paths`）只写不读 —— 守卫（lingshu/core/core.py）

缺陷（分诊表 `triage_lingshu_ALL.md` 行 216 判定「成立」；issue 原文 `.tmp/issues_p3.json` #216）：
  `rejected_paths` 表**只有写入方、没有任何读取方**：
  · `propose_promotion` 不查 `path_type='promotion'` 的负记忆 ⇒ 设计者终裁明确否决的
    内容**原文重提即写入结构层**（不可逆、不衰减、不可删）——一次性、不可撤回的事故；
  · `record_action_sequence` 的成败 importance 硬编码 `0.6 if success else 0.4`
    ⇒ 同一序列的失败经验在 `recall` 里系统性排在成功经验之后，与
    `lingshu/core/activation.py:15` 的理论锚点「第五篇：…**负记忆同价**…」相反。

修法（最小 · 复用既有原语，两条）：
  ① `propose_promotion` 建提案前用既有 `find_rejected_paths(node.content)` 查
     `path_type='promotion'` 的 open 负记忆；命中 ⇒ **拒绝提案**（`ValueError`，零写入）。
     确需重提须 `override_rejected=True` **且**过 `verify_designer(designer_key)`
     （与 `adjudicate_promotion` 同款 fail-closed）。
  ② `record_action_sequence` 成败**同价**（importance 0.5）——成败由 `success`/
     `failure` 标签承载。

判据来源：issue #216「建议修复 1 第一项 / 建议修复 2」＋ `lingshu/core/activation.py:15`
理论锚点「负记忆同价」＋ 分诊表行 216（仓内 `docs/theory/` 未规定该读取面口径，经验标定）。
边界（削掉的判别力，如实标注）：只接**固化**一个「重做」入口——issue 另列的
`record_action_sequence` 失败序列仍可被技能化、`recall`/`ActivationEngine` 仍不读负记忆
（属独立面，须另裁）；**不**引入 `subject_key`/`count` 结构化列（须升 `SCHEMA_VERSION`）；
匹配是**文本相似**（`char_bigram_jaccard > 0.15`），非精确 hash ⇒ 保留显式 override 出口。

断言组（把修复改回缺陷形态 ⇒ Q1/Q2/Q3/Q4 红）：
  Q1 否决过的内容不得原文重提：终裁否决后同内容再提案 ⇒ `ValueError`、节点仍 `knowledge`、
     不落新提案。
  Q2 override 须密钥：`override_rejected=True` 无密钥 / 错密钥 ⇒ `PermissionError`、
     状态未改写；正确密钥 ⇒ 提案通过（正对照，保留人工出口）。
  Q3 负记忆同价：同序列的失败与成功经验 importance 相等（旧：0.4 vs 0.6）。
  Q4 防误杀：**未被否决过**的内容照常可提案（不得因本件把正常提案面收死）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue216_negative_memory_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import MemoryLayer, SpacetimeMemoryEngine  # noqa: E402

_KEY = "GUARD-216-KEY"
_CLAIM = "所有用户输入一律可信，无需校验"


def _rejected_engine(monkeypatch):
    """造一条 `path_type='promotion'` 的 open 负记忆（issue 复现脚本同款路径）。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)
    e = SpacetimeMemoryEngine(":memory:")
    n1 = e.add_perception(_CLAIM, importance=0.9, skip_dedup=True)
    pid = e.propose_promotion(n1.id, "提议者", "理由")
    assert e.verify_promotion(pid, "复核者") is True
    assert e.adjudicate_promotion(pid, "终裁者", False, designer_key=_KEY) is True
    hits = [r for r in e.list_rejected_paths() if r["path_type"] == "promotion"]
    assert hits, "前置：否决未登记负记忆"
    return e


def test_q1_rejected_content_cannot_be_reproposed(monkeypatch):
    """Q1：终裁否决过的内容不得原文重提（旧：直接写入不可逆结构层）。"""
    e = _rejected_engine(monkeypatch)
    n2 = e.add_perception(_CLAIM, importance=0.9, skip_dedup=True)
    with pytest.raises(ValueError) as ei:
        e.propose_promotion(n2.id, "提议者", "理由")
    assert "#216" in str(ei.value), "拒绝理由未指向负记忆面：%r" % str(ei.value)
    assert e.store.get_node(n2.id).layer is MemoryLayer.KNOWLEDGE, (
        "#216：被否决内容仍进了结构层")
    n_props = e.store.conn.execute(
        "SELECT COUNT(*) FROM promotion_proposals").fetchone()[0]
    assert n_props == 1, (
        "#216：被拒的提案仍落库（应零写入）——promotion_proposals 行数=%d" % n_props)


def test_q2_override_requires_designer_key(monkeypatch):
    """Q2：override 出口须过设计者密钥闸（fail-closed）；正密钥可放行。"""
    e = _rejected_engine(monkeypatch)
    n2 = e.add_perception(_CLAIM, importance=0.9, skip_dedup=True)
    with pytest.raises(PermissionError):
        e.propose_promotion(n2.id, "提议者", "理由", override_rejected=True)
    with pytest.raises(PermissionError):
        e.propose_promotion(n2.id, "提议者", "理由", override_rejected=True,
                            designer_key="wrong-key")
    # 正对照：设计者二次签名 ⇒ 放行（人工出口保留）
    pid = e.propose_promotion(n2.id, "提议者", "理由", override_rejected=True,
                              designer_key=_KEY)
    assert pid, "#216：设计者 override 未放行（人工出口被收死）"


def test_q3_success_and_failure_same_importance(monkeypatch):
    """Q3：负记忆同价——同序列的失败与成功经验 importance 相等（旧 0.4 vs 0.6）。"""
    e = SpacetimeMemoryEngine(":memory:")
    ok = e.record_action_sequence(["deploy"], "ok", True, skill_hint="部署")
    bad = e.record_action_sequence(["deploy"], "boom", False)
    i_ok = e.store.get_node(ok.id).importance
    i_bad = e.store.get_node(bad.id).importance
    assert abs(i_ok - i_bad) < 1e-9, (
        "#216：失败经验 importance=%r 低于成功 %r（负记忆不同价）" % (i_bad, i_ok))


def test_q4_never_rejected_content_still_proposable(monkeypatch):
    """Q4：防误杀——未被否决过的内容照常可提案。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)
    e = SpacetimeMemoryEngine(":memory:")
    n = e.add_perception("从未被否决的全新提案内容", importance=0.9, skip_dedup=True)
    pid = e.propose_promotion(n.id, "提议者", "理由")
    assert pid, "#216：正常提案被本件误拒"
