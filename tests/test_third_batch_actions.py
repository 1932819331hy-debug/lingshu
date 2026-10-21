# -*- coding: utf-8 -*-
"""test_third_batch_actions · F08 预测行动（第三批）验收守卫
============================================================================
被测面（lingshu/world/seven_layer_loop.py · scene_simulator.py）：
  · 公共 API：SevenLayerLoop.configure_action(entity_id, target, step_size=1.0,
    policy="predictive", fixed_action="east") / clear_action()；target ＝ 目标实体
    id 或 (x, y, z) 坐标；未配置（或 clear 之后）完全兼容既有七层观测 policy。
  · 决策信息源：候选排序只读观测（explorer.nodes）+ 学得模型
    （explorer.predict(horizon=1)）+ 上一 tick 的观测误差修正；执行（真实位移）
    由 SceneSimulator.set_command/_apply_command 完成——决策与执行分离。
  · 每 tick 留痕：候选未来 / 评分 / 选择 / 执行 / 后续观测 / 误差（audit["L7_action"]）。

断言组
------
A 默认关闭兼容：未配置不新增字段；configure→clear 后与「从未配置」逐 tick 逐位一致，
  且 configure/clear 本身不改状态（无污染）。
B 公共 API：实体目标与坐标目标；action_state()/state()["action"]；clear 幂等。
C fail-fast：错目标 / 未知 policy / 未知动作 / 不合法步长一律 ValueError 且配置不变；
  simulator 侧同口径；失败不留待消费动作。
D 留痕完整：5 个候选（动作表序）+ 每个候选的预测自身位置与评分 + 选择 + 执行 +
  后续观测 + 目标/自身误差；评分可由留痕复算；自身误差＝0（模型与执行口径一致）。
E 必要观测：预算够时总数不变，预算不足时扩到 len(required) 并显式标 budget_expanded。
F 决策质量（同 seed 同世界对照）：static 目标 40 tick、移动目标 40 tick —— predictive
  末态距离显著小于固定规则；移动目标掠近时「预测择优」与「不外推贪心」动作不同，
  且留痕可复算该反事实距离；后续观测改变后下一 tick 选择改变。
G 信息源约束：评分路径不接触世界对象；真值目标位置被改而不观测 ⇒ 决策不变；
  目标 behavior/goal（隐藏行为规则）被改 ⇒ 决策不变（但世界演化确实变了，非空场）。
H 冷启动/观测不足显式标注；坐标目标视为静态；误差修正确实进入下一 tick 的预测。
I 陈旧/缺失目标观测不构成新误差反馈（F08 返工契约）：target_fresh=False 时
  correction / error_history 逐项冻结、不返回 corrected=True、并写明理由；
  fresh=True 的已知误差 1→0.5→0 更新路径逐位保持；缺观测与无预测分支同口径。

运行（lingshu 仓根）：
  python -X utf8 -m pytest tests/test_third_batch_actions.py -q --no-header
  python -X utf8 tests/test_third_batch_actions.py --report      # 打印可复算对照数据
退出码：0 = 全过；1 = 有断言失败
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lingshu.world.scene_simulator import (ACTION_DELTAS, ACTION_NAMES,  # noqa: E402
                                           SceneSimulator)
from lingshu.world.seven_layer_loop import (ACTION_POLICIES,  # noqa: E402
                                            TARGET_ERROR_STATUSES,
                                            ActionDecision, SevenLayerLoop)


# =============================== 公共夹具/工具 ===============================

def canon(obj, mapping):
    """把实体 id 递归替换成稳定名字（两个同构世界才能逐位对比）。"""
    if isinstance(obj, dict):
        return {canon(k, mapping): canon(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [canon(v, mapping) for v in obj]
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    return obj


def canon_json(obj, mapping):
    return json.dumps(canon(obj, mapping), sort_keys=True, ensure_ascii=False)


def decision_core(trace):
    """只保留「决策」字段（不含执行/观测结算），用于跨世界对比。"""
    return {key: trace.get(key) for key in (
        "status", "cold_start", "model_ready", "degraded", "observation",
        "forecast", "candidates", "greedy_current_choice", "greedy_scores",
        "model_choice", "chosen_action", "decision_rule", "policy",
        "fixed_action", "step_size", "target_kind")}


def legacy_world(seed=42):
    """既有七层测试世界（wander/seek/flee 三实体）——用于「关闭后复原」对照。"""
    loop = SevenLayerLoop(size=24, seed=seed, budget=2)
    loop.create_scene(trees=2, water=False)
    player = loop.add_entity("player", behavior="wander", pos=(2, 1.5, 2), speed=0.5)
    wolf = loop.add_entity("wolf", behavior="seek", pos=(15, 1.5, 15), speed=0.6,
                           goal=player)
    rabbit = loop.add_entity("rabbit", behavior="flee", pos=(10, 1.5, 10),
                             speed=0.5, goal=wolf)
    return loop, {player: "<e0>", wolf: "<e1>", rabbit: "<e2>"}


def static_world(policy, seed=42, ticks=40, fixed_action="east"):
    """静止目标：受控体从 (2,2) 出发，目标实体在 (18,18)。"""
    loop = SevenLayerLoop(size=24, seed=seed, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(2, 1.5, 2), speed=0.0)
    beacon = loop.add_entity("beacon", behavior="wander", pos=(18, 1.5, 18),
                             speed=0.0)
    loop.explorer.observe()
    loop.explorer.run(n=4)
    loop.configure_action(runner, beacon, step_size=1.0, policy=policy,
                          fixed_action=fixed_action)
    for _ in range(ticks):
        loop.step()
    return loop, runner, beacon


def patrol_world(policy, seed=42, ticks=40, fixed_action="east"):
    """移动目标：scout 沿 (14,14)->(20,14)->(20,20)->(14,20) 巡逻（0.5/tick）。"""
    loop = SevenLayerLoop(size=24, seed=seed, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(2, 1.5, 2), speed=0.0)
    scout = loop.add_entity("scout", behavior="follow", pos=(14, 1.5, 14),
                            speed=0.5, goal="loop")
    loop.add_path("loop", [(14, 1.5, 14), (20, 1.5, 14),
                           (20, 1.5, 20), (14, 1.5, 20)])
    loop.explorer.observe()
    loop.explorer.run(n=6)
    loop.configure_action(runner, scout, step_size=1.0, policy=policy,
                          fixed_action=fixed_action)
    for _ in range(ticks):
        loop.step()
    return loop, runner, scout


def passby_world(seed=42, warm=2):
    """移动目标「掠近」场景：scout 沿 z=5.4 以 1.0/tick 东行，受控体在 (5,5)。

    warm=2 使 scout 恰好走到 x=5（与受控体 z 差 0.4）——此时「按观测位置贪心」
    会选 stay，而一步外推（学得速度 1.0 东）会选 east，两者动作不同且后者更近。
    """
    loop = SevenLayerLoop(size=24, seed=seed, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(5, 1.5, 5), speed=0.0)
    scout = loop.add_entity("scout", behavior="follow", pos=(3, 1.5, 5.4),
                            speed=1.0, goal="patrol")
    loop.add_path("patrol", [(3, 1.5, 5.4), (23, 1.5, 5.4)])
    loop.explorer.observe()
    loop.explorer.run(n=warm)
    return loop, runner, scout


def configure_value_error(loop, *args, **kwargs):
    """执行 configure_action 并返回 (是否抛 ValueError, 消息)。"""
    try:
        loop.configure_action(*args, **kwargs)
    except ValueError as exc:
        return True, str(exc)
    return False, ""


# =============================== A 默认关闭兼容 ===============================

def test_a_unconfigured_loop_has_no_action_field_and_matches_cleared_loop():
    """A1：未配置 ⇒ audit 无 L7_action；configure→clear 后与从未配置逐 tick 逐位一致。"""
    a, ma = legacy_world()
    b, mb = legacy_world()
    cleared = b.clear_action()
    assert cleared["cleared"] is False, "A1：未配置时 clear_action 应报告 cleared=False"
    configured = b.configure_action(list(mb)[0], list(mb)[1], step_size=1.0,
                                    policy="predictive")
    assert configured["entity"] == list(mb)[0]
    assert b.action_state()["configured"] is True
    b.clear_action()
    assert b.action_state()["configured"] is False
    assert b.world.commands() == {}, "A1：clear 后不得残留待消费动作"

    for tick in range(5):
        ra, rb = a.step(), b.step()
        assert "L7_action" not in ra, "A1：未配置的 loop 不该有 L7_action 留痕"
        assert "L7_action" not in rb, "A1：clear_action 后不该有 L7_action 留痕"
        assert canon_json(ra, ma) == canon_json(rb, mb), (
            "A1：clear_action 后第 %d tick 与未配置循环不一致" % (tick + 1),
            canon_json(ra, ma), canon_json(rb, mb))
    assert a.audit[0].keys() == b.audit[0].keys()


def test_a_configure_then_clear_leaves_state_untouched():
    """A2：configure/clear 本身零副作用（state/report/观测面/strict 审计长度不变）。"""
    loop, mapping = legacy_world()
    ids = list(mapping)
    before = {"state": canon_json(loop.state(), mapping),
              "report": canon_json(loop.report(), mapping),
              "tick": loop.explorer.tick, "obs": dict(loop.explorer.obs_counts),
              "audit": len(loop.audit), "nodes": sorted(loop.explorer.nodes)}
    loop.configure_action(ids[0], ids[1], step_size=1.0)
    assert "action" in loop.state(), "A2：配置后 state() 应带 action 配置"
    loop.clear_action()
    after = {"state": canon_json(loop.state(), mapping),
             "report": canon_json(loop.report(), mapping),
             "tick": loop.explorer.tick, "obs": dict(loop.explorer.obs_counts),
             "audit": len(loop.audit), "nodes": sorted(loop.explorer.nodes)}
    assert before == after, ("A2：configure/clear 改动了状态", before, after)


# =============================== B 公共 API ===============================

def test_b_entity_and_coordinate_targets():
    """B1：target 支持实体 id 与 3D 坐标；配置面完整且可审计。"""
    loop, runner, scout = passby_world()
    cfg = loop.configure_action(runner, scout, step_size=1.5, policy="predictive")
    # B1b：返回值＝行动配置载荷本身（core 门面把它整体放进 action_config）
    assert "status" not in cfg and "action_config" not in cfg, cfg
    assert cfg["target_kind"] == "entity"
    assert cfg["entity"] == runner
    assert cfg["target"] == scout
    assert cfg["step_size"] == 1.5
    assert cfg["policy"] == "predictive" and cfg["fixed_action"] == "east"
    assert loop.action_state()["configured"] is True

    loop2, runner2, _scout2 = passby_world()
    cfg2 = loop2.configure_action(runner2, (9.0, 1.5, 9.0), step_size=1.0)
    assert cfg2["target_kind"] == "coordinate"
    assert cfg2["target"] == [9.0, 1.5, 9.0]


def test_b_clear_action_is_idempotent():
    """B2：clear_action 幂等；重复调用不报错且第二次报告 cleared=False。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout)
    first = loop.clear_action()
    second = loop.clear_action()
    assert first["cleared"] is True and second["cleared"] is False
    assert loop.action_state()["configured"] is False


