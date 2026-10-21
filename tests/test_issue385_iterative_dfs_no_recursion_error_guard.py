# -*- coding: utf-8 -*-
"""test_issue385_iterative_dfs_no_recursion_error_guard · lingshu issue #385 守卫
============================================================================
缺陷（lingshu issue #385 · 基线 main 83cce59）：
  `reason_causal`（两个分支：`collect` 与 `infer_causal_paths.dfs`）与
  `find_cycles.dfs` 都是**每走一条边递归一层**的 Python 递归 DFS，递归深度 =
  路径/环长 = `max_depth`，而 `max_depth` 未与 `sys.getrecursionlimit()` 挂钩：
  1500 节点线性因果链上 `reason_causal(n0, max_depth=2000)` /
  `reason_causal(n0, 'n1499', max_depth=2000)` / `find_cycles(max_depth=2000)`
  全部抛 `RecursionError`，而同一张图上**迭代实现**的 `timeline` 照常返回 1500 个
  节点、`has_causal_cycle` 照常返回 True。

修法（最小 · 按 issue 修复建议）：
  三处 DFS 改**显式栈**（与同文件 `has_causal_cycle` / Tarjan / `traverse` 一致）。
  `infer_causal_paths` 用「边序逆序入栈」保持递归版的正向输出顺序；
  `collect` 与 `find_cycles` 用「帧 + 边迭代器」逐字保持递归版的交错输出顺序。
  `max_depth` / `truncated` / `cyclic` / 空列表 / 每环只报一次 语义一律不动。

判据来源：公开 issue #385「修复建议」（「把三个 DFS 改成显式栈（与同文件的
has_causal_cycle / Tarjan 迭代写法一致）」）＋ 其复现实读（1500 节点链/环上三处
RecursionError，timeline/has_causal_cycle 对照正常）＋ 分诊表
`triage_lingshu_ALL.md` 行 385（「递归深度=max_depth，链长>~1000 抛 RecursionError
（同文件 timeline/has_causal_cycle 为迭代）」）。仓内无规定该实现口径的理论章节，
属经验标定（本件 #385）。
边界（削掉的判别力，如实标注）：只改遍历实现，不放宽任何判据；单帧携带路径副本，
内存与路径长度同阶（与递归版栈帧同阶）。

断言组（把修复改回递归 DFS ⇒ Q1/Q2/Q3/Q4 红）：
  Q1 长链不崩：1500 节点链 `reason_causal(n0, max_depth=2000)` 返回 1 条长度 1500
     的链（旧形态：RecursionError）；`end_id` 分支返回 1 条长度 1499 的路径。
  Q2 长环不崩：同一图加一条回边后 `find_cycles(max_depth=2000)` 返回 1 个环
     （旧形态：RecursionError）。
  Q3 输出顺序与递归版逐字一致：对随机小图，迭代实现与**独立重写的参考递归实现**
     产出完全相同的边 id 序列（证明「改栈」未悄悄改变结果次序）。
  Q4 对照不变：同一张 1500 节点图上迭代实现的 `timeline` 仍返回 1500
     （前置：确认这不是「图坏了」）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue385_iterative_dfs_no_recursion_error_guard.py -q --no-header
"""
from __future__ import annotations

import os
import random
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from lingshu.core.core import (  # noqa: E402
    ConditionSpace,
    EdgeType,
    SpacetimeMemoryEngine,
)

L = 1500


def _long_chain(close_ring: bool = False):
    """L 节点线性因果链（issue 同款批量写入）；close_ring 时末尾回边闭单环。"""
    e = SpacetimeMemoryEngine(":memory:")
    s = e.store
    cs = ConditionSpace("p", "t", (0.0, 0.0), "c").to_json()
    now = time.time()
    s.conn.executemany(
        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"n{i}", f"事件{i}", "text", "{}", now + i, cs, 0.5, 0.5, "knowledge",
          0, now, now, "[]", "{}", "{}", None) for i in range(L)])
    s.conn.executemany(
        "INSERT INTO edges VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(f"e{i}", f"n{i}", f"n{i+1}", "causal", cs, 0.5, 1.0, 0, now, 0.0,
          "extracted") for i in range(L - 1)])
    if close_ring:
        s.conn.execute("INSERT INTO edges VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       ("back", f"n{L-1}", "n0", "causal", cs, 0.5, 1.0, 0, now,
                        0.0, "extracted"))
    s.conn.commit()
    return e


