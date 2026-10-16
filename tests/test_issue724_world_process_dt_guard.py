# -*- coding: utf-8 -*-
"""test_issue724_world_process_dt_guard —— `world_process` 的 `dt <= 0` 采样对守卫。

缺陷（lingshu#724 · P1）
--------------------------------------------------------------------------
`motion_events` 内 `speed = moved / cur["dt"]` 对 `dt == 0` 直接除零崩溃；
`event_echo_readings` / `event_echo_report` 复用同一函数，故 **3 个公开引擎
入口**（`core._world_process_impl` 的 `events` / `echo` 两个 MCP 动作）都可被
「显式喂重复 tick 的 history」打崩。真实数据源 `_world_process_history` 对
显式入参 `p.get("history")` 零校验直接透传 ⇒ 模块 docstring 声称的
「history 按 tick 递增」契约在公开接口处不成立。

实现侧口径（本守卫的判据）
--------------------------------------------------------------------------
`dt <= 0` 的采样对**不构成运动证据**，一律**跳过该对**（不判事件、不更新参考
方向），**不是** `max(dt, eps)` 之类把物理量静默改成"看起来合理"的值。
依据：
  · 同仓既有同族口径逐字一致 —— `lingshu/world/motion.py::_estimate_detail`
    对位移对做 `dt = t1 - t0; if dt <= 0: continue`（长间隔降权、零/负间隔丢弃）；
  · `track_samples` 仍**如实**返回 `dt=0` / `dt<0` 的采样读数（那是真实观测的
    tick 差，不是待删的坏点）——故「跳过」只发生在事件判定层，不污染采样读数。
    这一点正是本守卫 G4 钉住的：修法不是把 dt 改值，而是不拿无定义的速度判事件。
  · 反向（tick 回退，dt<0）同样必须跳过：旧码不崩，但会把 `moved / 负数` 的
    **负速度**当成 `speed <= stop_speed` 成立 ⇒ 凭空造一条 `stop` 事件并写出
    负 speed（静默错值，比崩溃更隐蔽）。

守卫用例
--------------------------------------------------------------------------
G1 正对照（量具能测到已知正确形态）：单调递增 tick 的「走→停→停→转」轨迹
   必须报出 2 条 stop + 1 条 turn（改前也过 —— 防"恒空/恒崩"假绿）。
G2 正对照（量具的敏感度）：`track_samples` 必须**看得见**重复 tick 的 dt=0
   （证明 G3 的输入确实踩到退化 dt，而不是探针没造出来）。
G3 缺陷态 A：`dt == 0`（相邻两帧同 tick）⇒ 三入口均**不抛**且不凭空造事件。
G4 缺陷态 B：`dt == 0` 的那一对**不得**被判成 stop（"把 dt=0 当零速度"这种
   错修法会造出 tick=5 的假 stop —— 本组钉住"跳过"而非"改值"）。
G5 缺陷态 C：tick 回退（dt<0）⇒ 不报事件（旧码报 stop 且 speed 为**负**）。
G6 范围控制：`dt == 1` 的合法采样对事件照旧（跳过只针对 dt<=0，未伤正常路径）。
G7 影响面可达性：issue 点名的**公开引擎入口**（`core.py` 的 `events` / `echo`
   两个动作）在重复 tick 的 history 上不再崩 —— 修复必须在**真实调用路径**上
   成立，而不只是模块级函数可用。附带钉住**消费者读数**：`speed` 键的唯一读者
   就是 `motion_events` 自己的返回体（`event_echo_readings` 只复制 `dt`，不读
   `speed`）⇒ 「跳过该对」不会让任何下游读到被篡改的速度值。

运行（lingshu 仓根）：
    python -X utf8 -m pytest tests/test_issue724_world_process_dt_guard.py -q --no-header
退出码：0 = 全过；非 0 = 有断言失败。
"""
from __future__ import annotations

from lingshu.core.core import SpacetimeMemoryEngine
from lingshu.world import register_world_process
from lingshu.world.world_process import (
    ProcessThresholds,
    event_echo_readings,
    event_echo_report,
    motion_events,
    track_samples,
)

#: 相邻两帧同 tick（dt=0）——第 1、2 帧 tick 均为 1
DUP_HIST = [{"tick": 1, "entities": {"a": [0.0, 0.0, 0.0]}},
            {"tick": 1, "entities": {"a": [1.0, 0.0, 0.0]}},
            {"tick": 2, "entities": {"a": [2.0, 0.0, 0.0]}}]