def test_b_coordinate_target_is_static_and_reachable():
    """B3：坐标目标＝静态目标（无外推、无误差修正），受控体确实抵达坐标。"""
    loop = SevenLayerLoop(size=24, seed=42, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(5, 1.5, 5), speed=0.0)
    loop.explorer.observe()
    loop.explorer.run(n=4)
    loop.configure_action(runner, (9.0, 1.5, 9.0), step_size=1.0, policy="predictive")
    for _ in range(8):
        loop.step()
    trace = loop.audit[-1]["L7_action"]
    assert trace["forecast"]["mode"] == "static_coordinate"
    assert trace["forecast"]["extrapolated"] is False
    assert trace["forecast"]["correction_applied"] == [0.0, 0.0]
    assert trace["outcome"]["target_error"] == [0.0, 0.0]
    assert math.dist(tuple(loop.world.entities[runner].pos), (9.0, 1.5, 9.0)) == 0.0


def test_b_core_facade_passes_the_config_payload_through():
    """B4：core 门面 action_config 即本方法的配置载荷（A↔C 接口不套两层）。"""
    from lingshu.core.core import SpacetimeMemoryEngine
    engine = SpacetimeMemoryEngine(":memory:")
    try:
        engine.seven_layer_loop("init", {"policy": "curiosity", "seed": 7})
        engine.seven_layer_loop("create", {"trees": 0})
        eid = engine.seven_layer_loop(
            "entity", {"category": "walker", "pos": [2, 1.5, 2]})["entity_id"]
        other = engine.seven_layer_loop(
            "entity", {"category": "goal", "pos": [6, 1.5, 6]})["entity_id"]
        result = engine.seven_layer_loop("configure_action", {
            "entity_id": eid, "target": other, "step_size": 1.0,
            "policy": "predictive", "fixed_action": "east"})
        assert result["status"] == "ok", result
        payload = result["action_config"]
        assert payload["entity"] == eid and payload["target"] == other
        assert "action_config" not in payload, ("B4：配置载荷被套了两层（门面约定是整体转发）",
                                                payload)
        loop = engine._sll
        assert loop.policy == "curiosity", "B4：行动配置改写了 L7 观察策略"
        assert loop.world.entities[eid].pos == (2.0, 1.5, 2.0), (
            "B4：configure_action 本身不得移动实体")
        cleared = engine.seven_layer_loop("clear_action", {})
        assert cleared["status"] == "ok"
        assert cleared["action_config"]["cleared"] is True
        assert cleared["action_config"]["entity"] == eid
    finally:
        engine.store.conn.close()


# =============================== C fail-fast ===============================

