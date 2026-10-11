# -*- coding: utf-8 -*-
"""#222 盲区闭环的设计者密钥只加在引擎门面 —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #222，判定「成立」）：
  引擎 `SpacetimeMemoryEngine.resolve_blindspot` 写明「D-007 需设计者密钥」，无密钥
  抛 `PermissionError`；但闸只在门面上。同一引擎的公开属性 `e.store` 上有两个语义
  相同、**不验密钥**的方法：
    · `LayeredStore.resolve_blindspot(id)`——直接 `status='resolved'`；
    · `LayeredStore.update_blindspot_status(id, status)`——接受**任意字符串** status。
  学习方向驱动 `get_open_blindspots` 只看 `status='open'` ⇒ 盲区被改成任何其它状态
  都从学习队列消失。仓内其它模块普遍直接调 `engine.store.*`，`store` 并非私有接口。

修法（最小 · 按 issue「建议修复 1/2」）：
  ① `LayeredStore.resolve_blindspot` 加 `designer_key` 形参 + `verify_designer` 闸
     （与 `adjudicate_promotion` 同款 fail-closed）；引擎门面**透传**密钥（门面保留
     自身校验以先判后写）。
  ② `LayeredStore.update_blindspot_status` 对非 `open` 状态同样要求密钥，且 status
     走白名单 `BLINDSPOT_STATUSES = ("open", "resolved")`（越界即 `ValueError`）。

判据来源：公开 issue #222「建议修复 1（下沉 verify_designer）/ 2（白名单+密钥）」＋
  `D-007`（引擎 `resolve_blindspot` docstring 原文「D-007 需设计者密钥：D-001 语义
  判定权在维生系统」）。仓内理论章节未规定该落点口径，属经验标定（本件 #222）。
边界（削掉的判别力，如实标注）：①白名单只列仓内实际出现的 `open`/`resolved`；
  ②`status='open'`（重新打开）按 issue 原文不要求密钥（该方向只是把盲区放回学习
  队列，非抹除）。

断言组（抽掉修复——删 store 层 verify / 删白名单——Q1/Q3 必红）：
  Q1 无密钥调 `store.resolve_blindspot` ⇒ `PermissionError`，状态仍 `open`。
  Q2 无/错密钥调 `store.update_blindspot_status(id,'resolved')` ⇒ `PermissionError`，
     状态仍 `open`。
  Q3 任意字符串 status（`'ignored'`）⇒ `ValueError`（白名单），状态仍 `open`。
  Q4 正确密钥 ⇒ 两方法照旧生效（防误杀），盲区从 open 队列移出。
  Q5 门面未松动：无密钥 `engine.resolve_blindspot` 仍 `PermissionError`；有密钥生效。

运行（仓根）：python -X utf8 -m pytest tests/test_issue222_blindspot_gate_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_KEY = "guard-dummy-key-222"
_ENV = "AEIS_DESIGNER_KEY"


@pytest.fixture()
def engine(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    return SpacetimeMemoryEngine(":memory:")


def _status(e, bid):
    for b in e.list_blindspots():
        if b["id"] == bid:
            return b["status"]
    raise AssertionError("盲区 %s 不存在" % bid)


def test_q1_store_resolve_requires_key(engine):
    """Q1：无密钥 store.resolve_blindspot ⇒ PermissionError，状态不变。"""
    b = engine.register_blindspot("BS-1", "未知领域 A", "high")
    with pytest.raises(PermissionError):
        engine.store.resolve_blindspot(b)
    assert _status(engine, b) == "open", (
        "#222：无密钥经 store.resolve_blindspot 关闭了盲区（状态=%s）" % _status(engine, b))
    assert len(engine.get_open_blindspots()) == 1


def test_q2_store_update_status_requires_key(engine):
    """Q2：无/错密钥 store.update_blindspot_status(resolved) ⇒ PermissionError。"""
    b = engine.register_blindspot("BS-2", "未知领域 B", "high")
    with pytest.raises(PermissionError):
        engine.store.update_blindspot_status(b, "resolved")
    with pytest.raises(PermissionError):
        engine.store.update_blindspot_status(b, "resolved", designer_key="wrong")
    assert _status(engine, b) == "open", (
        "#222：无/错密钥改写了盲区状态（状态=%s）" % _status(engine, b))


def test_q3_status_whitelist(engine):
    """Q3：任意字符串 status ⇒ ValueError（白名单），状态不变。"""
    b = engine.register_blindspot("BS-3", "未知领域 C", "high")
    with pytest.raises(ValueError):
        engine.store.update_blindspot_status(b, "ignored", designer_key=_KEY)
    assert _status(engine, b) == "open", (
        "#222：越界 status 被写入（状态=%s）" % _status(engine, b))


def test_q4_correct_key_still_works(engine):
    """Q4：正确密钥 ⇒ 两方法照旧生效（防误杀）。"""
    b1 = engine.register_blindspot("BS-4", "未知领域 D", "high")
    b2 = engine.register_blindspot("BS-5", "未知领域 E", "high")
    engine.store.resolve_blindspot(b1, designer_key=_KEY)
    engine.store.update_blindspot_status(b2, "resolved", designer_key=_KEY)
    assert _status(engine, b1) == "resolved"
    assert _status(engine, b2) == "resolved"
    assert engine.get_open_blindspots() == [], "有密钥关闭后仍在 open 队列"


def test_q5_engine_facade_intact(engine):
    """Q5：门面未松动——无密钥拒、有密钥生效。"""
    b = engine.register_blindspot("BS-6", "未知领域 F", "high")
    with pytest.raises(PermissionError):
        engine.resolve_blindspot(b, True)
    assert _status(engine, b) == "open"
    engine.resolve_blindspot(b, True, designer_key=_KEY)
    assert _status(engine, b) == "resolved"
