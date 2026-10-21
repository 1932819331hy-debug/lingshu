# -*- coding: utf-8 -*-
"""探测：① 本机可用 view_transform ② Material 属性面（use_nodes 替代） ③ EEVEE 逐字节确定性。

产出（outdir 下）：
    det_a1.png det_a2.png   同配置连渲两张（同进程）
    det_b1.png              另一次进程/会话渲的第三张
    probe_eevee.json        全部读数
"""
# 注意：本文件由 blender_rt_headless script_file 执行，外层会拼装前导，
# 因此**不能**写 `from __future__ import annotations`（会不在文件首行）。
import hashlib
import json
import os
import sys
import time

import bpy

OUT = os.environ.get("SHADE_OUT") or r"<外部产物根>\wmopt\shade"
os.makedirs(OUT, exist_ok=True)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


R = {}

# ── 0. use_nodes 替代口径（5.2：node_tree 隐式创建）
m = bpy.data.materials.new("probe_implicit")
R["node_tree_implicit_created"] = m.node_tree is not None
R["node_tree_node_count"] = len(m.node_tree.nodes) if m.node_tree else -1
R["node_tree_node_types"] = [n.type for n in m.node_tree.nodes] if m.node_tree else []
R["material_surface_render_method_choices"] = [
    i.identifier for i in bpy.types.Material.bl_rna.properties["surface_render_method"].enum_items
] if "surface_render_method" in bpy.types.Material.bl_rna.properties else None
R["material_props_relevant"] = sorted(
    x for x in dir(bpy.types.Material.bl_rna.properties)
    if any(k in x for k in ("blend", "shadow", "backface", "surface", "shadow_method"))
)

# ── 1. view_transform 试写 + 读回
vs = bpy.context.scene.view_settings


def vt_options():
    cur = str(vs.view_transform)
    ok = []
    for c in ("Standard", "AgX", "Filmic", "Filmic Log", "Khronos PBR Neutral", "Raw",
              "False Color", "Linear", "Linear Rec.709", "sRGB", "None"):
        try:
            vs.view_transform = c
            if str(vs.view_transform) == c and c not in ok:
                ok.append(c)
        except Exception:
            pass
    vs.view_transform = cur
    return ok


R["view_transforms_available"] = vt_options()
R["view_transform_default"] = str(vs.view_transform)
R["look_default"] = str(vs.look)

# ── 2. 搭一个最小测试场景（球 + 三光源 + 相机 + 渐变世界）
sc = bpy.context.scene
sc.render.engine = "BLENDER_EEVEE"
ee = sc.eevee
ee.use_raytracing = True
try:
    ee.ray_tracing_method = "SCREEN"
except Exception as e:
    R["rt_method_err"] = str(e)[:80]

# 清场景
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=48, ring_count=24, location=(0, 0, 1.0))
sp = bpy.context.object
mm = bpy.data.materials.new("probe_surf")
nt = mm.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
bsdf.inputs["Base Color"].default_value = (0.85, 0.3, 0.15, 1.0)
bsdf.inputs["Metallic"].default_value = 0.0
bsdf.inputs["Roughness"].default_value = 0.35
sp.data.materials.append(mm)

bpy.ops.mesh.primitive_plane_add(size=40.0, location=(0, 0, 0))
gnd = bpy.context.object
gm = bpy.data.materials.new("probe_gnd")
gnt = gm.node_tree
gb = next(n for n in gnt.nodes if n.type == "BSDF_PRINCIPLED")
gb.inputs["Base Color"].default_value = (0.06, 0.08, 0.11, 1.0)
gb.inputs["Roughness"].default_value = 0.85
gnd.data.materials.append(gm)

for name, (loc, energy, radius, color) in {
    "key": ((3.2, -3.6, 4.4), 900.0, 3.0, (1.0, 0.92, 0.82)),
    "fill": ((-4.4, -1.8, 2.0), 220.0, 5.0, (0.75, 0.85, 1.0)),
    "rim": ((-1.4, 4.6, 3.4), 700.0, 2.0, (0.7, 0.85, 1.0)),
}.items():
    ld = bpy.data.lights.new(name, type="AREA")
    ld.energy = energy
    ld.size = radius
    ld.color = color
    lo = bpy.data.objects.new(name, ld)
    lo.location = loc
    sc.collection.objects.link(lo)
    d = (0.0, 0.0, 1.0)
    import mathutils
    lo.rotation_euler = (mathutils.Vector(d) - mathutils.Vector(loc)).to_track_quat("-Z", "Y").to_euler()

