# -*- coding: utf-8 -*-
"""test_event_echo · 事件回声的延迟关联判据（世界过程第 2 组）
============================================================================
被测模块：lingshu/world/world_process.py 的事件回声段。
事件源是 motion_events 的停止与转向事件：本件先用运动轨迹造出计划好的事件序列，
再核对事件流、延迟关联、切分与留出片段上的两模型对照。

配对口径：签名对 = (source_eid, source_event, target_eid, target_event)。同一签名对
按 (target_eid, target_event) 分桶，每桶只记最接近源事件的那条响应；事件流里的位置
间隔不参与统计，所以插入无关事件不改变同一签名对测到的 lag，跨实体对也能学。

判据组：
  A 已知延迟：停止与转向相隔 5 tick 时学到 lag=5、consistency=1.0；
    同一签名对的响应唯一，簇内更晚的同类响应不重复计数。
  B 留出对照与切分：留出条数 = ceil(事件数 × holdout_fraction)，切分下标 =
    事件数 − 留出条数，留出段取流尾；边界落在同 tick 组内部时整组进留出段，
    dev 末 tick 严格小于 holdout 首 tick；两模型在留出段上共用触发集合与命中判据。
  C 反例：错误类型同 tick 不算命中；源事件自身不算响应；同 tick 簇与交错序列不改变
    同一签名对的 lag；无关事件与实体改名／输入顺序不改变关联，也不改变命中与 MAE；
    不规则序列在默认支持度下不出停止到转向的关联；固定 seed 独立序列在默认门槛下
    不出稳定关联。
  D 评分只在一处：evaluate_event_echo 与 echo_lag_prediction 的命中判据一致；真实响应
    取固定目标签名在源事件之后最早的一条；预测目标与 lag 只由开发关联与当前源事件
    决定，actual_next 不参与选择，也不进 actual 三键与偏差。
  E 输入不变量与边界：history 与事件列表不被改写；缺帧按真实 tick 差算延迟；
    同输入同输出；lag 超出 max_lag 不计入；无关联时预测返回 None；
    coverage = predictions ÷ events，预测超出观察尾部按 beyond_window 单列计数。
  F 隔离变异：6 份副本各自只做定点替换并核对替换计数，每份都让对应语义断言变红——
    M1 命中判据去掉 eid、M2 退化成相邻事件配对、M3 切分去掉同 tick 组回退、
    M4 颠倒切分方向、M5 响应退化成全流下一事件、M6 coverage 用 scored 除事件数。
    副本在 group2_echo_fix2/mutation_work/，由 make_mutants.py 从仓库源码生成。

运行（仓根）：
    .venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_event_echo.py -q -p no:cacheprovider
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.world.world_process import (ProcessThresholds,        # noqa: E402
                                         echo_lag_associations,
                                         echo_lag_prediction,
                                         echo_split,
                                         evaluate_event_echo,
                                         event_echo_readings,
                                         event_echo_report,
                                         motion_events)

MODULE_PATH = os.path.join(REPO, "lingshu", "world", "world_process.py")
WORK_DIR = os.path.join(REPO, "artifacts", "world_process_delivery_20261014",
                        "group2_echo_fix2", "mutation_work")


# ================= 夹具：按事件计划造轨迹 =================

def build_from_plan(plan, steps=240):
    """plan 元素 = (事件, tick)：stop 的该步位移为 0，turn 的该步左转 90°。

    第 1 个样本在 tick 1；计划事件挂在哪个采样 tick 上由运动学决定，
    本函数不直接写事件，事件一律由 motion_events 从轨迹里读出来。
    """
    kinds = {}
    for event, tick in plan:
        kinds.setdefault(tick - 1, []).append(event)
    for key, kinds_at_tick in kinds.items():
        assert kinds_at_tick in (["stop"], ["turn"]), (key, kinds_at_tick)
    dx, dz = 1.0, 0.0
    x = z = 0.0
    track = [[0.0, 1.0, 0.0]]
    for k in range(1, steps + 1):
        kind = kinds.get(k)
        if kind == ["stop"]:
            track.append([x, 1.0, z])
            continue
        if kind == ["turn"]:
            dx, dz = -dz, dx
        x += dx
        z += dz
        track.append([x, 1.0, z])
    return track


def history_of(tracks):
    """{eid: 轨迹} → history；该 tick 没有观测的实体不写进该帧。"""
    ticks = max(len(t) for t in tracks.values())
    out = []
    for i in range(ticks):
        ents = {eid: [float(v) for v in t[i]]
                for eid, t in tracks.items() if i < len(t)}
        out.append({"tick": i + 1, "entities": ents})
    return out


def periodic_plan(cycles=10):
    """每轮 4 步：先停一步，再直行 4 步，第 5 步转向。"""
    plan, tick = [], 2
    for _ in range(cycles):
        plan.append(("stop", tick))
        plan.append(("turn", tick + 5))
        tick += 10
    return plan


def periodic_history(cycles=10):
    return history_of({"courier": build_from_plan(periodic_plan(cycles))})


def irregular_plan():
    """延迟逐次变化：停止到转向的间隔按 1/2/4/5 重复 3 轮。"""
    plan, tick = [], 2
    for lags in ((1, 2, 4, 5), (1, 2, 4, 5), (1, 2, 4, 5)):
        for lag in lags:
            plan.append(("stop", tick))
            plan.append(("turn", tick + lag))
            tick += lag + 3
    return plan


def irregular_history():
    return history_of({"wanderer": build_from_plan(irregular_plan())})


def drift_history():
    """稀疏采样：停止那 4 个 tick 只漂移 0.4，速度 0.1 低于 stop_speed 0.2。"""
    return [{"tick": 1, "entities": {"drifter": [0.0, 1.0, 0.0]}},
            {"tick": 2, "entities": {"drifter": [1.0, 1.0, 0.0]}},
            {"tick": 6, "entities": {"drifter": [1.4, 1.0, 0.0]}},
            {"tick": 7, "entities": {"drifter": [1.4, 1.0, 1.0]}},
            {"tick": 8, "entities": {"drifter": [1.4, 1.0, 2.0]}},
            {"tick": 9, "entities": {"drifter": [1.4, 1.0, 3.0]}},
            {"tick": 13, "entities": {"drifter": [1.4, 1.0, 3.4]}}]


def two_entity_history():
    return history_of({"alpha": build_from_plan(periodic_plan(6)),
                       "beta": build_from_plan([("turn", 5), ("stop", 9),
                                                ("turn", 16), ("stop", 20),
                                                ("turn", 27), ("stop", 31)])})


def events_ticks(events, event=None):
    return [e["tick"] for e in events if event is None or e["event"] == event]


def event(tick, eid, kind):
    """最小事件：只填签名判据要用的三个键，另给 dt 与 lag_from_prev 占位。"""
    return {"tick": int(tick), "eid": str(eid), "event": str(kind),
            "dt": 1, "lag_from_prev": 0}


def sig(assoc):
    """关联的签名与读数：eid 与 event 两侧都在键里。"""
    return (assoc["source_eid"], assoc["source_event"],
            assoc["target_eid"], assoc["target_event"],
            assoc["lag"], assoc["count"], assoc["consistency"])


def assoc_of(assocs, source_eid, source_event, target_eid, target_event):
    for item in assocs:
        if (item["source_eid"], item["source_event"], item["target_eid"],
                item["target_event"]) == (source_eid, source_event,
                                          target_eid, target_event):
            return item
    return None


def seq(pairs):
    """(eid, tick, event) 列表 → 事件流；用时按 (tick, eid, event) 排序。"""
    return sorted([event(t, eid, kind) for eid, t, kind in pairs],
                  key=lambda e: (e["tick"], e["eid"], e["event"]))


def independent_events(seed, count=40, size=400):
    """固定 seed 的确定伪随机序列：线性同余生成 tick 与事件类型，不用 random 模块。"""
    state = seed & 0xFFFFFFFF
    out = []
    for i in range(count):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        tick = state % size
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        kind = "stop" if (state % 2) == 0 else "turn"
        out.append(event(tick, "e%d" % (i % 3), kind))
    return out


# ================= A 组：已知延迟 =================

def test_learns_known_lag():
    history = periodic_history()
    events = event_echo_readings(history)
    assert [(e["tick"], e["eid"], e["event"]) for e in events[:2]] == [
        (2, "courier", "stop"), (7, "courier", "turn")]
    assert events_ticks(events, "stop") == [2, 12, 22, 32, 42, 52, 62, 72, 82, 92]
    assert events_ticks(events, "turn") == [7, 17, 27, 37, 47, 57, 67, 77, 87, 97]
    # 事件流按 tick 与实体排序；lag_from_prev 按真实 tick 差算。
    assert [e["lag_from_prev"] for e in events] == [0] + [5] * 19

    assoc = echo_lag_associations(events)
    main = assoc_of(assoc, "courier", "stop", "courier", "turn")
    assert main is not None, assoc
    assert main["lag"] == 5 and main["count"] == 10
    assert main["lag_counts"] == {5: 10}
    assert main["consistency"] == pytest.approx(1.0)
    assert main["triggers"] == 10 and main["sources_total"] == 10
    # 跨轮的同 tick 组合（停止与转向相隔 10 tick）也成对，但主 lag 仍是 5。
    assert (main["source_eid"], main["target_eid"]) == ("courier", "courier")


def test_association_keeps_both_side_identity():
    """统计键保留 source 与 target 的 eid 与 event；同类型不同实体的对分开计数。"""
    events = seq([("aa", 10, "stop"), ("bb", 15, "stop"),
                  ("aa", 20, "stop"), ("bb", 25, "stop"),
                  ("aa", 30, "stop"), ("bb", 35, "stop")])
    assoc = echo_lag_associations(events, min_support=1)
    # aa 的下一条事件每次都是 bb@+5（同 tick 没有事件），所以 aa 之后
    # 同一签名的最早响应就是 bb 那条：同类型不同 eid 的对分开计数。
    cross_ab = assoc_of(assoc, "aa", "stop", "bb", "stop")
    cross_ba = assoc_of(assoc, "bb", "stop", "aa", "stop")
    same_bb = assoc_of(assoc, "bb", "stop", "bb", "stop")
    assert cross_ab["lag"] == 5 and cross_ab["count"] == 3
    assert cross_ba["lag"] == 5 and cross_ba["count"] == 2
    assert same_bb["lag"] == 10 and same_bb["count"] == 2
    # 同实体同类型（aa→aa）只有隔一条才成对：最早的 aa 响应是 bb，被 bb 挡掉。
    assert assoc_of(assoc, "aa", "stop", "aa", "stop") is None
    assert all(a["source_eid"] and a["target_eid"] for a in assoc)
    # 同一签名对的响应唯一：同一源事件对同一签名只记一条响应。
    assert same_bb["count"] <= same_bb["sources_total"]
    assert set(same_bb["lag_counts"]) == {10}


# ================= B 组：留出片段两模型对照 =================

def test_split_keeps_same_tick_group_together():
    """边界落在同 tick 组内部时整组进留出段：dev 末 tick 小于留出首 tick。"""
    events = seq([("aa", 10, "stop"), ("aa", 20, "stop"), ("aa", 30, "stop"),
                  ("bb", 30, "stop"), ("cc", 30, "turn"), ("aa", 40, "stop")])
    split = echo_split(events, 0.5)
    # 留出 3 条落在 tick 30 组内部，边界向前移到该组首，留出段从 tick 30 起。
    assert split["events"] == 6 and split["holdout_events"] == 4
    assert split["dev_events"] == 2
    assert split["boundary_tick"] == 30
    assert split["dev_last_tick"] == 20 and split["holdout_first_tick"] == 30
    assert split["dev_last_tick"] < split["holdout_first_tick"]
    assert [(e["tick"], e["eid"], e["event"]) for e in split["dev"]] == [
        (10, "aa", "stop"), (20, "aa", "stop")]
    assert [(e["tick"], e["eid"], e["event"]) for e in split["holdout"]] == [
        (30, "aa", "stop"), (30, "bb", "stop"), (30, "cc", "turn"),
        (40, "aa", "stop")]
    # 同 tick 的三条事件全在同一段，没有一条被劈到另一段。
    assert split["same_tick_grouped"] is True
    assert len([e for e in split["holdout"] if e["tick"] == 30]) == 3
    assert not [e for e in split["dev"] if e["tick"] == 30]
    # 换一刀（0.34，期望留出 3 条）同样落在 tick 30 组内部，读数不变。
    other = echo_split(events, 0.34)
    assert other["dev_events"] == 2 and other["holdout_events"] == 4
    assert other["boundary_tick"] == 30
    assert other["dev_last_tick"] < other["holdout_first_tick"]
    assert not [e for e in other["dev"] if e["tick"] == 30]
    # 比例 0 时留出段为空，dev 末 tick 是最大 tick。
    none = echo_split(events, 0.0)
    assert none["holdout_events"] == 0 and none["boundary_tick"] is None
    assert none["dev_last_tick"] == 40 and none["holdout_first_tick"] is None
    # 比例 1 时全部进留出段，dev 为空。
    whole = echo_split(events, 1.0)
    assert whole["dev_events"] == 0 and whole["holdout_events"] == 6
    assert whole["boundary_tick"] == 10 and whole["dev_last_tick"] is None


def test_split_fraction_sweep_matches_ceil_rule():
    """留出条数 = ceil(事件数 × 比例) 且留出段取流尾；训练末 tick 严格早于留出首 tick。"""
    events = event_echo_readings(periodic_history())
    ticks = [e["tick"] for e in events]
    assert len(events) == 20 and len(set(ticks)) == 20        # 本条流没有同 tick 组
    expect = {0.0: 0, 0.1: 2, 0.3: 6, 0.5: 10, 0.7: 14, 0.99: 20, 1.0: 20}
    for fraction, holdout_count in expect.items():
        split = echo_split(events, fraction)
        assert split["holdout_events"] == holdout_count, (fraction, split)
        assert split["dev_events"] == 20 - holdout_count, (fraction, split)
        # 留出段确实是流的最后 holdout_count 条，不是开头的那几条。
        assert [e["tick"] for e in split["holdout"]] == ticks[20 - holdout_count:]
        assert [e["tick"] for e in split["dev"]] == ticks[:20 - holdout_count]
        if holdout_count and split["dev_events"]:
            assert split["dev_last_tick"] < split["holdout_first_tick"]
        if not holdout_count:
            assert split["boundary_tick"] is None
            assert split["dev_last_tick"] == ticks[-1]
        if not split["dev_events"]:
            assert split["dev_last_tick"] is None
    # 比例 0.3 的定档读数：dev 14 条、留出 6 条、边界 tick 72。
    cut = echo_split(events, 0.3)
    assert (cut["dev_events"], cut["holdout_events"], cut["boundary_tick"]) == \
        (14, 6, 72)


def test_split_boundary_at_group_head_middle_and_tail():
    """边界在 tick 组首、组中、组末三种位置：只有落在组内部才向前移到组首。"""
    shared = seq([("aa", 10, "stop"), ("aa", 20, "stop"), ("aa", 30, "stop"),
                  ("bb", 30, "stop"), ("cc", 30, "turn"), ("aa", 40, "stop")])
    # 组首：边界正好落在 tick 30 组的首条上（前一条 tick 20），不向前退。
    # 旧实现会因为后一条同为 tick 30 再多退一组，把 dev 压到 1 条。
    at_head = echo_split(shared, 0.65)
    assert at_head["dev_events"] == 2 and at_head["holdout_events"] == 4
    assert at_head["dev_last_tick"] == 20 and at_head["boundary_tick"] == 30
    assert [e["tick"] for e in at_head["dev"]] == [10, 20]
    # 组中：边界落在 tick 30 组的中间，向前移到该组首。
    middle = echo_split(shared, 0.5)
    assert middle["dev_events"] == 2 and middle["holdout_events"] == 4
    assert middle["boundary_tick"] == 30
    assert [e["eid"] for e in middle["holdout"] if e["tick"] == 30] == \
        ["aa", "bb", "cc"]
    # 组末：边界落在 tick 20 组的最后一条上，整组三条一起进留出段。
    tail = seq([("aa", 10, "stop"), ("bb", 20, "stop"), ("cc", 20, "stop"),
                ("dd", 20, "stop"), ("aa", 30, "stop"), ("aa", 40, "stop")])
    at_tail = echo_split(tail, 0.5)
    assert at_tail["dev_events"] == 1 and at_tail["holdout_events"] == 5
    assert at_tail["dev_last_tick"] == 10 and at_tail["holdout_first_tick"] == 20
    assert [e["eid"] for e in at_tail["holdout"] if e["tick"] == 20] == \
        ["bb", "cc", "dd"]
    assert not [e for e in at_tail["dev"] if e["tick"] == 20]


def test_lag_learned_from_development_only():
    """留出段的事件不参与学习：只用开发段也学到同一个 lag。"""
    events = event_echo_readings(periodic_history())
    split = echo_split(events)
    assert split["events"] == 20 and split["dev_events"] == 14
    assert split["holdout_events"] == 6 and split["boundary_tick"] == 72
    assert events_ticks(split["dev"]) == [2, 7, 12, 17, 22, 27, 32, 37, 42, 47,
                                          52, 57, 62, 67]
    assert events_ticks(split["dev"])[-1] < split["holdout_first_tick"]
    dev_assoc = echo_lag_associations(split["dev"])
    main = assoc_of(dev_assoc, "courier", "stop", "courier", "turn")
    assert main["lag"] == 5 and main["count"] == 7
    assert main["consistency"] == pytest.approx(1.0)
    assert main["triggers"] == 7 and main["sources_total"] == 7
    # 开发段只有 14 条事件；每个源事件对每个目标签名至多记 1 条响应。
    assert sum(a["count"] for a in dev_assoc) == 25
    assert sum(a["count"] for a in dev_assoc) <= 14 * 13
    assert {e["tick"] for e in split["holdout"]} == {72, 77, 82, 87, 92, 97}
    # 不借留出段：把留出段 6 条从整条流里删掉后重算，关联逐字相同。
    lean = [e for e in events if e["tick"] not in {72, 77, 82, 87, 92, 97}]
    assert json.dumps(echo_lag_associations(lean), sort_keys=True) == \
        json.dumps(dev_assoc, sort_keys=True)


def test_holdout_comparison_reports_both_models():
    events = event_echo_readings(periodic_history())
    split = echo_split(events)
    out = evaluate_event_echo(split["dev"], split["holdout"])

    assert out["kind"] == "event_echo_evaluation"
    assert out["models"] == ["lag", "no_delay"]
    assert out["max_lag"] == 12 and out["min_support"] == 2
    assert out["min_consistency"] == pytest.approx(0.7)
    assert out["dev_events"] == 14 and out["holdout_events"] == 6

    lag = out["lag_model"]
    # 延迟模型：留出段 6 条事件里 5 条可评分且全部命中，末条的预测越过观察尾部。
    assert lag["events"] == 6 and lag["hit_rate"] == pytest.approx(5 / 6)
    assert lag["hits"] == 5 and lag["scored"] == 5
    assert lag["abs_tick_errors"] == [0] * 5
    assert lag["mean_abs_tick_error"] == 0.0
    assert lag["predictions"] == 6 and lag["no_prediction"] == 0
    assert lag["unscored"] == 1 and lag["prediction_beyond_window"] == 1
    assert lag["unresolved_window_end"] == 1
    assert lag["coverage"] == pytest.approx(1.0)
    assert lag["scored_rate"] == pytest.approx(5 / 6)

    # 无延迟模型：触发集合与目标签名相同，只把 lag 记成 0，所以全部落空。
    base = out["no_delay_model"]
    assert base["predictions"] == lag["predictions"]
    assert base["coverage"] == lag["coverage"]
    assert base["hits"] == 0 and base["hit_rate"] == 0.0
    assert base["abs_tick_errors"] == [5] * 5
    assert base["mean_abs_tick_error"] == 5.0
    assert base["scored"] == 6 and base["prediction_beyond_window"] == 0

    # 命中要求目标 eid、event、tick 三者全部吻合；没有靠“同 tick 的同类事件”顶替。
    assert all(r["predicted_eid"] == "courier" for r in out["lag_readings"])
    assert [r["predicted_event"] for r in out["lag_readings"][:5]] == [
        "turn", "stop", "turn", "stop", "turn"]
    assert [(r["tick"], r["predicted_tick"], r["actual_tick"], r["hit"])
            for r in out["lag_readings"][:3]] == [
        (72, 77, 77, True), (77, 82, 82, True), (82, 87, 87, True)]
    # 末条的预测落在观察尾部之外：不给命中，也不给偏差。
    assert out["lag_readings"][-1]["end_state"] == "beyond_window"
    assert out["lag_readings"][-1]["actual_tick"] is None
    assert out["lag_readings"][-1]["abs_tick_error"] is None
    assert all(r["hit"] is False for r in out["no_delay_readings"])
    assert all(r["predicted_tick"] == r["tick"] for r in out["no_delay_readings"])
    # 四类数量各自独立，且构成事件集合的一个划分。
    for model in (lag, base):
        assert model["no_prediction"] + model["predictions"] == model["events"]
        assert model["scored"] + model["unscored"] == model["events"]
        assert model["misses"] == model["events"] - model["hits"]
        assert model["hits"] <= model["predictions"] <= model["events"]
        assert model["coverage"] == pytest.approx(
            model["predictions"] / model["events"])
        assert (model["scored"] + model["no_prediction"]
                + model["prediction_beyond_window"]) == model["events"]


def test_report_readings_from_one_history():
    """完整读数：事件流 + 切分 + 两模型对照，各层事件数一致；主要键保持原名。"""
    report = event_echo_report(periodic_history())
    assert report["kind"] == "event_echo_report"
    assert report["thresholds"]["max_lag"] == 12
    assert report["thresholds"]["min_support"] == 2
    assert report["thresholds"]["min_consistency"] == pytest.approx(0.7)
    assert report["thresholds"]["holdout_fraction"] == pytest.approx(0.3)
    assert report["thresholds"]["stop_speed"] == ProcessThresholds().stop_speed
    assert report["thresholds"]["turn_deg"] == ProcessThresholds().turn_deg
    assert len(report["events"]) == 20
    assert report["split"] == {"events": 20, "dev_events": 14, "holdout_events": 6,
                               "boundary_tick": 72, "dev_last_tick": 67,
                               "holdout_first_tick": 72, "same_tick_grouped": True}
    main = assoc_of(report["associations"], "courier", "stop", "courier", "turn")
    assert main is not None and main["lag"] == 5 and main["count"] == 7
    assert report["evaluation"]["lag_model"]["hits"] == 5
    assert report["evaluation"]["no_delay_model"]["hits"] == 0
    assert report["evaluation"]["lag_model"]["hit_rate"] > \
        report["evaluation"]["no_delay_model"]["hit_rate"]
    # core 与演示读的主要键仍在。
    for key in ("kind", "thresholds", "events", "split", "associations", "evaluation"):
        assert key in report
    for key in ("lag_model", "no_delay_model", "lag_readings", "no_delay_readings"):
        assert key in report["evaluation"]


# ================= C 组：反例与不变量 =================

def test_wrong_event_type_on_same_tick_is_not_a_hit():
    """留出段停 100、停 105、转 110：预测转 110 而实际下一条是停 105，不算命中。"""
    dev = seq([("x", 2, "stop"), ("x", 7, "turn"),
               ("x", 12, "stop"), ("x", 17, "turn"),
               ("x", 22, "stop"), ("x", 27, "turn"),
               ("x", 32, "stop"), ("x", 37, "turn")])
    holdout = seq([("x", 100, "stop"), ("x", 105, "stop"), ("x", 110, "turn")])
    out = evaluate_event_echo(dev, holdout)
    main = assoc_of(out["associations"], "x", "stop", "x", "turn")
    assert main["lag"] == 5 and main["consistency"] == pytest.approx(1.0)
    assert (out["lag_readings"][0]["predicted_tick"],
            out["lag_readings"][0]["predicted_eid"],
            out["lag_readings"][0]["predicted_event"]) == (105, "x", "turn")
    # 105 上确有一条事件，但它是 stop；固定目标签名是 (x, turn)，真实响应在 110。
    assert {(e["tick"], e["event"]) for e in holdout
            if e["tick"] == 105} == {(105, "stop")}
    assert (out["lag_readings"][0]["actual_tick"],
            out["lag_readings"][0]["actual_eid"],
            out["lag_readings"][0]["actual_event"]) == (110, "x", "turn")
    assert out["lag_readings"][0]["hit"] is False
    assert out["lag_readings"][0]["end_state"] == "missed"
    assert out["lag_readings"][0]["abs_tick_error"] == 5
    # 停 105 的下一条响应是转 110，类型与 eid 都对，命中。
    assert out["lag_readings"][1]["predicted_tick"] == 110
    assert out["lag_readings"][1]["actual_tick"] == 110
    assert out["lag_readings"][1]["hit"] is True
    assert out["lag_model"]["hits"] == 1
    assert out["lag_model"]["misses"] == 2


def test_source_event_itself_is_not_a_response():
    """预测落在源事件自己的 tick 上时，同 tick 的同类响应不能顶替成命中。"""
    dev = seq([("x", t, "stop") for t in (10, 20, 30, 40, 50, 60)])
    assoc = echo_lag_associations(dev)
    assert assoc[0]["lag"] == 10 and assoc[0]["consistency"] == pytest.approx(1.0)
    holdout = seq([("x", 100, "stop"), ("x", 105, "turn"),
                   ("x", 110, "stop"), ("x", 120, "stop"), ("x", 130, "stop")])
    out = evaluate_event_echo(dev, holdout)
    # 无延迟模型的预测是同一 tick 上的目标签名 (x, stop)：源事件自己不算响应，
    # 所以真实响应是 110 的停，预测 tick 100 与它不符，这一条不能白拿。
    base = out["no_delay_readings"][0]
    assert base["predicted_tick"] == 100 and base["predicted_event"] == "stop"
    assert base["actual_tick"] == 110 and base["hit"] is False
    assert out["no_delay_model"]["hits"] == 0
    # 延迟模型预测停@110：固定目标签名 (x, stop) 在 100 之后最早的一条就是它。
    row = out["lag_readings"][0]
    assert (row["tick"], row["predicted_tick"], row["predicted_event"]) == \
        (100, 110, "stop")
    assert (row["actual_tick"], row["actual_eid"], row["actual_event"]) == \
        (110, "x", "stop")
    assert row["hit"] is True and row["abs_tick_error"] == 0
    # 转@105 没有已学签名对，不给预测；停@110 与停@120 的签名对逐条命中。
    assert out["lag_readings"][1]["predicted_tick"] is None
    assert out["lag_readings"][1]["end_state"] == "no_prediction"
    assert out["lag_readings"][2]["predicted_tick"] == 120
    assert out["lag_readings"][2]["predicted_event"] == "stop"
    assert out["lag_readings"][2]["actual_tick"] == 120
    assert out["lag_readings"][2]["hit"] is True
    assert out["lag_readings"][3]["predicted_tick"] == 130
    assert out["lag_readings"][3]["hit"] is True
    assert out["lag_model"]["hits"] == 3
    # 把关联的目标 eid 换成 y：同类型、同 tick 区间，但 eid 不符 ⇒ 不算命中，
    # 而且流里没有 (y, stop) 这条签名，actual 三键一起为 None。
    other_eid = [dict(a, target_eid="y") for a in assoc]
    miss = echo_lag_prediction(holdout[2], holdout[3], associations=other_eid,
                               events=holdout)
    assert miss["predicted_tick"] == 120 and miss["predicted_eid"] == "y"
    assert (miss["actual_tick"], miss["actual_eid"], miss["actual_event"]) == \
        (None, None, None)
    assert miss["hit"] is False and miss["end_state"] == "missed"


def test_unrelated_entity_events_do_not_change_hits_or_mae():
    """插入或删除无关实体的事件，不改变固定目标签名的命中与 MAE。"""
    dev = seq([("x", 10, "stop"), ("y", 15, "turn"), ("x", 20, "stop"),
               ("y", 25, "turn"), ("x", 30, "stop"), ("y", 35, "turn"),
               ("x", 40, "stop"), ("y", 45, "turn")])
    plain = seq([("x", 100, "stop"), ("y", 105, "turn"), ("x", 110, "stop"),
                 ("y", 115, "turn")])
    noisy = seq([("x", 100, "stop"), ("w", 102, "turn"), ("y", 105, "turn"),
                 ("x", 110, "stop"), ("w", 112, "turn"), ("y", 115, "turn")])
    lean = evaluate_event_echo(dev, plain)["lag_model"]
    full = evaluate_event_echo(dev, noisy)["lag_model"]
    # 同触发集合、同目标签名、同真实目标：hits 与 MAE 两次相同。
    assert lean["hits"] == full["hits"] == 3
    assert lean["abs_tick_errors"] == full["abs_tick_errors"] == [0, 0, 0]
    assert lean["mean_abs_tick_error"] == full["mean_abs_tick_error"] == 0.0
    rows = evaluate_event_echo(dev, noisy)["lag_readings"]
    # 无关实体的事件没有已学签名对，不产生预测；真实目标只按签名取。
    assert [r["predicted_tick"] for r in rows] == [105, None, 110, 115, None, 120]
    assert [r["actual_tick"] for r in rows] == [105, None, 110, 115, None, None]
    # actual 三键取自同一条响应事件，不各取一处。
    assert [(r["actual_tick"], r["actual_eid"], r["actual_event"])
            for r in rows[:3]] == [(105, "y", "turn"), (None, None, None),
                                   (110, "x", "stop")]
    assert [r["hit"] for r in rows] == [True, False, True, True, False, False]
    assert rows[0]["end_state"] == "hit" and rows[-1]["end_state"] == \
        "beyond_window"


def test_coverage_counts_predictions_not_scored_rows():
    """coverage = predictions ÷ events；没有预测的行不算覆盖。"""
    dev = seq([("x", 10, "stop")])
    holdout = seq([("x", 100, "stop"), ("x", 110, "turn"),
                   ("x", 120, "stop"), ("x", 130, "turn")])
    out = evaluate_event_echo(dev, holdout)
    assert out["associations"] == []
    for model in (out["lag_model"], out["no_delay_model"]):
        assert model["events"] == 4 and model["predictions"] == 0
        assert model["no_prediction"] == 4 and model["scored"] == 0
        assert model["unscored"] == 4 and model["coverage"] == 0.0
        assert model["scored_rate"] == 0.0
        assert model["hits"] == 0 and model["misses"] == 4
        assert model["no_prediction"] + model["predictions"] == model["events"]
        assert (model["scored"] + model["no_prediction"]
                + model["prediction_beyond_window"]) == model["events"]
    # 有预测但超出观察尾部时 coverage 仍按预测数算，scored_rate 才是判据跑过的比例。
    tail = evaluate_event_echo(
        seq([("x", t, "stop") for t in (10, 20, 30, 40, 50, 60)]),
        seq([("x", 100, "stop"), ("x", 101, "stop")]))["lag_model"]
    assert tail["predictions"] == 2 and tail["scored"] == 0
    assert tail["coverage"] == pytest.approx(1.0)
    assert tail["scored_rate"] == pytest.approx(0.0)
    assert tail["coverage"] != tail["scored_rate"]


def test_prediction_beyond_window_is_marked_and_counted():
    """预测超出已观察尾部：单列 beyond_window 计数，不混成普通 miss。"""
    dev = seq([("x", t, "stop") for t in (10, 20, 30, 40, 50, 60)])
    holdout = seq([("x", 100, "stop"), ("x", 101, "stop")])
    out = evaluate_event_echo(dev, holdout)
    model, rows = out["lag_model"], out["lag_readings"]
    assert [(r["tick"], r["predicted_tick"], r["end_state"]) for r in rows] == [
        (100, 110, "beyond_window"), (101, 111, "beyond_window")]
    assert all(r["scored"] is False for r in rows)
    assert all(r["abs_tick_error"] is None for r in rows)
    # 行 0 的流里其实还有下一条事件（101），但它不是预测的目标位置：偏差仍为 None，
    # 且单独计数，不混成 abs_error=None 的普通 miss。
    assert rows[0]["actual_tick"] == 101
    assert model["prediction_beyond_window"] == 2
    assert model["unresolved_window_end"] == 2
    assert model["scored"] == 0 and model["unscored"] == 2
    assert model["abs_tick_errors"] == []
    assert model["mean_abs_tick_error"] is None
    # 右侧删失范围 = 预测 tick 大于观察尾部 tick 的行；本条流观察止于 tick 101。
    assert model["events"] == 2 and model["predictions"] == 2
    assert model["coverage"] == pytest.approx(1.0)


def test_observed_target_differing_from_prediction_is_miss():
    """真实目标已观察到但与预测不同：如实记落空，偏差按该响应算。"""
    dev = seq([("x", t, "stop") for t in (10, 20, 30, 40, 50, 60)])
    holdout = seq([("x", 100, "stop"), ("x", 111, "stop"), ("x", 120, "stop")])
    out = evaluate_event_echo(dev, holdout)
    row = out["lag_readings"][0]
    assert row["predicted_tick"] == 110
    assert (row["actual_tick"], row["actual_eid"], row["actual_event"]) == \
        (111, "x", "stop")
    assert row["hit"] is False and row["end_state"] == "missed"
    assert row["scored"] is True and row["abs_tick_error"] == 1
    assert out["lag_model"]["abs_tick_errors"] == [1]


def test_consistency_mixes_target_choice_and_lag_concentration():
    """consistency 的分子只数一个目标签名，分母数该源签名的全部有响应触发。"""
    import inspect
    events = seq([("x", 10, "stop"), ("y", 11, "turn"), ("x", 20, "stop"),
                  ("y", 21, "turn"), ("x", 30, "stop"), ("y", 31, "turn"),
                  ("x", 40, "stop"), ("y", 42, "turn"), ("x", 50, "stop"),
                  ("w", 51, "stop")])
    assoc = echo_lag_associations(events, min_support=1, min_consistency=0.0)
    # (x, stop) 到 (y, turn)：主 lag 1 出现 3 次，该签名对的 lag 计数共 4 次，
    # 而该源签名在窗口内有响应的触发是 5 次 —— 两个分母给出两个读数。
    turn = assoc_of(assoc, "x", "stop", "y", "turn")
    assert turn["lag_counts"] == {1: 3, 2: 1} and turn["count"] == 3
    assert turn["triggers"] == 5 and turn["sources_total"] == 5
    assert turn["sources_with_pair"] == turn["triggers"]
    assert turn["consistency"] == pytest.approx(3 / 5)
    assert turn["lag_consistency"] == pytest.approx(3 / 4)
    # 混合口径：目标覆盖 0.6 与 lag 集中 0.75 一起进 consistency；
    # lag_consistency 只看后者，只作原始读数。
    assert turn["consistency"] < 0.7 <= turn["lag_consistency"]
    assert turn["consistency"] != turn["lag_consistency"]
    # 同一源签名到它自己：目标唯一，两个读数的差别只剩响应覆盖率。
    main = assoc_of(assoc, "x", "stop", "x", "stop")
    assert main["lag_counts"] == {10: 4} and main["count"] == 4
    assert main["consistency"] == pytest.approx(4 / 5)
    assert main["lag_consistency"] == pytest.approx(1.0)
    assert main["consistency"] < main["lag_consistency"]
    # 默认门槛与支持度没为凑随机空输出而改动：(y, turn) 这条按 0.4 被剔除，
    # (x, stop) 到它自己按 0.8 保留。
    sig = inspect.signature(echo_lag_associations)
    assert sig.parameters["min_consistency"].default == 0.7
    assert sig.parameters["min_support"].default == 2
    kept = echo_lag_associations(events)
    assert assoc_of(kept, "x", "stop", "y", "turn") is None
    assert assoc_of(kept, "x", "stop", "x", "stop") is not None


def test_same_tick_cluster_keeps_signature_pair_lag():
    """同 tick 簇不打断同一签名对：a@30 与 a@40 的 lag 仍是 10，跨簇对不混入。"""
    clustered = seq([("a", 10, "stop"), ("a", 20, "stop"), ("a", 30, "stop"),
                     ("b", 30, "stop"), ("c", 30, "turn"), ("a", 40, "stop")])
    assoc = echo_lag_associations(clustered, min_support=1)
    same = assoc_of(assoc, "a", "stop", "a", "stop")
    assert same["lag"] == 10
    # a@10→a@20、a@20→a@30、a@30→a@40 三次都能算出 lag 10：
    # 同 tick 簇上的 b、c 事件没有打断 a 的相邻事件对。
    assert same["count"] == 3
    assert same["lag_counts"] == {10: 3}
    assert same["triggers"] == 3 and same["sources_total"] == 4
    assert same["consistency"] == pytest.approx(1.0)
    # 同 tick 簇上的 b、c 事件各自与 a@40 成对，但不与 a 的相邻对混成一条。
    assert assoc_of(assoc, "b", "stop", "a", "stop")["lag"] == 10
    assert assoc_of(assoc, "b", "stop", "a", "stop")["count"] == 1
    assert assoc_of(assoc, "c", "turn", "a", "stop")["lag"] == 10
    assert assoc_of(assoc, "a", "stop", "c", "turn") is None
    # 去掉同 tick 的两条事件后，同一对的 lag 与计数都不变。
    clean = seq([("a", 10, "stop"), ("a", 20, "stop"), ("a", 30, "stop"),
                 ("a", 40, "stop")])
    clean_same = assoc_of(echo_lag_associations(clean, min_support=1),
                          "a", "stop", "a", "stop")
    assert clean_same["lag"] == same["lag"] == 10
    assert clean_same["count"] == same["count"] == 3
    assert clean_same["consistency"] == same["consistency"] == pytest.approx(1.0)


def test_inserted_irrelevant_event_does_not_change_lag():
    """插入无关事件不改变同一签名对测到的 lag 与预测落点。"""
    base = seq([("aa", 10, "stop"), ("aa", 20, "stop"), ("aa", 30, "stop"),
                ("aa", 40, "stop"), ("aa", 50, "stop"), ("aa", 60, "stop"),
                ("aa", 70, "stop")])
    noisy = seq([("aa", 10, "stop"), ("bb", 15, "stop"), ("aa", 20, "stop"),
                 ("bb", 25, "turn"), ("aa", 30, "stop"), ("aa", 40, "stop"),
                 ("aa", 50, "stop"), ("aa", 60, "stop"), ("aa", 70, "stop")])
    key = ("aa", "stop", "aa", "stop")
    before = assoc_of(echo_lag_associations(base, min_support=1), *key)
    after = assoc_of(echo_lag_associations(noisy, min_support=1), *key)
    assert before["lag"] == after["lag"] == 10
    assert before["count"] == after["count"] == 6
    assert before["consistency"] == after["consistency"] == pytest.approx(1.0)
    # 无关事件自身也成对，但不改写 aa 签名对的 lag。
    assert assoc_of(echo_lag_associations(noisy, min_support=1),
                    "bb", "stop", "bb", "turn")["lag"] == 10
    one = echo_lag_prediction(noisy[0], None,
                              associations=echo_lag_associations(base, min_support=1),
                              events=noisy)
    assert (one["predicted_tick"], one["predicted_eid"], one["predicted_event"]) == \
        (20, "aa", "stop")


def test_entity_rename_and_insertion_order_do_not_change_associations():
    """实体改名与输入顺序不改变关联结构：同 lag、同计数、同一致性。"""
    events = seq([("aa", 10, "stop"), ("bb", 15, "stop"), ("aa", 20, "stop"),
                  ("bb", 25, "turn"), ("aa", 30, "stop"), ("aa", 40, "stop"),
                  ("bb", 45, "stop"), ("aa", 50, "stop")])
    original = echo_lag_associations(events, min_support=1)
    renamed = echo_lag_associations(
        [dict(e, eid={"aa": "cat", "bb": "dog"}[e["eid"]]) for e in events],
        min_support=1)
    assert [sig(a) for a in renamed] == [
        sig(a) for a in
        sorted([dict(a, source_eid={"aa": "cat", "bb": "dog"}[a["source_eid"]],
                     target_eid={"aa": "cat", "bb": "dog"}[a["target_eid"]])
                for a in original],
               key=lambda a: (a["after"], a["before"], a["target_eid"]))]
    # 输入顺序换成倒序，读数逐字相同。
    assert json.dumps(echo_lag_associations(list(reversed(events)), min_support=1),
                      sort_keys=True) == json.dumps(original, sort_keys=True)
    assert echo_split(list(reversed(events)))["dev"] == echo_split(events)["dev"]


def test_irregular_sequence_default_support_has_no_stop_turn_association():
    """不规则夹具在默认支持度下不出停止到转向的关联。"""
    events = event_echo_readings(irregular_history())
    assert events_ticks(events) == [
        2, 6, 8, 11, 15, 18, 23, 26, 27, 30, 32, 35, 39, 42, 47, 50, 51, 54,
        56, 59, 63, 66, 71]
    loose = echo_lag_associations(events)
    assert loose == [] or all(a["after"] == "turn" for a in loose), loose
    # 停止到转向的每个源事件只记一条最早响应：主 lag 也只出现 3 次，
    # 比例 3/7 = 0.4286 低于 0.7，所以在默认门槛下被剔除。
    stops = [e["tick"] for e in events if e["event"] == "stop"]
    turns = [e["tick"] for e in events if e["event"] == "turn"]
    earliest = {}
    for source in stops:
        same = [t - source for t in turns if 0 < t - source <= 12]
        if same:
            earliest[source] = min(same)
    counts = {}
    for lag in earliest.values():
        counts[lag] = counts.get(lag, 0) + 1
    assert sorted(counts.values()) == [1, 2, 3, 3, 3]
    assert len(counts) == 5 and len(earliest) == 12
    assert max(counts.values()) / len(earliest) == pytest.approx(3 / 12, abs=1e-6)
    assert 3 / 12 < 0.7
    # min_support 高于样本总数也不该成为通过手段：默认门槛已把它排除在外。
    assert echo_lag_associations(events, min_support=len(events) + 1) == []
    # wanderer 的转向到停止确实每次相隔 3 tick，这一条按新口径保留。
    kept = assoc_of(loose, "wanderer", "turn", "wanderer", "stop")
    assert kept is not None and kept["lag"] == 3
    assert kept["count"] == 10 and kept["consistency"] == pytest.approx(1.0)


def test_independent_sequences_do_not_pass_default_gate():
    """固定 seed 的独立序列在默认门槛下不出非空关联；读数由本件复算，不宣称显著性。"""
    seeds = (1000, 1001, 1002)
    for seed in seeds:
        events = independent_events(seed)
        assert echo_lag_associations(events) == [], seed
        # 若把 min_consistency 放宽到 0，就会出现偶然候选：门槛的作用可复算。
        assert echo_lag_associations(events, min_consistency=0.0) != [], seed
    # 门槛值 0.7 的校准读数：本件 3 个 seed 下没有一条比例达到 0.7。
    for seed in seeds:
        events = independent_events(seed)
        raw = echo_lag_associations(events, min_support=1, min_consistency=0.0)
        assert all(a["consistency"] < 0.7 for a in raw), seed


# ================= D 组：预测不借答案、评分只在一处 =================

def test_prediction_does_not_use_actual_next():
    """actual_next 取 None 与取伪事件：预测、actual 三键与偏差逐字相同。"""
    dev = seq([("x", 2, "stop"), ("x", 7, "turn"),
               ("x", 12, "stop"), ("x", 17, "turn"),
               ("x", 22, "stop"), ("x", 27, "turn"),
               ("x", 32, "stop"), ("x", 37, "turn")])
    assoc = echo_lag_associations(dev)
    current = event(40, "x", "stop")
    stream = [event(7, "x", "turn"), event(45, "x", "turn")]
    first = echo_lag_prediction(current, None, associations=assoc, events=stream)
    second = echo_lag_prediction(current, event(9999, "x", "turn"),
                                 associations=assoc, events=stream)
    assert (first["predicted_tick"], first["predicted_eid"],
            first["predicted_event"], first["lag"]) == (45, "x", "turn", 5)
    assert (second["predicted_tick"], second["predicted_eid"],
            second["predicted_event"], second["lag"]) == (45, "x", "turn", 5)
    # actual 三键统一取固定目标签名 (x, turn) 在源事件之后最早的一条：45 才是响应，
    # 7 在源事件之前；入参 actual_next 不进任何字段。
    for got in (first, second):
        assert (got["actual_tick"], got["actual_eid"], got["actual_event"]) == \
            (45, "x", "turn")
        assert got["hit"] is True and got["abs_tick_error"] == 0
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    # 流里没有该目标签名时，actual 三键一起为 None，偏差也是 None。
    lean = [event(7, "x", "turn"), event(45, "z", "stop")]
    third = echo_lag_prediction(current, event(9999, "x", "turn"),
                                associations=assoc, events=lean)
    assert third["predicted_tick"] == 45
    assert (third["actual_tick"], third["actual_eid"], third["actual_event"]) == \
        (None, None, None)
    assert third["abs_tick_error"] is None and third["hit"] is False


def test_prediction_and_evaluation_share_one_scoring_rule():
    """导出函数与评估路径对同一条预测给出同样的命中判定。"""
    dev = seq([("x", 2, "stop"), ("x", 7, "turn"),
               ("x", 12, "stop"), ("x", 17, "turn"),
               ("x", 22, "stop"), ("x", 27, "turn"),
               ("x", 32, "stop"), ("x", 37, "turn")])
    assoc = echo_lag_associations(dev)
    holdout = seq([("x", 100, "stop"), ("x", 105, "stop"), ("x", 110, "turn")])
    out = evaluate_event_echo(dev, holdout)
    direct = echo_lag_prediction(holdout[0], holdout[1], associations=assoc,
                                 events=holdout)
    row = out["lag_readings"][0]
    assert direct["predicted_tick"] == row["predicted_tick"] == 105
    assert direct["predicted_eid"] == row["predicted_eid"] == "x"
    assert direct["predicted_event"] == row["predicted_event"] == "turn"
    assert direct["hit"] is row["hit"] is False
    # 真实响应是 110 的转（105 上是停，签名不符），偏差按它算。
    assert (direct["actual_tick"], direct["actual_eid"],
            direct["actual_event"]) == (110, "x", "turn")
    assert (row["actual_tick"], row["actual_eid"], row["actual_event"]) == \
        (110, "x", "turn")
    assert direct["abs_tick_error"] == row["abs_tick_error"] == 5
    # 命中放行的那条也一样：类型与 eid 都对才算命中。
    direct_hit = echo_lag_prediction(holdout[1], holdout[2], associations=assoc,
                                     events=holdout)
    assert direct_hit["hit"] is out["lag_readings"][1]["hit"] is True
    assert direct_hit["predicted_eid"] == direct_hit["actual_eid"] == "x"
    assert direct_hit["predicted_event"] == direct_hit["actual_event"] == "turn"
    assert direct_hit["predicted_tick"] == direct_hit["actual_tick"] == 110


def test_both_models_share_trigger_set_and_signature():
    """两模型的触发集合、目标签名与覆盖率一致，只有 tick 落点不同。"""
    events = event_echo_readings(periodic_history())
    split = echo_split(events)
    out = evaluate_event_echo(split["dev"], split["holdout"])
    lag, base = out["lag_model"], out["no_delay_model"]
    assert lag["predictions"] == base["predictions"]
    assert lag["coverage"] == base["coverage"]
    lag_keys = [(r["tick"], r["eid"], r["event"]) for r in out["lag_readings"]
                if r["predicted_tick"] is not None]
    base_keys = [(r["tick"], r["eid"], r["event"]) for r in out["no_delay_readings"]
                 if r["predicted_tick"] is not None]
    assert lag_keys == base_keys
    for a, b in zip(out["lag_readings"], out["no_delay_readings"]):
        assert (a["predicted_eid"], a["predicted_event"]) == \
            (b["predicted_eid"], b["predicted_event"])
        # 两条路径共用同一个实际响应对象，actual 三键逐条相同。
        assert (a["actual_tick"], a["actual_eid"], a["actual_event"]) == \
            (b["actual_tick"], b["actual_eid"], b["actual_event"])
        # 命中判据也共用：同一个实际响应不能同时命中两个不同的预测 tick。
        assert not (a["hit"] and b["hit"])
    # scored 的差别只来自 lag 模型的预测先越过观察尾部：越过的那几行不可评分。
    beyond = [r for r in out["lag_readings"] if r["end_state"] == "beyond_window"]
    assert len(beyond) == 1
    assert lag["scored"] == base["scored"] - len(beyond)
    assert out["lag_readings"][-1]["end_state"] == "beyond_window"
    assert out["no_delay_readings"][-1]["end_state"] == "missed"
    assert out["no_delay_readings"][-1]["scored"] is True


# ================= E 组：输入不变量与边界 =================

def test_input_not_modified_and_output_sorted():
    history = two_entity_history()
    before = json.dumps(history, sort_keys=True)
    events = event_echo_readings(history)
    assert json.dumps(history, sort_keys=True) == before

    keys = [(e["tick"], e["eid"], e["event"]) for e in events]
    assert keys == sorted(keys)
    assert keys == [
        (2, "alpha", "stop"), (5, "beta", "turn"), (7, "alpha", "turn"),
        (9, "beta", "stop"), (12, "alpha", "stop"), (16, "beta", "turn"),
        (17, "alpha", "turn"), (20, "beta", "stop"), (22, "alpha", "stop"),
        (27, "alpha", "turn"), (27, "beta", "turn"), (31, "beta", "stop"),
        (32, "alpha", "stop"), (37, "alpha", "turn"), (42, "alpha", "stop"),
        (47, "alpha", "turn"), (52, "alpha", "stop"), (57, "alpha", "turn")]
    # 两个实体的事件都进了同一条事件流；同 tick 并列时按 eid 排序。
    assert {e["eid"] for e in events} == {"alpha", "beta"}
    assert [e["eid"] for e in events if e["tick"] == 27] == ["alpha", "beta"]

    snapshot = json.dumps(events, sort_keys=True)
    echo_lag_associations(events)
    echo_split(events)
    evaluate_event_echo(events[:12], events[12:])
    assert json.dumps(events, sort_keys=True) == snapshot
    assert json.dumps(history, sort_keys=True) == before


def test_entities_filter_selects_stream():
    history = two_entity_history()
    alpha = event_echo_readings(history, entities=["alpha"])
    assert {e["eid"] for e in alpha} == {"alpha"}
    assert events_ticks(alpha) == [2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57]
    assert [e["eid"] for e in event_echo_readings(history, entities=["alpha", "beta"])] \
        == [e["eid"] for e in event_echo_readings(history)]


def test_lag_from_real_ticks_with_missing_frames():
    """缺帧不插值：延迟按真实 tick 差算，不按帧数算。"""
    history = drift_history()
    assert [r["tick"] for r in history] == [1, 2, 6, 7, 8, 9, 13]
    # 停止对跨 4 个真实 tick、只漂移 0.4，速度 0.1 低于 stop_speed 0.2；
    # 若按 dt=1 算，同样的速度判定会得到 0.4，判不出停止。
    assert ProcessThresholds().stop_speed == 0.2
    events = motion_events(history, "drifter")
    assert [(e["tick"], e["event"], e["dt"]) for e in events] == [
        (6, "stop", 4), (7, "turn", 1), (13, "stop", 4)]
    assert events[0]["moved"] == pytest.approx(0.4)
    assert events[0]["speed"] == pytest.approx(0.1)
    assert events[0]["speed"] <= ProcessThresholds().stop_speed
    assert events[0]["moved"] / 1 > ProcessThresholds().stop_speed
    assert events[1]["angle_deg"] == pytest.approx(90.0, abs=1e-6)

    stream = event_echo_readings(history)
    assert [(e["tick"], e["event"], e["dt"], e["lag_from_prev"]) for e in stream] == [
        (6, "stop", 4, 0), (7, "turn", 1, 1), (13, "stop", 4, 6)]
    assoc = echo_lag_associations(stream, min_support=1)
    stop_turn = assoc_of(assoc, "drifter", "stop", "drifter", "turn")
    assert stop_turn["lag"] == 1 and stop_turn["count"] == 1
    # 真实延迟取 tick 差：停止对落在 tick 6 上（其样本 dt 是 4），
    # 转向在 tick 7，下一次停止在 tick 13，延迟 6 只能由真实 tick 差算出。
    assert stream[2]["tick"] - stream[1]["tick"] == 6
    assert assoc_of(assoc, "drifter", "turn", "drifter", "stop")["lag"] == 6


def test_lag_outside_max_lag_is_excluded():
    events = event_echo_readings(periodic_history())
    assert echo_lag_associations(events, max_lag=4) == []
    assert echo_lag_associations(events, max_lag=5)[0]["lag"] == 5


def test_prediction_without_association_is_none():
    events = event_echo_readings(irregular_history())
    assert echo_lag_associations(events, min_support=len(events) + 1) == []
    assert echo_lag_prediction(events[0], events[1], associations=[],
                               events=events) is None
    # 学到的事件图对里转向之后只有转向：转向事件之后的预测返回 None。
    events = event_echo_readings(periodic_history())
    assoc = echo_lag_associations(events)
    assert echo_lag_prediction({"tick": 40, "eid": "courier", "event": "go"}, None,
                               associations=assoc, events=events) is None
    # 有关联时按 lag 给出预测 tick、目标签名与命中标记。
    pred = echo_lag_prediction(events[0], events[1], associations=assoc,
                               events=events)
    assert pred["after"] == "stop" and pred["lag"] == 5
    assert pred["predicted_tick"] == 7 and pred["actual_tick"] == 7
    assert pred["predicted_event"] == "turn" and pred["predicted_eid"] == "courier"
    assert pred["hit"] is True and pred["abs_tick_error"] == 0
    # 目标签名在流里出现但 tick 不符：如实记落空，偏差按该响应算。
    off = echo_lag_prediction({"tick": 3, "eid": "courier", "event": "stop"},
                              {"tick": 8, "eid": "courier", "event": "turn"},
                              associations=assoc, events=events)
    assert off["predicted_tick"] == 8
    assert off["actual_tick"] == 7 and off["actual_event"] == "turn"
    assert off["hit"] is False and off["abs_tick_error"] == 1
    # 目标签名在流里一次都没出现：actual 三键一起为 None，偏差也是 None，
    # 且预测 tick 没超出流尾，属真实落空而不是超窗未决。
    lean = [event(2, "courier", "stop"), event(12, "courier", "stop")]
    never = echo_lag_prediction({"tick": 3, "eid": "courier", "event": "stop"},
                                {"tick": 8, "eid": "courier", "event": "turn"},
                                associations=assoc, events=lean)
    assert never["predicted_tick"] == 8 and never["hit"] is False
    assert (never["actual_tick"], never["actual_eid"], never["actual_event"]) == \
        (None, None, None)
    assert never["abs_tick_error"] is None
    assert never["end_state"] == "missed" and never["scored"] is True


def test_repeat_same_input_same_output():
    history = two_entity_history()
    first = event_echo_report(history)
    second = event_echo_report(history)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.dumps(echo_lag_associations(event_echo_readings(history)),
                      sort_keys=True) == \
        json.dumps(echo_lag_associations(event_echo_readings(history)), sort_keys=True)


def test_module_stays_read_only():
    with open(MODULE_PATH, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"\.perceive\(|\.generate\(|\.execute\(", source)
    assert "scene_simulator" not in source
    assert "import random" not in source


# ================= F 组：隔离变异 =================

def load_mutant(name):
    """把隔离副本当独立模块加载；副本不存在时跳过（证据目录由本组提交）。"""
    path = os.path.join(WORK_DIR, name + ".py")
    if not os.path.exists(path):
        pytest.skip("隔离副本缺失：%s" % path)
    spec = importlib.util.spec_from_file_location("echo_mutant_" + name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 解析字符串注解时要能在 sys.modules 里找到本模块，否则加载失败。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DEV_MUT = [("x", 2, "stop"), ("x", 7, "turn"), ("x", 12, "stop"),
           ("x", 17, "turn"), ("x", 22, "stop"), ("x", 27, "turn"),
           ("x", 32, "stop"), ("x", 37, "turn")]


def test_mutation_dropping_eid_in_hit_rule_turns_new_assertion_red():
    """M1：命中判据去掉 eid 且响应只按 event 筛 ⇒ eid 不符的响应被算成命中。"""
    mutant = load_mutant("wp_m1_no_eid")
    dev = seq(DEV_MUT)
    assoc = assoc_of(echo_lag_associations(dev, min_support=1),
                     "x", "stop", "x", "turn")
    # 关联的目标 eid 换成 y：tick 与 event 都跟实际响应相同，只有 eid 不同。
    holdout = seq([("x", 100, "stop"), ("x", 105, "turn"), ("x", 108, "stop")])
    assoc_y = [dict(assoc, target_eid="y")]
    correct = echo_lag_prediction(holdout[0], holdout[1], associations=assoc_y,
                                  events=holdout)
    broken = mutant.echo_lag_prediction(holdout[0], holdout[1],
                                        associations=assoc_y, events=holdout)
    assert (correct["predicted_tick"], correct["predicted_eid"],
            correct["predicted_event"]) == (105, "y", "turn")
    # 正确实现：固定目标签名 (y, turn) 在流里不存在，actual 三键一起为 None。
    assert (correct["actual_tick"], correct["actual_eid"],
            correct["actual_event"]) == (None, None, None)
    assert correct["hit"] is False
    # 变异实现：响应退化成全流下一条 (x, turn)，命中比较又去掉 eid ⇒ 误判命中。
    assert broken["predicted_tick"] == 105 and broken["hit"] is True
    assert broken["predicted_eid"] == "y" and broken["actual_eid"] == "x"
    # 目标 eid 相同的正常例在两条路径上都命中，差别只出在 eid 不符的那条。
    out_ok = evaluate_event_echo(dev, holdout)
    assert out_ok["lag_model"]["hits"] == 1
    real_row = out_ok["lag_readings"][0]
    assert real_row["predicted_eid"] == "x" and real_row["actual_eid"] == "x"
    assert real_row["hit"] is True


def test_mutation_adjacent_pairing_breaks_insertion_invariance():
    """M2：配对退化成相邻事件 ⇒ 插入无关事件改变同一签名对的计数。"""
    mutant = load_mutant("wp_m2_adjacent")
    base = seq([("aa", t, "stop") for t in (10, 20, 30, 40, 50, 60, 70)])
    noisy = seq([("aa", 10, "stop"), ("bb", 15, "stop"), ("aa", 20, "stop"),
                 ("bb", 25, "turn"), ("aa", 30, "stop"), ("aa", 40, "stop"),
                 ("aa", 50, "stop"), ("aa", 60, "stop"), ("aa", 70, "stop")])
    key = ("aa", "stop", "aa", "stop")
    correct_base = assoc_of(echo_lag_associations(base, min_support=1), *key)
    correct_noisy = assoc_of(echo_lag_associations(noisy, min_support=1), *key)
    assert correct_base["lag"] == correct_noisy["lag"] == 10
    assert correct_base["count"] == correct_noisy["count"] == 6
    assert correct_noisy["consistency"] == pytest.approx(1.0)
    broken_base = assoc_of(mutant.echo_lag_associations(base, min_support=1), *key)
    broken_noisy = assoc_of(mutant.echo_lag_associations(noisy, min_support=1), *key)
    assert broken_base["lag"] == 10 and broken_base["count"] == 6
    # 相邻配对下，插入的 bb@15 把 aa@10 的配对对象换成 bb，aa@20 换成 bb@25：
    # 该签名对的计数从 6 掉到 4，一致性跌到 4/6，被默认门槛整条剔除。
    assert broken_noisy is None or broken_noisy["count"] != broken_base["count"]
    loose = assoc_of(mutant.echo_lag_associations(noisy, min_support=1,
                                                  min_consistency=0.0), *key)
    assert loose["lag"] == 10 and loose["count"] == 4
    assert loose["triggers"] == 6
    assert loose["consistency"] == pytest.approx(4 / 6)


def test_mutation_count_split_breaks_tick_boundary():
    """M3：切分去掉同 tick 组回退 ⇒ 同 tick 事件被劈开且边界不再严格跨 tick。"""
    mutant = load_mutant("wp_m3_count_split")
    events = seq([("aa", 10, "stop"), ("aa", 20, "stop"), ("aa", 30, "stop"),
                  ("bb", 30, "stop"), ("cc", 30, "turn"), ("aa", 40, "stop")])
    mine = mutant.echo_split(events, 0.5)
    # 去掉同 tick 组回退后，下标 3 正好落在 tick 30 组内部，组被劈成 1 + 2 条。
    assert mine["dev_events"] == 3 and mine["holdout_events"] == 3
    assert len([e for e in mine["dev"] if e["tick"] == 30]) == 1
    assert len([e for e in mine["holdout"] if e["tick"] == 30]) == 2
    assert mine["dev_last_tick"] == mine["holdout_first_tick"] == 30
    assert not (mine["dev_last_tick"] < mine["holdout_first_tick"])
    ok = echo_split(events, 0.5)
    assert ok["dev_events"] == 2 and ok["holdout_events"] == 4
    assert ok["boundary_tick"] == 30
    assert ok["dev_last_tick"] == 20 and ok["holdout_first_tick"] == 30
    assert ok["dev_last_tick"] < ok["holdout_first_tick"]
    assert not [e for e in ok["dev"] if e["tick"] == 30]
    assert len([e for e in ok["holdout"] if e["tick"] == 30]) == 3


def test_mutation_split_reversed_changes_dev_holdout_counts():
    """M4：颠倒切分方向 ⇒ 留出段变成流头，dev 与留出条数对调。"""
    mutant = load_mutant("wp_m4_split_reversed")
    events = event_echo_readings(periodic_history())
    ticks = [e["tick"] for e in events]
    ok = echo_split(events, 0.3)
    assert ok["dev_events"] == 14 and ok["holdout_events"] == 6
    assert ok["boundary_tick"] == 72
    # 留出段必须取流尾的 6 条。
    assert [e["tick"] for e in ok["holdout"]] == ticks[14:]
    broken = mutant.echo_split(events, 0.3)
    assert broken["dev_events"] == 6 and broken["holdout_events"] == 14
    assert broken["dev_events"] != ok["dev_events"]
    assert [e["tick"] for e in broken["dev"]] == ticks[:6]
    assert broken["boundary_tick"] == ticks[6]


def test_mutation_next_event_scoring_breaks_signature_response():
    """M5：响应退化成全流下一事件 ⇒ 无关实体事件插入后命中与 MAE 都变。"""
    mutant = load_mutant("wp_m5_next_event_scoring")
    dev = seq([("x", 10, "stop"), ("y", 15, "turn"), ("x", 20, "stop"),
               ("y", 25, "turn"), ("x", 30, "stop"), ("y", 35, "turn"),
               ("x", 40, "stop"), ("y", 45, "turn")])
    noisy = seq([("x", 100, "stop"), ("w", 102, "turn"), ("y", 105, "turn"),
                 ("x", 110, "stop"), ("w", 112, "turn"), ("y", 115, "turn")])
    plain = seq([("x", 100, "stop"), ("y", 105, "turn"), ("x", 110, "stop"),
                 ("y", 115, "turn")])
    ok = evaluate_event_echo(dev, noisy)["lag_model"]
    assert ok["hits"] == 3 and ok["abs_tick_errors"] == [0, 0, 0]
    assert ok["mean_abs_tick_error"] == 0.0
    broken = mutant.evaluate_event_echo(dev, noisy)["lag_model"]
    # 变异实现把 w@102 当成 x@100 的响应：预测 105 落空，偏差按 102 算成 3。
    assert broken["hits"] == 1 and broken["abs_tick_errors"] == [3, 0, 3]
    assert broken["mean_abs_tick_error"] == 2.0
    assert broken["hits"] != ok["hits"]
    assert broken["mean_abs_tick_error"] != ok["mean_abs_tick_error"]
    # 无关事件插在 x@100 与 y@105 之间时变异实现给出 3 的偏差；取同一条流
    # 但去掉 w 两条事件，变异实现的偏差回到 0，证明读数被无关事件改写。
    broken_plain = mutant.evaluate_event_echo(dev, plain)["lag_model"]
    assert broken_plain["hits"] == 3
    assert broken_plain["abs_tick_errors"] == [0, 0, 0]
    assert broken_plain["mean_abs_tick_error"] == 0.0


def test_mutation_coverage_scored_formula_turns_red():
    """M6：coverage 用 scored 除事件数 ⇒ 越过观察尾部的预测被算进覆盖率。"""
    mutant = load_mutant("wp_m6_coverage_scored")
    events = event_echo_readings(periodic_history())
    split = echo_split(events)
    ok = evaluate_event_echo(split["dev"], split["holdout"])["lag_model"]
    assert ok["predictions"] == 6 and ok["scored"] == 5
    # 正确口径：coverage = predictions ÷ events = 1.0。
    assert ok["coverage"] == pytest.approx(ok["predictions"] / ok["events"])
    assert ok["coverage"] == pytest.approx(1.0)
    assert ok["coverage"] != ok["scored_rate"]
    broken = mutant.evaluate_event_echo(split["dev"], split["holdout"])["lag_model"]
    assert broken["coverage"] == pytest.approx(broken["scored"] / broken["events"])
    assert broken["coverage"] == pytest.approx(5 / 6)
    assert broken["coverage"] != ok["coverage"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