@pytest.mark.parametrize("kwargs,label", [
    ({"target": "does-not-exist"}, "错目标实体 id"),
    ({"target": (1.0, 2.0)}, "2 维坐标"),
    ({"target": (1.0, 2.0, 3.0, 4.0)}, "4 维坐标"),
    ({"target": (1.0, 2.0, float("nan"))}, "NaN 坐标"),
    ({"target": (1.0, 2.0, float("inf"))}, "inf 坐标"),
    ({"target": (1.0, 2.0, "3")}, "非数值坐标"),
    ({"target": True}, "非 id 非坐标"),
    ({"target": None}, "None 目标"),
    ({"policy": "greedy"}, "未知 policy"),
    ({"policy": "Predictive"}, "大小写不符 policy"),
    ({"fixed_action": "up"}, "未知动作"),
    ({"step_size": 0}, "step_size=0"),
    ({"step_size": -1.0}, "负步长"),
    ({"step_size": float("nan")}, "NaN 步长"),
    ({"step_size": float("inf")}, "inf 步长"),
    ({"step_size": "1.0"}, "字符串步长"),
    ({"step_size": True}, "布尔步长"),
])
def test_c_bad_config_fails_fast_without_pollution(kwargs, label):
    """C1：坏配置 fail-fast（ValueError）且当前配置/世界待消费动作都不变。"""
    loop, runner, scout = passby_world()
    good = loop.configure_action(runner, scout, step_size=2.0, policy="predictive")
    snapshot = json.dumps(loop.action_state()["config"], sort_keys=True,
                          ensure_ascii=False)
    calls = {"target": scout, "step_size": 1.0, "policy": "predictive"}
    calls.update(kwargs)
    raised, message = configure_value_error(loop, runner, **calls)
    assert raised, ("C1：%s 未 fail-fast（应 ValueError）" % label)
    assert message.strip(), "C1：%s 的失败信息为空" % label
    assert json.dumps(loop.action_state()["config"], sort_keys=True,
                      ensure_ascii=False) == snapshot, (
        "C1：%s 失败后配置被污染" % label)
    assert loop.world.commands() == {}, "C1：%s 失败后留下待消费动作" % label
    assert good["step_size"] == 2.0


def test_c_bad_config_on_fresh_loop_leaves_no_action():
    """C2：全新循环上的坏配置不得留下任何配置（state 无 action 键）。"""
    loop, runner, scout = passby_world()
    for bad in ({"policy": "typo"}, {"step_size": 0}, {"fixed_action": "sideways"},
                {"target": "missing"}):
        calls = {"target": scout, "step_size": 1.0}
        calls.update(bad)
        raised, _ = configure_value_error(loop, runner, **calls)
        assert raised, bad
    assert loop.action_state()["configured"] is False
    assert "action" not in loop.state(), "C2：失败后 state() 仍带 action 配置"


def test_c_self_target_and_unknown_entity_rejected():
    """C3：目标＝受控体自身 / 受控体不在场景 ⇒ ValueError。"""
    loop, runner, scout = passby_world()
    raised, message = configure_value_error(loop, runner, runner)
    assert raised and "自身" in message, message
    raised, message = configure_value_error(loop, "ghost", scout)
    assert raised and "受控实体" in message, message


def test_c_simulator_set_command_fails_closed():
    """C4：真实执行侧同口径 fail-closed（未知动作/坏步长/未知实体）。"""
    sim = SceneSimulator(size=24, seed=42)
    eid = sim.add_entity("e", pos=(5.0, 1.5, 5.0), speed=0.5)
    for action, step in (("up", 1.0), ("", 1.0), ("east", 0), ("east", -2.0),
                         ("east", float("nan")), ("east", "1"), ("east", None)):
        with pytest.raises(ValueError):
            sim.set_command(eid, action, step)
    with pytest.raises(ValueError):
        sim.set_command("ghost", "east", 1.0)
    assert sim.commands() == {}, "C4：失败的 set_command 不得登记动作"
    assert sim.entities[eid].pos == (5.0, 1.5, 5.0), "C4：失败不得移动实体"


def test_c_action_executed_verbatim_by_simulator():
    """C5：真实执行＝动作表方向 × step_size，单次消费，且留行为日志。"""
    sim = SceneSimulator(size=24, seed=42)
    eid = sim.add_entity("e", pos=(5.0, 1.5, 5.0), speed=0.5)
    sim.set_command(eid, "east", 1.25)
    assert sim.commands() == {eid: {"entity": eid, "action": "east",
                                     "step_size": 1.25}}
    result = sim.step(n=1)
    assert result["actions"] == 1
    assert sim.entities[eid].pos == (6.25, 1.5, 5.0), sim.entities[eid].pos
    assert sim.entities[eid].velocity == (1.25, 0.0, 0.0)
    assert sim.commands() == {}, "C5：动作应被单次消费"
    log = sim.behavior_log(limit=5)
    assert log and log[-1]["action"] == "east" and log[-1]["commanded"] is True
    # stay：位移为 0 ⇒ 不计入 actions，但仍留决策记录（且不残留 velocity）
    sim.set_command(eid, "stay", 1.0)
    assert sim.step(n=1)["actions"] == 0
    assert sim.entities[eid].pos == (6.25, 1.5, 5.0)
    assert sim.entities[eid].velocity == (0.0, 0.0, 0.0)
    assert sim.behavior_log(limit=1)[0]["action"] == "stay"


