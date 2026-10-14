# -*- coding: utf-8 -*-
"""world_process · 时间会意与示教迁移（世界过程第 1 组）
============================================================================
输入合同：UnifiedWorldModel 的真实观测 history——
    [{"tick": int, "entities": {eid: [x, y, z]}}, ...]
（world_model.py perceive() 第 243-246 行追加的形状）。本模块只读该列表。

时间会意（过程识别）从实体间关系变化识别 3 类过程：
  · approach_stay_leave 接近→停留→离开
  · detour_return       绕行后返回
  · meet_escort         汇合后同行
判据只用位置：一对实体的水平距离序列、各自水平路程、相对偏移、actor 绕 ref 的方位角、
actor 自身绝对轨迹的闭合回路形状（回路等效半径），以及相对轨迹到 ref 的最近距离
（绕行不能把参照物当路点穿过）。
detour_return 由三项几何证据共同判定：actor 绝对轨迹的闭合回路、actor 绕 ref 的方位角
扫描、相对轨迹不穿越参照物。
绝对回路判据不依赖 ref 是否移动：参照物移动只改变相对形状，不改 actor 自身轨迹的回路。
本模块不读 category、不读 behavior_inferred、不读模拟器行为日志。

示教迁移把示教期的相对轨迹存成 JSON 记录，再绑定新 actor/ref，
支持平移、水平旋转（绕 +y 轴）与显式尺度 3 种变换，输出带 tick 的帧轨迹，
每帧标 source="generated"。yaw 与 scale 是整体场景变换：以记录首帧 ref 位置为中心，
ref 位移与 rel 一起缩放、旋转；ref 静止时与只变换 rel 等价。

边界与不变量：
  · 本模块不调用 perceive()/generate()/execute()，不读模拟器，不写 actual。
  · 缺失帧不插值：只对有观测的 tick 计算，dt 取相邻真实采样的 tick 差。
  · y 轴按原值保留：识别只用水平 x/z；迁移对 ref 位移与相对偏移的 y 分量同样缩放与平移。
  · 同一对实体在观察窗口内至多报 1 条过程，判据优先级见 PROCESS_TYPES。
  · 未识别到过程时返回空列表，不猜测。
  · 回路等效半径的周长含末点回到首点的闭合边；12 个既有场景下去掉该边不改变判定。
  · 已声明限制：路径贴近参照物（0.05 以上）经过、而回路实际在远处的对抗构造可能误报。
    穿越容差 0.05 是绝对量，不随参照物尺寸变化。
  · 已声明限制（漏报方向）：参照物在窗口内去回振幅超过 actor 回路半径约 18 倍时，
    合法绕行可能漏报（复审实测翻转点：ref 去回振幅 55.1、actor 回路半径 2.97）。

运行（仓根）：
    python -X utf8 -m pytest tests/test_world_process.py -q
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

PROCESS_APPROACH_STAY_LEAVE = "approach_stay_leave"
PROCESS_DETOUR_RETURN = "detour_return"
PROCESS_MEET_ESCORT = "meet_escort"

# 元组顺序 = 判据优先级：绕行判据最严，先判；同一对实体只报第 1 条命中的过程。
PROCESS_TYPES: Tuple[str, ...] = (PROCESS_DETOUR_RETURN,
                                  PROCESS_APPROACH_STAY_LEAVE,
                                  PROCESS_MEET_ESCORT)

GENERATED = "generated"          # 生成帧的来源标记；观测帧没有该键

PHASE_APPROACH = "approach"
PHASE_STAY = "stay"
PHASE_LEAVE = "leave"
PHASE_DETOUR = "detour"
PHASE_RETURN = "return"
PHASE_CONVERGE = "converge"
PHASE_ESCORT = "escort"


@dataclass(frozen=True)
class ProcessThresholds:
    """过程识别判据的阈值（长度单位 = 世界单位，只量水平面距离）。"""

    min_samples: int = 4          # 一对实体的成对真实采样数下限
    approach_drop: float = 1.0    # 接近阶段：距离相对起点的净缩短下限
    leave_rise: float = 1.0       # 离开阶段：距离相对最近点的净拉长下限
    stay_band: float = 0.5        # 停留阶段：距离偏离最近点的波动上限
    stay_move: float = 0.5        # 停留阶段：actor 相对停留窗口中心样本的水平偏离上限
    hold_min_samples: int = 3     # 持续阶段（停留/同行）的最少真实采样数
    hold_min_ticks: int = 3       # 持续阶段（停留/同行）的最少覆盖 tick 数
    detour_sweep_deg: float = 90.0   # 绕行阶段：actor 绕 ref 的方位角总变化下限（度）
    detour_loop_radius: float = 0.5  # 绕行回路等效半径下限：2 × 回路面积 ÷ 折线周长
    detour_crossing_tol: float = 0.05  # 穿越参照物的近距容差：actor 相对轨迹到 ref 的
                                       # 最近距离须大于该值；真穿越的读数为 0，
                                       # 容差只覆盖浮点与采样表述误差（语义是穿越，
                                       # 不是最小分离）
    detour_return_tol: float = 0.5   # 返回容差：距离与 actor 自身位置
    detour_path_min: float = 3.0  # 绕行的水平路程下限
    meet_gap: float = 1.5         # 汇合判据：距离降到该值以下算汇合
    escort_offset_tol: float = 0.5   # 同行阶段：相对偏移偏离汇合时偏移的上限
    escort_move: float = 0.5      # 同行阶段：双方各自的最小水平位移
    stop_speed: float = 0.2       # 事件提取：水平速度不超过该值记为停止（世界单位每 tick）
    turn_deg: float = 45.0        # 事件提取：方向变化不小于该角度记为转向


# ================= 采样：只取真实观测 =================

def _planar(pos: Sequence[float]) -> Tuple[float, float]:
    """观测位置 → 水平坐标 (x, z)。识别不用 y 轴。"""
    return float(pos[0]), float(pos[2])


def _planar_distance(p: Sequence[float], q: Sequence[float]) -> float:
    """两点的水平距离；也用于相邻采样的水平位移。"""
    return math.hypot(float(p[0]) - float(q[0]), float(p[2]) - float(q[2]))


def _path_length(positions: Sequence[Sequence[float]]) -> float:
    """相邻真实采样之间的水平路程之和。"""
    return sum(_planar_distance(p, q) for p, q in zip(positions, positions[1:]))


def track_samples(history: Sequence[Dict], eid: str) -> List[Dict]:
    """单个实体的真实采样：[{"tick", "dt", "pos"}]。

    history 里没有该 eid 的帧直接跳过：缺帧不补采样、不插值。
    dt = 本采样 tick 与上一真实采样 tick 之差；首个采样记 0。
    pos 是新列表，调用方的 history 不被改写。
    """
    out: List[Dict] = []
    prev_tick: Optional[int] = None
    for rec in history:
        pos = rec["entities"].get(eid)
        if pos is None:
            continue
        tick = int(rec["tick"])
        out.append({"tick": tick,
                    "dt": 0 if prev_tick is None else tick - prev_tick,
                    "pos": [float(v) for v in pos]})
        prev_tick = tick
    return out


def pair_samples(history: Sequence[Dict], a: str, b: str) -> List[Dict]:
    """一对实体的成对真实采样：[{"tick", "dt", "distance", "a", "b"}]。

    只有 a 与 b 在同一帧都有观测才记一条；dt 取相邻成对采样的 tick 差。
    """
    out: List[Dict] = []
    prev_tick: Optional[int] = None
    for rec in history:
        entities = rec["entities"]
        pa, pb = entities.get(a), entities.get(b)
        if pa is None or pb is None:
            continue
        tick = int(rec["tick"])
        out.append({"tick": tick,
                    "dt": 0 if prev_tick is None else tick - prev_tick,
                    "distance": _planar_distance(pa, pb),
                    "a": [float(v) for v in pa],
                    "b": [float(v) for v in pb]})
        prev_tick = tick
    return out


def motion_events(history: Sequence[Dict], eid: str, *,
                  thresholds: Optional[ProcessThresholds] = None) -> List[Dict]:
    """单个实体的最小停止/转向事件，复用 track_samples 的采样与 dt。

    停止：相邻两个真实采样的水平速度（水平位移 ÷ dt）≤ stop_speed。
    转向：速度大于 stop_speed 的采样对，与前一个运动方向的变化 ≥ turn_deg。
    停止的采样对不更新参考方向；恢复运动后与停止前的运动方向比较。
    事件挂在后一个采样的 tick 上；缺帧处没有采样，因此不产生事件。
    速度按真实 dt 归一化：4 tick 间隔里漂移 0.4 与逐 tick 移动 0.4 不等价。
    """
    th = thresholds or ProcessThresholds()
    samples = track_samples(history, eid)
    events: List[Dict] = []
    last_dir: Optional[Tuple[float, float]] = None
    for prev, cur in zip(samples, samples[1:]):
        dx = cur["pos"][0] - prev["pos"][0]
        dz = cur["pos"][2] - prev["pos"][2]
        moved = math.hypot(dx, dz)
        speed = moved / cur["dt"]
        if speed <= th.stop_speed:
            events.append({"tick": cur["tick"], "dt": cur["dt"], "event": "stop",
                           "moved": round(moved, 6), "speed": round(speed, 6)})
            continue
        direction = (dx / moved, dz / moved)
        if last_dir is not None:
            dot = max(-1.0, min(1.0, direction[0] * last_dir[0] + direction[1] * last_dir[1]))
            angle = math.degrees(math.acos(dot))
            if angle >= th.turn_deg:
                events.append({"tick": cur["tick"], "dt": cur["dt"],
                               "event": "turn", "angle_deg": round(angle, 3)})
        last_dir = direction
    return events


# ================= 时间会意：3 类过程 =================

def _hold_window(dists: Sequence[float], positions: Sequence[Sequence[float]], center: int,
                band: float, move: float) -> Tuple[int, int]:
    """包含 center 的最长连续样本区间：距离不超过 dists[center]+band，且 actor
    相对 center 样本的水平偏离不超过 move。停留是原地停留，因此等距绕行
    （距离不变、位置在动）不构成停留窗口。"""
    ref = dists[center]
    anchor = positions[center]
    n = len(dists)

    def holds(i: int) -> bool:
        return (dists[i] <= ref + band
                and _planar_distance(positions[i], anchor) <= move)

    lo = center
    while lo > 0 and holds(lo - 1):
        lo -= 1
    hi = center
    while hi < n - 1 and holds(hi + 1):
        hi += 1
    return lo, hi


def _offset(sample: Dict) -> Tuple[float, float]:
    """actor 相对 ref 的水平偏移。"""
    return (sample["a"][0] - sample["b"][0], sample["a"][2] - sample["b"][2])


def _bearing_sweep_deg(samples: Sequence[Dict]) -> float:
    """actor 绕 ref 的水平方位角总绝对变化量（度）。

    方位角 = atan2(actor.z − ref.z, actor.x − ref.x)。相邻采样的角差归一化到
    (-180°, 180°] 后取绝对值累加：等半径绕一圈约 360°，直线往返约 0°。
    actor 与 ref 重合时方位角无定义，该样本不参与累加。
    """
    total = 0.0
    prev: Optional[float] = None
    for s in samples:
        dx = s["a"][0] - s["b"][0]
        dz = s["a"][2] - s["b"][2]
        if dx == 0.0 and dz == 0.0:
            continue
        cur = math.atan2(dz, dx)
        if prev is not None:
            total += abs(math.atan2(math.sin(cur - prev), math.cos(cur - prev)))
        prev = cur
    return math.degrees(total)


def _loop_radius(points: Sequence[Sequence[float]]) -> float:
    """actor 自身绝对水平轨迹的闭合回路等效半径 = 2 × 回路面积 ÷ 折线周长。

    输入是 actor 自己的水平采样点 (x, z)，不是相对 ref 的偏移。
    面积取 shoelace 有向面积，周长含末点回到首点的闭合边。沿同一条直线往返时，
    往程与回程经过的三角形符号相反、面积相消，等效半径恒为 0；
    绕开参照物走一圈时回路面积为正，等效半径约等于回路的平均半径。
    参照物在窗口内移动不会改 actor 自己的轨迹：绝对回路判据不依赖 ref 是否移动。
    传入相对轨迹（actor − ref）时得到的是相对形状的等效半径，不是本判据用的量。
    """
    area2 = 0.0
    length = 0.0
    n = len(points)
    for i in range(n):
        x1, z1 = points[i]
        x2, z2 = points[(i + 1) % n]
        area2 += x1 * z2 - x2 * z1
        length += math.hypot(x2 - x1, z2 - z1)
    return abs(area2) / length if length > 0.0 else 0.0


def _ref_clearance(rel: Sequence[Sequence[float]]) -> float:
    """相对轨迹到参照物（相对坐标原点）的最小水平距离（世界单位）。

    逐条采样线段取到原点的最近距离，即 evidence 里的 ref_min_distance。
    该读数只作穿越判定：直线穿过参照物的轨迹读数为 0；
    绕行把参照物保持在回路之外，读数大于 0，但不要求最小分离达到某个距离。
    """
    best = math.inf
    for p, q in zip(rel, rel[1:]):
        dx, dz = q[0] - p[0], q[1] - p[1]
        seg2 = dx * dx + dz * dz
        if seg2 == 0.0:
            dist = math.hypot(p[0], p[1])
        else:
            t = max(0.0, min(1.0, -(p[0] * dx + p[1] * dz) / seg2))
            dist = math.hypot(p[0] + t * dx, p[1] + t * dz)
        if dist < best:
            best = dist
    return best


def _result(ptype: str, actor: str, ref: str, ticks: List[int],
            phases: List[Dict], evidence: Dict, n_samples: int) -> Dict:
    """过程结果：类型 / 角色 / 时间范围 / 阶段 / 数值依据。"""
    return {"type": ptype,
            "roles": {"actor": actor, "ref": ref},
            "ticks": [ticks[0], ticks[-1]],
            "phases": phases,
            "evidence": evidence,
            "samples": n_samples}


def _recognize_pair(a: str, b: str, samples: Sequence[Dict],
                    th: ProcessThresholds) -> Optional[Dict]:
    """判定一对实体的过程；a < b 由调用方保证，平手时取 a 作 actor。"""
    ticks = [s["tick"] for s in samples]
    dists = [s["distance"] for s in samples]
    a_pos = [s["a"] for s in samples]
    b_pos = [s["b"] for s in samples]
    n = len(samples)
    start_d, end_d = dists[0], dists[-1]

    # 角色：actor = 窗口内水平路程更长的实体；平手时取 a，保证确定性。
    path_a, path_b = _path_length(a_pos), _path_length(b_pos)
    actor, ref = (a, b) if path_a >= path_b else (b, a)
    actor_pos = a_pos if actor == a else b_pos
    ref_pos = b_pos if actor == a else a_pos
    actor_path = path_a if actor == a else path_b

    # ① 绕行后返回：actor 绕开 ref 后回到起点。三项几何证据：
    #    ① actor 自身绝对轨迹构成闭合回路（回路等效半径 ≥ detour_loop_radius）；
    #    ② actor 绕 ref 的方位角总变化 ≥ detour_sweep_deg（水平角度证据）；
    #    ③ actor 相对 ref 的轨迹不穿越参照物（到 ref 的最近距离 > detour_crossing_tol）。
    #    另有返回与路程：actor 回到起点 ±tol；距离回到起点距离 ±tol；路程 ≥ path_min。
    #    径向抬升不是判据：等半径绕行的抬升为 0，直线往返的抬升、actor 回位、
    #    水平路程都与绕行相同。直线穿过参照物再原路返回时，方位角在参照物两侧
    #    各跳 180°、累计 360°，但绝对轨迹是直线、往程与回程的面积相消、等效半径为 0。
    #    参照物移动时其自身位移会把相对轨迹撑出面积（相对等效半径可达 1.0）；
    #    绝对回路判据不看相对形状，因此不依赖 ref 是否移动。
    #    三项证据一起区分“绕开参照物的回路”与“把参照物当路点穿过的直线”。
    apex = max(range(n), key=lambda i: _planar_distance(actor_pos[0], actor_pos[i]))
    sweep_deg = _bearing_sweep_deg(samples)
    rel = [(p[0] - q[0], p[2] - q[2]) for p, q in zip(actor_pos, ref_pos)]
    loop_radius = _loop_radius([_planar(p) for p in actor_pos])
    ref_min_distance = _ref_clearance(rel)
    actor_return = _planar_distance(actor_pos[0], actor_pos[-1])
    dmax = max(dists)
    if (abs(end_d - start_d) <= th.detour_return_tol
            and actor_path >= th.detour_path_min
            and sweep_deg >= th.detour_sweep_deg
            and loop_radius >= th.detour_loop_radius
            and ref_min_distance > th.detour_crossing_tol
            and 0 < apex < n - 1
            and actor_return <= th.detour_return_tol):
        evidence = {"distance_start": round(start_d, 6),
                    "distance_peak": round(dmax, 6),
                    "distance_end": round(end_d, 6),
                    "rise": round(dmax - start_d, 6),
                    "return_gap": round(abs(end_d - start_d), 6),
                    "return_tol": th.detour_return_tol,
                    "actor_path": round(actor_path, 6),
                    "actor_path_min": th.detour_path_min,
                    "actor_return": round(actor_return, 6),
                    "loop_radius": round(loop_radius, 6),
                    "loop_radius_min": th.detour_loop_radius,
                    "ref_min_distance": round(ref_min_distance, 6),
                    "crossing_tol": th.detour_crossing_tol,
                    "bearing_sweep_deg": round(sweep_deg, 6),
                    "bearing_sweep_min_deg": th.detour_sweep_deg}
        phases = [{"phase": PHASE_DETOUR, "ticks": [ticks[0], ticks[apex]]},
                  {"phase": PHASE_RETURN, "ticks": [ticks[apex], ticks[-1]]}]
        return _result(PROCESS_DETOUR_RETURN, actor, ref, ticks, phases, evidence, n)

    # ② 接近→停留→离开：距离先净缩短 ≥ approach_drop，在最近点附近保持 band
    #    内、且 actor 相对最近点偏离 ≤ stay_move 的窗口里，有 hold_min_samples
    #    个真实采样且覆盖 hold_min_ticks 个 tick，再相对最近点净拉长 ≥ leave_rise。
    imin = min(range(n), key=lambda i: dists[i])
    lo, hi = _hold_window(dists, actor_pos, imin, th.stay_band, th.stay_move)
    drop = start_d - dists[imin]
    rise = dists[-1] - dists[imin]
    held_samples, held_ticks = hi - lo + 1, ticks[hi] - ticks[lo]
    if (drop >= th.approach_drop and rise >= th.leave_rise
            and held_samples >= th.hold_min_samples and held_ticks >= th.hold_min_ticks):
        evidence = {"distance_start": round(start_d, 6),
                    "distance_min": round(dists[imin], 6),
                    "distance_end": round(end_d, 6),
                    "approach_drop": round(drop, 6),
                    "leave_rise": round(rise, 6),
                    "stay_band": th.stay_band,
                    "stay_move": th.stay_move,
                    "stay_samples": held_samples,
                    "stay_held_ticks": held_ticks,
                    "actor_path": round(actor_path, 6)}
        phases = [{"phase": PHASE_APPROACH, "ticks": [ticks[0], ticks[imin]]},
                  {"phase": PHASE_STAY, "ticks": [ticks[lo], ticks[hi]]},
                  {"phase": PHASE_LEAVE, "ticks": [ticks[hi], ticks[-1]]}]
        return _result(PROCESS_APPROACH_STAY_LEAVE, actor, ref, ticks, phases, evidence, n)

    # ③ 汇合后同行：距离从 meet_gap 以上降到 meet_gap 以下（汇合），
    #    其后相对偏移偏离汇合时偏移不超过 escort_offset_tol，且双方都在移动。
    imeet = next((i for i in range(n) if dists[i] <= th.meet_gap), None)
    if imeet is not None and imeet > 0:
        base = _offset(samples[imeet])
        dev = max(math.hypot(ox - base[0], oz - base[1])
                  for ox, oz in (_offset(s) for s in samples[imeet:]))
        # 角色：actor = 汇合阶段位移更大的一方。
        conv_a = _planar_distance(a_pos[0], a_pos[imeet])
        conv_b = _planar_distance(b_pos[0], b_pos[imeet])
        actor, ref = (a, b) if conv_a >= conv_b else (b, a)
        actor_pos = a_pos if actor == a else b_pos
        ref_pos = b_pos if actor == a else a_pos
        move_actor = _planar_distance(actor_pos[imeet], actor_pos[-1])
        move_ref = _planar_distance(ref_pos[imeet], ref_pos[-1])
        esc_samples = n - imeet
        esc_ticks = ticks[-1] - ticks[imeet]
        if (dev <= th.escort_offset_tol and move_actor >= th.escort_move
                and move_ref >= th.escort_move
                and esc_samples >= th.hold_min_samples and esc_ticks >= th.hold_min_ticks):
            evidence = {"distance_start": round(start_d, 6),
                        "distance_meet": round(dists[imeet], 6),
                        "meet_gap": th.meet_gap,
                        "offset_deviation": round(dev, 6),
                        "offset_tol": th.escort_offset_tol,
                        "escort_samples": esc_samples,
                        "escort_ticks": esc_ticks,
                        "actor_move": round(move_actor, 6),
                        "ref_move": round(move_ref, 6)}
            phases = [{"phase": PHASE_CONVERGE, "ticks": [ticks[0], ticks[imeet]]},
                      {"phase": PHASE_ESCORT, "ticks": [ticks[imeet], ticks[-1]]}]
            return _result(PROCESS_MEET_ESCORT, actor, ref, ticks, phases, evidence, n)

    return None


def recognize_processes(history: Sequence[Dict], *,
                        thresholds: Optional[ProcessThresholds] = None,
                        entities: Optional[Sequence[str]] = None) -> List[Dict]:
    """识别观察窗口内的过程，返回过程结果列表（无过程时为空列表）。

    history 按 tick 递增（perceive 保证）；本函数不改写 history。
    entities 限定参与判定的实体；省略时取 history 中出现过的全部实体。
    结果按 (起始 tick, 类型, actor, ref) 排序，同一输入重复调用结果相同。
    """
    th = thresholds or ProcessThresholds()
    if entities is None:
        seen = set()
        for rec in history:
            seen.update(rec["entities"])
        eids = sorted(seen)
    else:
        eids = sorted(set(str(e) for e in entities))
    out: List[Dict] = []
    for index, a in enumerate(eids):
        for b in eids[index + 1:]:
            samples = pair_samples(history, a, b)
            if len(samples) < th.min_samples:
                continue
            proc = _recognize_pair(a, b, samples, th)
            if proc is not None:
                out.append(proc)
    out.sort(key=lambda p: (p["ticks"][0], p["type"], p["roles"]["actor"],
                            p["roles"]["ref"]))
    return out


# ================= 示教迁移 =================

def extract_relative_trajectory(history: Sequence[Dict], actor: str, ref: str, *,
                                process: Optional[Dict] = None) -> Dict:
    """示教期的相对轨迹记录（JSON 可存）。

    rel = actor 位置 − ref 位置（含 y），tick 与 dt 保留原采样值，不重采样。
    process 参数保存示教原意（recognize_processes 的某条结果，可为 None）。
    """
    samples = pair_samples(history, actor, ref)
    return {"kind": "relative_trajectory",
            "actor": actor,
            "ref": ref,
            "process": process,
            "ticks": [samples[0]["tick"], samples[-1]["tick"]] if samples else [],
            "samples": [{"tick": s["tick"], "dt": s["dt"],
                         "ref": list(s["b"]),
                         "rel": [s["a"][k] - s["b"][k] for k in range(3)]}
                        for s in samples]}


def transfer_demonstration(record: Dict, actor: str, ref: str, *,
                           translation: Sequence[float] = (0.0, 0.0, 0.0),
                           yaw: float = 0.0,
                           scale: float = 1.0,
                           tick_offset: int = 0) -> Dict:
    """把相对轨迹记录绑定到新 actor/ref，输出带 tick 的生成帧轨迹。

    yaw 与 scale 是整体场景变换，锚点是记录首帧的 ref 位置 ref0：
        ref 帧位置 = ref0 + translation + 旋转(scale × (记录 ref 位置 − ref0))
        执行者帧位置 = ref 帧位置 + 旋转(scale × rel)
    变换顺序：刻度 scale → 绕 +y 轴右手旋转 yaw → 平移 translation。
    ref 静止时 ref 位移为 0，此时只有 rel 随 yaw 与 scale 变化。
    角色变换只改帧里的 eid：记录本身不改写。
    translation 取 (0,0,0)、yaw 取 0、scale 取 1 时即原坐标回放。
    scale 必须为正数；record 必须含至少 1 条采样；两条约束在此处只查一次。
    """
    samples = record.get("samples") or []
    if not samples:
        raise ValueError("相对轨迹记录没有采样，无法迁移")
    if scale <= 0.0:
        raise ValueError("尺度必须为正数")
    tx, ty, tz = (float(v) for v in translation)
    cos_y, sin_y = math.cos(float(yaw)), math.sin(float(yaw))
    ox0, oy0, oz0 = (float(v) for v in samples[0]["ref"])
    frames: List[Dict] = []
    for s in samples:
        rx, ry, rz = (float(v) for v in s["ref"])
        # ref 位移与 rel 走同一条变换：先刻度 scale，再绕 +y 轴旋转 yaw。
        mx, my, mz = (rx - ox0) * scale, (ry - oy0) * scale, (rz - oz0) * scale
        ref_pos = [ox0 + tx + mx * cos_y + mz * sin_y,
                   oy0 + ty + my,
                   oz0 + tz - mx * sin_y + mz * cos_y]
        dx, dy, dz = (float(v) * scale for v in s["rel"])
        frames.append({"tick": int(s["tick"]) + int(tick_offset),
                       "dt": int(s["dt"]),
                       "entities": {ref: ref_pos,
                                    actor: [ref_pos[0] + dx * cos_y + dz * sin_y,
                                            ref_pos[1] + dy,
                                            ref_pos[2] - dx * sin_y + dz * cos_y]},
                       "source": GENERATED})
    return {"kind": "demonstration_transfer",
            "source": GENERATED,
            "actor": actor,
            "ref": ref,
            "transform": {"translation": [tx, ty, tz],
                          "yaw": float(yaw),
                          "scale": float(scale),
                          "tick_offset": int(tick_offset)},
            "ticks": [frames[0]["tick"], frames[-1]["tick"]],
            "frames": frames}
