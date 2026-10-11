# -*- coding: utf-8 -*-
"""#183 标签检索 `LIKE '%tag%'` 子串误配 + 通配符注入 —— 守卫

缺陷（lingshu issue #183 · [core/存储]）：
  `get_nodes_by_tag` / `search_content` / `search_skills` 用
  `LIKE '%{tag}%'` 且不转义 `%` / `_`：
  ① **子串误配**——`LIKE '%ent:car_1%'` 命中 `ent:car_12` / `ent:carX1`；
     `cycle:c1` 命中 `cycle:c10…c19`（实体/循环身份在存储层被合并）；
  ② **通配符注入**——`_` 匹配任意单字符、`%` 匹配全部 ⇒
     `get_nodes_by_tag("%")` / `search_content("%")` 返回整库。

修法：
  · `get_nodes_by_tag` 走 `json_each` **元素级精确匹配**（`tags` 列是 JSON 数组
    文本，见 `_dumps_tags`——故不能写 `tags = ?`）；坏行经
    `CASE WHEN json_valid(...) THEN ... ELSE '[]' END` 兜底，不让非法 JSON 抛错。
  · `search_content` 的预筛 `LIKE` 统一经 `_escape_like`（转义 `\\ % _`）并加
    `ESCAPE '\\'`；并**仅对「纯通配符查询」跳过全表二元组兜底**（否则空预筛
    会把整库当零分候选返回，`search_content("%")` 仍返回全库）。
  · `search_skills` 的转义由 #200 已收口（`_escape_like` + `ESCAPE`），本件不重复改。

★调用方 survey（#183 改前，强制项）：仓内 `get_nodes_by_tag` 的全部调用点
  （`vprim` ×2、`cycle:{id}`、`cycle_record_full`、`scene_model.
  load_world_from_memory(tag="spatial")`）传入的都是**完整标签字面量**，
  **无一依赖子串/前缀语义** ⇒ 直接改精确匹配，**不另设**
  `get_nodes_by_tag_prefix`（一刀切不成立，因为无人需要子串）。
  （`visual_check` 的 `screen_state` 走独立 SQL，不经本方法。）

判据来源：公开 issue #183（`_b4_issue_183.json`：`ent:car_1` 命中 3 个实体、
`search_content("%")` 返回全库；建议 1/2/3）；阈值本体**一律未改**。

断言组（抽掉修复关键一行 ⇒ 红）：
  A 前缀/子串不再误配：`ent:car_1` 只回含该标签者；`cycle:c1` 不回 `c10…c19`
  B 通配符注入封死：`get_nodes_by_tag("%")` / `("%")`/`("_")` 只回字面含该字符者
  C `search_content("%")` / `("_")` 只回字面含该字符者（不再返回整库）
  D ★性质测试（issue 建议 3）：tag A 是 tag B 的前缀 ⇒ 两者结果互不包含
  E 坏行（非法 JSON tags）不毒化 `get_nodes_by_tag`（返回空而非抛错）

运行（仓根）：python -X utf8 -m pytest tests/test_issue183_tag_like_escape_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    SpacetimeMemoryEngine, STNode, ConditionSpace, MemoryLayer,
)


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _add(e, content, tags=None):
    e.add_perception(content, tags=list(tags or []), skip_dedup=True)


def test_a_prefix_substring_no_longer_mismatches():
    """A：`ent:car_1` 不得命中 `ent:car_12` / `ent:carX1`；`cycle:c1` 不得命中 `c10`。"""
    e = _engine()
    _add(e, "frame of ent:car_1", ["ent:car_1"])
    _add(e, "frame of ent:car_12", ["ent:car_12"])
    _add(e, "frame of ent:carX1", ["ent:carX1"])
    _add(e, "cycle one", ["cycle:c1"])
    _add(e, "cycle ten", ["cycle:c10"])
    got = sorted(n.content for n in e.store.get_nodes_by_tag("ent:car_1"))
    assert got == ["frame of ent:car_1"], (
        "子串误配：ent:car_1 命中 %r（旧缺陷＝3 个实体混为一个）" % (got,))
    got2 = sorted(n.content for n in e.store.get_nodes_by_tag("cycle:c1"))
    assert got2 == ["cycle one"], (
        "子串误配：cycle:c1 命中 %r" % (got2,))


def test_b_tag_wildcard_injection_sealed():
    """B：`%` / `_` 作为**标签精确值**只命中该标签本身（旧缺陷＝返回整库）。

    `get_nodes_by_tag` 是**标签精确检索**：`get_nodes_by_tag("%")` 只应回标签
    **恰为** `"%"` 的节点，`"a%b"` / `"a_b"` 等含该字符但不相等的标签**不**命中
    （旧缺陷下 `LIKE '%'` 返回整库）。
    """
    e = _engine()
    _add(e, "普通节点", ["normal"])
    _add(e, "下划线标签", ["a_b"])
    _add(e, "百分号标签", ["a%b"])
    _add(e, "字面百分号", ["%"])
    _add(e, "字面下划线", ["_"])
    assert [n.content for n in e.store.get_nodes_by_tag("%")] == ["字面百分号"], (
        "get_nodes_by_tag('%%') 非精确匹配：%r"
        % ([n.content for n in e.store.get_nodes_by_tag("%")],))
    assert [n.content for n in e.store.get_nodes_by_tag("_")] == ["字面下划线"], (
        "get_nodes_by_tag('_') 非精确匹配：%r"
        % ([n.content for n in e.store.get_nodes_by_tag("_")],))
    # 无字面 % / _ 标签时须为空（旧缺陷＝整库）
    e2 = _engine()
    _add(e2, "苹果是红色的", ["水果"])
    assert e2.store.get_nodes_by_tag("%") == [], "无 % 标签却回节点（旧缺陷）"
    assert e2.store.get_nodes_by_tag("_") == [], "无 _ 标签却回节点（旧缺陷）"


def test_c_search_content_wildcard_no_longer_returns_whole_store():
    """C：`search_content("%")` / `("_")` 只回字面含该字符者（旧缺陷＝整库）。"""
    e = _engine()
    _add(e, "苹果是红色的", ["水果"])
    assert e.store.search_content("%") == [], (
        "search_content('%%') 返回整库（旧缺陷）：%d 条"
        % (len(e.store.search_content("%")),))
    assert e.store.search_content("_") == [], (
        "search_content('_') 返回整库（旧缺陷）：%d 条"
        % (len(e.store.search_content("_")),))
    _add(e, "折扣 50% 起", ["促销"])
    _add(e, "under_score 命名", ["code"])
    got_pct = sorted(n.content for n, _ in e.store.search_content("%"))
    assert got_pct == ["折扣 50% 起"], "search_content('%') 命中 %r" % (got_pct,)
    got_us = sorted(n.content for n, _ in e.store.search_content("_"))
    assert got_us == ["under_score 命名"], "search_content('_') 命中 %r" % (got_us,)


def test_d_prefix_tag_property_results_disjoint():
    """D（issue 建议 3 的性质测试）：tag A 是 tag B 的前缀 ⇒ 结果互不包含。"""
    e = _engine()
    _add(e, "n1", ["ent:car_1"])
    _add(e, "n2", ["ent:car_12"])
    a = {n.id for n in e.store.get_nodes_by_tag("ent:car_1")}
    b = {n.id for n in e.store.get_nodes_by_tag("ent:car_12")}
    assert a and b, "前置：两组各自非空"
    assert not (a & b), "前缀标签结果相交（子串误配未封死）：%r" % (a & b,)
    assert a != b, "前缀标签结果相同（身份被合并）"


def test_e_malformed_tags_row_does_not_poison_query():
    """E：tags 列非法 JSON 的坏行不得让 `get_nodes_by_tag` 抛错（容错口径）。"""
    e = _engine()
    _add(e, "good", ["target"])
    # 直接注入一行 tags 为非法 JSON（模拟外部卡/损坏行）
    e.store.conn.execute(
        "INSERT INTO nodes (id, content, modality, spatial_coordinates,"
        " temporal_coordinate, condition_space, importance, confidence, layer,"
        " access_count, last_access, created_at, tags, semantic_coordinates,"
        " state_attributes, entity_id) VALUES"
        " ('bad','bad node','text','{}',0.0,'{}',0.5,0.5,'knowledge',0,0.0,0.0,"
        " 'not-json', '{}','{}', NULL)")
    e.store.conn.commit()
    got = [n.content for n in e.store.get_nodes_by_tag("target")]
    assert got == ["good"], "坏行毒化查询：%r" % (got,)