def test_c_bad_config_does_not_disturb_a_running_loop():
    """C6：运行中拒绝坏配置 ⇒ 既有配置继续按原参数执行（真实位移可验）。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout, step_size=2.0, policy="fixed",
                          fixed_action="north")
    raised, _ = configure_value_error(loop, runner, scout, step_size=0,
                                      policy="bogus")
    assert raised
    rec = loop.step()
    assert rec["L7_action"]["chosen_action"] == "north"
    assert rec["L7_action"]["step_size"] == 2.0
    assert loop.world.entities[runner].pos == (5.0, 1.5, 3.0), (
        "C6：坏配置影响了真实执行", loop.world.entities[runner].pos)

def test_c_policy_whitelist_is_exactly_predictive_and_fixed():
    """C7：行动策略白名单 fail-closed（白名单外一律 ValueError，不静默退化）。"""
    assert tuple(ACTION_POLICIES) == ("predictive", "fixed")
    loop, runner, scout = passby_world()
    for bad in ("greedy", "curiosity", "Predictive", "predictive ", "", "fixed2"):
        raised, message = configure_value_error(loop, runner, scout, policy=bad)
        assert raised, ("C7：policy=%r 未 fail-fast" % (bad,))
        assert "policy" in message, message
    assert loop.action_state()["configured"] is False


def test_c_action_decision_is_standalone_and_matches_actuator():
    """C8：ActionDecision 只依赖观测面（不持有世界）；动作模型＝执行口径。"""
    loop, runner, scout = passby_world()
    decision = ActionDecision(entity_id=runner, target=scout, target_kind="entity",
                              step_size=1.0, policy="predictive",
                              fixed_action="east")
    assert decision.config()["entity"] == runner and decision.config()["target"] == scout
    # 边界钳制口径与 SceneSimulator 真实执行逐位一致
    sim = SceneSimulator(size=24, seed=42)
    probe = sim.add_entity("probe", pos=(23.5, 1.5, 0.5), speed=0.5)
    predicted = decision.predict_self((23.5, 1.5, 0.5), "east", 1.0, 24)
    sim.set_command(probe, "east", 1.0)
    sim.step(n=1)
    assert list(sim.entities[probe].pos) == predicted == [23.5, 1.5, 0.5]
    probe_pos = list(sim.entities[probe].pos)
    for action in ACTION_NAMES:
        predicted = decision.predict_self(tuple(probe_pos), action, 1.0, 24)
        sim2 = SceneSimulator(size=24, seed=42)
        p2 = sim2.add_entity("p", pos=tuple(probe_pos), speed=0.5)
        sim2.set_command(p2, action, 1.0)
        sim2.step(n=1)
        assert list(sim2.entities[p2].pos) == predicted, (action, predicted)
    # 目标从未被观测 ⇒ 显式 status，不编造目标位置
    ghost = ActionDecision(entity_id=runner, target="ghost", target_kind="entity",
                           step_size=1.0, policy="predictive", fixed_action="east")
    trace = ghost.decide(loop.explorer, predictions={})
    assert trace["status"] == "target_unobserved", trace
    assert trace["chosen_action"] is None


# =============================== D 留痕完整 ===============================

def test_d_every_tick_traces_candidates_scores_choice_execution_observation_error():
    """D1：每 tick 留痕含候选未来/评分/选择/执行/后续观测/误差，且评分可复算。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout, step_size=1.0, policy="predictive")
    for tick in range(4):
        rec = loop.step()
        assert "L7_action" in rec, "D1：第 %d tick 缺 L7_action 留痕" % (tick + 1)
        trace = rec["L7_action"]
        assert trace["status"] == "ok", trace
        assert trace["entity"] == runner and trace["target"] == scout
        assert trace["step_size"] == 1.0 and trace["policy"] == "predictive"
        candidates = trace["candidates"]
        assert [c["action"] for c in candidates] == list(ACTION_NAMES), (
            "D1：候选动作须为动作表全序（确定性）", candidates)
        forecast = trace["forecast"]
        scores = {c["action"]: c["score"] for c in candidates}
        for cand in candidates:
            assert len(cand["predicted_self"]) == 3
            expect = round(math.dist(cand["predicted_self"], forecast["predicted"]), 4)
            assert cand["score"] == expect, ("D1：评分须＝候选位置到预测目标位置的距离",
                                            cand, forecast)
        assert trace["chosen_action"] in ACTION_NAMES
        best = min(ACTION_NAMES, key=lambda a: (scores[a], ACTION_NAMES.index(a)))
        assert trace["chosen_action"] == best, (
            "D1：predictive 必须选评分最小的动作（tie-break＝动作表序）", trace)
        executed = trace["executed"]
        assert executed["action"] == trace["chosen_action"]
        assert executed["observed_to"] is not None, "D1：缺执行后的观测位置"
        assert executed["self_error"] == 0.0, (
            "D1：动作模型与真实执行口径必须一致（自身预测误差应为 0）", executed)
        outcome = trace["outcome"]
        assert outcome["observed_self"] == executed["observed_to"]
        assert outcome["observed_target"] is not None
        assert outcome["target_observation_fresh"] is True
        assert len(outcome["target_error"]) == 2
        assert outcome["target_error_norm"] == round(
            math.hypot(*outcome["target_error"]), 4)
        assert executed["distance_before"] == round(math.dist(
            trace["observation"]["self"], trace["observation"]["target"]), 4)
        assert executed["distance_after"] == round(math.dist(
            outcome["observed_self"], outcome["observed_target"]), 4)
    assert loop.action_log_view(limit=10) == loop._action_log[-10:]
    assert len(loop.action_log_view(limit=2)) == 2


def test_d_predictive_choice_is_actually_executed_by_the_world():
    """D2：审计里的 chosen_action 必须等价于世界真值位移（决策↔执行一致）。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout, step_size=1.0, policy="predictive")
    for _ in range(3):
        before = loop.world.entities[runner].pos
        rec = loop.step()
        trace = rec["L7_action"]
        dx, dz = ACTION_DELTAS[trace["chosen_action"]]
        after = loop.world.entities[runner].pos
        assert after == (round(before[0] + dx, 2), before[1],
                         round(before[2] + dz, 2)), (
            "D2：真实位移与审计动作不符", trace["chosen_action"], before, after)


def test_d_fixed_policy_executes_the_declared_action_every_tick():
    """D3：fixed 策略恒执行 fixed_action（对照基线语义），但照样留痕。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout, step_size=1.0, policy="fixed",
                          fixed_action="west")
    for _ in range(3):
        trace = loop.step()["L7_action"]
        assert trace["chosen_action"] == "west"
        assert trace["executed"]["action"] == "west"
        assert trace["model_choice"] in ACTION_NAMES, (
            "D3：fixed 基线仍应记录模型会选什么（对照证据）", trace)


# =============================== E 必要观测 ===============================

def test_e_required_observations_fit_inside_budget_when_possible():
    """E1：预算够 ⇒ 受控体与目标都在观测集内，且观测总数不变（预算语义未改）。"""
    loop, runner, scout = passby_world()
    loop.budget = 2
    loop.configure_action(runner, scout)
    decision = loop.step()["L7_decision"]
    assert set(decision["required_observations"]) == {runner, scout}
    assert set(decision["chosen"]) >= {runner, scout}
    assert decision["effective_observations"] == 2
    assert decision["budget_expanded"] is False
    assert decision["budget_note"] == "必要观测已含在预算内，总数不变"


def test_e_budget_shortfall_is_expanded_and_disclosed():
    """E2：预算不足（budget=1 但需 2 个必要观测）⇒ 扩到必要数**并显式标注**。"""
    loop, runner, scout = passby_world()
    loop.budget = 1
    loop.configure_action(runner, scout)
    rec = loop.step()
    decision = rec["L7_decision"]
    assert set(decision["chosen"]) == {runner, scout}, decision
    assert len(decision["ig_selected"]) == 1, (
        "E2：IG 选择本身仍按原 budget=1 走（未改 _select 语义）", decision)
    assert decision["budget_expanded"] is True
    assert decision["effective_observations"] == 2
    assert "扩到 2 个" in decision["budget_note"]
    assert "不静默超预算" in decision["budget_note"]
    assert rec["L1_perception"]["observed"] == decision["chosen"], (
        "E2：L1 必须真观测被选中的必要实体（异常计数口径一致）")


def test_e_unconfigured_loop_keeps_original_budget_semantics():
    """E3：未配置 ⇒ L7_decision 只有原四个键，chosen 完全由原策略给出。"""
    loop, _mapping = legacy_world()
    rec = loop.step()
    assert set(rec["L7_decision"]) == {"chosen", "ig_scores", "policy", "budget"}
    assert len(rec["L7_decision"]["chosen"]) <= max(1, loop.budget)


@pytest.mark.parametrize("obs_policy", ["curiosity", "random", "round_robin"])
def test_e_action_layer_works_with_every_observation_policy(obs_policy):
    """E4：行动层与三种既有 L7 观测策略都能共存，且不改写观测策略。"""
    loop = SevenLayerLoop(size=24, seed=42, budget=2, policy=obs_policy)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(5, 1.5, 5), speed=0.0)
    beacon = loop.add_entity("beacon", behavior="wander", pos=(15, 1.5, 15), speed=0.0)
    loop.explorer.observe()
    loop.explorer.run(n=4)
    loop.configure_action(runner, beacon, step_size=1.0, policy="predictive")
    rec = loop.step()
    assert loop.policy == obs_policy, "E4：行动配置改写了 L7 观测策略"
    assert rec["L7_decision"]["policy"] == obs_policy
    assert set(rec["L7_decision"]["chosen"]) >= {runner, beacon}
    assert rec["L7_action"]["status"] == "ok"
    assert rec["L7_action"]["observation"]["source"].startswith("explorer.nodes")


# =============================== F 决策质量对照 ===============================

