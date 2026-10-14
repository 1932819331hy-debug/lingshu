# -*- coding: utf-8 -*-
"""Lead 的美学标杆实验：设计过的三光源主视觉渲染（对照组 = 默认场景）

目的：把「呈现效果」拆成**可执行的手法**，而不是形容词。
手法清单（每一条都可在报告里被别的成员照抄）：
  1. 三光源：key(强,偏侧上) / fill(弱,对侧) / rim(强,背后偏侧) —— rim 用于把主体从背景"抠"出来
  2. 背景：渐变幕布（不用世界纯色），与主体色相拉开
  3. 相机：微俯 + 轻微透视，主体占画面 60~75%
  4. 色彩管理：AgX（5.2 的默认 view transform 是 AgX；记录选型）
  5. 材质：金属 / 涂装金属 / 橡胶 / 自发光 四种角色，几何与光照相同 ⇒ 差异**只来自材质**
  6. 地面：带轻微反射的哑光地台（产生接触阴影，这是"落地感"的关键）
"""
import json
import math
import os

import bpy

OUT = os.environ.get("DSH_OUT") or "/tmp"
try:
    #: headless 预置内核：把 WSL 路径归一成 Windows 形式（否则 /tmp 会被当成 C:\tmp）
    OUT = K.win_path(OUT)                                        # noqa: F821
except Exception:                                                # noqa: BLE001
    pass
os.makedirs(OUT, exist_ok=True)


def purge():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def mat_principled(name, base, rough, metal=0.0, emis=None, emis_str=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    b.inputs["Base Color"].default_value = (base[0], base[1], base[2], 1.0)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emis is not None:
        b.inputs["Emission Color"].default_value = (emis[0], emis[1], emis[2], 1.0)
        b.inputs["Emission Strength"].default_value = emis_str
    return m


def add_light(name, kind, loc, energy, rot=(0, 0, 0), size=1.0, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, type=kind)
    d.energy = energy
    d.color = color
    if kind == "AREA":
        d.size = size
    ob = bpy.data.objects.new(name, d)
    ob.location = loc
    ob.rotation_euler = rot
    bpy.context.scene.collection.objects.link(ob)
    return ob


def look_at(ob, target):
    import mathutils
    d = mathutils.Vector(target) - ob.location
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


# ───────────────────────────────────────── 场景
purge()
sc = bpy.context.scene
sc.render.engine = "BLENDER_EEVEE"
sc.render.resolution_x, sc.render.resolution_y = 1280, 720
sc.render.film_transparent = False
try:
    sc.view_settings.view_transform = "AgX"
except TypeError:
    sc.view_settings.view_transform = "Filmic"
sc.view_settings.look = "AgX - Punchy" if "AgX" in sc.view_settings.view_transform else "None"
sc.view_settings.exposure = 0.15

# 地台（哑光 + 微弱反射）
bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 0, -0.001))
floor = bpy.context.active_object
floor.name = "FLOOR"
floor.data.materials.append(mat_principled("M_floor", (0.055, 0.06, 0.075), 0.62))

# 主体：四种材质角色，同一几何（圆角柱）+ 一个基座
roles = [
    ("metal",        (0.62, 0.64, 0.68), 0.18, 1.0, None, 0.0),
    ("painted_metal",(0.72, 0.22, 0.10), 0.42, 0.15, None, 0.0),
    ("rubber",       (0.045, 0.045, 0.05), 0.86, 0.0, None, 0.0),
    ("emissive",     (0.05, 0.05, 0.06), 0.30, 0.0, (0.25, 0.85, 1.0), 6.0),
]
objs = []
for i, (role, base, rough, metal, emis, es) in enumerate(roles):
    x = (i - 1.5) * 1.5
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.34, depth=1.15,
                                        location=(x, 0, 0.575))
    ob = bpy.context.active_object
    ob.name = "OBJ_" + role
    ob.data.materials.append(mat_principled("M_" + role, base, rough, metal, emis, es))
    # 倒角，让高光有"边"，硬表面才有可信度
    bm = ob.modifiers.new("bevel", "BEVEL")
    bm.width = 0.022
    bm.segments = 3
    objs.append(ob)

