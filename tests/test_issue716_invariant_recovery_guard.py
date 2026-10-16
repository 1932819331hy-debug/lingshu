# -*- coding: utf-8 -*-
"""test_issue716_invariant_recovery_guard · 不变量违例判据「可回落」守卫（#716）

缺陷（P1 · `lingshu/world/spacetime_consistency.py`）
-------------------------------------------------------------------------------
`step_verified()` 只有 `self._invariant_violations += 1`（终身累计、**只增不减**），
而 `consistency_report()` 的判决支读的正是这个累计值：

    elif self._invariant_violations > 0:
        verdict = "invariant_violated"

⇒ 任何一次**瞬时**不变量违规（`teleport` 出界/入地、瞬时抖动、外部写入）之后，
世界即便在后续 tick 里完全自愈（`_check_invariants()` 每 tick 都判 True），
`verdict` 也**永久**停在 `invariant_violated`、`self_consistent()` 永久 False
（`core.py` 门面 `spacetime_consistency(action="self_consistent")` 直接对外返回它）
—— 「持续运行」框架下最严重的语义倒退。

判据来源（为什么判「当前状态」而非「累计计数器」）
-------------------------------------------------------------------------------
1. `_check_invariants()` 是**每 tick 的当前态谓词**（实体此刻在界内/不穿地/坐标有限），
   不变量是**状态**而不是**事件**；
2. 字段名 `_invariant_violations` 与 `consistency_report` docstring 的
   `invariant_violated：世界状态不变量被破坏` 都在描述「世界**被破坏**」这一状态；
3. `#303`（本支的引入者）在其修复建议里明确留了这条取舍为**待决**：
   「如果 owner 倾向『一次违规永久不自洽』，只需把窗口改成全程」——
   代码落到了它标注为待决的那一侧，且 #303 未报告该后果。
4. 消费者读数（`git grep`）：`_invariant_violations` 只在**本文件**被写；读点只有
   ①本文件的判决支 ②`report["invariant_violations"]` 输出 ③两件既有守卫断言
   `>= 1`。**无任何消费方**依赖「终身累计」参与判决。

修法（最小 · 按 issue 修复建议）
-------------------------------------------------------------------------------
新增 `self._inv_window = deque(maxlen=self.window)`，每 tick 追加 0/1；
判决改读 `any(self._inv_window)`；累计值 `_invariant_violations` **保留**
（审计口径不变——`report["invariant_violations"]` 仍是终身计数）。

告警行为变化（如实声明）
-------------------------------------------------------------------------------
世界在窗口（`self.window` 个 tick，缺省 20）内自愈后，`verdict` 由
`invariant_violated` 回落为按命中率判定；窗口内仍违规则照旧判违规
（不瞬间清除——见 A2/B2）。累计值不变 ⇒ 「曾经违规」仍可审计。

断言组（抽掉修复 ⇒ 按标注转红）
-------------------------------------------------------------------------------
  A **自愈后回落**：注入 1 tick 违规 → 回原位 → 跑满窗口 ⇒ `verdict` 不再是
    `invariant_violated` 且 `self_consistent()` 为 True（**旧码在此恒红**）
  B **不瞬间清除**（过修护栏）：注入后**窗口未满**时仍必须判 `invariant_violated`
    —— 把修法做成「立刻置 0」也会让本组红
  C **审计口径不变**：`report["invariant_violations"]` 仍是终身累计（注入 2 次 ⇒ ≥2）
  D **正对照**：不注入的同构世界 ⇒ 计数 0、verdict 非 `invariant_violated`、自洽为真

运行（仓根）：python -X utf8 -m pytest tests/test_issue716_invariant_recovery_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world.spacetime_consistency import SpacetimeConsistency  # noqa: E402

#: 注入载体：**界内** (x, z)、**入地** y（`_check_invariants` 的 `y < gnd + 0.4`）。
#: 用 `speed=0.0` 的 wander ⇒ 世界侧本 tick 位移为 0、y 从不被 step 改写
#: ⇒ 注入在 step 之后仍成立（出界形态会被 `clamp_xz` 立刻钳回，注入不成立）。
BELOW_GROUND = (12.0, 0.0, 12.0)
HOME = (12.0, 1.5, 12.0)


def _world(window: int = 20, n_entities: int = 2):
    """构造同构世界：`n_entities` 个 speed=0 的 wander 实体（界内）。"""
    sp = SpacetimeConsistency(size=24, window=window, min_consistent_ticks=1)
    sp.scene.create_scene(trees=0, water=False)
    ids = [sp.add_entity("actor", behavior="wander", pos=HOME, speed=0.0)
           for _ in range(n_entities)]
    assert len(ids) == n_entities and len(sp.scene.entities) == n_entities, \
        "前置失败：世界未建出实体（防 for 循环空转成假绿）"
    return sp, ids[0]


def test_a_self_heal_clears_invariant_verdict():
    """A：一次瞬时违规后世界自愈 ⇒ verdict 必须回落（旧码恒判 invariant_violated）。"""
    window = 20
    sp, eid = _world(window=window)
    assert len(sp._inv_window) == 0, "前置：新世界的违例窗口应为空"
    for _ in range(5):
        sp.step_verified()
    # ---- 注入 1 tick：入地（世界模型无法解释的外部变更）----
    assert sp.teleport(eid, BELOW_GROUND) is True, "前置：teleport 未排队（实体不存在）"
    rec = sp.step_verified()
    assert rec["invariants_ok"] is False, \
        "前置失败：注入未生效（本 tick 应判不变量被破坏）: %r" % (rec["invariant_issues"],)
    rep_bad = sp.consistency_report()
    assert rep_bad["verdict"] == "invariant_violated", \
        "量具有效性：违规 tick 后必须判 invariant_violated（否则本组恒绿）: %r" % (rep_bad["verdict"],)
    # ---- 自愈：回原位，跑满窗口 ----
    assert sp.teleport(eid, HOME) is True
    sp.step_verified()
    sp.run(window + 2)
    rec_tail = sp.prediction_history(1)[0]
    assert rec_tail["invariants_ok"] is True, \
        "前置失败：世界未自愈（最近 tick 仍判违规）: %r" % (rec_tail["invariant_issues"],)
    rep = sp.consistency_report()
    assert rep["verdict"] != "invariant_violated", (
        "【#716】世界已自愈（最近 %d tick 不变量全 OK）却仍判 invariant_violated —— "
        "判据读的是终身累计而非当前状态" % window)
    assert rep["self_consistent"] is True, (
        "【#716】世界已自愈却仍 self_consistent=False（core.py 门面直接对外返回它）")


def test_b_violation_not_cleared_instantly():
    """B（过修护栏）：注入后**窗口未满**时必须仍判违规——「立刻置 0」也会红。"""
    window = 20
    sp, eid = _world(window=window)
    sp.teleport(eid, BELOW_GROUND)
    sp.step_verified()
    assert sp.teleport(eid, HOME) is True
    sp.step_verified()                      # 世界已自愈 1 tick，但窗口未满
    rep = sp.consistency_report()
    assert rep["verdict"] == "invariant_violated", (
        "【#716】违规仍在窗口内（刚过 1 tick / 窗口 %d）却已判非违规 —— "
        "修法做成了「立刻置 0」，丢掉了窗口语义" % window)
    assert rep["self_consistent"] is False, \
        "【#716】违规仍在窗口内却已自报自洽"
    # 再跑满窗口 ⇒ 必须回落（同 A 的另一侧）
    sp.run(window)
    assert sp.consistency_report()["verdict"] != "invariant_violated", \
        "【#716】窗口已滚过违规 tick 却仍判违规"


def test_c_cumulative_counter_kept_for_audit():
    """C：累计计数保留为审计口径（修法**不**得抹掉「曾经违规」的痕迹）。"""
    sp, eid = _world(window=4)
    assert sp.consistency_report()["invariant_violations"] == 0, "前置：新世界计数应为 0"
    for _ in range(2):
        sp.teleport(eid, BELOW_GROUND)
        rec = sp.step_verified()
        assert rec["invariants_ok"] is False, "前置：本次注入未生效"
        sp.teleport(eid, HOME)
        sp.step_verified()
    sp.run(4)                    # 滚过两次违规 tick（窗口 maxlen=4）
    rep = sp.consistency_report()
    assert rep["invariant_violations"] >= 2, (
        "【#716】累计审计计数被抹掉（应保留终身累计）: %r" % (rep["invariant_violations"],))
    assert not any(sp._inv_window), \
        "前置：窗口已滚过违规 tick，应全为 0: %r" % (list(sp._inv_window),)
    # 两次注入各留 1 个未命中 tick ⇒ overall 0.5 < consistent_rate ⇒ 此处
    # 判决为 inconsistent（**非** invariant_violated）——本组只钉「不变量支不再
    # 永久压过其余支」，自洽为真的断言留在 A/D。
    assert rep["verdict"] != "invariant_violated", (
        "【#716】窗口已滚过违规 tick 却仍由不变量支判违规: %r" % (rep["verdict"],))


def test_d_positive_control_no_injection():
    """D（正对照）：不注入的同构世界 ⇒ 计数 0、verdict 非违规、自洽为真。"""
    window = 20
    sp, eid = _world(window=window)
    sp.run(40)
    rec = sp.prediction_history(1)[0]
    assert rec["invariants_ok"] is True, "前置：无注入世界不得判违规"
    assert all(r["invariants_ok"] for r in sp.prediction_history(40)), \
        "前置：无注入世界历史里不得出现违规 tick"
    rep = sp.consistency_report()
    assert rep["invariant_violations"] == 0, \
        "正对照：无注入 ⇒ 累计计数必须为 0: %r" % (rep["invariant_violations"],)
    assert rep["verdict"] != "invariant_violated", \
        "正对照：无注入 ⇒ verdict 不得为 invariant_violated"
    assert rep["self_consistent"] is True, "正对照：无注入且持续足够 ⇒ 应自洽"


def test_e_window_shares_class_window_semantics():
    """E：窗口长度取 `self.window`（与 `_rolling` / `_entity_hits` 同口径）。"""
    sp, eid = _world(window=3)
    assert sp._inv_window.maxlen == sp.window == 3, (
        "违例窗口的 maxlen 应等于 `self.window`（缺省 20）: %r vs %r"
        % (sp._inv_window.maxlen, sp.window))
    sp.teleport(eid, BELOW_GROUND)
    sp.step_verified()
    sp.teleport(eid, HOME)
    sp.run(3)                                # 恰好滚过违规 tick
    assert not any(sp._inv_window), \
        "窗口 %d 已滚过违规 tick，窗口内应全为 0: %r" % (3, list(sp._inv_window))
    assert sp.consistency_report()["verdict"] != "invariant_violated"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header"]))
