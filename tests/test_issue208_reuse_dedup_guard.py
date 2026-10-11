# -*- coding: utf-8 -*-
"""#208 `_note_reuse` 同轮同节点重复落库 + `_reuse_tracker` 无上界 —— 守卫
（lingshu/core/core.py）

缺陷（issue #208）：
  docstring 写「同轮同节点去重」，内存 bucket 确是 set，但**落库时插入的是整个
  累计 bucket**（`for nid in sorted(bucket)`）⇒ 同一轮每检索一次，之前已记录的
  节点就再插一遍，`flywheel_reuse` 里同轮同节点的行数 = 其后的检索次数；下游按
  行数统计的复用率被放大。另 `_reuse_tracker`（按轮次编号的 dict）从不清理，
  常驻进程持续增长。

修法（issue 建议 1「只插入 new」路线）：
  `new = set(node_ids) - bucket` → `bucket |= new` → **只插入 `new`**；
  tracker 只保留最近 `_REUSE_TRACKER_ROUNDS` 轮（更旧键整键删除）。
  取「只插入 new」而非「表加 UNIQUE」：`_init_tables` 对既有库是
  `CREATE TABLE IF NOT EXISTS`，加不了新 UNIQUE ⇒ 需迁移；纯代码改动即时生效。

断言组（抽掉「只插入 new」即 A 变红；抽掉 tracker 修剪即 B 变红）：
  A 同轮同节点在库中只有一行（issue 实测 [3,2,4]/总 9 → 现应为 [1,1,1]/总 3）。
  B `_reuse_tracker` 轮数有上界（跑 > K 轮后 len <= K）。
  C 对照：不同轮的复用照常分别记录（去重只在本轮内，不跨轮吞并）。
  D 对照：跨轮同节点仍各自一行（每轮一行）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue208_reuse_dedup_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_K = SpacetimeMemoryEngine._REUSE_TRACKER_ROUNDS


def _rows(e, rnd=None):
    sql = ("SELECT round, node_id, COUNT(*) FROM flywheel_reuse"
           + (" WHERE round=?" if rnd is not None else "")
           + " GROUP BY round, node_id")
    args = () if rnd is None else (rnd,)
    return e.store.conn.execute(sql, args).fetchall()


def test_a_same_round_same_node_single_row():
    """A：同轮同节点在库中只有一行（issue 复现脚本：旧 [3,2,4]/总 9）。"""
    e = SpacetimeMemoryEngine(":memory:")
    for t in ("苹果是红色的水果", "香蕉是黄色的水果", "葡萄是紫色的水果"):
        e.add_perception(t, skip_dedup=True)
    for q in ("苹果", "香蕉", "葡萄", "水果"):
        e.search_content(q)
    counts = [r[2] for r in _rows(e)]
    assert counts and all(c == 1 for c in counts), (
        "#208 同轮同节点重复落库：每节点行数 %r（应全为 1）" % (counts,))
    total = e.store.conn.execute("SELECT COUNT(*) FROM flywheel_reuse").fetchone()[0]
    assert total == len(counts), (
        "#208 总行数 %d != 去重后节点数 %d" % (total, len(counts)))


def test_b_reuse_tracker_bounded():
    """B：`_reuse_tracker` 轮数有上界（旧实现从不清理 ⇒ 无界增长）。"""
    e = SpacetimeMemoryEngine(":memory:")
    for i in range(_K + 8):
        e.add_perception("轮次探针 %d" % i, skip_dedup=True)
        e.search_content("轮次探针")
    assert len(e._reuse_tracker) <= _K, (
        "#208 tracker 无上界：保留 %d 轮 > K=%d" % (len(e._reuse_tracker), _K))


def test_c_distinct_rounds_recorded_separately():
    """C：对照——不同轮的复用分别记录（去重只在本轮内，不跨轮吞并）。"""
    e = SpacetimeMemoryEngine(":memory:")
    k = e.add_perception("跨轮复用探针", skip_dedup=True)
    e.search_content("跨轮复用探针")     # 轮 1
    e.add_perception("另一条", skip_dedup=True)   # 进入轮 2
    e.search_content("跨轮复用探针")     # 轮 2
    rounds = sorted(r[0] for r in _rows(e))
    assert len(rounds) == 2 and rounds[0] != rounds[1], (
        "#208 跨轮复用未分别记录：rounds=%r" % (rounds,))
    assert all(r[2] == 1 for r in _rows(e)), _rows(e)


if __name__ == "__main__":
    fns = [test_a_same_round_same_node_single_row, test_b_reuse_tracker_bounded,
           test_c_distinct_rounds_recorded_separately]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as exc:
            failed += 1
            print("FAIL %s | %s" % (fn.__name__, exc))
    print("-- %d passed, %d failed --" % (len(fns) - failed, failed))
    print("VERDICT=%s" % ("PASS" if not failed else "FAIL"))