def test_f_predictive_beats_fixed_rule_on_static_target_same_seed():
    """F1：同 seed 同世界，40 tick 后 predictive 明显比固定规则更接近目标。"""
    pre, pre_runner, pre_beacon = static_world("predictive")
    fix, fix_runner, fix_beacon = static_world("fixed")
    assert pre.world.entities[pre_beacon].pos == fix.world.entities[fix_beacon].pos, (
        "F1：对照两名选手的世界必须完全一致（同 seed 同轨迹）")
    pre_d = math.dist(pre.world.entities[pre_runner].pos,
                      pre.world.entities[pre_beacon].pos)
    fix_d = math.dist(fix.world.entities[fix_runner].pos,
                      fix.world.entities[fix_beacon].pos)
    assert fix_d > 10.0, ("F1：固定 east 基线应远离 (18,18) 目标", fix_d)
    assert pre_d < 1.0, ("F1：预测行动应抵达静止目标", pre_d)
    assert pre_d < fix_d - 10.0, ("F1：predictive 未显著更接近目标", pre_d, fix_d)
    pre_actions = [x["L7_action"]["chosen_action"] for x in pre.audit]
    fix_actions = [x["L7_action"]["chosen_action"] for x in fix.audit]
    assert set(fix_actions) == {"east"}, ("F1：固定规则必须恒执行同一动作",
                                          sorted(set(fix_actions)))
    assert set(pre_actions) != {"east"}, (
        "F1：预测行动的动作序列必须随几何变化（否则与固定规则无从区分）",
        sorted(set(pre_actions)))
    assert "south" in pre_actions, ("F1：目标在 +z 方向 ⇒ 必须用得上 south",
                                    sorted(set(pre_actions)))


def test_f_predictive_beats_fixed_rule_on_moving_target_same_seed():
    """F2：同 seed 移动目标（巡逻）对照，predictive 追上、固定规则不追。"""
    pre, pre_runner, pre_scout = patrol_world("predictive")
    fix, fix_runner, fix_scout = patrol_world("fixed")
    assert pre.world.entities[pre_scout].pos == fix.world.entities[fix_scout].pos, (
        "F2：移动目标两个世界必须同轨迹")
    pre_d = math.dist(pre.world.entities[pre_runner].pos,
                      pre.world.entities[pre_scout].pos)
    fix_d = math.dist(fix.world.entities[fix_runner].pos,
                      fix.world.entities[fix_scout].pos)
    assert pre_d < 2.0, ("F2：预测行动应追到巡逻目标附近", pre_d)
    assert fix_d > 10.0, ("F2：固定 east 基线应远离巡逻区域", fix_d)
    assert pre_d < fix_d - 8.0, ("F2：predictive 未显著更接近移动目标", pre_d, fix_d)
    extrapolated = [x["L7_action"]["forecast"]["extrapolated"] for x in pre.audit]
    assert sum(extrapolated) >= len(extrapolated) // 2, (
        "F2：移动目标上应有过半 tick 的预测位置离开观测位置（预测真参与打分）",
        sum(extrapolated), len(extrapolated))


def test_f_moving_target_prediction_differs_from_no_extrapolation_greedy():
    """F3：目标掠近时「一步外推」与「按当前观测位置贪心」动作不同，且前者更近。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout, step_size=1.0, policy="predictive")
    trace = loop.step()["L7_action"]
    forecast = trace["forecast"]
    assert forecast["extrapolated"] is True, (
        "F3：学得速度（1.0/tick 东）必须把预测位置推出观测位置", forecast)
    assert forecast["mode"] in ("bounded_noisy", "exact", "chase_stochastic"), forecast
    assert forecast["predicted"] != forecast["observed"], forecast
    assert trace["model_choice"] == "east", trace["candidates"]
    assert trace["greedy_current_choice"] == "stay", (
        "F3：不外推的当前位置贪心应选 stay（受控体已贴近观测位置）",
        trace["greedy_current_choice"], trace["greedy_scores"])
    assert trace["model_choice"] != trace["greedy_current_choice"], (
        "F3：预测必须改变选择（记录外推 vs 不外推）", trace)
    # 反事实距离：候选预测位置（自身预测误差=0，等价真值）vs 执行后的真实观测目标位置
    observed_target = trace["outcome"]["observed_target"]
    dists = {c["action"]: round(math.dist(c["predicted_self"], observed_target), 4)
             for c in trace["candidates"]}
    assert dists[trace["chosen_action"]] < dists[trace["greedy_current_choice"]], (
        "F3：预测所选动作的末距必须优于不外推贪心所选动作", dists)
    assert trace["executed"]["distance_after"] == dists[trace["chosen_action"]]


def test_f_next_choice_changes_after_the_observation_changes():
    """F4：目标位置改变并被观测到之后，下一 tick 的选择随之改变。"""
    loop = SevenLayerLoop(size=24, seed=42, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(5, 1.5, 5), speed=0.0)
    beacon = loop.add_entity("beacon", behavior="wander", pos=(12, 1.5, 5), speed=0.0)
    loop.explorer.observe()
    loop.explorer.run(n=4)
    loop.configure_action(runner, beacon, step_size=1.0, policy="predictive")
    first = loop.step()["L7_action"]
    assert first["observation"]["target"] == [12.0, 1.5, 5.0]
    assert first["chosen_action"] == "east", ("F4：目标在东 ⇒ 先往东",
                                              first["candidates"])
    # 世界事件：目标被移到西侧（受控体尚未观测到）
    loop.world.entities[beacon].pos = (0.5, 1.5, 5)
    second = loop.step()["L7_action"]
    assert second["observation"]["target"] == [12.0, 1.5, 5.0], (
        "F4：本轮决策仍用上一条观测（观测面滞后于未经观测的世界事件）", second)
    assert second["outcome"]["observed_target"] == [0.5, 1.5, 5.0], (
        "F4：本轮 L1 必须观测到目标的新位置", second["outcome"])
    assert second["chosen_action"] == "east"
    third = loop.step()["L7_action"]
    assert third["observation"]["target"] == [0.5, 1.5, 5.0]
    assert third["chosen_action"] == "west", (
        "F4：观测改变后下一步选择必须随之改变", third["candidates"])


def test_f_error_feedback_enters_the_next_tick_prediction():
    """F5：观测误差按 EWMA 修正下一 tick 的目标预测（冷启动也能纠正）。"""
    loop = SevenLayerLoop(size=24, seed=42, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(5, 1.5, 5), speed=0.0)
    scout = loop.add_entity("scout", behavior="follow", pos=(3, 1.5, 5.4),
                            speed=1.0, goal="patrol")
    loop.add_path("patrol", [(3, 1.5, 5.4), (23, 1.5, 5.4)])
    loop.configure_action(runner, scout, step_size=1.0, policy="predictive")
    traces = [loop.step()["L7_action"] for _ in range(3)]
    first = traces[0]
    assert first["cold_start"] is True, "F5：首 tick 无观测 ⇒ 冷启动须显式标注"
    assert first["model_ready"] is False
    assert first["degraded"] == "insufficient_target_observations"
    assert first["forecast"]["extrapolated"] is False, (
        "F5：冷启动不得假装外推", first["forecast"])
    assert first["forecast"]["correction_applied"] == [0.0, 0.0]
    assert first["outcome"]["target_error"] == [1.0, 0.0], first["outcome"]
    assert first["outcome"]["next_correction"] == [0.5, 0.0], first["outcome"]
    second = traces[1]
    assert second["cold_start"] is False
    assert (second["forecast"]["correction_applied"]
            == first["outcome"]["next_correction"]), (
        "F5：上一 tick 的修正必须进入下一 tick 的预测", second["forecast"])
    assert second["forecast"]["observed"] == [4.0, 1.5, 5.4]
    errors = [abs(t["outcome"]["target_error_norm"]) for t in traces]
    assert errors[0] > errors[1] > errors[2], (
        "F5：误差反馈必须让下一步的预测误差收紧", errors)


def test_f_action_decision_is_deterministic_across_identical_worlds():
    """F6：同 seed 同构世界的行动决策逐位一致（确定性择优，无隐藏状态泄漏）。"""
    a, a_runner, a_scout = passby_world()
    b, b_runner, b_scout = passby_world()
    a.configure_action(a_runner, a_scout, step_size=1.0, policy="predictive")
    b.configure_action(b_runner, b_scout, step_size=1.0, policy="predictive")
    map_a = {a_runner: "<e0>", a_scout: "<e1>"}
    map_b = {b_runner: "<e0>", b_scout: "<e1>"}
    for tick in range(5):
        trace_a = a.step()["L7_action"]
        trace_b = b.step()["L7_action"]
        assert canon_json(trace_a, map_a) == canon_json(trace_b, map_b), (
            "F6：第 %d tick 行动留痕不一致" % (tick + 1),
            canon_json(trace_a, map_a), canon_json(trace_b, map_b))


# =============================== G 信息源约束 ===============================

class _ExplodingWorld:
    """哨兵：任何属性访问都失败——用来证明评分路径不接触世界对象。"""

    def __getattr__(self, name):
        raise AssertionError(
            "G1：决策/评分路径访问了世界对象属性 %r（应当只用观测面）" % (name,))


def test_g_scoring_path_never_touches_the_world_object():
    """G1：把 loop.world 换成访问即炸的哨兵后，决策仍能得到同一份结果。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout, step_size=1.0, policy="predictive")
    predictions = loop.explorer.predict(horizon=1)["predictions"]
    real_world = loop.world
    loop.world = _ExplodingWorld()
    try:
        guarded = loop._action.decide(loop.explorer, predictions=predictions)
    finally:
        loop.world = real_world
    reference = loop._action.decide(loop.explorer, predictions=predictions)
    assert guarded["status"] == "ok"
    assert (json.dumps(guarded, sort_keys=True, ensure_ascii=False)
            == json.dumps(reference, sort_keys=True, ensure_ascii=False)), (
        "G1：哨兵世界下的决策与正常决策不一致", guarded, reference)
    assert guarded["chosen_action"] == "east", guarded


