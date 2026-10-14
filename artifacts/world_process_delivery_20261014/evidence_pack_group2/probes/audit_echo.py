# -*- coding: utf-8 -*-
"""事件回声终审探针（独立构造，不复用原测试与其它代理的读数）。

从仓根运行：
    .venv/Scripts/python.exe -X utf8 -B artifacts/world_process_delivery_20261014/group2_final_audit/probe/audit_echo.py
输出 JSON 到本目录 logs/audit_echo.json，并打印逐项结论。
本探针只读仓库源码与模块函数，不写生产文件。
"""
from __future__ import annotations

import json
import math
import os
import random
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, ROOT)

from lingshu.world import world_process as wp   # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "logs", "audit_echo.json")
RES: dict = {}


def ev(tick, eid, event):
    """构造一条事件。事件流只认 tick / eid / event 三键。"""
    return {"tick": int(tick), "eid": str(eid), "event": str(event)}


def periodic(n_stop=10, stop_start=2, turn_start=7, step=10):
    """周期流：每 step 一个 stop 与一个 turn，tick 严格递增。"""
    out = []
    for k in range(n_stop):
        out.append(ev(stop_start + step * k, "x", "stop"))
        out.append(ev(turn_start + step * k, "x", "turn"))
    return out


def group_by_tick(events):
    """按 tick 分组的整组边界：返回 [(tick, [事件下标])]。"""
    groups = []
    for i, e in enumerate(events):
        if groups and groups[-1][0] == e["tick"]:
            groups[-1][1].append(i)
        else:
            groups.append((e["tick"], [i]))
    return groups


# ============ 1. holdout 比例与同 tick 整组 ============

def check_split():
    stream = periodic()                      # 20 条事件
    assert len(stream) == 20, len(stream)
    rows = []
    bad = []
    for f in (0.0, 0.1, 0.3, 0.5, 0.7, 0.99, 1.0):
        s = wp.echo_split(stream, f)
        want_holdout = max(0, min(20, int(math.ceil(20 * f))))
        idx = 20 - want_holdout
        # 独立复算：边界落在同 tick 组内部时整组进留出段（只向前移到组首）
        groups = group_by_tick(stream)
        while 0 < idx < 20:
            prev_tick = stream[idx - 1]["tick"]
            cur_tick = stream[idx]["tick"]
            if prev_tick == cur_tick:
                idx -= 1
            else:
                break
        want = {"events": 20, "dev_events": idx, "holdout_events": 20 - idx,
                "boundary_tick": None if idx >= 20 else stream[idx]["tick"],
                "dev_last_tick": None if idx == 0 else stream[idx - 1]["tick"],
                "holdout_first_tick": None if idx >= 20 else stream[idx]["tick"]}
        got = {k: s[k] for k in want}
        ok = got == want
        # 留出段必须是流尾连续子序列
        ok = ok and s["holdout"] == stream[idx:]
        ok = ok and s["dev"] == stream[:idx]
        # 两段并集 = 全流，不重叠
        ok = ok and len(s["dev"]) + len(s["holdout"]) == 20
        if not ok:
            bad.append((f, want, got))
        rows.append({"f": f, "want": want, "got": got, "ok": ok,
                     "boundary_after_dev": (s["dev_last_tick"] is not None
                                            and s["holdout_first_tick"] is not None
                                            and s["dev_last_tick"] < s["holdout_first_tick"])})
    RES["split_sweep_n20"] = {"rows": rows, "bad": bad, "pass": not bad}

    # 同 tick 组：组首、组中、组末三种边界位置
    def tick_row(events, f):
        s = wp.echo_split(events, f)
        dev_ticks = sorted({e["tick"] for e in s["dev"]})
        hold_ticks = sorted({e["tick"] for e in s["holdout"]})
        overlap = set(dev_ticks) & set(hold_ticks)
        return {"events": s["events"], "dev_events": s["dev_events"],
                "holdout_events": s["holdout_events"],
                "dev_ticks": dev_ticks, "holdout_ticks": hold_ticks,
                "overlap_ticks": sorted(overlap),
                "same_tick_grouped": s["same_tick_grouped"]}

    cases = {}
    head = [ev(10, "a", "stop"), ev(20, "a", "stop"),
            ev(30, "a", "stop"), ev(30, "b", "turn"), ev(30, "c", "stop"),
            ev(40, "a", "stop")]
    for label, f in (("组首_0.65", 0.65), ("组中_0.50", 0.50)):
        row = tick_row(head, f)
        row["ok"] = (row["dev_events"] == 2 and row["holdout_events"] == 4
                     and row["overlap_ticks"] == [] and row["dev_ticks"] == [10, 20]
                     and row["holdout_ticks"] == [30, 40])
        cases[label] = row
    tail = [ev(10, "a", "stop"), ev(20, "a", "stop"), ev(20, "b", "turn"),
            ev(20, "c", "stop"), ev(30, "a", "stop"), ev(40, "a", "stop")]
    row = tick_row(tail, 0.50)
    row["ok"] = (row["dev_events"] == 1 and row["holdout_events"] == 5
                 and row["overlap_ticks"] == [] and row["dev_ticks"] == [10]
                 and row["holdout_ticks"] == [20, 30, 40])
    cases["组末_0.50"] = row

    # 比例极端：0 与 1
    cases["f0"] = tick_row(tail, 0.0)
    cases["f0"]["ok"] = (cases["f0"]["dev_events"] == 6
                         and cases["f0"]["holdout_events"] == 0)
    cases["f1"] = tick_row(tail, 1.0)
    cases["f1"]["ok"] = (cases["f1"]["dev_events"] == 0
                         and cases["f1"]["holdout_events"] == 6)
    # 全流覆盖：dev + holdout 条数 = 事件数
    for label, row in cases.items():
        s_all = wp.echo_split(tail if "组末" in label or label in ("f0", "f1") else head,
                              {"组首_0.65": 0.65, "组中_0.50": 0.50}.get(label, 0.0))
        row["partition_ok"] = s_all["dev_events"] + s_all["holdout_events"] == s_all["events"]
    RES["split_tick_groups"] = {"cases": cases,
                                "pass": all(v["ok"] and v["partition_ok"]
                                            for v in cases.values())}


