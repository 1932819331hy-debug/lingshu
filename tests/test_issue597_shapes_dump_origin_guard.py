# -*- coding: utf-8 -*-
"""test_issue597_shapes_dump_origin_guard · dump_shapes 坐标口径如实导出守卫（#597）
===================================================================================
缺陷（#597，成立）：`dump_shapes` 硬编码文件级 `pos_origin="bbox_center"`——
库中 anchor 口径件（`register_shape(..., recenter=False)` / `load_shapes` 的
anchor 件）导出时被**误标**；任何再载入都会让 `load_shapes` 走
`_recenter_parts` 把关节枢轴挪走。载入侧 #575/#580 已修（缺失告警回落、
非法 raise），导出侧无守卫——往返保真在导出端断裂。

改前读数（本机，Windows / py3.12，main @ 375cf55）：两盒部件按
`recenter=False`（anchor 口径）注册（pos 相对枢轴、不对称），`dump_shapes`
→ `doc["pos_origin"] == "bbox_center"`（真实口径 anchor）；清内存态后
`load_shapes` 往返，**最大枢轴位移 0.7500**（issue 取证同型件 0.4332）。

修法：`pos_origin` 按**待导出类别的真实口径**（`_SHAPE_ORIGINS`）如实写出；
内置库全为 bbox_center ⇒ 纯内置导出逐字节不变。混用口径时 fail-closed
（schema 的 pos_origin 是文件级单一值，混源必有一侧被误标）。

守卫判据（注入式断言，回退修复即红）：
  G1  anchor 口径件导出 doc.pos_origin=="anchor"，且 dump→load 往返
      **部件 pos 逐位一致**（枢轴零位移）；
  G2（防过修·字节兼容）bbox_center 口径（内置）导出 doc.pos_origin==
      "bbox_center"；
  G3  混用口径导出必须 ValueError（fail-closed，不写半截）。

判据来源：issue #597 取证（round-trip 枢轴位移）＋ #575/#580 载入侧契约
（pos_origin 必需、决定是否归零）；schema 声明见 `load_shapes` docstring。
运行：pytest tests/test_issue597_shapes_dump_origin_guard.py
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.world import shapes as S  # noqa: E402

CAT_A = "__rt597_anchor__"
CAT_B = "__rt597_bbox__"
PARTS = [
    {"kind": "box", "pos": (0.25, 0.5, 0.35), "size": (0.5, 0.5, 0.5)},
    {"kind": "box", "pos": (1.25, 0.5, -0.15), "size": (0.5, 0.5, 0.5)},
]


@pytest.fixture()
def anchor_cat(tmp_path):
    """注册一个 anchor 口径件并清理（全局库状态隔离）。"""
    S.register_shape(CAT_A, PARTS, source="issue597", recenter=False)
    yield CAT_A
    S.SHAPE_LIBRARY.pop(CAT_A, None)
    S._SHAPE_ORIGINS.pop(CAT_A, None)
    S._SHAPE_SOURCES.pop(CAT_A, None)


def test_anchor_dump_is_truthful_and_roundtrip_exact(tmp_path, anchor_cat):
    """G1：anchor 件导出须如实标 anchor，往返部件 pos 逐位一致（零枢轴位移）。"""
    before = [dict(p) for p in S.SHAPE_LIBRARY[anchor_cat]]
    out = str(tmp_path / "anchor.json")
    assert S.dump_shapes(out, source="issue597", only=[anchor_cat]) == 1
    doc = json.load(open(out, encoding="utf-8"))
    assert doc["pos_origin"] == "anchor", (
        "anchor 口径件被 dump_shapes 误标为 %r（#597）——再载入会 _recenter_parts "
        "挪走枢轴。pos_origin 须按 shape_origin() 如实写出。" % doc["pos_origin"])
    # 往返：清内存态再载入，部件 pos 必须逐位一致
    S.SHAPE_LIBRARY.pop(anchor_cat, None)
    S._SHAPE_ORIGINS.pop(anchor_cat, None)
    n_cat, n_part = S.load_shapes(out, source="issue597")
    assert (n_cat, n_part) == (1, len(PARTS))
    after = [dict(p) for p in S.SHAPE_LIBRARY[anchor_cat]]
    assert after == before, (
        "dump→load 往返后部件 pos 改变（#597 枢轴位移）：\n前 %r\n后 %r" % (before, after))
    assert S.shape_origin(anchor_cat) == "anchor"


def test_builtin_dump_still_bbox_center(tmp_path):
    """G2（防过修·字节兼容）：bbox_center 口径导出值不变。"""
    out = str(tmp_path / "builtin.json")
    assert S.dump_shapes(out, source="builtin", only=["chair"]) == 1
    doc = json.load(open(out, encoding="utf-8"))
    assert doc["pos_origin"] == "bbox_center", doc


def test_mixed_origin_dump_fails_closed(tmp_path, anchor_cat):
    """G3：混用口径导出必须 ValueError——文件级单一 pos_origin 表达不了混源。"""
    out = str(tmp_path / "mixed.json")
    with pytest.raises(ValueError, match="混用坐标口径"):
        S.dump_shapes(out, source="issue597", only=[anchor_cat, "chair"])
    assert not os.path.exists(out), "fail-closed：不得写半截文件"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
