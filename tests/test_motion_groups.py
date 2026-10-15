# -*- coding: utf-8 -*-
"""test_motion_groups · 运动组合体检测与预测（lingshu/world/motion_groups.py）
============================================================================
范围：观测窗口内共同平移的实体集合。首版只支持共同平移；组模型不认定机械连接。
输入：统一世界模型的真实观测 history，每条 {"tick": int, "entities": {eid: [x,y,z]}}。
位移判据只看水平 x/z；y 不参与判据，预测按组内相对高度差给出未观测成员的高度。

断言组：
  A1 3 实体共同平移成组：成员、锚点、偏移、相对高度、速度、位移、窗口、采样 dt 逐项查
  A2 成员转独立运动后重算分组：前段 3 人组，含独立段后只剩 {e1,e2}
  A3 不同向、不同速不成组；同地点静止集合不成组
  A4 完全兼容成员集：改名与 entities 插入序不改变组语义
  A4b 非传递兼容图：结果确定、与插入序无关；改名可能换极大团，本版不承诺改名不变
  A5 输入 history 零改动；重复调用输出逐位一致
  A6 缺帧不造样本：成员缺席区间整段跳过，dts 只记真实间隔
  A7 模拟器隐藏字段（goal/_rng/reward 等）不改变观测面结果
  A8 预测入口：来源 motion_group、actual=False，只预测未观测成员
  A9 组内无可见锚点 ⇒ unresolved，不给坐标
  A10 检测与预测结果可 json.dumps 往返
  A11 遮蔽一名成员后的对照：组预测 / 独立恒速 / 静止三种基线，两种适用条件各判一次
  A12 接口合同：统一世界模型写入的 history 原样入参即可检测
  A13 窗口内相对位置缓慢分离超容差不成组；容差内仍成组
  A14 相对偏移来回漂移（首尾净值 0）不成组：判据不是首尾净值
  A15 组速度定义：净位移 / 总 dt，不是区间速度的算术均值
  A16 异高成员被遮蔽：预测高度保留各自相对高度，不拉平到锚点高度
  B   三个命名定点变异在隔离源码副本上把对应语义判红，工作树零改动

定点变异（隔离副本；清理只用 TemporaryDirectory 自带的删除，不对其它路径调用 rmtree）：
  M1 取消窗口内相对位置稳定判据：'if drift > tol_pos:' 恒假 ⇒ 缓慢分离的成员成组，A13 语义红
  M2 删组速度：'speed' 置空表 ⇒ A1 的“组模型必须给出共同位移速度”红
  M3 预测高度拉平到锚点：y 直接取锚点 y ⇒ 异高遮蔽反例红

运行（仓根）：
  .venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_motion_groups.py -q -p no:cacheprovider
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world import motion_groups as MG  # noqa: E402

MODULE_PATH = os.path.join(REPO, "lingshu", "world", "motion_groups.py")

BASE = {"e1": (0.0, 1.0, 0.0), "e2": (2.0, 1.0, 0.0), "e3": (0.0, 1.0, 3.0)}
BASE_HEIGHTS = {"e1": (0.0, 1.0, 0.0), "e2": (2.0, 3.0, 0.0), "e3": (0.0, 5.0, 3.0)}
TOGETHER = {"e1": (1.0, 0.5), "e2": (1.0, 0.5), "e3": (1.0, 0.5)}
TICKS5 = [1, 2, 3, 4, 5]

_MUTATIONS = (
    ("取消窗口内相对位置稳定判据",
     "            if drift > tol_pos:",
     "            if False:  # 变异 A：取消窗口内相对位置稳定判据",
     "每 tick 缓慢分离的成员不得成组"),
    ("删组速度",
     '        "speed": [round(vx, 6), round(vz, 6)],',
     '        "speed": [],  # 变异 B：删组速度',
     "组模型必须给出共同位移速度"),
    ("预测高度拉平到锚点高度",
     "                    round(anchor_pos[1] + offset[1], 6),",
     "                    round(anchor_pos[1], 6),  # 变异 C：预测高度拉平到锚点高度",
     "异高成员的预测高度必须保留相对高度"),
)


# --------------------------------------------------------------- 构造工具
def _record(tick, positions, **extra):
    """positions: {eid: (x, y, z)} → 一条观测记录。"""
    record = {"tick": tick,
              "entities": {eid: [float(value) for value in pos]
                           for eid, pos in positions.items()}}
    record.update(extra)
    return record


def _translate(base, velocity, ticks, absent=None):
    """按固定速度平移；absent: {tick: (eid, ...)} 表示该 tick 缺席的成员。"""
    absent = absent or {}
    history = []
    for tick in ticks:
        step = tick - ticks[0]
        positions = {}
        for eid, (x, y, z) in base.items():
            if eid in absent.get(tick, ()):
                continue
            vx, vz = velocity[eid]
            positions[eid] = (x + vx * step, y, z + vz * step)
        history.append(_record(tick, positions))
    return history


def _march(base, ticks, speed_at):
    """按 speed_at(eid, 起始 tick) 逐帧累加速度，保证帧间连续、dt 真实。"""
    positions = {eid: [float(value) for value in pos] for eid, pos in base.items()}
    history = []
    for index, tick in enumerate(ticks):
        if index:
            dt = tick - ticks[index - 1]
            for eid, pos in positions.items():
                vx, vz = speed_at(eid, ticks[index - 1])
                pos[0] += vx * dt
                pos[2] += vz * dt
        history.append(_record(tick, positions))
    return history


def _occlude(history, eid, from_tick):
    """遮蔽：该成员从 from_tick 起不出现在观测面。返回新列表，不改原历史。"""
    return [{"tick": rec["tick"],
             "entities": {other: list(pos) for other, pos in rec["entities"].items()
                          if not (other == eid and rec["tick"] >= from_tick)}}
            for rec in history]


def _slow_separation_history(per_tick):
    """e1 匀速直行，e2 每 tick 相对 e1 多走 per_tick；8 帧共 7 个区间。"""
    return [_record(tick, {"e1": (1.0 * (tick - 1), 1.0, 0.0),
                           "e2": (1.0 * (tick - 1) + per_tick * (tick - 1), 1.0, 0.0)})
            for tick in range(1, 9)]


def _non_uniform_dt_history():
    """e1、e2 同速平移，采样 tick 为 1、2、5：区间速度 1.0 与 3.0。"""
    return [_record(1, {"e1": [0.0, 1.0, 0.0], "e2": [2.0, 1.0, 0.0]}),
            _record(2, {"e1": [1.0, 1.0, 0.0], "e2": [3.0, 1.0, 0.0]}),
            _record(5, {"e1": [10.0, 1.0, 0.0], "e2": [12.0, 1.0, 0.0]})]


def _non_transitive_history():
    """e1 与 e2、e2 与 e3 相对稳定；e1 与 e3 在窗口内偏离 0.08，超过 tol_pos=0.05。"""
    chord_2 = [0.0, 0.03, 0.05, 0.05, 0.05]
    chord_3 = [0.0, 0.01, 0.02, 0.03, 0.03]
    history = []
    for index, tick in enumerate(range(1, 6)):
        history.append(_record(tick, {
            "e1": (1.0 * index, 1.0, 0.0),
            "e2": (1.0 * index + chord_2[index], 1.0, 0.0),
            "e3": (1.0 * index + chord_2[index] + chord_3[index], 1.0, 3.0)}))
    return history


def _height_occlusion():
    """异高三成员共同平移；e2、e3 从 tick 6 起被遮蔽。返回（观察史，真值史）。"""
    truth = _translate(BASE_HEIGHTS, TOGETHER, [1, 2, 3, 4, 5, 6])
    seen = _occlude(_occlude(truth, "e2", 6), "e3", 6)
    return seen, truth


def _pair_max_offset(history, a, b):
    """两名成员在共同观测 tick 上，相对偏移相对首个共同观测的最大误差。"""
    common = [(rec["entities"][a], rec["entities"][b])
              for rec in history if a in rec["entities"] and b in rec["entities"]]
    base = (common[0][1][0] - common[0][0][0], common[0][1][2] - common[0][0][2])
    return max(math.hypot(pos_b[0] - pos_a[0] - base[0], pos_b[2] - pos_a[2] - base[1])
               for pos_a, pos_b in common)


def _truth(history, eid, tick):
    """全量真实轨迹里取某 tick 的位置，供对照基线算误差。"""
    for rec in history:
        if rec["tick"] == tick:
            return rec["entities"][eid]
    raise AssertionError("真实轨迹缺少 tick=%d" % tick)


def _icv_predict(history, eid, tick):
    """独立恒速基线：只用该成员自己最近两次观测外推。"""
    points = [(rec["tick"], rec["entities"][eid])
              for rec in history if eid in rec["entities"]]
    if len(points) < 2:
        return None
    (t0, p0), (t1, p1) = points[-2], points[-1]
    dt = t1 - t0
    if dt <= 0:
        return None
    return [p1[0] + (p1[0] - p0[0]) / dt * (tick - t1), p1[1],
            p1[2] + (p1[2] - p0[2]) / dt * (tick - t1)]


def _static_predict(history, eid, tick):
    """静止基线：取该成员最后一次观测位置。"""
    points = [rec["entities"][eid] for rec in history if eid in rec["entities"]]
    if not points:
        return None
    last = points[-1]
    return [float(last[0]), float(last[1]), float(last[2])]


def _error(predicted, actual):
    """水平面误差（x/z），与判据口径一致。"""
    return math.hypot(predicted[0] - actual[0], predicted[2] - actual[2])


def _relative_geometry(group, rename):
    """组内两两相对位置（x/z），与锚点选择、成员改名无关。"""
    rel = {group["anchor"]: (0.0, 0.0)}
    for eid, offset in group["offsets"].items():
        rel[eid] = (float(offset[0]), float(offset[1]))
    pairs = []
    for first in group["members"]:
        for second in group["members"]:
            if first != second:
                pairs.append([rename(first), rename(second),
                              round(rel[second][0] - rel[first][0], 6),
                              round(rel[second][1] - rel[first][1], 6)])
    return sorted(pairs)


def _partition(model, rename):
    return sorted(sorted(rename(eid) for eid in group["members"])
                  for group in model["groups"])


def _rename_history(history, rename_map):
    return [{"tick": rec["tick"],
             "entities": {rename_map[eid]: list(pos)
                          for eid, pos in rec["entities"].items()}}
            for rec in history]


# ------------------------------------------------------------------ A1
def test_a1_three_entities_in_common_translation_form_one_group():
    history = _translate(BASE, TOGETHER, TICKS5)
    model = MG.detect_motion_groups(history)
    assert model["kind"] == "motion_groups"
    assert model["rejected"] == []
    assert len(model["groups"]) == 1
    group = model["groups"][0]
    assert group["members"] == ["e1", "e2", "e3"]
    assert group["anchor"] == "e1"
    assert group["kind"] == "common_translation"
    assert group["mechanical_link"] is False
    assert group["offsets"] == {"e2": [2.0, 0.0], "e3": [0.0, 3.0]}
    assert group["y_offsets"] == {"e2": 0.0, "e3": 0.0}
    assert group["speed"] == [1.0, 0.5]
    assert group["speed_magnitude"] == pytest.approx(math.hypot(1.0, 0.5), abs=1e-6)
    assert group["displacement"] == [4.0, 2.0]
    assert group["window"] == {"start_tick": 1, "end_tick": 5, "frames": 5,
                               "ticks": [1, 2, 3, 4, 5]}
    assert group["samples"] == 4
    assert group["evidence"]["dts"] == [1.0, 1.0, 1.0, 1.0]
    assert group["evidence"]["max_step_drift"] == 0.0
    assert group["evidence"]["max_offset_drift"] == 0.0
    assert group["evidence"]["moving_samples"] == 4
    assert MG.get_motion_group(model, "e2") is group
    assert MG.get_motion_group(model, "e9") is None


# ------------------------------------------------------------------ A2
def test_a2_group_recomputed_when_member_moves_independently():
    def speed_at(eid, tick):
        if tick < 4:
            return TOGETHER[eid]
        return (1.0, -0.5) if eid == "e3" else (1.0, 0.5)

    full = _march(BASE, [1, 2, 3, 4, 5, 6, 7, 8], speed_at)
    early = MG.detect_motion_groups(full[:4])
    assert [group["members"] for group in early["groups"]] == [["e1", "e2", "e3"]]

    model = MG.detect_motion_groups(full)
    assert [group["members"] for group in model["groups"]] == [["e1", "e2"]]
    assert MG.get_motion_group(model, "e3") is None
    assert model["groups"][0]["window"]["end_tick"] == 8


# ------------------------------------------------------------------ A3
def test_a3_different_direction_or_speed_does_not_form_group():
    opposite = _march(BASE, TICKS5,
                      lambda eid, tick: {"e1": (1.0, 0.0), "e2": (-1.0, 0.0),
                                         "e3": (0.0, 1.0)}[eid])
    assert MG.detect_motion_groups(opposite)["groups"] == []

    different_speed = _march(BASE, TICKS5,
                             lambda eid, tick: {"e1": (1.0, 0.0), "e2": (0.5, 0.0),
                                                "e3": (0.25, 0.0)}[eid])
    assert MG.detect_motion_groups(different_speed)["groups"] == []


def test_a3b_stationary_set_is_not_a_group():
    spread = [_record(tick, BASE) for tick in TICKS5]
    assert MG.detect_motion_groups(spread)["groups"] == []

    collocated = [{eid: (5.0, 1.0, 5.0) for eid in BASE}
                  for _ in TICKS5]
    same_place = [_record(tick, positions) for tick, positions in zip(TICKS5, collocated)]
    assert MG.detect_motion_groups(same_place)["groups"] == []


# ------------------------------------------------------------------ A4
def test_a4_complete_compatibility_keeps_semantics_under_rename_and_insertion_order():
    """三成员两两兼容；此时首次适配与合并顺序无关，改名不改组语义。"""
    history = _translate(BASE, TOGETHER, TICKS5)
    renamed_map = {"e1": "z9", "e2": "a0", "e3": "m5"}
    renamed = [{"tick": rec["tick"],
                "entities": {renamed_map[eid]: list(pos)
                             for eid, pos in reversed(list(rec["entities"].items()))}}
               for rec in history]

    model = MG.detect_motion_groups(history)
    other = MG.detect_motion_groups(renamed)
    assert _partition(model, renamed_map.get) == _partition(other, lambda eid: eid)
    assert sorted(group["speed"] for group in model["groups"]) ==         sorted(group["speed"] for group in other["groups"])
    assert sorted(group["displacement"] for group in model["groups"]) ==         sorted(group["displacement"] for group in other["groups"])
    assert sorted(_relative_geometry(group, renamed_map.get)
                  for group in model["groups"]) ==         sorted(_relative_geometry(group, lambda eid: eid)
               for group in other["groups"])

    reversed_records = [{"tick": rec["tick"],
                         "entities": dict(reversed(list(rec["entities"].items())))}
                        for rec in history]
    again = MG.detect_motion_groups(reversed_records)
    assert json.dumps(again, sort_keys=True) == json.dumps(model, sort_keys=True)


# ------------------------------------------------------------------ A4b
def test_a4b_non_transitive_compatibility_is_deterministic_without_rename_claim():
    """非传递兼容图：只保证结果确定、与插入序无关。

    e1 与 e2、e2 与 e3 通过判据，e1 与 e3 不通过。首次适配按 eid 字典序选极大团，
    改名会改字典序，可能选到另一个极大团，所以本版不承诺所有情形改名不变。
    """
    history = _non_transitive_history()
    assert _pair_max_offset(history, "e1", "e2") <= 0.05
    assert _pair_max_offset(history, "e2", "e3") <= 0.05
    assert _pair_max_offset(history, "e1", "e3") > 0.05

    renamed_map = {"e1": "z9", "e2": "a0", "e3": "m5"}
    inverse_map = {renamed: original for original, renamed in renamed_map.items()}
    renamed = _rename_history(history, renamed_map)
    model = MG.detect_motion_groups(history)
    other = MG.detect_motion_groups(renamed)

    for candidate, back in ((model, lambda eid: eid), (other, inverse_map.get)):
        assert len(candidate["groups"]) == 1
        members = [back(eid) for eid in candidate["groups"][0]["members"]]
        assert len(members) == 2
        assert _pair_max_offset(history, members[0], members[1]) <= 0.05

    assert json.dumps(MG.detect_motion_groups(history), sort_keys=True) ==         json.dumps(model, sort_keys=True)
    reversed_records = [{"tick": rec["tick"],
                         "entities": dict(reversed(list(rec["entities"].items())))}
                        for rec in history]
    assert json.dumps(MG.detect_motion_groups(reversed_records), sort_keys=True) ==         json.dumps(model, sort_keys=True)


# ------------------------------------------------------------------ A5
def test_a5_input_untouched_and_repeatable():
    history = _translate(BASE, TOGETHER, TICKS5)
    before = copy.deepcopy(history)
    snapshot = json.dumps(history, sort_keys=True)

    first = MG.detect_motion_groups(history)
    second = MG.detect_motion_groups(history)
    prediction = MG.predict_with_groups(history)
    again = MG.predict_with_groups(history)

    assert history == before
    assert json.dumps(history, sort_keys=True) == snapshot
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.dumps(prediction, sort_keys=True) == json.dumps(again, sort_keys=True)


# ------------------------------------------------------------------ A6
def test_a6_absent_member_adds_no_sample_and_keeps_real_dt():
    history = _translate(BASE, TOGETHER, TICKS5, absent={2: ("e3",), 3: ("e3",)})
    model = MG.detect_motion_groups(history)
    assert len(model["groups"]) == 1
    group = model["groups"][0]
    assert group["members"] == ["e1", "e2", "e3"]
    assert group["window"]["ticks"] == [1, 4, 5]
    assert group["samples"] == 2
    assert group["evidence"]["dts"] == [3.0, 1.0]

    sparse = _translate(BASE, TOGETHER, TICKS5,
                        absent={2: ("e3",), 3: ("e3",), 4: ("e3",), 5: ("e3",)})
    sparse_model = MG.detect_motion_groups(sparse)
    assert [group["members"] for group in sparse_model["groups"]] == [["e1", "e2"]]
    assert MG.get_motion_group(sparse_model, "e3") is None


# ------------------------------------------------------------------ A7
def test_a7_simulator_hidden_fields_do_not_change_observation_surface():
    clean = _translate(BASE, TOGETHER, TICKS5)
    clean_model = MG.detect_motion_groups(clean)

    hidden = [dict(rec, goal="gather", _rng=1234567, reward=0.25, action="move",
                   timestamp=1000.5, hypothesis=True, agent_belief={"e1": 9.9})
              for rec in clean]
    assert json.dumps(MG.detect_motion_groups(hidden), sort_keys=True) ==         json.dumps(clean_model, sort_keys=True)

    tuples = [{"tick": rec["tick"],
               "entities": {eid: tuple(pos) for eid, pos in rec["entities"].items()}}
              for rec in clean]
    assert json.dumps(MG.detect_motion_groups(tuples), sort_keys=True) ==         json.dumps(clean_model, sort_keys=True)


# ------------------------------------------------------------------ A8
def test_a8_prediction_uses_observed_anchor_and_labels_source():
    history = _translate(BASE, TOGETHER, [1, 2, 3, 4, 5, 6], absent={6: ("e3",)})
    before = copy.deepcopy(history)

    result = MG.predict_with_groups(history)
    assert result["kind"] == "motion_group_predictions"
    assert result["tick"] == 6
    assert set(result["predictions"]) == {"e3"}
    assert result["unresolved"] == {}
    prediction = result["predictions"]["e3"]
    assert prediction["pos"] == [5.0, 1.0, 5.5]
    assert prediction["source"] == "motion_group"
    assert prediction["actual"] is False
    assert prediction["anchor"] == "e1"
    assert prediction["offset"] == [0.0, 3.0]
    assert prediction["y_offset"] == 0.0
    assert prediction["offset_tick"] == 5
    assert prediction["gap"] == 1
    assert history == before
    assert "e3" not in history[-1]["entities"]

    group = MG.get_motion_group(result, "e3")
    direct = MG.predict_group_members(group, {"e1": [5.0, 1.0, 2.5],
                                              "e2": [7.0, 1.0, 2.5]}, tick=6)
    assert direct["predictions"]["e3"]["pos"] == [5.0, 1.0, 5.5]

    fallback = MG.predict_group_members(group, {"e2": [7.0, 1.0, 2.5]}, tick=6)
    assert fallback["predictions"]["e1"]["anchor"] == "e2"
    assert fallback["predictions"]["e1"]["pos"] == [5.0, 1.0, 2.5]
    assert fallback["predictions"]["e3"]["anchor"] == "e2"
    assert fallback["predictions"]["e3"]["pos"] == [5.0, 1.0, 5.5]


def test_a9_missing_anchor_gives_reason_not_coordinates():
    history = _translate(BASE, TOGETHER, TICKS5)
    group = MG.detect_motion_groups(history)["groups"][0]
    result = MG.predict_group_members(group, {}, tick=9)
    assert result["predictions"] == {}
    assert set(result["unresolved"]) == {"e1", "e2", "e3"}
    assert result["unresolved"]["e3"]["reason"] == "no_observed_anchor"
    assert "pos" not in result["unresolved"]["e3"]


# ------------------------------------------------------------------ A10
def test_a10_outputs_are_json_plain():
    history = _translate(BASE, TOGETHER, [1, 2, 3, 4, 5, 6], absent={6: ("e3",)})
    model = MG.detect_motion_groups(history)
    result = MG.predict_with_groups(history)
    assert json.loads(json.dumps(model)) == model
    assert json.loads(json.dumps(result)) == result


# ------------------------------------------------------------------ A11
def test_a11_occlusion_compares_group_against_independent_baselines():
    """遮蔽 e3 后三种预测正面对照；适用条件随场景不同而不同。"""
    ticks = list(range(1, 16))
    from_tick = 6

    # 场景一：遮蔽后 e3 仍随组运动，且组在遮蔽后转向。
    # 适用条件：锚点当前可见 + e3 与锚点的相对位置未变 ⇒ 组预测误差 0。
    turned = _march(BASE, ticks, lambda eid, tick: (1.0, 0.5) if tick < 5 else (-1.0, 2.0))
    seen_turned = _occlude(turned, "e3", from_tick)
    group_result = MG.predict_with_groups(seen_turned, window=len(seen_turned) + 1)
    group_pos = group_result["predictions"]["e3"]["pos"]
    actual = _truth(turned, "e3", 15)
    group_error = _error(group_pos, actual)
    icv_error = _error(_icv_predict(seen_turned, "e3", 15), actual)
    static_error = _error(_static_predict(seen_turned, "e3", 15), actual)
    assert group_error == 0.0
    assert group_error < static_error
    assert group_error < icv_error

    # 场景二：窗口内全组同速，遮蔽后 e3 保持自己的恒速，组改向。
    # 适用条件：e3 脱离组运动 ⇒ 独立恒速基线更准，组预测只比静止基线好。
    def member_keeps_speed(eid, tick):
        if eid == "e3" or tick < 5:
            return (1.0, 0.5)
        return (0.5, 1.0)

    split = _march(BASE, ticks, member_keeps_speed)
    seen_split = _occlude(split, "e3", from_tick)
    split_result = MG.predict_with_groups(seen_split, window=len(seen_split) + 1)
    assert set(split_result["predictions"]) == {"e3"}
    split_group = split_result["predictions"]["e3"]
    split_actual = _truth(split, "e3", 15)
    split_error = _error(split_group["pos"], split_actual)
    split_icv = _error(_icv_predict(seen_split, "e3", 15), split_actual)
    split_static = _error(_static_predict(seen_split, "e3", 15), split_actual)
    assert split_group["gap"] == 10
    assert split_group["anchor"] == "e1"
    assert split_icv == pytest.approx(0.0, abs=1e-9)
    assert split_icv < split_error < split_static

    print("\n遮蔽 e3 后 tick = 15 的水平误差（位置单位）：")
    print("场景 | 组预测 | 独立恒速 | 静止")
    print("组内继续同行 | %.6f | %.6f | %.6f" % (group_error, icv_error, static_error))
    print("成员保持恒速 | %.6f | %.6f | %.6f" % (split_error, split_icv, split_static))


# ------------------------------------------------------------------ A12
def test_a12_real_world_model_history_is_accepted_as_is():
    """接口合同：统一世界模型写入的 history 原样入参即可检测。"""
    from lingshu.world.world_model import UnifiedWorldModel

    model = UnifiedWorldModel(size=24, ground_level=1, seed=7)
    for tick in TICKS5:
        step = tick - TICKS5[0]
        observations = [{"eid": eid, "category": "box",
                         "pos": (x + step * 1.0, y, z + step * 0.5)}
                        for eid, (x, y, z) in BASE.items()]
        model.perceive(observations=observations, tool="observer", tick=tick)

    assert model.history[0] == {"tick": 1,
                                "entities": {"e1": [0.0, 1.0, 0.0],
                                             "e2": [2.0, 1.0, 0.0],
                                             "e3": [0.0, 1.0, 3.0]}}
    groups = MG.detect_motion_groups(model.history)["groups"]
    assert [group["members"] for group in groups] == [["e1", "e2", "e3"]]
    assert groups[0]["speed"] == [1.0, 0.5]
    assert groups[0]["evidence"]["dts"] == [1.0, 1.0, 1.0, 1.0]


# ------------------------------------------------------------------ A13
def test_a13_slow_separation_beyond_tolerance_is_not_a_group():
    """每 tick 相对分离 0.04、8 帧共 0.28：单区间漂移在容差内，整窗偏移已超容差。"""
    drifting = MG.detect_motion_groups(_slow_separation_history(0.04), tol_pos=0.05)
    assert drifting["groups"] == []
    assert drifting["ticks"] == [1, 8]

    inside = MG.detect_motion_groups(_slow_separation_history(0.005), tol_pos=0.05)
    assert [group["members"] for group in inside["groups"]] == [["e1", "e2"]]
    assert inside["groups"][0]["offsets"] == {"e2": [0.035, 0.0]}
    assert inside["groups"][0]["evidence"]["max_offset_drift"] == pytest.approx(0.035)
    assert inside["groups"][0]["speed"] == [1.0, 0.0]


# ------------------------------------------------------------------ A14
def test_a14_back_and_forth_drift_is_not_a_group():
    """相对偏移来回漂移 ±0.06，首尾净值 0；只用首尾净值的判据会漏检。"""
    pattern = [0.0, 0.06, 0.0, 0.06, 0.0]
    history = [_record(tick, {"e1": (1.0 * index, 1.0, 0.0),
                              "e2": (1.0 * index + pattern[index], 1.0, 0.0)})
               for index, tick in enumerate(range(1, 6))]
    assert pattern[-1] - pattern[0] == 0.0
    assert max(abs(value - pattern[0]) for value in pattern) > 0.05
    assert MG.detect_motion_groups(history, tol_pos=0.05)["groups"] == []


# ------------------------------------------------------------------ A15
def test_a15_speed_is_net_displacement_over_total_dt():
    """非均匀 dt：区间速度 1.0 与 3.0，算术均值 2.0；定义取净位移 / 总 dt = 2.5。"""
    group = MG.detect_motion_groups(_non_uniform_dt_history())["groups"][0]
    assert group["window"]["ticks"] == [1, 2, 5]
    assert group["evidence"]["dts"] == [1.0, 3.0]
    assert group["displacement"] == [10.0, 0.0]
    assert group["speed"] == [2.5, 0.0]
    assert group["speed_magnitude"] == 2.5
    assert (1.0 + 3.0) / 2.0 == 2.0, "区间速度算术均值与净位移 / 总 dt 不同"


# ------------------------------------------------------------------ A16
def test_a16_occluded_members_keep_their_observed_heights():
    """异高三成员共同平移：预测高度保留各自相对高度，不拉平到锚点高度。"""
    seen, truth = _height_occlusion()
    result = MG.predict_with_groups(seen)
    group = MG.get_motion_group(result, "e2")
    assert group["y_offsets"] == {"e2": 2.0, "e3": 4.0}
    assert set(result["predictions"]) == {"e2", "e3"}
    assert result["predictions"]["e2"]["pos"] == [7.0, 3.0, 2.5]
    assert result["predictions"]["e3"]["pos"] == [5.0, 5.0, 5.5]
    assert result["predictions"]["e2"]["y_offset"] == 2.0
    assert result["predictions"]["e3"]["y_offset"] == 4.0
    for eid in ("e2", "e3"):
        assert result["predictions"][eid]["pos"][1] == _truth(truth, eid, 6)[1]
    assert result["predictions"]["e2"]["pos"][1] != 1.0, "高度不得拉平到锚点 1.0"

    # 锚点换人时同一高度差仍然成立：只可见 e2 时以 e2 为锚点。
    direct = MG.predict_group_members(group, {"e2": [7.0, 3.0, 2.5]}, tick=6)
    assert direct["predictions"]["e3"]["anchor"] == "e2"
    assert direct["predictions"]["e3"]["y_offset"] == 2.0
    assert direct["predictions"]["e3"]["pos"][1] == 5.0
    assert direct["predictions"]["e1"]["y_offset"] == -2.0
    assert direct["predictions"]["e1"]["pos"][1] == 1.0


# ------------------------------------------------------------------- B
def _isolation_scenario(module):
    """变异判别场景；任一语义被破坏即 AssertionError。"""
    together = module.detect_motion_groups(_translate(BASE, TOGETHER, TICKS5))
    assert len(together["groups"]) == 1, "3 实体共同平移必须成组"
    group = together["groups"][0]
    assert group["members"] == ["e1", "e2", "e3"], "成员集合必须按字典序给出"
    assert group["offsets"] == {"e2": [2.0, 0.0], "e3": [0.0, 3.0]},         "成员偏移必须相对锚点给出"
    assert group["speed"] == [1.0, 0.5], "组模型必须给出共同位移速度"
    assert group["displacement"] == [4.0, 2.0], "组模型必须给出窗口内共同位移"

    drifting = module.detect_motion_groups(_slow_separation_history(0.04), tol_pos=0.05)
    assert drifting["groups"] == [], "每 tick 缓慢分离的成员不得成组"

    seen_heights, _truth_heights = _height_occlusion()
    heights = module.predict_with_groups(seen_heights)
    assert heights["predictions"]["e2"]["pos"][1] == 3.0,         "异高成员的预测高度必须保留相对高度"
    assert heights["predictions"]["e3"]["pos"][1] == 5.0,         "异高成员的预测高度必须保留相对高度"

    non_uniform = module.detect_motion_groups(_non_uniform_dt_history())["groups"][0]
    assert non_uniform["speed"] == [2.5, 0.0], "非均匀 dt 的组速度必须取净位移 / 总 dt"

    independent = module.detect_motion_groups(_march(
        BASE, TICKS5, lambda eid, tick: {"e1": (1.0, 0.0), "e2": (-1.0, 0.0),
                                         "e3": (0.0, 1.0)}[eid]))
    assert independent["groups"] == [], "独立不同向运动不得成组"

    still = [_record(tick, BASE) for tick in TICKS5]
    assert module.detect_motion_groups(still)["groups"] == [], "同地点静止集合不得成组"


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def test_b_named_mutations_turn_isolation_red_without_touching_working_tree():
    before = _digest(MODULE_PATH)
    with open(MODULE_PATH, encoding="utf-8") as handle:
        source = handle.read()
    _isolation_scenario(MG)

    for name, old, new, expected in _MUTATIONS:
        assert source.count(old) == 1, "变异点 %s 在源码里不唯一：%r" % (name, old)
        mutated = source.replace(old, new)
        with tempfile.TemporaryDirectory(prefix="motion_groups_mutant_") as workdir:
            path = os.path.join(workdir, "motion_groups_mutant.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(mutated)
            module = _load_module(path, "motion_groups_mutant")
            with pytest.raises(AssertionError, match=expected):
                _isolation_scenario(module)

    assert _digest(MODULE_PATH) == before, "定点变异不得改动工作树源码"