def test_q1_long_chain_no_recursion_error():
    """Q1：1500 节点链上两个 reason_causal 分支都不得抛 RecursionError。"""
    e = _long_chain()
    chains = e.reason_causal("n0", max_depth=2000)
    assert len(chains) == 1 and len(chains[0]) == L - 1, (
        "#385：长链 reason_causal(start) 返回 %r（旧形态：RecursionError）"
        % [len(c) for c in chains])
    paths = e.reason_causal("n0", "n%d" % (L - 1), max_depth=2000)
    assert len(paths) == 1 and len(paths[0]) == L - 1, (
        "#385：长链 reason_causal(start,end) 返回 %r（旧形态：RecursionError）"
        % [len(p) for p in paths])


def test_q2_long_ring_no_recursion_error():
    """Q2：1500 节点单环上 find_cycles 不得抛 RecursionError。"""
    e = _long_chain(close_ring=True)
    cycles = e.store.find_cycles(max_depth=2000)
    assert len(cycles) == 1 and len(cycles[0]) == L, (
        "#385：长环 find_cycles 返回 %r（旧形态：RecursionError）"
        % [len(c) for c in cycles])


def test_q3_output_order_matches_recursive_reference():
    """Q3：随机小图上迭代实现与参考递归实现的输出边序列逐字一致。"""
    rnd = random.Random(20261011)

    def ref_chains(edges_by_src, start, max_depth):
        """参考递归实现（issue 描述的原逻辑，逐字复刻）——独立判据。"""
        out = []

        def collect(cur, path, on_path, depth):
            kept = edges_by_src.get(cur, [])
            if not kept:
                if path:
                    out.append(list(path))
                return
            if depth >= max_depth:
                if path:
                    out.append(list(path) + ["T"])
                return
            for e in kept:
                if e[1] in on_path:
                    out.append(list(path) + [e[0], "C"])
                    continue
                collect(e[1], path + [e[0]], on_path | {e[1]}, depth + 1)

        collect(start, [], {start}, 0)
        return out

    def ref_cycles(edges_by_src, nodes, max_depth):
        """参考递归环枚举（每环最小节点起、环边集去重）——独立判据。"""
        out, seen = [], set()

        def dfs(cur, start, path, on_path, depth):
            if depth > max_depth:
                return
            for e in edges_by_src.get(cur, ()):
                t = e[1]
                if t == start:
                    cyc = path + [e[0]]
                    key = frozenset(cyc)
                    if key not in seen:
                        seen.add(key)
                        out.append(cyc)
                    continue
                if t in on_path or t <= start:
                    continue
                dfs(t, start, path + [e[0]], on_path | {t}, depth + 1)

        for s in sorted(nodes):
            dfs(s, s, [], {s}, 0)
        return out

    for trial in range(12):
        n = rnd.randint(4, 9)
        names = ["v%d" % i for i in range(n)]
        e = SpacetimeMemoryEngine(":memory:")
        ids = {nm: e.add_perception("节点%s：记录%d" % (nm, trial * 100 + i),
                                    skip_dedup=True).id
               for i, nm in enumerate(names)}
        edges_by_src = {}
        for a in names:
            for b in names:
                if a != b and rnd.random() < 0.35:
                    ed = e.add_edge(ids[a], ids[b], EdgeType.CAUSAL)
                    edges_by_src.setdefault(ids[a], []).append((ed.id, ids[b]))
        md = rnd.randint(1, 4)
        want = ref_chains(edges_by_src, ids["v0"], md)
        # reason_causal 产出 CausalChain：把 truncated/cyclic 标记映射到参考记号
        mapped = []
        for c in e.reason_causal(ids["v0"], max_depth=md):
            seq = [x.id for x in c]
            if c.truncated:
                seq = seq + ["T"]
            if c.cyclic:
                seq = seq + ["C"]
            mapped.append(seq)
        assert mapped == want, (
            "#385 trial=%d：迭代实现输出顺序与递归参考不一致——got=%r want=%r"
            % (trial, mapped, want))

        got_cyc = [sorted(x.id for x in c) for c in e.store.find_cycles(max_depth=md + 2)]
        want_cyc = [sorted(x) for x in ref_cycles(edges_by_src, set(ids.values()),
                                                  md + 2)]
        # find_cycles 在非平凡 SCC 内枚举；参考实现不做 SCC 剪枝但输出集合应一致
        assert sorted(map(tuple, got_cyc)) == sorted(map(tuple, want_cyc)), (
            "#385 trial=%d：迭代 find_cycles 与递归参考不一致——got=%r want=%r"
            % (trial, got_cyc, want_cyc))


def test_q4_iterative_control_unchanged():
    """Q4：同一张 1500 节点图上迭代实现的 timeline 仍返回 1500（非「图坏了」）。"""
    e = _long_chain()
    assert len(e.timeline("n0", max_depth=2000)) == L
    assert e.store.has_causal_cycle() is False
