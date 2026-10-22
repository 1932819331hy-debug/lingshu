# -*- coding: utf-8 -*-
"""test_issue860_n_expected_guard · `multimodal_check(..., n_expected=...)` 兑现契约守卫
====================================================================================
缺陷（件 #860，`lingshu/nn/hex_text.py:225-246`，锚定 `f6b85abf`）：
  签名声明 `n_expected: Optional[int] = None`（语义＝「期望检测到的物体数」），
  函数体一次不读它——既不参与判定，也不落进返回体。调用方传 `n_expected=0`
  （空白帧／纯负样本／拒答校验）时，返回体与不传时逐字段相同，`verdict` 照判
  `ACCEPT`，静默吞掉「实际检测数 ≠ 期望数」这条矛盾信号。

本守卫钉死四条不变量（与 1A 同一份规格，口径不改）：
  A 边界：不传 与 `n_expected=None` 返回 dict `==` 全等，键集合恰为修前 6 键。
  B 回显：`k is not None` ⇒ `res["n_expected"] == k` 且 `"count_match" in res`。
  C 判定：`count_match is False` ⇒ `verdict != "ACCEPT"`（降级目标钉死 `DEFER`）。
  D 不误伤：`count_match is True` ⇒ 6 个**老键**取值与不传时逐项相同。
    注：B 已钉死非 None 时必须多出 `n_expected`／`count_match` 两键，故 D 只能
    按「6 个老键逐项」读，不能读成整体 dict 全等——两者只有这样读才不冲突。

纪律：受控场景，`spatial_detect` 打桩成固定检测清单——不训练、不读图，
      全件 < 1 秒（不新增 150 步训练）。
防假绿自证（两道）：① 调用计数——每次取读数都断言「打桩函数本次恰被调用一次」
      且收到占位入参 (`_run` 内)；② 输出溯源——`test_stub_output_is_adopted` 让 stub
      返回 2 件、断言 `detections == 2`，证读数取自 stub 而非真模型。
      打桩一旦因重构（局部导入/模块级别名/间接调用）静默失效，守卫立即转红而非假绿。
运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue860_n_expected_guard.py -q
"""
import os as _os
import sys as _sys

_RP = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # 仓根
_sys.path.insert(0, _RP)

import pytest  # noqa: E402

import lingshu.nn.hex_text as hex_text  # noqa: E402
from lingshu.nn.hex_text import multimodal_check  # noqa: E402

# 修前返回体的键集合（顺序即规格里写的那 6 个）——A 的键集合判据。
OLD_KEYS = ["align", "clauses", "detections", "note", "score", "verdict"]

DESC = "左上有红色圆形"          # 解析为单子句：pos=r0 / shape=circle / color=red
DET_MATCH = [{"pos": "r0", "obj": "circle|red", "conf": 0.9, "share": 0.9}]
# ↑ 与描述全符 ⇒ fuse 得分 1.0 ⇒ ACCEPT。C 的「只降 ACCEPT」需要这个前置。


@pytest.fixture
def dets(monkeypatch):
    """受控检测清单（默认 1 件全符）。可改写 holder['v'] 造出 REJECT/DEFER/BLINDSPOT。

    `calls` 记录打桩函数每次被调用的入参，供 `_run` 做「打桩真被调用」自证。
    """
    holder = {"v": [dict(d) for d in DET_MATCH], "calls": []}

    def fake_detect(net, lat, exist_mult=1.15):
        holder["calls"].append({"net": net, "lat": lat})
        return [[dict(d) for d in holder["v"]]]

    monkeypatch.setattr(hex_text, "spatial_detect", fake_detect)
    return holder


def _run(holder, text=DESC, **kwargs):
    """跑一次 multimodal_check，并自证「受控 spatial_detect 真被调用恰好一次」。

    为什么必须有这条：`spatial_detect` 是 hex_text 模块级定义、函数内裸名调用，
    所以 `monkeypatch.setattr(hex_text, ...)` 当前确实生效。但若将来有人把它改成
    局部导入、模块级别名（如 `_sd = spatial_detect`）或经别的模块间接调用，打桩会
    **静默失效**——守卫照样绿，测到的却是真模型输出，属假绿。本断言把「打桩生效」
    从隐含假设变成显式契约，成本一行。

    计数按次重置（取完即 clear），故同一测试内多次调用 `_run` 不会跨调用累加导致假红。
    net/lat 传 None 是占位：spatial_detect 已被打桩，二者不参与计算。
    """
    before = len(holder["calls"])
    res = multimodal_check(None, None, text, **kwargs)
    got = len(holder["calls"]) - before          # 按次差值：同测试内多次调用不累加
    assert got == 1, (
        "受控 spatial_detect 未被调用恰好一次——打桩失效，本次读数不可信"
        f"（本次实际调用 {got} 次）")
    last = holder["calls"][-1]
    assert last["net"] is None and last["lat"] is None, (
        "打桩收到的不是占位入参——调用未走到受控 stub，读数不可信"
        f"（net={last['net']!r}, lat={last['lat']!r}）")
    return res


