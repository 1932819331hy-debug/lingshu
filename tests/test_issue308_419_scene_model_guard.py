# -*- coding: utf-8 -*-
"""#308 / #419 守卫：scene_model 呈现通道与跨表示遮挡（本组件号 308、419）。

逐件钉住「缺陷不再存在」，而非「代码能跑」：

#308（world/scene_model · present=skeleton 的实体从不画骨架）
    症状：`build()` 的 skeleton 分支摆好 `Skeleton3D` 姿态后**只**把
    `_skeleton_to_object(...)` 的 0.6×1.7×0.4 米色 pillar 占位盒塞进
    World3D，`sk` 本体被丢弃；`render()` 里没有骨架通道（`bones_overlay`
    只给 fatfish 叠线）。结果 person/girl 实体渲染成一根米色盒柱，
    state=happy/tired 的姿态代码是死代码。
    修法：`build()` 把骨架存进 `self._skeletons`（不再进 World3D）；
    `render()` 在盒体之后、轮廓之前画骨骼线（复用 `_draw_bones`）。
    守卫 G1：person 实体 ⇒ World3D 无 `skeleton_*` 占位盒、盒柱色像素 = 0、
    骨骼线像素 > 0（骨架真的画出来了）。
    守卫 G2：state=neutral 与 happy 渲染逐字节不同（姿态不再是死代码），
    且 happy 的 `shoulder_r` 旋转确实被应用到骨架本体上。
    守卫 G3：盒体实体（table）不受影响——仍进 World3D、仍有像素产出。
    变异（把占位盒塞回 World3D 并去掉 render 的骨架通道）⇒ G1/G2 红。

#419（world/渲染 · scene_model 最后无条件叠加 silhouette）
    症状：`render()` 先画 World3D 实体，再把全部 silhouette 角色 alpha
    paste 到最终图像（`img.paste(layer, (0, 0), layer)`），缺跨表示的深度
    遮挡——角色在墙/房后仍穿墙显示。
    修法：paste 前按盒体深度（同一画家算法深度键 `Object3D.depth(cam)`）
    把更近实体的屏幕足迹剔出角色 alpha。
    守卫 G4：角色完全在不透明墙后 ⇒ 墙面像素**零变化**（且中心像素 = 只有
    墙时的渲染）；**同一夹具的对照项**要求「只有角色、没有墙」时该角色确实
    改变像素——否则把 silhouette 通道整个删掉也能让 G4 变绿（代偿）。
    守卫 G5：角色在墙**前** ⇒ 角色仍可见（防过杀：遮挡不得吃掉本该可见的角色）。
    变异（改回 `img.paste(layer, (0, 0), layer)`）⇒ G4 红、G5 仍绿。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue308_419_scene_model_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
from collections import Counter

from PIL import ImageChops

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.world.scene_model import WorldModel                     # noqa: E402
from lingshu.world.world3d import Camera3D, Object3D, World3D        # noqa: E402

# `_draw_bones` 的线条色（scene_model._draw_bones fill=(40, 60, 90)）
BONE_RGB = (40, 60, 90)
# 旧缺陷形态的占位盒颜色（_skeleton_to_object color=(200, 160, 140)）及其明暗变体
PILLAR_RGBS = {(190, 152, 133), (200, 160, 140), (133, 106, 93),
               (170, 136, 119), (230, 184, 161)}

WALL_RGB = (38, 85, 171)


def _count_rgb(img, rgbs):
    cnt = Counter(img.tobytes()[i:i + 3] for i in range(0, len(img.tobytes()), 3))
    return sum(v for k, v in cnt.items() if tuple(k) in rgbs)


def _diff_pixels(a, b):
    """任一 RGB 通道差 > 12 的像素数——只用 PIL，不引 numpy。"""
    r, g, bl = ImageChops.difference(a.convert("RGB"),
                                     b.convert("RGB")).split()
    m = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    return m.point(lambda v: 255 if v > 12 else 0).histogram()[255]


def _wall(camera):
    """复现基准（#419 复现记录）：不透明墙，中心 (0,0.85,4)，尺寸 (6,6,0.2)。"""
    return Object3D(category="wall", center=(0, 0.85, 4.0), size=(6, 6, 0.2),
                    color=WALL_RGB, shape="box")


def _wall_only_render(wall, camera):
    """只有墙的 World3D 渲染——与 WorldModel 同底（背景/地面色一致）。"""
    w = World3D()
    w.objects.append(wall)
    return w.render(320, 320, camera=camera, background=(250, 248, 244),
                    ground_color=(235, 230, 220))


# ===========================================================================
# #308：present=skeleton 的实体必须画骨架，不画占位盒
# ===========================================================================
def test_g1_skeleton_entity_draws_bones_not_placeholder_box():
    """G1：person 实体 ⇒ 无 skeleton_* 占位盒、无盒柱色像素、有骨骼线像素。"""
    wm = WorldModel()
    wm.add_entity("访客", "person", pos=(0, 0.85, 5.0), state="neutral")
    wm.build()

    cats = [o.category for o in wm.scene.objects]
    assert not any(c.startswith("skeleton_") for c in cats), (
        "G1 占位盒仍在 World3D（旧缺陷行为）：%r" % (cats,))
    assert "访客" in wm._skeletons, (
        "G1 骨架本体未进入呈现通道：%r" % (list(wm._skeletons),))

    img = wm.render(300, 300)
    pillar = _count_rgb(img, PILLAR_RGBS)
    bone = _count_rgb(img, {BONE_RGB})
    assert pillar == 0, "G1 渲染出盒柱色像素 %d（旧缺陷行为：画的是占位盒）" % pillar
    assert bone > 0, "G1 骨架线像素为 0（骨架从未画出，旧缺陷行为）"


