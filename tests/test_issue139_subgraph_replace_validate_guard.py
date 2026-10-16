# -*- coding: utf-8 -*-
"""#139 追评守卫：subgraph_replace 的「抛错 = 零改动」必须对**字段类型**类失败也成立。

缺陷（issue #139 追评，报告人最小复现）：
  前置校验 `_validate` 只查「是 dict 且有 `id`」，而 `_attach` 还会对
  `importance` / `confidence` / `spatial_coordinates.image2d` 分量做 `float()`。
  新子树里任一**非首个**节点带非数值字段（`"importance": "high"`、
  坐标分量为 `null`、`"confidence": "0.5x"`）时，`_attach` 在**挂入中途**抛
  `ValueError`/`TypeError`，而此前已挂的节点各自落库 ⇒ parent 下留下半棵无主
  新子树、旧子树同时还在；调用方按「抛错 = 没改动」重试，每次再多挂半棵
  （修复前本机实测：两次调用后 parent 的 hierarchical 后代数 1 → 5）。

修复后语义：递归挂入 ＋ 旧子树删除收进**单一事务边界**（`store._tx()`）——
任一步抛错即整体回滚，parent 的后代集合逐节点不变、旧子树完好、无残留新节点；
且该边界具备嵌套感知（在调用方事务内调用时只开 SAVEPOINT，不报
`cannot start a transaction within a transaction`）。

断言组（把 `subgraph_replace` 的单一事务边界抽掉 ⇒ G1–G3、G6 变红）：
  G1 非首节点 `importance="high"` ⇒ 抛错，且 parent 后代集合与节点总数不变。
  G2 非首节点坐标分量为 `None` ⇒ 同上。
  G3 非首节点 `confidence="0.5x"` ⇒ 同上。
  G4 重试两次仍抛错 ⇒ 库态与首次失败后逐节点相同（不叠加半棵）。
  G5 正对照：合法子树照旧整体生效（旧子树消失、新根挂到 parent）。
  G6 嵌套调用：在调用方 `store._tx()` 内调用不抛「事务嵌套」错，且照旧生效。

运行（仓根）：python -X utf8 -m pytest tests/test_issue139_subgraph_replace_validate_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import EdgeType, SpacetimeMemoryEngine  # noqa: E402


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _mk_parent_and_old(eng):
    """建 parent 与挂在它下面的旧子树（1 个旧子根），返回 (parent_id, old_id)。"""
    parent = eng.add_perception("parent-部件", skip_dedup=True)
    old = eng.add_perception("旧子根", skip_dedup=True)
    eng.add_edge(old.id, parent.id, relation_type=EdgeType.HIERARCHICAL)
    return parent.id, old.id


def _desc_ids(eng, root_id):
    rows = eng.store.traverse(root_id, relation_types=["hierarchical"],
                              direction="in", max_depth=64)
    return {r["node_id"] for r in rows}


def _snapshot(eng, parent_id):
    """parent 的 hierarchical 后代集合 ＋ 全库节点 id 集合。"""
    desc = _desc_ids(eng, parent_id)
    all_ids = {r[0] for r in eng.store.conn.execute("SELECT id FROM nodes")}
    return desc, all_ids


def _bad_subtree(second_node):
    """根 + 两个子节点，第二个（非首个）节点带非法字段。"""
    return {"id": "head2", "content": {"part": "新猫头"}, "importance": 0.6,
            "spatial_coordinates": {"image2d": [1, 2]},
            "subgraph": {"nodes": [
                {"id": "ear_l", "content": {"part": "左耳"}, "importance": 0.5},
                second_node]}}


_BAD_CASES = {
    "importance-high": {"id": "ear_r", "content": {"part": "右耳"},
                        "importance": "high"},
    "coord-none": {"id": "ear_r", "content": {"part": "右耳"}, "importance": 0.5,
                   "spatial_coordinates": {"image2d": [None, 1]}},
    "confidence-0.5x": {"id": "ear_r", "content": {"part": "右耳"},
                        "importance": 0.5, "confidence": "0.5x"},
}


@pytest.mark.parametrize("label", sorted(_BAD_CASES))
def test_g1_g3_field_type_error_leaves_zero_change(label):
    """G1–G3：非首节点字段类型非法 ⇒ 抛错，parent 后代/全库节点集合逐节点不变。"""
    eng = _engine()
    parent_id, old_id = _mk_parent_and_old(eng)
    before = _snapshot(eng, parent_id)
    with pytest.raises((ValueError, TypeError)):
        eng.subgraph_replace(parent_id, old_id, _bad_subtree(_BAD_CASES[label]))
    after = _snapshot(eng, parent_id)
    assert after[0] == before[0], "半截落库：parent 后代集合被改动"
    assert after[1] == before[1], "半截落库：库内节点总数被改动"
    assert eng.store.get_node(old_id) is not None, "旧子树被误删"


def test_g4_retry_does_not_accumulate_half_subtree():
    """G4：调用方重试（抛错=没改动）⇒ 库态与首次失败后逐节点相同。"""
    eng = _engine()
    parent_id, old_id = _mk_parent_and_old(eng)
    before = _snapshot(eng, parent_id)
    for _attempt in (1, 2):
        with pytest.raises((ValueError, TypeError)):
            eng.subgraph_replace(parent_id, old_id,
                                 _bad_subtree(_BAD_CASES["importance-high"]))
    assert _snapshot(eng, parent_id) == before, "重试叠加了半棵新子树"


def test_g5_valid_subtree_still_replaces():
    """G5 正对照：合法子树整体生效（旧子树消失、新根挂到 parent）。"""
    eng = _engine()
    parent_id, old_id = _mk_parent_and_old(eng)
    good = {"id": "head3", "content": {"part": "新猫头"}, "importance": 0.6,
            "spatial_coordinates": {"image2d": [1, 2]},
            "subgraph": {"nodes": [
                {"id": "ear_l", "content": {"part": "左耳"}, "importance": 0.5},
                {"id": "ear_r", "content": {"part": "右耳"}, "importance": 0.5,
                 "confidence": 0.8}]}}
    res = eng.subgraph_replace(parent_id, old_id, good)
    assert res["new_nodes"] == 3
    assert res["removed_nodes"] == 1
    assert eng.store.get_node(old_id) is None, "旧子树未被移除"
    assert res["new_root_id"] in _desc_ids(eng, parent_id), "新根未挂到 parent 下"


def test_g6_nested_call_inside_caller_transaction():
    """G6：在调用方事务内调用（重入）不得报「事务嵌套」错，且照旧生效。"""
    eng = _engine()
    parent_id, old_id = _mk_parent_and_old(eng)
    good = {"id": "head4", "content": {"part": "新猫头"}, "importance": 0.6,
            "subgraph": {"nodes": [
                {"id": "ear_l", "content": {"part": "左耳"}, "importance": 0.5}]}}
    with eng.store._tx():
        res = eng.subgraph_replace(parent_id, old_id, good)
    assert res["new_nodes"] == 2
    assert eng.store.get_node(old_id) is None
    # 反向：调用方事务内失败 ⇒ 整体回滚（含调用方本段自己的写）
    eng2 = _engine()
    parent2, old2 = _mk_parent_and_old(eng2)
    before2 = _snapshot(eng2, parent2)
    with pytest.raises(RuntimeError):
        with eng2.store._tx():
            eng2.add_perception("调用方本段的写", skip_dedup=True)
            try:
                eng2.subgraph_replace(parent2, old2, _bad_subtree(
                    _BAD_CASES["coord-none"]))
            except (ValueError, TypeError):
                pass          # 挂入失败（本件要修的路径）⇒ 调用方按「抛错=没改动」处理
            raise RuntimeError("调用方后续失败")
    assert _snapshot(eng2, parent2) == before2, "调用方事务未整体回滚"
