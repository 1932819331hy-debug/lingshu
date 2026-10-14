# -*- coding: utf-8 -*-
"""test_second_batch_relations_feedback · 第二批 F06（关系判定）+ F07（像素读回反馈修正）
====================================================================================
本件是**功能判据**，不是「代码能跑」的冒烟件。两侧口径都先读源码定死，再逐条钉：

F06（hex_query · 配方 role → detection 绑定 + tags.relations 判定）
  配方 relations 的真实格式（见 hex_query.build_default_library）：
      tags = {"relations": [{"from": <role>, "to": <role>, "rel": <关系名>}, ...]}
  角色名对应 connections[*]["role"]；关系名与 extract_relations 的 spatial 词汇同源
  （above/below/left_of/right_of），另有库内声明词 side_by_side（= 同排左右）。
  本件钉住：① 绑定一对一且确定（同输入同报告，重复 role 得到**不同**检测）；
  ② 相同部件、不同相对布局 ⇒ 评分不同（且 conn 侧命中数相同，差异只能来自关系）；
  ③ 只改关系声明（above ↔ below）⇒ 评分不同；④ 无 relations 的旧配方评分与
  返回结构逐位不变（与 _match_score 相等、键集不变）；⑤ 未知关系名 / 畸形条目 /
  角色不在配方 / 角色未绑定 ⇒ 一律不计满足且逐条落原因；
  ⑥ **检测顺序无关**：同类型通配 role 的检测任意排列 ⇒ score/verdict/角色绑定/
  关系证据逐位一致（原为 0.667 vs 1.0），role_detections 保留**原输入下标**、
  永远指向位置与该角色一致的检测（不错指）；
  ⑦ 关系证据按各关系**自己的 row/col 判据**消费：斜向一对可同时 above 与 left_of，
  同格不得编造左右，side_by_side 只在同排不同列成立；
  ⑧ 同 role 的**有向**关系枚举两个方向（fi<ti 会漏掉反向合法对），对称关系
  （side_by_side）按规范序确定性去重。

F07（hex_gen · 有限确定性反馈修正）
  口径：像素读回（extract_attributes，色词表 red/green/blue，尺寸只对圆，96px 为
  面积下限）定位失配部件 → **只改渲染参数**（pattern_period）→ 仅接受真实图像上
  严格下降且构造性命中不降的改动；同 seed 全程不变；每轮差异/改动/停止原因留痕。
  本件钉住：① 一个可复现的真实失败用例（端到端文本 → 0 差异）且差异**严格下降**，
  读数由本文件自算（只读返回图像像素，不采信 refine 的记账）；
  ② 零差异 ⇒ 图像一字不改；③ 上限停 + 不可修正时**不假装成功**（overrides 空、
  差异不降、停止原因如实）；④ 48px 不可验证 ⇒ 明确不修正、after=None、不声称成功；
  ⑤ text/parts 意图与 seed 保留（修正只出现在 overrides 的渲染参数里）；
  ⑥ **空真护栏**：全 coverage 外 / parts 为空 / 缺对应落格日志 / 没有任何可判项
  ⇒ verifiable=False、before/after=None、不声称成功、不改图像（不得报 0/0 零差异）；
  ⑦ RGB + orange 混合 ⇒ 覆盖计数与未覆盖 part 下标/原因显式披露，RGB 的已验证
  结论不得外推到 orange。

运行（lingshu 仓根）：
  python -X utf8 -m pytest tests/test_second_batch_relations_feedback.py -q --no-header
  python -X utf8 tests/test_second_batch_relations_feedback.py
退出码：交给 pytest（全绿=0）。
"""
from __future__ import annotations

import itertools
import json
import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.nn.hex_composite import PATTERNS, extract_attributes  # noqa: E402
from lingshu.nn.hex_gen import (ATTR_CALIB_96, READBACK_COLORS,  # noqa: E402
                               REFINE_DEFAULT_ROUNDS, compile_description,
                               generate_from_text, render_relations,
                               readback_mismatches, refine_render,
                               verify_constructive)
from lingshu.nn.hex_query import ConnectionLibrary, _match_score  # noqa: E402
from lingshu.nn.hex_query import build_default_library, query_match  # noqa: E402

#: 与 hex_gen 同口径的像素判据（本文件只借提取器这个「独立观察者」；
#: 失配的**计数与定位**由下面的 pixel_mismatches 自己算，不调 hex_gen 的记账）。
_LEGACY_KEYS = {"name", "score", "id", "verdict", "margin", "all_scores"}


def pixel_mismatches(img, parts, log, size=96):
    """本文件自算的像素回读差异清单（只读 img 像素）。

    返回 [(部件下标, 项, 声明值, 读回值), ...]；覆盖范围与 hex_gen 文档的
    回读通道一致（色词表内、尺寸只对圆、size>=96 才可判）。
    """
    assert size >= 96, "96px 以下属性回读不可判（不可验证）"
    items = []
    for i, p in enumerate(parts):
        if p["color"] not in READBACK_COLORS:
            continue
        entry = next(e for e in log if e["part"] == i)
        a = extract_attributes(img, entry["zone_landed"], p["color"],
                               ATTR_CALIB_96, shape_hint=p["shape"])
        if a.get("pattern") != p["pattern"]:
            items.append((i, "pattern", p["pattern"], a.get("pattern")))
        if p["shape"] == "circle" and p["size"] in ("small", "large") \
                and a.get("size") != p["size"]:
            items.append((i, "size", p["size"], a.get("size")))
    return items


def readable_parts(parts, log):
    """本文件自算的「可读回部件」清单（独立复核 hex_gen 的覆盖账，不调其函数）。

    口径同回读通道：色在词表内（READBACK_COLORS）且落格日志里有该部件（有
    zone_landed 才能定位窗口）。读不回来的部件**不得**被算进「已验证」。
    """
    landed = {e["part"] for e in log}
    return [i for i, p in enumerate(parts)
            if p["color"] in READBACK_COLORS and i in landed]


