# -*- coding: utf-8 -*-
"""_det/demo_scene.py · 演示场景（**真 3D 几何**，用 `vec.prims` 程序化生成）
================================================================================
用途：给 SVG 产线一个"值得出海报"的主体——一台悬停在六边形平台上的巡检机。
所有几何都由 `prims` 生成后在 Blender 里建网格，故 **Blender 渲染的**与 **SVG 导出的**
是同一份顶点/面（同源，口径不会漂移）。

坐标：Z-up、米。
"""
from __future__ import annotations

import math

from wm_bridge.factory.vec import prims as P
from wm_bridge.factory.vec.bpy_vec import ROLE_COLORS

#: 调色（线性 0..1）
C = {
    "hull": (0.78, 0.28, 0.075),        # 主体橙红涂装
    "hull_dark": (0.20, 0.115, 0.06),   # 深色涂装
    "metal": (0.42, 0.45, 0.50),
    "metal_light": (0.62, 0.66, 0.72),
    "rubber": (0.045, 0.048, 0.055),
    "glass": (0.30, 0.70, 0.86),
    "emissive": (0.22, 0.92, 1.0),
    "stone": (0.13, 0.145, 0.175),
    "stone_dark": (0.085, 0.095, 0.12),
    "fabric": (0.20, 0.28, 0.46),
}


def _rot_x(deg):
    return (deg, 0.0, 0.0)


def build_items():
    """→ `[(name, Mesh, 线性色, role)]`（顺序 = 建对象顺序）。"""
    items = []

    def add(name, mesh, color, role="painted_metal"):
        items.append((name, mesh, color, role))

    # ---------------------------------------------------------- 地面（瓦片 → 缝即线）
    tile, gap, n = 2.45, 0.09, 5
    span = n * tile + (n - 1) * gap
    for i in range(n):
        for j in range(n):
            x = -span / 2 + tile / 2 + i * (tile + gap)
            y = -span / 2 + tile / 2 + j * (tile + gap)
            k = (i * 7 + j * 13) % 3
            col = (C["stone"], C["stone_dark"], (0.155, 0.17, 0.20))[k]
            add("ground_tile_%d_%d" % (i, j),
                P.transform(P.box((tile, tile, 0.22), "tile"),
                            translate=(x, y, -0.11)),
                col, "stone")

    # ---------------------------------------------------------- 平台
    add("platform_base", P.cylinder(4.05, 0.46, 6, name="hex"), C["metal"], "metal")
    add("platform_deck", P.transform(P.cylinder(3.62, 0.16, 6, name="deck"),
                                     translate=(0, 0, 0.46)), C["hull_dark"], "painted_metal")
    for i in range(6):
        a = math.pi / 3 * i + math.pi / 6
        add("platform_pad_%d" % i,
            P.transform(P.box((1.0, 0.5, 0.12), "pad"),
                        translate=(3.0 * math.cos(a), 3.0 * math.sin(a), 0.615),
                        rot_deg=(0, 0, math.degrees(a))),
            C["metal_light"], "metal")

    # ---------------------------------------------------------- 立柱 + 躯干
    add("pillar", P.cylinder(0.46, 2.55, 16, name="pillar"), C["metal"], "metal")
    add("pillar_ring", P.transform(P.torus(0.60, 0.11, 24, 10, "ring"),
                                   translate=(0, 0, 1.35)), C["metal_light"], "metal")
    add("torso", P.transform(P.box((2.35, 1.65, 1.45), "torso"),
                             translate=(0, 0, 3.90), rot_deg=(0, 0, 0)),
        C["hull"], "painted_metal")
    add("torso_top", P.transform(P.box((2.05, 1.35, 0.28), "torso_top"),
                                 translate=(0, 0, 4.66)), C["metal"], "metal")
    add("torso_belt", P.transform(P.box((2.45, 1.75, 0.16), "belt"),
                                  translate=(0, 0, 3.30)), C["hull_dark"], "painted_metal")
    add("chest_core", P.transform(P.torus(0.60, 0.11, 28, 10, "core"),
                                  translate=(0, -0.86, 3.98), rot_deg=(90, 0, 0)),
        C["emissive"], "emissive")
    add("chest_plate", P.transform(P.box((1.05, 0.16, 0.75), "plate"),
                                   translate=(0, -0.86, 3.98)), C["metal_light"], "metal")

    # ---------------------------------------------------------- 肩/臂/手
    for side, sx in (("l", -1), ("r", 1)):
        add("shoulder_" + side,
            P.transform(P.sphere(0.44, 18, 10, "sh"),
                        translate=(sx * 1.30, 0, 4.42)), C["metal"], "metal")
        add("upper_arm_" + side,
            P.transform(P.cylinder(0.22, 1.25, 12, name="ua"),
                        translate=(sx * 1.62, 0.02, 3.35), rot_deg=(0, -sx * 14, 0)),
            C["metal_light"], "metal")
        add("forearm_" + side,
            P.transform(P.cylinder(0.26, 1.05, 12, name="fa"),
                        translate=(sx * 1.98, 0.02, 2.55), rot_deg=(0, -sx * 8, 0)),
            C["hull"], "painted_metal")
        add("hand_" + side,
            P.transform(P.box((0.40, 0.44, 0.50), "hand"),
                        translate=(sx * 2.14, 0.02, 1.85)), C["rubber"], "rubber")

    # ---------------------------------------------------------- 头/面罩/光环
    add("head", P.transform(P.sphere(0.80, 26, 14, "head"), translate=(0, 0, 5.32)),
        C["metal_light"], "metal")
    add("visor", P.transform(P.box((0.92, 0.22, 0.34), "visor"),
                             translate=(0, -0.66, 5.36)), C["emissive"], "emissive")
    add("crest", P.transform(P.box((0.16, 1.05, 0.42), "crest"),
                             translate=(0, 0.10, 5.92)), C["hull"], "painted_metal")
    add("halo", P.transform(P.torus(1.42, 0.075, 40, 10, "halo"),
                            translate=(0, 0, 6.30), rot_deg=(8, 0, 0)),
        C["emissive"], "emissive")
    for side, sx in (("l", -1), ("r", 1)):
        add("fin_" + side,
            P.transform(P.box((0.10, 0.95, 1.05), "fin"),
                        translate=(sx * 1.26, 0.30, 4.85), rot_deg=(0, 0, -sx * 9)),
            C["hull_dark"], "painted_metal")

    # ---------------------------------------------------------- 前景浮块（给"前景"层用）
    for i, (x, y, z, s) in enumerate(((3.9, -2.6, 6.6, 0.30), (-4.2, -1.4, 7.1, 0.24),
                                      (4.4, 2.4, 5.9, 0.20), (-3.4, 3.1, 6.9, 0.27),
                                      (1.6, -4.4, 7.6, 0.22))):
        add("float_%d" % i,
            P.transform(P.box((s, s, s), "f"),
                        translate=(x, y, z), rot_deg=(25 + i * 11, 18 + i * 7, i * 23)),
            C["emissive"] if i % 2 == 0 else C["metal_light"],
            "emissive" if i % 2 == 0 else "metal")
    return items


