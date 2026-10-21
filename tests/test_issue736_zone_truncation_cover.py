# -*- coding: utf-8 -*-
"""test_issue736_zone_truncation_cover · `parse_prompt` 的方位前缀表对**截断**要全覆盖（#736）
============================================================================================
缺陷（#736）：``lingshu/gen/hexgen_c1_real.py`` 的 ``ZONE_CELLS`` 是「按当时语料逐个回填截断点」
的手写表（``upper le``/``right s``/``top cen``/``bott`` …），``parse_prompt`` 只做该表的
**最长前缀匹配** ⇒ 表外的截断形态**静默**返回 ``cells=None``（方位不判、无任何提示）。

独立复现（本机，改前基线；探针在仓外）：
  · 本机语料在私有库 ``AEIS/data/vision/qwen_controlled/connection_library.json``，
    37 条 ``vs_s*`` 的 prompt **实测长度一律 70**（不是旧注释所称的 64），旧表在 70 上恰好全覆盖
    ⇒ **真实语料触发面 = 0**（这一点与立案时的降级声明一致，如实登记）；
  · 但同 8 条**完整**模板按 **64** 字符截断 ⇒ **6/8 静默 `cells=None`**；按 65/66/67/68
    截断分别漏 6/4/2/1 条；issue 点名的 2 字母形态 ``'le'/'ri'/'bo'/'lower l'`` 亦全漏；
  · 另有「被更粗的键遮蔽」的既有读数（``'top c'`` 命中键 ``'top'`` ⇒ 读成整行 r0..r2 而非 r1）
    —— 那是**同族另一面**，不在本条点名范围，本件**不擅自扩面**（读数原样保留）。

修法（最小）：``ZONE_CELLS`` **逐字保留**（它决定既有读数），新增 ``_zone_cells`` 推导支——
表内最长前缀解不出时，取「所有以该截断串**开头**的键」的值的**并集**（方位必落其中）。
⇒ 覆盖与**截断长度解耦**；表内能解析的输入读数**逐字不变**。

判据（本件）：
  G1 **截断全覆盖**：每条完整模板从「方位串第 1 个字符」到全文的**每一个**截断长度上
      ``cells`` 都非 ``None``（改前 64 字符处 6/8 漏、65..68 处 6/4/2/1 漏）；
  G2 **原本正确集逐字不变**：旧表（冻结副本）能解析的每个 ``zone_text`` ⇒ 新实现给出**相同集合**
      （含被更粗的键遮蔽的那些既有读数）；
  G3 issue 点名的 2 字母形态与真实语料形态都命中。
正/负对照：
  C1 正对照：9 条**完整**（未截断）方位短语读出各自的期望格子（量具不空转）；
  C2 负对照：**旧口径**（冻结副本 + 最长前缀）在同一输入域上**确实**给出 ``None``
      ⇒ 证明 G1/G3 测的是真现象、有判别力。

运行（仓根）：python -X utf8 -m pytest tests/test_issue736_zone_truncation_cover.py -q
"""
from __future__ import annotations

from lingshu.gen import hexgen_c1_real as C

#   ---- 旧表（HEAD 逐字冻结副本；只用于 G2/C2 的对照，不参与被测实现的判据） ----
_LEGACY = {
    "upper left": {"r0"}, "upper le": {"r0"}, "upper l": {"r0"}, "top left": {"r0"},
    "top center": {"r1"}, "top cente": {"r1"}, "top cent": {"r1"}, "top cen": {"r1"},
    "upper right": {"r2"}, "upper right ar": {"r2"}, "upper ri": {"r2"}, "top right": {"r2"},
    "left side": {"r0", "r3", "r6"}, "left s": {"r0", "r3", "r6"}, "left": {"r0", "r3", "r6"},
    "center": {"r4"}, "centre": {"r4"},
    "right side": {"r2", "r5", "r8"}, "right si": {"r2", "r5", "r8"},
    "right s": {"r2", "r5", "r8"}, "right": {"r2", "r5", "r8"},
    "lower left": {"r6"}, "lower le": {"r6"}, "bottom left": {"r6"},
    "bottom center": {"r7"}, "bottom cent": {"r7"}, "bottom c": {"r7"},
    "lower right": {"r8"}, "lower ri": {"r8"}, "bottom right": {"r8"},
    "top": {"r0", "r1", "r2"}, "bott": {"r6", "r7", "r8"}, "bottom": {"r6", "r7", "r8"},
}

#   完整（未截断）方位短语 → 期望格子（C1 正对照）
FULL = {
    "upper left": {"r0"}, "upper right": {"r2"}, "lower left": {"r6"},
    "lower right": {"r8"}, "top center": {"r1"}, "bottom center": {"r7"},
    "left side": {"r0", "r3", "r6"}, "right side": {"r2", "r5", "r8"},
    "center": {"r4"},
}

#   8 条完整模板（与真实语料同模板、覆盖全部方位族）
TEMPLATES = [
    "flat vector illustration, one brown square(s) in the upper left area, plain",
    "flat vector illustration, three brown dotted hexagon(s) in the right side area, plain",
    "flat vector illustration, one red hexagon(s) in the center area, plain",
    "flat vector illustration, one black striped diamond(s) in the right side area, plain",
    "flat vector illustration, one pink dotted circle(s) in the lower right area, plain",
    "flat vector illustration, three brown striped rectangle(s) in the bottom center area, plain",
    "flat vector illustration, three red striped triangle(s) in the top center area, plain",
    "flat vector illustration, one yellow dotted star(s) in the upper left area, plain",
]