# ==================== 防假绿 · 输出溯源 ====================

def test_stub_output_is_adopted(dets):
    """证明读数的 `detections` 确实来自受控 stub 的返回值，而非真模型。

    `_run` 的调用计数只证「stub 被调用」；若将来在 :237 之后加了回退真模型、
    或对 detections 重算，计数照样是 1，但读数已非受控。此处让 stub 返回 2 件，
    断言返回体的 `detections` 恰为 2——返回体只有取自 stub 才可能是 2。
    """
    dets["v"] = [dict(DET_MATCH[0]),
                 {"pos": "r8", "obj": "stripe|blue", "conf": 0.5, "share": 0.5}]
    res = _run(dets)
    assert res["detections"] == 2, (
        f"detections 未取自受控 stub（期望 2，实得 {res['detections']}）——"
        "读数非受控，不可信")


# ==================== A 边界 ====================

def test_A_none_equals_omitted(dets):
    """不传 与 None 必须全等，且键集合恰为修前 6 键（不得先加键再删）。"""
    omitted = _run(dets)
    explicit_none = _run(dets, n_expected=None)
    assert explicit_none == omitted, f"None 与不传不等: {explicit_none} != {omitted}"
    assert sorted(omitted.keys()) == OLD_KEYS, \
        f"键集合不是修前 6 键: {sorted(omitted.keys())}"
    assert "n_expected" not in omitted, "不传时不得回显 n_expected"
    assert "count_match" not in omitted, "不传时不得回显 count_match"


# ==================== B 回显 ====================

@pytest.mark.parametrize("k", [0, 1, 3])
def test_B_echo(dets, k):
    """非 None 必须回显 n_expected 原值，并带上 count_match。"""
    res = _run(dets, n_expected=k)
    assert "n_expected" in res, f"未回显 n_expected（传 {k}）: {sorted(res.keys())}"
    assert res["n_expected"] == k, f"回显值不符: {res['n_expected']} != {k}"
    assert "count_match" in res, f"缺 count_match（传 {k}）: {sorted(res.keys())}"


# ==================== C 判定 ====================

@pytest.mark.parametrize("k", [0, 3, -1, 10 ** 9])
def test_C_mismatch_downgrades(dets, k):
    """实测 1 件 vs 期望 k（k != 1）⇒ count_match False ⇒ verdict 不得 ACCEPT。

    -1 / 10**9 一并钉死：既不许抛异常，也不许溢出，一律算不符。
    """
    base = _run(dets)
    assert base["verdict"] == "ACCEPT", \
        f"前置不成立：受控场景应判 ACCEPT，实得 {base['verdict']}"
    res = _run(dets, n_expected=k)
    assert res["count_match"] is False, \
        f"实测 {base['detections']} 件 vs 期望 {k}：count_match 应为 False"
    assert res["verdict"] != "ACCEPT", \
        f"计数不符仍判 ACCEPT（期望 {k}）: {res}"
    assert res["verdict"] == "DEFER", \
        f"降级目标钉死 DEFER，实得 {res['verdict']}"
    assert res["score"] == base["score"], \
        f"score 不该被计数不符改动: {res['score']} != {base['score']}"
    assert res["note"] != base["note"], "计数不符时 note 应追加说明"


# ==================== D 不误伤 ====================

def test_D_match_no_side_effect(dets):
    """计数吻合 ⇒ 6 个老键逐项与不传时相同（含 note，不许被追写）。"""
    base = _run(dets)
    res = _run(dets, n_expected=1)
    assert res["count_match"] is True, \
        f"实测 1 件 vs 期望 1：count_match 应为 True，实得 {res.get('count_match')}"
    for key in OLD_KEYS:
        assert res[key] == base[key], \
            f"吻合时老键 {key} 被改动: {res[key]!r} != {base[key]!r}"


# ==================== 「只降 ACCEPT」没写歪 ====================

@pytest.mark.parametrize("text,det,expected", [
    (DESC, [{"pos": "r8", "obj": "stripe|blue", "conf": 0.5, "share": 0.5}], "REJECT"),
    (DESC, [{"pos": "r0", "obj": "circle|blue", "conf": 0.5, "share": 0.5}], "DEFER"),
    ("", [], "BLINDSPOT"),
])
def test_only_accept_downgraded(dets, text, det, expected):
    """原 verdict 已是 REJECT/DEFER/BLINDSPOT ⇒ 计数不符也必须保持原值。"""
    dets["v"] = det
    base = _run(dets, text)
    assert base["verdict"] == expected, \
        f"前置不成立：期望构造出 {expected}，实得 {base['verdict']}"
    res = _run(dets, text, n_expected=3)          # 3 != len(det) ⇒ 不吻合
    assert res["count_match"] is False
    assert res["verdict"] == expected, \
        f"非 ACCEPT 的 verdict 被改写: {res['verdict']} != {expected}"


if __name__ == "__main__":
    _sys.exit(pytest.main([__file__, "-v"]))
