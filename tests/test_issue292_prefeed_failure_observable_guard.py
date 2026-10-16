#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_issue292_prefeed_failure_observable_guard · #292 追加变体守卫。

判据来源（**仓内自订纪律，非外来口味**）
----------------------------------------
1. issue `#292` 正文（已修：`prefeed` 缺 `skip_dedup` ⇒ 新正文被 M5 去重并入旧
   节点却回报 `prefeed_boost`）之外，复核者 `123WP-a` 在 2026-10-15 追加了**同族
   变体**（同族、不同触发路径）：
     · 病灶 A —— `prefeed` 的宽 `except: pass` 之后**无条件返回成功语义**：
       `engine.add_perception` 抛异常 ⇒ `node_id` 停在 `None`，返回值却仍是
       `novel=True / action='prefeed_boost'` ⇒ **0 个节点写入却回报成功**。
     · 病灶 B —— `write_snapshot` 的 `except` 分支与成功路径**同值**：
       `node_id = existing_id` 在 try 尾与 except 里各写一遍 ⇒ 层级/重要度提升的
       `UPDATE` 失败被吞，**异常在返回值上不可观测**。
2. 同文件 `longterm_gate.py` 的自订纪律（`prefeed` 建边前那段注释）：
   「去重命中时 `add_perception` 返回的是**既有**节点：回报实际落库值，不要回报
   一个并未生效的计算值（"回报 ≠ 事实"）」——该纪律已在前一处（`skip_dedup`）
   生效，本组钉住后一处漏改的形态。
3. `#170` 静默吞错口径：宽 `except` 必须**留痕或收窄**——降级面（建边 /
   `protect_node`）失败须在返回值上可观测（`degraded` 列表），不得再 `except: pass`。

判据（每条断言都钉住「缺陷形态不再存在」，抽掉修复即红）
--------------------------------------------------------
  ① `prefeed` 主写入抛 ⇒ `action != 'prefeed_boost'`、`novel is False`、
     `node_id is None`（改前：三者分别为 `prefeed_boost` / `True` / `None`）。
  ② `prefeed` 正常 ⇒ `action == 'prefeed_boost'` 且 `degraded == []`（无降级）。
  ③ 降级失败（建边抛）⇒ 节点仍写入（行数 +1、`node_id` 非空），但 `degraded`
     非空 ⇒ **可观测**（改前无该键，失败不可见）。
  ④ `write_snapshot` 的「已存在」腿 `UPDATE` 抛 ⇒ 返回体带 `write_failed=True`
     （键在且取值与成功路径不同）；成功路径 `write_failed is False`。

边界（本件不改）：`add_perception` / `protect_node` / `add_edge` 的异常语义、
阈值（`NOVEL_TRIGGER` / `LONG_TERM_THRESHOLD` / `KNOWLEDGE_THRESHOLD`）均未动；
既有键（`status` / `layer` / `importance` / `node_id` / `links` …）语义与取值
形态一字未改，只**新增**结果键。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue292_prefeed_failure_observable_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _eng():
    return SpacetimeMemoryEngine(":memory:")


def _rows(engine):
    return engine.store.conn.execute("SELECT count(*) FROM nodes").fetchone()[0]


def _seed(engine):
    """铺参照集：`_novelty` 在空库上返回中性 0.5（< NOVEL_TRIGGER）不触发前馈。"""
    for t in ("水在标准大气压下100度沸腾。", "地球绕太阳公转一周约一年。",
              "项目上线日期定在十一月三日。"):
        engine.add_perception(t, importance=0.5)


NOVEL_TEXT = "探针内容：蜂窝卷积核的六邻等距性决定旋转等变的上限"