def test_g_decision_uses_observed_not_true_target_position():
    """G2：只改物理真值目标位置、不给新观测 ⇒ 决策字段逐位不变。"""
    base, base_runner, base_scout = passby_world()
    base.configure_action(base_runner, base_scout, step_size=1.0, policy="predictive")
    base_trace = base.step()["L7_action"]

    probe, probe_runner, probe_scout = passby_world()
    probe.configure_action(probe_runner, probe_scout, step_size=1.0, policy="predictive")
    probe.world.entities[probe_scout].pos = (5.0, 1.5, 23.0)   # 真值远在南边
    probe_trace = probe.step()["L7_action"]

    assert base_trace["observation"]["target"] == [5.0, 1.5, 5.4]
    assert probe_trace["observation"]["target"] == [5.0, 1.5, 5.4], (
        "G2：决策用的目标位置必须来自观测，而不是物理真值", probe_trace["observation"])
    strip = lambda t: {k: t[k] for k in ("observation", "forecast", "candidates",
                                         "greedy_current_choice", "model_choice",
                                         "chosen_action")}
    assert (json.dumps(strip(base_trace), sort_keys=True, ensure_ascii=False)
            == json.dumps(strip(probe_trace), sort_keys=True, ensure_ascii=False)), (
        "G2：真值改变（无新观测）却改变了决策 ⇒ 评分读到了真值",
        strip(base_trace), strip(probe_trace))


def test_g_decision_ignores_hidden_behaviour_rules():
    """G3：改目标的 behavior/goal（隐藏行为规则）⇒ 决策不变，但世界演化确实变了。"""
    base, base_runner, base_scout = passby_world()
    base.configure_action(base_runner, base_scout, step_size=1.0, policy="predictive")
    base_trace = base.step()["L7_action"]
    base_true = base.world.entities[base_scout].pos

    probe, probe_runner, probe_scout = passby_world()
    probe.configure_action(probe_runner, probe_scout, step_size=1.0, policy="predictive")
    entity = probe.world.entities[probe_scout]
    entity.behavior = "seek"          # 隐藏规则：follow ⇐ seek(受控体)
    entity.goal = probe_runner
    probe_trace = probe.step()["L7_action"]
    probe_true = probe.world.entities[probe_scout].pos

    strip = lambda t: {k: t[k] for k in ("observation", "forecast", "candidates",
                                         "greedy_current_choice", "model_choice",
                                         "chosen_action")}
    assert (json.dumps(strip(base_trace), sort_keys=True, ensure_ascii=False)
            == json.dumps(strip(probe_trace), sort_keys=True, ensure_ascii=False)), (
        "G3：隐藏行为规则改变了决策 ⇒ 决策读到了 behavior/goal",
        strip(base_trace), strip(probe_trace))
    assert probe_true != base_true, (
        "G3：夹具无效——改掉 behavior/goal 后世界演化竟然没变", base_true, probe_true)