def two_wheel_car(relations):
    """3 部件配方（车身条纹 + 两轮），relations 可变（用于「只改声明」对照）。"""
    lib = ConnectionLibrary()
    lib.add_record("car", [
        {"type": "stripe", "zone": "*", "color": "*", "role": "body"},
        {"type": "circle", "zone": "*", "color": "*", "role": "wheel"},
        {"type": "circle", "zone": "*", "color": "*", "role": "wheel"},
    ], tags={"relations": relations})
    return lib


GOOD_LAYOUT = [{"obj": "stripe|red", "pos": "r1"},
               {"obj": "circle|red", "pos": "r6"},
               {"obj": "circle|blue", "pos": "r8"}]
FLIPPED_LAYOUT = [{"obj": "stripe|red", "pos": "r7"},
                  {"obj": "circle|red", "pos": "r0"},
                  {"obj": "circle|blue", "pos": "r2"}]


# ==================== F06 · 关系判定 ====================

def test_f06_role_binding_one_to_one_and_deterministic():
    """绑定来自既有匹配：一对一（两轮绑到不同检测）、同输入同报告。"""
    lib = two_wheel_car([{"from": "body", "to": "wheel", "rel": "above"},
                         {"from": "wheel", "to": "wheel", "rel": "side_by_side"}])
    first = query_match(GOOD_LAYOUT, lib)[0]
    again = query_match(GOOD_LAYOUT, lib)[0]

    assert json.loads(json.dumps(first)) == json.loads(json.dumps(again)), (
        "同输入必须得到同一份报告（角色绑定确定性）")
    assert first["roles"] == {"body": ["r1"], "wheel": ["r6", "r8"]}, first["roles"]
    assert len(set(first["roles"]["wheel"])) == 2, "重复 role 必须绑到不同检测（一对一）"
    assert first["relation_hits"] == 2 and first["conn_hits"] == 3
    assert first["score"] == 1.0 and first["verdict"] == "ACCEPT"
    for rel in first["relations"]:
        assert rel["satisfied"] is True, rel
        assert rel["witness"]["from"] != rel["witness"]["to"], "证据对必须是两个不同检测"
        assert rel["witness"] in rel["observed"], "witness 必须取自实际观测表"
        assert rel["witness"]["fact"] in ("above", "left_of", "right_of"), rel
    print("[数字] F06 绑定:", json.dumps(first["roles"], ensure_ascii=False),
          "关系命中", first["relation_hits"], "/", first["relation_total"])


def test_f06_same_parts_different_layout_changes_score():
    """相同部件、不同相对布局 ⇒ 评分/裁决不同；conn 侧命中数不变（差异只能来自关系）。"""
    lib = two_wheel_car([{"from": "body", "to": "wheel", "rel": "above"},
                         {"from": "wheel", "to": "wheel", "rel": "side_by_side"}])
    good = query_match(GOOD_LAYOUT, lib)[0]
    flip = query_match(FLIPPED_LAYOUT, lib)[0]

    assert good["conn_hits"] == flip["conn_hits"] == 3, (good["conn_hits"], flip["conn_hits"])
    assert _match_score(lib.records[0], GOOD_LAYOUT) == _match_score(lib.records[0], FLIPPED_LAYOUT) == 1.0
    assert flip["relation_hits"] < good["relation_hits"], (flip, good)
    assert flip["score"] < good["score"], (flip["score"], good["score"])
    bad_rel = next(r for r in flip["relations"] if r["rel"] == "above")
    assert bad_rel["satisfied"] is False
    assert bad_rel["observed"] and all(o["fact"] == "below" for o in bad_rel["observed"]), bad_rel
    print("[数字] F06 布局: good score=%s rel=%s | flipped score=%s rel=%s"
          % (good["score"], good["relation_hits"], flip["score"], flip["relation_hits"]))


def test_f06_relation_declaration_alone_changes_score():
    """同一批部件、同一布局，只把声明 above 改成 below ⇒ 评分随之改变。"""
    above = query_match(FLIPPED_LAYOUT, two_wheel_car(
        [{"from": "body", "to": "wheel", "rel": "above"}]))[0]
    below = query_match(FLIPPED_LAYOUT, two_wheel_car(
        [{"from": "body", "to": "wheel", "rel": "below"}]))[0]

    assert above["conn_hits"] == below["conn_hits"] == 3, (above, below)
    assert above["relation_hits"] == 0 and below["relation_hits"] == 1
    assert above["score"] == round(3 / 4, 3) and below["score"] == 1.0, (above, below)
    assert above["score"] != below["score"], "只改关系声明必须影响评分"
    print("[数字] F06 仅改声明: above=%s below=%s" % (above["score"], below["score"]))


def test_f06_legacy_recipes_keep_old_score_and_result_shape():
    """无 relations 的旧配方：评分 = _match_score，返回键集/排序快照形状不变。"""
    lib = ConnectionLibrary()
    lib.add_record("pair", [{"type": "circle", "zone": "top-left", "color": "red"},
                            {"type": "circle", "zone": "bottom-right", "color": "blue"}])
    lib.add_record("one", [{"type": "triangle", "zone": "*", "color": "green"}])
    cases = [[{"obj": "circle|red", "pos": "r0"}, {"obj": "circle|blue", "pos": "r8"}],
             [{"obj": "circle|red", "pos": "r0"}],
             [{"obj": "stripe|red", "pos": "r4"}]]
    for dets in cases:
        best = query_match(list(dets), lib)[0]
        legacy = {rec["name"]: _match_score(rec, dets) for rec in lib.records}
        assert set(best.keys()) == _LEGACY_KEYS, (
            "无 relations 的旧配方不得新增返回键（旧契约）: %s" % sorted(best.keys()))
        assert best["score"] == round(max(legacy.values()), 3), (best, legacy)
        for item in best["all_scores"]:
            assert set(item.keys()) == {"name", "score", "id"}, item
    print("[数字] F06 旧配方键集:", sorted(_LEGACY_KEYS),
          "| 评分 = _match_score（逐例相等）")