# 基座（把四个件"锚"在一条线上，构图更稳）
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.06))
base = bpy.context.active_object
base.name = "BASE"
base.scale = (3.55, 0.62, 0.12)
base.data.materials.append(mat_principled("M_base", (0.10, 0.105, 0.12), 0.35, 0.6))

# ───────────────────────────────────────── 三光源
add_light("KEY", "AREA", (-3.2, -3.6, 4.2), 1500, size=4.0, color=(1.0, 0.96, 0.90))
add_light("FILL", "AREA", (4.0, -2.4, 2.0), 260, size=5.0, color=(0.80, 0.86, 1.0))
add_light("RIM", "AREA", (2.6, 4.2, 3.0), 900, size=2.5, color=(0.72, 0.88, 1.0))
for nm, tgt in (("KEY", (0, 0, 0.6)), ("FILL", (0, 0, 0.6)), ("RIM", (0, 0, 0.9))):
    look_at(bpy.data.objects[nm], tgt)

# ───────────────────────────────────────── 背景幕布（渐变，与主体色相拉开）
world = bpy.data.worlds.new("W")
sc.world = world
world.use_nodes = True
wnt = world.node_tree
wnt.nodes.clear()
bg = wnt.nodes.new("ShaderNodeBackground")
grad = wnt.nodes.new("ShaderNodeTexGradient")
ramp = wnt.nodes.new("ShaderNodeValToRGB")
mapping = wnt.nodes.new("ShaderNodeMapping")
texco = wnt.nodes.new("ShaderNodeTexCoord")
out = wnt.nodes.new("ShaderNodeOutputWorld")
mapping.inputs["Rotation"].default_value[1] = math.radians(90)
ramp.color_ramp.elements[0].position = 0.0
ramp.color_ramp.elements[0].color = (0.035, 0.045, 0.075, 1)
ramp.color_ramp.elements[1].position = 1.0
ramp.color_ramp.elements[1].color = (0.16, 0.11, 0.13, 1)
bg.inputs["Strength"].default_value = 0.9
ln = wnt.links
ln.new(texco.outputs["Generated"], mapping.inputs["Vector"])
ln.new(mapping.outputs["Vector"], grad.inputs["Vector"])
ln.new(grad.outputs["Fac"], ramp.inputs["Fac"])
ln.new(ramp.outputs["Color"], bg.inputs["Color"])
ln.new(bg.outputs["Background"], out.inputs["Surface"])

# ───────────────────────────────────────── 相机（微俯 + 轻透视）
cam_d = bpy.data.cameras.new("CAM")
cam_d.lens = 52
cam = bpy.data.objects.new("CAM", cam_d)
cam.location = (0.0, -6.6, 2.35)
bpy.context.scene.collection.objects.link(cam)
look_at(cam, (0, 0, 0.72))
sc.camera = cam

# 采样与输出
sc.eevee.taa_render_samples = 128
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_mode = "RGB"
png = os.path.join(OUT, "lead_hero_v1.png")
sc.render.filepath = png
bpy.ops.render.render(write_still=True)

# 读回重建：核对实际落盘路径（Windows Blender 对 POSIX 路径的语义差异）
import glob
cands = glob.glob(os.path.join(OUT, "lead_hero_v1.png")) or \
    glob.glob(os.path.join(os.path.dirname(png), "lead_hero_v1*"))
print("HEADLESS " + json.dumps({
    "engine": sc.render.engine,
    "view_transform": sc.view_settings.view_transform,
    "look": sc.view_settings.look,
    "res": [sc.render.resolution_x, sc.render.resolution_y],
    "samples": sc.eevee.taa_render_samples,
    "out": png,
    "exists": os.path.exists(png),
    "bytes": os.path.getsize(png) if os.path.exists(png) else 0,
    "cands": cands,
    "objects": len(bpy.data.objects),
    "materials": len(bpy.data.materials),
}, ensure_ascii=False))
