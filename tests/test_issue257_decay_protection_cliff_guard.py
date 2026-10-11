# -*- coding: utf-8 -*-
"""#257 decay_cycle 把保护名单展开成 2N 个 SQL 占位符 → 越限即整轮停摆 —— 守卫

缺陷（公开 issue #257 · [core/情境层·遗忘] · 判定「成立」）：
  `LayeredStore.decay_cycle` 每轮先取整张保护名单，再把它**两次**展开成 SQL
  占位符：
      protected = self.get_protected_nodes()
      ph = ",".join("?" for _ in protected)
      exclude = f" AND e.source_id NOT IN ({ph}) AND e.target_id NOT IN ({ph})"
      params = tuple(protected) * 2
  ⇒ 一轮衰减的绑定变量数 = **2 × 名单长度**。名单越过「变量上限/2」（SQLite 上游
  默认 32766 ⇒ 16383 条）后第一句 SELECT 即抛
  `sqlite3.OperationalError: too many SQL variables`，整个 `decay_cycle` 中止——
  边衰减、情境层衰减/删除、`run_maintenance_cycle` 第一步全部停摆，且
  `start_auto_decay` 线程首轮即退出（无自愈路径）。而保护名单在设计上只增不减。

修法（最小 · 按 issue 建议 1）：排除改 SQL **内联子查询**
  （`NOT IN (SELECT node_id FROM protections)`）⇒ 绑定变量数与名单长度无关。

判据来源：公开 issue #257「建议修复 1：不要把名单展开成占位符：改为 SQL 内联子查询」
  ＋ 其复现读数（保护 16384 条 ⇒ `too many SQL variables`）。仓内无规定该口径的
  理论章节，属经验标定（本件 #257）。
边界（削掉的判别力，如实标注）：本件**只**拆掉占位符悬崖——issue 建议 2/3/4
  （保护行随节点删除、`protect_node` 校验节点存在、孤儿行回收）属保护名单**回收**
  面，不在本件范围（名单仍可无限增长，但不再触发悬崖）。

断言组（抽掉修复＝把 exclude 改回 2N 占位符 ⇒ A/B 必红）：
  A 大名单 + 低变量上限（`SQLITE_LIMIT_VARIABLE_NUMBER=999`）下 `decay_cycle`
    不抛异常；受保护情境节点仍在、未受保护情境节点按预期衰减删除（遗忘面复活）
  B 边衰减的 SQL **形态**：边查询语句含内联子查询、且占位符数不随名单长度增长
  C 保护语义未削：受保护端点的边不衰减；未受保护的同类边照常衰减消失（防误杀）
  D 名单规模与单轮成本无关的**读数**：100 条与 4000 条保护名单下占位符计数相同

运行（仓根）：python -X utf8 -m pytest tests/test_issue257_decay_protection_cliff_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

#: 远低于上游默认 32766 的上限——旧实现下「名单长度 > 999/2」即触发悬崖。
_LIMIT = 999


def _engine(limit=_LIMIT):
    e = SpacetimeMemoryEngine(":memory:")
    e.store.conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)
    return e


def _protect_many(e, n, prefix="gone_"):
    for i in range(n):
        e.protect_node("%s%d" % (prefix, i), "LongTermMemoryGate:snapshot")


def _edge_decay_sql(e):
    """跑一轮 decay_cycle，抓出边衰减那条 SELECT 的原文。"""
    seen = []
    e.store.conn.set_trace_callback(lambda sql: seen.append(sql))
    try:
        e.decay_cycle()
    finally:
        e.store.conn.set_trace_callback(None)
    hits = [s for s in seen if "FROM edges e" in s]
    assert hits, "未抓到边衰减 SQL（trace 失效）：%r" % (seen[:5],)
    return hits[0]


def test_a_large_protection_list_no_longer_stalls_decay():
    """A：大名单 + 低变量上限 ⇒ decay_cycle 不抛；遗忘面复活。"""
    e = _engine()
    kept = e.add_context("情境：一次性验证码 482913", importance=0.4)
    e.protect_node(kept.id, "守卫：不可遗忘")
    doomed = e.add_context("情境：十分钟后出发去机场", importance=0.4)
    _protect_many(e, 4000)

    assert len(e.store.get_protected_nodes()) == 4001, "前置：保护名单规模不符"
    # 旧实现：第一句即 OperationalError too many SQL variables
    for _ in range(200):
        e.decay_cycle()

    assert e.store.get_node(kept.id) is not None, (
        "#257 受保护情境节点被衰减删除（保护语义被削）")
    assert e.store.get_node(doomed.id) is None, (
        "#257 未受保护情境节点未被遗忘（decay_cycle 停摆，遗忘面未复活）")


def test_b_edge_sql_uses_inline_subquery():
    """B：边衰减 SQL 用内联子查询；占位符数不随名单长度增长。"""
    e = _engine()
    _protect_many(e, 4000)
    sql = _edge_decay_sql(e)
    assert "SELECT node_id FROM protections" in sql, (
        "#257 边衰减 SQL 未改用内联子查询（仍是 2N 占位符形态）：%r" % (sql,))
    assert "?" * 40 not in sql, (
        "#257 边衰减 SQL 仍含大批占位符（名单被展开）：%r" % (sql[:400],))


def test_c_protection_semantics_preserved():
    """C：防误杀——受保护端点的边不衰减；未受保护同类边照常消失。"""
    e = _engine()
    a = e.add_perception("知识 A", skip_dedup=True)
    b = e.add_perception("知识 B", skip_dedup=True)
    c = e.add_perception("知识 C", skip_dedup=True)
    e.add_edge(a.id, b.id)   # a 受保护（引擎 add_edge 默认 verified=False）
    e.add_edge(b.id, c.id)   # 两端都不受保护
    e.protect_node(a.id, "守卫：不可遗忘")
    _protect_many(e, 4000)

    for _ in range(200):
        e.decay_cycle()

    left = {(r["source_id"], r["target_id"]) for r in
            e.store.conn.execute("SELECT source_id, target_id FROM edges").fetchall()}
    assert (a.id, b.id) in left, "#257 受保护端点的边被衰减删除（保护语义被削）"
    assert (b.id, c.id) not in left, "#257 未受保护边未衰减消失（防误杀对照失败）"


def test_d_placeholder_count_independent_of_list_size():
    """D：100 条与 4000 条名单下，边衰减 SQL 的占位符计数相同。"""
    counts = []
    for n in (100, 4000):
        e = _engine()
        _protect_many(e, n)
        sql = _edge_decay_sql(e)
        counts.append(sql.count("?"))
    assert counts[0] == counts[1], (
        "#257 占位符数随保护名单增长：100 条 -> %d，4000 条 -> %d" % tuple(counts))
