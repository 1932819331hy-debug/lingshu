# -*- coding: utf-8 -*-
"""test_issue601_channel_cred_load_guard · _load 逐通道防护守卫（#601）
================================================================================
缺陷（#601，成立）：``ChannelCredibilityRegistry._load`` 把逐通道的
``float()`` 转换与整个循环包进**同一个** ``try/except Exception: pass``——
某个通道的 ``a`` 是 ``"oops"`` / ``null`` / 嵌套 dict 时 ``float()`` 抛
``ValueError``/``TypeError``，循环**当场中断**：

  · 坏值**之前**的通道已落状态；
  · 坏值**之后**的通道（含完全健康的）被**静默弃养**（保持构造器先验）；
  · 随后任一次 ``_update`` 触发的全量 ``save()`` 把残缺状态固化回盘
    ⇒ 后续健康通道的历史**永久丢失**。

这与同函数 ``_sanitize`` 自己写明的纪律——「单个通道有毒就**只丢该通道**
（回落先验），不牵连其他通道」——相悖：语义毒（NaN/非有限）有防护，
**不可解析毒**反而能把整轮载入打断。

改前读数（本机，Windows / py3.12，main @ 375cf55）：persist 文件三通道
``visual``（健康，a=8.0）/``tactile``（毒，a="oops"）/``audio``（健康，
a=6.0），``_load`` 后 ``audio.a=2.0``（先验）——被连坐。

修法：文件级 try 只兜 json 解析；逐通道 try 只丢毒通道。

守卫判据（注入式断言，回退修复即红）：
  G1  毒通道**之后**的健康通道必须载入自己的持久化值（不连坐）；
  G2  毒通道**之前**的健康通道照常载入（既有行为零回归）；
  G3  毒通道自身保持先验（只丢自己，与 _sanitize 纪律同口径）。

判据来源：issue #601 取证 + 本模块 ``_sanitize`` docstring 自述纪律
（「单通道有毒只丢该通道」）；理论章节不涉及持久化损坏面。
运行：pytest tests/test_issue601_channel_cred_load_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.world.channel_credibility import (  # noqa: E402
    PRIOR_A,
    ChannelCredibilityRegistry,
)


def _make_persist():
    """persist 文件：健康(前) / 毒 / 健康(后) 三通道。"""
    path = os.path.join(tempfile.mkdtemp(prefix="issue601_"), "cred.json")
    data = {
        "visual": {"a": 8.0, "b": 2.0, "hits": 10.0, "misses": 1.0},
        "tactile": {"a": "oops", "b": 2.0, "hits": 5.0, "misses": 0.0},
        "audio": {"a": 6.0, "b": 3.0, "hits": 7.0, "misses": 2.0},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def test_healthy_channel_after_poison_still_loads():
    """G1：毒通道之后的健康通道必须载入持久化值（#601：不得连坐弃养）。"""
    reg = ChannelCredibilityRegistry(persist_path=_make_persist())
    st = reg.channel_state("audio")
    assert st["a"] == 6.0 and st["hits"] == 7.0, (
        "健康通道 audio 被前置毒通道（tactile.a=\"oops\"）连坐弃养（#601）："
        "a=%r hits=%r——_load 须逐通道防护，单通道不可解析只丢该通道。"
        % (st["a"], st["hits"]))


def test_healthy_channel_before_poison_still_loads():
    """G2：毒通道之前的健康通道照常载入（既有行为零回归）。"""
    reg = ChannelCredibilityRegistry(persist_path=_make_persist())
    st = reg.channel_state("visual")
    assert st["a"] == 8.0 and st["misses"] == 1.0, st


def test_poisoned_channel_keeps_prior_only_for_itself():
    """G3：毒通道自身保持先验（只丢自己，与 _sanitize 纪律同口径）。"""
    reg = ChannelCredibilityRegistry(persist_path=_make_persist())
    st = reg.channel_state("tactile")
    assert st["a"] == PRIOR_A, (
        "毒通道 tactile 须回落先验 %.1f，实得 %r" % (PRIOR_A, st["a"]))


def test_corrupt_json_file_still_falls_back_wholesale():
    """G4（防过修）：文件级损坏（非 JSON）仍整体保持先验，不抛异常。"""
    path = os.path.join(tempfile.mkdtemp(prefix="issue601c_"), "cred.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not-json")
    reg = ChannelCredibilityRegistry(persist_path=path)   # 不得抛
    assert reg.channel_state("visual")["a"] == PRIOR_A


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
