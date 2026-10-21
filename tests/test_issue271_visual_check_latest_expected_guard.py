# -*- coding: utf-8 -*-
"""#271 visual_check 的「预期」必须取**最近**一张屏幕状态 —— 守卫

缺陷（lingshu issue #271，判定成立）：`visual_check` docstring 的语义是「预期来自记忆
中**最近**的屏幕状态（screen_state 节点），即『过去 predicts 现在』；对照后把当前截图
写回记忆，形成持续更新的『过去』」。旧实现：

    nodes = self.store.get_nodes_by_tag("screen_state", limit=3)   # importance/last_access 降序
    for n in reversed(nodes):                                       # ← 从第 3 名（最旧）往前找

`get_nodes_by_tag` 已按降序返回，`reversed()` 先看**三条里最旧**的一条 ⇒ 预期固定落后
（实测 7 次 `visual_check` 的 expected 依次为 0001/0002/0002/0001/0004/0003，从未是当时
最新那张）；画面变过一次后每次对照都报「有变化」、`consistent` 永远 False。

修法（issue 补充帖的「更稳的修法」）：在 `visual_check` 内单独按
`created_at DESC, rowid DESC` 取最近 3 条 screen_state（不动共享的 `get_nodes_by_tag`
排序，那会连带影响 vprim / cycle_record / load_world_from_memory）。

断言组（把 `reversed(...)`/`get_nodes_by_tag` 换回即红）：
  J1 预期随对照前进：第 i 轮（i≥1）的 expected 必须是第 i-1 轮 capture 的那张。
  J2 不再停在基线：第 ≥3 轮的 expected 不得再是 baseline（旧缺陷：永远 baseline）。
  J3 最新优先（文件缺失回退方向）：最新那张被删时，回退取**次新**而不是最旧。

运行（仓根）：python -X utf8 -m pytest tests/test_issue271_visual_check_latest_expected_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


class _R:
    """与 body 组件 `invoke()` 返回值同形：ok / data / error / to_dict()。"""

    def __init__(self, data):
        self.ok, self.data, self.error = True, data, None

    def to_dict(self):
        return {"ok": True, "data": self.data}


class _FakeScreen:
    """最小假屏幕：capture 每次产出一张新图；diff 报告与 reference 是否一致。"""

    def __init__(self, shots_dir):
        self.shots_dir = shots_dir
        self.i = 0
        self.paths = []

    def invoke(self, name, action, params):
        assert name == "screen", name
        if action == "capture":
            self.i += 1
            p = os.path.join(self.shots_dir, "shot%04d.png" % self.i)
            with open(p, "wb") as fh:
                fh.write(b"x")
            self.paths.append(p)
            return _R({"path": p})
        if action == "diff":
            ref = params["reference"]
            return _R({"changed": ref != self.paths[-1], "change_ratio": 0.5})
        raise AssertionError(action)


def _engine(tmp_path):
    e = SpacetimeMemoryEngine(":memory:")
    reg = _FakeScreen(str(tmp_path))
    e._body_registry = reg
    return e, reg


def test_j1_expected_advances_with_each_check(tmp_path):
    """J1：第 i 轮（i≥1）预期＝第 i-1 轮 capture 的那张（最近的那张）。"""
    e, reg = _engine(tmp_path)
    for i in range(7):
        r = e.visual_check()
        if i == 0:
            assert r.get("established") is True, r
            continue
        assert r.get("expected") == reg.paths[i - 1], (
            "第 %d 轮 expected=%s，应为上一轮 capture 的 %s（#271：预期未取最近一张）"
            % (i, r.get("expected"), reg.paths[i - 1]))


def test_j2_expected_does_not_stick_to_baseline(tmp_path):
    """J2：多轮之后预期不得再停在 baseline（旧缺陷：永远第一张）。"""
    e, reg = _engine(tmp_path)
    r = e.visual_check()
    baseline = r["baseline"]
    for _ in range(5):
        last = e.visual_check()
    assert last.get("expected") != baseline, (
        "6 轮之后 expected 仍是 baseline=%s（#271：预期停在开机第一张）" % baseline)


def test_j3_fallback_prefers_newer_when_latest_missing(tmp_path):
    """J3：最新那张文件被删 ⇒ 回退到**次新**，不是最旧。"""
    e, reg = _engine(tmp_path)
    e.visual_check()          # baseline = paths[0]
    e.visual_check()          # expected=paths[0]，capture paths[1]
    e.visual_check()          # expected=paths[1]，capture paths[2]
    e.visual_check()          # expected=paths[2]，capture paths[3]
    os.remove(reg.paths[3])   # 最新那张（第 3 轮 capture）被清理
    r = e.visual_check()
    assert r.get("expected") == reg.paths[2], (
        "最新张缺失时 expected=%s，应为次新的 %s（#271：回退方向反了，取到最旧）"
        % (r.get("expected"), reg.paths[2]))
