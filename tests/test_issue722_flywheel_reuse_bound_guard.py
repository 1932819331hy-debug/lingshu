# -*- coding: utf-8 -*-
"""#722 `flywheel_reuse` 持久表跨会话只增不减 —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #722，本守卫独立复现，读数见交付报告）：
  `_note_reuse` 的 DB 落库段（表 DDL `session_id/round/node_id/ts`，无主键、无上限）
  是**跨会话累计**层。`#208` 只把去重钉在内存 tracker（最近 `_REUSE_TRACKER_ROUNDS`
  轮）与「只插 new」两处，DB 表本身既无读取窗口也无回收面（全仓无
  `DELETE FROM flywheel_reuse`）⇒ 常驻部署下「行数 = 会话数 × 每会话命中节点数」
  只增不减（`#122`「无界增长族」的 DB 表落点）。对照同文件 `gap_history` 有 2000
  行钳制——同类钳制手法漏挂。

修法（同 `record_info_gap` 对 `gap_history` 的既有手法）：`_note_reuse` 落库后
  同事务追加一次裁剪，只保留最近 `FLYWHEEL_REUSE_MAX_ROWS` 行，保留序取 `rowid`
  （插入序）——与 `prune_self_snapshots`(#489) / `enforce_context_cap`(#259)
  同键同法，不用墙钟 `ts`（`time.time()` 不单调，NTP 步进/快照回滚下会先删刚写入的行）。

断言组（把裁剪语句抽掉 ⇒ A 变红；把上界读成常量硬编码 ⇒ B 仍可测）：
  A 上界生效：把 `FLYWHEEL_REUSE_MAX_ROWS` 调小到 K，跑出远超 K 的复用行后，
    表内行数必须 <= K（旧实现恒等于总插入数 ⇒ 红）。
  B 保留的是**最近**的：裁剪后仍在表内的是最后写入的节点 id（按 rowid 插入序）。
  C 正对照（#208 语义未被改坏）：同会话重复 recall 不重复插行。
  D 正对照（量具非恒绿）：上界大于总行数时裁剪**不动**任何行（不误删）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue722_flywheel_reuse_bound_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _rows(e):
    return e.store.conn.execute(
        "SELECT session_id, round, node_id, ts FROM flywheel_reuse").fetchall()


def test_a_table_is_bounded():
    """A：表行数受 `FLYWHEEL_REUSE_MAX_ROWS` 钳制（旧实现无界）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.FLYWHEEL_REUSE_MAX_ROWS = 6
        for rnd in range(10):
            for i in range(3):
                e.add_perception("压测记录 %d-%d 内容" % (rnd, i), skip_dedup=True)
            e.search_content("压测记录")
        n = e.store.conn.execute("SELECT COUNT(*) FROM flywheel_reuse").fetchone()[0]
        assert n > 0, "A 前置：本用例须真产生复用观测行（否则空转成假绿）"
        assert n <= 6, (
            "#722 flywheel_reuse 无界增长：%d 行 > 上界 6（旧实现只增不减）" % (n,))
    finally:
        e.close()


def test_b_keeps_most_recent_rows():
    """B：裁剪保留的是**最近**写入的行（按插入序 rowid）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.FLYWHEEL_REUSE_MAX_ROWS = 3
        ids = []
        for i in range(6):
            n = e.add_perception("观测记录 序号%d 内容" % i, skip_dedup=True)
            ids.append(n.id)
            e.search_content("序号%d" % i)
        rows = _rows(e)
        assert rows, "B 前置：表须有行"
        kept = {r[2] for r in rows}
        assert kept.issubset(set(ids)), "B：表内出现非本次写入的节点 id：%r" % (kept,)
        assert ids[-1] in kept, (
            "#722 裁剪把**刚写入**的行删了（保留序不是插入序）：kept=%r" % (kept,))
        assert len(rows) <= 3, "#722 上界未生效：%d 行" % (len(rows),)
    finally:
        e.close()


def test_c_positive_control_same_round_dedup_intact():
    """C（正对照）：#208 的同轮同节点去重语义未被改坏。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        for t in ("苹果是红色的水果", "香蕉是黄色的水果", "葡萄是紫色的水果"):
            e.add_perception(t, skip_dedup=True)
        for q in ("苹果", "香蕉", "葡萄", "水果"):
            e.search_content(q)
        rows = e.store.conn.execute(
            "SELECT round, node_id, COUNT(*) FROM flywheel_reuse GROUP BY round, node_id"
        ).fetchall()
        assert rows, "C 前置：本用例须真产生复用观测行"
        assert all(c == 1 for _, _, c in rows), (
            "#722 修法破坏了 #208 的同轮去重：%r" % (rows,))
    finally:
        e.close()


def test_d_positive_control_no_prune_below_bound():
    """D（正对照）：总行数小于上界时裁剪不动任何行（不误删）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.FLYWHEEL_REUSE_MAX_ROWS = 1000
        for i in range(3):
            e.add_perception("小样本记忆 %d 内容" % i, skip_dedup=True)
        e.search_content("小样本记忆")
        rows = _rows(e)
        assert rows, "D 前置：表须有行"
        assert len(rows) == 3, (
            "#722 未越上界却发生裁剪（误删）：%d 行（期望 3）" % (len(rows),))
    finally:
        e.close()
