# -*- coding: utf-8 -*-
"""test_issue148_233_242_world_misc4_guard —— world 三件 P2 缺陷守卫（本组件号 148/233/242）。

逐件钉住「缺陷不再存在」，而非「代码能跑」：

#148（world/curiosity_explorer · 全带宽 observe() 不记好奇簿记）
    症状：`observe(entities=None)` 直接 `return super().observe()`，基类只更新
    nodes/history——`last_observed`/`obs_counts` 不变，而下游把「无记录」当
    「刚看过」：`_prediction_bound` 陈旧度放大恒为 0（`k = ... if last else 0`）、
    `_info_gain` 新奇度恒为 1.0（`1/(1+obs_counts.get(eid,0))`）。全带宽
    observe 是正常路径（`probe()`／`compare_policies` 预热／`run` 自举）。
    修法：`entities is None` 时展开为全实体，走同一登记路径。
    守卫 G1/G2/G3；变异（改回 `return super().observe()`）⇒ 全红。

#233（world/seven_layer_loop · `run(0)` 的 ticks 与 loop_tick 自相矛盾；
      未知 policy 静默退化为轮询而审计照抄请求名）
    症状 A：`for _ in range(max(1, int(n)))` 配 `"ticks": int(n)` ⇒ `run(0)`
    的同一返回 dict 里 `ticks=0` 而 `loop_tick=1`；`run(-5)` 报 `ticks=-5`
    却已推进 1 tick。修法：`max(0, ...)` + `ticks = self.tick - start`。
    症状 B：`_select` 只认 `curiosity`/`random`，其余（含拼错 `curious`）落进
    轮询兜底且 `ig_scores={}`；审计写**请求的**字符串。修法：白名单 fail-closed
    （`round_robin` 升为显式分支，未知策略 `ValueError`）。
    守卫 G4/G5/G6；变异（还原钳制/去掉白名单）⇒ 红。

#242（world/prediction · D-006「动态阈值」在判定上恒不起作用）
    症状：`threshold = max(BASE, mean - 2*std)` 后判 `mean < threshold`，而
    `mean - 2*std ≤ mean` 恒成立 ⇒ `reflect` 与「`mean < BASE_HIT_RATE`」
    逐位等价（动态项只抬高返回的 threshold 数字）。修法：历史基线窗
    `mean_b ± 2·SE` 定阈值、**近期窗**均值定判定（σ 有下界防退化读数）。
    守卫 G7/G8/G9/G10；变异（还原旧式）⇒ 红。

判据来源：三件均以**缺陷单自身**的「期望行为／建议修法」为口径（#148 验收段、
#233 §六 判据、#242 §期望行为），另 #233 引《PR 审核细则 v0.1》§三.3 边界诚实／
§三.4 判据外置；`RECENT_WINDOW`/`MIN_STD` 为经验标定（追不到理论章节），已在
`prediction.py` docstring 如实标注。

运行（lingshu 仓根）：
    python -X utf8 -m pytest tests/test_issue148_233_242_world_misc4_guard.py -q --no-header
退出码：0 = 全过；非 0 = 有断言失败。
"""
from __future__ import annotations

import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.world.curiosity_explorer import CuriosityExplorer   # noqa: E402
from lingshu.world.prediction import PredictionEngine           # noqa: E402
from lingshu.world.seven_layer_loop import SevenLayerLoop       # noqa: E402


def _scene(ex, n_entities: int = 2):
    """最小世界：n 个 wander 实体（返回 eid 列表）。"""
    ex.world.create_scene(trees=0, water=False)
    eids = []
    for i in range(n_entities):
        eids.append(ex.world.add_entity(
            "e%d" % i, behavior="wander",
            pos=(2.0 + 8 * i, 1.5, 2.0 + 8 * i), speed=0.5))
    return eids


# ---------------------------------------------------------------------------
# #148 —— 全带宽 observe() 登记 last_observed / obs_counts
# ---------------------------------------------------------------------------

