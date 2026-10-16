# -*- coding: utf-8 -*-
"""#723 `action_logs` 治理审计表无界增长且无清理面 —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #723，本守卫独立复现，读数见交付报告）：
  `action_logs` 的 DDL 无上限、`log_action` 纯追加，全仓**无任何回收面**（修前
  `grep -r "DELETE FROM action_logs"` 0 命中）。高频写点有三：`consolidate_cycle`
  尾部（`run_maintenance_cycle` 每周期一行）、`_strip_incident_edges` 审计（每条
  不可变层边剥离一行）、`set_escalation_enabled`。长程部署（MCP 3 进程 + 心跳 +
  睡眠巩固默认 60s）按每维护周期一行估算年化约 53 万行/进程。对照同文件
  `gap_history` 有 2000 行钳制（`record_info_gap`）——同族手法漏挂。

修法（**只补不改变对外语义的最小缺口**）：
  新增 store 级显式原语 `prune_action_logs(keep_rows=None)`（引擎侧同名入口），
  保留最近 `ACTION_LOGS_KEEP_ROWS`（缺省 10000）行、按插入序 `id` 降序，
  返回删除条数，并落一条 `action_logs_pruned` 审计（否则「审计行被删」不可审计）。
  ★**刻意不在 `run_maintenance_cycle` 里自动调用**：本表在多处被声明为「审计只
  追加（无更新/删除路径）」（`log_action` / `import_all` 的 #125 #479 硬配套 /
  `_strip_incident_edges`），且 `import_all` 把**带 id 的 action_logs 行**视为需密钥
  治理载荷——自动裁剪与保留窗口取值属**设计者裁面**（见交付报告「需设计者裁」）。
  故本守卫的 D 组把「维护周期不自动裁剪」这一**刻意不改**的现状钉成判据，防止
  后续无人裁量地把它改成自动裁剪。

断言组（把 `prune_action_logs` 抽掉 ⇒ A/B 变红）：
  A 显式回收面生效：写 N 行后 `prune_action_logs(keep_rows=K)` 返回 N-K、表内剩 K
    行（旧实现无此原语 ⇒ AttributeError 红）。
  B 保留的是**最近**的行（按插入序 id 降序），且裁剪动作**自身留痕**
    （末行 action_type == `action_logs_pruned`）。
  C 正对照（不越上界不动）：行数 <= keep_rows 时返回 0、表内行数不变。
  D 正对照（**刻意不自动裁剪**）：连续跑维护周期，`action_logs` 仍随周期 1:1 增长
    ——本件不把审计改成自动滚动（该取向属设计者裁面）。
  E 引擎侧入口可调用且与 store 级同语义（缺省保留窗口取自 `ACTION_LOGS_KEEP_ROWS`）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue723_action_logs_prune_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _count(e):
    return e.store.conn.execute("SELECT COUNT(*) FROM action_logs").fetchone()[0]


def _seed(e, n):
    for i in range(n):
        e.store.log_action("probe_action_%d" % i, "审计行 %d" % i, outcome={"i": i})


def test_a_prune_primitive_effective():
    """A：显式回收面生效——返回删除条数、表内剩 K 行。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        assert hasattr(e.store, "prune_action_logs"), (
            "#723 store 级回收原语缺失：action_logs 仍无清理面")
        _seed(e, 50)
        assert _count(e) == 50, "A 前置：播种 50 行，实测 %d" % (_count(e),)
        removed = e.store.prune_action_logs(keep_rows=10)
        assert removed == 40, "#723 返回删除条数不符：%r（期望 40）" % (removed,)
        assert _count(e) == 11, (
            "#723 保留窗口不符：%d 行（期望 10 行 + 1 行裁剪留痕）" % (_count(e),))
    finally:
        e.close()


def test_b_keeps_recent_rows_and_audits_itself():
    """B：保留最近的行（插入序）＋ 裁剪动作自身留痕。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        _seed(e, 30)
        e.store.prune_action_logs(keep_rows=5)
        rows = e.store.conn.execute(
            "SELECT action_type FROM action_logs ORDER BY id DESC").fetchall()
        kinds = [r[0] for r in rows]
        assert kinds and kinds[0] == "action_logs_pruned", (
            "#723 裁剪未留痕（末行应为 action_logs_pruned）：%r" % (kinds[:3],))
        assert "probe_action_29" in kinds, (
            "#723 裁剪把最近的行删了（保留序不是插入序）：%r" % (kinds,))
        assert "probe_action_0" not in kinds, (
            "#723 最旧的行仍在表内（未真正回收）：%r" % (kinds,))
    finally:
        e.close()


def test_c_positive_control_no_prune_below_bound():
    """C（正对照）：行数未越上界 ⇒ 返回 0、不动任何行。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        _seed(e, 5)
        removed = e.store.prune_action_logs(keep_rows=1000)
        assert removed == 0, "#723 未越上界却报删除：%r" % (removed,)
        assert _count(e) == 5, "#723 未越上界却动了行：%d" % (_count(e),)
    finally:
        e.close()


def test_d_positive_control_maintenance_does_not_auto_prune():
    """D（正对照 · 刻意不改）：维护周期不自动裁剪（取向属设计者裁面）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.store.ACTION_LOGS_KEEP_ROWS = 5
        before = _count(e)
        for _ in range(12):
            e.run_maintenance_cycle()
        after = _count(e)
        assert after > before, "D 前置：维护周期应写审计行"
        assert after >= 12, (
            "#723 维护周期被改成自动裁剪（本件刻意不做——审计只追加契约与保留窗口"
            "取值属设计者裁面）：%d 行" % (after,))
        assert e.store.conn.execute(
            "SELECT COUNT(*) FROM action_logs WHERE action_type='action_logs_pruned'"
        ).fetchone()[0] == 0, (
            "#723 维护周期内出现自动裁剪留痕——本件刻意不自动调用 prune_action_logs")
    finally:
        e.close()


def test_e_engine_level_entry_same_semantics():
    """E：引擎侧同名入口可调用，缺省窗口取 `ACTION_LOGS_KEEP_ROWS`。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        assert hasattr(e, "prune_action_logs"), "#723 引擎侧入口缺失"
        e.ACTION_LOGS_KEEP_ROWS = 4
        _seed(e, 9)
        removed = e.prune_action_logs()
        assert removed == 5, "#723 引擎侧缺省窗口不符：removed=%r（期望 5）" % (removed,)
        assert _count(e) == 5, (
            "#723 引擎侧缺省窗口不符：%d 行（期望 4 行 + 1 行留痕）" % (_count(e),))
    finally:
        e.close()
