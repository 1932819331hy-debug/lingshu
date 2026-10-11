# -*- coding: utf-8 -*-
"""test_issue259_context_fifo_insertion_order_guard · #259 情境层排序键守卫
============================================================================
缺陷（lingshu issue #259 · P1）：情境层的 FIFO 淘汰（`enforce_context_cap`）
与「最近情境」（`get_recent_context`）按**墙钟 `created_at`** 排序，而
`created_at = time.time()` 不单调。只要库里有任何情境节点的 `created_at`
大于当前时间（NTP 步进校正 / 虚拟机快照回滚 / 无 RTC 开机 / `import_all`
导入时钟偏快机器的备份），新写入节点的 `created_at` 就是全层最小，紧接着的
淘汰会把它**当场删除**——`add_context` 照常返回 id，库里却已无该节点。

修法：排序键由 `created_at` 改为插入序 `rowid`（`enforce_context_cap`
`ORDER BY rowid ASC` 淘汰最旧；`get_recent_context` `ORDER BY rowid DESC`
取最新）。判据来源：公开 issue #259 ＋ 分诊表 `triage_lingshu_ALL.md` 行 259。

守卫断言组：
  A 组（硬判据 · 时钟回拨）：把已写情境的 `created_at` 推到未来 1 天后，
     `add_context` 返回的节点必须**仍在库中**，且 `get_recent_context(1)`
     第一条就是它；随后连写 20 条必须 20/20 存活。旧实现（按 created_at
     排序）⇒ 写入即删除、存活 0/20 ⇒ 必红。
  B 组（硬判据 · 未来时间戳钉死）：5 条 `created_at` 在 10 年后的情境节点
     占位，1000 轮衰减后写 20 条真实情境必须 20/20 存活。旧实现 ⇒ 0/20。
  C 组（对照 · 正常 FIFO 仍生效）：无时钟异常时，容量上限照旧被强制
     （写入超过上限后，层内恰好剩 max_size 条，且最旧的被淘汰）。
  D 组（对照 · 「最近」语义）：正常写入顺序下 `get_recent_context` 首条
     为最后写入者。

运行（仓根）：
    python -X utf8 -m pytest tests/test_issue259_context_fifo_insertion_order_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import Role, SpacetimeMemoryEngine  # noqa: E402


def test_clock_rollback_new_context_not_evicted():
    """A 组：已写情境的 created_at 在未来时，新写入的情境不得被当场淘汰。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.set_context_cap(5)
        for i in range(5):
            e.add_context(f"校正前的情境 {i}")
        # 模拟这 5 条是在时钟偏快时写入的（NTP 步进校正 / 快照回滚 / 无 RTC）
        e.store.conn.execute(
            "UPDATE nodes SET created_at = created_at + 86400 WHERE layer='context'")
        e.store.conn.commit()

        new = e.add_context("用户：刚才说的地址写错了，正确的是 3 号楼 502")
        assert e.store.get_node(new.id) is not None, (
            "#259：add_context 返回的节点必须仍在库中（旧实现按 created_at 排序 ⇒ "
            "它是全层最小、被自己的 FIFO 当场删除）")
        recent = e.get_recent_context(1)
        assert recent and recent[0].id == new.id, (
            "#259：get_recent_context(1) 第一条必须是刚写入的节点；实际=%s"
            % [n.content for n in recent])

        alive = [e.store.get_node(e.add_context(f"校正后第 {i} 条").id) is not None
                 for i in range(20)]
        assert sum(alive) == 20, (
            "#259：时钟回拨期间连写 20 条情境必须全部存活；实际 %d/20" % sum(alive))
    finally:
        e.close()


def test_future_timestamps_cannot_pin_context_layer():
    """B 组：未来时间戳的占位节点不得把情境层永久钉死。"""
    e = SpacetimeMemoryEngine(":memory:", role=Role.SUB)
    try:
        e.set_context_cap(5)
        for i in range(5):
            n = e.add_context(f"占位 {i}", importance=0.1)
            n.created_at = time.time() + 10 * 365 * 86400
            e.store.add_node(n)                      # SUB 写本地层合法
        for _ in range(1000):
            e.decay_cycle()
        alive = [e.store.get_node(e.add_context(f"真实情境 {i}", importance=0.9).id)
                 is not None for i in range(20)]
        assert sum(alive) == 20, (
            "#259：未来时间戳占位后，真实情境仍必须能写入并存活；实际 %d/20" % sum(alive))
    finally:
        e.close()


def test_normal_fifo_still_enforced():
    """C 组（对照）：无时钟异常时容量上限照旧被强制。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.set_context_cap(5)
        for i in range(12):
            e.add_context(f"情境 {i}")
        from lingshu.core.core import MemoryLayer
        nodes = e.store.get_layer_nodes(MemoryLayer.CONTEXT)
        assert len(nodes) == 5, (
            "C 组对照：容量上限=5 时层内应恰有 5 条；实际 %d" % len(nodes))
        contents = {n.content for n in nodes}
        assert "情境 0" not in contents and "情境 11" in contents, (
            "C 组对照：应淘汰最旧的、保留最新的；实际=%s" % sorted(contents))
    finally:
        e.close()


def test_recent_context_is_last_written():
    """D 组（对照）：正常顺序下「最近」= 最后写入者。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        for i in range(5):
            e.add_context(f"顺序情境 {i}")
        recent = e.get_recent_context(3)
        assert [n.content for n in recent] == ["顺序情境 4", "顺序情境 3", "顺序情境 2"], (
            "D 组对照：get_recent_context 应按写入序倒序；实际=%s"
            % [n.content for n in recent])
    finally:
        e.close()
