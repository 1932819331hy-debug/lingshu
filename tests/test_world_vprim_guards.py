# -*- coding: utf-8 -*-
"""vprim 视觉原语守卫（本组 #174 / #374 / #405；P2 批次 #309）。

本组三条 P1 缺陷都落在 lingshu/world/vprim.py 的确定性原语上：
  #174 parse_anchor 读不回自家 anchor_text()/describe()（类别被截断 + conf 丢失）
  #374 parse_anchor 锚点正则前缀无边界断言 → 平方回溯（资源耗尽）
  #405 spatial_relation 先判上下、且不比两轴分离量（斜对角关系误判）
P2 #309：_overlap 的分母被钳到 `max(1.0, min(a_area, b_area))` ⇒ 归一化坐标 /
  亚像素框下重叠率被压低、关系判定随坐标单位变化（scale 依赖）。

运行：python -m pytest tests/test_world_vprim_guards.py -q
      python tests/test_world_vprim_guards.py
"""
import os as _os
import sys as _sys
import time as _time

_RP = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # 仓根
_sys.path.insert(0, _RP)

from lingshu.world.vprim import VPrim, parse_anchor, spatial_relation  # noqa: E402


def test_parse_anchor_roundtrips_own_serializers():
    """#174：parse_anchor 必须是 anchor_text()/describe() 的逆。

    旧实现用 `[\\w\\-]+` 收类别 → `obj.2`/`a/b`/`person-1` 之外的类别被截断成
    末段（`obj.2@(...)` 读回 `2`）；且丢弃 describe() 的 `conf=` → 一律回默认
    0.5。两条都是「读不回自家序列化产物」，本断言逐项钉住。
    """
    for cat in ("moon", "person-1", "obj.2", "a/b", "月亮"):
        v = VPrim(category=cat, bbox=(452, 84, 516, 148), confidence=0.52)
        a = parse_anchor(v.anchor_text())
        assert a is not None, f"anchor_text 读不回: {v.anchor_text()!r}"
        assert (a.category, tuple(a.bbox)) == (cat, v.bbox), \
            f"anchor_text 逆失败: {v.anchor_text()!r} -> {a.category!r}"
        d = parse_anchor(v.describe())
        assert d is not None, f"describe 读不回: {v.describe()!r}"
        assert (d.category, tuple(d.bbox)) == (cat, v.bbox), \
            f"describe 类别/bbox 逆失败: {v.describe()!r} -> {d.category!r}"
        assert d.confidence == v.confidence, \
            f"describe 置信度未读回: {v.describe()!r} -> {d.confidence}"


def test_parse_anchor_skips_scene_prefix_without_truncating_category():
    """#174 回归面：vprims_to_scene_text 的 `[视觉原语…] ` 前缀仍被跳过。

    放宽类别字符类后，前缀（空白之后）不得被吞进类别；这是 core.world3d build /
    vprim_query 的真实输入形态（按「；」切分后首段带前缀）。
    """
    token = "[视觉原语 山景] person-1@(10,20,30,40) 20x20 conf=0.73"
    vp = parse_anchor(token)
    assert vp is not None
    assert vp.category == "person-1"
    assert tuple(vp.bbox) == (10, 20, 30, 40)
    assert vp.confidence == 0.73


def test_parse_anchor_is_linear_on_adversarial_input():
    """#374：parse_anchor 的正则不得平方回溯（资源耗尽）。

    旧式 `([\\w\\-]+)@\\(` 的可变长前缀无边界断言：长词串上 `re.search` 逐起点
    展开、每起点线性回退 ⇒ O(n²)。调用方 core.world3d build / vprim_query 对库内
    content 不截断，故可被内容拖垮。n=40000 时旧实现实测 4.35s；修复后为字面量
    `@(` 定位 + 线性回扫，同一输入 < 0.01s。这里以 1.0s 为界（红侧 4× 余量、
    绿侧 >100× 余量）。
    """
    budget = 1.0
    for payload in ("a" * 40000,            # 长词串：旧前缀平方回溯
                    "@(" + "1" * 40000):    # 坐标数字串：不得二次回溯
        t0 = _time.perf_counter()
        parse_anchor(payload)
        elapsed = _time.perf_counter() - t0
        assert elapsed < budget, \
            f"parse_anchor 在 len={len(payload)} 输入上耗时 {elapsed:.3f}s ≥ {budget}s（回溯）"