def build_scene(clear=True):
    """在**当前** Blender 场景里建出演示场景，返回对象列表。"""
    import bpy
    from wm_bridge.factory.vec.bpy_vec import scene_from_meshes

    if clear:
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
        for me in list(bpy.data.meshes):
            bpy.data.meshes.remove(me)
    items = build_items()
    objs = scene_from_meshes(items)
    for ob in objs:
        ob.select_set(False)
    return objs, items


def add_camera_and_lights(eye=(8.6, -10.6, 7.0), look=(0.0, 0.25, 3.75),
                          lens=52.0):
    """相机 + 三点光。相机朝向用 track-to 语义手算（不依赖约束，便于复现）。"""
    import bpy
    from mathutils import Vector

    cam_data = bpy.data.cameras.new("vec_cam")
    cam_data.lens = float(lens)
    cam_data.sensor_width = 36.0
    cam = bpy.data.objects.new("vec_cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    cam.location = Vector(eye)
    d = Vector(look) - Vector(eye)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()

    lights = (("key", (6.5, -7.5, 9.0), 1400.0, 4.0),
              ("fill", (-7.5, -4.0, 4.0), 420.0, 6.0),
              ("rim", (-2.0, 8.5, 6.5), 900.0, 4.0))
    for name, loc, power, size in lights:
        ld = bpy.data.lights.new(name, type="AREA")
        ld.energy = power
        ld.size = size
        lo = bpy.data.objects.new(name, ld)
        bpy.context.scene.collection.objects.link(lo)
        lo.location = Vector(loc)
        dd = Vector((0, 0, 3.2)) - Vector(loc)
        lo.rotation_euler = dd.to_track_quat("-Z", "Y").to_euler()
    return cam


def setup_world(color=(0.035, 0.042, 0.062)):
    import bpy
    w = bpy.context.scene.world or bpy.data.worlds.new("vec_world")
    bpy.context.scene.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (color[0], color[1], color[2], 1.0)
        bg.inputs[1].default_value = 0.6
