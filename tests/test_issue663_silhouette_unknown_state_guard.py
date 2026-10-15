# -*- coding: utf-8 -*-
"""test_issue663_silhouette_unknown_state_guard · #663 守卫
============================================================
缺陷（#663）：`lingshu/world/silhouette3d.py` 的 `set_expression` 用一串
`if expression == "...":` 分派、末尾直接 `return` 默认分支，**对未知值不做任何校验**
——任何无法识别的 `state`/表情名**静默退化为 `neutral`**，无异常、无 warning、
返回值无失败指示。`_eye_poly` / `_mouth_poly` 的未知 `shape` 同样落末尾 `return`。

端到端可达性（实跑核实）：`scene_model._apply_state_to_silhouette` 透传
`entity.state`，而该值可由**库内数据**驱动（`ingest_scene` 写入 `state_attributes`
→ `load_world_from_memory` 读回 → `WorldModel.build()`）；写一个未知 state（如
`"mystery"`）重建世界时 `set_expression` 拿到未知值却静默出 neutral 脸。

修法（取向=fail-soft）：未知表情名 ⇒ `warnings.warn` + 退回 `neutral`；未知眼型/嘴型
⇒ 同款告警 + 退回默认。**不选 fail-closed**：仓内合法调用方会传 `state="tired"`
（scene_model 文档化状态之一，`tired` 不在表情映射里）⇒ fail-closed 会让既有宽松
调用方的 `build()` 直接崩。边界与理由见 `set_expression` docstring。

守卫判据（回退 ⇒ 指名哪条红）：
  ① 未知表情名 ⇒ 发 `UserWarning` 且文案点名合法取值集合；返回与 `neutral` 逐位相同；
  ② 未知眼型 / 嘴型（直接调 `_eye_poly`/`_mouth_poly`）⇒ 同款告警 + 默认几何；
  ③ 防误杀正对照：**全部合法**表情 × 两个模板 ⇒ 眼/嘴/高光/腮红几何与颜色
     与**改前冻结 sha256 逐位相同**；
  ④ 变异自证：撤掉校验（把合法集合扩到含未知值）⇒ ① 必红。

运行（lingshu 仓根）：
  python3 -X utf8 -m pytest tests/test_issue663_silhouette_unknown_state_guard.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lingshu.world import silhouette3d as S                       # noqa: E402

EXPRS = ("happy", "sad", "angry", "surprised", "shy", "neutral")
MAKERS = ("fatfish_skinned", "fatfish_skeleton")
#: 改前冻结：全部合法表情 × 两模板的眼/嘴/高光/腮红几何 + 颜色，序列化 sha256。
#: 该值在**改前代码**上复算取得（`git stash` 后跑同一快照脚本），改后逐位相同。
FROZEN_SHA256 = "ad7df9a4e6f28030700c11165cda701b7426ca9f2fe4e876d4649e149bdaec1e"
NEUTRAL_SHA256 = "1ac0560f477f7b3550c93f07cc12336d5c9c5fcca7636b289df7142761eae2ea"


def _snap(maker, expr):
    """快照该模板在该表情下的眼/嘴/高光几何 + 腮红颜色（确定性，不依赖 Pillow）。"""
    s = getattr(S, maker)()
    S.set_expression(s, expr)
    out = {}
    for p in s.parts:
        if (p.name in ("eye_l", "eye_r", "mouth")
                or p.name.startswith("blush_") or p.name.startswith("highlight_")):
            out[p.name] = [list(pt) for pt in p.points]
            out[p.name + "__color"] = list(p.color)
    return out


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


# ===========================================================================
# ① 未知表情名 ⇒ 告警 + 退回 neutral
# ===========================================================================
@pytest.mark.parametrize("bad", ["mystery", "tired", "HAPPY", "", "开心", None])
def test_663_1_未知表情名告警且点名合法集合(bad):
    """未知表情名 ⇒ 发 `UserWarning`；文案须点名「合法取值」集合（便于调用方自纠）。"""
    s = S.fatfish_skinned()
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        S.set_expression(s, bad)
    msgs = [str(w.message) for w in rec
            if issubclass(w.category, UserWarning)]
    assert msgs, "未知表情名 %r 没有发出任何 UserWarning（静默退化未消除）" % (bad,)
    joined = " ".join(msgs)
    assert "合法取值" in joined, "告警文案未点名合法取值集合：%r" % joined
    assert "happy" in joined and "neutral" in joined, (
        "告警文案未列出合法表情名：%r" % joined)


def test_663_2_未知表情名返回与neutral逐位相同():
    """fail-soft 的语义：告警后**保持既有返回**（退化为 neutral），不抛异常。"""
    a = S.fatfish_skinned()
    b = S.fatfish_skinned()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        S.set_expression(a, "mystery")
        S.set_expression(b, "neutral")
    assert _sha(_snap_parts(a)) == _sha(_snap_parts(b)), (
        "未知表情名的返回与 neutral 不一致（fail-soft 语义被破坏）")


def _snap_parts(s):
    out = {}
    for p in s.parts:
        if (p.name in ("eye_l", "eye_r", "mouth")
                or p.name.startswith("blush_") or p.name.startswith("highlight_")):
            out[p.name] = [list(pt) for pt in p.points]
            out[p.name + "__color"] = list(p.color)
    return out


# ===========================================================================
# ② 未知眼型 / 嘴型 ⇒ 告警 + 默认几何
# ===========================================================================
def test_663_3_未知眼型告警且退回normal():
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        got = S._eye_poly(0.0, 0.0, 1.0, 0.5, "瞎写眼型")
    msgs = [str(w.message) for w in rec if issubclass(w.category, UserWarning)]
    assert msgs, "未知眼型没有发出 UserWarning"
    assert "合法取值" in " ".join(msgs)
    assert got == S._eye_poly(0.0, 0.0, 1.0, 0.5, "normal"), (
        "未知眼型未退回 normal 几何")


def test_663_4_未知嘴型告警且退回smile():
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        got = S._mouth_poly(0.0, 0.0, 1.0, 0.5, "瞎写嘴型")
    msgs = [str(w.message) for w in rec if issubclass(w.category, UserWarning)]
    assert msgs, "未知嘴型没有发出 UserWarning"
    assert "合法取值" in " ".join(msgs)
    assert got == S._mouth_poly(0.0, 0.0, 1.0, 0.5, "smile"), (
        "未知嘴型未退回 smile 几何")


# ===========================================================================
# ③ 防误杀正对照：全部合法值 ⇒ 输出与改前逐位相同、且零告警
# ===========================================================================
def test_663_5_全部合法表情输出与改前逐位相同():
    blob = {}
    for m in MAKERS:
        for e in EXPRS:
            blob["%s/%s" % (m, e)] = _snap(m, e)
    got = _sha(blob)
    assert got == FROZEN_SHA256, (
        "合法表情的输出被改动了（修复误伤合法路径）：%s vs 冻结 %s"
        % (got[:16], FROZEN_SHA256[:16]))


def test_663_6_合法表情不产生任何告警():
    """合法取值路径必须**零 warning**（否则每次正常渲染都刷警告 = 噪声）。"""
    for m in MAKERS:
        for e in EXPRS:
            with warnings.catch_warnings(record=True) as rec:
                warnings.simplefilter("always")
                S.set_expression(getattr(S, m)(), e)
            msgs = [str(w.message) for w in rec
                    if issubclass(w.category, UserWarning) and "silhouette3d" in str(w.message)]
            assert not msgs, "合法表情 %r/%r 误报告警：%r" % (m, e, msgs)


def test_663_7_合法眼型嘴型不产生告警():
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        for sh in S._EYE_SHAPES:
            S._eye_poly(0.0, 0.0, 1.0, 0.5, sh)
        for sh in S._MOUTH_SHAPES:
            S._mouth_poly(0.0, 0.0, 1.0, 0.5, sh)
    msgs = [str(w.message) for w in rec if issubclass(w.category, UserWarning)]
    assert not msgs, "合法眼型/嘴型误报告警：%r" % msgs


# ===========================================================================
# ④ 变异自证：撤掉校验 ⇒ ① 必红
# ===========================================================================
def test_663_8_变异自证_把未知值并入合法集合则必红(monkeypatch):
    """本文件内自证「守卫有牙」：把合法集合扩到含未知 token ⇒ 校验不再触发 ⇒
    ① 断言「必须告警」立刻变红。证明 ① 的判据确实依赖该校验。"""
    monkeypatch.setattr(S, "_EXPRESSIONS", S._EXPRESSIONS + ("mystery",))
    s = S.fatfish_skinned()
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        S.set_expression(s, "mystery")
    msgs = [str(w.message) for w in rec if issubclass(w.category, UserWarning)]
    assert not msgs, "变异后仍告警（说明 ① 的判据不依赖该校验，守卫无牙）"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
