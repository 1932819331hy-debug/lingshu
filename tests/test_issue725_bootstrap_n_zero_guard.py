# -*- coding: utf-8 -*-
"""test_issue725_bootstrap_n_zero_guard —— `SevenLayerLoop.run` 自举段的边界守卫。

缺陷（lingshu#725 · P2）
--------------------------------------------------------------------------
`run(n)` 的**自举分支**（`if not self.explorer.history:` 全带宽感知 + 跑
`explorer.window` tick）不判 `n`，故 `run(0)` / `run(-5)` 仍偷跑世界
`window` tick + `window+1` 帧观测，而同一个返回 dict 报 `ticks=0` /
`loop_tick=0`（副作用与返回值自相矛盾）。#233(A) 只修了主体循环
（`range(max(1,int(n))) → range(max(0,int(n)))`）与 `ticks` 字段自洽，未动自举段。

实现侧口径（本守卫的判据）
--------------------------------------------------------------------------
自举的**设计意图**＝给「本轮要跑的闭环」备认知种子（`step()` 的 L7 决策与 L4
预测都要 `explorer` 已有观测/模型），docstring 原话「若尚无观测，先做一轮初始
全带宽感知（闭环启动需要认知种子）」；而 `ticks` 字段的语义（#233(A) 钉住）
＝**本轮真实推进的闭环 tick 数**（`self.tick` 增量，自举段不推进 `self.tick`）。
两侧合起来只有一个自洽解：**`n <= 0` 时闭环一次都不跑 ⇒ 不需要种子 ⇒ 自举也
不该跑**（否则就改世界状态/观测史却报"没跑"）。故把自举收进 `n0 > 0`——
不是改 `ticks` 去"如实反映偷跑"，因为自举段本就不是闭环 tick，报进 `ticks`
会与 #233(A) 的字段语义冲突。
依据：issue #725「修复建议：把 bootstrap 收进 `if n>0` 分支」＋
《PR 审核细则 v0.1》§三.3 边界诚实（不静默改外部状态）／§三.4 判据外置
（字段语义由断言钉住）。

守卫用例
--------------------------------------------------------------------------
G1 正对照（量具能测到已知正确形态）：全新实例 `run(3)` 必须**真的**跑自举
   （世界 Δ = window + 3、观测史 = window + 1 + 3）——证明探针测得到自举，
   而不是"恒 0 假绿"。
G2 正对照（自举只发生一次）：预热实例 `run(3)` 的世界 Δ 恰为 3。
G3 缺陷态：全新实例 `run(0)` ⇒ 世界 Δ=0、观测史 0 帧、`explorer.tick=0`，
   且返回 `ticks=0` / `loop_tick=0`（改前：Δ=window、7 帧、tick=window+1）。
G4 缺陷态：全新实例 `run(-5)` 同上（改前与 `run(0)` 逐位相同地偷跑）。
G5 自洽性：`ticks == loop_tick 增量 == 0`，且 `audit` 为空（零步闭环）。
G6 范围控制：`n=1` 时自举仍在（本修只挡 n<=0，不误伤正常路径）。

运行（lingshu 仓根）：
    python -X utf8 -m pytest tests/test_issue725_bootstrap_n_zero_guard.py -q --no-header
退出码：0 = 全过；非 0 = 有断言失败。
"""
from __future__ import annotations

from lingshu.world.seven_layer_loop import SevenLayerLoop

WINDOW = 6


def _fresh_loop():
    """全新实例（尚无任何观测 ⇒ 自举分支可达）：2 个 wander 实体。"""
    lp = SevenLayerLoop(size=24, seed=42, window=WINDOW)
    lp.create_scene(trees=0, water=False)
    lp.add_entity("player", behavior="wander", pos=(2, 1.5, 2), speed=0.5)
    lp.add_entity("wolf", behavior="wander", pos=(15, 1.5, 15), speed=0.6)
    assert lp.world.tick_count == 0, lp.world.tick_count
    assert len(lp.explorer.history) == 0, len(lp.explorer.history)
    return lp


def _assert_loop_usable(lp):
    """硬性前置：世界与实体都在场（防"空世界 ⇒ Δ 恒 0"的假绿）。"""
    assert lp.world.entities, "守卫前置失败：世界必须非空"
    assert len(lp.world.entities) >= 2, list(lp.world.entities)


