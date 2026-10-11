# -*- coding: utf-8 -*-
"""test_spacetime_consistency_guards · `spacetime_consistency.py` 四条 P1 缺陷守卫
============================================================================
本文件钉住 `lingshu/world/spacetime_consistency.py` 的四个缺陷「不再存在」
（不是「代码能跑」）。四条缺陷同属本文件，故守卫合在一件：

  #9  （成立）漂移检测在 N≥4 实体时看不到「部分实体长期漂移」
      旧判据：`_update_drift(rolling)`，rolling = **全体实体平均**窗口命中率。
      N=4 且 1 个实体永久预测失败时 rolling=(N-1)/N=0.75 > drift_rate(0.7)
      ⇒ 恒不触发（只能捕捉「全体同时崩塌」）。
      判据来源：`lingshu` triage #9（判定成立）；算术见 `_worst_entity_rate`
      docstring。

  #22 （成立）「无观测 = 满分」：`… if <集> else 1.0` 的率类兜底
      本文件 4 处（`step_verified.rate` / `_rolling_rate` / `overall_hit_rate`
      / `per_behavior_rates[*].rate`）在分母为 0 时回 1.0，使
      `overall < consistent_rate` 与 `rolling < drift_rate` **两支永假**
      ⇒ 空世界跑满 tick 被判 `self_consistent`（判决级假阳性）。
      修法：无样本 ⇒ `None` + 显式标记（复用仓内既有约定，见
      `docs/plans/待裁清单_v0.1.md` D-63/D-64/D-65 与 C-11）。
      **本件只改 `spacetime_consistency.py` 内的 4 处**；#22 另 12 处在
      其余 5 个文件（各属其它并行组的主文件），不在本件守卫面内。

  #303（成立）自洽判定无视世界状态：`_invariant_violations` 只进报告、不进
      verdict ⇒ 不变量已被破坏（实体出界/入地/非有限）时仍可判
      `self_consistent`。判据来源：triage #303（判定成立）。

  #25 （成立）`SpacetimeConsistency.__init__` 未纳入 seed 轴：签名里没有
      `seed`，构造 `SceneSimulator` 时丢弃种子 ⇒ 场景演化在结构上不可做
      seed 维度消融（`SpacetimeConsistency(seed=...)` 直接 TypeError）。
      修法：新增 `seed: int = 42` 并透传给 `SceneSimulator`（缺省 42 与
      `scene_simulator.py` 历史硬编码逐位一致，默认行为不变）。
      判据来源：triage #25（判定成立）§三判据 1–4。本件守卫**只认 seed 透传**
      这一条通路——`SceneSimulator` 本体（#12）不提供 seed 之外的第二随机源，
      本类自身 `random` 出现 0 次，故不存在与别批守卫的代偿。

  #427（成立·资源耗尽）`_results` 每 tick 追加、永不裁剪，且
      `overall_hit_rate`/`per_behavior_rates` 每次全量遍历它 ⇒ 内存与报告耗时
      随 tick 线性增长。修法：`_results` 改有界环形缓冲（`HISTORY_MAXLEN`），
      聚合改由终身累积计数器承担（口径不随裁剪而变）。
      判据来源：triage #427；上限值经验标定（追不到理论章节），与
      `scene_simulator.HISTORY_MAXLEN` 同量级。

运行（lingshu 仓根）——脚本/pytest 双模式：
    python -X utf8 tests/test_spacetime_consistency_guards.py     # 脚本式，退出码 0/1
    python -X utf8 -m pytest tests/test_spacetime_consistency_guards.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world.spacetime_consistency import (  # noqa: E402
    SpacetimeConsistency, HISTORY_MAXLEN,
)

_PASS, _FAIL = [], []


def ok(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


def _is_numeric_full(x) -> bool:
    """是否为数值满分 1.0（排除 True/False 与 None）。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == 1.0


# ==================== #9：部分实体长期漂移必须可检出 ====================

def _four_entities_one_permanently_wrong():
    """4 实体；其中 1 个每 tick 被外部瞬移到两个远角 ⇒ 该实体永久预测失败。"""
    sp = SpacetimeConsistency(size=24, window=20, drift_rate=0.7, drift_ticks=5)
    sp.scene.create_scene(trees=0, water=False)
    ids = [sp.add_entity("actor", behavior="wander",
                         pos=(4 + i * 3, 1.5, 4), speed=0.3)
           for i in range(4)]
    bad = ids[0]
    last = None
    for t in range(30):
        sp.teleport(bad, (22.0, 1.5, 22.0) if t % 2 == 0 else (2.0, 1.5, 2.0))
        last = sp.step_verified()
    return sp, last, bad


