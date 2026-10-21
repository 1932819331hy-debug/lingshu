# -*- coding: utf-8 -*-
"""test_issue732_budget_zero_honest · `budget` 0/负数被 `max(1, ...)` 静默升为 1（#732）

缺陷（#732，改前实测复现）：`_select` 用 `b = max(1, min(int(budget), n))` 把
`budget <= 0` 静默升为 1，而 `explore` 的返回面又用 `int(budget)` 回填**原始输入**
⇒ 观测面与报数面分叉：

    [改前] ex.explore(ticks=3, budget=0) → 返回 budget=0，
           但每 tick 的 chosen 各含 1 个实体、obs_counts 每 tick +1

判据（本件）：
  G1 `_select(0|负数, 三策略)` 一律返回**空选择**（零带宽），不抛异常；
  G2 `explore(ticks=3, budget=0)`：每 tick `chosen == []`、观测计数零增、
     返回 `effective_budget == 0`，且 `budget` 与观测面**一致**；
  G3 负数钳 0（与 `explore(ticks)` 的既有「负数钳 0 不抛」口径一致）；
  G4 正对照（防过修）：`budget=2` 恰 2 个；`budget > n` 封顶 n；
     空世界仍 `[]`（#55 路径）；`ticks=0` 零推进（#497 路径）；round_robin
     零带宽不推进轮询指针；
  G5 报数面自洽：`budget` 如实回填输入，`effective_budget ==
     min(max(0, budget), 实体数)`，且 `budget>0` 时与每 tick 实际 chosen 数一致。

判据来源：issue #732 正文（实测 + 建议「`budget<=0` 时返回 `[]`（零带宽）或
`raise ValueError`；返回值如实报 `effective_budget`」）。**取向裁定＝零带宽不抛**：
  · 与同模块 `explore(ticks)` 的既有口径（#497：负数钳 0、不抛）一致；
  · 与 `_select` 空世界路径（#55：无候选 ⇒ `[]`）同一语义；
  · `raise` 会让「带宽=0」这一合法实验配置无法表达，且 `_select` 的调用方
    （`seven_layer_loop.step`）不预期异常。
同族点 `seven_layer_loop.py` 的 `self.budget = max(1, int(budget))` 属另一文件，
不在本条修复面（按派工书 §2.2 只报告、不扩面）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue732_budget_zero_honest.py -q
"""
from __future__ import annotations

from lingshu.world.curiosity_explorer import CuriosityExplorer

POLICIES = ("curiosity", "random", "round_robin")


def _explorer(n_entities=3, size=24, seed=42):
    ex = CuriosityExplorer(size=size, ground_level=1, seed=seed, window=6)
    ex.world.create_scene(trees=0, water=False)
    for i in range(n_entities):
        ex.world.add_entity("e%d" % i, behavior="wander",
                            pos=(2.0 + 8 * i, 1.5, 2.0 + 8 * i), speed=0.5)
    return ex


# ---------------------------------------------------------------------------
# G1 —— 零带宽：三策略空选择
# ---------------------------------------------------------------------------

def test_732_select_zero_and_negative_budget_is_empty():
    """G1：`_select(0, ·)` 与 `_select(-2, ·)` 三策略都返回 `[]`（改前各 1 个）。"""
    ex = _explorer(3)
    assert len(ex.world.entities) == 3, "硬性前提：世界非空（防 for 空转成假绿）"
    for b in (0, -2):
        for pol in POLICIES:
            chosen, _scores = ex._select(b, pol)
            assert chosen == [], (
                "#732 _select(%d, %r) 返回 %r——零带宽必须空选择（改前静默升为 1）"
                % (b, pol, chosen))


# ---------------------------------------------------------------------------
# G2 —— explore 的观测面与报数面一致
# ---------------------------------------------------------------------------

def test_732_explore_zero_budget_observes_nothing_and_reports_zero():
    """G2：`explore(ticks=3, budget=0)` ⇒ 每 tick chosen 空、观测零增、报 0。"""
    ex = _explorer(3)
    before = sum(ex.obs_counts.values())
    assert len(ex.world.entities) == 3, "硬性前提：实体数应为 3"
    r = ex.explore(ticks=3, budget=0)
    lens = [len(entry["chosen"]) for entry in ex.exploration_log]
    assert len(lens) == 3, "构造前提失败：应有 3 条探索日志，实为 %d" % len(lens)
    assert lens == [0, 0, 0], (
        "#732 声明零带宽却每 tick 观测 %r 个（改前 [1, 1, 1]）" % (lens,))
    assert sum(ex.obs_counts.values()) - before == 0, (
        "#732 budget=0 不得产生任何观测（改前 +3）：%r" % (dict(ex.obs_counts),))
    assert r["budget"] == 0, "#732 返回 budget 应如实报输入 0，实为 %r" % r["budget"]
    assert r["effective_budget"] == 0, (
        "#732 返回 effective_budget 应为 0，实为 %r" % r.get("effective_budget"))
    assert ex.tick == 3, "构造前提失败：世界 tick 应推进 3（零带宽仍推进时间）"