def test_spatial_relation_picks_dominant_axis_not_vertical_first():
    """#405：spatial_relation 的方向由「主导轴」决定，不是固定上下优先。

    旧实现 `elif acy < by1: above` 在横向分离远大于纵向时仍报 above。这里用
    斜置对：a 在 b 左侧 100px、仅高出 1px（dx=-100, dy=-6）必须判 left_of；
    镜像（a 在下 95px、仅偏右 1px）必须判 below。同时钉住轴向明确的常规用例
    与包含/重叠不被破坏。
    """
    a = (0, 0, 10, 10)          # center (5,5)
    b = (100, 6, 110, 16)       # center (105,11)：横向间隙 95 > 纵向间隙 1
    assert spatial_relation(a, b)["relation"] == "left_of"
    assert spatial_relation(b, a)["relation"] == "right_of"

    c = (6, 100, 16, 110)       # center (11,105)：纵向间隙 95 > 横向间隙 1
    d = (0, 0, 10, 10)
    assert spatial_relation(c, d)["relation"] == "below"
    assert spatial_relation(d, c)["relation"] == "above"

    # 轴向明确的常规用例（修复前后必须一致）
    assert spatial_relation((0, 0, 10, 10), (0, 100, 10, 110))["relation"] == "above"
    assert spatial_relation((0, 100, 10, 110), (0, 0, 10, 10))["relation"] == "below"
    assert spatial_relation((0, 0, 10, 10), (100, 0, 110, 10))["relation"] == "left_of"
    assert spatial_relation((100, 0, 110, 10), (0, 0, 10, 10))["relation"] == "right_of"
    # 包含/被包含/重叠（结构性优先，不受轴序影响）
    assert spatial_relation((0, 0, 100, 100), (10, 10, 20, 20))["relation"] == "contains"
    assert spatial_relation((10, 10, 20, 20), (0, 0, 100, 100))["relation"] == "inside"
    assert spatial_relation((0, 0, 10, 10), (1, 1, 11, 11))["relation"] == "overlap"


# ==================== #309 ====================

def test_overlap_ratio_scale_invariant_example():
    """#309：重叠率必须与坐标单位/缩放无关（分母不再钳到 1.0）。

    缺陷形态：`return inter / max(1.0, min(a_area, b_area))`。同一对框整体
    ×1/1000 后面积 < 1，钳位生效 ⇒ 比值被压低。修前实测：像素 (overlap, 0.64)
    vs 归一化 (adjacent, 0.16)。
    """
    a_px, b_px = (0, 0, 500, 500), (100, 100, 600, 600)
    s = 1 / 1000
    a_n = tuple(v * s for v in a_px)
    b_n = tuple(v * s for v in b_px)
    r1, r2 = spatial_relation(a_px, b_px), spatial_relation(a_n, b_n)
    assert r1["relation"] == r2["relation"] == "overlap", (r1, r2)
    assert abs(r1["overlap_ratio"] - r2["overlap_ratio"]) <= 1e-3, (r1, r2)
    # 真实比值：交集 400x400=160000 / min(250000,250000)=250000 = 0.64
    assert abs(r1["overlap_ratio"] - 0.64) <= 1e-3, r1["overlap_ratio"]


def test_subpixel_boxes_true_ratio():
    """#309：亚像素小框（面积 < 1）的重叠率必须是真实比值，不被钳位压低。

    缺陷形态同 #309。修前实测：真实 0.766 读成 0.49、关系误报 adjacent。
    """
    r3 = spatial_relation((10, 10, 10.8, 10.8), (10.1, 10.1, 10.9, 10.9))
    assert abs(r3["overlap_ratio"] - 0.766) <= 0.01, r3["overlap_ratio"]
    assert r3["relation"] == "overlap", r3["relation"]


def test_relation_scale_invariant_property():
    """#309 性质测试：500 对随机框在 ×1/1000 与 ×1/640 缩放下关系与重叠率不变。

    修前（钳位）在小面积缩放下大面积对会翻转 relation；本断言逐对钉住。
    """
    import random
    rng = random.Random(20261011)
    for _ in range(500):
        x, y = rng.uniform(0, 100), rng.uniform(0, 100)
        w, h = rng.uniform(0.1, 40), rng.uniform(0.1, 40)
        a = (x, y, x + w, y + h)
        x2, y2 = rng.uniform(0, 100), rng.uniform(0, 100)
        w2, h2 = rng.uniform(0.1, 40), rng.uniform(0.1, 40)
        b = (x2, y2, x2 + w2, y2 + h2)
        base = spatial_relation(a, b)
        for sc in (1 / 1000, 1 / 640):
            a2 = tuple(v * sc for v in a)
            b2 = tuple(v * sc for v in b)
            got = spatial_relation(a2, b2)
            assert got["relation"] == base["relation"], (a, b, sc, base, got)
            assert abs(got["overlap_ratio"] - base["overlap_ratio"]) <= 1e-3, \
                (a, b, sc, base, got)