def group_9_partial_drift_detected():
    print("\n#9：N=4 且 1 实体永久预测失败 —— 漂移必须被检出")
    sp, last, bad = _four_entities_one_permanently_wrong()
    # 复现前提：全体平均仍在阈值之上（旧判据因此永不触发）
    ok("#9-A1 复现前提：全体平均窗口命中率 > drift_rate（旧判据看不见）",
       last["rolling"] is not None and last["rolling"] > sp.drift_rate,
       "rolling=%r drift_rate=%r" % (last["rolling"], sp.drift_rate))
    # 修复点：最差实体窗口命中率必须低于阈值
    ok("#9-A2 最差实体窗口命中率 < drift_rate（新判据看得见）",
       last["worst_entity_rate"] is not None
       and last["worst_entity_rate"] < sp.drift_rate,
       "worst=%r" % last["worst_entity_rate"])
    ok("#9-A3 漂移被检出（drift_active 为真）", sp.drift_active() is True,
       "drift_active=%r" % sp.drift_active())
    rep = sp.consistency_report()
    ok("#9-A4 报告 verdict 为 drift_detected", rep["verdict"] == "drift_detected",
       rep["verdict"])
    ok("#9-A5 该实体确为最差者（bad 在窗口内命中率为 0）",
       sp._entity_hits[bad].count(1) == 0,
       list(sp._entity_hits[bad]))


def group_9_no_false_positive():
    print("#9：全体预测良好 —— 不得误报漂移")
    sp = SpacetimeConsistency(size=24, window=20, drift_rate=0.7, drift_ticks=5)
    sp.scene.create_scene(trees=0, water=False)
    for i in range(4):
        sp.add_entity("actor", behavior="wander", pos=(4 + i * 3, 1.5, 4),
                      speed=0.3)
    for _ in range(30):
        last = sp.step_verified()
    ok("#9-B1 良好世界 worst_entity_rate 不低于阈值",
       last["worst_entity_rate"] is not None
       and last["worst_entity_rate"] >= sp.drift_rate,
       "worst=%r" % last["worst_entity_rate"])
    ok("#9-B2 良好世界不触发漂移", sp.drift_active() is False,
       "drift_active=%r" % sp.drift_active())


# ==================== #22：无观测不得记满分 ====================

def group_22_no_observation_not_full_marks():
    print("\n#22：空世界（无任何可比实体）—— 率字段不得为 1.0")
    sp = SpacetimeConsistency(min_consistent_ticks=1)
    rec = sp.step_verified()
    ok("#22-A1 step_verified：total=0 时 rate 为 None（非 1.0）",
       rec["total"] == 0 and rec["rate"] is None,
       "total=%r rate=%r" % (rec["total"], rec["rate"]))
    ok("#22-A2 step_verified：rate 不得是数值满分", not _is_numeric_full(rec["rate"]),
       rec["rate"])
    ok("#22-A3 rolling_hit_rate() 为 None（非 1.0）",
       sp.rolling_hit_rate() is None, sp.rolling_hit_rate())
    ok("#22-A4 overall_hit_rate() 为 None（非 1.0）",
       sp.overall_hit_rate() is None, sp.overall_hit_rate())
    pbr = sp.per_behavior_rates()
    bad = {b: v["rate"] for b, v in pbr["per_behavior"].items()
           if v["outcomes"] == 0 and _is_numeric_full(v["rate"])}
    ok("#22-A5 per_behavior_rates：空桶 rate 不得为数值满分", not bad, bad)
    ok("#22-A6 空桶 rate 为 None（确定性/随机汇总桶）",
       pbr["deterministic"]["rate"] is None and pbr["stochastic"]["rate"] is None,
       (pbr["deterministic"]["rate"], pbr["stochastic"]["rate"]))
    rep = sp.consistency_report()
    ok("#22-A7 同体自洽：overall 为 None 时不得自报 self_consistent",
       rep["overall_hit_rate"] is None and rep["self_consistent"] is False,
       {"overall": rep["overall_hit_rate"], "self_consistent": rep["self_consistent"]})
    ok("#22-A8 判决前置独立支：verdict 为 no_observation",
       rep["verdict"] == "no_observation", rep["verdict"])
    # 跑满 min_consistent_ticks 后仍不得翻成 self_consistent（判决级假阳性回归点）
    sp.run(3)
    rep2 = sp.consistency_report()
    ok("#22-A9 空世界跑满 tick 后仍非 self_consistent（判决级假阳性）",
       rep2["sustained"] is True and rep2["self_consistent"] is False,
       {"sustained": rep2["sustained"], "verdict": rep2["verdict"]})
    ok("#22-A10 run() 的 rolling_hit_rate 为 None（非 1.0）",
       sp.run(1)["rolling_hit_rate"] is None, sp.run(1)["rolling_hit_rate"])