def test_g_executor_only_moves_the_controlled_entity():
    """G4：执行面只写受控实体；目标实体由自身行为演化（执行与预测分离）。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout, step_size=1.0, policy="predictive")
    target_before = loop.world.entities[scout].pos
    rec = loop.step()
    assert loop.world.entities[runner].pos == tuple(
        rec["L7_action"]["executed"]["observed_to"])
    # 目标自身行为（follow 东行 1.0）决定其位移，不受受控动作影响
    assert loop.world.entities[scout].pos == (round(target_before[0] + 1.0, 2),
                                             target_before[1], target_before[2])
    assert loop.world.commands() == {}, "G4：动作必须被单次消费"


# =============================== H 冷启动/边界 ===============================

def test_h_cold_start_is_labelled_and_never_fakes_extrapolation():
    """H1：无任何观测时仍能决策，但必须标注冷启动/学得模型未就绪。"""
    loop = SevenLayerLoop(size=24, seed=42, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(5, 1.5, 5), speed=0.0)
    beacon = loop.add_entity("beacon", behavior="wander", pos=(15, 1.5, 15), speed=0.0)
    assert loop.explorer.nodes == {}, "H1：前置条件应是零观测"
    loop.configure_action(runner, beacon, step_size=1.0, policy="predictive")
    rec = loop.step()
    trace = rec["L7_action"]
    assert trace["cold_start"] is True, trace
    assert trace["model_ready"] is False, trace
    assert trace["degraded"] == "insufficient_target_observations", trace
    assert trace["forecast"]["mode"] == "bounded_stochastic", trace["forecast"]
    assert trace["forecast"]["extrapolated"] is False, trace["forecast"]
    assert trace["forecast"]["correction_applied"] == [0.0, 0.0]
    # 仍是一步真实决策：按最新观测位置贪心，且被观测集包含必要实体
    assert trace["chosen_action"] == trace["greedy_current_choice"], trace
    assert set(rec["L7_decision"]["chosen"]) == {runner, beacon}
    assert rec["L1_perception"]["observed"] == rec["L7_decision"]["chosen"]


def test_h_warm_model_is_marked_ready_after_enough_observations():
    """H2：观测足够后冷启动标记消失、学得模型就绪（对照 H1）。"""
    loop, runner, scout = passby_world()
    loop.configure_action(runner, scout, step_size=1.0, policy="predictive")
    trace = loop.step()["L7_action"]
    assert trace["cold_start"] is False
    assert trace["model_ready"] is True
    assert "degraded" not in trace, trace
    assert trace["forecast"]["samples"] >= 1


def test_h_action_state_and_log_expose_the_evidence():
    """H3：action_state()/action_log_view() 暴露配置、累计与逐 tick 留痕。"""
    loop, runner, scout = passby_world()
    assert loop.action_state()["configured"] is False
    loop.configure_action(runner, scout, step_size=1.0, policy="predictive")
    for _ in range(3):
        loop.step()
    state = loop.action_state()
    assert state["configured"] is True
    assert state["config"]["entity"] == runner and state["config"]["target"] == scout
    assert state["config"]["decisions"] == 3
    assert state["config"]["cold_starts"] == 0
    assert state["config"]["error_samples"] == 3
    assert state["latest"] is loop.audit[-1]["L7_action"]
    assert len(loop.action_log_view(limit=2)) == 2
    assert "action" in loop.state() and loop.state()["action"]["decisions"] == 3
    loop.clear_action()
    assert "action" not in loop.state(), "H3：clear 后 state() 不应再带 action"


def test_h_run_api_works_with_actions_configured():
    """H4：run(n) 与 step() 一致（每 tick 一条 L7_action），且 ticks 语义不变。"""
    loop = SevenLayerLoop(size=24, seed=42, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(2, 1.5, 2), speed=0.0)
    beacon = loop.add_entity("beacon", behavior="wander", pos=(18, 1.5, 18), speed=0.0)
    loop.explorer.observe()
    loop.explorer.run(n=4)
    loop.configure_action(runner, beacon, step_size=1.0, policy="predictive")
    result = loop.run(n=40)
    assert result["ticks"] == 40 and result["loop_tick"] == 40
    traces = [rec["L7_action"] for rec in loop.audit]
    assert len(traces) == 40
    assert all(t["status"] == "ok" for t in traces)
    assert all(t["executed"]["self_error"] == 0.0 for t in traces)
    assert math.dist(tuple(loop.world.entities[runner].pos),
                     tuple(loop.world.entities[beacon].pos)) < 1.0


def test_h_run_bootstrap_is_a_perception_seed_not_an_action_tick():
    """H5：run() 自举段不做行动决策（只播种感知）；此后每 tick 一条行动留痕。"""
    loop = SevenLayerLoop(size=24, seed=42, window=6, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(2, 1.5, 2), speed=0.0)
    beacon = loop.add_entity("beacon", behavior="wander", pos=(18, 1.5, 18), speed=0.0)
    loop.configure_action(runner, beacon, step_size=1.0, policy="predictive")
    result = loop.run(n=5)
    assert result["ticks"] == 5 and result["loop_tick"] == 5
    assert loop.world.tick_count == 5 + loop.explorer.window, (
        "H5：自举段（window tick）不应计入闭环 tick", loop.world.tick_count)
    assert len(loop.audit) == 5, "H5：自举段不该产生七层留痕"
    assert all("L7_action" in rec for rec in loop.audit)
    first = loop.audit[0]["L7_action"]
    assert first["cold_start"] is False, (
        "H5：自举段已全带宽观测 ⇒ 首个行动 tick 不该是冷启动", first)
    assert first["model_ready"] is True, first


# ========== I 陈旧/缺失目标观测不构成新误差反馈（F08 返工契约） ==========

def _feedback_decision(correction=(0.0, 0.0), history=()):
    """返工探针用的决策器：可预置 correction / error_history（模拟已持有修正的对象）。"""
    decision = ActionDecision(entity_id="agent", target="target",
                              target_kind="entity", step_size=1.0,
                              policy="predictive", fixed_action="east")
    decision.correction = tuple(correction)
    decision.error_history = list(history)
    return decision


def _feedback_stats(decision):
    cfg = decision.config()
    return {"correction": list(decision.correction),
            "error_history": list(decision.error_history),
            "error_samples": cfg["error_samples"],
            "mean_abs_target_error": cfg["mean_abs_target_error"]}


def _settle(decision, fresh, observed_self=(2, 1, 2), observed_target=(9, 1, 5),
            predicted_self=(2, 1, 2), predicted_target=(5, 1, 5)):
    """直接结算一次上一预测；返回 (outcome, 结算前统计, 结算后统计)。"""
    trace = {"status": "ok", "chosen_action": "east",
             "executed": {"predicted_self": (None if predicted_self is None
                                             else list(predicted_self))},
             "forecast": {"predicted": (None if predicted_target is None
                                        else list(predicted_target))}}
    before = _feedback_stats(decision)
    outcome = decision.observe_outcome(
        trace,
        (None if observed_self is None else list(observed_self)),
        (None if observed_target is None else list(observed_target)),
        fresh)["outcome"]
    return outcome, before, _feedback_stats(decision)


def test_i_stale_target_observation_is_not_new_error_feedback():
    """I1：target_fresh=False 不得写 correction / error_history，也不得宣称已纠正。

    返工前的复现读数：同一探针得到 corrected=True / correction=(2.0, 0.0) /
    error_history=[4.0]（陈旧目标位置被当成新误差反馈）。
    """
    decision = _feedback_decision()
    outcome, before, after = _settle(decision, False)

    assert outcome["target_observation_fresh"] is False, outcome
    assert outcome["target_error"] is None, (
        "陈旧观测不得算出「目标误差」当新证据", outcome)
    assert outcome["target_error_norm"] is None, outcome
    assert outcome["corrected"] is False, (
        "陈旧观测不得宣称已纠正（返工前缺陷：corrected=True）", outcome)
    assert outcome["target_error_status"] == "stale_target_observation", outcome
    assert outcome["target_error_status"] in TARGET_ERROR_STATUSES, outcome
    assert outcome["target_error_reason"], "必须写明「不反馈」的理由：%r" % (outcome,)
    assert "陈旧" in outcome["target_error_reason"], outcome

    assert after == before, ("陈旧结算改动了统计", before, after)
    assert decision.correction == (0.0, 0.0), decision.correction
    assert decision.correction != (2.0, 0.0), "返工前缺陷读数（陈旧误差进了 EWMA）"
    assert decision.error_history == [], decision.error_history
    assert _feedback_stats(decision)["mean_abs_target_error"] is None


def test_i_pre_existing_correction_is_frozen_by_stale_calls():
    """I2：已持有非零 correction 的对象，反复陈旧结算后三个统计量逐项不变。"""
    decision = _feedback_decision(correction=(2.0, 0.0), history=[4.0])
    frozen = _feedback_stats(decision)
    assert frozen == {"correction": [2.0, 0.0], "error_history": [4.0],
                      "error_samples": 1, "mean_abs_target_error": 4.0}

    for _ in range(3):
        outcome, before, after = _settle(decision, False)
        assert after == before == frozen, (
            "陈旧结算改动了既有修正/统计", before, after)
        assert outcome["corrected"] is False, outcome
        assert outcome["next_correction"] == [2.0, 0.0], (
            "陈旧结算只能回显既有修正，不得推进它", outcome)
        assert outcome["target_error_status"] == "stale_target_observation", outcome


def test_i_fresh_observation_keeps_the_known_error_correction_path():
    """I3：fresh=True 的新观测照常更新——误差 1.0→0.5、修正 0.5→0 逐位保持。"""
    decision = _feedback_decision()

    first, before1, after1 = _settle(decision, True, observed_target=(6, 1, 5),
                                     predicted_target=(5, 1, 5))
    assert first["target_error"] == [1.0, 0.0], first
    assert first["target_error_norm"] == 1.0, first
    assert first["target_error_status"] == "verified", first
    assert first["corrected"] is True, first
    assert first["next_correction"] == [0.5, 0.0], first
    assert after1["correction"] == [0.5, 0.0] and after1["error_history"] == [1.0], after1

    # 下一 tick 的预测已含 0.5 修正 ⇒ 同一真值下误差 -0.5，修正回到 0（1→0.5→0）
    second, before2, after2 = _settle(decision, True, observed_target=(6, 1, 5),
                                      predicted_target=(6.5, 1, 5))
    assert second["target_error"] == [-0.5, 0.0], second
    assert second["target_error_norm"] == 0.5, second
    assert second["corrected"] is True, second
    assert second["next_correction"] == [0.0, 0.0], second
    assert after2["correction"] == [0.0, 0.0], after2
    assert after2["error_history"] == [1.0, 0.5], after2


def test_i_missing_observation_and_missing_forecast_are_labelled_only():
    """I4：目标缺观测 / 无目标预测 ⇒ 只标状态，不记账、不宣称已验证。"""
    decision = _feedback_decision(correction=(2.0, 0.0), history=[4.0])
    frozen = _feedback_stats(decision)

    for fresh in (False, True):
        outcome, before, after = _settle(decision, fresh, observed_target=None)
        assert outcome["target_error_status"] == "target_unobserved", outcome
        assert outcome["target_error_status"] in TARGET_ERROR_STATUSES, outcome
        assert outcome["target_error"] is None and outcome["corrected"] is False, outcome
        assert "观测" in outcome["target_error_reason"], outcome
        assert after == before == frozen, (fresh, before, after)

    bare = _feedback_decision()
    blank = _feedback_stats(bare)
    outcome, before, after = _settle(bare, True, predicted_target=None)
    assert outcome["target_error_status"] == "no_forecast", outcome
    assert outcome["target_error"] is None and outcome["corrected"] is False, outcome
    assert outcome["target_error_reason"], outcome
    assert after == before == blank, (before, after)


def test_i_loop_labels_a_stale_target_tick_and_freezes_feedback_statistics():
    """I5：端到端 —— 目标本轮无法被 L1 观测 ⇒ 标注陈旧、统计冻结；恢复观测后照常更新。"""
    loop = SevenLayerLoop(size=24, seed=42, budget=2)
    loop.create_scene(trees=0, water=False)
    runner = loop.add_entity("runner", behavior="wander", pos=(5, 1.5, 5), speed=0.0)
    beacon = loop.add_entity("beacon", behavior="wander", pos=(12, 1.5, 5), speed=0.0)
    loop.explorer.observe()
    loop.explorer.run(n=4)
    loop.configure_action(runner, beacon, step_size=1.0, policy="predictive")

    fresh_trace = loop.step()["L7_action"]
    assert fresh_trace["outcome"]["target_observation_fresh"] is True, fresh_trace
    assert fresh_trace["outcome"]["target_error_status"] == "verified", fresh_trace
    frozen = _feedback_stats(loop._action)
    assert frozen["error_samples"] == 1, frozen

    # 目标本轮起不在世界里 ⇒ L1 拿不到新读数（explorer.nodes 里只剩陈旧位置）
    entity = loop.world.entities.pop(beacon)
    try:
        stale_trace = loop.step()["L7_action"]
    finally:
        loop.world.entities[beacon] = entity

    outcome = stale_trace["outcome"]
    assert stale_trace["status"] == "ok", stale_trace
    assert outcome["target_observation_fresh"] is False, outcome
    assert outcome["target_error_status"] == "stale_target_observation", outcome
    assert outcome["target_error"] is None and outcome["corrected"] is False, outcome
    assert _feedback_stats(loop._action) == frozen, (
        "陈旧 tick 写入了 correction / error_history", frozen, _feedback_stats(loop._action))

    # 目标重新可观测 ⇒ 反馈恢复（不是一刀切停用）
    resumed = loop.step()["L7_action"]
    assert resumed["outcome"]["target_observation_fresh"] is True, resumed
    assert resumed["outcome"]["target_error_status"] == "verified", resumed
    assert _feedback_stats(loop._action)["error_samples"] == frozen["error_samples"] + 1


# =============================== 可复算对照数据 ===============================

def comparison_report(ticks=40):
    """可复算对照数据：predictive vs fixed（同 seed 同世界）。"""
    report = {"ticks": int(ticks), "seed": 42, "runs": {}}
    for name, builder, target_kind in (("static", static_world, "static_beacon"),
                                       ("patrol", patrol_world, "moving_scout")):
        for policy in ("predictive", "fixed"):
            loop, runner, target = builder(policy, ticks=ticks)
            actions = [rec["L7_action"]["chosen_action"] for rec in loop.audit]
            distances = [rec["L7_action"]["executed"]["distance_after"]
                         for rec in loop.audit]
            report["runs"]["%s/%s" % (name, policy)] = {
                "scenario": name, "policy": policy,
                "target_kind": target_kind,
                "runner_final": list(loop.world.entities[runner].pos),
                "target_final": list(loop.world.entities[target].pos),
                "final_distance": round(math.dist(
                    loop.world.entities[runner].pos,
                    loop.world.entities[target].pos), 4),
                "actions": actions,
                "extrapolated_ticks": sum(
                    1 for rec in loop.audit
                    if rec["L7_action"]["forecast"]["extrapolated"]),
                "final_traced_distance": distances[-1]}
    return report


def test_report_predictive_final_distance_beats_fixed_in_both_scenarios():
    """Z1：可复算对照数据自证——两个场景里 predictive 的末距都小于 fixed。"""
    report = comparison_report(ticks=40)
    for scenario in ("static", "patrol"):
        pre = report["runs"]["%s/predictive" % scenario]
        fix = report["runs"]["%s/fixed" % scenario]
        assert pre["final_distance"] < fix["final_distance"], (scenario, pre, fix)
        assert pre["final_distance"] == pre["final_traced_distance"], (
            "Z1：留痕里的末距必须等于实测末距（追踪数据自洽）", pre)
        if scenario == "patrol":
            assert pre["extrapolated_ticks"] > 0, (
                "Z1：移动目标上必须出现真外推（学得速度参与打分）", pre)
        else:
            assert pre["extrapolated_ticks"] == 0, (
                "Z1：静止目标不应被外推（预测位置＝观测位置）", pre)


if __name__ == "__main__":
    if "--report" in sys.argv:
        print(json.dumps(comparison_report(ticks=40), ensure_ascii=False, indent=1))
    else:
        sys.exit(pytest.main([__file__, "-v", "-s"]))
