# -*- coding: utf-8 -*-
"""test_issue727_ingest_nonfinite_coord_guard —— `ingest_scene` 的入库卫生守卫。

缺陷（lingshu#727 · P2）
--------------------------------------------------------------------------
`scene_model.ingest_scene` 的文本解析段只 `except ValueError: continue` 防
**个数错**，对**非有限值**（`nan` / `inf` / `-inf`，`float()` 解析得出来）照单
全收 ⇒ 非有限坐标被写进 `spatial_coordinates` **入库**（写库前无任何校验）。
下游 `WorldModel.apply_relations` 用 `math.hypot(dx, dz)` 与 `dx >= 0` 判约束，
NaN 使这些比较恒为 False ⇒ 该实体的关系约束**静默失判**（位置永不修正、无告警）。

实现侧口径（本守卫的判据）
--------------------------------------------------------------------------
选**写库前拒绝该行**（`continue`，该实体不入库），**不是**写库后再清洗。理由：
  · 正文 `场景实体 …位于 (x,y,z)` 与 `spatial_coordinates` 是同一份观测的两个
    投影 —— 写库后清洗只能改坐标列，正文与坐标列当场自相矛盾；
  · 非有限坐标不是"可用但需修正"的读数，而是**不可用证据**（同仓 #402 /
    #418-F3 口径：NaN/±Inf 一律不算可用证据），故不落库、不留半成品节点；
  · 与**同一段**既有的个数错口径（`except ValueError: continue`）逐字一致 ——
    同一段输入不会因两种坏法得到两种语义（一种静默跳过、一种静默改值）；
  · 拒绝是**可判读**的：返回的 `written` 长度即实际写入节点数（既有返回体，
    不新增字段），调用方与输入行数一比即可发现被拒行。
依据：issue #727「修复建议：解析后加 `if not all(math.isfinite(v) ...): continue`，
与 #347/#398 同口径」＋《PR 审核细则 v0.1》§三.3 边界诚实。

守卫用例
--------------------------------------------------------------------------
G1 正对照（量具能测到已知正确形态）：合法坐标行必须真入库、`spatial_coordinates`
   逐分量精确、经 `load_world_from_memory` 重建回同一坐标（防"恒空假绿"）。
G2 正对照（"跳过"机制确实可观测）：个数错/非数行照旧被跳过（`written` 变短）
   —— 证明 G3 的判据不是"这个函数什么都不写"。
G3 缺陷态：`nan` / `inf` / `-inf` 行**零入库**（改前：各写 1 条，`sc["x"]` 为
   `nan`/`inf`）。
G4 库卫生：混合输入（1 合法 + 3 非有限）⇒ 只有合法行入库，且**全库**节点的
   `spatial_coordinates` 三轴全有限（改前该断言扫到 nan/inf）。
G5 缺陷态可判读：非有限行被拒时**不留半成品节点**、不发状态写；`written` 长度
   与合法行数相等（拒绝口径可判读，不需要新字段）。
G6 范围控制：合法行的事件照旧；缺实体的 relations 照旧 `ValueError`（本修只加
   非有限闸，不放宽也不收紧既有判据）。

运行（lingshu 仓根）：
    python -X utf8 -m pytest tests/test_issue727_ingest_nonfinite_coord_guard.py -q --no-header
退出码：0 = 全过；非 0 = 有断言失败。
"""
from __future__ import annotations

import math

import pytest

from lingshu.core.core import MemoryLayer, SpacetimeMemoryEngine
from lingshu.world.scene_model import (
    WorldModel,
    ingest_scene,
    load_world_from_memory,
)


class _Agent:
    """scene_model 消费面：同时提供 `.store` 与 `.engine`。"""

    def __init__(self, engine):
        self.engine = engine
        self.store = engine.store


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _nodes(engine):
    return engine.store.query_nodes(layer=MemoryLayer.KNOWLEDGE, limit=50)


def _run(desc, relations=None):
    """跑一条场景描述 ⇒ (ids, 全库 spatial_coordinates, 重建世界坐标)。"""
    assert desc, "守卫输入不得为空"
    e = _engine()
    ids = ingest_scene(_Agent(e), desc, relations=relations)
    scs = [dict(n.spatial_coordinates or {}) for n in _nodes(e)]
    wm = load_world_from_memory(e.store)
    pos = {k: tuple(v.pos) for k, v in wm.entities.items()}
    return ids, scs, pos


def test_727_positive_control_legal_coords_are_written():
    """G1 正对照：合法坐标真入库、逐分量精确、重建回同一坐标。"""
    ids, scs, pos = _run("肥鱼|fatfish|0,0.85,5|shy")
    assert len(ids) == 1, ids
    assert scs == [{"x": 0.0, "y": 0.85, "z": 5.0}], scs
    assert pos == {"肥鱼": (0.0, 0.85, 5.0)}, pos