class _ConnProxy:
    """包装 sqlite3 连接：命中特征串的语句必抛，其余全量委托（含 commit）。"""

    def __init__(self, real, needle):
        self._real = real
        self._needle = needle

    def execute(self, sql, *a, **kw):
        if self._needle in sql:
            raise RuntimeError("test_issue292: injected UPDATE failure")
        return self._real.execute(sql, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


# ---------------- 病灶 A ----------------

def test_1_prefeed_main_write_failure_is_fail_closed(monkeypatch):
    """① 主写入抛 ⇒ 不得报 `prefeed_boost` / `novel=True`；`node_id is None`。

    改前读数：`action='prefeed_boost'`、`novel=True`、`node_id=None`、写入行数
    0 —— 0 个节点写入却回报成功（"回报 ≠ 事实"）。
    """
    e = _eng()
    try:
        gate = e._ensure_gate()
        _seed(e)
        before = _rows(e)

        def _boom(*a, **kw):
            raise RuntimeError("test_issue292: injected add_perception failure")
        monkeypatch.setattr(e, "add_perception", _boom)

        r = gate.prefeed(NOVEL_TEXT)
        assert r.get("action") != "prefeed_boost", (
            "主写入失败仍回报成功语义：%r" % (r,))
        assert r.get("action") == "prefeed_failed", r
        assert r.get("novel") is False, "主写入失败仍报 novel=True：%r" % (r,)
        assert r.get("node_id") is None, r
        assert r.get("error") == "RuntimeError", r
        assert _rows(e) == before, "主写入失败却写了节点：%d→%d" % (
            before, _rows(e))
    finally:
        e.close()


def test_2_prefeed_success_path_reports_no_degradation(monkeypatch):
    """② 正对照：正常路径 `action='prefeed_boost'`、`degraded == []`、行数 +1。"""
    e = _eng()
    try:
        gate = e._ensure_gate()
        _seed(e)
        before = _rows(e)
        r = gate.prefeed(NOVEL_TEXT)
        assert r.get("action") == "prefeed_boost", r
        assert r.get("novel") is True, r
        assert r.get("degraded") == [], (
            "无降级时 degraded 必须为空列表：%r" % (r,))
        assert r.get("node_id"), r
        assert _rows(e) == before + 1, "正常路径未写入节点：%d→%d" % (
            before, _rows(e))
    finally:
        e.close()


def test_3_prefeed_degraded_edge_failure_is_observable(monkeypatch):
    """③ 降级失败（建边抛）⇒ 节点仍写入（算成功），但失败面在返回值上可观测。

    改前：宽 `except: pass` 吞掉建边异常，返回值里**无任何**失败痕迹。
    """
    e = _eng()
    try:
        gate = e._ensure_gate()
        _seed(e)
        ref = types.SimpleNamespace(id="node_ref_issue292")
        # 使建边分支真被触及（门槛 NOVEL_EDGE_SIM 一字未改，桩掉检索结果即可）。
        monkeypatch.setattr(e.store, "search_content", lambda *a, **kw: [(ref, 0.9)])

        def _boom(*a, **kw):
            raise RuntimeError("test_issue292: injected add_edge failure")
        monkeypatch.setattr(e, "add_edge", _boom)

        before = _rows(e)
        r = gate.prefeed(NOVEL_TEXT)
        after = _rows(e)
        assert r.get("action") == "prefeed_boost", (
            "降级失败不应降级为失败语义（节点已写入）：%r" % (r,))
        assert r.get("node_id"), r
        assert after == before + 1, "降级路径节点未写入：%d→%d" % (before, after)
        assert r.get("degraded"), "降级失败在返回值上不可观测：%r" % (r,)
        assert "links" in r["degraded"], r
    finally:
        e.close()


# ---------------- 病灶 B ----------------

def test_4_write_snapshot_update_failure_is_observable(monkeypatch):
    """④ `write_snapshot` 的「已存在」腿 `UPDATE` 抛 ⇒ 失败在返回值上可观测。

    改前：`except` 分支与成功路径同值（`node_id = existing_id` 各写一遍），
    返回值键集与取值与成功路径**逐位相同** ⇒ 异常不可观测。
    """
    e = _eng()
    try:
        content = "test_issue292：项目上线日期定在十一月三日"
        r1 = e.longterm_snapshot(content, importance_hint=0.5)
        nid = r1.get("node_id")
        assert nid, r1

        real_conn = e.store.conn
        proxy = _ConnProxy(real_conn, "UPDATE nodes SET importance")
        monkeypatch.setattr(type(e.store), "conn", property(lambda self: proxy))

        r = e.longterm_snapshot(content, importance_hint=0.5)
        assert r.get("node_id") == nid, "既有节点识别被误伤：%r" % (r,)
        assert "write_failed" in r, (
            "UPDATE 失败在返回值上不可观测（无 write_failed 键）：%r" % (r,))
        assert r["write_failed"] is True, r
        assert r.get("write_error") == "RuntimeError", r
    finally:
        e.close()


def test_5_write_snapshot_success_path_write_failed_false():
    """⑤ 正对照：正常路径 `write_failed is False` / `write_error is None`。

    「加键」不得改动既有键的语义与取值形态。
    """
    e = _eng()
    try:
        r = e.longterm_snapshot("test_issue292 正对照：地球绕太阳公转一周约一年",
                                importance_hint=0.5)
        assert r.get("write_failed") is False, r
        assert r.get("write_error") is None, r
        assert r.get("node_id"), r
        assert r.get("layer") in ("knowledge", "long_term", "context"), r
    finally:
        e.close()
