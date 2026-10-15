# -*- coding: utf-8 -*-
"""test_issue656_escalation_null_guard · #656 升级点 NULL 兜底守卫
============================================================================
缺陷（本件守卫对象）：
    `list_escalation_points()` 原样回填 `trigger` / `condition` 两列 ⇒ 存量
    行该列为 `NULL` 时返回 `None`；`check_escalation()` 随后对其做 `in`
    （子串匹配）⇒ `TypeError: argument of type 'NoneType' is not iterable`。
    `add_escalation_point` 不校验入参，NULL 可达 ⇒ 一条毒行令**整个公开读面
    崩掉**。

修法（唯一读取点归一）：
    `list_escalation_points` 返回体 `"trigger": r[2] or ""`,
    `"condition": r[3] or ""`——与同行 `enabled: bool(r[6])` **同处、同手法**，
    缺口最小：只堵 NULL，不动任何既有匹配口径。

断言组（回退即红）：
    ① `condition=NULL` ⇒ `list_escalation_points()` 不抛，该键为 `""`；
    ② `trigger=NULL` ⇒ 同上（issue 自述只实测了 condition，trigger 是
       判据式顺序推出的第二条路径，**须独立覆盖**）；
    ③ `check_escalation(signal_type)` 对上述两行均**不抛**（修复前任一即崩）；
    ④ 正对照：合法值语义逐位不变（非空 trigger/condition 原样返回，
       `check_escalation` 仍按子串命中）；
    ⑤ **空侧不参与匹配**（`check_escalation` 判据守卫）：NULL 归一为 `""` 后，
       若不短路，`"" in signal` **恒真** ⇒ 该行对**任意信号**都命中（误报全部）。
       本件判据要求空侧（含 NULL 归一来的 `""`）**不参与匹配**（fail-closed，
       与函数开头 `if not signal: return []` 同口径）：直插
       `trigger=NULL, condition='signal_x'` 与 `trigger=NULL, condition=NULL`
       两行后，`check_escalation("完全不相干ABC")` **不含**这两行，
       而 `check_escalation("signal_x")` **含**有效侧（condition）那行。

变异自证（人工改码须触发）：
    · 把 `r[2] or ""` / `r[3] or ""` 改回 `r[2]` / `r[3]` ⇒ ①②③必红。
    · 把 `check_escalation` 判据改回原四段 `or` 式（去掉 `trigger and` /
      `condition and` 空侧短路）⇒ ⑤必红（`"" in signal` 恒真令无关信号命中
      空侧行）。

运行：python -X utf8 -m pytest tests/test_issue656_escalation_null_guard.py -q
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine(tmp_path):
    return SpacetimeMemoryEngine(os.path.join(str(tmp_path), "656.db"))


def _insert_raw(eng, code, trigger, condition, enabled=1):
    """直插一行（绕过 add_escalation_point 的写入面）——毒行复现手法。"""
    conn = eng.store.conn
    conn.execute(
        "INSERT INTO escalation_points (code, trigger, condition, action, "
        "severity, enabled, created_at) VALUES (?,?,?,?,?,?,?)",
        (code, trigger, condition, "noop", "medium", enabled, 0.0))
    conn.commit()


def test_null_condition_and_trigger_do_not_raise(tmp_path):
    """①②③：两条 NULL 路径都不抛，且归一为 ""。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "E-NULLCOND", "some_trigger", None)
    _insert_raw(eng, "E-NULLTRIG", None, "signal_x")

    # ①② 读面不抛 + 归一
    rows = {r["code"]: r for r in eng.list_escalation_points()}
    assert rows["E-NULLCOND"]["condition"] == "", rows["E-NULLCOND"]
    assert rows["E-NULLCOND"]["trigger"] == "some_trigger"
    assert rows["E-NULLTRIG"]["trigger"] == "", rows["E-NULLTRIG"]
    assert rows["E-NULLTRIG"]["condition"] == "signal_x"

    # ③ 消费面不抛（修复前任一即 TypeError）
    eng.check_escalation("some_trigger")
    eng.check_escalation("signal_x")
    eng.check_escalation("no_such_signal")


def test_valid_values_unchanged(tmp_path):
    """④ 正对照：合法值语义不变。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "E-OK", "ok_trigger", "ok_condition")
    rows = {r["code"]: r for r in eng.list_escalation_points()}
    assert rows["E-OK"]["trigger"] == "ok_trigger"
    assert rows["E-OK"]["condition"] == "ok_condition"
    # 子串匹配口径不动：仍能命中
    hits = eng.check_escalation("ok_trigger")
    assert any(h["code"] == "E-OK" for h in hits), hits


def test_null_side_does_not_match_any_signal(tmp_path):
    """⑤ 空侧不参与匹配：NULL 归一为 "" 后不得对任意信号误报。

    缺陷本体：`"" in signal` 恒真 ⇒ 空侧（含 NULL 归一来的 ""）若参与匹配，
    该升级点对**任意信号**都命中（误报全部）。本件判据要求空侧短路，
    fail-closed（与 `if not signal: return []` 同口径）。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "E-NULLCOND", "some_trigger", None)
    _insert_raw(eng, "E-NULLTRIG", None, "signal_x")
    _insert_raw(eng, "E-BOTHNULL", None, None)

    # 不相关信号：三行都不得命中（空侧不参与匹配）
    unrelated = {h["code"] for h in eng.check_escalation("完全不相干ABC")}
    assert "E-NULLCOND" not in unrelated, unrelated
    assert "E-NULLTRIG" not in unrelated, unrelated
    assert "E-BOTHNULL" not in unrelated, unrelated

    # 有效侧照旧命中：NULL 侧不参与，有值侧仍按子串命中
    hits_cond = {h["code"] for h in eng.check_escalation("signal_x")}
    assert "E-NULLTRIG" in hits_cond, hits_cond   # condition 侧有效
    hits_trig = {h["code"] for h in eng.check_escalation("some_trigger")}
    assert "E-NULLCOND" in hits_trig, hits_trig   # trigger 侧有效