def test_g2_state_pose_changes_render():
    """G2：neutral 与 happy 渲染不同，且姿态确实落在骨架本体上（非死代码）。"""
    def build(state):
        wm = WorldModel()
        wm.add_entity("访客", "person", pos=(0, 0.85, 5.0), state=state)
        wm.build()
        return wm

    wm_n, wm_h = build("neutral"), build("happy")
    img_n = wm_n.render(300, 300)
    img_h = wm_h.render(300, 300)
    assert img_n.tobytes() != img_h.tobytes(), (
        "G2 state=neutral 与 happy 渲染逐字节相同（姿态是死代码，旧缺陷行为）")

    sk = wm_h._skeletons["访客"]
    assert sk.joints["shoulder_r"].rot == (0, 0, 1.2), (
        "G2 happy 的挥手姿态未落在被渲染的骨架上：%r" % (sk.joints["shoulder_r"].rot,))

    # 对照：tired（低头）也必须是另一张图，且不同于 happy
    wm_t = build("tired")
    assert wm_t.render(300, 300).tobytes() not in (img_n.tobytes(), img_h.tobytes()), (
        "G2 tired 姿态未改变渲染（姿态分支未生效）")


def test_g3_box_entities_unaffected():
    """G3：盒体实体照旧进 World3D 并渲染（本修复不误伤盒体通道）。"""
    wm = WorldModel()
    wm.add_entity("桌子", "table", pos=(1.5, 0.45, 6.0))
    wm.build()
    assert [(o.category, o.shape) for o in wm.scene.objects] == [("table", "box")], (
        "G3 盒体实体未进 World3D：%r" % ([o.category for o in wm.scene.objects],))

    img = wm.render(300, 300)
    # 同一相机（render 的缺省 look_at）下的空场景作为底：盒体实体必须改变像素
    cam = Camera3D.look_at(eye=(0, 1.2, 1.5), target=(0, 0.9, 5.0), fov_deg=50)
    empty = World3D().render(300, 300, camera=cam, background=(250, 248, 244),
                             ground_color=(235, 230, 220))
    assert _diff_pixels(img, empty) > 0, "G3 盒体实体渲染未改变任何像素（盒体通道被误伤）"


# ===========================================================================
# #419：跨表示（轮廓角色 vs World3D 盒体）深度遮挡
# ===========================================================================
def test_g4_silhouette_behind_opaque_wall_changes_nothing():
    """G4：角色全在墙后 ⇒ 墙面像素零变化；对照项证明角色层本身会改像素。"""
    cam = Camera3D.look_at(eye=(0, 0.85, 0), target=(0, 0.85, 8), fov_deg=50)
    wall = _wall(cam)
    wall_only = _wall_only_render(wall, cam)

    # 角色中心 (0,0.85,8)，墙中心 (0,0.85,4)：角色几何全部在墙后
    wm = WorldModel()
    wm.add_entity("肥鱼", "fatfish", pos=(0, 0.85, 8.0))
    wm.build()
    wm.scene.objects.append(wall)
    behind = wm.render(320, 320, camera=cam)

    changed = _diff_pixels(behind, wall_only)
    assert changed == 0, (
        "G4 墙后角色穿墙显示：changed_pixels_behind_opaque_wall = %d（旧缺陷读数 797）"
        % changed)
    assert behind.getpixel((160, 160)) == wall_only.getpixel((160, 160)), (
        "G4 墙面中心像素被墙后角色改写：%r vs 只有墙 %r"
        % (behind.getpixel((160, 160)), wall_only.getpixel((160, 160))))

    # 对照项（去代偿）：同一角色在无墙场景里必须改变像素——否则把轮廓通道整个
    # 删掉也能让上面的断言变绿，两条守卫互相代偿、双双失去判别力。
    wm_no_wall = WorldModel()
    wm_no_wall.add_entity("肥鱼", "fatfish", pos=(0, 0.85, 8.0))
    wm_no_wall.build()
    empty = World3D().render(320, 320, camera=cam, background=(250, 248, 244),
                             ground_color=(235, 230, 220))
    assert _diff_pixels(wm_no_wall.render(320, 320, camera=cam), empty) > 0, (
        "G4 对照项失败：无墙时角色层本应改变像素，现为 0——轮廓通道可能已被删除")


def test_g5_silhouette_in_front_of_wall_still_visible():
    """G5：角色在墙前 ⇒ 仍可见（遮挡不得过杀本该可见的角色）。"""
    cam = Camera3D.look_at(eye=(0, 0.85, 0), target=(0, 0.85, 8), fov_deg=50)
    wall = _wall(cam)
    wall_only = _wall_only_render(wall, cam)

    wm = WorldModel()
    wm.add_entity("肥鱼", "fatfish", pos=(0, 0.85, 2.0))
    wm.build()
    wm.scene.objects.append(wall)
    front = wm.render(320, 320, camera=cam)

    changed = _diff_pixels(front, wall_only)
    assert changed > 1000, (
        "G5 墙前角色被误遮：changed_pixels = %d（应大面积可见）" % changed)