def group_22_scored_world_unchanged():
    print("#22：有观测世界 —— 率仍为数值且口径不变")
    sp = SpacetimeConsistency(size=24)
    sp.scene.create_scene(trees=0, water=False)
    sp.add_entity("actor", behavior="wander", pos=(5, 1.5, 5), speed=0.3)
    rec = sp.step_verified()
    ok("#22-B1 有实体 ⇒ rate 为数值（非 None）",
       rec["total"] == 1 and isinstance(rec["rate"], float), rec["rate"])
    ok("#22-B2 overall_hit_rate 为数值且落在 [0,1]",
       isinstance(sp.overall_hit_rate(), float)
       and 0.0 <= sp.overall_hit_rate() <= 1.0, sp.overall_hit_rate())


# ==================== #303：verdict 必须看世界状态 ====================

def group_303_invariant_violation_changes_verdict():
    print("\n#303：世界状态不变量被破坏 —— verdict 不得仍判自洽")
    sp = SpacetimeConsistency(size=24, min_consistent_ticks=1,
                              consistent_rate=0.0)
    sp.scene.create_scene(trees=0, water=False)
    sp.add_entity("actor", behavior="wander", pos=(0, 0, 12), speed=0.0)
    rec = sp.step_verified()
    ok("#303-A1 复现前提：不变量确被破坏（below_ground）",
       rec["invariants_ok"] is False
       and any("below_ground" in i for i in rec["invariant_issues"]),
       rec["invariant_issues"])
    rep = sp.consistency_report()
    ok("#303-A2 不变量违例计数进报告", rep["invariant_violations"] >= 1,
       rep["invariant_violations"])
    ok("#303-A3 verdict 为 invariant_violated（不再无视世界状态）",
       rep["verdict"] == "invariant_violated", rep["verdict"])
    ok("#303-A4 self_consistent 为 False",
       rep["self_consistent"] is False, rep["self_consistent"])


# ==================== #25：seed 轴必须接通 ====================

def _seed_traj(seed):
    """同 seed 构造 → 有实体非空场景跑 5 tick，返回 (位置轨迹, 率读数)。

    比较键按**插入序取位置**——【禁止】按 entity.id（`SceneEntity.id` 用
    uuid4，两次实例化必不同，见 triage #25 §一补充）。空世界不得用于本判据
    （#22：空世界 rate 恒 None/满分会让断言恒真）。
    旧码无 `seed` 参数 ⇒ TypeError，返回哨兵 (None, None, None) 使断言红而非崩。
    `seed=None` 表示**不传 seed**（缺省构造），用于 A7 缺省等价对照。
    """
    try:
        sp = (SpacetimeConsistency(size=24) if seed is None
              else SpacetimeConsistency(size=24, seed=seed))
    except TypeError:
        return None, None, None
    sp.scene.create_scene(trees=0, water=False)
    eid = sp.add_entity("rabbit", behavior="wander", pos=(2, 1.5, 2), speed=0.5)
    traj = []
    for _ in range(5):
        sp.step_verified()
        traj.append(tuple(round(v, 12) for v in sp.scene.entities[eid].pos))
    return traj, sp.overall_hit_rate(), sp.rolling_hit_rate()


def _rng_initial_state(seed):
    """构造实例并读 `scene._rng` 初态；旧码无 seed ⇒ None（不崩）。"""
    try:
        return SpacetimeConsistency(size=24, seed=seed).scene._rng.random()
    except TypeError:
        return None


