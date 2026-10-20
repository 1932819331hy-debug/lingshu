# -*- coding: utf-8 -*-
"""#212 record_value_change 会把整段变化无界追加 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #212；分诊表 `triage_lingshu_core_front.md` 行 212 判定成立）：
  `SelfModel.record_value_change` 旧实现每次调用为**每个** value 追加一条
  `superseded`、再追加一条新值 ⇒ ①每次调用都增长；②重复同一 value 也照增
  （语义等价的冗余条目）⇒ `value_evolution` **无上限、无去重**地增长。

判据来源（修法）：上界与钳制手法 —— 经验标定（#212 修复轮；**理论章节追不到**），
沿用同类既有口径 `SelfModel.TRUST_HISTORY_MAX` / `HISTORY_MAX`（`core.py` 的
append 后钳制）。功能口径（「2.1.2 价值观修正事件可追溯」）见 `core.py` 原 docstring。

断言组（抽掉任一修复即红）：
  I1 重复同一 value 调用 ⇒ `value_evolution` 长度不再增长（幂等去重）。
  I2 反复改值 ⇒ 长度被钳到 `VALUE_EVOLUTION_MAX`（有界，不无界增长）。
  I3 保留最近语义：钳制后最后一条仍是「最近一次变更」（tail 保留，不是 head）。
  I4 防误杀：正常改值仍写入 superseded（旧值）+ 新值两条，可追溯。

运行（仓根）：python -X utf8 -m pytest tests/test_issue212_value_evolution_bound_guard.py -q --no-header
"""
from __future__ import annotations

import inspect
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SelfModel  # noqa: E402


def test_i1_repeated_same_value_is_idempotent():
    """I1：重复同一 value ⇒ 记录不再增长（旧缺陷：每次 +2）。"""
    m = SelfModel()
    m.record_value_change("存在优先", "细化")
    n1 = len(m.value_evolution)
    for _ in range(50):
        m.record_value_change("存在优先", "重复")
    assert len(m.value_evolution) == n1, \
        "I1 重复调用仍增长：%r -> %r" % (n1, len(m.value_evolution))
    assert m.values == ["存在优先"], "I1 values 被扰动：%r" % (m.values,)


def test_i2_repeated_distinct_values_bounded():
    """I2：反复改值 ⇒ 长度被钳到 VALUE_EVOLUTION_MAX（有界）。"""
    m = SelfModel()
    for i in range(500):
        m.record_value_change("v%d" % i, "t%d" % i)
    assert len(m.value_evolution) <= m.VALUE_EVOLUTION_MAX, \
        "I2 无界增长：%r > %r" % (len(m.value_evolution), m.VALUE_EVOLUTION_MAX)
    assert m.VALUE_EVOLUTION_MAX > 0


def test_i3_tail_retained_after_clamp():
    """I3：钳制保留最近（tail），最后一条是最近一次变更。"""
    m = SelfModel()
    for i in range(500):
        m.record_value_change("v%d" % i, "trigger%d" % i)
    last = m.value_evolution[-1]
    assert last["value"] == "v499" and last["trigger"] == "trigger499", \
        "I3 未保留最近记录：%r" % (last,)


def test_i4_normal_change_still_traces_old_and_new():
    """I4：防误杀——正常改值仍写 superseded（旧值）+ 新值两条。"""
    m = SelfModel()          # 默认 values = ["存在优先", "信任深化", "结构完整"]
    before = len(m.value_evolution)
    m.record_value_change("新价值观", "修正")
    added = m.value_evolution[before:]
    assert len(added) == 4, "I4 记录条数不符：%r" % (added,)
    assert [e["value"] for e in added[:3]] == ["存在优先", "信任深化", "结构完整"], \
        "I4 旧值 superseded 未记全：%r" % (added,)
    assert added[-1]["value"] == "新价值观" and added[-1]["to"] is True, \
        "I4 新值未记：%r" % (added[-1],)


def test_q5_refine_value_keeps_other_values():
    """Q5：`refine_value` 只改指定条目，**其余条目逐条仍在**（#212 路 B）。

    判别力：若实现退回「全量替换」（`values = [new]`）⇒ 其余两条消失 ⇒ 本件红。
    """
    m = SelfModel()                       # 默认 values = ["存在优先", "信任深化", "结构完整"]
    others = [v for v in m.values if v != "信任深化"]
    changed = m.refine_value("信任深化", "信任深化（含纠错开放）", "设计者校准")
    assert changed is True, "Q5 未报告变更"
    assert "信任深化" not in m.values, "Q5 目标条目未改：%r" % (m.values,)
    assert "信任深化（含纠错开放）" in m.values, "Q5 新条目未落：%r" % (m.values,)
    for v in others:
        assert v in m.values, "Q5 其余条目被抹掉 %r：%r" % (v, m.values)
    assert len(m.values) == len(others) + 1, "Q5 列表长度变了：%r" % (m.values,)
    trg = m.value_evolution[-1]["trigger"]
    assert trg.startswith("refine:信任深化"), "Q5 演化 trigger 记法不符：%r" % (trg,)


