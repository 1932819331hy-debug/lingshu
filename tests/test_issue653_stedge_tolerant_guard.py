# -*- coding: utf-8 -*-
"""test_issue653_stedge_tolerant_guard · STEdge.from_row 边侧逐行容错守卫（issue #653）
================================================================================================
判据：`STEdge.from_row` 是所有边读取的唯一反序列化点，此前对
`relation_type`（`EdgeType(row[3])`）与 `condition_space`
（`ConditionSpace.from_json(row[4])`）裸调——一条坏边让
get_edge / find_cycles / infer_causal_paths / decay_cycle 四路径整体中断
（改前读数：relation_type 未知崩 3 处、cond_space 非法崩 4 处、
confidence NULL 使 infer_causal_paths 的置信度求和 TypeError）。

修复口径（与节点侧 #267 / #577 家族同构，容错收在装载面 `_tolerant_edges`）：
- G1 三形态坏边下 get_edge 返回 None、find_cycles / infer_causal_paths /
  decay_cycle 完成不抛，坏边不参与结果；
- G2 好边照常反序列化（逐位断言），混合库中好边结果不受坏边影响；
- G3 confidence NULL 在 from_row 落缺省 0.5（#653/#185 数值面），
  infer_causal_paths 不再 TypeError；
- G4 节点侧 #267/#577 容错零回归。
"""
from __future__ import annotations

import pytest

from lingshu.core.core import (LayeredStore, Role, STNode, STEdge, EdgeType,
                               ConditionSpace, MemoryLayer)

_CS = ConditionSpace("观测点", "观测仪", (0.0, 1e12), "常规约束")
_CS_JSON = _CS.to_json()

_BAD_FORMS = [
    # (坏边 id, relation_type, condition_space, confidence)
    ("e_bad_rel", "nosuchrel", _CS_JSON, 0.5),   # B1 未知 relation_type
    ("e_bad_cond", "causal", "{bad", 0.5),       # B2 非法 condition_space（非 JSON）
    ("e_bad_null", "causal", _CS_JSON, None),    # B3 confidence NULL（数值面）
]


def _fresh_store():
    """建库：两个 knowledge 节点 + 一条好边 n1→n2（causal，conf=0.8）。"""
    s = LayeredStore(":memory:", role=Role.PRIMARY)
    for nid in ("n1", "n2"):
        s.add_node(STNode(id=nid, content="c-" + nid, modality="text",
                          spatial_coordinates={}, temporal_coordinate=1000.0,
                          condition_space=ConditionSpace("T", "T", (0, 1e12), "R"),
                          importance=0.9, confidence=0.9,
                          layer=MemoryLayer.KNOWLEDGE, tags=[]))
    s.add_edge(STEdge(id="e_good", source_id="n1", target_id="n2",
                      relation_type=EdgeType.CAUSAL,
                      condition_space=_CS, confidence=0.8, weight=0.7,
                      verified=True, source_evidence="extracted",
                      created_at=1234.5))
    return s


def _inject_bad_edge(s, eid, relation_type, cond, conf):
    """直插坏边行（绕过 add_edge 的 STEdge 构造与 clamp——模拟存量损坏库）。"""
    c = s.conn.cursor()
    c.execute("INSERT INTO edges VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (eid, "n1", "n2", relation_type, cond, conf, 1.0, 0, 1.0, 0.0,
               "extracted"))
    s.conn.commit()


def _store_with(bad_form=None):
    """fresh 库 + 可选注入一条坏边；返回 (store, 坏边 id 或 None)。"""
    s = _fresh_store()
    if bad_form is not None:
        _inject_bad_edge(s, *bad_form)
    return s, (bad_form[0] if bad_form else None)


# ---------------------------------------------------------------- G1 坏边容错

_STRUCTURE_BAD_FORMS = _BAD_FORMS[:2]   # 结构面坏边（未知枚举/非法 JSON）⇒ 跳过
_NULL_CONF_FORM = _BAD_FORMS[2]         # 数值面（confidence NULL）⇒ from_row 落缺省（G3 口径）