def test_f06_unknown_or_broken_relations_are_never_satisfied():
    """未知关系名/畸形条目/幽灵角色：一律不计满足，且与「已知但不成立」同分。"""
    unknown = query_match(FLIPPED_LAYOUT, two_wheel_car(
        [{"from": "body", "to": "wheel", "rel": "on_top_of"}]))[0]
    known_unsat = query_match(FLIPPED_LAYOUT, two_wheel_car(
        [{"from": "body", "to": "wheel", "rel": "above"}]))[0]
    assert unknown["relation_hits"] == known_unsat["relation_hits"] == 0
    assert unknown["score"] == known_unsat["score"], (unknown, known_unsat)
    assert unknown["relations"][0]["satisfied"] is False
    assert "未知关系名" in unknown["relations"][0]["reason"]

    broken = ConnectionLibrary()
    broken.add_record("b", [{"type": "stripe", "zone": "*", "color": "*", "role": "body"},
                            {"type": "circle", "zone": "*", "color": "*", "role": "wheel"}],
                      tags={"relations": [{"from": "body", "to": "wheel", "rel": "above"},
                                          {"from": "body", "rel": "above"},
                                          "junk",
                                          {"from": "body", "to": "ghost", "rel": "above"}]})
    res = query_match(GOOD_LAYOUT[:2], broken)[0]
    # 只有第一条是「完好且成立」，其余三条（缺字段 / 非字典 / 幽灵角色）必须不满足
    assert res["relation_hits"] == 1 and res["relation_total"] == 4, res
    assert res["conn_hits"] == 2 and res["conn_total"] == 2, res
    # 总数 = 2 conn + 4 关系 ⇒ 3/6（坏关系条数照样进分母：不满足≠不存在）
    assert res["score"] == round(3 / 6, 3), res
    reasons = [r.get("reason", "") for r in res["relations"][1:]]
    assert all(reasons), reasons
    assert any("缺" in r for r in reasons), reasons                 # 缺字段
    assert any("非字典" in r for r in reasons), reasons             # 条目形态坏
    assert any("不在配方" in r for r in reasons), reasons           # 幽灵角色
    assert res["relations"][1]["satisfied"] is False
    assert res["relations"][2]["satisfied"] is False
    assert res["relations"][3]["satisfied"] is False

    # 整块非列表的畸形声明：不得被当成「无约束」白拿满分
    squashed = ConnectionLibrary()
    squashed.add_record("s", [{"type": "stripe", "zone": "*", "color": "*", "role": "body"},
                              {"type": "circle", "zone": "*", "color": "*", "role": "wheel"}],
                        tags={"relations": "above"})
    sq = query_match(GOOD_LAYOUT[:2], squashed)[0]
    assert sq["relation_total"] == 1 and sq["relation_hits"] == 0, sq
    assert sq["score"] == round(2 / 3, 3), sq
    print("[数字] F06 坏关系: unknown=%s broken=%s squashed=%s"
          % (unknown["score"], res["score"], sq["score"]))


def test_f06_duplicate_parts_duplicate_roles_and_unbound_roles_are_safe():
    """重复部件/重复 role 不重复消费；角色未绑定如实落账，不拿别的检测顶替。"""
    lib = two_wheel_car([{"from": "wheel", "to": "wheel", "rel": "side_by_side"}])
    dup_same_cell = query_match([{"obj": "circle|red", "pos": "r0"},
                                 {"obj": "circle|red", "pos": "r0"}], lib)[0]
    # 两个检测都能满足 conn（命中 2），但同格两检测之间没有方向关系 ⇒ 关系不满足
    assert dup_same_cell["conn_hits"] == 2 and dup_same_cell["relation_hits"] == 0
    assert dup_same_cell["score"] == round(2 / 4, 3), dup_same_cell   # 3 conn + 1 关系
    assert dup_same_cell["relations"][0]["satisfied"] is False

    single = query_match([{"obj": "circle|red", "pos": "r0"}], lib)[0]
    assert single["conn_hits"] == 1 and single["relation_hits"] == 0
    assert single["roles"]["wheel"] == ["r0"] and single["roles"]["body"] == []
    assert "无成对检测" in single["relations"][0]["reason"], single["relations"][0]

    no_body = query_match([{"obj": "circle|red", "pos": "r6"},
                           {"obj": "circle|blue", "pos": "r8"}], lib)[0]
    assert no_body["roles"]["body"] == [] and no_body["relation_hits"] == 1
    assert no_body["relations"][0]["satisfied"] is True   # 两轮都绑到 r6/r8（同排）⇒ 关系成立
    print("[数字] F06 重复/未绑定: 同格重复=%s 单检测=%s 车身未绑定=%s"
          % (dup_same_cell["score"], single["score"], no_body["score"]))


def test_f06_default_library_composites_carry_evidence():
    """默认连接库的复合体（car）在带证据的路径上给出可读的逐关系回报。"""
    default = build_default_library()
    lib = ConnectionLibrary()
    src = next(r for r in default.records if r["name"] == "car")
    lib.add_record(src["name"], src["connections"], tags=src["tags"])   # 只留 car，隔离裁决
    car = query_match(GOOD_LAYOUT, lib)[0]
    assert car["name"] == "car", car
    assert car["score"] == 1.0 and car["relation_total"] == 2 and car["relation_hits"] == 2
    rels = {r["rel"]: r for r in car["relations"]}
    assert rels["above"]["satisfied"] and rels["side_by_side"]["satisfied"], car
    assert rels["side_by_side"]["witness"]["fact"] in ("left_of", "right_of")
    assert [set(item) for item in car["all_scores"]] == [{"name", "score", "id"}], car["all_scores"]
    print("[数字] F06 默认库 car: score=%s 证据=%s"
          % (car["score"], json.dumps([{r["rel"]: r["satisfied"]} for r in car["relations"]],
                                      ensure_ascii=False)))


# ==================== F06 · 顺序无关与判据消费（第一次返工）====================