def test_148_full_bandwidth_observe_registers_books():
    """G1：无参 observe() N 次 ⇒ obs_counts[eid]==N 且 last_observed[eid]==tick。"""
    ex = CuriosityExplorer(size=24, seed=42, window=6)
    eids = _scene(ex, 2)
    for i in range(1, 6):
        ex.observe()
        for eid in eids:
            assert ex.obs_counts.get(eid) == i, (
                "#148 全带宽 observe 第 %d 次后 obs_counts[%s]=%r（改前恒缺键/为 0）"
                % (i, eid, ex.obs_counts.get(eid)))
            assert ex.last_observed.get(eid) == ex.tick, (
                "#148 last_observed[%s]=%r 须等于当前 tick=%d"
                % (eid, ex.last_observed.get(eid), ex.tick))
    assert ex.tick == 5, ex.tick


def test_148_novelty_decays_after_full_bandwidth_observe():
    """G2：新奇度分量 = 1/(1+obs_counts) 随全带宽观测下降（改前恒为 1.0）。"""
    ex = CuriosityExplorer(size=24, seed=42, window=6)
    eids = _scene(ex, 1)
    eid = eids[0]
    ex.observe()
    n1 = 1.0 / (1.0 + ex.obs_counts.get(eid, 0))
    for _ in range(4):
        ex.observe()
    n5 = 1.0 / (1.0 + ex.obs_counts.get(eid, 0))
    assert ex.obs_counts[eid] == 5, ex.obs_counts
    assert abs(n1 - 1.0 / 2) < 1e-9, "#148 首次全带宽观测后新奇度应为 1/2"
    assert abs(n5 - 1.0 / 6) < 1e-9, "#148 第 5 次后新奇度应为 1/6（改前恒 1.0）"


def test_148_staleness_amplifies_prediction_bound():
    """G3：久未观测 ⇒ bound 按 (1 + k/window) 放大（改前 last_observed 空 ⇒ 恒 1.0×）。"""
    ex = CuriosityExplorer(size=24, seed=42, window=6)
    a, b = _scene(ex, 2)
    ex.observe()                      # 全带宽：a/b 均登记（tick=1）
    ex.learn()
    fresh = ex._prediction_bound(a)
    for _ in range(21):               # 之后只看 b
        ex.world.step(n=1)
        ex.observe(entities=[b])
    ex.learn()
    stale = ex._prediction_bound(a)
    assert ex.last_observed.get(a) == 1, ex.last_observed
    assert ex.tick == 22, ex.tick
    ratio = stale / fresh
    expected = 1.0 + 21 / 6.0
    assert abs(ratio - expected) < 0.02, (
        "#148 陈旧度放大比 = %.4f，应 ≈ %.4f（改前因 last_observed 空 ⇒ 1.0）"
        % (ratio, expected))
    assert ratio > 1.5, "#148 陈旧度放大必须真实生效（改前恒 1.0）"


def test_148_non_regression_subset_and_probe_still_work():
    """非回归：有限带宽 observe 计数照旧；probe()/run() 不崩且 tick 前进。"""
    ex = CuriosityExplorer(size=24, seed=42, window=6)
    a, b = _scene(ex, 2)
    ex.observe()
    ex.observe(entities=[a])
    assert ex.obs_counts[a] == 2 and ex.obs_counts[b] == 1, ex.obs_counts
    pr = ex.probe(ticks=2)
    assert pr["probe_ticks"] == 2 and ex.tick > 3, (pr, ex.tick)


# ---------------------------------------------------------------------------
# #233 —— run(n) 的 ticks 语义 / 未知 policy fail-closed
# ---------------------------------------------------------------------------

def test_233_run_zero_does_not_advance_loop():
    """G4：run(0)/run(-5) 不推进闭环且 ticks==loop_tick 增量（改前 0 != 1 / -5）。"""
    for n in (0, -5):
        lp = SevenLayerLoop(size=24, seed=42)
        r = lp.run(n)
        assert r["ticks"] == 0, (
            "#233 run(%d) 返回 ticks=%r，应为 0（改前 -5）" % (n, r["ticks"]))
        assert r["loop_tick"] == 0, (
            "#233 run(%d) 实际 loop_tick=%d，应 0（改前偷跑 1 tick）"
            % (n, r["loop_tick"]))
        assert len(lp.audit) == 0 and lp.tick == 0, (len(lp.audit), lp.tick)