#: 连续三帧同 tick + 一个合法帧：合法帧上有一个真实停止（0.1 ≤ stop_speed）
DUP3_HIST = [{"tick": 5, "entities": {"a": [0.0, 0.0, 0.0]}},
             {"tick": 5, "entities": {"a": [0.5, 0.0, 0.0]}},
             {"tick": 5, "entities": {"a": [0.5, 0.0, 0.0]}},
             {"tick": 6, "entities": {"a": [0.6, 0.0, 0.0]}}]

#: tick 回退（dt = 3 - 9 = -6 < 0）
REWIND_HIST = [{"tick": 9, "entities": {"a": [0.0, 0.0, 0.0]}},
               {"tick": 3, "entities": {"a": [1.0, 0.0, 0.0]}}]

#: 单调递增 tick 的「走→停→停→转」：t2 走、t3/t4 停、t5 转 90°、t6 同向
MONO_HIST = [{"tick": 1, "entities": {"a": [0.0, 0.0, 0.0]}},
             {"tick": 2, "entities": {"a": [1.0, 0.0, 0.0]}},
             {"tick": 3, "entities": {"a": [1.0, 0.0, 0.0]}},
             {"tick": 4, "entities": {"a": [1.0, 0.0, 0.0]}},
             {"tick": 5, "entities": {"a": [1.0, 0.0, 1.0]}},
             {"tick": 6, "entities": {"a": [1.0, 0.0, 2.0]}}]


def _assert_hist_usable(hist, eid="a"):
    """硬性前置：输入必须非空且真有 >= 2 个采样（防 for 循环空转成假绿）。"""
    assert hist, "守卫输入不得为空"
    samples = track_samples(hist, eid)
    assert len(samples) >= 2, ("采样数须 >= 2（否则配对循环空转、断言恒真）：%r"
                               % (samples,))


def test_724_positive_control_monotonic_history_reports_known_events():
    """G1 正对照：单调递增 tick 上量具测得到**已知正确形态**（停/转各就各位）。"""
    _assert_hist_usable(MONO_HIST)
    events = motion_events(MONO_HIST, "a")
    assert [(e["tick"], e["event"]) for e in events] == [
        (3, "stop"), (4, "stop"), (5, "turn")], (
        "#724 正对照失败：正常轨迹的事件序列被改动（改前即 [stop@3, stop@4, turn@5]）"
        "，量具已不可信：%r" % (events,))
    assert all(e["dt"] == 1 for e in events), events
    # 三入口在正常轨迹上同样可用（防止"修一处崩一处"）：echo 是 motion_events 的
    # 合流，故事件身份必须逐条一致（eid 补上、lag_from_prev 按真实 tick 计）。
    stream = event_echo_readings(MONO_HIST)
    assert [(e["tick"], e["eid"], e["event"]) for e in stream] == [
        (3, "a", "stop"), (4, "a", "stop"), (5, "a", "turn")], stream
    report = event_echo_report(MONO_HIST)
    assert report["kind"] == "event_echo_report", report.get("kind")
    assert report["events"] == stream, (report["events"], stream)


def test_724_positive_control_track_samples_sees_dt_zero():
    """G2 正对照：`track_samples` 如实报出重复 tick 的 dt=0（G3 的输入确实退化）。"""
    _assert_hist_usable(DUP_HIST)
    dts = [(s["tick"], s["dt"]) for s in track_samples(DUP_HIST, "a")]
    assert dts == [(1, 0), (1, 0), (2, 1)], (
        "#724 量具敏感度失败：重复 tick 的采样读数应为 [(1,0),(1,0),(2,1)]"
        "（dt=0 必须**如实**出现在采样读数里，修法不许改采样层）：%r" % (dts,))
    _assert_hist_usable(REWIND_HIST)
    assert [s["dt"] for s in track_samples(REWIND_HIST, "a")] == [0, -6], (
        "#724 tick 回退的 dt 必须如实为负：%r"
        % (track_samples(REWIND_HIST, "a"),))


def test_724_dt_zero_does_not_crash_any_public_entry():
    """G3 缺陷态 A：dt=0 ⇒ 三入口不抛（改前 ZeroDivisionError）。"""
    _assert_hist_usable(DUP_HIST)
    _assert_hist_usable(DUP3_HIST)
    for label, hist in (("DUP", DUP_HIST), ("DUP3", DUP3_HIST)):
        events = motion_events(hist, "a")                     # 改前：ZeroDivisionError
        assert isinstance(events, list), (label, events)
        stream = event_echo_readings(hist)                     # 改前：ZeroDivisionError
        assert isinstance(stream, list), (label, stream)
        report = event_echo_report(hist)                       # 改前：ZeroDivisionError
        assert report["kind"] == "event_echo_report", (label, report.get("kind"))