def test_727_positive_control_bad_arity_rows_are_skipped():
    """G2 正对照：个数错/非数行照旧被跳过（"跳过"机制可观测）。"""
    for bad in ("肥鱼|fatfish|5|shy",        # 1 个值
                "肥鱼|fatfish|1,2|shy",      # 2 个值
                "肥鱼|fatfish|1,2,3,4|shy",  # 4 个值
                "肥鱼|fatfish|abc,2,3|shy"):  # 非数
        ids, scs, pos = _run(bad)
        assert ids == [] and scs == [] and pos == {}, (bad, ids, scs, pos)
    # 混合：坏行跳、好行留（"跳过"不是整条拒绝）
    ids, scs, pos = _run("肥鱼|fatfish|5|shy\n桌子|table|1.5,0.45,6|neutral")
    assert len(ids) == 1, ids
    assert scs == [{"x": 1.5, "y": 0.45, "z": 6.0}], scs


def test_727_nonfinite_coords_are_rejected_before_write():
    """G3 缺陷态：nan / inf / -inf 行零入库（改前各写 1 条）。"""
    for bad in ("肥鱼|fatfish|nan,0,5|shy",
                "肥鱼|fatfish|inf,0,5|shy",
                "肥鱼|fatfish|-inf,0,5|shy",
                "肥鱼|fatfish|NaN,0,5|shy",
                "肥鱼|fatfish|1,nan,5|shy",
                "肥鱼|fatfish|1,0,-inf|shy"):
        ids, scs, pos = _run(bad)
        assert ids == [], (
            "#727 %r 被接受并写库（改前 spatial_coordinates 带 nan/inf）：%r"
            % (bad, ids))
        assert scs == [], ("#727 非有限坐标不得落库：%r" % (scs,))
        assert pos == {}, pos


def test_727_mixed_input_keeps_library_clean():
    """G4 库卫生：混合输入只留合法行，且全库坐标三轴全有限。"""
    desc = ("a|box|nan,0,5|neutral\n"
            "b|box|1,2,3|neutral\n"
            "c|box|inf,0,5|neutral\n"
            "d|box|4,5,-inf|neutral")
    ids, scs, pos = _run(desc)
    assert len(ids) == 1, ("#727 混合输入应只写 1 条（合法行 b）：%r" % (ids,))
    assert scs == [{"x": 1.0, "y": 2.0, "z": 3.0}], scs
    assert pos == {"b": (1.0, 2.0, 3.0)}, pos
    for sc in scs:
        assert all(math.isfinite(sc[k]) for k in ("x", "y", "z")), (
            "#727 库内出现非有限坐标（入库卫生破了）：%r" % (sc,))


def test_727_rejected_rows_leave_no_partial_node_or_state():
    """G5 可判读：被拒行不留半成品节点；`written` 长度＝合法行数。"""
    # 名字取互不为子串的两串（否则 "keeper" in "…" 这类子串判断会误判）
    desc = "ghost|box|nan,0,5|happy\nkeeper|box|1,2,3|sad"
    e = _engine()
    ids = ingest_scene(_Agent(e), desc)
    assert len(ids) == 1, ids
    nodes = _nodes(e)
    assert len(nodes) == 1, [n.content for n in nodes]
    assert "ghost" not in nodes[0].content, (
        "#727 被拒实体 ghost 留下了半成品节点：%r" % (nodes[0].content,))
    assert "keeper" in nodes[0].content, nodes[0].content
    # 合法行的状态写入不受影响
    wm = load_world_from_memory(e.store)
    assert wm.entities["keeper"].state == "sad", wm.entities["keeper"].state


def test_727_range_control_legal_path_and_relations_unchanged():
    """G6 范围控制：合法行照旧；缺实体 relations 照旧 ValueError。"""
    scene = WorldModel()
    scene.add_entity("桌子", "table", (1.5, 0.45, 6.0), eid="t1")
    scene.add_entity("肥鱼", "fatfish", (0.0, 0.85, 5.0), eid="f1")
    scene.relation("桌子", "right_of", "肥鱼")
    e = _engine()
    ids = ingest_scene(_Agent(e), scene)
    assert len(ids) == 2, ids
    wm = load_world_from_memory(e.store)
    assert wm.entities["桌子"].relations == [("肥鱼", "right_of")], (
        wm.entities["桌子"].relations)
    # 约束求解仍活着（正对照：合法数据下 right_of 真的会把桌子挪到肥鱼右侧）
    assert wm.entities["桌子"].pos[0] >= 1.2, wm.entities["桌子"].pos
    # 缺实体 relations 照旧显式拒绝
    with pytest.raises(ValueError):
        ingest_scene(_Agent(_engine()),
                     "a|box|1,2,3|neutral\nb|box|4,5,6|neutral",
                     relations=[("a", "near", "zzz")])