def wildcard_pair(relations):
    """两个同类型通配 role（a/b）的配方——检测顺序无关性探针的最小载体。"""
    lib = ConnectionLibrary()
    lib.add_record("pair", [
        {"type": "circle", "zone": "*", "color": "*", "role": "a"},
        {"type": "circle", "zone": "*", "color": "*", "role": "b"}],
        tags={"relations": relations})
    return lib


def report_signature(entry):
    """一次 query_match 里**与输入排列无关**的部分（不含 role_detections 原下标）。"""
    return json.dumps({k: entry[k] for k in
                       ("name", "score", "verdict", "margin", "roles", "relations",
                        "conn_hits", "conn_total", "relation_hits", "relation_total")},
                      sort_keys=True, ensure_ascii=False)


def assert_role_index_mapping(entry, detections):
    """role_detections 里的原下标必须指向「位置与该角色一致」的检测（不错指）。"""
    for role, positions in entry["roles"].items():
        idx = entry["role_detections"][role]
        assert [detections[i]["pos"] for i in idx] == positions, \
            (role, idx, positions)


def test_f06_wildcard_roles_are_order_invariant_full_permutations():
    """F06 复现探针：两个同类型通配 role 的检测换序 ⇒ 报告必须一致（修复前 0.667 vs 1.0）。

    d = [circle|red@r0, circle|blue@r8]，配方 pair(a above b)，两个 conn 都是通配。
    修复前正向评分 0.667（a→r8、b→r0，above 判不成立）、反向 1.0——角色绑定跟着
    detections 的排列翻转。现在两种排列都必须给出 a→r0、b→r8、above 成立、score=1.0；
    role_detections 随输入改变（原下标本就不同）但**绝不指向别的检测**。
    """
    lib = wildcard_pair([{"from": "a", "to": "b", "rel": "above"}])
    base = [{"obj": "circle|red", "pos": "r0"}, {"obj": "circle|blue", "pos": "r8"}]
    sigs = set()
    for p in itertools.permutations(range(len(base))):
        dets = [base[i] for i in p]
        entry = query_match(dets, lib)[0]
        assert_role_index_mapping(entry, dets)
        sigs.add(report_signature(entry))
    assert len(sigs) == 1, sigs

    entry = query_match(list(base), lib)[0]
    assert entry["score"] == 1.0 and entry["verdict"] == "ACCEPT", entry
    assert entry["roles"] == {"a": ["r0"], "b": ["r8"]}, entry["roles"]
    assert entry["conn_hits"] == 2 and entry["relation_hits"] == 1
    assert entry["relations"][0]["satisfied"] is True
    assert entry["relations"][0]["witness"]["facts"] == ["above", "left_of"], \
        entry["relations"][0]
    print("[数字] F06 顺序无关(通配双角色): %d 个排列 -> %d 份报告 %s；score=%s 绑定=%s"
          % (2, len(sigs), json.dumps(entry["roles"], ensure_ascii=False),
             entry["score"], json.dumps(entry["role_detections"], ensure_ascii=False)))


def test_f06_relation_report_is_stable_across_three_detection_permutations():
    """3 检测 × 6 排列：score/verdict/角色/逐条证据（含 observed/witness）逐位一致。"""
    lib = two_wheel_car([{"from": "body", "to": "wheel", "rel": "above"},
                         {"from": "wheel", "to": "wheel", "rel": "right_of"}])
    sigs = set()
    for p in itertools.permutations(range(3)):
        dets = [GOOD_LAYOUT[i] for i in p]
        entry = query_match(dets, lib)[0]
        assert_role_index_mapping(entry, dets)
        sigs.add(report_signature(entry))
    assert len(sigs) == 1, sigs
    entry = query_match(list(GOOD_LAYOUT), lib)[0]
    assert entry["conn_hits"] == 3 and entry["relation_hits"] == 2 and entry["score"] == 1.0
    assert entry["roles"] == {"body": ["r1"], "wheel": ["r6", "r8"]}, entry["roles"]
    print("[数字] F06 6 个排列 -> %d 份报告；relation_hits=%d" % (len(sigs), entry["relation_hits"]))


def test_f06_legacy_recipes_hit_count_order_invariant_and_keys_unchanged():
    """无 relations 的旧配方：任意排列命中数不变、返回键集不新增（最大匹配口径保留）。"""
    lib = ConnectionLibrary()
    lib.add_record("gizmo", [{"type": "circle", "zone": "*", "color": "*"},
                             {"type": "circle", "zone": "top", "color": "*"}])
    dets = [{"obj": "circle|red", "pos": "r0"},
            {"obj": "circle|red", "pos": "r7"}]
    for p in itertools.permutations(range(len(dets))):
        order = [dets[i] for i in p]
        best = query_match(list(order), lib)[0]
        assert set(best.keys()) == _LEGACY_KEYS, sorted(best.keys())
        assert best["score"] == round(_match_score(lib.records[0], order), 3) == 1.0, best
    print("[数字] F06 旧配方排列: 键集=%s；每个排列 score=1.0（2 conn 全命中）"
          % sorted(_LEGACY_KEYS))


