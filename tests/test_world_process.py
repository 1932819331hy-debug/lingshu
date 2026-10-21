# -*- coding: utf-8 -*-
"""test_world_process · 时间会意与示教迁移判据（世界过程第 1 组）
============================================================================
被测模块：lingshu/world/world_process.py

判据组：
  A 真实 history 识别 3 类过程——history 由 UnifiedWorldModel.perceive 产生，
    形状 [{"tick": int, "entities": {eid: [x, y, z]}}]。
    绕行后返回含等半径绕行一圈：径向距离不变，方位角扫描 360°，绝对回路等效半径 2.90。
    绕行判据需要三项几何证据同时成立：actor 绝对轨迹的闭合回路、actor 绕 ref 的方位角
    扫描、相对轨迹不穿越参照物（ref_min_distance > crossing_tol）。
  B 相似反例不冒认：接近后立刻离开 / 单调接近 / 汇合后分开 / 汇合后静止 /
    直线外走再原路回 / 直线穿过参照物再原路回（含整景旋转与平移变体）/
    参照物移动时的直线穿越往返（D = 2.0/2.25/2.5/3.0，终审 F1 的误报区间）/
    穿越参照物加外侧小圈（定点变异 M5 的靶子）/
    参照物外侧 0.6 处的直线往返（定点变异 M3 的靶子）/
    参照物外侧的局部回路 / 绕行不返回 / 同半径运动冒充停留 /
    包围参照物但有一段只离 0.45 的回路（终审 F2 反例二，不得被穿越检测误杀）。
  C 输入不变量：history 不被改写；同一输入重复调用同输出；缺帧不插值；
    tick 与实际 dt 正确；过程阶段覆盖 tick 与真实采样数一致。
    停止事件用速度判定，逐 tick 移动与稀疏 dt 下的漂移不混淆。
  D 三条基线：原坐标回放 / 仅平移 / 关系迁移（含 yaw 旋转与 scale 尺度），
    三者都可算；ref 会移动时 yaw 与 scale 变换 ref 位移与 rel 两者。
  E 边界：单一实体无过程；未识别返回空列表；生成帧标 source="generated"；
    迁移不改写记录；模块不调用 perceive/generate/execute。
  F 定点变异：M1 去方位角判据、M2 关原地停留条件、M3 去回路形状判据、
    M4 回路半径阈值退化、M5 去穿越检测（ref_min_distance > crossing_tol），在
    artifacts/world_process_delivery_20261014/process_fix/run_mutation_check_round3.py
    的隔离副本里变红（本件提供被判红的断言组 A/B）。

运行（仓根）：
    python -X utf8 -m pytest tests/test_world_process.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.world.world_model import UnifiedWorldModel          # noqa: E402
from lingshu.world.world_process import (ProcessThresholds,      # noqa: E402
                                         motion_events,
                                         extract_relative_trajectory,
                                         pair_samples,
                                         recognize_processes,
                                         track_samples,
                                         transfer_demonstration)

MODULE_PATH = os.path.join(REPO, "lingshu", "world", "world_process.py")


# ================= 夹具 =================

def real_history(tracks):
    """tracks = {eid: [[x, y, z], ...]} → UnifiedWorldModel 的真实观测 history。"""
    wm = UnifiedWorldModel(size=24, seed=7)
    for i in range(len(next(iter(tracks.values())))):
        wm.perceive([{"eid": eid, "category": "c", "pos": tuple(track[i])}
                     for eid, track in tracks.items()], tick=i + 1)
    return wm.history


def approach_stay_leave_history():
    """接近 4 tick → 停留 4 tick → 离开 4 tick；参照物 landmark 不动。"""
    return real_history({
        "landmark": [[10, 1, 10]] * 12,
        "walker": [[4, 1, 10], [5, 1, 10], [6, 1, 10], [7, 1, 10], [8, 1, 10],
                   [8.2, 1, 10], [8, 1, 10], [8.1, 1, 10], [7, 1, 10], [5, 1, 10],
                   [3, 1, 10], [1, 1, 10]]})


def detour_return_history():
    """绕行：距离从 3.0 抬升到 6.71 后回落到 3.0，actor 回到起点。"""
    return real_history({
        "goal": [[0, 1, 0]] * 8,
        "scout": [[3, 1, 0], [5, 1, 1], [5, 1, 4], [3, 1, 6], [1, 1, 5], [1, 1, 2],
                  [2.5, 1, 0.5], [3, 1, 0]]})


def circle_points(center, radius, steps=12, first=0, last=12):
    """圆心 center、半径 radius 的水平等分点，返回第 first..last 个（含端点）。"""
    return [[center[0] + radius * math.cos(2 * math.pi * k / steps),
             center[1],
             center[2] + radius * math.sin(2 * math.pi * k / steps)]
            for k in range(first, last + 1)]


def rel_offsets(track, ref):
    """track 各点相对 ref 的水平偏移；ref 可是单点 [x, y, z] 或同长轨迹。"""
    if len(ref) == 3 and all(isinstance(v, (int, float)) for v in ref):
        return [(p[0] - ref[0], p[2] - ref[2]) for p in track]
    return [(p[0] - q[0], p[2] - q[2]) for p, q in zip(track, ref)]


def bearing_sweep_deg(track, ref):
    """actor 绕 ref 的方位角总绝对变化量：测试件另算一遍，做法与模块代码无关。"""
    total = 0.0
    prev = None
    for rx, rz in rel_offsets(track, ref):
        ang = math.atan2(rz, rx)
        if prev is not None:
            total += abs((ang - prev + math.pi) % (2 * math.pi) - math.pi)
        prev = ang
    return math.degrees(total)


def _closed_loop_radius(points):
    """水平点列 (x, z) 的闭合回路等效半径 = 2 × |shoelace 面积| ÷ 折线周长。

    周长含末点回首点的闭合边。测试件另算一遍，做法与模块代码无关。
    """
    area2 = 0.0
    length = 0.0
    n = len(points)
    for i in range(n):
        x1, z1 = points[i]
        x2, z2 = points[(i + 1) % n]
        area2 += x1 * z2 - x2 * z1
        length += math.hypot(x2 - x1, z2 - z1)
    return abs(area2) / length if length else 0.0


def loop_radius(track):
    """actor 自身绝对水平轨迹的闭合回路等效半径：测试件另算一遍。

    判据用的就是这个量：actor 的绝对轨迹，不是相对 ref 的偏移。
    沿同一条直线往返时往程与回程面积相消，结果为 0；绕一圈时约等于回路的平均半径。
    参照物在窗口内移动不改变本读数。
    """
    return _closed_loop_radius([(p[0], p[2]) for p in track])


def rel_loop_radius(track, ref):
    """actor 相对 ref 的闭合回路等效半径（旧判据的量，现只作对照读数）。

    参照物在窗口内移动时，相对形状由 ref 的位移撑出面积，这个读数会变正，
    而 actor 的绝对轨迹仍可能是一条直线。
    """
    return _closed_loop_radius(rel_offsets(track, ref))


def ref_min_distance(track, ref):
    """相对轨迹到 ref 的最小水平距离（穿越判据的读数）：测试件另算一遍。

    ref 可是单点或同长轨迹。该读数只判穿越：0 表示轨迹穿过参照物，
    大于 crossing_tol 0.05 即算不穿越，不要求最小分离达到某个距离。
    """
    rel = rel_offsets(track, ref)
    best = math.inf
    for p, q in zip(rel, rel[1:]):
        dx, dz = q[0] - p[0], q[1] - p[1]
        seg2 = dx * dx + dz * dz
        if seg2 == 0.0:
            dist = math.hypot(p[0], p[1])
        else:
            t = max(0.0, min(1.0, -(p[0] * dx + p[1] * dz) / seg2))
            dist = math.hypot(p[0] + t * dx, p[1] + t * dz)
        best = min(best, dist)
    return best


def scene_points(ref, offsets, yaw, shift):
    """ref 为原点的水平偏移 (x, z) → 整景绕 +y 轴旋转 yaw 再平移 shift。

    旋转式与 transfer_demonstration 一致；y 分量不参与水平旋转。
    """
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    return [[ref[0] + shift[0] + ox * cos_y + oz * sin_y,
             ref[1],
             ref[2] + shift[1] - ox * sin_y + oz * cos_y]
            for ox, oz in offsets]


def track_path(track):
    """轨迹的水平路程：测试件另算一遍，做法与模块代码无关。"""
    return sum(math.dist(p[::2], q[::2]) for p, q in zip(track, track[1:]))


def meet_escort_history():
    """汇合：距离 8.0 → 1.2（t4 起 ≤ meet_gap），其后两者等偏移同行。"""
    return real_history({
        "lead": [[0, 1, 0], [1, 1, 0], [2, 1, 0], [3, 1, 0], [4, 1, 0.5], [5, 1, 1.5],
                 [6, 1, 3], [7, 1, 5]],
        "mate": [[8, 1, 0], [6, 1, 0], [4.5, 1, 0], [4.2, 1, 0], [5.2, 1, 0.5],
                 [6.2, 1, 1.5], [7.2, 1, 3], [8.2, 1, 5]]})


def _offset(frame, actor, ref):
    """帧内 actor 相对 ref 的偏移。"""
    e = frame["entities"]
    return tuple(e[actor][k] - e[ref][k] for k in range(3))


def _recognition_of_frames(frames):
    """生成帧 → 识别输入（只取 tick 与 entities，生成帧同样是真实位置数据）。"""
    return [{"tick": f["tick"], "entities": f["entities"]} for f in frames]


# D1 几何用的基准形状：直线穿过参照物（往返）与绕参照物的回路。
# 偏移是相对 ref 的水平 (x, z)；整景变体由 scene_points 施加旋转与平移。
REF_AT = [0.0, 1.0, 0.0]
CROSS_LINE = [(x, 0.0) for x in (3.0, 2.0, 1.0, -1.0, -2.0, -3.0, -2.0, -1.0, 1.0, 2.0, 3.0)]
ARC_LOOP = [(3.0, 0.0), (5.0, 1.0), (5.0, 4.0), (3.0, 6.0), (1.0, 5.0), (1.0, 2.0),
            (2.5, 0.5), (3.0, 0.0)]
SCENE_VARIANTS = [(0.0, (0.0, 0.0)), (math.pi / 3, (0.0, 0.0)),
                  (math.pi / 2, (4.0, -3.0)), (math.pi, (-3.0, 2.0))]


def scene_history(offsets, yaw, shift):
    """整景变体 → (真实 history, actor 轨迹, ref 位置)。

    ref 固定在 REF_AT 平移后的位置；actor 走整体旋转 yaw 再平移的轨迹。
    旋转与平移不改变相对距离、相对角度与回路面积。
    """
    ref = [REF_AT[0] + shift[0], REF_AT[1], REF_AT[2] + shift[1]]
    actor = scene_points(REF_AT, offsets, yaw, shift)
    return real_history({"beacon": [ref] * len(actor), "rider": actor}), actor, ref


# ================= A 组：真实 history 识别 3 类过程 =================

def test_approach_stay_leave_from_real_history():
    history = approach_stay_leave_history()
    results = recognize_processes(history)
    assert [p["type"] for p in results] == ["approach_stay_leave"], results
    proc = results[0]
    assert proc["roles"] == {"actor": "walker", "ref": "landmark"}, proc["roles"]
    assert proc["ticks"] == [1, 12], proc["ticks"]
    assert [ph["phase"] for ph in proc["phases"]] == ["approach", "stay", "leave"]
    assert [ph["ticks"] for ph in proc["phases"]] == [[1, 6], [5, 8], [8, 12]]
    ev = proc["evidence"]
    assert ev["distance_start"] == pytest.approx(6.0)
    assert ev["distance_min"] == pytest.approx(1.8)
    assert ev["approach_drop"] == pytest.approx(4.2)
    assert ev["leave_rise"] == pytest.approx(7.2)
    assert ev["stay_samples"] == 4 and ev["stay_held_ticks"] == 3
    assert proc["samples"] == 12


def test_detour_return_from_real_history():
    history = detour_return_history()
    results = recognize_processes(history)
    assert [p["type"] for p in results] == ["detour_return"], results
    proc = results[0]
    assert proc["roles"] == {"actor": "scout", "ref": "goal"}, proc["roles"]
    assert proc["ticks"] == [1, 8]
    assert [ph["phase"] for ph in proc["phases"]] == ["detour", "return"]
    assert [ph["ticks"] for ph in proc["phases"]] == [[1, 4], [4, 8]]
    ev = proc["evidence"]
    assert ev["distance_start"] == pytest.approx(3.0)
    assert ev["distance_peak"] == pytest.approx(6.708204, abs=1e-6)
    assert ev["rise"] == pytest.approx(3.708204, abs=1e-6)
    assert ev["return_gap"] == pytest.approx(0.0, abs=1e-9)
    assert ev["actor_return"] == pytest.approx(0.0, abs=1e-9)
    # 水平角度证据：actor 绕 ref 扫过 157.38°，高于下限又不足一整圈。
    scout = [s["b"] for s in pair_samples(history, "goal", "scout")]
    assert ev["bearing_sweep_deg"] == pytest.approx(
        bearing_sweep_deg(scout, [0.0, 1.0, 0.0]), abs=1e-6)
    assert ProcessThresholds().detour_sweep_deg <= ev["bearing_sweep_deg"] < 360.0
    assert ev["bearing_sweep_min_deg"] == ProcessThresholds().detour_sweep_deg
    # 绝对回路证据：actor 自身轨迹的回路等效半径 2.23，高于下限 0.5。
    assert ev["loop_radius"] == pytest.approx(loop_radius(scout), abs=1e-6)
    assert ev["loop_radius"] == pytest.approx(2.232006, abs=1e-6)
    assert ev["loop_radius"] >= ProcessThresholds().detour_loop_radius
    assert ev["loop_radius_min"] == ProcessThresholds().detour_loop_radius
    # 穿越证据：参照物静止，相对轨迹离参照物最近 2.12，远高于 crossing_tol 0.05。
    assert ev["ref_min_distance"] == pytest.approx(
        ref_min_distance(scout, [0.0, 1.0, 0.0]), abs=1e-6)
    assert ev["ref_min_distance"] > ProcessThresholds().detour_crossing_tol
    assert ev["crossing_tol"] == ProcessThresholds().detour_crossing_tol
    # 该距离序列的净缩短 ≈0.764 < approach_drop 1.0：接近判据不成立，
    # 因此绕行结果不依赖判据优先级。
    dists = [s["distance"] for s in pair_samples(history, "goal", "scout")]
    assert dists[0] - min(dists) < ProcessThresholds().approach_drop


def test_detour_circle_around_ref_from_real_history():
    """等半径绕 ref 一圈：径向距离全程 3.0，靠方位角扫描 360° 判为绕行。"""
    center = (0.0, 1.0, 0.0)
    circle = circle_points(center, 3.0)
    history = real_history({"beacon": [list(center)] * len(circle), "rider": circle})
    results = recognize_processes(history)
    assert [p["type"] for p in results] == ["detour_return"], results
    proc = results[0]
    assert proc["roles"] == {"actor": "rider", "ref": "beacon"}, proc["roles"]
    assert proc["ticks"] == [1, 13]
    assert [ph["phase"] for ph in proc["phases"]] == ["detour", "return"]
    # 折返点 = 离起点最远的样本：等半径圆上是圆心对面的第 7 个样本。
    assert [ph["ticks"] for ph in proc["phases"]] == [[1, 7], [7, 13]]
    ev = proc["evidence"]
    assert ev["distance_start"] == pytest.approx(3.0)
    assert ev["distance_peak"] == pytest.approx(3.0)
    assert ev["rise"] == pytest.approx(0.0, abs=1e-9)
    assert ev["return_gap"] == pytest.approx(0.0, abs=1e-9)
    assert ev["actor_return"] == pytest.approx(0.0, abs=1e-9)
    assert ev["actor_path"] == pytest.approx(18.634971, abs=1e-6)
    assert ev["bearing_sweep_deg"] == pytest.approx(360.0, abs=1e-6)
    assert ev["bearing_sweep_deg"] == pytest.approx(
        bearing_sweep_deg(circle, list(center)), abs=1e-6)
    assert ev["bearing_sweep_min_deg"] == ProcessThresholds().detour_sweep_deg
    # 等半径绕行的绝对回路等效半径 ≈ 圆半径 × 内接多边形系数 = 2.897777。
    assert ev["loop_radius"] == pytest.approx(loop_radius(circle), abs=1e-6)
    assert ev["loop_radius"] == pytest.approx(2.897777, abs=1e-6)
    assert ev["loop_radius"] >= ProcessThresholds().detour_loop_radius
    # 穿越证据：回路把参照物围在里面，最近距离是内接 12 边形的边心距。
    assert ev["ref_min_distance"] == pytest.approx(
        ref_min_distance(circle, list(center)), abs=1e-6)
    assert ev["ref_min_distance"] > ProcessThresholds().detour_crossing_tol


def test_meet_escort_from_real_history():
    history = meet_escort_history()
    results = recognize_processes(history)
    assert [p["type"] for p in results] == ["meet_escort"], results
    proc = results[0]
    assert proc["roles"] == {"actor": "mate", "ref": "lead"}, proc["roles"]
    assert proc["ticks"] == [1, 8]
    assert [ph["phase"] for ph in proc["phases"]] == ["converge", "escort"]
    assert [ph["ticks"] for ph in proc["phases"]] == [[1, 4], [4, 8]]
    ev = proc["evidence"]
    assert ev["distance_start"] == pytest.approx(8.0)
    assert ev["distance_meet"] == pytest.approx(1.2)
    assert ev["offset_deviation"] == pytest.approx(0.0, abs=1e-9)
    assert ev["escort_ticks"] == 4 and ev["escort_samples"] == 5
    assert ev["actor_move"] == pytest.approx(6.403124, abs=1e-6)
    assert ev["ref_move"] == pytest.approx(6.403124, abs=1e-6)


# ================= B 组：相似反例不冒认 =================

def test_similar_without_phase_not_claimed():
    # 接近后立刻离开：距离 6→1.5 后立刻拉长，最近点附近只有 2 个真实采样。
    no_stay = real_history({
        "lm": [[10, 1, 10]] * 8,
        "w": [[4, 1, 10], [6, 1, 10], [8, 1, 10], [8.5, 1, 10], [7, 1, 10],
              [5, 1, 10], [3, 1, 10], [1, 1, 10]]})
    assert recognize_processes(no_stay) == []

    # 单调接近：没有停留，也没有离开。
    only_approach = real_history({
        "lm": [[10, 1, 10]] * 5,
        "w": [[2, 1, 10], [3, 1, 10], [4, 1, 10], [5, 1, 10], [6, 1, 10]]})
    assert recognize_processes(only_approach) == []

    # 汇合后分开：汇合后距离重新拉长，且相对偏移不再稳定。
    split_after_meet = real_history({
        "p": [[0, 1, 0], [1, 1, 0], [2, 1, 0], [3, 1, 2], [4, 1, 4], [5, 1, 6]],
        "q": [[8, 1, 0], [5, 1, 0], [3.2, 1, 0], [1, 1, 0], [-1, 1, 0], [-3, 1, 0]]})
    assert recognize_processes(split_after_meet) == []

    # 汇合后双方都静止：相对偏移稳定，但双方都没移动。
    stand_together = real_history({
        "p": [[0, 1, 0], [1, 1, 0], [2, 1, 0], [2, 1, 0], [2, 1, 0], [2, 1, 0]],
        "q": [[8, 1, 0], [5, 1, 0], [3.2, 1, 0], [3.2, 1, 0], [3.2, 1, 0], [3.2, 1, 0]]})
    assert recognize_processes(stand_together) == []


def test_straight_round_trip_not_detour():
    """直线外走再原路回：径向抬升、actor 回位、水平路程全满足旧判据，方位角不变。"""
    ref = [0.0, 1.0, 0.0]
    line = [[x, 1.0, 0.0] for x in (3.0, 4.0, 5.0, 6.0, 5.0, 4.0, 3.0)]
    history = real_history({"beacon": [ref] * len(line), "rider": line})
    assert recognize_processes(history) == []
    dists = [s["distance"] for s in pair_samples(history, "beacon", "rider")]
    assert max(dists) - dists[0] == pytest.approx(3.0)
    assert dists[-1] == pytest.approx(dists[0])
    assert line[-1][:1] == line[0][:1]
    assert track_path(line) >= ProcessThresholds().detour_path_min
    # 全程方位角 0°：直线往返没有绕行动作。
    assert bearing_sweep_deg(line, ref) == pytest.approx(0.0, abs=1e-9)


def test_straight_cross_ref_round_trip_not_detour():
    """D1 反例：直线穿过参照物再原路返回。

    旧判据的其余各项全部满足，仅方位角在参照物两侧各跳 180°、累计 360° 就把
    这条直线判成绕行。往返两程经过的回路面积符号相反、互相抵消，actor 自身绝对
    轨迹退化成一个来回的线段、回路等效半径为 0，不是回路。
    """
    history, actor, ref = scene_history(CROSS_LINE, 0.0, (0.0, 0.0))
    assert recognize_processes(history) == []
    dists = [s["distance"] for s in pair_samples(history, "beacon", "rider")]
    assert dists[-1] == pytest.approx(dists[0])
    assert track_path(actor) >= ProcessThresholds().detour_path_min
    assert math.dist(actor[0][::2], actor[-1][::2]) == pytest.approx(0.0)
    # 方位角证据成立：整窗扫描正好 360°，高于下限 90°。
    assert bearing_sweep_deg(actor, ref) == pytest.approx(360.0, abs=1e-6)
    assert bearing_sweep_deg(actor, ref) >= ProcessThresholds().detour_sweep_deg
    # 绝对回路证据不成立：直线往返回路等效半径为 0，低于下限 0.5。
    assert loop_radius(actor) == pytest.approx(0.0, abs=1e-12)
    assert loop_radius(actor) < ProcessThresholds().detour_loop_radius
    # 穿越证据也不成立：轨迹两段都穿过参照物本身。
    assert ref_min_distance(actor, ref) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("yaw,shift", SCENE_VARIANTS)
def test_straight_cross_ref_round_trip_variants_not_detour(yaw, shift):
    """D1 反例的旋转变体与平移变体：整景变换后仍是同一条穿过参照物的直线。"""
    history, actor, ref = scene_history(CROSS_LINE, yaw, shift)
    assert recognize_processes(history) == []
    assert bearing_sweep_deg(actor, ref) == pytest.approx(360.0, abs=1e-6)
    assert loop_radius(actor) < ProcessThresholds().detour_loop_radius


@pytest.mark.parametrize("yaw,shift", SCENE_VARIANTS)
def test_detour_return_variants_keep_detour(yaw, shift):
    """绕行正例的旋转变体与平移变体：同一几何形状的判定与阶段不变。"""
    history, actor, ref = scene_history(ARC_LOOP, yaw, shift)
    results = recognize_processes(history)
    assert [p["type"] for p in results] == ["detour_return"], results
    proc = results[0]
    assert proc["roles"] == {"actor": "rider", "ref": "beacon"}
    assert proc["ticks"] == [1, 8]
    assert [ph["phase"] for ph in proc["phases"]] == ["detour", "return"]
    assert [ph["ticks"] for ph in proc["phases"]] == [[1, 4], [4, 8]]
    ev = proc["evidence"]
    assert ev["loop_radius"] == pytest.approx(loop_radius(actor), abs=1e-6)
    assert ev["loop_radius"] >= ProcessThresholds().detour_loop_radius
    assert ev["loop_radius_min"] == ProcessThresholds().detour_loop_radius
    assert ev["bearing_sweep_deg"] == pytest.approx(
        bearing_sweep_deg(actor, ref), abs=1e-6)
    assert ev["bearing_sweep_deg"] >= ProcessThresholds().detour_sweep_deg
    assert ev["ref_min_distance"] == pytest.approx(
        ref_min_distance(actor, ref), abs=1e-6)
    assert ev["crossing_tol"] == ProcessThresholds().detour_crossing_tol


#: 终审 F1 的误报区间：D = 2.0 是不误报的余量最小点，2.25 起旧判据整族误报。
MOVING_REF_D = (2.0, 2.25, 2.5, 3.0)
#: 同一构造的实测读数 (D, 相对方位角扫描, 相对最近距离, 相对回路等效半径)。
MOVING_REF_READINGS = {2.0: (337.268881, 0.499376, 0.951249),
                       2.25: (334.482043, 0.561612, 1.063497),
                       2.5: (331.713724, 0.623783, 1.174314),
                       3.0: (326.239225, 0.747899, 1.391713)}


@pytest.mark.parametrize("D", MOVING_REF_D)
def test_moving_ref_straight_cross_round_trip_not_detour(D):
    """D1 同族反例（独立终审 F1）：参照物在窗口内横向移动，actor 直线往返。

    构造与终审复现探针 repro_d1_moving_ref_family.py 相同：actor 沿 x 走
    10 → −10 → 10，z 恒定 0，水平转向角恒为 0°；ref 沿 z 从 0 匀速移到 D。
    ref 的位移把**相对**轨迹撑出面积（相对等效半径 0.95 至 1.39，过线），
    方位角扫描也在 326° 以上，旧判据靠单条净空阈值拦，D ≥ 2.25 起整族误报。

    新判据看 actor 自身绝对轨迹：直线去回的往程与回程面积相消，等效半径恒为 0，
    整族不再误报。本例因此证明拦下这一族的是绝对回路判据，不是方位角判据。
    """
    n = 9
    ref = [[0.0, 1.0, D * i / (n - 1)] for i in range(n)]
    actor = [[x, 1.0, 0.0] for x in (10.0, 5.0, 0.0, -5.0, -10.0, -5.0, 0.0, 5.0, 10.0)]
    history = real_history({"beacon": ref, "rider": actor})
    assert recognize_processes(history) == []
    th = ProcessThresholds()
    sweep, near, rel_loop = MOVING_REF_READINGS[D]
    # 拦住这一族的是绝对回路判据：actor 自身轨迹是一条直线去回，读数为 0。
    assert loop_radius(actor) == pytest.approx(0.0, abs=1e-12)
    assert loop_radius(actor) < th.detour_loop_radius
    # 方位角判据在这一族上全部成立，说明拦下的不是方位角判据。
    assert bearing_sweep_deg(actor, ref) == pytest.approx(sweep, abs=1e-6)
    assert bearing_sweep_deg(actor, ref) >= th.detour_sweep_deg
    # 相对形状本来就是个假回路：相对等效半径过线，但 actor 绝对轨迹不是回路。
    assert rel_loop_radius(actor, ref) == pytest.approx(rel_loop, abs=1e-6)
    assert rel_loop_radius(actor, ref) >= th.detour_loop_radius
    # 穿越检测也成立：两者在平面上相距很远（0.499 以上），远高于 crossing_tol。
    assert ref_min_distance(actor, ref) == pytest.approx(near, abs=1e-6)
    assert ref_min_distance(actor, ref) > th.detour_crossing_tol


def test_cross_ref_with_side_loop_not_detour():
    """同根因反例（参照物静止）：直线穿过 ref、到外侧绕一小圈再原路返回。

    绝对回路等效半径 0.844 过线，方位角扫描 523° 也过线，只有穿越检测拦得住：
    往返两段直线都穿过参照物本身，相对轨迹到 ref 的最近距离为 0。
    去掉穿越检测后本例被误判为绕行（定点变异 M5 的靶子）。
    """
    ref = [0.0, 1.0, 0.0]
    actor = ([[3.0, 1.0, 0.0], [-3.0, 1.0, 0.0]]
             + circle_points([-3.0, 1.0, 0.0], 2.0)
             + [[-3.0, 1.0, 0.0], [3.0, 1.0, 0.0]])
    history = real_history({"beacon": [ref] * len(actor), "rider": actor})
    assert recognize_processes(history) == []
    th = ProcessThresholds()
    assert loop_radius(actor) >= th.detour_loop_radius
    assert bearing_sweep_deg(actor, ref) >= th.detour_sweep_deg
    # 穿越检测拦下：相对轨迹穿过参照物，最近距离 0，低于 crossing_tol 0.05。
    assert ref_min_distance(actor, ref) == pytest.approx(0.0, abs=1e-12)
    assert ref_min_distance(actor, ref) < th.detour_crossing_tol


def test_pinched_orbit_around_ref_is_detour():
    """终审 F2 反例二：半径 3 的 12 顶点回路，把一个顶点拉到半径 0.45。

    回路始终包围参照物，只有那一处贴近到 0.45。旧净空判据（最小分离 > 0.5）
    把这条合法绕行误杀；新判据只判穿越，0.45 远高于 crossing_tol 0.05，
    仍报 detour_return。等半径版本见 test_detour_circle_around_ref_from_real_history。
    """
    th = ProcessThresholds()
    center = (0.0, 1.0, 0.0)
    orbit = circle_points(center, 3.0)             # k = 0..12，首尾是同一点
    orbit[0] = [0.45, 1.0, 0.0]                    # 该顶点拉到半径 0.45
    orbit[-1] = [0.45, 1.0, 0.0]                   # 闭合点与该顶点是同一个顶点
    history = real_history({"beacon": [list(center)] * len(orbit), "rider": orbit})
    results = recognize_processes(history)
    assert [p["type"] for p in results] == ["detour_return"], results
    proc = results[0]
    assert proc["roles"] == {"actor": "rider", "ref": "beacon"}, proc["roles"]
    ev = proc["evidence"]
    # 穿越检测不误杀：最近距离 0.45，高于 crossing_tol 0.05。
    assert ev["ref_min_distance"] == pytest.approx(
        ref_min_distance(orbit, list(center)), abs=1e-6)
    assert ev["ref_min_distance"] == pytest.approx(0.45, abs=1e-9)
    assert ev["ref_min_distance"] > th.detour_crossing_tol
    assert ev["crossing_tol"] == th.detour_crossing_tol
    # 绝对回路与方位角两项证据同时成立：回路仍包围参照物，扫过一整圈。
    assert ev["loop_radius"] == pytest.approx(loop_radius(orbit), abs=1e-6)
    assert ev["loop_radius"] == pytest.approx(2.231683, abs=1e-6)
    assert ev["loop_radius"] >= th.detour_loop_radius
    assert ev["bearing_sweep_deg"] == pytest.approx(360.0, abs=1e-6)
    assert ev["bearing_sweep_deg"] >= th.detour_sweep_deg


def test_straight_near_miss_round_trip_not_detour():
    """D1 同族反例（定点变异 M3 的靶子）：在参照物外侧 0.6 处直线往返。

    actor 全程走在 z = 0.6 这条直线上，x 从 +3 到 -3 再回 +3：往返两程的回路
    面积相消，绝对回路等效半径为 0；相对轨迹到参照物的最近距离恒为 0.6，
    高于穿越容差 0.05。方位角与穿越两项证据都成立，只剩绝对回路判据能拦下这条
    直线：去掉回路判据后本例被误判为绕行。
    """
    ref = [0.0, 1.0, 0.0]
    actor = [[x, 1.0, 0.6] for x in (3.0, 2.0, 1.0, -1.0, -2.0, -3.0,
                                     -2.0, -1.0, 1.0, 2.0, 3.0)]
    history = real_history({"beacon": [ref] * len(actor), "rider": actor})
    assert recognize_processes(history) == []
    th = ProcessThresholds()
    # 返回与路程成立：actor 回位、水平路程 12.0 高于下限 3.0。
    assert math.dist(actor[0][::2], actor[-1][::2]) == pytest.approx(0.0)
    assert track_path(actor) >= th.detour_path_min
    # 方位角证据成立：往返两程在参照物上侧各扫一遍，累计 314.76°，高于下限 90°。
    assert bearing_sweep_deg(actor, ref) == pytest.approx(314.76, abs=0.01)
    assert bearing_sweep_deg(actor, ref) >= th.detour_sweep_deg
    # 穿越证据成立：相对轨迹离参照物 0.6，高于穿越容差 detour_crossing_tol 0.05。
    assert ref_min_distance(actor, ref) == pytest.approx(0.6, abs=1e-12)
    assert ref_min_distance(actor, ref) > th.detour_crossing_tol
    # 绝对回路证据不成立：直线往返回路等效半径为 0，低于下限 0.5。
    assert loop_radius(actor) == pytest.approx(0.0, abs=1e-12)
    assert loop_radius(actor) < th.detour_loop_radius


def test_detour_circle_clockwise_around_ref():
    """镜像（顺时针）绕行：面积取绝对值，绕行方向不影响判定。"""
    center = (0.0, 1.0, 0.0)
    circle = [[p[0], p[1], -p[2]] for p in circle_points(center, 3.0)]
    history = real_history({"beacon": [list(center)] * len(circle), "rider": circle})
    results = recognize_processes(history)
    assert [p["type"] for p in results] == ["detour_return"], results
    ev = results[0]["evidence"]
    assert ev["loop_radius"] == pytest.approx(2.897777, abs=1e-6)
    assert ev["bearing_sweep_deg"] == pytest.approx(360.0, abs=1e-6)
    # 内接 12 边形的最近距离是边心距：3 × cos(15°)。
    assert ev["ref_min_distance"] == pytest.approx(
        ref_min_distance(circle, list(center)), abs=1e-6)
    assert ev["ref_min_distance"] == pytest.approx(2.897777, abs=1e-6)
    assert ev["ref_min_distance"] > ProcessThresholds().detour_crossing_tol
    assert ev["crossing_tol"] == ProcessThresholds().detour_crossing_tol


def test_loop_far_from_ref_not_detour():
    """参照物外侧的局部回路：回路形状成立，绕参照物的方位角不足。

    绝对回路等效半径 1.0 高于下限 0.5，方位角只扫过 28.07°、低于下限 90°。
    这项证明方位角判据在回路判据加入后仍然必要：两项几何证据缺一不可。
    """
    ref = [0.0, 1.0, 0.0]
    loop = [[6.0, 1.0, 0.0], [6.0, 1.0, 1.5], [9.0, 1.0, 1.5], [9.0, 1.0, 0.0],
            [6.0, 1.0, 0.0]]
    history = real_history({"beacon": [ref] * len(loop), "rider": loop})
    assert recognize_processes(history) == []
    dists = [s["distance"] for s in pair_samples(history, "beacon", "rider")]
    assert abs(dists[-1] - dists[0]) <= ProcessThresholds().detour_return_tol
    assert track_path(loop) >= ProcessThresholds().detour_path_min
    assert loop_radius(loop) == pytest.approx(1.0, abs=1e-9)
    assert loop_radius(loop) >= ProcessThresholds().detour_loop_radius
    assert bearing_sweep_deg(loop, ref) == pytest.approx(28.072487, abs=1e-6)
    assert bearing_sweep_deg(loop, ref) < ProcessThresholds().detour_sweep_deg


def test_detour_without_return_not_claimed():
    """绕行 3/4 圈后停在别处：方位角证据成立，但没有回到起点，不报绕行。"""
    center = (0.0, 1.0, 0.0)
    arc = circle_points(center, 3.0, first=0, last=9)
    history = real_history({"beacon": [list(center)] * len(arc), "rider": arc})
    assert recognize_processes(history) == []
    assert bearing_sweep_deg(arc, list(center)) >= ProcessThresholds().detour_sweep_deg
    assert math.dist(arc[0][::2], arc[-1][::2]) > ProcessThresholds().detour_return_tol


def test_same_radius_orbit_not_stay():
    """等距绕行与原地停留的距离序列逐点相同：停留还要求 actor 基本不动。"""
    center = (0.0, 1.0, 0.0)
    approach = [[4.0, 1.0, 0.0], [6.0, 1.0, 0.0], [5.0, 1.0, 0.0]]
    leaving = [[-5.0, 1.0, 0.0]]
    orbit = approach + circle_points(center, 3.0, first=1, last=4) + leaving
    stand = approach + [[3.0, 1.0, 0.0]] * 4 + leaving
    h_orbit = real_history({"beacon": [list(center)] * len(orbit), "wanderer": orbit})
    h_stand = real_history({"beacon": [list(center)] * len(stand), "wanderer": stand})

    d_orbit = [s["distance"] for s in pair_samples(h_orbit, "beacon", "wanderer")]
    d_stand = [s["distance"] for s in pair_samples(h_stand, "beacon", "wanderer")]
    assert d_orbit == pytest.approx(d_stand)
    assert d_orbit[3:7] == pytest.approx([3.0] * 4)

    # 同半径运动不冒充停留：不报任何过程。
    assert recognize_processes(h_orbit) == []

    # 同距离序列的原地停留报接近→停留→离开，窗口内 actor 位移为 0。
    stand_procs = recognize_processes(h_stand)
    assert [p["type"] for p in stand_procs] == ["approach_stay_leave"], stand_procs
    ev = stand_procs[0]["evidence"]
    assert ev["stay_samples"] == 4 and ev["stay_held_ticks"] == 3
    assert ev["stay_move"] == ProcessThresholds().stay_move


# ================= C 组：输入不变量 =================

def test_history_not_modified():
    history = approach_stay_leave_history()
    before = json.dumps(history, sort_keys=True)
    recognize_processes(history)
    extract_relative_trajectory(history, "walker", "landmark")
    motion_events(history, "walker")
    track_samples(history, "walker")
    pair_samples(history, "walker", "landmark")
    assert json.dumps(history, sort_keys=True) == before


def test_repeat_same_input_same_output():
    history = meet_escort_history()
    first = recognize_processes(history)
    second = recognize_processes(history)
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    rec = extract_relative_trajectory(history, "mate", "lead", process=first[0])
    assert json.dumps(rec, sort_keys=True) == json.dumps(
        extract_relative_trajectory(history, "mate", "lead", process=second[0]),
        sort_keys=True)


def test_missing_frames_not_interpolated():
    # a 在 t3..t6 无观测：成对采样只有 t1 t2 t7 t8，dt 记真实间隔 5。
    history = [{"tick": 1, "entities": {"a": [0.0, 1.0, 0.0], "b": [5.0, 1.0, 0.0]}},
               {"tick": 2, "entities": {"a": [3.0, 1.0, 0.0], "b": [5.0, 1.0, 0.0]}},
               {"tick": 3, "entities": {"b": [5.0, 1.0, 0.0]}},
               {"tick": 4, "entities": {"b": [5.0, 1.0, 0.0]}},
               {"tick": 5, "entities": {"b": [5.0, 1.0, 0.0]}},
               {"tick": 6, "entities": {"b": [5.0, 1.0, 0.0]}},
               {"tick": 7, "entities": {"a": [3.1, 1.0, 0.0], "b": [5.0, 1.0, 0.0]}},
               {"tick": 8, "entities": {"a": [0.0, 1.0, 0.0], "b": [5.0, 1.0, 0.0]}}]
    samples = pair_samples(history, "a", "b")
    assert [s["tick"] for s in samples] == [1, 2, 7, 8]
    assert [s["dt"] for s in samples] == [0, 1, 5, 1]
    assert [s["distance"] for s in samples] == pytest.approx([5.0, 2.0, 1.9, 5.0])
    assert [s["tick"] for s in track_samples(history, "a")] == [1, 2, 7, 8]
    # 若把缺帧插成新 actual 会得到“停留 5 tick”，真实采样只有 2 个 ⇒ 不报过程。
    assert recognize_processes(history) == []


def test_tick_and_dt_of_generated_frames():
    history = approach_stay_leave_history()
    rec = extract_relative_trajectory(history, "walker", "landmark")
    assert rec["ticks"] == [1, 12]
    assert [s["tick"] for s in rec["samples"]] == list(range(1, 13))
    assert [s["dt"] for s in rec["samples"]] == [0] + [1] * 11
    out = transfer_demonstration(rec, "a2", "r2", tick_offset=100)
    assert [f["tick"] for f in out["frames"]] == list(range(101, 113))
    assert [f["dt"] for f in out["frames"]] == [0] + [1] * 11
    assert out["ticks"] == [101, 112]


def test_motion_events_stop_and_turn():
    history = [{"tick": 1, "entities": {"e": [0.0, 1.0, 0.0]}},
               {"tick": 2, "entities": {"e": [1.0, 1.0, 0.0]}},
               {"tick": 3, "entities": {"e": [1.0, 1.0, 0.0]}},
               {"tick": 4, "entities": {"e": [1.0, 1.0, 1.0]}},
               {"tick": 5, "entities": {"e": [1.0, 1.0, 1.5]}}]
    events = motion_events(history, "e")
    assert [(e["tick"], e["event"]) for e in events] == [(3, "stop"), (4, "turn")]
    assert events[0]["dt"] == 1 and events[0]["moved"] == pytest.approx(0.0)
    assert events[1]["angle_deg"] == pytest.approx(90.0, abs=1e-6)


def test_motion_events_speed_threshold_with_sparse_dt():
    """同样的 0.4 位移：dt=1 是移动，dt=4 是停止。判定用速度而不是位移。"""
    history = [{"tick": 1, "entities": {"e": [0.0, 1.0, 0.0]}},
               {"tick": 2, "entities": {"e": [0.4, 1.0, 0.0]}},
               {"tick": 6, "entities": {"e": [0.8, 1.0, 0.0]}}]
    events = motion_events(history, "e")
    assert [(e["tick"], e["event"]) for e in events] == [(6, "stop")]
    assert events[0]["dt"] == 4
    assert events[0]["moved"] == pytest.approx(0.4)
    assert events[0]["speed"] == pytest.approx(0.1)
    # 位移 0.4 大于 stop_speed 0.2：这次停止只可能来自速度判定。
    assert events[0]["moved"] > ProcessThresholds().stop_speed

    # 同样的 4 tick 间隔里移动 0.9（速度 0.225）不算停止。
    moving = [{"tick": 1, "entities": {"e": [0.0, 1.0, 0.0]}},
              {"tick": 5, "entities": {"e": [0.9, 1.0, 0.0]}}]
    assert motion_events(moving, "e") == []

    # 停止的采样对不更新参考方向：停下再原路反向，与前一段运动方向比是转向。
    back = [{"tick": 1, "entities": {"e": [0.0, 1.0, 0.0]}},
            {"tick": 2, "entities": {"e": [1.0, 1.0, 0.0]}},
            {"tick": 3, "entities": {"e": [1.0, 1.0, 0.0]}},
            {"tick": 4, "entities": {"e": [0.2, 1.0, 0.0]}}]
    back_events = motion_events(back, "e")
    assert [(e["tick"], e["event"]) for e in back_events] == [(3, "stop"), (4, "turn")]
    assert back_events[1]["angle_deg"] == pytest.approx(180.0, abs=1e-6)


# ================= D 组：三条基线 =================

def test_three_baselines_original_shift_transfer():
    history = approach_stay_leave_history()
    procs = recognize_processes(history)
    assert [p["type"] for p in procs] == ["approach_stay_leave"], procs
    proc = procs[0]
    rec = extract_relative_trajectory(history, "walker", "landmark", process=proc)

    # 基线 1 原坐标回放：帧位置等于观测位置。
    original = transfer_demonstration(rec, "walker", "landmark")
    observed = {r["tick"]: r["entities"] for r in history}
    for frame in original["frames"]:
        e = frame["entities"]
        assert e["walker"] == pytest.approx(observed[frame["tick"]]["walker"])
        assert e["landmark"] == pytest.approx(observed[frame["tick"]]["landmark"])
    assert [f["source"] for f in original["frames"]] == ["generated"] * 12

    # 基线 2 仅平移：相对偏移与原坐标一致，绝对位置整体移动。
    shifted = transfer_demonstration(rec, "walker", "landmark", translation=(100.0, 0.0, -50.0))
    for f0, f1 in zip(original["frames"], shifted["frames"]):
        assert _offset(f1, "walker", "landmark") == pytest.approx(
            _offset(f0, "walker", "landmark"), abs=1e-9)
        assert f1["entities"]["walker"][0] == pytest.approx(f0["entities"]["walker"][0] + 100.0)
        assert f1["entities"]["walker"][2] == pytest.approx(f0["entities"]["walker"][2] - 50.0)

    # 基线 3 关系迁移：绑定新角色 + yaw 90° + scale 2，相对关系按同一变换保持。
    yaw = math.pi / 2
    scale = 2.0
    moved = transfer_demonstration(rec, "new_actor", "new_ref",
                                   translation=(-20.0, 3.0, 7.0), yaw=yaw, scale=scale)
    assert moved["actor"] == "new_actor" and moved["ref"] == "new_ref"
    for f0, f2 in zip(original["frames"], moved["frames"]):
        assert f0["tick"] == f2["tick"]
        dx, dy, dz = _offset(f0, "walker", "landmark")
        expect = (scale * dx * math.cos(yaw) + scale * dz * math.sin(yaw),
                  scale * dy,
                  -scale * dx * math.sin(yaw) + scale * dz * math.cos(yaw))
        assert _offset(f2, "new_actor", "new_ref") == pytest.approx(expect, abs=1e-9)
        assert f2["entities"]["new_ref"] == pytest.approx(
            [f0["entities"]["landmark"][0] - 20.0, f0["entities"]["landmark"][1] + 3.0,
             f0["entities"]["landmark"][2] + 7.0])

    # 三条基线的距离序列：原坐标 = 仅平移；关系迁移 = scale 倍。
    def dists(frames, actor, ref):
        return [math.dist(f["entities"][actor][::2], f["entities"][ref][::2])
                for f in frames["frames"]]

    d0 = dists(original, "walker", "landmark")
    d1 = dists(shifted, "walker", "landmark")
    d2 = dists(moved, "new_actor", "new_ref")
    assert d1 == pytest.approx(d0, abs=1e-9)
    assert d2 == pytest.approx([scale * d for d in d0], abs=1e-9)

    # 关系迁移后过程关系不变：同一类型、同一相对阶段，角色换成新 eid。
    after = recognize_processes(_recognition_of_frames(moved["frames"]))
    assert [p["type"] for p in after] == ["approach_stay_leave"], after
    assert after[0]["roles"] == {"actor": "new_actor", "ref": "new_ref"}
    assert [ph["phase"] for ph in after[0]["phases"]] == ["approach", "stay", "leave"]
    assert after[0]["ticks"] == moved["ticks"]
    assert after[0]["evidence"]["distance_min"] == pytest.approx(
        proc["evidence"]["distance_min"] * scale, abs=1e-6)


def test_transfer_moving_ref_is_scene_transform():
    """ref 会移动时 yaw 与 scale 变换 ref 位移与 rel 两者（整体场景变换）。"""
    history = meet_escort_history()
    rec = extract_relative_trajectory(history, "mate", "lead")
    observed = {r["tick"]: r["entities"] for r in history}

    # 默认参数：moving ref 也逐帧回到观测坐标。
    replay = transfer_demonstration(rec, "mate", "lead")
    for frame in replay["frames"]:
        assert frame["entities"]["mate"] == pytest.approx(observed[frame["tick"]]["mate"])
        assert frame["entities"]["lead"] == pytest.approx(observed[frame["tick"]]["lead"])

    # 仅平移：ref 位移与 rel 都不变，绝对位置整体移动。
    shift = (5.0, 2.0, -3.0)
    shifted = transfer_demonstration(rec, "mate", "lead", translation=shift)
    for f0, f1 in zip(replay["frames"], shifted["frames"]):
        assert _offset(f1, "mate", "lead") == pytest.approx(_offset(f0, "mate", "lead"), abs=1e-9)
        for eid in ("mate", "lead"):
            assert f1["entities"][eid] == pytest.approx(
                [f0["entities"][eid][k] + shift[k] for k in range(3)])

    # yaw 90° + scale 2：ref 位移与 rel 走同一条变换。
    yaw, scale = math.pi / 2, 2.0
    moved = transfer_demonstration(rec, "m2", "l2", yaw=yaw, scale=scale)
    lead0 = [float(v) for v in rec["samples"][0]["ref"]]
    for s, frame in zip(rec["samples"], moved["frames"]):
        mx = (float(s["ref"][0]) - lead0[0]) * scale
        my = (float(s["ref"][1]) - lead0[1]) * scale
        mz = (float(s["ref"][2]) - lead0[2]) * scale
        assert frame["entities"]["l2"] == pytest.approx(
            [lead0[0] + mx * math.cos(yaw) + mz * math.sin(yaw),
             lead0[1] + my,
             lead0[2] - mx * math.sin(yaw) + mz * math.cos(yaw)], abs=1e-9)
        assert _offset(frame, "m2", "l2") == pytest.approx(
            (float(s["rel"][0]) * scale * math.cos(yaw) + float(s["rel"][2]) * scale * math.sin(yaw),
             float(s["rel"][1]) * scale,
             -float(s["rel"][0]) * scale * math.sin(yaw) + float(s["rel"][2]) * scale * math.cos(yaw)),
            abs=1e-9)
    # ref 位移确实随 yaw 与 scale 变：只变换 rel 时第 2 帧 ref 会停在 (1, 1, 0)。
    assert moved["frames"][1]["entities"]["l2"] == pytest.approx([0.0, 1.0, -2.0], abs=1e-9)

    # 整体场景变换保持帧内距离 × scale。
    assert [math.dist(f["entities"]["m2"][::2], f["entities"]["l2"][::2])
            for f in moved["frames"]] == pytest.approx(
        [scale * math.dist(f["entities"]["mate"][::2], f["entities"]["lead"][::2])
         for f in replay["frames"]], abs=1e-9)

    # yaw 不改帧内距离：换新角色后仍识别为汇合后同行。
    rotated = transfer_demonstration(rec, "m3", "l3", yaw=yaw)
    after = recognize_processes(_recognition_of_frames(rotated["frames"]))
    assert [p["type"] for p in after] == ["meet_escort"], after
    assert after[0]["roles"] == {"actor": "m3", "ref": "l3"}


def test_transfer_preserves_record_and_y():
    history = approach_stay_leave_history()
    rec = extract_relative_trajectory(history, "walker", "landmark")
    before = json.dumps(rec, sort_keys=True)
    out = transfer_demonstration(rec, "x", "y", translation=(1.0, 2.0, 3.0), yaw=0.4, scale=0.5)
    assert json.dumps(rec, sort_keys=True) == before
    assert out["transform"] == {"translation": [1.0, 2.0, 3.0], "yaw": 0.4,
                                "scale": 0.5, "tick_offset": 0}
    assert [f["source"] for f in out["frames"]] == ["generated"] * 12
    # y 分量按同一变换：原 y 偏移 1 - 1 = 0，帧内两者 y 相同。
    for f in out["frames"]:
        assert f["entities"]["x"][1] == pytest.approx(f["entities"]["y"][1])


def test_transfer_input_constraints():
    rec = extract_relative_trajectory(approach_stay_leave_history(), "walker", "landmark")
    with pytest.raises(ValueError):
        transfer_demonstration({"samples": []}, "a", "b")
    with pytest.raises(ValueError):
        transfer_demonstration(rec, "a", "b", scale=0.0)


def test_record_json_roundtrip():
    history = meet_escort_history()
    procs = recognize_processes(history)
    assert [p["type"] for p in procs] == ["meet_escort"], procs
    rec = extract_relative_trajectory(history, "mate", "lead", process=procs[0])
    loaded = json.loads(json.dumps(rec))
    assert loaded == rec
    out = json.loads(json.dumps(transfer_demonstration(loaded, "m2", "l2",
                                                       translation=(1.0, 0.0, 2.0),
                                                       yaw=0.3, scale=1.5)))
    direct = transfer_demonstration(rec, "m2", "l2", translation=(1.0, 0.0, 2.0),
                                    yaw=0.3, scale=1.5)
    assert out == direct


# ================= E 组：边界与范围 =================

def test_single_entity_and_unknown_entity_have_no_process():
    solo = real_history({"only": [[0, 1, 0], [1, 1, 0], [2, 1, 0], [3, 1, 0], [4, 1, 0]]})
    assert recognize_processes(solo) == []
    history = approach_stay_leave_history()
    assert recognize_processes(history, entities=["walker"]) == []
    both = recognize_processes(history, entities=["walker", "landmark"])
    assert [p["type"] for p in both] == ["approach_stay_leave"], both


def test_module_does_not_perceive_or_execute():
    with open(MODULE_PATH, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"\.perceive\(|\.generate\(|\.execute\(", source)
    assert "scene_simulator" not in source
    assert "import random" not in source


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
