# -*- coding: utf-8 -*-
"""test_issue728_world_learner_ticks_guard —— `WorldLearner.run` 的 `ticks` 字段守卫。

缺陷（lingshu#728 · P2）
--------------------------------------------------------------------------
`WorldLearner.run(n)` 的主体循环已 `max(0, int(n))` 钳零步，但返回
`{"ticks": int(n), ...}` 未同步 ⇒ `run(-5)` 同一个返回 dict 里
`ticks=-5` 而 `tick=0`（**声称推进 -5、实际 0 迭代**，自相矛盾）。
#233(A) 修的是 `seven_layer_loop.run` 与 `curiosity_explorer.explore`，
`world_learner.run` 这个独立基类站点漏网（`CuriosityExplorer` 继承它，
故 `explorer.run(n)` 的返回体同样带这个矛盾）。

实现侧口径（本守卫的判据）
--------------------------------------------------------------------------
`ticks` 的语义＝**本轮真实推进的 tick 数**（`self.tick` 增量），与
`seven_layer_loop.run` 的 #233(A) 口径一致（#233(A) docstring 原文：
「返回的 `"ticks"` 改为**真实推进的闭环 tick 数**（`self.tick` 增量）」）。
`n < 0` 的处理＝**显式降级为零步并如实报 0**，**不是**把负数静默升级成 1 步
（`max(1, ...)` 那类"看起来合理"的值会凭空造出一步世界推进）。
依据：issue #728 正文「反向：n≥0 时 ticks 与实测一致」＋
《PR 审核细则 v0.1》§三.3 边界诚实／§三.4 判据外置（字段语义由断言钉住）。

守卫用例
--------------------------------------------------------------------------
G1 正对照（量具能测到已知正确形态）：`run(3)` ⇒ `ticks=3`、`tick` 增量 3、
   世界 Δ=3、观测史 +3（改前也过 —— 防"恒 0 假绿"）。
G2 正对照：`run(0)` ⇒ `ticks=0` 且世界不动（零步的既有正确形态）。
G3 缺陷态：`run(-5)` ⇒ `ticks=0`（改前 -5）且 `tick=0`、世界 Δ=0、观测史不变。
G4 自洽性：`ticks == tick 增量`，对 n ∈ {-5, -1, 0, 1, 3} 全部成立。
G5 范围控制（防过修）：`run(0)` 不得偷跑 1 步（`max(1, ...)` 式静默升级会被
   本组打红）；连续两次 `run(2)` 的第二次仍报增量 2。

运行（lingshu 仓根）：
    python -X utf8 -m pytest tests/test_issue728_world_learner_ticks_guard.py -q --no-header
退出码：0 = 全过；非 0 = 有断言失败。
"""
from __future__ import annotations

from lingshu.world.world_learner import WorldLearner


def _fresh_learner():
    """全新学习者 + 2 个 wander 实体（世界非空，防 Δ 恒 0 的假绿）。"""
    wl = WorldLearner(size=24, seed=42)
    wl.world.create_scene(trees=0, water=False)
    wl.world.add_entity("player", behavior="wander", pos=(2, 1.5, 2), speed=0.5)
    wl.world.add_entity("wolf", behavior="wander", pos=(15, 1.5, 15), speed=0.6)
    assert wl.world.tick_count == 0, wl.world.tick_count
    assert wl.history == [], wl.history
    assert len(wl.world.entities) >= 2, list(wl.world.entities)
    return wl


def test_728_positive_control_positive_n_matches_measurement():
    """G1 正对照：`run(3)` 的 `ticks` 与实测推进逐项一致。"""
    wl = _fresh_learner()
    r = wl.run(3)
    assert r["ticks"] == 3 and r["tick"] == 3, r
    assert wl.world.tick_count == 3, (
        "#728 正对照失败：run(3) 的世界推进 = %d，应 3（探针须测得到真实推进）"
        % (wl.world.tick_count,))
    assert len(wl.history) == 3, len(wl.history)
    assert wl.tick == 3, wl.tick


def test_728_positive_control_zero_n_is_zero_step():
    """G2 正对照：`run(0)` 零步且世界/观测史不动（既有正确形态）。"""
    wl = _fresh_learner()
    r = wl.run(0)
    assert r["ticks"] == 0 and r["tick"] == 0, r
    assert wl.world.tick_count == 0 and wl.history == [], (wl.world.tick_count,
                                                           wl.history)


def test_728_negative_n_reports_actual_advance():
    """G3 缺陷态：`run(-5)` 的 `ticks` 必须如实为 0（改前 -5）。"""
    wl = _fresh_learner()
    r = wl.run(-5)
    assert r["ticks"] == 0, (
        "#728 run(-5) 返回 ticks=%r，应为 0（声称推进 -5 而实际 0 迭代）：%r"
        % (r["ticks"], r))
    assert r["tick"] == 0, r
    assert wl.tick == 0, wl.tick
    assert wl.world.tick_count == 0, (
        "#728 run(-5) 的世界推进 = %d，应为 0" % (wl.world.tick_count,))
    assert wl.history == [], wl.history


def test_728_ticks_always_equals_actual_advance():
    """G4 自洽性：`ticks == tick 增量`，含负数/零/正数三档。"""
    for n in (-5, -1, 0, 1, 3):
        wl = _fresh_learner()
        before = wl.tick
        r = wl.run(n)
        assert r["ticks"] == wl.tick - before, (
            "#728 run(%d) 的 ticks=%r 与实测增量 %d 不符（返回体自相矛盾）"
            % (n, r["ticks"], wl.tick - before))
        assert r["tick"] == wl.tick, (n, r, wl.tick)


def test_728_zero_n_does_not_silently_upgrade_to_one_step():
    """G5 范围控制（防过修）：`run(0)` 不得被静默升成 1 步。"""
    wl = _fresh_learner()
    r = wl.run(0)
    assert wl.world.tick_count == 0 and wl.tick == 0, (
        "#728 run(0) 静默升级成一步（世界 %d / tick %d）——非法输入只许显式降级"
        "为零步并如实报 0，不许改成\"看起来合理\"的 1：%r"
        % (wl.world.tick_count, wl.tick, r))
    assert r["ticks"] == 0, r
    # 连续调用：第二次 run(2) 仍报增量 2（不是累计）
    wl.run(2)
    r2 = wl.run(2)
    assert r2["ticks"] == 2 and r2["tick"] == 4, r2