def test_f06_diagonal_pair_consumes_above_and_left_of_independently():
    """斜向一对按各自 row/col 判据：r0 对 r8 同时成立 above 与 left_of；同格不编造左右。

    修复前 _relation_fact 只取 extract_relations 的单一主词（行优先），col0<col2 的
    left_of 被误判不成立。现在逐条关系各按自己的判据取真值——同一对观测可同时支持
    above 与 left_of，但 below/right_of/side_by_side 仍必须为假。
    """
    def rel_item(rel, dets):
        return query_match(dets, wildcard_pair(
            [{"from": "a", "to": "b", "rel": rel}]))[0]["relations"][0]

    diag = [{"obj": "circle|red", "pos": "r0"}, {"obj": "circle|blue", "pos": "r8"}]
    up, left = rel_item("above", diag), rel_item("left_of", diag)
    for item in (up, left):
        assert item["satisfied"] is True, item
        assert item["witness"]["facts"] == ["above", "left_of"], item["witness"]
    for rel in ("below", "right_of", "side_by_side"):
        item = rel_item(rel, diag)
        assert item["satisfied"] is False, item
        assert item["observed"][0]["facts"] == ["above", "left_of"], item["observed"]

    # 同格：两个检测之间不存在方向关系 ⇒ 不得编造左右（事实表为空）
    same = rel_item("left_of", [{"obj": "circle|red", "pos": "r0"},
                                {"obj": "circle|blue", "pos": "r0"}])
    assert same["satisfied"] is False, same
    assert same["observed"][0]["facts"] == [] and same["observed"][0]["fact"] is None, same

    # side_by_side：同排不同列成立；斜向/同列不成立
    row = rel_item("side_by_side", [{"obj": "circle|red", "pos": "r6"},
                                    {"obj": "circle|blue", "pos": "r8"}])
    assert row["satisfied"] is True and "side_by_side" in row["witness"]["facts"], row
    assert rel_item("side_by_side", diag)["satisfied"] is False
    col = rel_item("side_by_side", [{"obj": "circle|red", "pos": "r0"},
                                    {"obj": "circle|blue", "pos": "r3"}])
    assert col["satisfied"] is False and col["observed"][0]["facts"] == ["above"], col
    print("[数字] F06 斜向: above=%s left_of=%s 同格 left_of=%s side_by_side(同排)=%s"
          % (up["satisfied"], left["satisfied"], same["satisfied"], row["satisfied"]))


def test_f06_same_role_directed_relations_cover_both_orders_and_dedupe_symmetric():
    """同 role（wheel→wheel）的有向关系枚举两个方向；对称关系确定性去重。

    修复前只枚举 fi<ti：wheel 绑到 [r6, r8] 时 right_of（后一个相对前一个成立）
    永远判不成立——虽然 (r8, r6) 是合法检测对。对称关系（side_by_side）在同一个
    无序对上两个方向同真，只留一条（observed 长度 1），有向关系则两个方向各一条。
    """
    items = {}
    for rel in ("left_of", "right_of", "above", "below", "side_by_side"):
        entry = query_match(GOOD_LAYOUT, two_wheel_car(
            [{"from": "wheel", "to": "wheel", "rel": rel}]))[0]
        items[rel] = entry["relations"][0]

    assert items["left_of"]["satisfied"] is True
    assert items["left_of"]["witness"]["from"] == "r6", items["left_of"]["witness"]
    assert items["right_of"]["satisfied"] is True, items["right_of"]
    assert items["right_of"]["witness"]["from"] == "r8", items["right_of"]["witness"]
    assert items["above"]["satisfied"] is False and items["below"]["satisfied"] is False
    assert items["side_by_side"]["satisfied"] is True
    assert len(items["side_by_side"]["observed"]) == 1, items["side_by_side"]["observed"]
    assert len(items["right_of"]["observed"]) == 2, items["right_of"]["observed"]
    assert len(items["above"]["observed"]) == 2, items["above"]["observed"]
    print("[数字] F06 同 role 有向: left_of=%s right_of=%s(witness %s) 对称去重 observed=%d"
          % (items["left_of"]["satisfied"], items["right_of"]["satisfied"],
             items["right_of"]["witness"]["from"], len(items["side_by_side"]["observed"])))


# ==================== F07 · 反馈修正 ====================

def test_f07_real_failure_diff_strictly_decreases():
    """可复现真实失败：点纹三角在 96px 下读不出花纹 ⇒ 修正后严格降到 0。

    证据链：① 关掉修正（refine_rounds=0）时同一 seed 的真实图像上，本文件自算的
    像素回读差异 = 1；② 打开修正后，**返回图像**上的同一自算读数 = 0；
    ③ 图像确实被重新渲染过（像素有变化）；④ 构造性命中没有下降。
    """
    text, seed = "中间有点纹三角", 0
    off = generate_from_text(text, size=96, seed=seed, refine_rounds=0)
    on = generate_from_text(text, size=96, seed=seed)

    before = pixel_mismatches(off["image"], off["parts"], off["log"])
    after = pixel_mismatches(on["image"], on["parts"], on["log"])
    assert before and before[0][1] == "pattern" and before[0][3] is None, before
    assert len(after) < len(before) and after == [], (before, after)
    assert not np.array_equal(off["image"], on["image"]), "修正必须真的重渲染过"
    con_off = verify_constructive(off["parts"], off["image"])["matched"]
    con_on = verify_constructive(on["parts"], on["image"])["matched"]
    assert con_on >= con_off, (con_off, con_on)
    # 生产侧记账必须与自算读数一致（两侧同源可复核）
    assert readback_mismatches(on["image"], on["parts"], on["log"], 96)["total"] == len(after)
    assert on["refine"]["before"] == len(before) and on["refine"]["after"] == len(after)
    assert on["refine"]["overrides"] == [{"part": 0, "render_params": {"pattern_period": 2}}], \
        on["refine"]["overrides"]
    assert on["refine"]["rounds"][1]["diff_before"] == 1 and \
        on["refine"]["rounds"][1]["diff_after"] == 0
    print("[数字] F07 端到端 %r seed=%d: 差异 %d -> %d；parts=%s"
          % (text, seed, len(before), len(after), json.dumps(on["parts"], ensure_ascii=False)))


def test_f07_zero_diff_leaves_image_untouched():
    """零差异 ⇒ 一处不改：图像与关闭修正逐像素相同，停止原因如实。"""
    text, seed = "左上有红色圆形,右下有绿色三角", 3
    off = generate_from_text(text, size=96, seed=seed, refine_rounds=0)
    on = generate_from_text(text, size=96, seed=seed)
    assert on["refine"]["before"] == 0 and on["refine"]["after"] == 0
    assert on["refine"]["improved"] is False
    assert on["refine"]["overrides"] == []
    assert on["refine"]["stop_reason"].startswith("零差异"), on["refine"]["stop_reason"]
    assert np.array_equal(off["image"], on["image"]), "零差异时图像必须一字不改"
    print("[数字] F07 零差异: before=0 after=0 overrides=[] 图像逐像素相同")


