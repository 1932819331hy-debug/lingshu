# -*- coding: utf-8 -*-
"""守卫 · #136/#311/#312/#313 · lingshu/world/world3d.py（本组四件合一）
============================================================================
#136 [多视角] add_view 从第 2 个视角起每次都 append 新 Object3D：同一把椅子
     4 个视角得到 3 个重复对象（下游 build_anchor_graph/计数/关系推断把它当
     3 个实体）。缺陷形态：`if result["center_3d"] is not None:` 内无条件
     `self.objects.append(obj)`。
     修法：按 MultiViewFusion 的**物体桶**（同一物体多视角=同一桶）记住
     Object3D 引用，首次成桶 append，后续视角原地更新 center/confidence。
     判据来源：world3d.add_view docstring「同一物体从多个视角观测 → 3D 位置
     收敛」；docs/theory/世界模型与语义时空图_完整理论整理与实现路线.md §3.2
     「三视图/双目视差」——多视角是对**同一物体**的收敛，不是多物体。
     守卫 G1：同一物体 4 视角 ⇒ objects 恰 1 个且对象身份不变、center 跟随
     融合结果；两物体 2 视角 ⇒ 仍 2 个（非回归，防过度合并）。
     变异（改回无条件 append）⇒ G1 读到 3 个 ⇒ 红。

#311 [渲染] _draw_box 按固定顺序画全部 6 个面、不做背面剔除：被遮的侧面/
     底面/顶面盖住可见面（俯视前面只剩 57.9%、仰视 63.2% 像素着色正确）。
     修法：凸的轴对齐盒只画朝向相机的面（外法线·(相机光心−面心) > 0）。
     判据来源：docs/theory/世界模型与语义时空图_完整理论整理与实现路线.md
     §3.2 透视投影——可见面须位于相机一侧（凸体面剔除为精确判定）。
     守卫 G2a：俯视/仰视下前面投影区域 100% 为前面色（改前 0.579/0.632）。
     守卫 G2b（防误杀）：相机在右/上/下时，右/顶/底面照常绘制。
     变异（改回写死 6 面顺序）⇒ G2a 读到 0.579/0.632 ⇒ 红。

#312 [渲染] 组合形状的部件按 SHAPE_LIBRARY 定义顺序绘制、彼此不做深度排序：
     身体后方的腿画在身体上（dog），桌面后方的桌腿压住桌面（table）。
     修法：部件按「相机光心→部件包围盒**最近点**」距离从远到近绘制。
     判据来源：同 §3.2 + 画家算法（近物遮远物）；最近点而非中心距——见 G3b。
     守卫 G3a：dog 远侧腿（dz=+0.125）投影处的像素须为身体色（改前为腿色）。
     守卫 G3b：嵌套部件不得翻转——树冠底部仍须盖住树干（按中心距排序会翻转）。
     变异（改回定义顺序遍历）⇒ G3a 读到腿色 ⇒ 红；
     变异（排序键换成部件中心距）⇒ G3b 读到树干色 ⇒ 红。

#313 [渲染] 金字塔图元高度写死为 30 像素：_draw_pyramid 的 apex 写成
     「底面投影均值 − 30」，不看 obj.size[1]、不看相机——0.5m 与 8m 高的
     金字塔（mountain/crystal/crown/ice_cream/鸟喙）渲染完全一样。
     修法：塔尖 = cam.project((x, y+size[1]/2, z))，画四个侧面按底边中点到
     相机光心的距离从远到近。
     判据来源：同 §3.2 透视投影——图元顶点须经同一投影映射（投影-关联）。
     守卫 G4：塔尖行 = project((x, y+h/2, z)) 的行（±1.5px）；屏幕高度随 h
     单调增（改前三者恒 37px）。
     变异（改回 `−30`）⇒ G4 两者皆红。

运行（仓根）：python -X utf8 -m pytest tests/test_issue136_311_312_313_world3d_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world.world3d import Camera3D, Object3D, World3D  # noqa: E402
from lingshu.world.shapes import get_shape  # noqa: E402

SW, SH = 800, 600


# ===========================================================================
# #136 同一物体多视角 ⇒ objects 里只有一个世界物体
# ===========================================================================

def test_136_same_object_seen_from_four_views_yields_one_object():
    """同一物体 4 视角 ⇒ objects 恰 1 个（缺陷形态：3 个重复对象）。

    直接照 issue #136 的最小复现（同一椅子、4 个环绕相机、精确 bbox）。
    """
    W, H = 640, 480
    P = (1.0, 0.5, 5.0)
    w = World3D()
    for e in [(0, 1.2, 0), (4, 1.2, 1), (-3, 1.2, 2), (2, 1.2, -1)]:
        c = Camera3D.look_at(e, P)
        sx, sy = c.project(P, W, H)
        w.add_view("chair", (sx - 10, sy - 10, sx + 10, sy + 10), W, H, camera=c)
    assert len(w.objects) == 1, (
        "#136：同一物体多视角被 append 成多个世界物体——%r"
        % [(o.category, o.center, o.source) for o in w.objects])
    assert w.objects[0].source == "multiview"
    # center 收敛到真值附近（多视角三角化）
    assert max(abs(a - b) for a, b in zip(w.objects[0].center, P)) < 0.2, w.objects[0].center


def test_136_same_object_updates_in_place_not_appends():
    """对象身份不变（原地更新）：4 视角全程同一 Object3D，且 center 跟随融合结果。

    缺陷形态下每次成桶都新建对象——对象 id 会变、len(objects) 会涨。
    """
    W, H = 640, 480
    P = (1.0, 0.5, 5.0)
    w = World3D()
    ids, added, updated, centers = [], [], [], []
    for i, e in enumerate([(0, 1.2, 0), (4, 1.2, 1), (-3, 1.2, 2), (2, 1.2, -1)]):
        c = Camera3D.look_at(e, P)
        sx, sy = c.project(P, W, H)
        r = w.add_view("chair", (sx - 10, sy - 10, sx + 10, sy + 10), W, H, camera=c)
        added.append(r.get("object_added"))
        updated.append(r.get("object_updated"))
        if r["center_3d"] is None:      # 首次观测仅记录（<2 视角不三角化）
            assert not w.objects, "#136：未三角化却已建世界物体"
            continue
        ids.append(id(w.objects[0]))
        centers.append(tuple(w.objects[0].center))
        # 世界物体的 center 必须等于本次融合结果（原地更新，非陈旧值）
        assert tuple(w.objects[0].center) == tuple(r["center_3d"]), (
            "#136：世界物体 center %r 未跟随融合结果 %r" % (w.objects[0].center, r["center_3d"]))
    assert len(set(ids)) == 1, "#136：同一物体的 Object3D 身份变了——%r" % (ids,)
    assert added == [False, True, False, False], (
        "#136：object_added 只在首次成桶（第 2 视角）为 True——%r" % added)
    assert updated == [False, False, True, True], (
        "#136：第 2 视角建对象、第 3/4 视角应标记 object_updated——%r" % updated)


def test_136_two_objects_still_two_world_objects():
    """非回归：两个真物体（同类、相距 > merge_dist）仍须产出 2 个世界物体。

    防「按类别去重」式的过度合并——修复以融合桶为身份，不得退化成按类别合并。
    """
    w = World3D()
    cams = [Camera3D(), Camera3D(cx=3.0, cz=-2.0, yaw=0.5)]
    truth = [(0.0, 1.2, 5.0), (2.5, 1.2, 5.0)]
    for cam in cams:
        for p in truth:
            px, py = cam.project(p, SW, SH)
            w.add_view("person", (px - 20, py - 20, px + 20, py + 20), SW, SH, camera=cam)
    assert len(w.objects) == 2, "#136：两个真物体被合并——%r" % [o.center for o in w.objects]


# ===========================================================================
# #311 盒体背面剔除
# ===========================================================================

_BOX_C = (200, 100, 50)
_BOX_SHADE = {"front": 0.95, "bottom": 0.6, "top": 1.15, "side": 0.85}


def _box_col(name):
    return tuple(min(255, int(v * _BOX_SHADE[name])) for v in _BOX_C)


def _front_face_ratio(center, size, cam=None):
    """前面（z-）投影区域内部 19×19 采样点中显示为「前面色」的比例。"""
    w = World3D(camera=cam) if cam else World3D()
    w.objects = [Object3D("crate", center=center, size=size, color=_BOX_C)]
    img = w.render(400, 300, background=(0, 0, 0), ground_color=(0, 0, 0))
    x, y, z = center
    hw, hh, hd = (s / 2 for s in size)
    ok = tot = 0
    for i in range(1, 20):
        for j in range(1, 20):
            p = w.camera.project((x - hw + 2 * hw * i / 20, y - hh + 2 * hh * j / 20, z - hd), 400, 300)
            if p is None or not (0 <= p[0] < 400 and 0 <= p[1] < 300):
                continue
            tot += 1
            if img.getpixel((int(p[0]), int(p[1]))) == _box_col("front"):
                ok += 1
    assert tot > 0, "前面投影区域无采样点（夹具失效）"
    return ok / tot


def test_311_hidden_faces_do_not_cover_front_face():
    """俯视/仰视近处立方体：前面投影区域须 100% 为前面色。

    缺陷形态（写死顺序画全 6 面、无剔除）：实测 0.579（俯视）/ 0.632（仰视），
    盖在前面上最多的是背对相机的侧面色。
    """
    r_down = _front_face_ratio((0, 0.5, 2.5), (1, 1, 1))     # 相机高 1.2m 俯视
    r_up = _front_face_ratio((0, 2.0, 3.0), (1, 1, 1))       # 立方体高于相机、仰视
    assert r_down > 0.99, "#311：俯视时背面盖住前面——前面色占比 %r（缺陷形态 0.579）" % r_down
    assert r_up > 0.99, "#311：仰视时背面盖住前面——前面色占比 %r（缺陷形态 0.632）" % r_up


def test_311_visible_faces_still_drawn():
    """防误杀：相机在右/上/下侧时，右面/顶面/底面照常绘制（不得过度剔除）。"""
    c = (0, 0.5, 3.0)
    size = (1, 1, 1)

    def render(cam):
        w = World3D(camera=cam)
        w.objects = [Object3D("crate", center=c, size=size, color=_BOX_C)]
        return w.render(400, 300, background=(0, 0, 0), ground_color=(0, 0, 0)), w

    for eye, face_pt, name in [
        ((4.0, 1.2, 3.0), (0.5, 0.5, 3.0), "side"),      # 右面
        ((0.0, 6.0, 3.0), (0.0, 1.0, 3.0), "top"),       # 顶面
        ((0.0, -3.0, 3.0), (0.0, 0.0, 3.0), "bottom"),   # 底面
    ]:
        cam = Camera3D.look_at(eye, c)
        img, w = render(cam)
        p = cam.project(face_pt, 400, 300)
        got = img.getpixel((int(p[0]), int(p[1])))
        assert got == _box_col(name), (
            "#311：可见的 %s 面被误剔除——像素 %r != %r" % (name, got, _box_col(name)))


# ===========================================================================
# #312 组合形状部件深度排序
# ===========================================================================

def test_312_far_leg_hidden_behind_dog_body():
    """dog 远侧腿（dz=+0.125，相机在原点朝 +z）应被身体挡住。

    缺陷形态（定义顺序绘制）：该像素为腿色 ×0.95 = (114,95,76)；
    修后（按相机深度排序）：为身体色 ×0.95 = (142,114,85)。
    """
    from lingshu.world.world3d import default_spec
    W, H = 800, 600
    center = (0.0, 0.5, 5.0)
    s = default_spec("dog")
    parts = get_shape("dog")
    w = World3D()
    w.objects = [Object3D("dog", center=center, size=s.size, color=s.color, shape=s.shape)]
    img = w.render(W, H, background=(0, 0, 0), ground_color=(0, 0, 0))
    far = parts[2]                                   # 远侧腿（dz=+0.125）
    pc = tuple(center[k] + far["pos"][k] for k in range(3))
    pc = (pc[0], pc[1] + far["size"][1] / 3, pc[2])  # 腿的上段（与身体在画面上重叠）
    p = w.camera.project(pc, W, H)
    got = img.getpixel((int(p[0]), int(p[1])))
    body_shade = tuple(min(255, int(v * 0.95)) for v in parts[0]["color"])
    leg_shade = tuple(min(255, int(v * 0.95)) for v in far["color"])
    assert got == body_shade, (
        "#312：远侧腿盖在身体上——像素 %r（腿色 %r / 身体色 %r）" % (got, leg_shade, body_shade))


def test_312_nested_parts_not_flipped_tree():
    """嵌套部件不得翻转：树冠底部仍须盖住树干。

    相机在树下方仰视（cy=−1.5, cz=8, pitch=0.4）：树干插进树冠，若排序键用
    **部件中心距**，树干（更近的中心）会被画到树冠前面——本项即红。
    """
    from lingshu.world.world3d import default_spec
    W, H = 400, 300
    cam = Camera3D(cy=-1.5, cz=8.0, pitch=0.4)
    center = (0.0, 0.0, 10.0)
    s = default_spec("tree")
    parts = get_shape("tree")
    w = World3D(camera=cam)
    w.objects = [Object3D("tree", center=center, size=s.size, color=s.color, shape=s.shape)]
    img = w.render(W, H, background=(0, 0, 0), ground_color=(0, 0, 0))
    # 树干轴上、落在树冠包围盒内的一点 → 该视线先穿树冠，像素应为树冠色
    p = cam.project((center[0], center[1], center[2]), W, H)
    got = img.getpixel((int(p[0]), int(p[1])))
    crown = tuple(parts[1]["color"])
    trunk_shade = tuple(min(255, int(v * 0.95)) for v in parts[0]["color"])
    assert got == crown, (
        "#312：嵌套部件被翻转（树干画到树冠前面）——像素 %r（树冠色 %r / 树干色 %r）"
        % (got, crown, trunk_shade))


# ===========================================================================
# #313 金字塔塔尖投影（不写死像素高度）
# ===========================================================================

def _pyramid_rows(height, cam=None):
    W, H = 400, 300
    w = World3D(camera=cam) if cam else World3D()
    w.objects = [Object3D("pyr", center=(0, height / 2, 8), size=(2, height, 2),
                          color=(255, 0, 0), shape="pyramid")]
    img = w.render(W, H, background=(0, 0, 0), ground_color=(0, 0, 0))
    rows = [y for y in range(H) for x in range(W) if img.getpixel((x, y))[0] > 100]
    assert rows, "金字塔未渲染"
    return min(rows), max(rows), w.camera


def test_313_apex_row_matches_projection():
    """塔尖行须等于 project((x, y+h/2, z)) 的行（±1.5px）——不写死像素常数。

    缺陷形态（apex = 底面投影均值 − 30）：h=0.5 时塔尖行 172，而投影行 180.3。
    """
    for h in (0.5, 2.0):
        top, _, cam = _pyramid_rows(h)
        apex_px = cam.project((0.0, h, 8.0), 400, 300)
        assert abs(top - apex_px[1]) < 1.5, (
            "#313：塔尖行 %r != 投影行 %.2f（h=%.1f）" % (top, apex_px[1], h))


def test_313_screen_height_grows_with_height():
    """金字塔屏幕高度须随真实高度单调增（缺陷形态三者恒 37px）。"""
    hs = []
    for h in (0.5, 2.0, 8.0):
        top, bottom, _ = _pyramid_rows(h)
        hs.append(bottom - top)
    assert hs[0] < hs[1] < hs[2], "#313：金字塔屏幕高度不随 size[1] 变化——%r" % (hs,)
    # 与同尺寸 box 的对照量级一致（金字塔略矮，因塔尖在顶面之上、底在同高处）
    assert hs[2] >= 100, "#313：8m 金字塔屏幕高度过小——%r" % hs[2]


# ===========================================================================
# 自身可收集 + 直跑入口
# ===========================================================================

def test_guard_is_collectable():
    assert os.path.basename(__file__).startswith("test_")
    assert World3D is not None and Object3D is not None and Camera3D is not None


def _main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print("FAIL %-58s %s" % (t.__name__, e))
        else:
            passed += 1
            print("ok   %s" % t.__name__)
    print("\n%d/%d passed" % (passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_main())