# ============ 2. 错误类型 / 错误实体 / 源自身不计 hit ============

def check_negative_hits():
    out = {}

    # 2.1 源自身不计 hit：dev 与留出都只有一条 x-stop@10 的周期，延迟 0
    src = [ev(10, "x", "stop")]
    assoc = [{"source_eid": "x", "source_event": "stop",
              "target_eid": "x", "target_event": "stop", "lag": 0,
              "count": 3, "after": "stop", "before": "stop"}]
    rows = wp.evaluate_event_echo(src, src, max_lag=12, min_support=1,
                                 min_consistency=0.0)
    # 该构造下关联 lag 0 不进入统计（lag<1 不计），所以留出段没有预测
    out["self_source"] = {"predictions": rows["lag_model"]["predictions"],
                          "hits": rows["lag_model"]["hits"],
                          "ok": rows["lag_model"]["hits"] == 0}

    # 2.2 错误类型：dev = x-stop@2..37（lag 5）；留出 x-stop@100, x-turn@105, x-stop@110
    dev = [ev(2 + 5 * k, "x", "stop") for k in range(8)]
    hold = [ev(100, "x", "stop"), ev(105, "x", "turn"), ev(110, "x", "stop")]
    rep = wp.evaluate_event_echo(dev, hold, max_lag=12, min_support=2,
                                 min_consistency=0.0)
    lag_rows = rep["lag_readings"]
    row0 = lag_rows[0]
    out["wrong_type"] = {"row0": {k: row0[k] for k in
                                  ("tick", "predicted_tick", "predicted_eid",
                                   "predicted_event", "actual_tick", "actual_eid",
                                   "actual_event", "hit", "end_state",
                                   "abs_tick_error")},
                         "ok": (row0["predicted_tick"] == 105
                                and row0["actual_tick"] == 110
                                and row0["hit"] is False
                                and row0["end_state"] == "missed")}

    # 2.3 错误实体：同一 tick 上另一实体有该事件类型
    dev2 = [ev(2 + 5 * k, "x", "stop") for k in range(8)]
    hold2 = [ev(100, "x", "stop"), ev(105, "y", "turn"), ev(110, "x", "stop")]
    rep2 = wp.evaluate_event_echo(dev2, hold2, max_lag=12, min_support=2,
                                  min_consistency=0.0)
    # 目标签名 x-turn 在留出段根本不存在 -> 响应为 None；预测 (105, x, turn) 不命中
    # dev 里没有 x-turn，所以关联目标只会是 x-stop；先确认真实触发的读数
    r0 = rep2["lag_readings"][0]
    # 105 上是 y-turn，不是 x-stop；源 x-stop@100 的响应只能是 110 的 x-stop
    ok_entity = (r0["actual_tick"] == 110 and r0["actual_eid"] == "x"
                 and r0["actual_event"] == "stop"
                 and not any(r["hit"] for r in rep2["lag_readings"]))
    out["wrong_entity"] = {"row0": {k: r0[k] for k in
                                    ("tick", "predicted_tick", "predicted_eid",
                                     "predicted_event", "actual_tick", "actual_eid",
                                     "actual_event", "hit", "end_state")},
                           "all_rows": [{k: r[k] for k in
                                         ("tick", "predicted_tick", "actual_tick",
                                          "hit", "end_state")}
                                        for r in rep2["lag_readings"]],
                           "ok": bool(ok_entity)}

    # 2.4 同 tick 上的源自身：源事件与响应同 tick 时必须不命中（lag=0 不进统计）
    same = [ev(10, "x", "stop"), ev(10, "x", "turn")]
    rep3 = wp.evaluate_event_echo(same, same, max_lag=12, min_support=1,
                                  min_consistency=0.0)
    out["same_tick_self"] = {"lag_model": rep3["lag_model"],
                             "ok": rep3["lag_model"]["hits"] == 0}

    # 2.5 反向：正确类型 + 正确实体 + 正确 tick 必须命中
    dev4 = [ev(2 + 5 * k, "x", "stop") for k in range(8)]
    hold4 = [ev(100, "x", "stop"), ev(105, "x", "stop"), ev(110, "x", "stop"),
             ev(115, "x", "stop"), ev(120, "x", "stop")]
    rep4 = wp.evaluate_event_echo(dev4, hold4, max_lag=12, min_support=2,
                                  min_consistency=0.0)
    out["positive_control"] = {"hits": rep4["lag_model"]["hits"],
                               "predictions": rep4["lag_model"]["predictions"],
                               "ok": rep4["lag_model"]["hits"] >= 3}

    out["ok"] = all(bool(v.get("ok")) for k, v in out.items()
                    if isinstance(v, dict))
    RES["negative_hits"] = out
    return out


