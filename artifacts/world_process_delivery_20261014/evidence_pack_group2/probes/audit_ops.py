# -*- coding: utf-8 -*-
"""过程补全与过程重组终审探针（独立构造）。

从仓根运行：
    .venv/Scripts/python.exe -X utf8 -B artifacts/world_process_delivery_20261014/group2_final_audit/probe/audit_ops.py
输出 JSON 到 logs/audit_ops.json。只读仓库源码，不写生产文件。
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, ROOT)

from lingshu.world import process_ops as ops   # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "logs", "audit_ops.json")
RES: dict = {}


def frame(tick, entities):
    return {"tick": int(tick), "entities": entities}


def build_record(n=12, start_tick=1, start_ref=10.0, step=1.0, narrowing=0.25,
                 ticks=None, ref_ys=None):
    """一条移动 ref 的记录：ref 每 tick 前进 step，actor 相对偏移逐帧收窄。

    ticks 显式给出采样 tick（用于非均匀 dt 与缺帧构造）；ref_ys 显式给出各采样的
    ref 竖直分量（用于竖直差构造）。两者都为 None 时是均匀 1 tick 一帧、y 恒为 0。
    """
    tick_list = list(ticks) if ticks is not None else [start_tick + k for k in range(n)]
    out = []
    for k, tick in enumerate(tick_list):
        ref = [start_ref + step * k, 0.0 if ref_ys is None else float(ref_ys[k]), 0.0]
        off = 5.0 - narrowing * k
        actor = [ref[0] + off, ref[1], ref[2]]
        out.append({"tick": int(tick),
                    "dt": 0 if k == 0 else int(tick_list[k]) - int(tick_list[k - 1]),
                    "ref": ref,
                    "rel": [actor[i] - ref[i] for i in range(3)]})
    return {"kind": "relative_trajectory", "actor": "leader", "ref": "walker",
            "process": None,
            "ticks": [out[0]["tick"], out[-1]["tick"]],
            "samples": out}


def sample_frames(record, ticks):
    """把记录采样按 tick 变成观测帧：entities 用记录里的角色名。"""
    by_tick = {s["tick"]: s for s in record["samples"]}
    out = []
    for t in ticks:
        s = by_tick[t]
        out.append(frame(t, {record["actor"]: [s["ref"][i] + s["rel"][i]
                                              for i in range(3)],
                             record["ref"]: list(s["ref"])}))
    return out


def expect_tail(record, matched, translation, prefix_last_tick, actor, ref, scale=1.0):
    """独立复算补全尾部：ref 帧 = 记录 ref + translation；actor 帧 = ref 帧 + scale×rel。"""
    out = []
    step = max(1, record["samples"][matched]["tick"] - record["samples"][matched - 1]["tick"])
    tick0 = prefix_last_tick + step
    for k, s in enumerate(record["samples"][matched:]):
        rp = [float(s["ref"][i]) + float(translation[i]) for i in range(3)]
        ap = [rp[i] + float(s["rel"][i]) * scale for i in range(3)]
        out.append({"tick": tick0 + (s["tick"] - record["samples"][matched]["tick"]),
                    "ref": rp, "actor": ap})
    return out


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def frames_match(frames, want, actor, ref, tol=1e-6):
    if len(frames) != len(want):
        return False, "帧数不一致 %d vs %d" % (len(frames), len(want))
    for i, (f, w) in enumerate(zip(frames, want)):
        if f["tick"] != w["tick"]:
            return False, "第 %d 帧 tick 不一致 %s vs %s" % (i, f["tick"], w["tick"])
        if not close(f["entities"][ref][0], w["ref"][0], tol) or \
           not close(f["entities"][ref][1], w["ref"][1], tol) or \
           not close(f["entities"][ref][2], w["ref"][2], tol):
            return False, "第 %d 帧 ref 不一致 %s vs %s" % (i, f["entities"][ref], w["ref"])
        if not close(f["entities"][actor][0], w["actor"][0], tol) or \
           not close(f["entities"][actor][1], w["actor"][1], tol) or \
           not close(f["entities"][actor][2], w["actor"][2], tol):
            return False, "第 %d 帧 actor 不一致 %s vs %s" % (i, f["entities"][actor], w["actor"])
    return True, ""


# ============ A. 原记录前缀再补全：逐帧恢复尾部 ============

def check_tail_restore():
    # 记录 12 个采样、逐 tick 一帧；观测前缀只给前 5 帧（tick 6 起要补全）
    rec = build_record()
    prefix = sample_frames(rec, [1, 2, 3, 4, 5])
    before = copy.deepcopy(rec)
    got = ops.complete_process(prefix, "leader", "walker", [rec])
    frames = got["frames"]
    match = got["match"]
    res = {}
    if match is None:
        RES["tail_restore"] = {"ok": False, "why": "未命中", "candidates": got["candidates"]}
        return
    matched = match["matched_samples"]
    want = expect_tail(rec, matched, match["translation"], 5, "leader", "walker")
    ok_frames, why = frames_match(frames, want, "leader", "walker")
    # 独立复算平移量：观测末帧 ref − 已匹配记录末帧 ref
    want_tr = [prefix[-1]["entities"]["walker"][i]
               - rec["samples"][matched - 1]["ref"][i] for i in range(3)]
    res = {"matched_samples": matched,
           "translation": match["translation"], "want_translation": want_tr,
           "translation_ok": all(close(a, b) for a, b in zip(match["translation"], want_tr)),
           "frames_ok": ok_frames, "frames_why": why,
           "frame_count": len(frames), "want_count": len(want),
           "ticks": got["ticks"], "want_ticks": [want[0]["tick"], want[-1]["tick"]],
           "seam": got["seam"],
           "record_unchanged": rec == before,
           "first_frame": frames[0]["entities"], "want_first": want[0]}
    # 反例：按改前口径（首条未观测采样的 ref 放到观测末帧）会有不同平移
    old_anchor = [prefix[-1]["entities"]["walker"][i]
                  - rec["samples"][matched]["ref"][i] for i in range(3)]
    res["old_anchor_translation"] = old_anchor
    res["old_anchor_differs"] = not all(close(a, b) for a, b in
                                        zip(old_anchor, match["translation"]))
    res["tail_covers_record_tail"] = (matched + len(frames) == len(rec["samples"]))
    res["ok"] = (res["translation_ok"] and res["frames_ok"] and res["record_unchanged"]
                 and res["ticks"] == res["want_ticks"] and res["old_anchor_differs"]
                 and res["tail_covers_record_tail"])
    RES["tail_restore"] = res


# ============ B. 整体平移 ============

def check_whole_translation():
    rec = build_record()
    delta = (100.0, 5.0, -30.0)
    prefix = sample_frames(rec, [1, 2, 3, 4, 5, 6])
    moved_prefix = [frame(f["tick"], {e: [p[i] + delta[i] for i in range(3)]
                                      for e, p in f["entities"].items()})
                    for f in prefix]
    got = ops.complete_process(moved_prefix, "leader", "walker", [rec])
    match = got["match"]
    if match is None:
        RES["whole_translation"] = {"ok": False, "why": "未命中"}
        return
    matched = match["matched_samples"]
    want = expect_tail(rec, matched, delta, 6, "leader", "walker")
    ok_frames, why = frames_match(got["frames"], want, "leader", "walker")
    res = {"translation": match["translation"],
           "translation_ok": all(close(a, b) for a, b in zip(match["translation"], delta)),
           "frames_ok": ok_frames, "frames_why": why,
           "ticks": got["ticks"]}
    res["ok"] = res["translation_ok"] and ok_frames
    RES["whole_translation"] = res


# ============ C. 非均匀 dt 与缺帧 ============

def check_nonuniform_dt():
    # 记录采样 tick 显式给出：缺 tick 4 与 tick 11（dt 在后一条上是 2）
    ticks = [1, 2, 3, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18]
    rec = build_record(ticks=ticks)
    obs_ticks = [1, 2, 3, 5, 6, 7]
    prefix = sample_frames(rec, obs_ticks)
    got = ops.complete_process(prefix, "leader", "walker", [rec])
    match = got["match"]
    res = {"prefix_ticks": obs_ticks}
    if match is None:
        res["ok"] = False
        res["why"] = "缺帧前缀未命中"
        res["candidates"] = got["candidates"]
        RES["nonuniform_dt"] = res
        return
    cand = [c for c in got["candidates"] if c["source"]["index"] == 0][0]
    res["timing"] = cand["timing"]
    res["matched_samples"] = match["matched_samples"]
    # 逐帧恢复：生成帧 tick 必须等于记录后续采样 tick 映射到前缀时间轴
    matched = match["matched_samples"]
    want = expect_tail(rec, matched, match["translation"], 7, "leader", "walker")
    ok_frames, why = frames_match(got["frames"], want, "leader", "walker")
    res["frames_ok"] = ok_frames
    res["frames_why"] = why
    res["ticks"] = got["ticks"]
    res["want_first_tick"] = want[0]["tick"]
    res["no_interpolation"] = True
    res["step"] = match["step"]
    # 同几何、节奏不同（每 tick 一帧）的记录不命中
    flat = build_record(n=len(ticks), start_tick=1)
    got_flat = ops.complete_process(prefix, "leader", "walker", [flat])
    res["flat_reason"] = got_flat["candidates"][0]["reason"]
    res["flat_timing"] = got_flat["candidates"][0]["timing"]
    res["flat_geometry"] = got_flat["candidates"][0]["geometry"]["score"]
    res["flat_match_none"] = got_flat["match"] is None
    res["flat_frames_empty"] = got_flat["frames"] == []
    # 只按匹配分数选候选时，两条记录必须给出不同的时序读数
    res["timing_distinguishes"] = (got_flat["candidates"][0]["timing"]["score"]
                                   < cand["timing"]["score"])
    res["ok"] = (ok_frames and cand["timing"]["matched"] is True
                 and got_flat["match"] is None and got_flat["frames"] == []
                 and got_flat["candidates"][0]["geometry"]["score"] == 1.0
                 and got_flat["candidates"][0]["timing"]["matched"] is False
                 and res["timing_distinguishes"])
    RES["nonuniform_dt"] = res


# ============ D. 不同 elapsed 默认不命中 ============

def check_timing_gate():
    # 记录逐 tick 一帧；前缀每 4 tick 一帧。几何用固定偏移，故意让几何全中；
    # 只有相对首帧的 elapsed tick 把它挡住。
    rec = build_record(n=24, narrowing=0.0)
    prefix = sample_frames(rec, [1, 5, 9, 13, 17, 21])
    got = ops.complete_process(prefix, "leader", "walker", [rec])
    cand = got["candidates"][0]
    res = {"geometry": cand["geometry"], "timing": cand["timing"],
           "match_score": cand["match_score"], "matched": cand["matched"],
           "match": got["match"], "reason": cand["reason"],
           "frames": got["frames"]}
    relaxed = ops.complete_process(prefix, "leader", "walker", [rec],
                                   thresholds=ops.ProcessOpsThresholds(min_hit_ratio=0.5))
    res["relaxed_score_unchanged"] = relaxed["candidates"][0]["match_score"] == cand["match_score"]
    res["relaxed_timing_matched"] = relaxed["candidates"][0]["timing"]["matched"]
    res["ok"] = (cand["geometry"]["score"] == 1.0
                 and cand["timing"]["matched"] is False
                 and cand["matched"] is False and got["match"] is None
                 and got["frames"] == [] and cand["match_score"] < 1.0
                 and res["relaxed_score_unchanged"]
                 and res["relaxed_timing_matched"] is False)
    RES["timing_gate"] = res


# ============ E. 同名角色抛 ValueError ============

def check_same_name():
    rec = build_record()
    prefix = sample_frames(rec, [1, 2, 3, 4, 5, 6])
    res = {}
    try:
        ops.complete_process(prefix, "walker", "walker", [rec])
        res["complete"] = "未抛异常"
    except ValueError as exc:
        res["complete"] = str(exc)
    try:
        ops.compose_processes(rec, rec, roles={"actor": "hero", "ref": "hero"})
        res["compose_roles"] = "未抛异常"
    except ValueError as exc:
        res["compose_roles"] = str(exc)
    same_named = copy.deepcopy(rec)
    same_named["actor"] = same_named["ref"] = "walker"
    try:
        ops.compose_processes(same_named, rec)
        res["compose_implicit"] = "未抛异常"
    except ValueError as exc:
        res["compose_implicit"] = str(exc)
    # 正常路径：不同名照常出帧
    ok_named = ops.complete_process(prefix, "leader", "walker", [rec])
    comp = ops.compose_processes(rec, rec, roles={"actor": "hero", "ref": "mark"})
    res["normal_complete_matched"] = ok_named["match"] is not None
    res["normal_compose_frames"] = len(comp["frames"]) > 0
    res["normal_compose_roles"] = sorted(comp["frames"][0]["entities"].keys())
    # 核心侧门面是否把该 ValueError 转成 error dict
    facade = None
    try:
        from lingshu.core.core import SpacetimeMemoryEngine
        from lingshu.world import register_world_process
        db = os.path.join(os.path.dirname(__file__), "..", "work", "facade_audit.db")
        if os.path.exists(db):
            os.remove(db)
        engine = SpacetimeMemoryEngine(db_path=db)
        register_world_process(engine)
        out = engine.world_process("complete", {"history": prefix, "actor": "walker",
                                                "ref": "walker", "record": rec})
        facade = {"type": "returned", "value": out}
        close = getattr(engine, "close", None)
        if callable(close):
            close()
    except Exception as exc:                       # noqa: BLE001
        facade = {"type": "raised", "error": type(exc).__name__ + ": " + str(exc)}
    res["core_facade"] = facade
    res["ok"] = (res["complete"].startswith("actor 与 ref 不得同名")
                 and res["compose_roles"].startswith("actor 与 ref 不得同名")
                 and res["compose_implicit"].startswith("actor 与 ref 不得同名")
                 and res["normal_complete_matched"] and res["normal_compose_frames"])
    RES["same_name"] = res


# ============ F. 候选来源与分数 ============

def check_candidates():
    rec = build_record(n=12)
    # 第二条记录：同样的帧数、形状完全不同
    other = build_record(n=12, start_ref=100.0, step=3.0, narrowing=-1.0)
    other["actor"] = "other_actor"
    other["ref"] = "other_ref"
    other["kind"] = "relative_trajectory"
    prefix = sample_frames(rec, [1, 2, 3, 4, 5, 6])
    got = ops.complete_process(prefix, "leader", "walker", [other, rec])
    cands = got["candidates"]
    res = {"order": [{"index": c["source"]["index"], "match_score": c["match_score"],
                      "matched": c["matched"], "reason": c["reason"],
                      "geometry": c["geometry"]["score"], "timing": c["timing"]["score"]}
                     for c in cands],
           "match_source": got["match"]["source"] if got["match"] else None,
           "match_score": got["match"]["match_score"] if got["match"] else None}
    # 独立复算命中候选的几何分数
    matched_index = got["match"]["source"]["index"] if got["match"] else None
    res["ok"] = (matched_index == 1
                 and res["order"][0]["index"] == 1
                 and res["order"][0]["match_score"] == 1.0
                 and res["match_score"] == 1.0
                 and res["order"][1]["matched"] is False)
    # 入参不改写
    res["records_argument_unchanged"] = other["actor"] == "other_actor"
    RES["candidates"] = res


# ============ G. 重组：只连两段、角色重绑定、joint 与关系 ============

def check_compose():
    first = build_record(n=6, start_ref=0.0, step=1.0, narrowing=0.5,
                         ticks=[1, 2, 3, 4, 5, 6], ref_ys=[0.0] * 6)
    first["process"] = {"type": "approach_stay_leave",
                        "phases": [{"phase": "approach"}, {"phase": "leave"}]}
    # 后段记录：ref 整体抬高 50；actor 相对 ref 的竖直偏移比前段末帧多 50
    second = build_record(n=4, start_ref=0.0, step=1.0,
                          ticks=[1, 2, 3, 4], ref_ys=[50.0] * 4)
    for s in second["samples"]:
        s["rel"][1] = 50.0
    for s in first["samples"]:
        s["rel"][1] = 0.0
    second["process"] = {"type": "approach_stay_leave",
                         "phases": [{"phase": "approach"}, {"phase": "leave"}]}
    before_first, before_second = copy.deepcopy(first), copy.deepcopy(second)
    got = ops.compose_processes(first, second, roles={"actor": "hero", "ref": "mark"},
                                translation=(10.0, 0.0, 5.0))
    res_inputs = {"inputs_unchanged": (first == before_first and second == before_second)}
    joint = got["joint"]
    seg = got["segments"]
    res = {"segments": len(seg), "roles": sorted(got["frames"][0]["entities"].keys()),
           "joint": joint, "relations": got["relations"],
           "ticks": got["ticks"], "frame_count": len(got["frames"]),
           "seg1_end_tick": seg[0]["frames"][-1]["tick"],
           "seg2_start_tick": seg[1]["frames"][0]["tick"]}
    # 独立复算 joint：后段首帧 ref 必须落在前段末帧 ref 上（水平）
    fr_end = seg[0]["frames"][-1]["entities"]
    fr_start = seg[1]["frames"][0]["entities"]
    res["ref_gap_independent"] = math.hypot(fr_end["mark"][0] - fr_start["mark"][0],
                                            fr_end["mark"][2] - fr_start["mark"][2])
    res["actor_gap_independent"] = math.hypot(fr_end["hero"][0] - fr_start["hero"][0],
                                              fr_end["hero"][2] - fr_start["hero"][2])
    res["ref_y_gap_independent"] = abs(fr_end["mark"][1] - fr_start["mark"][1])
    res["actor_y_gap_independent"] = abs(fr_end["hero"][1] - fr_start["hero"][1])
    res["tick_gap_ok"] = (res["seg2_start_tick"] - res["seg1_end_tick"]
                          == got["transform"]["tick_gap"])
    res["joint_ok"] = (close(joint["ref_gap"], res["ref_gap_independent"]) and
                       close(joint["actor_gap"], res["actor_gap_independent"]) and
                       close(joint["ref_y_gap"], res["ref_y_gap_independent"]) and
                       close(joint["actor_y_gap"], res["actor_y_gap_independent"]))
    res["continuous_covers_horizontal_only"] = (
        joint["continuous"] is (joint["actor_gap"] <= joint["joint_tol"]))
    # 记录里的原始角色名不得出现在生成帧里
    res["old_roles_absent"] = not any(k in got["frames"][0]["entities"]
                                      for k in ("leader", "walker"))
    res["relations_keys"] = sorted(got["relations"].keys())
    res["relations_text_ok"] = all(isinstance(got["relations"][k], (bool, type(None), str))
                                   for k in ("roles_rebound", "same_type"))
    res.update(res_inputs)
    # 关系读数只做事实陈述：没有“全语义兼容 / 可执行 / 因果”这类键
    res["relation_claim_keys"] = [k for k in got["relations"]
                                  if any(w in k for w in ("compat", "executable",
                                                          "causal", "semantic", "safe"))]
    res["no_compat_claim"] = res["relation_claim_keys"] == []
    # 读数只陈述事实：actor_y_gap = 两端相对竖直偏移之差；ref_y_gap 由连接点锚定恒为 0
    res["actor_y_gap_is_relative_offset_diff"] = abs(joint["actor_y_gap"] - 50.0) < 1e-6
    res["ref_y_gap_is_zero_by_anchor"] = abs(joint["ref_y_gap"]) < 1e-6
    res["ref_anchor_is_first_frame_of_head"] = (
        joint["ref"] == [round(v, 6) for v in seg[0]["frames"][-1]["entities"]["mark"]])
    res["ok"] = (res["segments"] == 2 and res["roles"] == ["hero", "mark"]
                 and res["joint_ok"] and res["tick_gap_ok"]
                 and res["continuous_covers_horizontal_only"]
                 and res["old_roles_absent"]
                 and res["relations"]["same_type"] is True
                 and res["actor_y_gap_is_relative_offset_diff"]
                 and res["ref_y_gap_is_zero_by_anchor"]
                 and res["no_compat_claim"] and res["inputs_unchanged"]
                 and res["ref_anchor_is_first_frame_of_head"])

    # 3 段串联：接口不接受
    res["three_segments_unsupported"] = None
    try:
        ops.compose_processes(first, second, {"actor": "a", "ref": "r"})
    except TypeError as exc:
        res["three_segments_unsupported"] = str(exc)
    RES["compose"] = res


def main():
    check_tail_restore()
    check_whole_translation()
    check_nonuniform_dt()
    check_timing_gate()
    check_same_name()
    check_candidates()
    check_compose()
    RES["source_sha256"] = {
        "process_ops.py": "59cde8f925fac077764acbba713c1de13432a22c6f8904d7fbe290019a93f2e7"}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(RES, fh, ensure_ascii=False, indent=2, sort_keys=True)
    for key, val in RES.items():
        if isinstance(val, dict) and "ok" in val:
            print("%-22s %s" % (key, "PASS" if val["ok"] else "FAIL"))
    print("written:", OUT)


if __name__ == "__main__":
    main()
