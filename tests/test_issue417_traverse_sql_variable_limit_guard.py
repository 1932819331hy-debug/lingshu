# -*- coding: utf-8 -*-
"""test_issue417_traverse_sql_variable_limit_guard · lingshu issue #417 守卫
============================================================================
缺陷（lingshu issue #417 · 基线 83cce599）：
  `LayeredStore.traverse` 的 `max_nodes` **只在 BFS 层开始时检查**，进入一层后仍
  把该层全部邻居加入结果；随后 importance 补全把所有候选 id **一次性**拼进
  `SELECT id, importance FROM nodes WHERE id IN (...)`。高扇出节点（一个 root 直接
  连 N 个叶子）使候选数越过 SQLite 变量上限（本机 3.49.1 实测
  `MAX_VARIABLE_NUMBER=32766`）：issue 实测 1200 叶子时 `ids_in_importance_query=1200`、
  32767 叶子时 `OperationalError: too many SQL variables`。返回条数有界，实际
  查询工作量无界。

修法（最小 · 按 issue 修复方向）：
  importance 补全改**分批查询**（`_SQL_IN_CHUNK = 900`，落在 SQLite 旧默认 999 与
  新默认 32766 之下）。**不**给单层扩展设上限——issue 明确要求「如果保证按
  importance 取前 N，需保留该语义，不能简单截取最先遇到的 N 个邻居」；本修法看全
  候选后仍按 importance 降序取前 `max_nodes`，语义逐字不变。

判据来源：公开 issue #417「修复方向」（「扩展阶段管理预算，并使用 JOIN 或分批查询
消除超长绑定列表」）＋ 其复现实读（1200 叶子 `returned_count=1` 但
`ids_in_importance_query=1200`；32767 叶子 `OperationalError: too many SQL
variables`）＋ 分诊表 `triage_lingshu_ALL.md` 行 417（「max_nodes 只在层开始时
检查，单层全部邻居一次进 IN 查询」）。仓内无规定该分批大小的理论章节，属经验标定。
边界（削掉的判别力，如实标注）：只消除超长绑定列表，**不**约束单层扩展规模——
候选集内存占用仍随扇出线性增长（与旧实现同阶）；`subgraph` 的同款 IN 查询不在本件
（其候选集来自本函数返回的 ≤max_nodes 条，天然有界）。

断言组（把修复改回一次性 IN ⇒ Q1/Q2 红）：
  Q1 高扇出不崩：32767 个直接叶子时 `traverse(max_nodes=1, max_depth=1)` 正常返回
     （旧形态：`OperationalError: too many SQL variables`）。
  Q2 绑定变量有界：猴子补丁包一层 cursor 记录每条 SELECT 的绑定参数个数，断言
     每条 IN 查询的参数数 ≤ `_SQL_IN_CHUNK`（旧形态：单条 32767 个参数）。
  Q3 语义保留（issue 明确要求）：高扇出下 importance 前 N 名必须被选出——把
     max_nodes 内的节点设为低 importance、max_nodes 外设为高 importance，
     `traverse(max_nodes=3)` 必须返回那 3 个高 importance 节点（而非最先遇到的）。
  Q4 非回归：小图读数不变（max_nodes 截断、importance 降序、min_importance 过滤）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue417_traverse_sql_variable_limit_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from lingshu.core.core import (  # noqa: E402
    _SQL_IN_CHUNK,
    ConditionSpace,
    EdgeType,
    SpacetimeMemoryEngine,
)

CS = ConditionSpace("p", "t", (0.0, 0.0), "c").to_json()


def _fanout(n_leaves, imp_of=None, direction="out"):
    """root + n 个直接叶子（无孤儿边、无环，issue 同款构造）。"""
    e = SpacetimeMemoryEngine(":memory:")
    s = e.store
    now = time.time()
    rows = [("root", "根", "text", "{}", now, CS, 0.9, 0.9, "knowledge", 0,
             now, now, "[]", "{}", "{}", None)]
    erows = []
    for i in range(n_leaves):
        lid = "leaf%06d" % i
        imp = 0.5 if imp_of is None else imp_of(i)
        rows.append((lid, "叶%d" % i, "text", "{}", now + i, CS, imp, 0.5,
                     "knowledge", 0, now, now, "[]", "{}", "{}", None))
        if direction == "out":
            erows.append(("edge%06d" % i, "root", lid, "causal", CS, 0.5, 1.0, 0,
                          now, 0.0, "extracted"))
        else:
            erows.append(("edge%06d" % i, lid, "root", "causal", CS, 0.5, 1.0, 0,
                          now, 0.0, "extracted"))
    s.conn.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    s.conn.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?,?,?,?,?,?)", erows)
    s.conn.commit()
    return e


class _Trace:
    """sqlite3 trace 回调：记录每条**展开后**的 SQL（含绑定的字面量）——#417 断言用。

    用 trace 而非包 cursor：`LayeredStore.conn` 是只读 property（每线程按需建连），
    无法替换；trace 直接给出实际执行的语句文本，可从中数出 IN 列表的元素个数。
    """

    def __init__(self):
        self.statements = []

    def __call__(self, stmt):
        self.statements.append(stmt)


def _in_list_sizes(statements):
    """从展开后的 SQL 里数出每个 `IN (...)` 列表的元素个数。"""
    import re
    sizes = []
    for st in statements:
        for m in re.finditer(r"IN \(([^)]*)\)", st):
            body = m.group(1).strip()
            if not body:
                sizes.append(0)
            else:
                sizes.append(body.count(",") + 1)
    return sizes


def test_q1_high_fanout_does_not_raise():
    """Q1：32767 直接叶子不得再抛 too many SQL variables。"""
    e = _fanout(32767)
    r = e.store.traverse("root", max_nodes=1, max_depth=1)
    assert len(r) == 1, (
        "#417：高扇出单层遍历崩溃或读数异常——returned_count=%r（旧形态："
        "OperationalError: too many SQL variables）" % len(r))


def test_q2_bind_variable_count_bounded():
    """Q2：每条 IN 查询的元素数必须 ≤ _SQL_IN_CHUNK。"""
    e = _fanout(5000)
    tr = _Trace()
    e.store.conn.set_trace_callback(tr)
    try:
        e.store.traverse("root", max_nodes=1, max_depth=1)
    finally:
        e.store.conn.set_trace_callback(None)
    sizes = _in_list_sizes(tr.statements)
    assert sizes, "前置：importance 补全的 IN 查询应被执行到"
    worst = max(sizes)
    assert worst <= _SQL_IN_CHUNK, (
        "#417：仍有超长绑定列表——单条 IN 含 %d 个元素（上限 %d；旧形态 5000）"
        % (worst, _SQL_IN_CHUNK))


def test_q3_top_n_by_importance_preserved():
    """Q3：importance 前 N 名必须被选出（不得截取最先遇到的 N 个邻居）。"""
    # 前 100 个叶子 importance=0.2，其余 4900 个 =0.95
    e = _fanout(5000, imp_of=lambda i: 0.2 if i < 100 else 0.95)
    r = e.store.traverse("root", max_nodes=3, max_depth=1)
    assert len(r) == 3, "前置：max_nodes=3 应返回 3 条"
    assert all(abs(x["importance"] - 0.95) < 1e-9 for x in r), (
        "#417：截取的是最先遇到的邻居而非 importance 前 N——importance=%r"
        % [x["importance"] for x in r])
    assert [x["importance"] for x in r] == sorted(
        [x["importance"] for x in r], reverse=True), "非回归：结果应按 importance 降序"


def test_q4_small_graph_unchanged():
    """Q4：非回归——小图读数（截断/降序/过滤）不变。"""
    e = _fanout(10, imp_of=lambda i: 0.1 * i)
    r = e.store.traverse("root", max_nodes=3, max_depth=1)
    assert len(r) == 3, "非回归：max_nodes 截断变了——%r" % len(r)
    assert [round(x["importance"], 4) for x in r] == [0.9, 0.8, 0.7], (
        "非回归：排序变了——%r" % [x["importance"] for x in r])
    r2 = e.store.traverse("root", max_nodes=20, min_importance=0.55, max_depth=1)
    assert [round(x["importance"], 4) for x in r2] == [0.9, 0.8, 0.7, 0.6], (
        "非回归：min_importance 过滤变了——%r" % [x["importance"] for x in r2])
    # in 方向（叶子→root）照常
    e2 = _fanout(5, direction="in")
    r3 = e2.store.traverse("root", direction="in", max_nodes=10, max_depth=1)
    assert len(r3) == 5 and all(x["direction"] == "in" for x in r3)
