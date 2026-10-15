# -*- coding: utf-8 -*-
"""#517 守卫：delete_node 的「不可遗忘」判定必须在 BEGIN IMMEDIATE 写事务之内。

缺陷态（修复前 main，值守复核 6187749146 两进程压测复现）：
  · 判定读（get_node/_is_forget_protected）发生在任何 DML 之前＝autocommit 快照；
    python sqlite3 的首条 DML 才隐式 BEGIN ⇒ 「判定→BEGIN」窗口里，另一进程
    `protect_node`（BEGIN IMMEDIATE）可提交插队 ⇒ protect 成功返回、节点仍被删、
    protections 留孤儿行（#36 的并发绕过，实测 2/100 命中）。

修法：显式 `BEGIN IMMEDIATE` 先于判定、判定后重读行——写事务起点与 protect 互斥排队。

守卫（**全部无时序依赖**，不引入 flaky）：
  G1 判定在写事务内：monkeypatch 记录 `_is_forget_protected` 被调用瞬间的
     `conn.in_transaction`——修复后必须 True（修复前 False＝判定在事务外，本件判别力）；
  G2 BEGIN 早于判定的 AST 静态序（机械核验，仿 test_core_thread_safety 的 AST 用例形态）；
  G3 行为零回归：未保护可删 True；protect 后删 False 且节点健在；不可变层拒删；不存在 False。

运行：python -X utf8 -m pytest tests/test_issue517_delete_protect_atomic_guard.py -q
"""
from __future__ import annotations

import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(REPO, "lingshu", "core", "core.py")


def _engine(tmp):
    from lingshu.core.core import SpacetimeMemoryEngine
    return SpacetimeMemoryEngine(os.path.join(tmp, "517.db"))


def test_g1_protection_check_runs_inside_write_transaction(tmp_path):
    e = _engine(str(tmp_path))
    n = e.add_context("可删节点")
    store = e.store
    seen = {}
    orig = store._is_forget_protected

    def spy(node):
        seen["in_tx"] = store.conn.in_transaction
        seen["layer"] = node.layer
        return orig(node)

    store._is_forget_protected = spy
    assert store.delete_node(n.id) is True
    assert seen.get("in_tx") is True, (
        "#517：判定发生在写事务之外（autocommit 快照）——"
        "与并发 protect_node 之间存在插队窗口")


def test_g2_begin_immediate_precedes_check_in_method_ast():
    tree = ast.parse(open(CORE, encoding="utf-8").read())
    fn = None
    for cls in [nd for nd in tree.body if isinstance(nd, ast.ClassDef)]:
        for m in cls.body:
            if isinstance(m, ast.FunctionDef) and m.name == "delete_node":
                fn = m
                break
        if fn:
            break
    assert fn is not None, "找不到 LayeredStore.delete_node"
    order = []  # (kind, lineno)

    class V(ast.NodeVisitor):
        def visit_Call(self, node):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name == "execute" and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str) and "BEGIN IMMEDIATE" in node.args[0].value:
                order.append(("begin", node.lineno))
            if name == "_is_forget_protected":
                order.append(("check", node.lineno))
            if name == "delete_node":
                return  # 不进嵌套
            self.generic_visit(node)

    V().visit(fn)
    kinds = [k for k, _ in order]
    assert "begin" in kinds and "check" in kinds, "BEGIN IMMEDIATE 或判定调用缺席"
    assert kinds.index("begin") < kinds.index("check"), (
        "#517：BEGIN IMMEDIATE 必须**先于**保护判定（当前序=%r）——否则判定仍在无事务快照上" % order)


def test_g3_behavior_zero_regression(tmp_path):
    e = _engine(str(tmp_path))
    # 未保护：删成功
    n1 = e.add_context("可删")
    assert e.store.delete_node(n1.id) is True and e.store.get_node(n1.id) is None
    # 受保护：拒删且节点健在
    n2 = e.add_context("受保护")
    e.store.protect_node(n2.id, "517 守卫：不可遗忘")
    assert e.store.delete_node(n2.id) is False
    assert e.store.get_node(n2.id) is not None
    # 不可变层拒删；不存在 False
    a = e.set_anchor("锚不可删")
    assert e.store.delete_node(a.id) is False
    assert e.store.delete_node("no-such") is False