# ============ 3. 插入无关事件不改固定目标命中与误差 ============

def check_unrelated_invariance():
    dev = [ev(2 + 5 * k, "x", "stop") for k in range(8)]
    clean = [ev(100, "x", "stop"), ev(105, "x", "stop"), ev(110, "x", "stop"),
             ev(115, "x", "stop"), ev(120, "x", "stop")]
    noisy = [ev(100, "x", "stop"), ev(103, "w", "turn"), ev(105, "x", "stop"),
             ev(107, "w", "stop"), ev(110, "x", "stop"), ev(112, "w", "turn"),
             ev(115, "x", "stop"), ev(120, "x", "stop")]
    a = wp.evaluate_event_echo(dev, clean, max_lag=12, min_support=2,
                               min_consistency=0.0)
    b = wp.evaluate_event_echo(dev, noisy, max_lag=12, min_support=2,
                               min_consistency=0.0)
    # 只比较同一源事件的逐条读数（tick+pid 必须都在两版里出现）
    def index(rep):
        return {(r["tick"], r["eid"]): r for r in rep["lag_readings"]
                if r["eid"] == "x"}
    ia, ib = index(a), index(b)
    common = sorted(set(ia) & set(ib))
    diffs = []
    for k in common:
        ra, rb = ia[k], ib[k]
        for key in ("predicted_tick", "predicted_eid", "predicted_event",
                    "actual_tick", "actual_eid", "actual_event", "hit",
                    "end_state", "abs_tick_error"):
            if ra[key] != rb[key]:
                diffs.append({"key": (k[0], k[1]), "field": key,
                              "clean": ra[key], "noisy": rb[key]})
    res = {"clean_hits": a["lag_model"]["hits"], "noisy_hits": b["lag_model"]["hits"],
           "clean_mae": a["lag_model"]["mean_abs_tick_error"],
           "noisy_mae": b["lag_model"]["mean_abs_tick_error"],
           "common_rows": len(common), "diffs": diffs,
           "clean_assoc": len(a["associations"]),
           "noisy_assoc_note": "无关实体在留出段插入，不参与开发段学习"}
    res["ok"] = (res["clean_hits"] == res["noisy_hits"]
                 and res["clean_mae"] == res["noisy_mae"] and not diffs)
    RES["unrelated_invariance"] = res
    return res


