# -*- coding: utf-8 -*-
"""#484 `_assertion_divergence` 的否定判据只比「两句各自是否含否定字」 —— 守卫

缺陷（公开 issue #484，维护者复核帖判定「成立」）：
  `lingshu/core/core.py` · `SpacetimeMemoryEngine._assertion_divergence` 的否定分支
  （特征串 `any(w in a for w in marks) != any(w in b for w in marks)`，词表
  `_NEGATION_MARKERS = ("不","非","未","无","没","别","莫","勿")`）比较的是两句
  **各自是否出现过任一否定字**，而不是否定标记本身的差异。`_NEGATION_MARKERS`
  里的单字大量嵌在**非否定的常用词**里（非常/未来/无法/无论/不同/不久/别人/没事…）：
  原句只要带一个这样的词即为 `True`，更正句再插入「不」/「尚未」也是 `True`
  ⇒ 判为无分歧；相似度 ≥ 0.85（`_effective_dedup_threshold`）即按**重复合并**。
  后果：更正文本不落库、无 `conflict` 标签、无 `OPPOSITE` 边，且 #223 的合并逻辑
  把**错误节点**的 importance 抬到更正的 0.9 —— 用更正的原话 `recall`，返回的
  反而是错误事实。

判据来源（本守卫断言的依据）：
  · 公开 issue #484 正文（三对句子 + 实测读数）与维护者复核帖（`9dd0beb` 基线，
    判定「成立」；并实测 issue 建议的 `difflib` opcodes 修法会引入**新的假分歧**
    ——「他这个人非常好」vs「他这个人非常不错」、「他无法完成这项任务」vs
    「他不能完成这项任务」被误判 polarity）；
  · 实现侧口径：把判据从「是否含否定**字**」改为「否定标记的**差异**」——按**词**
    而不是按**字**匹配，且只在「一方被否定、另一方未被否定」时判 `negation`；
    复用仓内既有词面口径 `lingshu/core/contradiction.py::_NON_NEG`（整词出现即
    不算否定的常用词表）并按 issue 清单补 4 词，故豁免表 = `_NON_NEG` ∪
    {无法, 不同, 不久, 没事}；`_NEGATION_MARKERS` 取值集合**不变**。
  为什么这样判：中文无空格词界（本仓不引分词器，见 `_synonym_trigger_hit` 的
  同款声明），故以「整词剔除豁免词后判剩余否定标记」代替 `\\b`；判据保守方向
  仍是「宁判分歧（另立节点）不判重复」，与函数 docstring 自陈一致。

断言组（把修复改回缺陷形态 ⇒ T1/T2/T3 红；过修 ⇒ T2/T4 红）：
  T1 三对句子（issue 正文原样）：前两对必须 `negation` 且更正**落库**、错误节点
     importance **不被抬高**、`conflict` 标签在场；第三对与改前**一致**。
  T2 常用词嵌入（非常/未来/无法/无论/不同/不久/别人/没事）**不得**判 `negation`
     —— 两句都含（措辞微调）与**单侧**替换（同义改写）两种形态都断言（防过修）。
  T3 真否定对（过敏 vs 不过敏、恢复 vs 尚未恢复）**必须**判 `negation`。
  T4 两句都含否定且**同向**（都「不 X」）⇒ **不**判 `negation`（旧实现会漏）。
  T5 退化输入（空串 / 单字 / 纯标点 / 非 `str`）不抛异常且返回值在取值集合内。
  T6 `_NEGATION_MARKERS` **取值集合不变**；豁免表 ⊆ 旧词表 ∪ 本件新增，逐项列出。
  T7 正对照：`char_bigram_jaccard` 对三对句子的读数与改前**逐位一致**（证明
     「修的是判据、不是量具」，也证明量具本身有效——能测到、不恒绿不恒红）。
  T8 端到端：更正句经 `add_perception` 后**可被 recall 到**、且 `conflict` 标签
     在原命题上在场（旧实现：更正不落库、原命题被增信到 0.9、无 conflict 标签）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue484_negation_divergence_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from lingshu.core import contradiction as _contradiction  # noqa: E402
from lingshu.core.core import LayeredStore, MemoryLayer, SpacetimeMemoryEngine  # noqa: E402

# ---- issue #484 正文原样的三对句子（第三对是 #142 已修的正对照） ----
PAIR_FANCHANG = (
    "用户在体检报告中确认对青霉素类抗生素过敏，这一点非常重要，开药前必须提醒医生。",
    "用户在体检报告中确认对青霉素类抗生素不过敏，这一点非常重要，开药前必须提醒医生。",
)
PAIR_WEILAI = (
    "线上数据库的自动备份任务已经恢复正常运行，未来每天凌晨两点会对全部业务库执行一次全量备份并校验。",
    "线上数据库的自动备份任务尚未恢复正常运行，未来每天凌晨两点会对全部业务库执行一次全量备份并校验。",
)
PAIR_CONTROL = (
    "用户在体检报告中确认对青霉素类抗生素过敏，开药前必须提醒医生注意这一点。",
    "用户在体检报告中确认对青霉素类抗生素不过敏，开药前必须提醒医生注意这一点。",
)

# ---- 常用词正对照（防过修）：两句都含该常用词、仅措辞微调 ----
COMMON_BOTH = [
    ("非常", "他非常认可这个方案，未来会一直支持这项决定。",
     "他非常认可这个方案，未来会持续支持这项决定。"),
    ("未来", "他非常认可这个方案，未来会一直支持这项决定。",
     "他非常认可这个方案，未来会持续支持这项决定。"),
    ("无法", "无论谁来负责，他无法独自完成全部的数据核对工作。",
     "无论谁来负责，他无法单独完成全部的数据核对工作。"),
    ("无论", "无论谁来负责，他无法独自完成全部的数据核对工作。",
     "无论谁来负责，他无法单独完成全部的数据核对工作。"),
    ("不同", "这两种型号的接口不同，不久之后厂商会发布统一的转接配件。",
     "这两种型号的接口不同，不久之后厂商会发布通用的转接配件。"),
    ("不久", "这两种型号的接口不同，不久之后厂商会发布统一的转接配件。",
     "这两种型号的接口不同，不久之后厂商会发布通用的转接配件。"),
    ("别人", "别人提出的意见他都会记录，没事的时候再逐条回复处理。",
     "别人提出的意见他都会记录，空闲的时候再逐条回复处理。"),
    ("没事", "别人提出的意见他都会记录，没事的时候再逐条回复处理。",
     "别人提出的意见他都会记录，没事的时候再逐条回复并跟进处理。"),
]

# ---- 常用词正对照（防过修）：**单侧**出现该常用词、另一侧同义改写（不含任何否定字） ----
COMMON_ONE_SIDE = [
    ("非常", "他非常认可这个方案，未来会一直支持这项决定。",
     "他极其认可这个方案，今后会一直支持这项决定。"),
    ("未来", "他非常认可这个方案，未来会一直支持这项决定。",
     "他极其认可这个方案，今后会一直支持这项决定。"),
    ("无法", "他无法独自完成全部的数据核对工作，需要同事协助。",
     "他难以独自完成全部的数据核对工作，需要同事协助。"),
    ("无论", "无论谁来负责，他都会把全部的数据核对工作做完。",
     "谁来负责都行，他都会把全部的数据核对工作做完。"),
    ("不同", "这两种型号的接口不同，需要额外的转接配件。",
     "这两种型号的接口各异，需要额外的转接配件。"),
    ("不久", "不久之后厂商会发布统一的转接配件。",
     "很快厂商就会发布统一的转接配件。"),
    ("别人", "别人提出的意见他都会记录并逐条回复处理。",
     "他人提出的意见他都会记录并逐条回复处理。"),
    ("没事", "没事的时候他会逐条回复处理这些意见。",
     "空闲的时候他会逐条回复处理这些意见。"),
]

# ---- 真否定对（必须判 negation） ----
REAL_NEGATION = [
    ("过敏 / 不过敏", "用户在体检报告中确认对青霉素类抗生素过敏，开药前必须提醒医生注意这一点。",
     "用户在体检报告中确认对青霉素类抗生素不过敏，开药前必须提醒医生注意这一点。"),
    ("恢复 / 尚未恢复", "线上数据库的自动备份任务已经恢复正常运行，每天凌晨两点执行全量备份。",
     "线上数据库的自动备份任务尚未恢复正常运行，每天凌晨两点执行全量备份。"),
]

# ---- 同向双否定（不得判 negation） ----
BOTH_NEGATED = [
    ("都含「不」同向", "系统在标准配置下运行不正常，每日巡检不得跳过。",
     "系统在标准配置下运行不正常，每日巡检不能跳过。"),
    ("都含「未」同向", "该漏洞尚未修复，补丁尚未发布。",
     "该漏洞尚未修复，补丁还未发布。"),
]

DIVERGENCE_VALUES = {"", "negation", "numeric"}


def _div(a, b):
    return SpacetimeMemoryEngine._assertion_divergence(a, b)


def _write_pair(x, y, times=5, importance=0.9):
    """按 issue 复现脚本口径写入：先 x，再重复写 y（importance=0.9）。"""
    e = SpacetimeMemoryEngine(":memory:")
    a = e.add_perception(x)
    for _ in range(times):
        e.add_perception(y, importance=importance)
    return e, a


def _stored_contents(e):
    return [n.content for n in e.store.query_nodes(layer=MemoryLayer.KNOWLEDGE, limit=-1)]


# ---------------------------------------------------------------- T1
@pytest.mark.parametrize("tag,x,y,expect_negation", [
    ("P1 原句含「非常」(非)", PAIR_FANCHANG[0], PAIR_FANCHANG[1], True),
    ("P2 原句含「未来」(未)", PAIR_WEILAI[0], PAIR_WEILAI[1], True),
    ("P3 对照：原句不含否定字（#142 已修）", PAIR_CONTROL[0], PAIR_CONTROL[1], True),
])
def test_t1_issue_three_pairs(tag, x, y, expect_negation):
    """T1：issue 正文三对句子——前两对必须判分歧且更正落库、错误节点不被抬高。"""
    assert x and y, "夹具非空（防循环/断言空转）"
    assert LayeredStore.char_bigram_jaccard(x, y) >= 0.85, (
        "%s：夹具相似度必须 ≥ 0.85 才会走 M5 去重（实测 %.4f）"
        % (tag, LayeredStore.char_bigram_jaccard(x, y)))
    div = _div(y, x)
    assert (div == "negation") is expect_negation, (
        "%s：#484 判据错——divergence=%r（期望 negation=%s）" % (tag, div, expect_negation))

    e, a = _write_pair(x, y)
    stored = _stored_contents(e)
    assert stored.count(y) >= 1, (
        "%s：更正文本未落库（被 M5 当重复吞并）——库内内容=%r" % (tag, stored))
    old = e.store.get_node(a.id)
    assert old.importance == pytest.approx(0.5), (
        "%s：错误节点 importance 被抬高到 %r（#223 合并把更正的重要度搬给了错误事实）"
        % (tag, old.importance))
    assert "conflict" in old.tags, (
        "%s：原命题缺 conflict 标签——tags=%r" % (tag, old.tags))
    assert "duplicate" not in old.tags or stored.count(y) >= 1, (
        "%s：更正被判重复合并（原节点只被重复计数）" % tag)
    e.close()


# ---------------------------------------------------------------- T2
@pytest.mark.parametrize("word,x,y", COMMON_BOTH)
def test_t2a_common_word_in_both_not_negation(word, x, y):
    """T2a（正对照·防过修）：单字嵌在常用词里、两句都含 ⇒ 不得判 negation。"""
    assert x and y and word, "夹具非空"
    assert word in x and word in y, "夹具必须两侧都含常用词 %r" % word
    div = _div(y, x)
    assert div != "negation", (
        "常用词 %r 被误判为否定（过修）：divergence=%r" % (word, div))


@pytest.mark.parametrize("word,x,y", COMMON_ONE_SIDE)
def test_t2b_common_word_one_side_not_negation(word, x, y):
    """T2b（正对照·防过修）：单侧出现常用词、另一侧同义改写 ⇒ 不得判 negation。"""
    assert x and y and word, "夹具非空"
    assert word in x and word not in y, (
        "夹具必须是「单侧」形态：%r 在 x 中、不在 y 中" % word)
    div = _div(y, x)
    assert div != "negation", (
        "常用词 %r 单侧出现被误判为否定（词面豁免失效/过修）：divergence=%r"
        % (word, div))


# ---------------------------------------------------------------- T3
@pytest.mark.parametrize("tag,x,y", REAL_NEGATION)
def test_t3_real_negation_detected(tag, x, y):
    """T3：真否定对（过敏/不过敏、恢复/尚未恢复）必须判 negation（防漏判）。"""
    assert x and y, "夹具非空"
    div = _div(y, x)
    assert div == "negation", (
        "%s：真否定未被判出——divergence=%r（判据被削过头）" % (tag, div))


# ---------------------------------------------------------------- T4
@pytest.mark.parametrize("tag,x,y", BOTH_NEGATED)
def test_t4_both_negated_same_direction_not_negation(tag, x, y):
    """T4（正对照）：两句都含否定且同向（都「不/未 X」）⇒ 不判 negation。"""
    assert x and y, "夹具非空"
    div = _div(y, x)
    assert div != "negation", (
        "%s：同向双否定被误判为分歧——divergence=%r" % (tag, div))


# ---------------------------------------------------------------- T5
@pytest.mark.parametrize("a,b", [
    ("", ""), ("", "不"), ("不", ""), ("不", "不"), ("不", "非"),
    ("。", "！"), ("，。！？", "，。！？"), ("。。", "。。。"),
    ("  ", " "), ("不。", "。不"),
])
def test_t5_degenerate_inputs_do_not_raise(a, b):
    """T5：退化输入（空串/单字/纯标点）不抛异常，且返回值在既有取值集合内。"""
    got = _div(a, b)
    assert got in DIVERGENCE_VALUES, "返回值越出既有取值集合：%r" % (got,)
    rev = _div(b, a)
    assert rev in DIVERGENCE_VALUES, "反向调用返回值越出集合：%r" % (rev,)


def test_t5b_non_str_input_does_not_raise():
    """T5b：非 str（None/bytes）走 helper 不得抛（`_assertion_divergence` 契约面只收 str）。"""
    assert SpacetimeMemoryEngine._has_negation_word(None) is False
    assert SpacetimeMemoryEngine._has_negation_word(b"x") is False
    assert SpacetimeMemoryEngine._has_negation_word("") is False


# ---------------------------------------------------------------- T6
def test_t6_marker_set_unchanged_and_exemption_table_superset():
    """T6：`_NEGATION_MARKERS` 取值集合不变；豁免表 ⊆ 旧词表 ∪ 本件新增（逐项列出）。"""
    marks = set(SpacetimeMemoryEngine._NEGATION_MARKERS)
    assert marks == {"不", "非", "未", "无", "没", "别", "莫", "勿"}, (
        "#484 不得扩/缩否定标记词表——实测 %r" % (sorted(marks),))

    words = set(SpacetimeMemoryEngine._NON_NEGATION_WORDS)
    old = set(_contradiction._NON_NEG)
    added = {"无法", "不同", "不久", "没事"}
    assert old <= words, "既有 `contradiction._NON_NEG` 被从豁免表里删掉了：%r" % (
        sorted(old - words),)
    assert (words - old) == added, (
        "本件新增豁免词必须逐项可列：实测新增 %r，声明新增 %r"
        % (sorted(words - old), sorted(added)))
    assert len(words) == len(SpacetimeMemoryEngine._NON_NEGATION_WORDS), "豁免表有重复项"


# ---------------------------------------------------------------- T7
@pytest.mark.parametrize("tag,x,y,expected", [
    ("P1", PAIR_FANCHANG[0], PAIR_FANCHANG[1], 0.925),
    ("P2", PAIR_WEILAI[0], PAIR_WEILAI[1], 0.8775510204081632),
    ("P3", PAIR_CONTROL[0], PAIR_CONTROL[1], 0.918918918918919),
])
def test_t7_char_bigram_jaccard_unchanged(tag, x, y, expected):
    """T7（正对照）：`char_bigram_jaccard` 读数与改前逐位一致（量具未被改动）。"""
    assert x and y, "夹具非空"
    got = LayeredStore.char_bigram_jaccard(x, y)
    assert got == pytest.approx(expected, abs=1e-12), (
        "%s：char_bigram_jaccard 被改动——实测 %r，改前 %r" % (tag, got, expected))
    assert 0.0 < got <= 1.0, "量具读数必须落在 (0,1]（非恒 0/恒 1 的假绿哨兵）"


# ---------------------------------------------------------------- T8
def test_t8_end_to_end_correction_recallable_with_conflict_tag():
    """T8：端到端——更正句经 `add_perception` 后可被 recall 到，且原命题带 conflict 标签。"""
    x, y = PAIR_FANCHANG
    assert x and y, "夹具非空"
    e, a = _write_pair(x, y)
    hits = [n for n in e.store.query_nodes(layer=MemoryLayer.KNOWLEDGE, limit=-1)
            if n.content == y]
    assert len(hits) == 1, "更正句未落库（旧实现：被 M5 当重复吞并）——命中 %d 条" % len(hits)
    got = e.recall(y, limit=1)
    assert got and got[0][0].id == hits[0].id, (
        "用更正的原话 recall 未返回更正节点（旧实现返回的是错误事实）——"
        "实际返回 %r" % ([n[0].id for n in got],))
    assert "conflict" in e.store.get_node(a.id).tags, (
        "原命题缺 conflict 标签——tags=%r" % (e.store.get_node(a.id).tags,))
    assert e.store.get_node(a.id).importance == pytest.approx(0.5), (
        "错误事实的 importance 被抬高到 %r（#223 合并副作用）"
        % (e.store.get_node(a.id).importance,))
    e.close()
