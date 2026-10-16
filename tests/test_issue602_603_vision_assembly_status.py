# -*- coding: utf-8 -*-
"""守卫 · issue #602 ＋ #603 —— `_ensure_vision` 错误记账的状态机缺口（姊妹件，一条线同修）
============================================================================================
判据来源：公开 issue #602 / #603（作者 @Yinfeng818，基线 `55a137fc`），
实现侧口径＝本批派工书 §1「取向（本端裁断，照做）」1–4 条。

两件同函数（`lingshu/core/core.py` 的 `_ensure_vision`），互为姊妹：

  · **#602（观测面推断缺陷）**：`_ensure_vision` 的
    `except Exception as e: self._vision_error = str(e)` 对**无消息异常**
    （`raise ImportError()`，`str(e) == ""`）记空串；而 `_component_assembly_status`
    用「错误为空」**推断**「从未尝试」（`elif name == "vision" and not err: lazy`）
    ⇒ 「尝试过 + 失败 + 异常无消息」与「从未尝试」在状态面上读数**逐位相同**，
    前者被报成 `lazy`（装配失败在 self_check 上重新隐形）。
    ★其它 10 个组件走 `err or "未装配（无错误信息）"` 回落，无此问题——
    vision 是唯一的特判分支。
  · **#603（观测面陈旧状态）**：成功出口只置 `_vision_provider`、**不清
    `_vision_error`**；`get_body_capabilities` 在 provider 就位时仍
    `vision_note = self._vision_error or None` ⇒ 「失败 → 恢复」（惰性装配的
    **常规路径**：装好依赖不重启即恢复）后能力声明 `available=True` 却带着陈旧
    error，自相矛盾直到进程重启。

修法口径（判据）：
  1. 「是否尝试过」做成**显式状态** `self._vision_attempted: bool`（进入处置 True、
     `_setup_v113` 复位 False）；`_component_assembly_status` 据此判 `lazy` /
     `failed`——**不得**再用「err 非空」当「尝试过」的代理；
  2. 失败不留空串：`str(e) or repr(e) or type(e).__name__`；
  3. 成功出口清陈旧 error（置 `None`）；
  4. 签名与返回体形状不变。

本守卫全部走 **stub `vision` 模块 + `sys.modules` 注入**（不依赖真实 ultralytics，
也不依赖 `vision` 身体侧包是否在本机）；只读既有槽位与 `*_error` 字段，
不触碰运行路径。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue602_603_vision_assembly_status.py -q --no-header
"""
from __future__ import annotations

import sys
import types

import pytest

from lingshu.core.core import SpacetimeMemoryEngine


# ==================== 量具（stub vision 模块 + 状态面读数） ====================

class _StubProvider:
    """duck-typed vision provider：只需 available() / name（引擎侧只读这两者）。"""

    name = "stub-vision"

    def available(self) -> bool:
        return True


def _install_vision(monkeypatch, factory):
    """把 stub `vision` 模块注入 `sys.modules`（monkeypatch 自动还原）。

    factory: 无参可调用 ⇒ `create_vision_provider()` 的返回值/抛出的异常。
    """
    mod = types.ModuleType("vision")
    mod.create_vision_provider = factory
    mod.perceive_image = lambda *a, **k: {"status": "ok"}
    monkeypatch.setitem(sys.modules, "vision", mod)
    return mod


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _vision_bucket(engine):
    """返回 ('ok'|'lazy'|'failed', err_text)——只读 `_component_assembly_status()`。"""
    ca = engine._component_assembly_status()
    if "vision" in ca["ok"]:
        return "ok", ""
    if "vision" in ca["lazy"]:
        return "lazy", ""
    return "failed", ca["failed"].get("vision", "")


# 非 vision 的 10 个组件（#602 的判据只许落在 vision 上；用于 ⑧ 过修正对照）
_NON_VISION = tuple(
    name for name, _slot, _err in SpacetimeMemoryEngine._COMPONENT_SLOT_TABLE
    if name != "vision")


def test_00_gauge_inputs_nonempty():
    """量具自检：输入面非空（防 for 循环空转成假绿）。"""
    table = SpacetimeMemoryEngine._COMPONENT_SLOT_TABLE
    assert len(table) == 11, table
    assert len(_NON_VISION) == 10, _NON_VISION
    assert "vision" in {n for n, _s, _e in table}


# ==================== ① 从未尝试 ⇒ lazy 且 error 为空 ====================
def test_01_never_attempted_is_lazy(monkeypatch):
    e = _engine()
    assert e._vision_attempted is False
    bucket, err = _vision_bucket(e)
    assert bucket == "lazy", (bucket, err)
    assert err == "", err


# ==================== ② 尝试过 + 有消息异常 ⇒ failed 且含该消息 ====================
def test_02_attempted_with_message_is_failed(monkeypatch):
    def _boom():
        raise ImportError("No module named 'ultralytics'")

    _install_vision(monkeypatch, _boom)
    e = _engine()
    assert e._ensure_vision() is None
    bucket, err = _vision_bucket(e)
    assert bucket == "failed", (bucket, err)
    assert "No module named 'ultralytics'" in err, err


# ==================== ③ 尝试过 + 无消息异常 ⇒ failed（#602 核心断言） ====================
def test_03_attempted_bare_exception_is_failed_not_lazy(monkeypatch):
    def _boom_bare():
        raise ImportError()          # str(e) == "" —— 旧实现据此被报成 lazy

    _install_vision(monkeypatch, _boom_bare)
    e = _engine()
    assert e._ensure_vision() is None
    assert e._vision_error, "无消息异常也必须留下可读标识（旧实现留空串）"
    bucket, err = _vision_bucket(e)
    assert bucket == "failed", (
        "无消息异常的装配失败被报成 %r —— #602 复发（应与「从未尝试」可区分）"
        % (bucket,))
    assert err.strip() != "", "failed 项的错误文本不得为空串"


