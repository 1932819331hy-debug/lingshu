# -*- coding: utf-8 -*-
"""#691 Z3 `configure_action` 不可哈希值穿透门面 —— 守卫（lingshu/core/core.py）

缺陷（issue #691 · [core/世界门面]）：
  `seven_layer_loop("configure_action", …)` 只捕 `ValueError`；不可哈希的
  `fixed_action`（如 `[1]`）在 C 侧 `validate_action` 的 `action not in
  ACTION_DELTAS` 处抛 `TypeError: unhashable type` ⇒ **穿透门面**（模型驱动面
  拿到裸异常而非可读 error dict）。

修法：门面把 `TypeError` 一并纳入捕获，返回同一 fail-closed 形
（`status=error` / `reason=action_config_rejected`，配置不变）。

断言组（回退只捕 `ValueError` ⇒ A 红）：
  A 不可哈希 `fixed_action=[1]` ⇒ 返回 error dict（不抛）
  B 防误杀：合法 `fixed_action="east"` 仍走正常路径（同样返回 error dict，
    原因是「实体不在场」而非异常穿透——证明本件只加捕获、未改语义）

运行（仓根）：python -X utf8 -m pytest tests/test_issue691_z3_action_facade_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _facade():
    e = SpacetimeMemoryEngine(":memory:")
    fn = e.seven_layer_loop
    fn("init", {"size": 12, "seed": 1})
    return fn


def test_a_unhashable_fixed_action_no_leak():
    """A：不可哈希 fixed_action 不得让 TypeError 穿透门面。"""
    fn = _facade()
    r = fn("configure_action", {"entity_id": "ghost", "target": [1, 2, 3],
                                "fixed_action": [1]})
    assert isinstance(r, dict) and r.get("status") == "error", \
        "#691 Z3：不可哈希值穿透门面（未返回 error dict）：%r" % (r,)
    assert r.get("reason") == "action_config_rejected", \
        "#691 Z3：返回形与既有 fail-closed 不一致：%r" % (r,)


def test_b_valid_action_still_failclosed_dict():
    """B：防误杀——合法动作值仍返回同形 error dict（实体不在场），不抛。"""
    fn = _facade()
    r = fn("configure_action", {"entity_id": "ghost", "target": [1, 2, 3],
                                "fixed_action": "east"})
    assert isinstance(r, dict) and r.get("status") == "error", \
        "#691 Z3：正常路径被改：%r" % (r,)