def test_f07_cap_stops_and_unfixable_case_is_not_faked():
    """上限停：cap=1 停在「达上限」且差异仍 >0；cap=2 才消除；不可修正时不假装成功。"""
    parts = [{"shape": "triangle", "color": "red", "zone": "r8", "pattern": "dotted", "size": "small"},
             {"shape": "circle", "color": "red", "zone": "r8", "pattern": "dotted", "size": "small"},
             {"shape": "triangle", "color": "red", "zone": "r4", "pattern": "striped", "size": "small"}]
    base = render_relations(parts, size=96, seed=38)
    base_diff = len(pixel_mismatches(base[0], parts, base[1]))
    r1 = refine_render(parts, size=96, seed=38, rounds=1)
    r2 = refine_render(parts, size=96, seed=38, rounds=2)

    assert base_diff == 3, base_diff
    assert r1["after"] == 1 and r1["stop_reason"].startswith("达上限"), (r1["after"], r1["stop_reason"])
    assert len(r1["rounds"]) - 1 <= 1, "轮数不得超过上限"
    assert r2["after"] == 0 and r2["stop_reason"] == "已消除全部可判差异"
    assert len(pixel_mismatches(r2["image"], parts, r2["log"])) == 0

    # 不可修正：小尺寸点纹三角给不出可判的花纹像素 ⇒ 无候选改善、不覆盖不假装
    small = [{"shape": "triangle", "color": "red", "zone": "r0", "pattern": "dotted", "size": "small"}]
    rb = render_relations(small, size=96, seed=0)
    un = refine_render(small, size=96, seed=0, rounds=REFINE_DEFAULT_ROUNDS)
    assert len(pixel_mismatches(rb[0], small, rb[1])) == 1
    assert un["before"] == 1 and un["after"] == 1 and un["improved"] is False
    assert un["overrides"] == [] and un["stop_reason"].startswith("无改进"), un["stop_reason"]
    assert np.array_equal(un["image"], rb[0]), "没有接受的改动 ⇒ 图像保持基线"
    print("[数字] F07 上限/不可修正: cap1=%d(达上限) cap2=%d | 不可修正 before=1 after=1 overrides=[]"
          % (r1["after"], r2["after"]))


def test_f07_unverifiable_size_is_not_corrected_and_not_claimed():
    """48px（低于提取器面积下限）⇒ 明确不修正、after=None、不声称成功。"""
    text, seed = "中间有点纹三角", 0
    off = generate_from_text(text, size=48, seed=seed, refine_rounds=0)
    on = generate_from_text(text, size=48, seed=seed)
    ref = on["refine"]
    assert ref["verifiable"] is False and ref["after"] is None and ref["before"] is None
    assert "不可判" in ref["stop_reason"] and "不声称成功" in ref["stop_reason"], ref["stop_reason"]
    assert ref["improved"] is False and ref["overrides"] == []
    assert np.array_equal(off["image"], on["image"]), "不可验证时不得改动图像"
    assert ref["coverage"]["min_size"] == 96
    print("[数字] F07 48px: verifiable=%s after=%s stop=%r"
          % (ref["verifiable"], ref["after"], ref["stop_reason"]))


def test_f07_intent_and_seed_preserved():
    """意图与 seed 保留：text/parts 原样、修正只落在渲染参数、同 seed 结果确定。"""
    text, seed = "中间有点纹三角", 0
    g = generate_from_text(text, size=96, seed=seed)
    assert g["text"] == text, "原始 text 必须原样保留"
    assert g["parts"] == compile_description(text)["parts"], "parts 意图不得被修正改写"
    assert g["parts"][0]["pattern"] == "dotted" and g["parts"][0]["size"] == "medium"
    for item in g["refine"]["overrides"]:
        assert set(item["render_params"]) == {"pattern_period"}, item
    assert "seed" not in json.dumps(g["refine"]), "修正不得改 seed（不靠换 seed 碰运气）"
    twin = generate_from_text(text, size=96, seed=seed)
    assert np.array_equal(g["image"], twin["image"])
    assert json.loads(json.dumps(g["refine"])) == json.loads(json.dumps(twin["refine"]))
    # 同一 seed 的基线是稳定失败的：改善来自渲染参数，不是 seed 恰好变好
    off = generate_from_text(text, size=96, seed=seed, refine_rounds=0)
    assert len(pixel_mismatches(off["image"], off["parts"], off["log"])) == 1
    print("[数字] F07 意图/seed: text=%r parts=%s overrides=%s"
          % (text, json.dumps(g["parts"], ensure_ascii=False),
             json.dumps(g["refine"]["overrides"], ensure_ascii=False)))


def test_f07_out_of_coverage_colors_are_not_touched():
    """覆盖范围外的开放词色（orange）不进入回读通道 ⇒ **不可验证**，不是「零差异」。

    修复前：refine 报 verifiable=True、before=after=0、stop=零差异——一个部件都读不
    回来，0/0 是空真（假成功）。现在必须 verifiable=False、before/after=None、不声称
    成功、图像逐像素不变，且覆盖账如实给出总部件数/未覆盖 part 下标/原因；独立读回
    （本文件的 readable_parts）必须同样认定「没有可读回部件」。
    """
    parts = [{"shape": "triangle", "color": "orange", "zone": "r4",
              "pattern": "dotted", "size": "medium"}]
    off = render_relations(parts, size=96, seed=0)
    rr = refine_render(parts, size=96, seed=0, rounds=REFINE_DEFAULT_ROUNDS)

    assert rr["verifiable"] is False, rr
    assert rr["before"] is None and rr["after"] is None, (rr["before"], rr["after"])
    assert rr["improved"] is False and rr["overrides"] == [] and rr["unresolved"] == []
    assert "不声称成功" in rr["stop_reason"] and "零差异" not in rr["stop_reason"], \
        rr["stop_reason"]
    assert np.array_equal(rr["image"], off[0]), "不可验证时不得改动图像"

    cov = rr["coverage"]
    assert cov["colors"] == list(READBACK_COLORS) == ["red", "green", "blue"]
    assert cov["min_size"] == 96
    assert cov["parts"] == 1 and cov["covered"] == [] and cov["judged"] == [], cov
    assert [u["part"] for u in cov["uncovered"]] == [0], cov
    assert cov["uncovered"][0]["color"] == "orange" and "不在回读色词表" in \
        cov["uncovered"][0]["reason"], cov
    # 独立复核：这个部件确实读不回来（本文件自己的读者也不认 orange）
    assert readable_parts(parts, off[1]) == [], readable_parts(parts, off[1])
    print("[数字] F07 覆盖外: verifiable=%s before=%s after=%s | 覆盖 %d/%d 未覆盖=%s"
          % (rr["verifiable"], rr["before"], rr["after"], len(cov["covered"]),
             cov["parts"], [u["part"] for u in cov["uncovered"]]))


