# -*- coding: utf-8 -*-
"""test_issue258_consolidate_full_scan_guard · #258 巩固周期取数截断守卫
============================================================================
缺陷（lingshu issue #258 · P1）：`consolidate_cycle` 用
`self.store.query_nodes(limit=1000)` 取数，而 `query_nodes` 的排序是
`ORDER BY importance DESC, last_access DESC`。库内节点超过 1000 条后，
排名 1000 之外的节点**全是低 importance 节点**——正是本函数声明要
「降权 + 标记 compressible」的那批；它们永不降权、永不标记，且 `stats`
仍报 `degraded: 0`（函数自己也不知道自己漏了）。

修法：取数改 `query_nodes(limit=-1)`（SQLite `LIMIT -1` = 不限条数），
与同文件 #297 的 M5 去重候选视野修正同款；口径与 docstring 声明对齐。
判据来源：公开 issue #258 ＋ 分诊表 `triage_lingshu_ALL.md` 行 258。

守卫断言组：
  A 组（硬判据 · 界外低权必须被处理）：总节点 = 1030（1000 噪声 0.5 +
     30 低权 0.05），断言 `stats["degraded"] == 30` 且 30 条低权节点
     importance 全部下降、全部被标记 `compressible`。旧实现（limit=1000）
     ⇒ `degraded=0` ⇒ 必红。
  B 组（对照 · 界内低权照旧被处理）：总节点 < 1000 时同样 30/30 被处理
     ——修法未把界内行为改坏。
  C 组（对照 · 高重要度演练照旧）：高重要度节点仍走演练分支
     （`rehearsed` 计数 > 0）。

必须用文件库：`:memory:` 后端下 `increment_access` 另开连接落进空库
（issue #52），会掩盖行为。

运行（仓根）：
    python -X utf8 -m pytest tests/test_issue258_consolidate_full_scan_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

N_LOW = 30


def _seed(engine, noise):
    """噪声（importance=0.5，占满 importance 降序前排）+ 低权（0.05）各就位。"""
    for i in range(noise):
        engine.add_perception(f"噪声{i}：例行记录{i}", skip_dedup=True,
                              importance=0.5)
    low = [engine.add_perception(f"低权重{i}：待巩固记录{i}", skip_dedup=True,
                                 importance=0.05).id for i in range(N_LOW)]
    return low


def test_low_weight_beyond_rank_1000_still_consolidated(tmp_path):
    """A 组：总节点 1030（> 1000）时，界外低权节点必须被降权并标记 compressible。"""
    eng = SpacetimeMemoryEngine(str(tmp_path / "a.db"))
    try:
        low = _seed(eng, 1000)
        total = eng.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert total == 1000 + N_LOW, "前置：库规模应为 1030，实际 %d" % total

        # 确认这批低权节点确实落在 query_nodes(limit=1000) 的界外（缺陷的触发条件）
        visible = {n.id for n in eng.store.query_nodes(limit=1000)}
        assert not any(i in visible for i in low), (
            "前置：低权节点应全部落在 importance 降序前 1000 之外")

        before = {i: eng.store.get_node(i).importance for i in low}
        stats = eng.consolidate_cycle()
        after = {i: eng.store.get_node(i).importance for i in low}

        degraded = [i for i in low if after[i] < before[i] - 1e-9]
        compressible = [i for i in low if "compressible" in eng.store.get_node(i).tags]

        assert stats["degraded"] == N_LOW, (
            "#258：界外低权节点必须被降权（旧实现 limit=1000 ⇒ degraded=0）；"
            "stats=%s" % stats)
        assert len(degraded) == N_LOW, (
            "#258：importance 实际下降的低权节点数应为 %d，实际 %d" % (N_LOW, len(degraded)))
        assert stats["compressible"] == N_LOW, (
            "#258：界外低权节点必须被标记 compressible（旧实现 ⇒ 0）；stats=%s" % stats)
        assert len(compressible) == N_LOW, (
            "#258：带 compressible 标签的低权节点数应为 %d，实际 %d"
            % (N_LOW, len(compressible)))
    finally:
        eng.close()


def test_in_range_low_weight_still_consolidated(tmp_path):
    """B 组（对照）：总节点 < 1000 时低权节点照旧 30/30 被处理。"""
    eng = SpacetimeMemoryEngine(str(tmp_path / "b.db"))
    try:
        low = _seed(eng, 300)
        before = {i: eng.store.get_node(i).importance for i in low}
        stats = eng.consolidate_cycle()
        after = {i: eng.store.get_node(i).importance for i in low}
        degraded = [i for i in low if after[i] < before[i] - 1e-9]
        assert stats["degraded"] == N_LOW and len(degraded) == N_LOW, (
            "B 组对照：界内低权节点照旧全部被降权；stats=%s" % stats)
    finally:
        eng.close()


def test_rehearsal_branch_still_works(tmp_path):
    """C 组（对照）：高重要度节点仍走演练分支（rehearsed 计数 > 0）。"""
    eng = SpacetimeMemoryEngine(str(tmp_path / "c.db"))
    try:
        eng.add_perception("高重要度记忆", skip_dedup=True, importance=0.85)
        stats = eng.consolidate_cycle()
        assert stats["rehearsed"] >= 1, (
            "C 组对照：高重要度节点应被演练（rehearsed ≥ 1）；stats=%s" % stats)
    finally:
        eng.close()
