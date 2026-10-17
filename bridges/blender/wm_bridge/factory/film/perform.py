# -*- coding: utf-8 -*-
"""wm_bridge.factory.film.perform · 表演契约 wmfilm.perform/1（纯标准库 · 零 bpy）

★ 这一份契约要回答什么
    anim 产线能产出「关节怎么随时间转」（wmfactory.rig/1 + 关节声明），
    但它回答不了「这段动作**是不是一个有预备、有发力、有余势的表演**」——
    后者正是 §4.2 P 族判据（P1 三段结构 / P2 重叠动作 / P3 动作线连续）要判的东西。
    wmfilm.perform/1 把表演**语义**写成数据，作为对 rig 关节的**覆写层**。

★ 为什么是「覆写」而不是另立一套关节
    关节的**物理定义**（轴 / 单位 / 插值）已由 wmfactory.spec/1 与 rig 定死。
    表演层只加「时序语义」（哪段是预备、末端晚到几帧），**不重定义关节本身**——
    否则同一个关节会有两套定义，正是 geo/spec.py 头部记的旧病根。

★ 三段结构（§4.2 P1 的契约面）
    anticipation（预备）→ action（发力）→ follow_through（余势）。
    三段必须**首尾衔接、恰好覆盖** frame_base：不留缝、不重叠、不越界。
    契约层只保证「结构完整」；各段**占比**是否合 P1 的
    15-30% / 40-60% / 15-35%，由判据门（_det/）判 —— **契约不替判据下结论**。

★ 本模块只立**契约与校验器**（§七 P0），不含动画实现。
"""
from __future__ import annotations

from . import _common as C

#: 契约标识
PERFORM_SCHEMA = "wmfilm.perform/1"

#: 三段的**顺序**即语义（不可乱序、不可改名）
PHASES = ("anticipation", "action", "follow_through")

#: 迪士尼动画十二原理（闭集；§附「理论来源」已录）
#: 为什么用闭集而不是自由字符串：自由字符串会让「同一个原理两种写法」（arc / arcs）
#: 慢慢分裂成两派，而判据门按名取用时只能命中其中一派 —— 静默漏判。
PRINCIPLES = (
    "squash_stretch", "anticipation", "staging", "straight_ahead_pose_to_pose",
    "follow_through_overlapping", "slow_in_slow_out", "arc", "secondary_action",
    "timing", "exaggeration", "solid_drawing", "appeal",
)

#: 覆写值的单位（闭集）
OVERRIDE_UNITS = ("deg", "m", "ratio")

__all__ = [
    "PERFORM_SCHEMA", "PHASES", "PRINCIPLES", "OVERRIDE_UNITS",
    "PerformError", "normalize_perform", "validate_perform", "perform_digest",
    "perform_summary", "phase_frames", "phase_ratios", "overrides_by_joint",
    "joint_kinds", "joint_easings", "axes",
]

_KEYS_PERFORM = ("schema", "id", "spec_ref", "frame_base", "phases",
                 "overlap", "joint_overrides", "principle_refs", "notes")
_KEYS_SPEC_REF = ("name", "digest")
_KEYS_FRAME_BASE = ("fps", "frame_start", "frame_end")
_KEYS_PHASE = ("start", "end")
_KEYS_OVERLAP = ("joint", "delay_frames")
_KEYS_OVERRIDE = ("joint", "kind", "axis", "keys", "unit", "easing")

#: 关节物理定义的**唯一权威**仍是 geo/spec.py —— 这里只做延迟导入的缓存，
#: 不复制常量（复制即第二套定义，会漂移）。
_CONST_CACHE = {}


def _spec_consts():
    if "v" not in _CONST_CACHE:
        from wm_bridge.factory.geo import spec as S
        _CONST_CACHE["v"] = (tuple(S.JOINT_KINDS), tuple(S.JOINT_EASINGS),
                             tuple(S.AXES))
    return _CONST_CACHE["v"]


def joint_kinds():
    """关节类型闭集（权威 = geo/spec.py 的 JOINT_KINDS）。"""
    return _spec_consts()[0]


def joint_easings():
    """关节插值闭集（权威 = geo/spec.py 的 JOINT_EASINGS）。"""
    return _spec_consts()[1]


def axes():
    """轴向闭集（权威 = geo/spec.py 的 AXES）。"""
    return _spec_consts()[2]


class PerformError(C.ContractError):
    """表演契约不合法（fail-closed：不猜、不补、不静默降级）。"""


# ─────────────────────────────────────────────────────────── 局部助手
def _is_hex(s):
    return bool(s) and all(ch in "0123456789abcdefABCDEF" for ch in s)


def _digest_str(d, where):
    v = C.req_str(d, "digest", where)
    if _is_hex(v) and len(v) != 64:
        raise PerformError(
            "%s.digest 形如十六进制但非 sha256（须 64 位），实为 %r" % (where, v))
    return v