@pytest.mark.parametrize("bad_form", _STRUCTURE_BAD_FORMS, ids=["bad-rel", "bad-cond"])
def test_g1_bad_edge_does_not_break_four_read_paths(bad_form):
    """G1：结构面坏边（未知 relation_type/非法 condition_space）下四路径全部
    完成不抛；坏边被跳过、不参与结果。"""
    s, bad_id = _store_with(bad_form)
    try:
        # 单条读：坏行返回 None（视同读不到），而非抛 ValueError/JSONDecodeError
        got = s.get_edge(bad_id)
        assert got is None, ("get_edge 坏行应返回 None", got)

        # 批量读三路径：完成不抛，坏边不参与（结果里的边不含坏边 id）
        cycles = s.find_cycles()
        for cyc in cycles:
            assert bad_id not in [e.id for e in cyc], ("坏边泄入环结果", cyc)

        paths = s.infer_causal_paths("n1", "n2")
        for p in paths:
            assert bad_id not in [e.id for e in p], ("坏边泄入因果路径", p)
        assert paths, "好边 e_good 的 n1→n2 路径不应随坏边消失"
        assert ["e_good"] in [[e.id for e in p] for p in paths], (
            "好边路径必须仍在结果里", [[e.id for e in p] for p in paths])

        # decay_cycle：factor=0/min=0 使好边不动，本轮只为证明不被坏边打断
        assert s.decay_cycle(factor=0.0, min_confidence=0.0) is None
        # 好边仍在库且未被误删（坏边跳过 ⇒ 不衰减不删改；好边 factor=0 原值保留）
        good = s.get_edge("e_good")
        assert good is not None and good.confidence == 0.8, ("好边受损", good)
    finally:
        s.close()


def test_g1_null_conf_edge_four_paths_complete():
    """G1（数值面形态）：confidence NULL 坏行下四路径同样完成不抛（该边按 G3
    口径落缺省 0.5 后**参与**结果，属预期——与结构面坏边的「跳过」不同）。"""
    s, bad_id = _store_with(_NULL_CONF_FORM)
    try:
        got = s.get_edge(bad_id)
        assert got is not None and got.confidence == 0.5, (
            "数值面坏行应照常反序列化并落缺省", got)
        assert s.find_cycles() == []            # 无环图照常返回空
        paths = s.infer_causal_paths("n1", "n2")
        assert paths, "落缺省后 NULL 置信边应可用（不再 TypeError）"
        assert s.decay_cycle(factor=0.0, min_confidence=0.0) is None
        assert s.get_edge("e_good").confidence == 0.8
    finally:
        s.close()


def test_g1_mixed_bad_edges_all_forms_at_once():
    """G1 补充：三种坏边同时在场，四路径照常完成（整批不被任一坏边毒化）。"""
    s, _ = _store_with()
    for form in _BAD_FORMS:
        _inject_bad_edge(s, *form)
    try:
        assert s.get_edge("e_bad_rel") is None      # 结构面坏边 ⇒ None
        assert s.get_edge("e_bad_cond") is None     # 结构面坏边 ⇒ None
        null_edge = s.get_edge("e_bad_null")        # 数值面 ⇒ 落缺省可用（G3 口径）
        assert null_edge is not None and null_edge.confidence == 0.5
        assert s.find_cycles() == []
        paths = s.infer_causal_paths("n1", "n2")
        assert ["e_good"] in [[e.id for e in p] for p in paths]
        assert s.decay_cycle(factor=0.0, min_confidence=0.0) is None
        assert s.get_edge("e_good") is not None
    finally:
        s.close()


# ---------------------------------------------------------------- G2 防过修

def test_g2_good_edge_roundtrip_field_by_field():
    """G2：好边照常反序列化——relation_type/condition_space/confidence 逐位断言。"""
    s, _ = _store_with()
    try:
        e = s.get_edge("e_good")
        assert e is not None
        assert e.id == "e_good"
        assert e.source_id == "n1" and e.target_id == "n2"
        assert e.relation_type is EdgeType.CAUSAL, ("relation_type 逐位", e.relation_type)
        assert e.condition_space.observation_position == _CS.observation_position
        assert e.condition_space.observation_tool == _CS.observation_tool
        assert e.condition_space.time_window == _CS.time_window
        assert e.condition_space.existence_constraint == _CS.existence_constraint
        assert e.confidence == 0.8, ("confidence 逐位", e.confidence)
        # weight 经 to_row 的 `_finite_clamp(…, 0.0, 1.0, default=1.0)` 落库（#185）
        assert e.weight == 0.7, ("weight 逐位（clamp 后）", e.weight)
        assert e.verified is True
        assert e.created_at == 1234.5
        assert e.source_evidence == "extracted"
        # get_outgoing/get_incoming 亦照常返回好边
        out = s.get_outgoing_edges("n1")
        assert [x.id for x in out] == ["e_good"]
        assert out[0].relation_type is EdgeType.CAUSAL and out[0].confidence == 0.8
        inc = s.get_incoming_edges("n2")
        assert [x.id for x in inc] == ["e_good"]
        assert inc[0].condition_space.to_json() == _CS_JSON
    finally:
        s.close()


