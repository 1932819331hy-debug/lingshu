# -*- coding: utf-8 -*-
"""#243 world 门面 `init` 缺分支 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #243 · [core/world 门面]）：
  `spacetime_consistency` / `world_model` / `world_learner` / `curiosity_explorer`
  / `seven_layer_loop` 五个门面的 docstring 与兜底错误信息都列 `init`，函数体内
  **没有 `if action == "init"` 分支**，实现是
  `if not hasattr(self, "_xxx"): self._xxx = <构造类>(size=p.get("size", 24), ...)`。
  ⇒ ① 首次调 `init`：对象被顺带按参数构造，接口却返回 `status=error`
  （落到「未知动作」兜底）；② 此前调过任何动作：`init` 的参数**完全不生效**，
  世界仍是默认 24。

修法：五个门面各在 `hasattr` 惰性构造**之前**加
  `if action == "init": self._xxx = <构造类>(size=int(p.get("size", 24)), ...);`
  `return {"status": "ok", **<state>}`
惰性构造只用类默认值（五个构造类的默认值与门面默认逐位相同，故非 init 路径
行为不变）。

判据来源：公开 issue #243（docstring/错误信息列 init 但无分支）。阈值（默认
size=24）**一律未改**，直接沿用门面原有默认。

断言组（抽掉任一门面的 `init` 分支 ⇒ 该门面 A/B 红）：
  A `init` 返回 `status=ok`（旧缺陷＝`status=error`）
  B `init` 的参数**实际生效**（size 落到被构造对象上）
  C 先跑一个非 init 动作，再 `init` 改 size ⇒ **重构造生效**（旧缺陷＝参数不生效）

运行（仓根）：python -X utf8 -m pytest tests/test_issue243_world_facade_init_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _size_stc(e):
    return e._stc.scene.world.size


def _size_wm(e):
    return e._wmodel.size


def _size_wl(e):
    return e._wlearner.size


def _size_cx(e):
    return e._curious.size


def _size_sll(e):
    return e._sll.world.world.size


def test_a_stc_init_ok_and_params_take_effect():
    e = _engine()
    r = e.spacetime_consistency("init", {"size": 40, "window": 12})
    assert r["status"] == "ok", "A stc init 返回 error（旧缺陷）：%r" % (r,)
    assert _size_stc(e) == 40, "A stc init 参数未生效：size=%s" % _size_stc(e)
    assert e._stc.window == 12, "A stc init window 未生效：%s" % e._stc.window


def test_b_stc_init_after_action_reconstructs():
    e = _engine()
    e.spacetime_consistency("init", {"size": 40})
    e.spacetime_consistency("create", {"trees": 0, "water": False})
    r = e.spacetime_consistency("init", {"size": 30})
    assert r["status"] == "ok", "B stc 二次 init 返回 error：%r" % (r,)
    assert _size_stc(e) == 30, (
        "B stc 调过其它动作后 init 参数不生效（旧缺陷）：size=%s" % _size_stc(e))


def test_c_world_model_init_ok_and_params_take_effect():
    e = _engine()
    r = e.world_model("init", {"size": 40})
    assert r["status"] == "ok", "C wm init 返回 error（旧缺陷）：%r" % (r,)
    assert _size_wm(e) == 40, "C wm init 参数未生效：size=%s" % _size_wm(e)
    e.world_model("create", {"trees": 0})
    r2 = e.world_model("init", {"size": 30})
    assert r2["status"] == "ok", "C wm 二次 init 返回 error：%r" % (r2,)
    assert _size_wm(e) == 30, (
        "C wm 调过其它动作后 init 参数不生效（旧缺陷）：size=%s" % _size_wm(e))


def test_d_world_learner_init_ok_and_params_take_effect():
    e = _engine()
    r = e.world_learner("init", {"size": 40, "window": 9})
    assert r["status"] == "ok", "D wl init 返回 error（旧缺陷）：%r" % (r,)
    assert _size_wl(e) == 40, "D wl init 参数未生效：size=%s" % _size_wl(e)
    assert e._wlearner.window == 9, "D wl init window 未生效：%s" % e._wlearner.window
    e.world_learner("create", {"trees": 0})
    r2 = e.world_learner("init", {"size": 30})
    assert r2["status"] == "ok", "D wl 二次 init 返回 error：%r" % (r2,)
    assert _size_wl(e) == 30, (
        "D wl 调过其它动作后 init 参数不生效（旧缺陷）：size=%s" % _size_wl(e))


def test_e_curiosity_explorer_init_ok_and_params_take_effect():
    e = _engine()
    r = e.curiosity_explorer("init", {"size": 40, "window": 9})
    assert r["status"] == "ok", "E cx init 返回 error（旧缺陷）：%r" % (r,)
    assert _size_cx(e) == 40, "E cx init 参数未生效：size=%s" % _size_cx(e)
    assert e._curious.window == 9, "E cx init window 未生效：%s" % e._curious.window
    e.curiosity_explorer("create", {"trees": 0})
    r2 = e.curiosity_explorer("init", {"size": 30})
    assert r2["status"] == "ok", "E cx 二次 init 返回 error：%r" % (r2,)
    assert _size_cx(e) == 30, (
        "E cx 调过其它动作后 init 参数不生效（旧缺陷）：size=%s" % _size_cx(e))


def test_f_seven_layer_loop_init_ok_and_params_take_effect():
    e = _engine()
    r = e.seven_layer_loop("init", {"size": 40, "budget": 5, "policy": "random"})
    assert r["status"] == "ok", "F sll init 返回 error（旧缺陷）：%r" % (r,)
    assert _size_sll(e) == 40, "F sll init 参数未生效：size=%s" % _size_sll(e)
    assert e._sll.budget == 5, "F sll init budget 未生效：%s" % e._sll.budget
    assert e._sll.policy == "random", "F sll init policy 未生效：%s" % e._sll.policy
    e.seven_layer_loop("create", {"trees": 0})
    r2 = e.seven_layer_loop("init", {"size": 30})
    assert r2["status"] == "ok", "F sll 二次 init 返回 error：%r" % (r2,)
    assert _size_sll(e) == 30, (
        "F sll 调过其它动作后 init 参数不生效（旧缺陷）：size=%s" % _size_sll(e))


def test_g_docstring_and_error_message_list_init_truthfully():
    """G：兜底错误信息仍列 init，且该动作现已真能执行（不再自相矛盾）。"""
    e = _engine()
    for name in ("spacetime_consistency", "world_model", "world_learner",
                 "curiosity_explorer", "seven_layer_loop"):
        err = getattr(e, name)("__no_such_action__", {})
        assert err["status"] == "error", "%s 未知动作未报 error" % name
        assert "init" in err["error"], "%s 兜底信息未列 init" % name
        ok = getattr(e, name)("init", {})
        assert ok["status"] == "ok", "%s init 未生效（旧缺陷）" % name