def _norm_frame_base(fb, where="perform.frame_base"):
    C.reject_unknown(fb, _KEYS_FRAME_BASE, where)
    fps = C.positive(C.req_num(fb, "fps", where), where + ".fps")
    fs = C.req_int(fb, "frame_start", where)
    fe = C.req_int(fb, "frame_end", where)
    if fe <= fs:
        raise PerformError(
            "%s 的 frame_end(%d) 须晚于 frame_start(%d)（表演须有跨度）" % (where, fe, fs))
    return {"fps": fps, "frame_start": fs, "frame_end": fe}


# ─────────────────────────────────────────────────────────── 规范化（执行面）
def normalize_perform(perf):
    """校验并补全默认值；返回**新的**规范化 perform（不修改入参）。

    补全的默认值：
      overlap            -> []
      joint_overrides    -> []
      principle_refs     -> []
      override.unit      -> "deg"
      override.easing    -> "linear"
      override.axis      -> "x"

    生效条件：合法 ⇒ 返回新 dict；不合法 ⇒ raise PerformError（消息含字段路径）。
    """
    if not isinstance(perf, dict):
        raise PerformError("perform 顶层须为对象，实为 %s" % type(perf).__name__)
    C.check_schema(perf, PERFORM_SCHEMA, "perform")
    C.reject_unknown(perf, _KEYS_PERFORM, "perform 顶层")

    out = {"schema": PERFORM_SCHEMA}
    out["id"] = C.req_str(perf, "id", "perform")

    sr = C.req_map(perf, "spec_ref", "perform")
    C.reject_unknown(sr, _KEYS_SPEC_REF, "perform.spec_ref")
    out["spec_ref"] = {"name": C.req_str(sr, "name", "perform.spec_ref"),
                       "digest": _digest_str(sr, "perform.spec_ref")}

    fb = _norm_frame_base(C.req_map(perf, "frame_base", "perform"))
    out["frame_base"] = fb

    out["phases"] = _norm_phases(C.req_map(perf, "phases", "perform"), fb)

    ov = []
    seen_joints = set()
    for i, o in enumerate(C.opt_list(perf, "overlap", "perform")):
        w = "perform.overlap[%d]" % i
        if not isinstance(o, dict):
            raise PerformError("%s 须为对象，实为 %s" % (w, type(o).__name__))
        C.reject_unknown(o, _KEYS_OVERLAP, w)
        j = C.req_str(o, "joint", w)
        if j in seen_joints:
            raise PerformError(
                "%s.joint 重复声明 %r —— 同一关节的到达延迟只能有一个定义" % (w, j))
        seen_joints.add(j)
        df = C.req_int(o, "delay_frames", w)
        if df < 1:
            raise PerformError("%s.delay_frames 须 >= 1（0 即无重叠），实为 %r" % (w, df))
        ov.append({"joint": j, "delay_frames": df})
    out["overlap"] = ov

    jos = []
    seen_ov = set()
    for i, o in enumerate(C.opt_list(perf, "joint_overrides", "perform")):
        e = _norm_override(o, "perform.joint_overrides[%d]" % i)
        if e["joint"] in seen_ov:
            raise PerformError(
                "perform.joint_overrides 重复覆写关节 %r —— 该关节时序将无定义" % e["joint"])
        seen_ov.add(e["joint"])
        jos.append(e)
    out["joint_overrides"] = jos

    prs = []
    for i, p in enumerate(C.opt_list(perf, "principle_refs", "perform")):
        if not isinstance(p, str):
            raise PerformError(
                "perform.principle_refs[%d] 须为字符串，实为 %s" % (i, type(p).__name__))
        prs.append(C.enum(p, PRINCIPLES, "perform.principle_refs[%d]" % i))
    out["principle_refs"] = prs

    if perf.get("notes") is not None:
        out["notes"] = C.req_str(perf, "notes", "perform")
    return out


