# -*- coding: utf-8 -*-
"""#210 `forget_advisor` 三处错误 —— 守卫（lingshu/core/core.py）

缺陷（分诊表 `triage_lingshu_ALL.md` 行 210 判定「成立」；issue 原文 `.tmp/issues_p3.json` #210）：
  ① `if (imp or 0.5) < low_value`：importance=0.0 是假值，被 `or` 换成 0.5
     ⇒ **价值最低的情境记忆反而不触发低价值信号**（不归档）。
  ② 归档时无条件 `new_imp = archived_imp`（默认 0.1）：docstring 写「importance
     降至 archived_imp」，但原本低于它的节点（如 0.05）归档后反而**升到** 0.1。
  ③ `access_stale = (...) if False else None`：死代码（真值恒 None，随后只查
     `SELECT access_count`）。

修法（最小 · 逐条对应）：
  ① `imp_v = 0.5 if imp is None else imp`（只有真 None 才回默认）再比较；
  ② `new_imp = min(imp_v, archived_imp)`（归档只降不升）；
  ③ 删除该死代码行。

判据来源：issue #210「期望行为：importance=0.0 判为低价值；归档只降不升」＋
分诊表行 210（经验标定；理论章节 `docs/theory/世界模型与语义时空图_完整理论整理与
实现路线.md` 未规定主动遗忘取值口径，追不到更早出处）。
边界（如实标注）：issue #210 第 3 点「archived_imp 恰等于 decay 的 min_confidence ⇒
归档节点永久驻留」**不由本函数承担**——该死区已由 #207 在 `decay_cycle` 消解；
Q4 只钉「归档节点不再永生」这一端到端结果，不重复 #207 的判据。

断言组（把修复改回缺陷形态 ⇒ 对应项红）：
  Q1 零重要度被归档：importance=0.0 的情境节点 `forget_advisor` 后 archived 且带
     `archived` 标签（旧：`(0.0 or 0.5)=0.5` 不触发低价值信号 ⇒ 不归档）。
  Q2 归档只降不升：importance=0.05（< archived_imp=0.1）归档后 importance 仍 ≤ 0.05
     （旧：被提到 0.1）。
  Q3 死代码已除：`forget_advisor` 函数体内不得再出现 `if False` 形态的死代码。
  Q4 端到端：归档节点在 1000 轮 `decay_cycle` 后不再永生（与 #207 联动的结果面）。
  Q5 防误杀：no_forget 标签的节点不被归档（原判据未被削弱）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue210_forget_advisor_guard.py -q --no-header
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _node(e, nid):
    return e.store.get_node(nid)


def test_q1_zero_importance_is_archived():
    """Q1：importance=0.0 必须触发低价值信号 → 归档（旧：`or 0.5` 吞掉 0.0）。"""
    e = SpacetimeMemoryEngine(":memory:")
    zero = e.add_context("零重要度情境 0.0", importance=0.0)
    res = e.forget_advisor(stale_days=30, low_value=0.2)
    assert res["archived"] == 1, (
        "#210①：importance=0.0 未被归档——res=%r（旧实现 `(0.0 or 0.5)=0.5` 不小于"
        " low_value=0.2，零重要度被当成 0.5）" % res)
    n = _node(e, zero.id)
    assert n is not None and "archived" in n.tags, (
        "#210①：归档未留痕——tags=%r" % (None if n is None else n.tags))


def test_q2_archiving_never_raises_importance():
    """Q2：归档只降不升——0.05 归档后不得被提到 archived_imp=0.1。"""
    e = SpacetimeMemoryEngine(":memory:")
    lo = e.add_context("低于归档值的情境 0.05", importance=0.05)
    res = e.forget_advisor(stale_days=30, low_value=0.2, archived_imp=0.1)
    assert res["archived"] == 1, "前置：0.05 应被归档——res=%r" % res
    n = _node(e, lo.id)
    assert n is not None, "前置：节点应仍在（归档是可逆降权，不是删除）"
    assert n.importance <= 0.05 + 1e-9, (
        "#210②：归档把 importance 从 0.05 提到 %.3f（旧实现无条件赋 archived_imp）"
        % n.importance)


def test_q3_dead_code_removed():
    """Q3：`forget_advisor` 体内不得再有 `if False` 死代码（旧：access_stale 行）。

    以 AST 判（只看代码、不看 docstring/注释里的字面量），钉死「死分支」这一形态。
    """
    e = SpacetimeMemoryEngine(":memory:")
    src = inspect.getsource(type(e).forget_advisor)
    tree = ast.parse(textwrap.dedent(src))
    dead = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.IfExp, ast.If)) \
                and isinstance(node.test, ast.Constant) and node.test.value is False:
            dead.append(node.lineno)
    assert not dead, (
        "#210③：forget_advisor 体内仍有 `... if False else ...` 死分支（AST 行号 %r）"
        % dead)


def test_q4_archived_node_not_immortal():
    """Q4：归档出的节点不再永生（与 #207 联动的结果面；旧：#210 报告实测永生）。"""
    e = SpacetimeMemoryEngine(":memory:")
    n = e.add_context("待归档情境", importance=0.05)
    assert e.forget_advisor(stale_days=30, low_value=0.2, archived_imp=0.1)["archived"] == 1
    for _ in range(1000):
        e.decay_cycle()
    assert _node(e, n.id) is None, (
        "#210：归档节点 1000 轮衰减后仍在（issue 报告实测：所有已归档节点 1000 轮后"
        "都还在）")


def test_q5_no_forget_nodes_are_not_archived():
    """Q5：防误杀——`no_forget` 标签的节点不被归档（原判据未被削弱）。"""
    e = SpacetimeMemoryEngine(":memory:")
    keep = e.add_context("不可遗忘情境", importance=0.0, tags=["no_forget"])
    e.forget_advisor(stale_days=30, low_value=0.2)
    n = _node(e, keep.id)
    assert n is not None and "archived" not in n.tags, (
        "#210：no_forget 节点被归档（原保护判据被削弱）——tags=%r"
        % (None if n is None else n.tags))