#   本机真实语料里出现过的 zone_text（37 条 vs_s* 的取值，去重）
REAL_ZONE_TEXTS = ["upper left", "upper le", "upper l", "top cen", "top cent", "top cente",
                   "right s", "right si", "right side", "left side", "bott", "bottom c",
                   "lower right", "lower ri", "lower left", "upper right", "upper right ar",
                   "center", "center area, plai", "left side area, p", "upper right area, p"]


def _legacy_cells(zone_text):
    """旧口径逐字复制：最长前缀匹配（表外 ⇒ None）。"""
    if not zone_text:
        return None
    for phrase in sorted(_LEGACY, key=len, reverse=True):
        if zone_text.startswith(phrase):
            return set(_LEGACY[phrase])
    return None


def _cells_of(zone_text):
    """走**被测实现**的公开入口（不是直接调私有函数）：造一条最小 prompt 再解析。"""
    p = "flat vector illustration, one red circle(s) in the %s" % zone_text
    r = C.parse_prompt(p)
    assert r is not None, "构造异常：prompt 未解析：%r" % p
    return r["cells"]


def test_g1_every_truncation_length_is_covered():
    """G1：每条完整模板在「方位串已开始」后的**每个**截断长度上 cells 都非 None。"""
    assert len(TEMPLATES) == 8, "构造异常：模板数 %d" % len(TEMPLATES)
    bad = []
    n_checked = 0
    for p in TEMPLATES:
        zt_full = C.parse_prompt(p)["zone_text"]
        i0 = p.index(zt_full)
        assert zt_full, "构造异常：模板方位串为空"
        for k in range(i0 + 1, len(p) + 1):
            cut = p[:k]
            zt = C.parse_prompt(cut)["zone_text"]
            got = C.parse_prompt(cut)["cells"]
            n_checked += 1
            if got is None:
                bad.append("len=%d zone_text=%r" % (k, zt))
    assert n_checked >= 8 * 20, "构造异常：只检查了 %d 个截断点" % n_checked
    assert not bad, "#736 截断形态静默 cells=None（%d 处）：\n  " % len(bad) + "\n  ".join(bad[:20])


def test_g2_legacy_resolvable_inputs_byte_identical():
    """G2：旧表能解析的每个 zone_text ⇒ 新实现给出**相同**集合（逐字不变）。"""
    inputs = set()
    for k in _LEGACY:
        inputs.add(k)
        for i in range(1, len(k) + 1):
            inputs.add(k[:i])
    assert len(inputs) >= 40, "构造异常：输入域只有 %d 个" % len(inputs)
    diff = []
    for zt in sorted(inputs):
        old = _legacy_cells(zt)
        if old is None:
            continue
        new = _cells_of(zt)
        if new is None or set(new) != old:
            diff.append("%r old=%s new=%s" % (zt, sorted(old),
                                              sorted(new) if new else None))
    assert not diff, "#736 既有读数被改动（%d 处）：\n  " % len(diff) + "\n  ".join(diff)


def test_g3_issue_named_and_real_corpus_forms_hit():
    """G3：issue 点名的 2 字母形态 + 本机真实语料出现过的形态，命中**且格子集合正确**。

    期望格子按「该截断串是哪些完整短语的前缀 ⇒ 取并集」推（判据与实现同源，但**独立写出**
    期望值——不是把实现算出来的值抄一遍）：`'le'` ⇒ left* ⇒ {r0,r3,r6}；`'ri'` ⇒ right* ⇒
    {r2,r5,r8}；`'bo'`/`'bot'` ⇒ bottom* ⇒ {r6,r7,r8}；`'lower l'` ⇒ lower left ⇒ {r6}。
    这一条同时挡住「过修成全集 r0..r8」——全集会在此红。"""
    named = {
        "le": {"r0", "r3", "r6"}, "lef": {"r0", "r3", "r6"},
        "lower": {"r6", "r8"}, "lower l": {"r6"},
        "ri": {"r2", "r5", "r8"}, "rig": {"r2", "r5", "r8"},
        "bo": {"r6", "r7", "r8"}, "bot": {"r6", "r7", "r8"},
    }
    for zt, want in sorted(named.items()):
        assert _legacy_cells(zt) is None, "构造异常：%r 在旧口径下已命中" % zt
        got = _cells_of(zt)
        assert got is not None, "#736 issue 点名形态 %r 仍漏判" % zt
        assert set(got) == want, "#736 %r 读出 %s（期望 %s）" % (zt, sorted(got), sorted(want))
    for zt in REAL_ZONE_TEXTS:
        assert _cells_of(zt) is not None, "#736 真实语料形态 %r 漏判" % zt


def test_c1_positive_control_full_phrases_resolve():
    """C1 正对照：9 条完整（未截断）方位短语读出各自期望格子（量具不空转）。"""
    for zt, want in sorted(FULL.items()):
        got = _cells_of(zt)
        assert got is not None, "正对照失败：完整短语 %r 读不出" % zt
        assert set(got) == want, "正对照失败：%r 读出 %s（期望 %s）" % (
            zt, sorted(got), sorted(want))


def test_c2_negative_control_legacy_metric_does_miss():
    """C2 负对照：旧口径在同一输入域上**确实**漏判 ⇒ G1/G3 有判别力。"""
    missed = [zt for zt in ["le", "lef", "lower", "lower l", "ri", "rig", "bo", "bot"]
              if _legacy_cells(zt) is None]
    assert len(missed) == 8, "负对照失败：旧口径只漏 %r" % missed
    #   旧口径在 64 字符截断上漏判（本件立案的决定性读数）
    miss64 = 0
    for p in TEMPLATES:
        r = C.parse_prompt(p[:64])
        zt = r["zone_text"] if r else ""
        if _legacy_cells(zt) is None:
            miss64 += 1
    assert miss64 >= 6, "负对照失败：旧口径在 64 截断上只漏 %d/8" % miss64
