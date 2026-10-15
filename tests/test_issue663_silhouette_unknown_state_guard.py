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
     与**改前冻结 sha256 逐位相同**（冻结值取「量化到 1e-6 后」的 sha——原始
     float repr 不可跨平台，见 `FROZEN_SHA256` 上方注释）；
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
#: 改前冻结：全部合法表情 × 两模板的眼/嘴/高光/腮红几何 + 颜色，**量化到 1e-6 后**
#: 序列化的 sha256。该值在**改前代码**（`385a243` 版 `silhouette3d.py`）上复算取得，
#: 改后逐位相同。
#:
#: 为什么必须量化（量具缺陷，不是产品缺陷）：快照里 `fatfish_skeleton()` 的头/眼几何
#: 经 `math.cos`/`math.sin`，而 Windows UCRT 与 Linux glibc 的 libm 结果可差 ≤1 ULP
#: （实测 1188 个 float 中 8 个不同，最大绝对差 1.39e-17）。用 mpmath 60 dps 正确舍入
#: 的 `cos`/`sin` 打桩复算，本机即得 CI 上那个「不符」值 `9eb9dc39…` ⇒ 直接对**原始
#: float repr** 求 sha **不可跨平台**，CI 红是量具所致、不是几何被改动。量化到 1e-6 后
#: 余量 ≥5.6e9 倍（这 8 个值到舍入边界最近 ≥7.83e-08），而任何真实几何改动（≥1e-5）
#: 仍必然翻转 sha——牙齿还在（见 `test_663_5` 与变异自证 `test_663_8`）。
FROZEN_SHA256 = "33bf4cc0fe6fbc4a49d98ff46fffc3519bc98ef343fa02d5596fc9c1b06ca460"
#: 同款量化 sha：`fatfish_skinned` 在 `neutral` 下的快照。用于 `test_663_2`——把
#: fail-soft 的「a 等于 b」升级为「b 等于冻结常量」，即使 a/b 两侧被同时改动也能抓到。
NEUTRAL_SHA256 = "6c1c34c10cbda8fa2016e4100aa9bd7ad2699cccb6fcd1b90cc62721f892056e"

_Q_DIGITS = 6   # 跨平台 libm 差 ≤1 ULP；量化到 1e-6 后余量 ≥5.6e9 倍（见上注释）


def _q(obj):
    """递归把 float 量化到 6 位小数——消除 libm 跨平台 ≤1 ULP 差对 sha 的影响。

    依据（编排侧实测，2026-10-15）：本件冻结值曾用**原始 float repr** 算 sha，
    而 fatfish_skeleton 的头/眼几何经 math.cos/sin；Windows UCRT 与 glibc 可差
    ≤1 ULP（实测 1188 值中 8 个不同、最大绝对差 1.39e-17）。用 mpmath 60 dps
    正确舍入的 cos/sin 打桩复算，本机即得 CI 上那个「不符」值 ⇒ 红是量具不可
    跨平台所致，非几何被改动。量化到 1e-6 后：这 8 个值到舍入边界最近 ≥7.83e-08，
    余量 ≥5.6e9 倍；同时任何真实几何改动（≥1e-5）仍必然翻转 sha（牙齿仍在）。
    """
    if isinstance(obj, dict):
        return {k: _q(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_q(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, _Q_DIGITS)
    return obj


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
    return _q(out)


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
    # 加强：b 侧同时钉在冻结常量上——若 a/b 被同时改动，「两侧互等」仍成立、
    # 这条钉死的断言会立刻翻红（见 FROZEN/NEUTRAL 注释里的跨平台量化理由）。
    assert _sha(_snap_parts(b)) == NEUTRAL_SHA256, (
        "neutral 快照偏离冻结常量（fail-soft 基线被改动）：%s vs 冻结 %s"
        % (_sha(_snap_parts(b))[:16], NEUTRAL_SHA256[:16]))


def _snap_parts(s):
    out = {}
    for p in s.parts:
        if (p.name in ("eye_l", "eye_r", "mouth")
                or p.name.startswith("blush_") or p.name.startswith("highlight_")):
            out[p.name] = [list(pt) for pt in p.points]
            out[p.name + "__color"] = list(p.color)
    return _q(out)


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