def test_03b_bare_failure_differs_from_never_attempted(monkeypatch):
    """③ 的判别力锚定：旧实现下 A（从未尝试）与 C（尝试过+无消息异常）读数逐位相同。"""
    def _boom_bare():
        raise ImportError()

    _install_vision(monkeypatch, _boom_bare)
    e_fail = _engine()
    e_fail._ensure_vision()
    read_fail = _vision_bucket(e_fail)
    read_untried = _vision_bucket(_engine())
    assert read_fail != read_untried, (
        "「尝试过+失败」与「从未尝试」在状态面上不可区分：%r" % (read_fail,))


def test_03c_state_surface_keys_on_attempted_not_on_error_text():
    """③ 的判据直钉：状态面必须按**显式** attempted 判 lazy，不得以「err 为空」反推。

    构造「已尝试 + 错误文本为空」的内部状态（未来/其它失败出口可能如此）——
    旧判据 `not err` 会读作 `lazy`，显式状态判据读作 `failed`。
    """
    e = _engine()
    e._vision_attempted = True
    e._vision_error = ""
    bucket, err = _vision_bucket(e)
    assert bucket == "failed", (
        "已尝试但错误文本为空时被报成 %r —— 状态面仍以「err 为空」反推「未尝试」"
        % (bucket,))
    assert err.strip() != "", "failed 项须回落非空文案（与其它 10 组件同口径）"


# ==================== ④ create_vision_provider() 返回 None ⇒ failed ====================
def test_04_provider_none_is_failed(monkeypatch):
    _install_vision(monkeypatch, lambda: None)      # "ultralytics 未安装" 出口
    e = _engine()
    assert e._ensure_vision() is None
    bucket, err = _vision_bucket(e)
    assert bucket == "failed", (bucket, err)
    assert err.strip() != "", err


# ==================== ⑤ 成功 ⇒ available=True 且 vision_note is None（#603 核心断言） ====================
def test_05_success_clears_error_and_capability_note_is_none(monkeypatch):
    _install_vision(monkeypatch, lambda: _StubProvider())
    e = _engine()
    assert e._ensure_vision() is not None
    assert e._vision_error is None, (
        "成功出口未清 _vision_error：%r（#603 复发）" % (e._vision_error,))
    caps = e.get_body_capabilities()["vision"]
    assert caps["available"] is True, caps
    assert caps["error"] is None, (
        "available=True 却带着 error=%r —— 声明面自相矛盾（#603）" % (caps["error"],))


# ==================== ⑥ 失败 → 恢复：provider 就位且 error 已清 ====================
def test_06_fail_then_recover(monkeypatch):
    def _boom():
        raise ImportError("No module named 'ultralytics'")

    _install_vision(monkeypatch, _boom)
    e = _engine()
    assert e._ensure_vision() is None
    assert e._vision_error          # 第一次失败有记录

    _install_vision(monkeypatch, lambda: _StubProvider())   # 「依赖装好了」
    p = e._ensure_vision()
    assert p is not None and e._vision_provider is p
    assert e._vision_error is None, "恢复后陈旧错误未清：%r" % (e._vision_error,)
    caps = e.get_body_capabilities()["vision"]
    assert caps["available"] is True and caps["error"] is None, caps


# ==================== ⑦ 正对照：成功态 vision 在 ok，不在 lazy/failed ====================
def test_07_positive_control_success_bucket(monkeypatch):
    _install_vision(monkeypatch, lambda: _StubProvider())
    e = _engine()
    e._ensure_vision()
    ca = e._component_assembly_status()
    assert "vision" in ca["ok"], ca
    assert "vision" not in ca["lazy"], ca
    assert "vision" not in ca["failed"], ca


# ==================== ⑧ 正对照：其它 10 个组件的分类逐字不变（防过修） ====================
def test_08_positive_control_other_components_untouched():
    """#602 的判据只许落在 vision 上：把「尝试过」判据泛化到其它组件 ⇒ 本组必红。"""
    e = _engine()
    ca = e._component_assembly_status()
    seen = []
    for name, slot, _err in SpacetimeMemoryEngine._COMPONENT_SLOT_TABLE:
        if name == "vision":
            continue
        expected = "ok" if getattr(e, slot, None) is not None else "failed"
        if expected == "ok":
            assert name in ca["ok"], (name, ca)
        else:
            assert name in ca["failed"], (name, ca)
        assert name not in ca["lazy"], (
            "非 vision 组件 %r 被报成 lazy —— 过修（「尝试过」判据泛化）" % (name,))
        seen.append(name)
    assert sorted(seen) == sorted(_NON_VISION) and len(seen) == 10, seen


# ==================== ⑨ 结构性：显式 attempted 状态存在且复位 ====================
def test_09_attempted_flag_is_explicit_state(monkeypatch):
    """#602 的修法前提：状态面不得再以「err 为空」当「尝试过」的代理。"""
    e = _engine()
    assert hasattr(e, "_vision_attempted") and e._vision_attempted is False

    _install_vision(monkeypatch, lambda: None)
    e._ensure_vision()
    assert e._vision_attempted is True, "进入尝试后未置位 attempted"

    e2 = _engine()
    e2._setup_v113()
    assert e2._vision_attempted is False, "复位面缺失"


if __name__ == "__main__":       # 便于单文件直跑（pytest 面不受影响）
    sys.exit(pytest.main([__file__, "-q", "--no-header"]))