def test_f07_period_channel_is_pixel_identical_to_shared_paint_at_period3():
    """period=3 时渲染参数通道必须与共享 _paint 逐像素一致（防局部几何漂移）。"""
    from lingshu.nn.hex_composite import _paint
    from lingshu.nn.hex_gen import _paint_base_patterned

    mismatches = []
    for size in (48, 96):
        for shape in ("circle", "triangle", "stripe"):
            for pattern in PATTERNS:
                for cx in (5, size // 2, size - 6):
                    for cy in (5, size // 3, size - 5):
                        for rad in (3, 6, 13):
                            shared = np.zeros((size, size, 3), np.uint8)
                            tuned = np.zeros((size, size, 3), np.uint8)
                            _paint(shared, shape, cx, cy, rad, (7, 8, 9), pattern)
                            _paint_base_patterned(tuned, shape, cx, cy, rad,
                                                  (7, 8, 9), pattern, 3)
                            if not np.array_equal(shared, tuned):
                                mismatches.append((size, shape, pattern, cx, cy, rad))
    assert mismatches == [], mismatches[:5]
    print("[数字] F07 周期通道 period=3 与共享 _paint 逐像素一致（%d 例对照）" % (2 * 3 * 3 * 3 * 3 * 3))


def test_f07_supersample_breaks_calibration_so_refinement_declines():
    """supersample≠1 时 96px 标定口径不成立 ⇒ 与 48px 同等处置：不可验证、不声称成功。

    这里的图像尺度变了，ATTR_CALIB_96 的读数不再可判——所以不能被标成
    verifiable=True（哪怕读数是 0），必须以 before/after=None 收口。
    """
    text, seed = "中间有点纹三角", 0
    off = generate_from_text(text, size=96, seed=seed, supersample=2, refine_rounds=0)
    on = generate_from_text(text, size=96, seed=seed, supersample=2)
    ref = on["refine"]
    assert ref["verifiable"] is False and ref["before"] is None and ref["after"] is None, ref
    assert ref["improved"] is False and ref["overrides"] == []
    assert "supersample" in ref["stop_reason"] and "不声称成功" in ref["stop_reason"]
    assert ref["coverage"]["judged"] == [], ref["coverage"]
    assert "标定" in ref["coverage"]["reason"], ref["coverage"]
    assert np.array_equal(off["image"], on["image"])
    print("[数字] F07 supersample=2: verifiable=%s after=%s stop=%r"
          % (ref["verifiable"], ref["after"], ref["stop_reason"]))


def test_f07_batch_never_worsens_and_improves_overall():
    """批量回归：逐例不劣化、总量严格下降（修正不会把好的改坏）。"""
    cols = ["red", "green", "blue"]
    shapes = ["circle", "triangle", "stripe"]
    before_total = after_total = improved = 0
    for seed in range(60):
        rng = np.random.default_rng(2000 + seed)
        k = int(rng.integers(1, 4))
        parts = [{"shape": shapes[rng.integers(3)], "color": cols[rng.integers(3)],
                  "zone": f"r{rng.integers(9)}", "pattern": PATTERNS[rng.integers(3)],
                  "size": ["small", "medium", "large"][rng.integers(3)]}
                 for _ in range(k)]
        img0, log0 = render_relations(parts, size=96, seed=seed)
        b = len(pixel_mismatches(img0, parts, log0))
        rr = refine_render(parts, size=96, seed=seed, rounds=REFINE_DEFAULT_ROUNDS)
        a = len(pixel_mismatches(rr["image"], parts, rr["log"]))
        assert a <= b, (seed, b, a)
        assert rr["before"] == b and rr["after"] == a, (seed, rr["before"], rr["after"], b, a)
        before_total += b
        after_total += a
        improved += int(a < b)
    assert after_total < before_total, (before_total, after_total)
    assert improved >= 3, improved
    print("[数字] F07 批量 60 例: 差异 %d -> %d；改善用例 %d；逐例不劣化"
          % (before_total, after_total, improved))


def test_f07_end_to_end_out_of_coverage_is_unverifiable_not_zero_diff():
    """端到端复现探针：generate_from_text("左上有橙色圆形", 96, seed=7)。

    修复前 refine 报 verifiable=True / before=after=0 / stop=零差异，而该图**没有
    任何可读回部件**——空真。现在必须 verifiable=False、before/after=None、stop 不
    声称成功、图像与关闭修正逐像素相同，覆盖账给出未覆盖 part 与原因。
    """
    text, seed = "左上有橙色圆形", 7
    off = generate_from_text(text, size=96, seed=seed, refine_rounds=0)
    on = generate_from_text(text, size=96, seed=seed)
    ref = on["refine"]

    assert ref["verifiable"] is False and ref["before"] is None and ref["after"] is None, ref
    assert ref["improved"] is False and ref["overrides"] == []
    assert "不声称成功" in ref["stop_reason"] and "零差异" not in ref["stop_reason"], \
        ref["stop_reason"]
    assert np.array_equal(off["image"], on["image"]), "不可验证时不得改动图像"
    cov = ref["coverage"]
    assert cov["parts"] == 1 and cov["covered"] == [] and cov["judged"] == [], cov
    assert [(u["part"], u["color"]) for u in cov["uncovered"]] == [(0, "orange")], cov
    assert on["parts"][0]["color"] == "orange" and on["parts"][0]["zone"] == "r0"
    # 独立复核：橙色部件读不回来 ⇒ 本文件的自算读数也是「无」而不是「0 个差异」
    assert readable_parts(on["parts"], on["log"]) == []
    print("[数字] F07 端到端覆盖外: verifiable=%s before=%s after=%s parts=%s"
          % (ref["verifiable"], ref["before"], ref["after"],
             json.dumps(on["parts"], ensure_ascii=False)))


def test_f07_mixed_rgb_and_out_of_coverage_part_discloses_counts():
    """RGB + orange 混合：RGB 部件的真实读数照算，orange 未覆盖必须显式披露、不得外推。"""
    parts = [{"shape": "triangle", "color": "red", "zone": "r4",
              "pattern": "dotted", "size": "medium"},
             {"shape": "triangle", "color": "orange", "zone": "r0",
              "pattern": "dotted", "size": "medium"}]
    off = render_relations(parts, size=96, seed=0)
    rr = refine_render(parts, size=96, seed=0, rounds=REFINE_DEFAULT_ROUNDS)
    cov = rr["coverage"]

    # 独立复核：只有 red 部件落在回读通道里
    assert readable_parts(parts, off[1]) == [0], readable_parts(parts, off[1])
    assert cov["parts"] == 2 and cov["covered"] == [0] and cov["judged"] == [0], cov
    assert [(u["part"], u["color"]) for u in cov["uncovered"]] == [(1, "orange")], cov
    assert "orange" in cov["uncovered"][0]["reason"], cov
    assert "不得外推" in cov["reason"], cov["reason"]

    # before/after 与本文件自算的独立读数逐位一致（口径＝只判可读回部件）
    before = len(pixel_mismatches(off[0], parts, off[1]))
    after = len(pixel_mismatches(rr["image"], parts, rr["log"]))
    assert rr["verifiable"] is True and rr["before"] == before and rr["after"] == after, \
        (rr["verifiable"], rr["before"], rr["after"], before, after)
    assert before == 1 and after == 0, (before, after)
    # 修正只落在可判的 RGB 部件上；未覆盖部件不进 unresolved、不被声称
    assert [o["part"] for o in rr["overrides"]] == [0], rr["overrides"]
    assert all(item["part"] != 1 for item in rr["unresolved"]), rr["unresolved"]
    assert not np.array_equal(off[0], rr["image"]), "RGB 部件必须有真实重渲染"
    print("[数字] F07 混合覆盖: before=%d after=%d | covered=%s uncovered=%s"
          % (before, after, cov["covered"], [u["part"] for u in cov["uncovered"]]))


def test_f07_empty_parts_and_missing_log_are_unverifiable():
    """空 parts / 缺对应落格日志 ⇒ 没有任何可判项 ⇒ 明确不可验证（不是零差异）。"""
    img, log = render_relations([], size=96, seed=0)
    empty = readback_mismatches(img, [], log, 96)
    assert empty["verifiable"] is False and empty["total"] is None and empty["items"] == []
    assert "没有任何可判项" in empty["note"] and "不声称成功" in empty["note"], empty
    assert empty["coverage"] == {"parts": 0, "covered": [], "uncovered": []}, empty["coverage"]

    parts = [{"shape": "circle", "color": "red", "zone": "r0",
              "pattern": "solid", "size": "medium"}]
    red_img, red_log = render_relations(parts, size=96, seed=0)
    no_log = readback_mismatches(red_img, parts, [], 96)
    assert no_log["verifiable"] is False and no_log["total"] is None, no_log
    assert no_log["coverage"]["covered"] == [] and no_log["coverage"]["parts"] == 1
    assert "落格日志" in no_log["coverage"]["uncovered"][0]["reason"], no_log["coverage"]
    # 对照：同一张图、同一批部件，日志在册时才是可判的（不是「图不可读」）
    ok = readback_mismatches(red_img, parts, red_log, 96)
    assert ok["verifiable"] is True and ok["coverage"]["covered"] == [0], ok

    # 空 parts 走修正入口也不得报成功
    rr = refine_render([], size=96, seed=0, rounds=REFINE_DEFAULT_ROUNDS)
    assert rr["verifiable"] is False and rr["before"] is None and rr["after"] is None, rr
    assert "不声称成功" in rr["stop_reason"], rr["stop_reason"]
    print("[数字] F07 空/缺账: empty=%s no_log=%s（同图有账时 covered=%s）"
          % (empty["verifiable"], no_log["verifiable"], ok["coverage"]["covered"]))


def test_f07_real_red_dotted_circle_seed7_improves_by_real_pixels():
    """保留既有实证：96px / seed7 / 红色小点纹圆的真实差异 1→0（读数由本文件自算）。

    与「覆盖外空真」对照：同一条路径上可判部件给出的是**真实下降**，不是 0/0。
    """
    text, seed = "左上有红色小点纹圆形", 7
    off = generate_from_text(text, size=96, seed=seed, refine_rounds=0)
    on = generate_from_text(text, size=96, seed=seed)
    before = pixel_mismatches(off["image"], off["parts"], off["log"])
    after = pixel_mismatches(on["image"], on["parts"], on["log"])

    assert len(before) == 1 and after == [], (before, after)
    ref = on["refine"]
    assert ref["verifiable"] is True and ref["before"] == 1 and ref["after"] == 0, ref
    assert ref["coverage"]["covered"] == [0] and ref["coverage"]["judged"] == [0], ref["coverage"]
    assert ref["overrides"] == [{"part": 0, "render_params": {"pattern_period": 2}}]
    assert not np.array_equal(off["image"], on["image"]), "改善必须来自真实重渲染"
    assert on["parts"] == off["parts"] and on["text"] == off["text"], "意图/seed 不得被改"
    print("[数字] F07 红色点纹圆 seed=%d: 独立读数 %d -> %d；覆盖=%s"
          % (seed, len(before), len(after), ref["coverage"]["judged"]))


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q", "--no-header"]))