camd = bpy.data.cameras.new("cam")
camd.lens = 50.0
cam = bpy.data.objects.new("cam", camd)
cam.location = (4.2, -5.6, 3.4)
import mathutils
d = (mathutils.Vector((0, 0, 1.0)) - mathutils.Vector(cam.location)).to_track_quat("-Z", "Y").to_euler()
cam.rotation_euler = d
sc.collection.objects.link(cam)
sc.camera = cam

# 世界：渐变背景
w = bpy.data.worlds.new("probe_world")
sc.world = w
w.node_tree  # 隐式创建
wnt = w.node_tree
wnt.nodes.clear()
w_out = wnt.nodes.new("ShaderNodeOutputWorld")
w_bg = wnt.nodes.new("ShaderNodeBackground")
w_bg.inputs["Strength"].default_value = 0.25
tc = wnt.nodes.new("ShaderNodeTexCoord")
sep = wnt.nodes.new("ShaderNodeSeparateXYZ")
ramp = wnt.nodes.new("ShaderNodeValToRGB")
el = ramp.color_ramp.elements
el[0].position = 0.0
el[0].color = (0.02, 0.05, 0.09, 1.0)
e2 = el.new(1.0)
e2.color = (0.32, 0.14, 0.06, 1.0)
wnt.links.new(tc.outputs["Window"], sep.inputs["Vector"])
wnt.links.new(sep.outputs["Y"], ramp.inputs["Fac"])
wnt.links.new(ramp.outputs["Color"], w_bg.inputs["Color"])
wnt.links.new(w_bg.outputs["Background"], w_out.inputs["Surface"])

# ── 3. 确定性配置
sc.render.resolution_x, sc.render.resolution_y = 480, 270
sc.render.resolution_percentage = 100
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_mode = "RGBA"
sc.render.image_settings.compression = 15
sc.render.film_transparent = False
vs.view_transform = "Standard"
ee.taa_render_samples = 32
DET_SNAPSHOT = {
    "engine": sc.render.engine,
    "taa_render_samples": ee.taa_render_samples,
    "use_taa_reprojection": ee.use_taa_reprojection,
    "use_shadow_jitter_viewport": ee.use_shadow_jitter_viewport,
    "use_bokeh_jittered": ee.use_bokeh_jittered,
    "use_raytracing": ee.use_raytracing,
    "ray_tracing_method": getattr(ee, "ray_tracing_method", None),
    "view_transform": str(vs.view_transform),
    "film_transparent": sc.render.film_transparent,
}
R["config"] = DET_SNAPSHOT


def shot(tag):
    p = os.path.join(OUT, tag)
    sc.render.filepath = p
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    ms = int((time.time() - t0) * 1000)
    real = p + ".png"
    return {"path": real, "ms": ms, "sha256": sha(real),
            "bytes": os.path.getsize(real)}


# A 组：默认（use_taa_reprojection 默认 True）连渲两张
R["det_a1"] = shot("det_a1_repro")
R["det_a2"] = shot("det_a2_repro")
R["A_TAAREPROJ_ON_same_sha"] = R["det_a1"]["sha256"] == R["det_a2"]["sha256"]

# 再测：开 use_taa_reprojection / 关它 各自连渲两张
ee.use_taa_reprojection = False
R["det_b1"] = shot("det_b1_norepro")
R["det_b2"] = shot("det_b2_norepro")
R["B_TAAREPROJ_OFF_same_sha"] = R["det_b1"]["sha256"] == R["det_b2"]["sha256"]
R["A_vs_B_same_sha"] = R["det_a1"]["sha256"] == R["det_b1"]["sha256"]

# 同会话再渲一次 B（第 3 张）看是否稳定
R["det_b3"] = shot("det_b3_norepro")
R["B3_same_as_B1"] = R["det_b3"]["sha256"] == R["det_b1"]["sha256"]

# 采样数改变是否可复现
ee.taa_render_samples = 8
R["det_c1"] = shot("det_c1_s8")
R["det_c2"] = shot("det_c2_s8")
R["C_s8_same_sha"] = R["det_c1"]["sha256"] == R["det_c2"]["sha256"]
R["B_vs_C_same_sha"] = R["det_b1"]["sha256"] == R["det_c1"]["sha256"]

json.dump(R, open(os.path.join(OUT, "probe_eevee.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, sort_keys=True)
print("HEADLESS " + json.dumps(R, ensure_ascii=False, sort_keys=True))
