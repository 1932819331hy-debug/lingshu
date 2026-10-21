# -*- coding: utf-8 -*-
"""test_issue658_limit_none_full_return_guard · #658 同族读函数的 limit=None 口径统一

缺陷（修复前 main，值守独立复现）：
  `query_nodes` / `get_recent_context` / `get_nodes_by_tag` / `get_nodes_in_range`
  无条件把 `limit` 绑进 `LIMIT ?`——SQLite 的 `LIMIT` **不接受 NULL**
  （`IntegrityError: datatype mismatch`，与 `LIMIT -1`＝无限制不同）⇒ 调用方按仓内
  既有「None＝全量返回」语义传参即抛。而同族 `query_nodes_by_time` 早已按
  「None ⇒ 不拼 LIMIT、全量返回」实现（其 docstring 明写该语义）⇒ 同族不一致。

修法：四个函数统一为 `query_nodes_by_time` 的形态——`limit is not None` 才拼
`LIMIT ?`，否则不加限制子句。**阈值本体未改**（各函数默认 limit 一字不动）。

组（回退即红）：
  G1 四个函数 `limit=None` ⇒ 返回**全量**（与 `query_nodes_by_time(None)` 同口径）。
  G2 `limit=0` / `limit=-1` 行为与 `query_nodes_by_time` 一致（0＝空、-1＝全量）。
  G3 防过修正向：显式 `limit=k` 仍**精确截断**到 k 条（既有分页语义未动）。
  G4 `get_nodes_by_tag` 的 tag 过滤在 None 口径下仍生效（不是把 WHERE 也丢了）。

判别力：把任一函数的 `if limit is not None` 分支还原成无条件绑 `LIMIT ?`
⇒ G1 必红；还原 ⇒ 全绿。

运行：python -X utf8 -m pytest tests/test_issue658_limit_none_full_return_guard.py -q
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine(n_ctx=4, n_tag=3):
    """建库：n_ctx 条情境层（带 tag 'probe658'）+ n_tag 条知识层。"""
    e = SpacetimeMemoryEngine(":memory:")
    for i in range(n_ctx):
        e.add_context("情境记录 %d：limit=None 口径验证" % i, tags=["probe658"])
    for i in range(n_tag):
        e.add_perception("知识记录 %d：limit=None 口径验证" % i, tags=["probe658"])
    return e


def test_g1_none_returns_full_same_as_by_time():
    """G1：四函数 limit=None ⇒ 全量，且与 query_nodes_by_time(None) 同口径。"""
    e = _engine()
    total = len(e.store.query_nodes(limit=-1))
    assert total == 7, total

    got = {
        "query_nodes": len(e.store.query_nodes(limit=None)),
        "get_recent_context": len(e.store.get_recent_context(limit=None)),
        "get_nodes_by_tag": len(e.store.get_nodes_by_tag("probe658", limit=None)),
        "get_nodes_in_range": len(e.store.get_nodes_in_range(0.0, 1e12, limit=None)),
        "query_nodes_by_time": len(e.store.query_nodes_by_time(limit=None)),
    }
    assert got["query_nodes"] == total, got
    assert got["get_nodes_by_tag"] == total, got
    assert got["get_nodes_in_range"] == total, got
    # 情境层 4 条（get_recent_context 只看 context 层）
    assert got["get_recent_context"] == 4, got
    # 对照口径：query_nodes_by_time 本就走 None=全量
    assert got["query_nodes_by_time"] == total, got


def test_g2_zero_and_minus_one_consistent():
    """G2：limit=0 ⇒ 空；limit=-1 ⇒ 各自的全量（四函数与 by_time 同口径）。

    注：`get_recent_context` 只看**情境层** ⇒ 其「全量」是 4（不是库内总数 7）；
    本组按各函数自己的 None 口径取全量基准，避免把层过滤当成 limit 语义。"""
    e = _engine()
    cases = [
        ("query_nodes", lambda k: e.store.query_nodes(limit=k),
         lambda: e.store.query_nodes(limit=None)),
        ("get_recent_context", lambda k: e.store.get_recent_context(limit=k),
         lambda: e.store.get_recent_context(limit=None)),
        ("get_nodes_by_tag", lambda k: e.store.get_nodes_by_tag("probe658", limit=k),
         lambda: e.store.get_nodes_by_tag("probe658", limit=None)),
        ("get_nodes_in_range", lambda k: e.store.get_nodes_in_range(0.0, 1e12, limit=k),
         lambda: e.store.get_nodes_in_range(0.0, 1e12, limit=None)),
        ("query_nodes_by_time", lambda k: e.store.query_nodes_by_time(limit=k),
         lambda: e.store.query_nodes_by_time(limit=None)),
    ]
    for name, fn, full in cases:
        n_full = len(full())
        assert len(fn(0)) == 0, (name, "limit=0 应返回空")
        assert len(fn(-1)) == n_full, (name, "limit=-1 应等于 None 全量", len(fn(-1)), n_full)


def test_g3_explicit_limit_still_truncates():
    """G3 防过修：显式 limit=2 仍精确截断（既有分页语义未动）。"""
    e = _engine()
    for name, fn in [
        ("query_nodes", lambda: e.store.query_nodes(limit=2)),
        ("get_recent_context", lambda: e.store.get_recent_context(limit=2)),
        ("get_nodes_by_tag", lambda: e.store.get_nodes_by_tag("probe658", limit=2)),
        ("get_nodes_in_range", lambda: e.store.get_nodes_in_range(0.0, 1e12, limit=2)),
        ("query_nodes_by_time", lambda: e.store.query_nodes_by_time(limit=2)),
    ]:
        assert len(fn()) == 2, (name, len(fn()))


def test_g4_tag_filter_still_applies_under_none():
    """G4：limit=None 口径下 tag 过滤仍生效（不是把 WHERE 一并丢掉）。"""
    e = _engine()
    e.add_perception("无标签记录：不应被 tag 查询命中")
    got = e.store.get_nodes_by_tag("probe658", limit=None)
    assert len(got) == 7, [n.id for n in got]
    assert all("probe658" in (n.tags or []) for n in got)
    assert e.store.get_nodes_by_tag("nonexistent-tag", limit=None) == []


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
