# -*- coding: utf-8 -*-
"""隔离变异自证：baseline 行为断言 pass，同一断言在 mutant 下 fail。

4 份副本都在内存里改一处源码串，写进系统临时目录，用 importlib 按文件路径加载，
不碰仓库源码。基线 = 仓库当前文件；每个副本各配一条语义断言。

从仓根运行：
    .venv/Scripts/python.exe -X utf8 -B artifacts/world_process_delivery_20261014/evidence_pack_group2/probes/audit_mutation.py

仓根由本脚本从自身位置向上找 lingshu/world/world_process.py 定位，
所以本脚本可放在包里任意深度。输出 JSON 到
<包>/readouts/final_audit/audit_mutation_clean.json；临时副本落系统临时目录，
跑完删除，不写进证据包。输出路径可用环境变量 AUDIT_MUTATION_OUT 覆盖。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, ".."))


def find_root(start):
    """从 start 向上找含 lingshu/world/world_process.py 的目录，即仓根。"""
    here = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(here, "lingshu", "world",
                                       "world_process.py")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            raise SystemExit("未找到仓根：从 %s 向上没有 "
                             "lingshu/world/world_process.py" % start)
        here = parent


ROOT = find_root(HERE)
WORK = tempfile.mkdtemp(prefix="audit_mutation_")
OUT = os.environ.get("AUDIT_MUTATION_OUT",
                     os.path.join(PACK, "readouts", "final_audit",
                                  "audit_mutation_clean.json"))
sys.path.insert(0, ROOT)

WP = os.path.join(ROOT, "lingshu", "world", "world_process.py")
PO = os.path.join(ROOT, "lingshu", "world", "process_ops.py")

MUTANTS = [
    {
        "name": "wp_m1_split_reversed",
        "file": WP,
        "old": "    index = total - holdout_count\n",
        "new": "    index = min(total, holdout_count)\n",
        "assert": "assert_split_direction",
        "why": "把留出段改从流首切出（比例语义反转）",
    },
    {
        "name": "wp_m2_coverage_scored",
        "file": WP,
        "old": '            "coverage": round(predictions / total, 6) if total else 0.0,\n',
        "new": '            "coverage": round(len(scored) / total, 6) if total else 0.0,\n',
        "assert": "assert_coverage_and_self_response",
        "why": "coverage 改回 scored ÷ events 的旧口径",
    },
    {
        "name": "wp_m3_no_self_exclusion",
        "file": WP,
        "old": "        if _echo_key(item) <= here:\n            continue\n",
        "new": "        if _echo_key(item) < here:\n            continue\n",
        "assert": "assert_coverage_and_self_response",
        "why": "真实响应不再排除源事件自身",
    },
    {
        "name": "po_m3_old_anchor",
        "file": PO,
        "old": "    matched_ref = [float(v) for v in samples[matched - 1][\"ref\"]]\n",
        "new": "    matched_ref = [float(v) for v in samples[matched][\"ref\"]]\n",
        "assert": "assert_tail_restore",
        "why": "补全平移锚点改成首条未观测采样的 ref（改前口径）",
    },
]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ---------- 语义断言（只吃模块对象，baseline 与 mutant 各跑一次） ----------

def ev(tick, eid, event):
    return {"tick": int(tick), "eid": str(eid), "event": str(event)}


def assert_split_direction(wp):
    stream = []
    for k in range(10):
        stream.append(ev(2 + 10 * k, "x", "stop"))
        stream.append(ev(7 + 10 * k, "x", "turn"))
    s = wp.echo_split(stream, 0.3)
    assert s["events"] == 20, s["events"]
    assert s["holdout_events"] == 6, s["holdout_events"]
    assert s["dev_events"] == 14, s["dev_events"]
    assert s["boundary_tick"] == 72, s["boundary_tick"]
    assert s["holdout"] == stream[14:], "留出段不是流尾 6 条"


def assert_unrelated_invariance(wp):
    """无关实体插入不改命中与偏差；同目标签名的插入必须改变 actual。"""
    dev = [ev(2 + 5 * k, "x", "stop") for k in range(8)]
    hold = [ev(100, "x", "stop"), ev(105, "x", "stop"), ev(110, "x", "stop"),
            ev(115, "x", "stop"), ev(120, "x", "stop")]
    # 噪音留出只在 x 的各条之间插入 y 实体的无关事件：
    # 目标签名 (x, stop) 的候选集合与干净留出逐条相同。
    noisy = [ev(100, "x", "stop"), ev(103, "y", "stop"), ev(105, "x", "stop"),
             ev(107, "y", "turn"), ev(110, "x", "stop"), ev(113, "y", "stop"),
             ev(115, "x", "stop"), ev(120, "x", "stop")]
    clean = wp.evaluate_event_echo(dev, hold, max_lag=12, min_support=2,
                                   min_consistency=0.0)
    dirty = wp.evaluate_event_echo(dev, noisy, max_lag=12, min_support=2,
                                   min_consistency=0.0)
    # 只比两边都有的同一源事件（tick, eid）：命中、actual 三键与偏差逐条相同
    ca = {(r["tick"], r["eid"]): r for r in clean["lag_readings"] if r["eid"] == "x"}
    da = {(r["tick"], r["eid"]): r for r in dirty["lag_readings"] if r["eid"] == "x"}
    assert sorted(ca) == sorted(da), (sorted(ca), sorted(da))
    for key in sorted(ca):
        assert ca[key]["hit"] == da[key]["hit"], (key, ca[key]["hit"], da[key]["hit"])
        assert ca[key]["actual_tick"] == da[key]["actual_tick"], key
        assert ca[key]["actual_eid"] == da[key]["actual_eid"], key
        assert ca[key]["actual_event"] == da[key]["actual_event"], key
        assert ca[key]["abs_tick_error"] == da[key]["abs_tick_error"], key
    assert len(ca) >= 3, sorted(ca)
    # 干扰之后真实响应仍是固定目标签名在源事件之后最早的一条（tick 105）
    r0 = dirty["lag_readings"][0]
    assert r0["tick"] == 100 and r0["predicted_tick"] == 105, r0
    assert r0["actual_tick"] == 105 and r0["actual_eid"] == "x" \
        and r0["actual_event"] == "stop", r0
    assert r0["hit"] is True and r0["end_state"] == "hit", r0
    # 对照：同一位置插入同目标签名 (x, stop) 的事件，actual 必须改到 103，
    # 命中转落空。无关插入的不变性只对无关签名成立，不是判据忽略新事件。
    clash_stream = [ev(100, "x", "stop"), ev(103, "x", "stop"),
                    ev(105, "x", "stop"), ev(110, "x", "stop"),
                    ev(115, "x", "stop"), ev(120, "x", "stop")]
    clash = wp.evaluate_event_echo(dev, clash_stream, max_lag=12, min_support=2,
                                   min_consistency=0.0)
    c0 = clash["lag_readings"][0]
    assert c0["tick"] == 100 and c0["predicted_tick"] == 105, c0
    assert c0["actual_tick"] == 103 and c0["actual_eid"] == "x" \
        and c0["actual_event"] == "stop", c0
    assert c0["hit"] is False and c0["end_state"] == "missed", c0
    assert c0["abs_tick_error"] == 2, c0
    assert c0["actual_tick"] != r0["actual_tick"], (c0, r0)


def assert_coverage_and_self_response(wp):
    """coverage = predictions ÷ events；源事件自身不得成为自己的响应。"""
    dev = [ev(2 + 5 * k, "x", "stop") for k in range(8)]
    hold = [ev(100, "x", "stop"), ev(105, "x", "stop"), ev(110, "x", "stop"),
            ev(115, "x", "stop"), ev(120, "x", "stop"), ev(122, "x", "stop")]
    rep = wp.evaluate_event_echo(dev, hold, max_lag=12, min_support=2,
                                 min_consistency=0.0)
    m = rep["lag_model"]
    assert m["predictions"] == 6 and m["events"] == 6, m
    assert m["scored"] == 4, m
    assert m["coverage"] == round(m["predictions"] / m["events"], 6), m
    assert m["coverage"] != round(m["scored"] / m["events"], 6), m
    assert m["scored_rate"] == round(m["scored"] / m["events"], 6), m
    # 源事件自身：预测落在自己的 tick 上时不得命中
    stream = [ev(10, "x", "stop"), ev(10, "x", "turn")]
    assoc = [{"source_eid": "x", "source_event": "stop", "target_eid": "x",
              "target_event": "stop", "lag": 0, "count": 3, "after": "stop",
              "before": "stop"}]
    row = wp._echo_row(stream, ev(10, "x", "stop"), 0, assoc)
    assert row["predicted_tick"] == 10, row
    assert row["actual_tick"] is None, row
    assert row["hit"] is False, row


def build_record(n=12, start_ref=10.0, narrowing=0.25, ticks=None):
    tick_list = list(ticks) if ticks is not None else [1 + k for k in range(n)]
    out = []
    for k, tick in enumerate(tick_list):
        ref = [start_ref + k, 0.0, 0.0]
        off = 5.0 - narrowing * k
        out.append({"tick": int(tick),
                    "dt": 0 if k == 0 else int(tick_list[k]) - int(tick_list[k - 1]),
                    "ref": ref,
                    "rel": [off, 0.0, 0.0]})
    return {"kind": "relative_trajectory", "actor": "leader", "ref": "walker",
            "process": None, "ticks": [out[0]["tick"], out[-1]["tick"]], "samples": out}


def frames_of(record, ticks):
    by_tick = {s["tick"]: s for s in record["samples"]}
    out = []
    for t in ticks:
        s = by_tick[t]
        out.append({"tick": t,
                    "entities": {record["actor"]: [s["ref"][i] + s["rel"][i]
                                                  for i in range(3)],
                                 record["ref"]: list(s["ref"])}})
    return out


def assert_tail_restore(ops):
    rec = build_record()
    prefix = frames_of(rec, [1, 2, 3, 4, 5])
    got = ops.complete_process(prefix, "leader", "walker", [rec])
    assert got["match"] is not None, got["candidates"]
    frames = got["frames"]
    want_tr = [prefix[-1]["entities"]["walker"][i]
               - rec["samples"][got["match"]["matched_samples"] - 1]["ref"][i]
               for i in range(3)]
    assert all(abs(a - b) < 1e-9 for a, b in zip(got["match"]["translation"], want_tr)), (
        got["match"]["translation"], want_tr)
    # 逐帧还原记录尾部：第 0 帧 ref 必须等于记录第 5 条采样的 ref + 平移量
    first_ref = frames[0]["entities"]["walker"]
    want_ref = [rec["samples"][5]["ref"][i] + want_tr[i] for i in range(3)]
    assert all(abs(a - b) < 1e-9 for a, b in zip(first_ref, want_ref)), (
        first_ref, want_ref)
    assert len(frames) == len(rec["samples"]) - 5, len(frames)


ASSERTS = {"assert_split_direction": assert_split_direction,
           "assert_unrelated_invariance": assert_unrelated_invariance,
           "assert_coverage_and_self_response": assert_coverage_and_self_response,
           "assert_tail_restore": assert_tail_restore}


def run(name, mod, assert_name):
    fn = ASSERTS[assert_name]
    try:
        fn(mod)
        return {"result": "pass"}
    except AssertionError as exc:
        return {"result": "fail", "kind": "assertion_error", "detail": str(exc)[:400]}
    except Exception as exc:                       # noqa: BLE001
        return {"result": "fail", "kind": type(exc).__name__, "detail": str(exc)[:400]}


def main():
    try:
        run_audit()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


def run_audit():
    report = {"baseline": {}, "mutants": [],
              "work_dir_pattern": "<临时目录>/audit_mutation_*",
              "output": os.path.relpath(OUT, ROOT).replace(os.sep, "/"),
              "source_sha256": {"world_process.py": sha(WP),
                                "process_ops.py": sha(PO)}}
    base_wp = load(WP, "audit_wp_base")
    # process_ops 有相对导入：基线走包内导入，副本在 work/ 里单独加载
    import lingshu.world.process_ops as base_po
    report["baseline"]["world_process.py"] = {
        "assert_split_direction": run("base", base_wp, "assert_split_direction"),
        "assert_unrelated_invariance": run("base", base_wp, "assert_unrelated_invariance"),
        "assert_coverage_and_self_response":
            run("base", base_wp, "assert_coverage_and_self_response")}
    report["baseline"]["process_ops.py"] = {
        "assert_tail_restore": run("base", base_po, "assert_tail_restore")}

    for item in MUTANTS:
        with open(item["file"], "r", encoding="utf-8") as fh:
            src = fh.read()
        hits = src.count(item["old"])
        if hits != 1:
            report["mutants"].append({"name": item["name"], "error":
                                      "替换串在源码里出现 %d 次，拒绝变异" % hits})
            continue
        mutated = src.replace(item["old"], item["new"])
        if item["file"] == PO:
            mutated = mutated.replace(
                "from .world_process import GENERATED, pair_samples, transfer_demonstration",
                "from lingshu.world.world_process import GENERATED, pair_samples, "
                "transfer_demonstration")
        path = os.path.join(WORK, item["name"] + ".py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(mutated)
        mod = load(path, "audit_" + item["name"])
        row = {"name": item["name"], "why": item["why"], "assert": item["assert"],
               "replacement_hits": hits,
               "mutant_sha256": hashlib.sha256(mutated.encode("utf-8")).hexdigest(),
               "baseline": run("base", base_wp if item["file"] == WP else base_po,
                               item["assert"]),
               "mutant": run("mutant", mod, item["assert"])}
        row["isolated"] = (row["baseline"]["result"] == "pass"
                           and row["mutant"]["result"] == "fail"
                           and row["mutant"]["kind"] == "assertion_error")
        report["mutants"].append(row)
        if os.path.exists(path):
            os.remove(path)

    # 仓库源码在本次运行前后保持一致
    report["source_unchanged"] = (sha(WP) == report["source_sha256"]["world_process.py"]
                                  and sha(PO) == report["source_sha256"]["process_ops.py"])
    report["all_baseline_pass"] = all(
        row["result"] == "pass"
        for group in report["baseline"].values() for row in group.values())
    report["all_mutants_isolated"] = all(
        row.get("isolated") is True for row in report["mutants"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
    for row in report["mutants"]:
        print("%-26s baseline=%-4s mutant=%-4s isolated=%s" % (
            row.get("name"), row.get("baseline", {}).get("result"),
            row.get("mutant", {}).get("result"), row.get("isolated")))
    print("all_baseline_pass:", report["all_baseline_pass"])
    print("all_mutants_isolated:", report["all_mutants_isolated"])
    print("source_unchanged:", report["source_unchanged"])
    print("output:", OUT)


if __name__ == "__main__":
    main()
