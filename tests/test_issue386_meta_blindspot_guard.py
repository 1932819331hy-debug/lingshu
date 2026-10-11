# -*- coding: utf-8 -*-
"""#386 元盲区「零记录」可被大小写/空白变体绕过 —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #386，判定「成立」）：
  `SpacetimeMemoryEngine.register_blindspot` 的 docstring 写明元盲区（对人类造成
  文明级别的重大负面影响）判定权在维生系统、「API 强制 meta 零记录」，实现却只有
  `if category == "meta":` **字面比较** ⇒ `'Meta'` / `'META'` / `' meta'` /
  `'meta\\n'` 全部绕过，被强制改标为 operational 写进注册表并进入
  `get_open_blindspots` 学习队列；同一对象上公开的 `e.store.add_blindspot` 更是
  **完全不检查**，`category='meta'` 原样落库。

修法（最小 · 按 issue 期望原文）：
  ① 新增模块级 `_is_meta_category(category)`——`isinstance(str)` 且
     `strip().lower() == "meta"`；
  ② `register_blindspot` 的 `category == "meta"` 改为该规范化判据；
  ③ `LayeredStore.add_blindspot`（公开可达、语义相同）同加该闸，命中即 `ValueError`。

判据来源：公开 issue #386 期望原文「所有规范化后等于 meta 的 category
  （`strip().lower()=="meta"`）都应被拒绝；store 层也应有同样的检查（或改为私有
  方法），注册表里不出现任何元盲区记录」。仓内理论章节未规定该落点口径，属经验
  标定（本件 #386）。
边界（削掉的判别力）：只按空白/大小写规范化；**不**做同义词/翻译匹配
  （如 `'元'`）——issue 未要求，且会引入新的判据面。

断言组（抽掉修复——把 `_is_meta_category` 退回字面比较、删 store 层闸——Q1/Q2/Q4 必红）：
  Q1 `register_blindspot(category=<变体>)` 逐变体 `ValueError`，注册表 0 条。
  Q2 `store.add_blindspot(category=<变体>)` 逐变体 `ValueError`，注册表 0 条。
  Q3 防误杀：正常 category（operational / 'Operational' / 'unknown_domain'）照旧写入。
  Q4 全变体尝试后注册表中不存在任何规范化后为 meta 的行。

运行（仓根）：python -X utf8 -m pytest tests/test_issue386_meta_blindspot_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

#: issue 实测列出的绕过变体 + 两个同族变体（制表符/全大写加空白）。
_VARIANTS = ["meta", "Meta", "META", " meta", "meta\n", "\tMETA  ", "Meta\t"]
_DESC = "对人类造成文明级别的重大负面影响：XX 方案"


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _meta_rows(e):
    return [b for b in e.list_blindspots()
            if isinstance(b["category"], str) and b["category"].strip().lower() == "meta"]


@pytest.mark.parametrize("cat", _VARIANTS)
def test_q1_register_blindspot_rejects_variants(cat):
    """Q1：门面逐变体拒（ValueError），且注册表不落任何行。"""
    e = _engine()
    with pytest.raises(ValueError):
        e.register_blindspot("BS-META", _DESC, "high", category=cat)
    assert e.list_blindspots() == [], (
        "#386：变体 %r 经 register_blindspot 写进了注册表 %r" % (cat, e.list_blindspots()))


@pytest.mark.parametrize("cat", _VARIANTS)
def test_q2_store_add_blindspot_rejects_variants(cat):
    """Q2：store 层（公开可达、语义相同）逐变体拒（ValueError），注册表不落行。"""
    e = _engine()
    with pytest.raises(ValueError):
        e.store.add_blindspot("BS-META", _DESC, "high", category=cat)
    assert e.list_blindspots() == [], (
        "#386：变体 %r 经 store.add_blindspot 落库 %r" % (cat, e.list_blindspots()))


def test_q3_non_meta_categories_still_work():
    """Q3：防误杀——正常 category 照旧写入（门面/store 两路径）。"""
    e = _engine()
    b1 = e.register_blindspot("BS-OK-1", "未知领域 A", "high")
    b2 = e.store.add_blindspot("BS-OK-2", "未知领域 B", "high", category="unknown_domain")
    b3 = e.store.add_blindspot("BS-OK-3", "未知领域 C", "high", category="Operational")
    ids = {b["id"] for b in e.list_blindspots()}
    assert {b1, b2, b3} <= ids, "#386：正常 category 被误杀，注册表 %r" % (e.list_blindspots(),)
    assert _meta_rows(e) == []


def test_q4_no_meta_row_survives_any_variant():
    """Q4：全变体轮番尝试后，注册表中不存在规范化后为 meta 的行。"""
    e = _engine()
    for cat in _VARIANTS:
        for fn in (lambda c: e.register_blindspot("BS-META", _DESC, "high", category=c),
                   lambda c: e.store.add_blindspot("BS-META", _DESC, "high", category=c)):
            try:
                fn(cat)
            except ValueError:
                pass
    assert _meta_rows(e) == [], (
        "#386：注册表仍含元盲区行 %r" % ([(b["code"], b["category"]) for b in _meta_rows(e)],))
    assert e.list_blindspots() == []