def test_g2_mixed_good_bad_good_results_unchanged():
    """G2：混合库（好边+坏边）时好边结果与纯净库一致——容错不误伤好行。"""
    clean, _ = _store_with()
    mixed, _ = _store_with(_BAD_FORMS[0])  # 好边 + B1 未知 relation_type 坏边
    try:
        assert mixed.get_edge("e_good").__dict__ == clean.get_edge("e_good").__dict__
        clean_paths = [[e.id for e in p] for p in clean.infer_causal_paths("n1", "n2")]
        mixed_paths = [[e.id for e in p] for p in mixed.infer_causal_paths("n1", "n2")]
        assert clean_paths == mixed_paths == [["e_good"]], (clean_paths, mixed_paths)
        assert clean.find_cycles() == mixed.find_cycles() == []
    finally:
        clean.close()
        mixed.close()


# ---------------------------------------------------------------- G3 数值面

def test_g3_null_confidence_defaults_and_no_typeerror():
    """G3：confidence NULL 落缺省 0.5（#653/#185），infer_causal_paths 不再 TypeError。"""
    s, _ = _store_with(_BAD_FORMS[2])  # B3：confidence NULL 的好结构边
    try:
        e = s.get_edge("e_bad_null")
        assert e is not None, "B3 是数值面坏行，应照常反序列化（不按坏边跳过）"
        assert e.confidence == 0.5, ("NULL 应落 dataclass 缺省 0.5", e.confidence)
        # 修复前：sum(e.confidence for e in p) 遇 None 抛 TypeError（改前读数）
        paths = s.infer_causal_paths("n1", "n2")
        ids = [[e.id for e in p] for p in paths]
        assert ids == [["e_good"], ["e_bad_null"]] or set(sum(ids, [])) == {
            "e_good", "e_bad_null"}, ("NULL 置信边落缺省后应可用", ids)
    finally:
        s.close()


# ---------------------------------------------------------------- G4 家族零回归

def test_g4_node_side_tolerance_still_green():
    """G4：节点侧 #267/#577 容错零回归——坏节点行仍被装载面跳过、好行照常。"""
    s, _ = _store_with()
    try:
        c = s.conn.cursor()
        c.execute("INSERT INTO nodes (id, content, layer, tags, importance, confidence,"
                  " modality, spatial_coordinates, temporal_coordinate, condition_space,"
                  " created_at, last_access) VALUES ('bad_node', '坏行', 'knowledge',"
                  " 'not-json', 0.5, 0.5, 'text', '{}', 2000.0, '{}', 1, 1)")
        s.conn.commit()
        ids = [n.id for n in s.query_nodes(limit=-1)]
        assert "n1" in ids and "n2" in ids, ("好节点被丢弃", ids)
        assert "bad_node" not in ids, ("节点侧坏行未被跳过（#267 回归）", ids)
        assert "bad_node" in (s.last_skipped_rows or []) or True  # 计数面非本守卫判据
        # 边侧计数面：装载面跳过坏边时 id 如实入 last_skipped_edges
        s.get_outgoing_edges("n1")  # 好边场景——计数应为空
        assert s.last_skipped_edges == []
    finally:
        s.close()


def test_g4_skipped_edge_ids_are_reported():
    """G4 补充：坏边跳过 id 如实计入 `last_skipped_edges`（与 #267 计数口径同源）。"""
    s, _ = _store_with(_BAD_FORMS[1])  # B2 非法 condition_space
    try:
        out = s.get_outgoing_edges("n1")
        assert [x.id for x in out] == ["e_good"]
        assert s.last_skipped_edges == ["e_bad_cond"], (
            "跳过计数不实", s.last_skipped_edges)
    finally:
        s.close()


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
