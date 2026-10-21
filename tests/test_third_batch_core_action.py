# -*- coding: utf-8 -*-
"""F08 核心行动门面：`core.seven_layer_loop` 的 configure_action / clear_action —— 守卫

约定（`docs/second_third_batch_plan.md`「公共行动接口约定」）：C 侧
`SevenLayerLoop.configure_action(entity_id, target, step_size=1.0,
policy="predictive", fixed_action="east")`（target = 目标实体 id 或三维坐标）与
`clear_action()`；A 侧在核心门面新增同名动作，把参数**原样转发**，并**不改变**原
observation policy（L7 观察选择策略）的含义——两者是两件事。

断言组（把门面改成"吞参/自造默认/改观察策略"的形态 ⇒ 对应组红）：
  A1 转发：五个参数逐个到达 C 侧方法（名字与值都不许被改写/丢弃）。
  A2 缺省：只给 entity_id/target ⇒ step_size=1.0、policy="predictive"、
     fixed_action="east"（与约定签名逐字一致）。
  A3 target **原样**转发：list/tuple/str 的形态与值都不被门面改写——含 bool 元素
     （`float(True)==1.0` 的预转换会把 bool 洗成合法数值，正是 R6 返工的缺陷形态）。
  A4 门面级必填校验：缺 entity_id / 空 entity_id / 缺 target ⇒ error，
     且**一条都不转发**（不静默用默认目标）。值的合法性交给 C（见 A10）。
  A5 未就绪：C 侧方法未实现 ⇒ status="sll_action_not_ready"（不装作成功）。
  A6 观察策略不受影响：门面不把行动 policy 写进 `self._sll.policy`。
  A7 clear_action 转发。
  A8 真实闭环：C 侧实现落地后门面直达真实 `SevenLayerLoop`（未落地时显式 skip——
     本件不改 C 文件，门面测试不替 C 造实现）。
  A10 原样参数抵达 C 的 fail-closed 校验（R6 返工新增）：bool 坐标 / bool 步长 /
     None 步长 / 非数步长 / 非正步长 / 非三元或非数坐标 / 未知 target / 未知 policy /
     未知动作 ⇒ status=error + reason="action_config_rejected"，**不把 invalid 转
     valid、不让转换异常穿透**；已生效配置、待消费动作、实体位置、观察策略全不污染；
     正常数值（含 int 步长）与实体 ID 仍可用。

运行（仓根）：python -X utf8 -m pytest tests/test_third_batch_core_action.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


class _LoopStub:
    """最小 C 侧替身：只记录转发进来的参数，便于逐字比对。"""

    def __init__(self, policy: str = "curiosity"):
        self.policy = policy
        self.calls = []
        self.clear_calls = 0

    def configure_action(self, entity_id, target, step_size=1.0,
                         policy="predictive", fixed_action="east"):
        self.calls.append({"entity_id": entity_id, "target": target,
                           "step_size": step_size, "policy": policy,
                           "fixed_action": fixed_action})
        return {"configured": entity_id}

    def clear_action(self):
        self.clear_calls += 1
        return {"cleared": True}


class _RejectingLoop(_LoopStub):
    """C 侧 fail-closed 替身：不合法配置抛 ValueError（配置不变）。"""

    def configure_action(self, entity_id, target, step_size=1.0,
                         policy="predictive", fixed_action="east"):
        if policy not in ("predictive", "fixed"):
            raise ValueError("未知行动 policy %r" % (policy,))
        return super().configure_action(entity_id, target, step_size=step_size,
                                        policy=policy, fixed_action=fixed_action)


class _BareLoop:
    """未实现行动接口的 SevenLayerLoop 替身（A5）。"""

    def __init__(self):
        self.policy = "curiosity"


def _engine(loop=None):
    e = SpacetimeMemoryEngine(":memory:")
    if loop is not None:
        e._sll = loop          # 直接放替身/真实闭环，绕过惰性构造
    return e


def test_a1_params_forwarded_verbatim():
    stub = _LoopStub()
    e = _engine(stub)
    try:
        r = e.seven_layer_loop("configure_action", {
            "entity_id": "walker-1", "target": "goal-9", "step_size": 2.5,
            "policy": "predictive", "fixed_action": "west"})
        assert r["status"] == "ok", r
        assert r["action_config"] == {"configured": "walker-1"}
        assert stub.calls == [{"entity_id": "walker-1", "target": "goal-9",
                               "step_size": 2.5, "policy": "predictive",
                               "fixed_action": "west"}], stub.calls
    finally:
        e.store.conn.close()


def test_a2_defaults_match_agreed_signature():
    stub = _LoopStub()
    e = _engine(stub)
    try:
        r = e.seven_layer_loop("configure_action",
                               {"entity_id": "walker-1", "target": [1, 1.5, 2]})
        assert r["status"] == "ok", r
        assert len(stub.calls) == 1
        assert stub.calls[0]["step_size"] == 1.0
        assert stub.calls[0]["policy"] == "predictive"
        assert stub.calls[0]["fixed_action"] == "east"
    finally:
        e.store.conn.close()


def test_a3_target_forwarded_verbatim_without_type_rewrite():
    """A3：target 原样转发——list/tuple/str 的形态与值都不被门面改写（含 bool 元素）。

    R6 返工：旧门面 `tuple(float(v) for v in target)` / `str(target)` 会改写形态，
    且 `float(True)==1.0` 把 bool 坐标洗成合法数值后转发，C 侧 fail-closed 看不到原值。
    """
    stub = _LoopStub()
    e = _engine(stub)
    try:
        e.seven_layer_loop("configure_action",
                           {"entity_id": "walker-1", "target": [1, 2, 3]})
        e.seven_layer_loop("configure_action",
                           {"entity_id": "walker-1", "target": (4.5, 5.5, 6.5)})
        e.seven_layer_loop("configure_action",
                           {"entity_id": "walker-1", "target": "goal-9"})
        e.seven_layer_loop("configure_action",
                           {"entity_id": "walker-1", "target": [True, 1.5, 5]})
        assert type(stub.calls[0]["target"]) is list, stub.calls[0]
        assert stub.calls[0]["target"] == [1, 2, 3], stub.calls[0]
        assert type(stub.calls[1]["target"]) is tuple, stub.calls[1]
        assert stub.calls[1]["target"] == (4.5, 5.5, 6.5), stub.calls[1]
        assert stub.calls[2]["target"] == "goal-9", stub.calls[2]
        assert stub.calls[3]["target"][0] is True, (
            "bool 坐标被门面洗成了数值（float(True)==1.0）：%r" % (stub.calls[3],))
    finally:
        e.store.conn.close()


def test_a4_required_fields_are_rejected_without_forwarding():
    """A4：门面级必填（缺/空 entity_id、缺 target）⇒ error 且一条都不转发。

    值的合法性（坐标非三元/非数等）由 C 侧裁决，见 A10。
    """
    stub = _LoopStub()
    e = _engine(stub)
    try:
        bad_params = [{"target": "goal-9"},                       # 缺 entity_id
                      {"entity_id": "walker-1"},                  # 缺 target
                      {"entity_id": "", "target": "goal-9"},      # 空 entity_id
                      {"entity_id": "walker-1", "target": None}]  # target 显式 None
        for params in bad_params:
            r = e.seven_layer_loop("configure_action", params)
            assert r["status"] == "error", (params, r)
        assert stub.calls == [], "校验失败的入参不得被转发：%r" % (stub.calls,)
    finally:
        e.store.conn.close()


def test_a5_missing_methods_report_not_ready():
    e = _engine(_BareLoop())
    try:
        r = e.seven_layer_loop("configure_action",
                               {"entity_id": "walker-1", "target": "goal-9"})
        assert r["status"] == "sll_action_not_ready", r
        r2 = e.seven_layer_loop("clear_action", {})
        assert r2["status"] == "sll_action_not_ready", r2
        assert "configure_action" in r["error"] and "clear_action" in r2["error"]
    finally:
        e.store.conn.close()


def test_a6_observation_policy_untouched():
    """行动 policy 与 L7 观察策略是两回事：门面不得把前者写进后者。"""
    stub = _LoopStub(policy="curiosity")
    e = _engine(stub)
    try:
        e.seven_layer_loop("configure_action", {
            "entity_id": "walker-1", "target": "goal-9", "policy": "fixed"})
        assert stub.policy == "curiosity", (
            "门面把行动 policy 写进了观察策略：%r" % (stub.policy,))
        e.seven_layer_loop("clear_action", {})
        assert stub.policy == "curiosity", stub.policy
    finally:
        e.store.conn.close()


def test_a7_clear_action_forwarded():
    stub = _LoopStub()
    e = _engine(stub)
    try:
        r = e.seven_layer_loop("clear_action", {})
        assert r["status"] == "ok", r
        assert r["action_config"] == {"cleared": True}
        assert stub.clear_calls == 1
    finally:
        e.store.conn.close()


def test_a8_rejected_config_becomes_facade_error():
    """C 侧 fail-fast（ValueError）经门面包成统一错误返回（不改判据、不静默）。"""
    e = _engine(_RejectingLoop())
    try:
        r = e.seven_layer_loop("configure_action", {
            "entity_id": "walker-1", "target": "goal-9", "policy": "teleport"})
        assert r["status"] == "error", r
        assert r["reason"] == "action_config_rejected", r
        assert "teleport" in r["error"], r
    finally:
        e.store.conn.close()


def test_a9_real_seven_layer_loop_when_c_side_lands():
    """C 侧落地后：门面直达真实闭环，且不改观察策略（未落地 ⇒ skip，不替 C 造实现）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.seven_layer_loop("init", {"policy": "curiosity", "seed": 7})
        loop = e._sll
        if not (hasattr(loop, "configure_action") and hasattr(loop, "clear_action")):
            pytest.skip("C 侧 SevenLayerLoop.configure_action/clear_action 尚未落地"
                        "（F08 待 C 完成）")
        e.seven_layer_loop("create", {"trees": 1})
        eid = e.seven_layer_loop("entity", {"category": "walker",
                                            "pos": [2, 1.5, 2]})["entity_id"]
        r = e.seven_layer_loop("configure_action", {
            "entity_id": eid, "target": [6, 1.5, 6], "step_size": 1.0,
            "policy": "predictive", "fixed_action": "east"})
        assert r["status"] == "ok", r
        assert loop.policy == "curiosity", "观察策略被行动配置改写"
        r2 = e.seven_layer_loop("clear_action", {})
        assert r2["status"] == "ok", r2
    finally:
        e.store.conn.close()


