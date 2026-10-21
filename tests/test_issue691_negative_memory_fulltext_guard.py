# -*- coding: utf-8 -*-
"""#691 P0-a 负记忆基座截断 —— 守卫（lingshu/core/core.py）

缺陷（issue #691 · [core/固化]）：
  终裁否决时负记忆基座被截断为 `node.content[:60]`（`adjudicate_promotion`
  的否决分支），而匹配侧（`find_rejected_paths` / `add_promotion_proposal`）
  用的是**新内容全文**——`char_bigram_jaccard(全文, content[:60]+理由)` 的
  并集被全文撑大。后果：**约 500 字以上的多样文本，连一字不改原文重提都不被拦**
  （改尾既非必要也非充分，作者原文的机制描述不准）。否决内容因此可原文写进
  不可逆的结构层。

修法：落库基座改用**完整正文**，与匹配侧全文口径统一（阈值 0.15 与匹配算法未动）。

断言组（回退 `node.content[:60]` ⇒ A 红）：
  A 长文本（~656 字多样）终裁否决后**同文一字不改**重提 ⇒ 必须被拦（ValueError #216）
  B 正对照：短文本（~40 字）同文重提仍被拦（量具有判别力）
  C 防误杀：未被否决过的**不同**内容照常可提案（口径统一未把提案面收死）

运行（仓根）：python -X utf8 -m pytest tests/test_issue691_negative_memory_fulltext_guard.py -q --no-header
"""
from __future__ import annotations

import os
import random
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_KEY = "GUARD-691-KEY"


def _rejected(monkeypatch, text):
    """造一条 `path_type='promotion'` 的 open 负记忆（走真实终裁否决路径）。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)
    e = SpacetimeMemoryEngine(":memory:")
    n1 = e.add_perception(text, importance=0.9, skip_dedup=True)
    pid = e.propose_promotion(n1.id, "提议者", "理由")
    assert e.verify_promotion(pid, "复核者") is True
    assert e.adjudicate_promotion(pid, "终裁者", False, designer_key=_KEY) is True
    hits = [r for r in e.list_rejected_paths() if r["path_type"] == "promotion"]
    assert hits, "前置：否决未登记负记忆"
    return e


def _long_diverse_text(n=656, seed=11):
    random.seed(seed)
    vocab = "灵枢认知记忆节点断言核验规范条件约束边界守护治理结构情境知识"
    return "".join(random.choice(vocab) for _ in range(n))


def test_a_long_text_same_content_reproposal_blocked(monkeypatch):
    """A：长文本同文一字不改重提必须被拦（旧：不被拦，进不可逆结构层）。"""
    text = _long_diverse_text()
    assert len(text) >= 500, "前置：样本须为长文本，实测 %d" % len(text)
    e = _rejected(monkeypatch, text)
    n2 = e.add_perception(text, importance=0.9, skip_dedup=True)
    with pytest.raises(ValueError) as ei:
        e.propose_promotion(n2.id, "提议者", "理由")
    assert "#216" in str(ei.value), "拒绝理由未指向负记忆面：%r" % str(ei.value)


def test_b_short_text_same_content_reproposal_blocked(monkeypatch):
    """B：正对照——短文本同文重提仍被拦（证明量具有判别力）。"""
    text = "灵枢负记忆短文本正对照样例内容一二三四五六七八九十"
    assert len(text) < 60, "前置：样本须为短文本，实测 %d" % len(text)
    e = _rejected(monkeypatch, text)
    n2 = e.add_perception(text, importance=0.9, skip_dedup=True)
    with pytest.raises(ValueError):
        e.propose_promotion(n2.id, "提议者", "理由")


def test_c_different_content_still_proposable(monkeypatch):
    """C：防误杀——未被否决过的不同内容照常可提案。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)
    e = _rejected(monkeypatch, _long_diverse_text())
    n = e.add_perception("这是一条与前述否决内容毫无重叠的全新提案正文",
                         importance=0.9, skip_dedup=True)
    pid = e.propose_promotion(n.id, "提议者", "理由")
    assert pid, "#691：正常提案被本件误拒"