def test_233_run_ticks_equals_actual_advance():
    """G4b：ticks 恒等于本轮真实推进数（同一返回 dict 内自洽）。"""
    for n in (1, 3):
        lp = SevenLayerLoop(size=24, seed=42)
        before = lp.tick
        r = lp.run(n)
        assert r["ticks"] == r["loop_tick"] - before == n, (n, r)
        assert len(lp.audit) == n, (n, len(lp.audit))
    # 连续调用：第二次 run 的 ticks 仍是增量
    lp = SevenLayerLoop(size=24, seed=42)
    lp.run(2)
    r2 = lp.run(2)
    assert r2["ticks"] == 2 and r2["loop_tick"] == 4, r2


def test_233_unknown_policy_fails_closed():
    """G5：未知策略名必须显式失败（改前静默退化为轮询）。"""
    for bad in ("curious", "typo_xyz", "CURIOSITY", ""):
        try:
            SevenLayerLoop(size=24, seed=42, policy=bad)
        except ValueError as exc:
            assert "policy" in str(exc) or "策略" in str(exc), exc
        else:
            raise AssertionError(
                "#233 SevenLayerLoop(policy=%r) 未报错——静默降级未修复" % (bad,))
    # 兜底面同样 fail-closed（直接调 _select）
    ex = CuriosityExplorer(size=24, seed=42)
    _scene(ex, 1)
    try:
        ex._select(1, "curious")
    except ValueError:
        pass
    else:
        raise AssertionError("#233 CuriosityExplorer._select('curious') 未报错")
    # 合法三策略仍可用（round_robin 从兜底升为显式分支）
    for good in ("curiosity", "random", "round_robin"):
        lp = SevenLayerLoop(size=24, seed=42, policy=good)
        lp.create_scene(trees=1)
        lp.add_entity("player", behavior="wander", pos=(2, 1.5, 2), speed=0.5)
        lp.add_entity("wolf", behavior="wander", pos=(15, 1.5, 15), speed=0.6)
        r = lp.run(3)
        assert r["ticks"] == 3, (good, r)
        assert lp.audit[0]["L7_decision"]["policy"] == good, lp.audit[0]["L7_decision"]


def test_233_audit_policy_matches_executed_branch():
    """G6：审计里的 policy 必是**实际执行**的分支——round_robin 显式可达且 ig_scores 为空；
    curiosity 非空（改前两者都靠兜底/照抄，无法从审计区分）。"""
    def run_policy(policy, n=6):
        lp = SevenLayerLoop(size=24, seed=42, window=6, budget=2, policy=policy)
        lp.create_scene(trees=2, water=False)
        p = lp.add_entity("player", behavior="wander", pos=(2, 1.5, 2), speed=0.5)
        w = lp.add_entity("wolf", behavior="seek", pos=(15, 1.5, 15), speed=0.6, goal=p)
        lp.add_entity("rabbit", behavior="flee", pos=(10, 1.5, 10), speed=0.5, goal=w)
        lp.run(n=n)
        return lp.audit

    aud_rr = run_policy("round_robin")
    aud_cur = run_policy("curiosity")
    assert aud_rr[0]["L7_decision"]["policy"] == "round_robin", aud_rr[0]["L7_decision"]
    assert aud_rr[0]["L7_decision"]["ig_scores"] == {}, (
        "#233 round_robin 分支无 IG 分解（空 scores 是它的痕迹）", aud_rr[0]["L7_decision"])
    assert aud_cur[0]["L7_decision"]["ig_scores"], (
        "#233 curiosity 分支必须给出非空 IG 分解", aud_cur[0]["L7_decision"])
    # 轮询序 ≠ 好奇序（两者确实是不同分支，非同一兜底）
    rr_seq = [tuple(sorted(r["L7_decision"]["chosen"])) for r in aud_rr]
    cur_seq = [tuple(sorted(r["L7_decision"]["chosen"])) for r in aud_cur]
    assert rr_seq != cur_seq, (rr_seq, cur_seq)