def _norm_phases(ph_in, fb):
    """三段结构：首尾衔接、恰好覆盖 frame_base（§4.2 P1 的契约面）。"""
    C.reject_unknown(ph_in, PHASES, "perform.phases")
    seg = {}
    for name in PHASES:
        w = "perform.phases.%s" % name
        d = C.req_map(ph_in, name, "perform.phases")
        C.reject_unknown(d, _KEYS_PHASE, w)
        s = C.req_int(d, "start", w)
        e = C.req_int(d, "end", w)
        if e <= s:
            raise PerformError("%s 的 end(%d) 须晚于 start(%d)" % (w, e, s))
        seg[name] = {"start": s, "end": e}

    if seg["anticipation"]["start"] != fb["frame_start"]:
        raise PerformError(
            "perform.phases.anticipation.start(%d) 须等于 frame_start(%d) —— "
            "三段必须恰好覆盖镜头，不留缝"
            % (seg["anticipation"]["start"], fb["frame_start"]))
    if seg["follow_through"]["end"] != fb["frame_end"]:
        raise PerformError(
            "perform.phases.follow_through.end(%d) 须等于 frame_end(%d) —— "
            "三段必须恰好覆盖镜头，不留缝"
            % (seg["follow_through"]["end"], fb["frame_end"]))
    if seg["anticipation"]["end"] != seg["action"]["start"]:
        raise PerformError(
            "预备段 end(%d) 与发力段 start(%d) 不衔接 —— 三段之间不得留缝或重叠"
            % (seg["anticipation"]["end"], seg["action"]["start"]))
    if seg["action"]["end"] != seg["follow_through"]["start"]:
        raise PerformError(
            "发力段 end(%d) 与余势段 start(%d) 不衔接 —— 三段之间不得留缝或重叠"
            % (seg["action"]["end"], seg["follow_through"]["start"]))
    return {n: seg[n] for n in PHASES}


def _norm_override(o, w):
    if not isinstance(o, dict):
        raise PerformError("%s 须为对象，实为 %s" % (w, type(o).__name__))
    C.reject_unknown(o, _KEYS_OVERRIDE, w)

    j = C.req_str(o, "joint", w)
    kind = C.enum(C.opt_str(o, "kind", "ease", w), joint_kinds(), w + ".kind")
    axis = C.enum(C.opt_str(o, "axis", "x", w), axes(), w + ".axis")

    keys_in = C.req_list(o, "keys", w)
    if len(keys_in) < 2:
        raise PerformError(
            "%s.keys 至少 2 个关键帧（单点无法构成时序），实为 %d 个" % (w, len(keys_in)))
    keys, prev = [], None
    for i, kv in enumerate(keys_in):
        wk = "%s.keys[%d]" % (w, i)
        if not isinstance(kv, (list, tuple)) or len(kv) != 2:
            raise PerformError("%s 须为 [t, value] 二元组，实为 %r" % (wk, kv))
        t, val = kv[0], kv[1]
        if not C.is_num(t):
            raise PerformError("%s[0] 须为有限数值，实为 %r" % (wk, t))
        if not C.is_num(val):
            raise PerformError("%s[1] 须为有限数值，实为 %r" % (wk, val))
        t = float(t)
        if not (0.0 <= t <= 1.0):
            raise PerformError("%s[0] 须落在 [0, 1]（段内归一化进度），实为 %r" % (wk, t))
        if prev is not None and not (t > prev):
            raise PerformError(
                "%s[0] 须严格递增（时序不可回头），实为 %r 不大于前一点 %r"
                % (wk, t, prev))
        prev = t
        keys.append([t, float(val)])

    return {"joint": j, "kind": kind, "axis": axis, "keys": keys,
            "unit": C.enum(C.opt_str(o, "unit", "deg", w), OVERRIDE_UNITS, w + ".unit"),
            "easing": C.enum(C.opt_str(o, "easing", "linear", w), joint_easings(),
                             w + ".easing")}


# ─────────────────────────────────────────────────────────── 查询面与摘要
def validate_perform(perf):
    """查询面：返回 (ok: bool, reason: str)。**不抛异常、不改入参。**"""
    return C.validate_with(normalize_perform, perf)


def perform_digest(perf):
    """表演契约的内容摘要（sha256 hex）。只依赖内容。"""
    return C.digest_of(normalize_perform(perf))


def phase_frames(perf):
    """三段各自的帧数（闭区间计数），按 PHASES 顺序返回 dict。"""
    ph = normalize_perform(perf)["phases"]
    return {n: ph[n]["end"] - ph[n]["start"] + 1 for n in PHASES}


def phase_ratios(perf):
    """三段占全镜头的时长占比（供 P1 判据门使用；**本函数不判合规**）。"""
    fr = phase_frames(perf)
    total = sum(fr.values())
    return {n: fr[n] / float(total) for n in PHASES}


def overrides_by_joint(perf):
    """关节 → 覆写声明（无覆写的关节不在结果中）。"""
    return {o["joint"]: o for o in normalize_perform(perf)["joint_overrides"]}


def perform_summary(perf):
    """人读摘要（用于报告与守卫断言，不参与哈希）。"""
    n = normalize_perform(perf)
    return {"schema": n["schema"], "id": n["id"], "spec": n["spec_ref"]["name"],
            "frames": n["frame_base"]["frame_end"] - n["frame_base"]["frame_start"] + 1,
            "phase_frames": phase_frames(n),
            "overlap_joints": len(n["overlap"]),
            "overrides": len(n["joint_overrides"]),
            "principles": list(n["principle_refs"]),
            "digest": perform_digest(n)}