def test_q6_add_value_appends_and_keeps_existing():
    """Q6：`add_value` 后 `len(values)` **+1**、既有条目逐条仍在（#212 路 B）。

    判别力：若实现退回「赋值」（`values = [value]`）⇒ 长度不增反减 ⇒ 本件红。
    """
    m = SelfModel()
    before = list(m.values)
    changed = m.add_value("诚实优先", "新增")
    assert changed is True, "Q6 未报告变更"
    assert len(m.values) == len(before) + 1, \
        "Q6 列表未变长：%r -> %r" % (before, m.values)
    for v in before:
        assert v in m.values, "Q6 既有条目被抹掉 %r：%r" % (v, m.values)
    assert m.values[-1] == "诚实优先", "Q6 新条目不在尾部：%r" % (m.values,)
    assert m.value_evolution[-1]["trigger"].startswith("add:诚实优先"), \
        "Q6 演化 trigger 记法不符：%r" % (m.value_evolution[-1],)


def test_q7_retire_value_removes_target_only():
    """Q7：`retire_value` 后目标条目**消失**、其余条目逐条仍在（#212 路 B）。"""
    m = SelfModel()
    others = [v for v in m.values if v != "结构完整"]
    changed = m.retire_value("结构完整", "退役")
    assert changed is True, "Q7 未报告变更"
    assert "结构完整" not in m.values, "Q7 目标条目未消失：%r" % (m.values,)
    assert m.values == others, "Q7 其余条目被扰动：%r != %r" % (m.values, others)
    assert m.value_evolution[-1]["trigger"].startswith("retire:结构完整"), \
        "Q7 演化 trigger 记法不符：%r" % (m.value_evolution[-1],)


def test_q8_legacy_two_positional_call_unchanged():
    """Q8（正对照 · 承重）：旧 `record_value_change(value, trigger)` 两位置参数调用
    行为**逐字不变**——防「修语义时把向后兼容改坏」（#212 裁定理由 1）。

    钉住三件：① 签名仍是 `(self, value, trigger)`、无新增必填参数；② 语义仍是
    **全量替换**（旧值逐条 superseded + `values = [value]`）；③ 仍幂等短路、仍钳制。
    """
    params = list(inspect.signature(SelfModel.record_value_change).parameters)
    assert params == ["self", "value", "trigger"], \
        "Q8 旧方法签名被改：%r" % (params,)
    assert inspect.signature(SelfModel.record_value_change).parameters["value"].default \
        is inspect.Parameter.empty, "Q8 value 被加了缺省值"
    assert inspect.signature(SelfModel.record_value_change).parameters["trigger"].default \
        is inspect.Parameter.empty, "Q8 trigger 被加了缺省值"

    m = SelfModel()                       # 默认三条
    before = len(m.value_evolution)
    m.record_value_change("唯一值", "修正")   # 两位置参数调用（仓外调用方形态）
    added = m.value_evolution[before:]
    assert m.values == ["唯一值"], "Q8 语义不再是全量替换：%r" % (m.values,)
    assert len(added) == 4, "Q8 记录条数不符：%r" % (added,)
    assert [e["trigger"] for e in added[:3]] == ["superseded"] * 3, \
        "Q8 旧值 trigger 记法被改：%r" % (added,)
    assert added[-1]["trigger"] == "修正" and added[-1]["to"] is True, \
        "Q8 新值 trigger 未逐字透传：%r" % (added[-1],)

    n1 = len(m.value_evolution)
    m.record_value_change("唯一值", "重复")   # 幂等短路仍生效
    assert len(m.value_evolution) == n1, "Q8 幂等短路被改坏：%r" % (len(m.value_evolution),)

    m2 = SelfModel()
    for i in range(500):
        m2.record_value_change("v%d" % i, "t%d" % i)
    assert len(m2.value_evolution) <= m2.VALUE_EVOLUTION_MAX, \
        "Q8 钳制被改坏：%r" % (len(m2.value_evolution),)
    assert m2.value_evolution[-1]["value"] == "v499", "Q8 尾保留语义被改：%r" % (m2.value_evolution[-1],)