# ============ 4. actual 三键同源 + 入参不参与 ============

def check_actual_tuple():
    dev = [ev(2 + 5 * k, "x", "stop") for k in range(8)]
    assoc = wp.echo_lag_associations(dev, max_lag=12, min_support=2,
                                     min_consistency=0.0)
    stream = [ev(100, "x", "stop"), ev(102, "w", "turn"),
              ev(105, "x", "stop"), ev(110, "x", "stop")]
    src = ev(100, "x", "stop")
    a = wp.echo_lag_prediction(src, None, associations=assoc, events=stream)
    b = wp.echo_lag_prediction(src, {"tick": 9999, "eid": "x", "event": "turn"},
                               associations=assoc, events=stream)
    same = all(a[k] == b[k] for k in a)
    # actual 三键必须指向 events 里真实存在的一条
    def finds(row):
        return any(e["tick"] == row["actual_tick"] and e["eid"] == row["actual_eid"]
                   and e["event"] == row["actual_event"] for e in stream)
    res = {"a": {k: a[k] for k in ("tick", "predicted_tick", "predicted_eid",
                                   "predicted_event", "actual_tick", "actual_eid",
                                   "actual_event", "hit", "end_state",
                                   "abs_tick_error")},
           "actual_next_ignored": same,
           "actual_triple_in_stream": finds(a),
           "same_response_object": (a["actual_tick"] == 105
                                    and a["actual_eid"] == "x"
                                    and a["actual_event"] == "stop")}
    res["ok"] = same and finds(a) and res["same_response_object"]
    RES["actual_tuple"] = res
    return res


# ============ 5. 两模型同触发同目标、coverage、右侧未决与已观测 miss ============

def check_models_and_coverage():
    dev = [ev(2 + 5 * k, "x", "stop") for k in range(8)]
    hold = [ev(100, "x", "stop"), ev(105, "x", "stop"), ev(110, "x", "turn"),
            ev(115, "x", "stop"), ev(120, "x", "stop"), ev(122, "x", "stop")]
    rep = wp.evaluate_event_echo(dev, hold, max_lag=12, min_support=2,
                                 min_consistency=0.0)
    la, nb = rep["lag_readings"], rep["no_delay_readings"]
    n = rep["holdout_events"]
    same_trigger = all((a["tick"], a["eid"], a["predicted_eid"], a["predicted_event"])
                       == (b["tick"], b["eid"], b["predicted_eid"], b["predicted_event"])
                       for a, b in zip(la, nb))
    models = {}
    for name, rows in (("lag_model", la), ("no_delay_model", nb)):
        m = rep[name]
        preds = sum(1 for r in rows if r["predicted_tick"] is not None)
        models[name] = {
            "events": m["events"], "predictions": m["predictions"],
            "hits": m["hits"], "misses": m["misses"], "scored": m["scored"],
            "no_prediction": m["no_prediction"], "unscored": m["unscored"],
            "prediction_beyond_window": m["prediction_beyond_window"],
            "coverage": m["coverage"], "scored_rate": m["scored_rate"],
            "mae": m["mean_abs_tick_error"],
            "coverage_ok": abs(m["coverage"] - round(m["predictions"] / n, 6)) < 1e-9,
            "prediction_count_ok": preds == m["predictions"],
            "partition_ok": (m["scored"] + m["no_prediction"]
                             + m["prediction_beyond_window"] == n),
        }
        models[name]["ok"] = (models[name]["coverage_ok"]
                              and models[name]["prediction_count_ok"]
                              and models[name]["partition_ok"]
                              and m["predictions"] + m["no_prediction"] == n)
    # 右侧未决：预测 tick 超出流尾
    beyond_rows = [r for r in la if r["end_state"] == "beyond_window"]
    beyond_ok = all(r["abs_tick_error"] is None and r["hit"] is False
                    and r["predicted_tick"] > hold[-1]["tick"] for r in beyond_rows)
    # 已观测目标与预测不同 -> missed 且偏差如实
    missed_rows = [r for r in la if r["end_state"] == "missed"]
    missed_ok = all(r["hit"] is False and r["actual_tick"] is not None
                    and r["abs_tick_error"] == abs(r["predicted_tick"] - r["actual_tick"])
                    for r in missed_rows)
    # 无预测的行：predicted_tick 为 None，end_state 为 no_prediction
    np_rows = [r for r in la if r["end_state"] == "no_prediction"]
    np_ok = all(r["predicted_tick"] is None and r["hit"] is False for r in np_rows)
    res = {"holdout_events": n, "same_trigger_and_target": same_trigger,
           "models": models, "beyond_rows": len(beyond_rows),
           "beyond_ok": beyond_ok, "missed_rows": len(missed_rows),
           "missed_ok": missed_ok, "no_prediction_rows": len(np_rows),
           "no_prediction_ok": np_ok,
           "detail": [{k: r[k] for k in ("tick", "predicted_tick", "actual_tick",
                                         "actual_eid", "actual_event", "hit",
                                         "end_state", "abs_tick_error")}
                      for r in la]}
    res["ok"] = (same_trigger and all(v["ok"] for v in models.values())
                 and beyond_ok and missed_ok and np_ok
                 and models["lag_model"]["coverage_ok"])
    RES["models_and_coverage"] = res
    return res


