# -*- coding: utf-8 -*-
"""test_issue718_boundary_tags_none_guard · `_boundary_consistency` 对 `tags=None` 的守卫（#718）

缺陷（P2 · `lingshu/world/prediction.py`）
-------------------------------------------------------------------------------
`_boundary_consistency()` 里对 `n.tags` 直接做成员判断：

    if n and ("boundary" in n.tags or "不确定" in n.content or "边界" in n.content):

`n.tags` 是 `Optional[List[str]]`：
  · `STNode.tags` 缺省 `[]`，但 `STNode.from_row` 用 **裸** `json.loads(row[12])`
    ⇒ `nodes.tags` 列为 JSON `null` 的行读回即为 `None`；
  · `SpacetimeMemoryEngine.import_all` 按备份行**原样**落该列（白名单列内），
    `tags='null'` 的备份行即产出 `tags=None` 的节点（实测可达）。
⇒ `"boundary" in None` 抛 `TypeError: argument of type 'NoneType' is not iterable`。

本方法是 `_score_route` 的**必经之路**（`predict_routes` 对每条路线都调）
⇒ **一个 `tags=None` 的节点让整批路线评分抛异常**（不是跳过一条路线，是整批）。

同文件**另一处已做防御**（口径自相矛盾的证据）：

    render_semantic_cube_3d:  nodes = [n for n in nodes if n.entity_id == entity_id
                                       or (n.tags and f"ent:{entity_id}" in n.tags)]

即 `(n.tags and ...)` —— None/空 视为「无该标记」，不命中而非抛错。
本件照该口径统一（`(n.tags or [])`），**只修本条点名的这一处**。

修法（最小 · 与同文件另一处同口径）
-------------------------------------------------------------------------------
    - if n and ("boundary" in n.tags or ...)
    + if n and ("boundary" in (n.tags or []) or ...)

断言组（抽掉修复 ⇒ 按标注转红）
-------------------------------------------------------------------------------
  A **真实路径不抛**：`nodes.tags` 列为 `'null'` 的节点经 `predict_routes`
    评分**不得**抛 `TypeError`（旧码在 `_score_route` → `_boundary_consistency` 处抛）
  B **直调不抛且计数口径不变**：`_boundary_consistency(path, nodes)` 对
    `tags=None` 返回 `0.0`（该节点**不**计为「有边界声明」）
  C **正对照·tags 非空**：`tags=['boundary']` 计 1、`tags=[]` 计 0（量具有判别力，
    既非恒 0 也非恒 1）
  D **口径与同文件另一处一致**（静态事实）：`render_semantic_cube_3d` 的
    `(n.tags and …)` 与 `_boundary_consistency` 的 `(n.tags or [])` 都不得再对
    `n.tags` 做**裸**成员判断（源码级判据，防回退）

运行（仓根）：python -X utf8 -m pytest tests/test_issue718_boundary_tags_none_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import (ConditionSpace, EdgeType, LayeredStore,  # noqa: E402
                               STEdge, STNode)
from lingshu.world.prediction import PredictionEngine  # noqa: E402


def _cs():
    return ConditionSpace("测试观测位", "测试工具", (1.0, 2.0), "协议实例运行中")


def _store():
    return LayeredStore(os.path.join(tempfile.mkdtemp(prefix="i718_"), "memory.db"))


def _add_node(store, nid, content, tags=("clean",)):
    store.add_node(STNode(id=nid, content=content, modality="text",
                          spatial_coordinates={}, temporal_coordinate=1.0,
                          condition_space=_cs(), tags=list(tags)))


def _add_edge(store, eid, src, dst, conf=0.5):
    store.add_edge(STEdge(id=eid, source_id=src, target_id=dst,
                          relation_type=EdgeType.CAUSAL,
                          condition_space=_cs(), confidence=conf))


def _set_tags_literal(store, nid, literal):
    """把 `nodes.tags` 列改成给定 **JSON 文本**（`'null'` ⇒ 读回 `None`）。"""
    c = store.conn.cursor()
    c.execute("UPDATE nodes SET tags=? WHERE id=?", (literal, nid))
    store.conn.commit()


class _FakeEngine:
    """最小 engine 门面（与既有 `test_prediction_engine_guards` 同款）。"""

    def __init__(self, store):
        self.store = store
        self.rejected = []

    def register_rejected_path(self, **kw):
        self.rejected.append(kw)
        return "rej_1"

    def add_perception(self, **kw):
        return type("N", (), {"id": "cond_1"})()


def _graph_with_null_tags():
    """a→b 两节点一因果边；`b` 的 tags 列为 JSON `null`（tags 读回 None）。"""
    store = _store()
    _add_node(store, "a", "起点节点内容")
    _add_node(store, "b", "终点节点内容")
    _add_edge(store, "eab", "a", "b")
    _set_tags_literal(store, "b", "null")
    n = store.get_node("b")
    assert n is not None and n.tags is None, \
        "前置失败：未构造出 tags=None 的节点（读回 %r）" % (None if n is None else n.tags,)
    return store, n


def test_a_predict_routes_does_not_raise_on_tags_none():
    """A：tags=None 的节点 ⇒ `predict_routes` 整批评分不得抛 TypeError。"""
    store, n = _graph_with_null_tags()
    pe = PredictionEngine(_FakeEngine(store))
    res = pe.predict_routes(start_id="a", horizon=2)
    routes = res["routes"]
    assert routes, "前置失败：未产出任何路线（量具空转）"
    to_b = [r for r in routes if r["path"][-1] == "b"]
    assert to_b, "前置失败：路线未到达 tags=None 的节点 b: %r" % ([r["path"] for r in routes],)
    assert all(isinstance(r["score"]["boundary"], float) for r in to_b), \
        "评分读数应为 float: %r" % ([r["score"] for r in to_b],)


def test_b_boundary_consistency_direct_call_tags_none_is_zero():
    """B：直调 ⇒ `tags=None` 返回 0.0（该节点不计为「有边界声明」）。"""
    store, n = _graph_with_null_tags()
    pe = PredictionEngine(_FakeEngine(store))
    assert pe._boundary_consistency(["b"], [n]) == 0.0, \
        "【#718】tags=None 不得计为「有边界声明」"
    assert pe._boundary_consistency(["b"], [n, n]) == 0.0, \
        "【#718】重复 None 节点亦不得计数"


def test_c_positive_controls_tags_nonempty_and_empty():
    """C（正对照）：`['boundary']` 计 1、`[]` 计 0 —— 量具既非恒 0 也非恒 1。"""
    store = _store()
    _add_node(store, "hit", "含边界声明的节点", tags=["boundary"])
    # 内容不得含「边界」/「不确定」（否则会命中 content 支，量具失去判别力）
    _add_node(store, "miss", "一条普通的观测内容", tags=[])
    pe = PredictionEngine(_FakeEngine(store))
    n_hit, n_miss = store.get_node("hit"), store.get_node("miss")
    assert n_hit is not None and n_miss is not None, "前置失败：节点未落库"
    assert pe._boundary_consistency(["hit"], [n_hit]) == 1.0, \
        "正对照：tags 含 'boundary' 应计 1.0"
    assert pe._boundary_consistency(["miss"], [n_miss]) == 0.0, \
        "正对照：tags 为空应计 0.0"
    # 内容里出现「不确定」/「边界」也算（既有口径不变）
    _add_node(store, "c1", "这条声明不确定", tags=[])
    assert pe._boundary_consistency(["c1"], [store.get_node("c1")]) == 1.0, \
        "正对照：内容含「不确定」应计 1.0（既有口径不变）"


def test_d_no_bare_tags_membership_left():
    """D：源码级判据——两处都不得再对 `n.tags` 做**裸**成员判断。"""
    src = open(os.path.join(REPO, "lingshu", "world", "prediction.py"),
               encoding="utf-8").read()
    assert "n.tags and" in src, \
        "同文件另一处（render_semantic_cube_3d）的 tags 防御不见了"
    assert "(n.tags or [])" in src, \
        "【#718】`_boundary_consistency` 未改成与同文件另一处同口径的 `(n.tags or [])`"
    assert '"boundary" in n.tags' not in src, \
        "【#718】仍存在对 `n.tags` 的裸成员判断（tags=None ⇒ TypeError）"


def test_e_import_all_null_tags_row_then_predict():
    """E：`tags='null'` 的备份行经 import_all 落库后，整批评分仍不得抛。"""
    store = _store()
    _add_node(store, "a", "起点节点内容")
    # b 由备份行式 INSERT 导入（tags 列字面 'null'），再补边（边需两端已落库）
    cs = json.dumps({"existence_constraint": "a", "tool_permission": "b",
                     "time_window": [1, 2], "reason": "c"})
    c = store.conn.cursor()
    c.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("b", "终点节点内容", "text", "{}", 1.0, cs, 0.5, 0.5,
               "knowledge", 0, 1.0, 1.0, "null", "{}", "{}", None))
    store.conn.commit()
    _add_edge(store, "eab", "a", "b")
    n = store.get_node("b")
    assert n is not None and n.tags is None, \
        "前置失败：备份行式 tags='null' 未产出 tags=None（读回 %r）" % (
            None if n is None else n.tags,)
    pe = PredictionEngine(_FakeEngine(store))
    routes = pe.predict_routes(start_id="a", horizon=2)["routes"]
    assert routes, "前置失败：未产出任何路线"
    assert all(isinstance(r["score"]["boundary"], float) for r in routes), \
        "【#718】tags='null' 行落库后整批评分抛异常"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header"]))
