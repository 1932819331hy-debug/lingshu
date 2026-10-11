# -*- coding: utf-8 -*-
"""守卫 · verdict.assess 不得把「未声明遮挡」（__UNASSERTED__）当作「有遮挡」，
且零面积部件不得被判 ACCEPT（lingshu issue #296）
============================================================================
缺陷（HEAD 修复前，`lingshu/core/verdict.py:assess`）
    ① `occluded = image_domains.get("occlusion", "无遮挡")` 对**缺键**默认无遮挡，
       但 `occluded != "无遮挡"` 把 `__UNASSERTED__`（`condition_normalize` 对
       「未声明」的编码）当遮挡 ⇒ 同一张图，未归一输入判 ACCEPT、经
       `canonical_condition` 归一后 4 个可见部件 DEFER——两种「未知」得出相反结论。
    ② `_fg_ratio` 用 `max(1, part_area)`：零面积时比值退化为前景像素数本身
       （实测 5.0）⇒ 越过所有阈值判 ACCEPT。

    实测（HEAD，本仓 Python 3.12）：
      raw = img0_domains(); canon = canonical_condition(raw)
      assess({'type':'head'}, raw,   9000, 10000) → ACCEPT
      assess({'type':'head'}, canon, 9000, 10000) → DEFER   ← 归一前后不一致
      assess({'type':'head'}, {}, 5, 0)           → ACCEPT  ← 零面积

修复
    · `occlusion == UNASSERTED` ⇒ 视同「无遮挡」（与缺键同义）；
    · `part_area <= 0` ⇒ REJECT（无有效区域），先于阈值比较。

判据来源
    · UNASSERTED 的语义：`lingshu/core/condition_normalize.py:38,67-76`——canonical
      化时「未声明/非法值」一律折叠为 `UNASSERTED`；卡 v0.2 P0-001「未声明无可校验」。
      「未声明」与「缺键」必须同结论（本件钉死）。
    · 零面积 ⇒ REJECT：四态语义（`docs/theory/自研蜂窝CNN_理论稿_v0.1.md:33`）——
      无有效区域即条件冲突，不是「条件充分」。属整数边界，与 #196 的 NaN 面独立。
    · 阈值本体（0.10 / 0.30）与 `CLOTH_OCCLUDES` 映射**未改**——本件只纠正
      「未知」的读法与零面积边界，未放宽任何判据。

定点变异自证（抽掉修复 ⇒ 必红）
    ① 删 `if occluded == UNASSERTED: occluded = "无遮挡"` ⇒
       test_canonicalised_domains_match_raw 红（canonical → DEFER）；
    ② 删 `if part_area <= 0: return REJECT` ⇒ test_zero_area_part_is_not_accepted 红（→ ACCEPT）。

运行（仓根）：python -X utf8 -m pytest tests/test_verdict_unasserted.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.condition_normalize import (  # noqa: E402
    canonical_condition, img0_domains, UNASSERTED,
)
from lingshu.core.verdict import assess  # noqa: E402

PARTS = ["head", "torso", "upper_arm_L", "hand_L", "upper_leg_L", "foot_L"]


def test_canonicalised_domains_match_raw():
    """★ 核心①：同一图 raw 与 canonical_condition(raw) 对每个部件结论一致。"""
    raw = img0_domains()
    canon = canonical_condition(raw)
    assert canon["occlusion"] == UNASSERTED, canon  # 前提：未声明被编码为 UNASSERTED
    for t in PARTS:
        a = assess({"type": t}, raw, part_fg_px=9000, part_area=10000)
        b = assess({"type": t}, canon, part_fg_px=9000, part_area=10000)
        assert a["verdict"] == b["verdict"], (t, a, b)


def test_unasserted_occlusion_equals_missing_key():
    """★ 核心①（直取语义）：UNASSERTED 与缺键同结论——都不得因遮挡判 DEFER。"""
    missing = assess({"type": "head"}, {}, part_fg_px=9000, part_area=10000)
    unasserted = assess({"type": "head"}, {"occlusion": UNASSERTED},
                        part_fg_px=9000, part_area=10000)
    assert missing["verdict"] == "ACCEPT", missing
    assert unasserted["verdict"] == missing["verdict"], (missing, unasserted)
    assert unasserted.get("occluded") is not True, unasserted


def test_explicit_occlusion_still_defers():
    """正对照（防误杀）：显式「部分遮挡/全遮挡」仍判 DEFER（未把真遮挡一起抹平）。"""
    for occ in ("部分遮挡", "全遮挡"):
        r = assess({"type": "head"}, {"occlusion": occ},
                   part_fg_px=9000, part_area=10000)
        assert r["verdict"] == "DEFER", (occ, r)
        assert r.get("occluded") is True, (occ, r)


def test_zero_area_part_is_not_accepted():
    """★ 核心②：零面积（及负面积）部件不得被判 ACCEPT。"""
    for area in (0, -5):
        r = assess({"type": "head"}, {}, part_fg_px=5, part_area=area)
        assert r["verdict"] != "ACCEPT", (area, r)
        assert r["verdict"] == "REJECT", (area, r)


def test_positive_control_normal_paths_intact():
    """正对照（防误杀）：正常面积下四态各档未变。"""
    d = {"clothing": "无", "occlusion": "无遮挡", "contrast": "清晰"}
    assert assess({"type": "head"}, d, 9000, 10000)["verdict"] == "ACCEPT"
    assert assess({"type": "head"}, d, 100, 10000)["verdict"] == "REJECT"
    assert assess({"type": "head"}, d, 2000, 10000)["verdict"] == "BLINDSPOT"
    assert assess({"type": "torso"}, dict(d, clothing="常服"),
                  9000, 10000)["verdict"] == "DEFER"