def test_732_explore_zero_budget_all_policies():
    """G2b：三策略同形——零带宽都不观测、都报 effective_budget=0。"""
    for pol in POLICIES:
        ex = _explorer(3)
        before = sum(ex.obs_counts.values())
        r = ex.explore(ticks=2, budget=0, policy=pol)
        assert all(not entry["chosen"] for entry in ex.exploration_log), (
            "#732 policy=%r 零带宽仍有观测：%r"
            % (pol, [entry["chosen"] for entry in ex.exploration_log]))
        assert sum(ex.obs_counts.values()) == before, (pol, dict(ex.obs_counts))
        assert r["effective_budget"] == 0, (pol, r)


# ---------------------------------------------------------------------------
# G3 —— 负数钳 0
# ---------------------------------------------------------------------------

def test_732_negative_budget_clamped_to_zero_without_raising():
    """G3：负数同 0（不抛、不观测），与 `explore(ticks)` 的钳 0 口径一致。"""
    ex = _explorer(3)
    before = sum(ex.obs_counts.values())
    r = ex.explore(ticks=2, budget=-5)
    assert r["budget"] == -5, "#732 报数面如实回填输入（-5），实为 %r" % r["budget"]
    assert r["effective_budget"] == 0, r.get("effective_budget")
    assert sum(ex.obs_counts.values()) == before, dict(ex.obs_counts)
    assert all(not entry["chosen"] for entry in ex.exploration_log)
    for pol in POLICIES:
        assert ex._select(-5, pol)[0] == [], pol


# ---------------------------------------------------------------------------
# G4 —— 正对照（防过修）
# ---------------------------------------------------------------------------

def test_732_control_positive_budget_still_selects():
    """G4a：`budget=2` 每 tick 恰 2 个；`budget>n` 封顶 n（改前行为不变）。"""
    ex = _explorer(3)
    r = ex.explore(ticks=3, budget=2)
    lens = [len(entry["chosen"]) for entry in ex.exploration_log]
    assert lens == [2, 2, 2], "#732 正对照失败：budget=2 应恰 2 个，实为 %r" % (lens,)
    assert r["effective_budget"] == 2, r.get("effective_budget")
    assert sum(ex.obs_counts.values()) == 6, dict(ex.obs_counts)

    ex2 = _explorer(3)
    r2 = ex2.explore(ticks=2, budget=5)
    assert r2["effective_budget"] == 3, (
        "#732 effective_budget 应受实体数封顶（3），实为 %r" % r2.get("effective_budget"))
    assert all(len(entry["chosen"]) == 3 for entry in ex2.exploration_log), (
        [entry["chosen"] for entry in ex2.exploration_log])


def test_732_control_empty_world_and_zero_ticks_unchanged():
    """G4b：空世界仍 `[]`（#55 路径）；`ticks=0` 零推进（#497 路径）。"""
    empty = CuriosityExplorer(size=24, seed=42, window=6)
    empty.world.create_scene(trees=0, water=False)
    for pol in POLICIES:
        assert empty._select(2, pol)[0] == [], pol
    r = empty.explore(ticks=2, budget=2)
    assert r["effective_budget"] == 0, (
        "#732 空世界 effective_budget 应 0（无实体），实为 %r" % r.get("effective_budget"))
    assert empty.tick == 2 and all(not entry["chosen"]
                                   for entry in empty.exploration_log)

    ex = _explorer(3)
    t0 = ex.tick
    r0 = ex.explore(ticks=0, budget=2)
    assert r0["ticks"] == 0 and ex.tick == t0 and ex.uncertainty_curve == [], r0


def test_732_control_round_robin_pointer_not_advanced_on_zero_budget():
    """G4c：零带宽下轮询指针不推进（`(idx+0) % n`），下一轮仍从原处开始。"""
    ex = _explorer(3)
    idx0 = ex._rr_index
    ex._select(0, "round_robin")
    assert ex._rr_index == idx0, (
        "#732 零带宽不应推进轮询指针：%r → %r" % (idx0, ex._rr_index))
    chosen, _ = ex._select(1, "round_robin")
    assert len(chosen) == 1 and ex._rr_index == (idx0 + 1) % 3, (chosen, ex._rr_index)


# ---------------------------------------------------------------------------
# G5 —— 报数面自洽
# ---------------------------------------------------------------------------

def test_732_effective_budget_formula_matches_selection():
    """G5：`effective_budget == min(max(0, budget), n)`，且 `budget>0` 时与
    每 tick 实际 chosen 数一致。"""
    ex = _explorer(4)
    n = len(ex.world.entities)
    for budget in (0, -3, 1, 4, 9):
        exp = min(max(0, budget), n)
        r = ex.explore(ticks=1, budget=budget)
        assert r["budget"] == budget, (budget, r["budget"])
        assert r["effective_budget"] == exp, (
            "#732 effective_budget=%r，应为 min(max(0,%d),%d)=%r"
            % (r.get("effective_budget"), budget, n, exp))
        assert len(ex.exploration_log[-1]["chosen"]) == exp, (
            "#732 报数面 effective_budget=%r 与观测面 chosen=%r 不一致"
            % (exp, ex.exploration_log[-1]["chosen"]))


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest",
         __file__, "-q", "--no-header"]))
