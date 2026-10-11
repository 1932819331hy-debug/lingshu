# -*- coding: utf-8 -*-
"""#166 induce_concepts 倒排预筛丢掉池内 DF>半数的节点 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #166 · 判定成立，实读复现见下）：
  倒排预筛只把 `df[bg] <= half`（`half = max(1, len(nodes)//2)`）的二元组收进
  `inverted`。**单主题候选池**里该主题的公共二元组 DF 等于池大小（> half）⇒
  池内每个节点的 bigram **全部**被剔除 ⇒ 倒排索引里没有它们的任何条目 ⇒
  候选对集合为空 ⇒ `induce_concepts` 恒返回 `[]`，与「无可归纳内容」无法区分。
  实测（HEAD，修前）：5 条同主题（两两 Jaccard=0.862 ≥ 0.6）的记忆 ⇒ clusters=0。

修法（最小 · 候选集回退）：某节点经预筛后**没有任何候选**且池内不止它一个时，
回退为「与池内其余节点逐一比对」，仍受同一 8s deadline 与长度预筛约束。
有候选的节点仍走原预筛（其候选集不变）。

判据来源：分诊表 `.tmp/triage_lingshu_ALL.md` 行 166；仓内无规定倒排预筛召回面
的理论章节，追不到更早出处（经验标定，本件 #166）。
边界（削掉的判别力）：回退只在「预筛后候选为空」时触发；DF>half 的**公共** bigram
仍不进倒排索引，故「部分节点全公共 bigram、部分节点有低频 bigram」的混合池里，
后者之间的候选仍来自原预筛（召回口径不变）。

断言组（抽掉回退分支即 A 变红）：
  A 单主题池（全部节点两两 ≥ 阈值）⇒ 必须聚成 ≥1 个概念（修前恒 0）。
  B 防误杀 · 混合池：两个各自成簇的主题 ⇒ 仍聚成 2 个概念（回退不得破坏原结果）。
  C 防误杀 · 阈值纪律：同主题但两两 Jaccard < 阈值 ⇒ 仍返回 []（回退不得造出假簇）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue166_induce_inverted_prune_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine, LayeredStore  # noqa: E402

A = "深度学习模型训练需要大量标注数据和算力资源投入"
B = "量子纠缠实验验证了贝尔不等式与非定域性假设"


def _db(name):
    return os.path.join(tempfile.mkdtemp(prefix="lingshu166_"), name + ".db")


def _engine(name, texts):
    e = SpacetimeMemoryEngine(_db(name))
    for t in texts:
        e.add_perception(t, importance=0.5, skip_dedup=True)
    return e


def test_a_single_topic_pool_still_clusters():
    """A 全池同主题（公共 bigram DF>half）不得被预筛整批丢弃。"""
    texts = [A + s for s in ["以提升效果", "以提升速度", "以提升质量"]]
    # 前置：确认这组确实都在阈值之上（否则本断言测的不是预筛缺陷）
    assert LayeredStore.char_bigram_jaccard(texts[0], texts[1]) >= 0.6
    assert LayeredStore.char_bigram_jaccard(texts[0], texts[2]) >= 0.6
    assert LayeredStore.char_bigram_jaccard(texts[1], texts[2]) >= 0.6
    cs = _engine("single", texts).induce_concepts()
    assert len(cs) >= 1, "单主题池未聚成概念（倒排预筛丢池内 DF>半数节点）：%r" % (cs,)


def test_b_mixed_pool_unchanged():
    """B 防误杀：两主题各自成簇的池仍聚成 2 个概念。"""
    texts = ([A + s for s in ["以提升效果", "以提升速度", "以提升质量"]]
             + [B + s for s in ["的结论", "的意义", "的价值"]])
    cs = _engine("mixed", texts).induce_concepts()
    assert len(cs) == 2, "混合池聚类结果被改变：%d 个概念 %r" % (len(cs), [c.content for c in cs])


def test_c_below_threshold_no_false_cluster():
    """C 防误杀：同主题但两两低于阈值 ⇒ 仍不得成簇（回退不造假簇）。"""
    texts = [
        "深度学习模型训练需要大量标注数据",
        "深度学习模型训练需要大量算力资源",
        "深度学习模型训练需要大量时间投入",
    ]
    assert LayeredStore.char_bigram_jaccard(texts[0], texts[1]) < 0.6
    cs = _engine("below", texts).induce_concepts()
    assert cs == [], "低于阈值的池被误聚成簇：%r" % ([c.content for c in cs],)