def group_25_seed_axis_connected():
    print("\n#25：seed 轴 —— 同 seed 可复现 / 异 seed 必分叉 / 缺省等价 42")
    # A1：签名收下 seed（旧码 TypeError: unexpected keyword argument 'seed'）
    try:
        sp = SpacetimeConsistency(seed=7)
        accepted = True
    except TypeError as e:
        accepted, sp = False, e
    ok("#25-A1 签名接受 seed（旧码 TypeError）", accepted is True, sp)
    # A2：seed 确实透传到 SceneSimulator（不是收下后丢弃）
    s7a, s7b = _rng_initial_state(7), _rng_initial_state(7)
    ok("#25-A2 seed 透传至 scene._rng（同 seed 两实例 rng 初态一致）",
       s7a is not None and s7a == s7b, (s7a, s7b))
    # A3：同 seed 逐位可复现（位置轨迹 + 率读数）
    t1, o1, r1 = _seed_traj(42)
    t2, o2, r2 = _seed_traj(42)
    ok("#25-A3 同 seed 位置轨迹逐位相同", t1 is not None and t1 == t2, (t1, t2))
    ok("#25-A4 同 seed 率读数逐位相同", (o1, r1) == (o2, r2),
       (o1, r1, o2, r2))
    # A5：异 seed 必须分叉（收了但没透传会静默退回硬编码 —— 用它钉住）
    t3, _, _ = _seed_traj(7)
    ok("#25-A5 异 seed 位置轨迹必须分叉", t1 is not None and t1 != t3, (t1, t3))
    # A6：缺省等价 seed=42（默认行为不变的回归护栏）
    td, _, _ = _seed_traj(None)                      # 缺省构造（不传 seed）
    s42 = _rng_initial_state(42)                     # 显式 seed=42
    try:
        sdef = SpacetimeConsistency(size=24).scene._rng.random()
    except TypeError:
        sdef = None
    ok("#25-A6 缺省 seed 等价 42（scene._rng 初态一致）",
       sdef is not None and sdef == s42, (sdef, s42))
    ok("#25-A7 缺省轨迹 == seed=42 轨迹",
       td is not None and td == t1, (td, t1))
    # A8：本类自身不新建第二随机源（静态事实，防代偿/回归）
    src = (os.path.join(REPO, "lingshu", "world",
                        "spacetime_consistency.py"))
    text = open(src, encoding="utf-8").read()
    ok("#25-A8 本模块不出现 'random'（随机性全部来自 scene._rng）",
       "random" not in text, "occurrences=%d" % text.count("random"))


# ==================== #427：_results 必须有界 ====================

def group_427_results_bounded():
    print("\n#427：长期运行 —— 验证记录必须有界且聚合口径不丢")
    cap = 50
    sp = SpacetimeConsistency(size=24, max_results=cap)
    sp.scene.create_scene(trees=0, water=False)
    sp.add_entity("actor", behavior="wander", pos=(5, 1.5, 5), speed=0.3)
    sp.run(200)
    ok("#427-A1 _results 长度被钉在上限内（旧实现为 200）",
       len(sp._results) <= cap, "len=%d cap=%d" % (len(sp._results), cap))
    ok("#427-A2 默认上限 HISTORY_MAXLEN 为正整数", HISTORY_MAXLEN >= 1,
       HISTORY_MAXLEN)
    ok("#427-A3 裁剪后总体聚合仍覆盖全部 200 tick（不丢样本）",
       sp._agg_outcomes == 200, sp._agg_outcomes)
    ok("#427-A4 分行为聚合亦覆盖全部 200 次判定",
       sp.per_behavior_rates()["per_behavior"]["wander"]["outcomes"] == 200,
       sp.per_behavior_rates()["per_behavior"])
    ok("#427-A5 prediction_history 仍可读（取最近 limit 条）",
       len(sp.prediction_history(10)) == 10, len(sp.prediction_history(10)))


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_9_partial_drift_detected()
    group_9_no_false_positive()
    group_22_no_observation_not_full_marks()
    group_22_scored_world_unchanged()
    group_303_invariant_violation_changes_verdict()
    group_25_seed_axis_connected()
    group_427_results_bounded()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（#9 逐实体漂移可检出 / #22 无样本非满分 / "
          "#303 verdict 看世界状态 / #25 seed 轴接通 / #427 记录有界）")
    return 0


def test_spacetime_consistency_guards():
    """pytest 入口（脚本/pytest 双模式，与仓内同惯例）。"""
    assert run_checks() == 0


if __name__ == "__main__":
    sys.exit(run_checks())