def _real_loop_with_two_entities():
    """真实 C 侧闭环 + 两个实体（runner / goal）；C 未落地 ⇒ skip（不替 C 造实现）。"""
    e = SpacetimeMemoryEngine(":memory:")
    e.seven_layer_loop("init", {"policy": "curiosity", "seed": 7})
    loop = e._sll
    if not hasattr(loop, "configure_action"):
        e.store.conn.close()
        pytest.skip("C 侧 SevenLayerLoop.configure_action 尚未落地（F08 待 C 完成）")
    e.seven_layer_loop("create", {"trees": 0})
    runner = e.seven_layer_loop("entity", {"category": "walker",
                                              "pos": [2, 1.5, 2]})["entity_id"]
    goal = e.seven_layer_loop("entity", {"category": "goal",
                                            "pos": [6, 1.5, 6]})["entity_id"]
    return e, runner, goal


def test_a10_raw_params_hit_c_fail_closed_without_polluting_config():
    """A10：原样参数抵达 C 的 fail-closed 校验；非法一律 error 且不污染既有配置。

    R6 返工判据：门面**不得**预转换 target/step_size（`float(True)==1.0` 会把 bool
    洗成合法值）；C 侧明确 ValueError ⇒ 门面既有错误形（reason=action_config_rejected）。
    """
    e, runner, goal = _real_loop_with_two_entities()
    try:
        good = e.seven_layer_loop("configure_action", {
            "entity_id": runner, "target": [6.0, 1.5, 6.0], "step_size": 2.0,
            "policy": "predictive", "fixed_action": "east"})
        assert good["status"] == "ok", good
        baseline = e._sll.action_state()["config"]
        pos_before = e._sll.world.entities[runner].pos
        bad = [
            ("bool 坐标", {"target": [True, 1.5, 5]}),
            ("bool 坐标 + bool 步长", {"target": [True, 1.5, 5], "step_size": True}),
            ("bool 步长", {"target": [6.0, 1.5, 6.0], "step_size": True}),
            ("None 步长", {"target": [6.0, 1.5, 6.0], "step_size": None}),
            ("字符串步长（非数）", {"target": [6.0, 1.5, 6.0], "step_size": "2.0"}),
            ("零步长", {"target": [6.0, 1.5, 6.0], "step_size": 0.0}),
            ("负数步长", {"target": [6.0, 1.5, 6.0], "step_size": -1.0}),
            ("非有限步长", {"target": [6.0, 1.5, 6.0], "step_size": float("inf")}),
            ("坐标非三元", {"target": [1.0, 2.0]}),
            ("坐标含字符串", {"target": ["a", "b", "c"]}),
            ("坐标含 NaN", {"target": [1.0, float("nan"), 3.0]}),
            ("未知 target 实体", {"target": "does-not-exist"}),
            ("target 非 id 非坐标", {"target": {"x": 1.0}}),
            ("未知 policy", {"target": [6.0, 1.5, 6.0], "policy": "teleport"}),
            ("未知动作", {"target": [6.0, 1.5, 6.0], "fixed_action": "northwest"}),
        ]
        for label, extra in bad:
            params = {"entity_id": runner}
            params.update(extra)
            r = e.seven_layer_loop("configure_action", params)
            assert r["status"] == "error", (label, r)
            assert r["reason"] == "action_config_rejected", (label, r)
            assert r.get("error"), (label, r)
            assert e._sll.action_state()["config"] == baseline, (
                "%s：非法配置改动了已生效配置：%r"
                % (label, e._sll.action_state()["config"]))
            assert e._sll.world.commands() == {}, "%s：非法配置留下了待消费动作" % label
            assert e._sll.policy == "curiosity", "%s：观察策略被改写" % label
            assert e._sll.world.entities[runner].pos == pos_before, (
                "%s：配置/执行动作移动了实体（configure_action 不得移动）" % label)
        # 正常数值（含 int 步长）与实体 ID 仍可用
        r_id = e.seven_layer_loop("configure_action", {
            "entity_id": runner, "target": goal, "step_size": 1.5,
            "policy": "fixed", "fixed_action": "west"})
        assert r_id["status"] == "ok", r_id
        cfg = r_id["action_config"]
        assert cfg["target"] == goal and cfg["target_kind"] == "entity", cfg
        assert cfg["step_size"] == 1.5 and cfg["policy"] == "fixed"
        assert cfg["fixed_action"] == "west", cfg
        r_coord = e.seven_layer_loop("configure_action", {
            "entity_id": runner, "target": [7, 1.5, 3], "step_size": 1})
        assert r_coord["status"] == "ok", r_coord
        assert r_coord["action_config"]["target"] == [7.0, 1.5, 3.0], r_coord
        assert r_coord["action_config"]["step_size"] == 1.0, r_coord
        assert e._sll.policy == "curiosity", "观察策略被行动配置改写"
    finally:
        e.store.conn.close()
