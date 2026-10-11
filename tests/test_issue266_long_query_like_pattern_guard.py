# -*- coding: utf-8 -*-
"""#266 超长查询不得让 LIKE 预筛自己抛异常 —— 守卫

缺陷（lingshu issue #266，判定成立）：`search_content` 的 LIKE 预筛把每个检索词
（含 `expand_query_terms` 的 `terms[0]`＝**整段查询原文**）拼成一条 `%{term}%`
模式。SQLite 上游常量 `SQLITE_MAX_LIKE_PATTERN_LENGTH` 默认 50000 字节 ⇒ 查询一过
约 1.67 万汉字（49998 字节），这条 `execute` 自身抛
`OperationalError: LIKE or GLOB pattern too complex`；而「LIKE 落空 → 全表二元组
Jaccard」的兜底在预筛之后、走不到。`recall` / `semantic_search` 走同一条
`search_content`，三入口同报错；反思链路与长期记忆门的 `try/except` 把它吞成
「无相关记忆」，同一份长快照写几次就留几份永久保护副本。

修法（issue 补充帖「更稳的修法」）：只在检索词**字节数**超过阈值时把整句模式切成
若干短片段（`_LIKE_FRAGMENTS`，每片 ≤ `_LIKE_PATTERN_MAX_BYTES`），短查询的整句
LIKE 行为逐字不变。

断言组（抽掉 `_like_fragments` 切分即红）：
  J1 三入口不抛：20000 汉字（60000 字节）查询在 `recall` / `search_content` /
     `semantic_search` 上都必须正常返回（旧缺陷：三入口同抛 OperationalError）。
  J2 超长仍能召回：库里那条短记忆（正文与查询前缀连续）在长查询下必须被召回
     （旧缺陷：异常被上层吞成「无相关记忆」）。
  J3 短查询行为不变：短查询 `_like_fragments` 返回原词、模式数与修前一致
     （防「顺手把短查询也切碎」）。
  J4 片段预算：超长词的片段数 ≤ 上限，且每片编码后 ≤ 阈值（防碎片数/字节越界）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue266_long_query_like_pattern_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    LayeredStore, SpacetimeMemoryEngine,
    _LIKE_FRAGMENT_CAP, _LIKE_PATTERN_MAX_BYTES,
)

_BASE = "请结合之前的记忆回答：用户对青霉素过敏吗？"


def _long_query(n_chars: int) -> str:
    return _BASE + "。" * (n_chars - len(_BASE))


def test_j1_three_entrypoints_do_not_raise_on_long_query():
    """J1：60000 字节查询在三入口都不得抛 OperationalError。"""
    e = SpacetimeMemoryEngine(":memory:")
    e.add_perception("用户对青霉素过敏，禁用阿莫西林", importance=0.9)
    q = _long_query(20000)
    assert len(q.encode("utf-8")) > 50000
    for name in ("recall", "search_content", "semantic_search"):
        r = getattr(e, name)(q)      # 旧缺陷：此处抛 OperationalError
        assert isinstance(r, list), "%s 未返回列表" % name


def test_j2_long_query_still_recalls_short_memory():
    """J2：长查询下那条短记忆必须仍被召回（旧缺陷：异常→上层吞成空）。"""
    e = SpacetimeMemoryEngine(":memory:")
    e.add_perception("用户对青霉素过敏，禁用阿莫西林", importance=0.9)
    hits = e.search_content(_long_query(20000))
    assert hits, "长查询下召回落空（#266：LIKE 预筛抛异常被吞）"
    assert any("青霉素" in n.content for n, _ in hits), [n.content for n, _ in hits]


def test_j3_short_query_pattern_unchanged():
    """J3：短词原样返回（短查询行为不得被顺手切碎）。"""
    assert LayeredStore._like_fragments("备份") == ["备份"]
    assert LayeredStore._like_fragments("user allergy") == ["user allergy"]
    # 恰在阈值边界内的长词也不切（含 `%%` 两字节）
    edge = "a" * (_LIKE_PATTERN_MAX_BYTES - 2)
    assert LayeredStore._like_fragments(edge) == [edge]


def test_j4_fragment_budget_bounded():
    """J4：超长词片段数 ≤ 上限，且每片编码后 ≤ 阈值。"""
    frags = LayeredStore._like_fragments(_long_query(20000))
    assert 1 < len(frags) <= _LIKE_FRAGMENT_CAP, len(frags)
    for f in frags:
        assert len(f.encode("utf-8")) <= _LIKE_PATTERN_MAX_BYTES, len(f.encode("utf-8"))