# ============ 6. 固定签名跨实体配对 + 两套一致性公式 ============

def check_cross_entity_pairs():
    stream = [ev(10, "alpha", "stop"), ev(15, "beta", "turn"),
              ev(20, "alpha", "stop"), ev(25, "beta", "turn"),
              ev(30, "alpha", "stop"), ev(35, "beta", "turn"),
              ev(40, "alpha", "stop"), ev(45, "beta", "turn")]
    assoc = wp.echo_lag_associations(stream, max_lag=12, min_support=2,
                                     min_consistency=0.0)
    cross = [a for a in assoc if a["source_eid"] == "alpha" and a["source_event"] == "stop"
             and a["target_eid"] == "beta" and a["target_event"] == "turn"]
    row = cross[0] if cross else None
    # 独立复算：对每个 alpha-stop 源事件，按 (target_eid, target_event) 分桶取最早响应
    own = {}
    total_src = 0
    with_resp = 0
    for src in stream:
        if src["eid"] != "alpha" or src["event"] != "stop":
            continue
        total_src += 1
        first = {}
        for tgt in stream:
            key = (tgt["eid"], tgt["event"])
            if key in first:
                continue
            lag = tgt["tick"] - src["tick"]
            if 1 <= lag <= 12:
                first[key] = lag
        if first:
            with_resp += 1
        if ("beta", "turn") in first:
            own.setdefault(first[("beta", "turn")], 0)
            own[first[("beta", "turn")]] += 1
    main_lag = min(own, key=lambda L: (-own[L], L)) if own else None
    count = own.get(main_lag, 0)
    pair_total = sum(own.values())
    want_consistency = round(count / with_resp, 6) if with_resp else 0.0
    want_lag_consistency = round(count / pair_total, 6) if pair_total else 0.0
    res = {"assoc_row": None if row is None else
           {k: row[k] for k in ("source_eid", "source_event", "target_eid",
                                "target_event", "lag", "count", "lag_counts",
                                "triggers", "sources_total", "sources_with_pair",
                                "consistency", "lag_consistency")},
           "independent": {"main_lag": main_lag, "count": count,
                           "with_response_sources": with_resp,
                           "sources_total": total_src,
                           "pair_total": pair_total,
                           "consistency": want_consistency,
                           "lag_consistency": want_lag_consistency}}
    res["ok"] = bool(row) and (row["lag"] == main_lag and row["count"] == count
                               and row["consistency"] == want_consistency
                               and row["lag_consistency"] == want_lag_consistency
                               and row["target_eid"] == "beta"
                               and row["target_event"] == "turn")
    RES["cross_entity"] = res
    return res