def test_724_dt_zero_pair_is_skipped_not_valued():
    """G4 缺陷态 B：dt=0 的对**跳过**（不判 stop / 不更新参考方向），合法对照判。"""
    _assert_hist_usable(DUP3_HIST)
    events = motion_events(DUP3_HIST, "a")
    # tick=5 的三帧之间没有时间间隔 ⇒ 不得凭空造事件（"dt=0 当零速度"会造假 stop）
    assert [e["tick"] for e in events] == [6], (
        "#724 dt=0 的对被判成了事件（把 dt=0 当零速度/静默改值的错修法）：%r"
        % (events,))
    assert [e["event"] for e in events] == ["stop"], events
    assert events[0]["dt"] == 1, events
    # 真速度必须按真实 dt 算（0.1 / 1 = 0.1），不是被改过的 dt 算出来的
    assert events[0]["speed"] == 0.1, events

    # DUP_HIST：dt=0 对被跳过，剩下那对速度 1.0 > stop_speed ⇒ 无事件
    assert motion_events(DUP_HIST, "a") == [], (
        "#724 重复 tick 历史不得报出任何事件：%r" % (motion_events(DUP_HIST, "a"),))


def test_724_rewound_tick_reports_no_event_and_no_negative_speed():
    """G5 缺陷态 C：tick 回退（dt<0）不得报事件（改前报 stop 且 speed 为负）。"""
    _assert_hist_usable(REWIND_HIST)
    events = motion_events(REWIND_HIST, "a")
    assert events == [], (
        "#724 dt<0 的对必须跳过：旧码 `moved / 负数` 得负速度，而"
        " `speed <= stop_speed` 对负数成立 ⇒ 凭空报 stop 并写出负 speed：%r"
        % (events,))
    assert event_echo_readings(REWIND_HIST) == [], event_echo_readings(REWIND_HIST)
    for e in event_echo_report(REWIND_HIST)["events"]:
        assert e["speed"] if "speed" in e else True, e


def test_724_range_control_normal_dt_pairs_unchanged():
    """G6 范围控制：dt==1 的正常对事件照旧（跳过只针对 dt<=0）。"""
    _assert_hist_usable(MONO_HIST)
    th = ProcessThresholds()
    assert th.stop_speed == 0.2 and th.turn_deg == 45.0, th   # 阈值未被本修改动
    events = motion_events(MONO_HIST, "a")
    assert len(events) == 3, (
        "#724 范围失控：dt=1 的合法对也被跳过（本修只许跳过 dt<=0）：%r" % (events,))


def test_724_public_engine_actions_survive_duplicate_ticks():
    """G7 影响面：`events` / `echo` 两个公开引擎动作在重复 tick 上不再崩。"""
    eng = SpacetimeMemoryEngine(":memory:")
    try:
        register_world_process(eng)
        assert eng.world_process("events", {"history": DUP_HIST, "eid": "a"}) == {
            "status": "ok", "eid": "a", "events": []}, (
            "#724 events 公开动作在重复 tick 上未收口（改前进程崩）")
        got = eng.world_process("echo", {"history": DUP_HIST})
        assert got["status"] == "ok", got
        assert got["report"]["kind"] == "event_echo_report", got.get("report")
        assert got["report"]["events"] == [], got["report"]["events"]
        # 反向对照：合法历史经同一入口仍给出正确事件（入口没被"修成恒空"）
        mono = eng.world_process("events", {"history": MONO_HIST, "eid": "a"})
        assert [(e["tick"], e["event"]) for e in mono["events"]] == [
            (3, "stop"), (4, "stop"), (5, "turn")], mono
    finally:
        eng.close()


def test_724_speed_has_no_downstream_reader_so_skipping_is_safe():
    """消费者读数：`speed` 只由 `motion_events` 返回；echo 流只复制 `dt`。"""
    _assert_hist_usable(MONO_HIST)
    events = motion_events(MONO_HIST, "a")
    assert all("speed" in e for e in events if e["event"] == "stop"), events
    # echo 流字段里没有 speed（唯一读者是 motion_events 的调用方）
    for item in event_echo_readings(MONO_HIST):
        assert "speed" not in item, item
        assert set(item) == {"tick", "eid", "event", "dt", "lag_from_prev"}, item