# ---------------------------------------------------------------------------
# #242 —— D-006 动态阈值真正进入判定
# ---------------------------------------------------------------------------

def _pe(hist):
    pe = PredictionEngine(engine=None)
    pe._hit_history = list(hist)
    return pe


def test_242_reflect_is_not_equivalent_to_mean_below_base():
    """G7：存在序列使 reflect 与「mean < BASE」不同（改前 20000 组 0 次不一致）。"""
    # 历史全命中 + 近期全崩：全历史 mean=0.8 ≥ BASE，近期窗=0 ⇒ 应反思
    r = _pe([True] * 80 + [False] * 20)._dynamic_hit_threshold()
    assert r["mean"] >= PredictionEngine.BASE_HIT_RATE, r
    assert r["reflect"] is True, (
        "#242 历史高、近期崩塌必须 reflect=True（改前恒 False）", r)
    # 历史全崩 + 近期全命中：全历史 mean=0.2 < BASE，近期窗=1 ⇒ 不应反思
    r2 = _pe([False] * 80 + [True] * 20)._dynamic_hit_threshold()
    assert r2["mean"] < PredictionEngine.BASE_HIT_RATE, r2
    assert r2["reflect"] is False, (
        "#242 近期已恢复不得误报（改前恒 True）", r2)


def test_242_reflect_differs_from_legacy_predicate_on_random_scan():
    """G7b：随机扫描下 reflect 必须**有**与旧判据分歧的组（旧式 0 分歧）。"""
    rng = random.Random(0)
    diff = 0
    for _ in range(3000):
        n = rng.randint(50, 200)
        p = rng.random()
        pe = _pe([rng.random() < p for _ in range(n)])
        r = pe._dynamic_hit_threshold()
        diff += r["reflect"] != (r["mean"] < pe.BASE_HIT_RATE)
    assert diff > 0, (
        "#242 reflect 与「mean<BASE」逐位等价（旧式 0 分歧）——动态项未进判定")


def test_242_threshold_not_degenerate_on_zero_variance():
    """G8：全命中基线（σ=0）不得报出 threshold=1.0 退化读数。"""
    r = _pe([True] * 100)._dynamic_hit_threshold()
    assert r["threshold"] < 1.0, (
        "#242 全命中时 threshold=%r（改前 1.0 退化）" % (r["threshold"],))
    assert r["threshold"] >= PredictionEngine.BASE_HIT_RATE, r
    # 近期也全命中 ⇒ 不反思（threshold 下界效应仍高于 1.0 不可达，判定用近期均值）
    assert r["reflect"] is False, r


def test_242_contract_fields_preserved_and_sample_gate_intact():
    """非回归：样本 < MIN_SAMPLES 不反思；mean 仍为全历史均值；新增字段自洽。"""
    r = _pe([True] * 10 + [False] * 10)._dynamic_hit_threshold()
    assert r["samples"] == 20 and r["reflect"] is False, r
    assert r["threshold"] == PredictionEngine.BASE_HIT_RATE, r
    r2 = _pe([True] * 80 + [False] * 20)._dynamic_hit_threshold()
    assert r2["mean"] == 0.8, r2
    assert r2["recent_mean"] == 0.0 and r2["base_mean"] == 1.0, r2
    assert r2["base_samples"] == 80 and r2["samples"] == 100, r2


if __name__ == "__main__":
    import traceback

    failed = 0
    for _name in sorted(n for n in list(globals()) if n.startswith("test_")):
        try:
            globals()[_name]()
            print("  PASS " + _name)
        except Exception as exc:                                   # noqa: BLE001
            failed += 1
            print("  FAIL %s: %s: %s" % (_name, type(exc).__name__, exc))
            traceback.print_exc()
    print("\n%s — %d failed" % ("FAILED" if failed else "OK", failed))
    sys.exit(1 if failed else 0)
