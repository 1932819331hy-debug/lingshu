# -*- coding: utf-8 -*-
"""#176 追评守卫：时钟回拨不得把衰减游标写回更小的 `now`（恢复后不重复计时）。

缺陷（issue #176 追评，报告人最小复现）：
  `_decay_dt` 在 `now` 给出时无条件 `set_meta("_last_decay_at", str(now))`。
  时钟回拨那一轮 `dt` 被钳成 0（不反向增信），**游标却倒退了**；时钟恢复后
  下一轮 `now - last` 把回拨区间**再算一遍** ⇒ 一次 1 小时回拨让恢复后多遗忘
  约 1 小时，情境层记忆被提前删除（修复前本机实测：对照 importance 0.4706 /
  回拨路径 0.1429；初始 importance=0.2 时回拨路径节点被整条删除，对照仍在）。

修复后语义：游标**单调不降**（写回 `max(last_f, now)`，无游标时仍写 `now`）。
回拨轮 `dt` 仍钳 0（回拨区间不计入衰减，也不反向增信），恢复轮只计真实流逝。

断言组（把 `_decay_dt` 的写回改回无条件 `str(now)` ⇒ A/B/C 变红）：
  A 相同真实流逝下，回拨路径与对照路径的 importance 相等（issue 判据逐字）。
  B 回拨轮的游标不得小于回拨前的游标（单调不降）。
  C 初始 importance 较低时，回拨路径节点不得被提前删除（对照仍在 ⇒ 回拨也在）。
  D 连续多次回拨后恢复，仍与对照相等（游标不被逐次拉低）。
  E 正对照：无回拨的普通推进照旧按真实流逝衰减（`now=None` 旧步进语义不变）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue176_clock_rollback_decay_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

T0 = 1_000_000.0
_CURSOR_KEY = "_last_decay_at"


def _engine_with_context(importance=0.5):
    eng = SpacetimeMemoryEngine(":memory:")
    node = eng.add_context(content="情境记忆-守卫", importance=importance)
    return eng, node.id


def _importance(eng, node_id):
    node = eng.store.get_node(node_id)
    return None if node is None else node.importance


def _cursor(eng):
    raw = eng.store.get_meta(_CURSOR_KEY).get(_CURSOR_KEY)
    return None if raw is None else float(raw)


def _control(importance=0.5):
    """对照：无回拨，真实流逝 120s（T0 → T0+60 → T0+120）。"""
    eng, nid = _engine_with_context(importance)
    for t in (T0, T0 + 60, T0 + 120):
        eng.store.decay_cycle(now=t)
    return eng, nid


def _rollback(importance=0.5, rollbacks=(-3600 + 60,)):
    """回拨路径：T0 起跑，按 `rollbacks` 逐个回拨，最后回到 T0+120（真实流逝 120s）。"""
    eng, nid = _engine_with_context(importance)
    eng.store.decay_cycle(now=T0)
    for off in rollbacks:
        eng.store.decay_cycle(now=T0 + off)
    eng.store.decay_cycle(now=T0 + 120)
    return eng, nid


def test_a_rollback_matches_control_for_same_elapsed():
    """A：同真实流逝（120s）下，回拨路径与对照路径 importance 相等。"""
    _ca, an = _control()
    _cb, bn = _rollback()
    imp_c, imp_b = _importance(_ca, an), _importance(_cb, bn)
    assert imp_c is not None and imp_b is not None
    assert imp_b == pytest.approx(imp_c, rel=0, abs=1e-12), (
        "回拨后重复计时：对照 %r vs 回拨 %r" % (imp_c, imp_b))


def test_b_cursor_never_regresses_on_rollback():
    """B：回拨轮的游标不得小于回拨前的游标（单调不降）。"""
    eng, _nid = _engine_with_context()
    eng.store.decay_cycle(now=T0)
    c1 = _cursor(eng)
    eng.store.decay_cycle(now=T0 - 3600 + 60)      # 回拨 1 小时
    c2 = _cursor(eng)
    assert c1 is not None and c2 is not None
    assert c2 >= c1, "回拨把游标写小了：%r -> %r（恢复后会重复计时）" % (c1, c2)
    assert c2 == pytest.approx(T0), "回拨轮不应把游标推进到回拨前的值之外"


def test_c_low_importance_node_not_prematurely_deleted():
    """C：初始 importance 较低时，回拨路径节点不得被提前删除。"""
    _ca, an = _control(importance=0.2)
    _cb, bn = _rollback(importance=0.2)
    assert _importance(_ca, an) is not None, "对照路径节点不应被删除（量具自检）"
    assert _importance(_cb, bn) is not None, "回拨路径节点被提前删除（额外遗忘）"


def test_d_multiple_rollbacks_then_recovery():
    """D：连续多次回拨后恢复，仍与对照相等（游标不被逐次拉低）。"""
    _ca, an = _control(importance=0.2)
    _cb, bn = _rollback(importance=0.2,
                        rollbacks=(-3600 + 60, -7200 + 120, -10800 + 180))
    imp_c, imp_b = _importance(_ca, an), _importance(_cb, bn)
    assert imp_c is not None and imp_b is not None
    assert imp_b == pytest.approx(imp_c, rel=0, abs=1e-12)


def test_e_forward_progress_and_none_semantics_unchanged():
    """E 正对照：普通推进按真实流逝；`now=None` 仍是「每次 = 一个周期」旧步进。"""
    eng, nid = _engine_with_context(importance=0.5)
    eng.store.decay_cycle(now=T0)                  # 首轮（无游标）：一个周期
    assert _importance(eng, nid) == pytest.approx(0.5 * 0.98, rel=0, abs=1e-12)
    eng.store.decay_cycle(now=T0 + 60)             # 恰好一个周期 ⇒ 再一个周期的量
    assert _importance(eng, nid) == pytest.approx(0.5 * 0.98 ** 2, rel=0, abs=1e-12)
    eng.store.decay_cycle(now=T0 + 180)            # 两个周期 ⇒ 一步等价于两步
    assert _importance(eng, nid) == pytest.approx(0.5 * 0.98 ** 4, rel=0, abs=1e-12)

    eng2, nid2 = _engine_with_context(importance=0.5)
    eng2.store.decay_cycle()                       # now=None ⇒ 一个周期
    assert _importance(eng2, nid2) == pytest.approx(0.5 * 0.98, rel=0, abs=1e-12)
    assert _cursor(eng2) is None, "now=None 不应写时间游标（既有语义）"