def check_two_formulas():
    """同一份构造上读出两套口径的差别，并验证字段名与文档一致。"""
    stream = [ev(10, "x", "stop"), ev(11, "y", "turn"),
              ev(20, "x", "stop"), ev(21, "y", "stop"),
              ev(30, "x", "stop"), ev(31, "y", "turn"),
              ev(40, "x", "stop"), ev(41, "z", "stop"),
              ev(50, "x", "stop"), ev(51, "z", "stop")]
    assoc = wp.echo_lag_associations(stream, max_lag=12, min_support=1,
                                     min_consistency=0.0)
    rows = []
    for a in assoc:
        pair_total = sum(a["lag_counts"].values())
        rows.append({"target_eid": a["target_eid"], "target_event": a["target_event"],
                     "lag": a["lag"], "count": a["count"],
                     "lag_counts": a["lag_counts"], "triggers": a["triggers"],
                     "consistency": a["consistency"],
                     "lag_consistency": a["lag_consistency"],
                     "formula_a_ok": a["consistency"] == round(a["count"] / a["triggers"], 6),
                     "formula_b_ok": a["lag_consistency"] == round(a["count"] / pair_total, 6)})
    # 存在至少一条两种口径不同的行，说明字段没有同义重复
    distinct = any(r["consistency"] != r["lag_consistency"] for r in rows)
    res = {"rows": rows, "has_distinct_pair": distinct,
           "ok": bool(rows) and all(r["formula_a_ok"] and r["formula_b_ok"] for r in rows)}
    RES["two_formulas"] = res
    return res


# ============ 7. 固定 seed 校准（只作启发式，不作统计检验） ============

def lcg_events(seed, count=40, size=400):
    """独立实现的线性同余序列：40 条事件、tick 落在 0..399、eid 取 e0/e1/e2。

    与返修报告写明的构造同法（不用 random 模块），但由本次审计自己写。
    """
    state = int(seed) & 0xFFFFFFFF
    out = []
    for i in range(count):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        tick = state % size
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        kind = "stop" if (state % 2) == 0 else "turn"
        out.append(ev(tick, "e%d" % (i % 3), kind))
    return out


def check_seed_calibration(seeds=range(1000, 1300)):
    def make(seed):
        return lcg_events(seed)

    got0 = 0
    got7 = 0
    best = 0.0
    total = 0
    for seed in seeds:
        total += 1
        evs = make(seed)
        a0 = wp.echo_lag_associations(evs, max_lag=12, min_support=2,
                                      min_consistency=0.0)
        a7 = wp.echo_lag_associations(evs, max_lag=12, min_support=2,
                                      min_consistency=0.7)
        if a0:
            got0 += 1
            best = max(best, max(x["consistency"] for x in a0))
        if a7:
            got7 += 1
    RES["seed_calibration"] = {
        "seeds": [min(seeds), max(seeds)], "trials": total,
        "assoc_at_0": got0, "assoc_at_0.7": got7, "max_consistency_at_0": best,
        "note": "只复现门槛量级，不作统计检验，不保证随机序列不出现偶然候选"}
    return RES["seed_calibration"]


def main():
    check_split()
    check_negative_hits()
    check_unrelated_invariance()
    check_actual_tuple()
    check_models_and_coverage()
    check_cross_entity_pairs()
    check_two_formulas()
    check_seed_calibration()
    RES["source_sha256"] = {
        "world_process.py": "950f3d9669ffaf5cc9ee63c71e3f55cc21ebba00ede6d70649454cacf8a74659",
        "process_ops.py": "59cde8f925fac077764acbba713c1de13432a22c6f8904d7fbe290019a93f2e7",
        "core.py": "5fc2ad929702c9b8a2087e755faa2835354ce974f09c0bffe1c46de02403be82",
        "git_head": "71b6c00"}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(RES, fh, ensure_ascii=False, indent=2, sort_keys=True)
    for key, val in RES.items():
        if isinstance(val, dict) and "ok" in val:
            print("%-24s %s" % (key, "PASS" if val["ok"] else "FAIL"))
        elif key == "split_sweep_n20":
            print("%-24s %s" % (key, "PASS" if val["pass"] else "FAIL"))
        elif key == "split_tick_groups":
            print("%-24s %s" % (key, "PASS" if val["pass"] else "FAIL"))
    print("written:", OUT)


if __name__ == "__main__":
    main()