def test_725_positive_control_bootstrap_really_runs_on_positive_n():
    """G1 正对照：`run(3)` 确实跑自举（世界 Δ = window+3、观测史 window+1+3）。"""
    lp = _fresh_loop()
    _assert_loop_usable(lp)
    r = lp.run(3)
    assert r["ticks"] == 3 and r["loop_tick"] == 3, r
    assert lp.world.tick_count == WINDOW + 3, (
        "#725 正对照失败：`run(3)` 的世界推进 = %d，应 %d（自举 window tick + 3 步）"
        "——探针测不到自举就不该用来判 n<=0：%r"
        % (lp.world.tick_count, WINDOW + 3, r))
    assert len(lp.explorer.history) == WINDOW + 1 + 3, (
        "#725 正对照失败：观测史 = %d，应 %d（1 次全带宽 + window + 3 步）"
        % (len(lp.explorer.history), WINDOW + 1 + 3))
    assert lp.explorer.tick == WINDOW + 1 + 3, lp.explorer.tick


def test_725_positive_control_bootstrap_happens_only_once():
    """G2 正对照：自举只发生一次——预热实例再 `run(3)` 世界恰前进 3。"""
    lp = _fresh_loop()
    _assert_loop_usable(lp)
    lp.run(2)
    world_before = lp.world.tick_count
    r = lp.run(3)
    assert r["ticks"] == 3, r
    assert lp.world.tick_count - world_before == 3, (
        "#725 正对照失败：预热后 `run(3)` 世界前进 %d，应恰为 3"
        "（自举只发生一次）" % (lp.world.tick_count - world_before,))


def test_725_run_zero_has_no_side_effects():
    """G3 缺陷态：`run(0)` 世界/观测史/explorer.tick 一律不动（改前偷跑 window）。"""
    lp = _fresh_loop()
    _assert_loop_usable(lp)
    r = lp.run(0)
    assert lp.world.tick_count == 0, (
        "#725 run(0) 偷跑世界 %d tick（改前 = window = %d）——副作用与返回值矛盾"
        % (lp.world.tick_count, WINDOW))
    assert len(lp.explorer.history) == 0, (
        "#725 run(0) 偷跑 %d 帧观测（改前 = window+1 = %d）"
        % (len(lp.explorer.history), WINDOW + 1))
    assert lp.explorer.tick == 0, lp.explorer.tick
    assert lp.explorer.nodes == {}, list(lp.explorer.nodes)


def test_725_run_negative_has_no_side_effects():
    """G4 缺陷态：`run(-5)` 与 `run(0)` 逐位一致（都零副作用）。"""
    lp = _fresh_loop()
    _assert_loop_usable(lp)
    r = lp.run(-5)
    assert lp.world.tick_count == 0, (
        "#725 run(-5) 偷跑世界 %d tick（改前 = window = %d）"
        % (lp.world.tick_count, WINDOW))
    assert len(lp.explorer.history) == 0, len(lp.explorer.history)
    assert lp.explorer.tick == 0, lp.explorer.tick
    assert r["ticks"] == 0, r


def test_725_return_fields_are_self_consistent_for_n_le_zero():
    """G5 自洽性：n<=0 ⇒ `ticks == loop_tick 增量 == 0` 且 `audit` 空。"""
    for n in (0, -5, -100):
        lp = _fresh_loop()
        _assert_loop_usable(lp)
        before = lp.tick
        r = lp.run(n)
        assert r["ticks"] == 0, ("#725 run(%d) 返回 ticks=%r，应为 0"
                                 % (n, r["ticks"]))
        assert r["loop_tick"] == before == 0, (n, r, before)
        assert r["ticks"] == r["loop_tick"] - before, (n, r)
        assert lp.audit == [], ("#725 run(%d) 零步闭环不得留下审计留痕"
                                % (n, len(lp.audit)))


def test_725_range_control_n_one_still_bootstraps():
    """G6 范围控制：`n=1` 自举仍在（只挡 n<=0，不误伤正常路径）。"""
    lp = _fresh_loop()
    _assert_loop_usable(lp)
    r = lp.run(1)
    assert r["ticks"] == 1, r
    assert lp.world.tick_count == WINDOW + 1, (
        "#725 范围失控：run(1) 的世界推进 = %d，应 %d（自举不许被一起挡掉）"
        % (lp.world.tick_count, WINDOW + 1))
    assert len(lp.audit) == 1, len(lp.audit)
