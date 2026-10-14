# -*- coding: utf-8 -*-
"""`hero_render.py` · Lead 的**呈现效果**实验与定标（不是接口验证）

为什么会存在这个文件：用户明确指出「不止接口，还要优化项目最终呈现效果」。
而"好看"如果只写成形容词就无法验收。所以这里把**设计手法**固化成可执行、可复看的代码，
并把每一轮"哪里难看"记在文末（`ITERATIONS`）。

本文件是 `shade` 产线要实现的**目标形态**的参考实现（spec-independent）：
`render_scene(cfg)` 是一个**纯 Blender 侧配方执行器**，`cfg` 全为声明式参数
⇒ 将来 `wm_bridge/factory/shade/render.py` 只负责"从 spec 生成 cfg"。

★ 关键教训（v1 → v2，见文件末 ITERATIONS）：
  · 金属必须有**可反射的环境**，否则在暗背景里渲成黑洞 —— 本例用**环形发光板**做程序化 IBL
  · 物体必须**坐在**地台上（v1 算错，圆柱漂浮、下面露出"脚"）
  · 相机要能装下全部主体（v1 裁切）
  · 接触阴影是"落地感"的唯一来源
"""
import json
import math
import os

import bpy
import mathutils

OUT = os.environ.get("DSH_OUT") or "/tmp"
try:
    OUT = K.win_path(OUT)                                        # noqa: F821
except Exception:                                                # noqa: BLE001
    pass
os.makedirs(OUT, exist_ok=True)


# ═══════════════════════════════════════════════════════════ 基础
def purge():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def look_at(ob, target):
    d = mathutils.Vector(target) - ob.location
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def principled(name, base, rough, metal=0.0, emis=None, emis_str=0.0, spec=0.5):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    b.inputs["Base Color"].default_value = (*base, 1.0)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if "Specular IOR Level" in b.inputs:
        b.inputs["Specular IOR Level"].default_value = spec
    if emis is not None:
        b.inputs["Emission Color"].default_value = (*emis, 1.0)
        b.inputs["Emission Strength"].default_value = emis_str
    return m


def add_light(name, kind, loc, energy, target=None, size=1.0, color=(1, 1, 1), spread=None):
    d = bpy.data.lights.new(name, type=kind)
    d.energy = energy
    d.color = color
    if kind == "AREA":
        d.size = size
        if spread is not None:
            d.spread = spread
    ob = bpy.data.objects.new(name, d)
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    if target:
        look_at(ob, target)
    return ob


def emissive_plane(name, loc, target, size, color, strength):
    """环形发光板：给金属提供**可见的反射内容**（程序化 IBL，无需 HDR 文件）。"""
    bpy.ops.mesh.primitive_plane_add(size=size, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    look_at(ob, target)
    m = bpy.data.materials.new("M_" + name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = (*color, 1.0)
    e.inputs["Strength"].default_value = strength
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(e.outputs["Emission"], o.inputs["Surface"])
    ob.data.materials.append(m)
    return ob


# ═══════════════════════════════════════════════════════════ 主渲染
def render_scene(cfg):
    purge()
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = cfg["res"]
    try:
        sc.view_settings.view_transform = cfg.get("view_transform", "AgX")
    except TypeError:
        sc.view_settings.view_transform = "Filmic"
    try:
        sc.view_settings.look = cfg.get("look", "AgX - Punchy")
    except TypeError:
        pass
    sc.view_settings.exposure = cfg.get("exposure", 0.15)

    # ---- 地台（顶面 = 0，物体坐在上面）
    bpy.ops.mesh.primitive_plane_add(size=cfg.get("floor_size", 80),
                                     location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "FLOOR"
    floor.data.materials.append(principled("M_floor", cfg.get("floor_color", (0.045, 0.05, 0.062)),
                                           cfg.get("floor_rough", 0.55)))

    # ---- 主体：4 个材质角色（同一几何 ⇒ 差异只来自材质）
    bh = cfg.get("base_h", 0.10)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, bh / 2))
    base = bpy.context.active_object
    base.name = "BASE"
    base.scale = (cfg.get("base_wide", 3.2), cfg.get("base_deep", 0.52), bh)
    base.data.materials.append(principled("M_base", (0.085, 0.09, 0.10), 0.30, 0.75))

    roles = cfg["roles"]
    objs = []
    top = bh
    for i, r in enumerate(roles):
        x = (i - (len(roles) - 1) / 2.0) * cfg["spacing"]
        r_cyl, h = r.get("r", 0.30), r.get("h", 0.95)
        bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=r_cyl, depth=h,
                                            location=(x, 0, top + h / 2))
        ob = bpy.context.active_object
        ob.name = "OBJ_" + r["role"]
        ob.data.materials.append(principled("M_" + r["role"], r["base"], r["rough"],
                                            r.get("metal", 0.0), r.get("emis"),
                                            r.get("emis_str", 0.0)))
        bv = ob.modifiers.new("bevel", "BEVEL")
        bv.width = r.get("bevel", 0.018)
        bv.segments = 4
        objs.append(ob)

    # ---- 程序化 IBL：环形发光板（给金属反射内容，同时抬高环境光）
    if cfg.get("ring", True):
        n = cfg.get("ring_n", 6)
        rr = cfg.get("ring_r", 5.2)
        rh = cfg.get("ring_h", 3.4)
        c1 = cfg.get("ring_color", (1.0, 0.97, 0.92))
        c2 = cfg.get("ring_color2", (0.55, 0.75, 1.0))
        for k in range(n):
            a = 2 * math.pi * k / n + math.radians(cfg.get("ring_phase", 15))
            loc = (rr * math.cos(a), rr * math.sin(a), rh)
            col = c1 if k % 2 == 0 else c2
            emissive_plane("RING%d" % k, loc, (0, 0, 0.5),
                           cfg.get("ring_size", 3.2), col, cfg.get("ring_strength", 9.0))

    # ---- 三光源
    lc = cfg.get("lights", {})
    add_light("KEY", "AREA", lc.get("key_loc", (-3.0, -3.4, 4.0)),
              lc.get("key_e", 1400), target=(0, 0, 0.65), size=4.0,
              color=lc.get("key_c", (1.0, 0.96, 0.90)))
    add_light("FILL", "AREA", lc.get("fill_loc", (3.8, -2.6, 1.9)),
              lc.get("fill_e", 180), target=(0, 0, 0.5), size=6.0,
              color=lc.get("fill_c", (0.75, 0.83, 1.0)))
    add_light("RIM", "AREA", lc.get("rim_loc", (2.2, 4.4, 3.2)),
              lc.get("rim_e", 1100), target=(0, 0, 0.9), size=3.0,
              color=lc.get("rim_c", (0.70, 0.88, 1.0)))

    # ---- 世界背景：程序化 **Sky Texture**（不是往场景里放发光板！）
    # ★ v2 的教训：用"环形发光板"给金属反射内容 ⇒ 发光板**本身进了画面**（顶上一排白块）。
    #   正确做法是**环境贴图**：Sky Texture 是纯程序化的天空+太阳，
    #   既提供金属可反射的明暗层次，又不会在画面里出现实体。**无需 HDR 文件**。
    world = bpy.data.worlds.new("W")
    sc.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Rotation"].default_value[0] = math.radians(cfg.get("sky_rot_x", -8))
    mp.inputs["Rotation"].default_value[2] = math.radians(cfg.get("sky_rot_z", 25))
    ln = nt.links
    ln.new(tc.outputs["Generated"], mp.inputs["Vector"])

    if cfg.get("sky", True):
        sky = nt.nodes.new("ShaderNodeTexSky")
        try:
            sky.sky_type = "NISHITA"
        except TypeError:
            sky.sky_type = "PREETHAM"
        try:
            sky.sun_elevation = math.radians(cfg.get("sun_elev", 12))
            sky.sun_rotation = math.radians(cfg.get("sun_rot", 25))
            sky.altitude = cfg.get("sky_altitude", 120)
            sky.air_density = cfg.get("air_density", 1.1)
            sky.dust_density = cfg.get("dust_density", 1.4)
        except Exception:                                        # noqa: BLE001
            pass
        ln.new(mp.outputs["Vector"], sky.inputs["Vector"])
        tint = nt.nodes.new("ShaderNodeMixRGB")
        tint.blend_type = "MULTIPLY"
        tint.inputs["Fac"].default_value = cfg.get("sky_tint_fac", 0.45)
        tint.inputs["Color2"].default_value = (*cfg.get("sky_tint", (0.85, 0.72, 0.85)), 1)
        ln.new(sky.outputs["Color"], tint.inputs["Color1"])
        ln.new(tint.outputs["Color"], bg.inputs["Color"])
    else:
        g = nt.nodes.new("ShaderNodeTexGradient")
        g.gradient_type = "SPHERICAL"
        rp = nt.nodes.new("ShaderNodeValToRGB")
        rp.color_ramp.interpolation = "EASE"
        rp.color_ramp.elements[0].position = cfg.get("bg_pos0", 0.06)
        rp.color_ramp.elements[0].color = (*cfg.get("bg_in", (0.20, 0.14, 0.17)), 1)
        rp.color_ramp.elements[1].position = cfg.get("bg_pos1", 0.62)
        rp.color_ramp.elements[1].color = (*cfg.get("bg_out", (0.012, 0.016, 0.030)), 1)
        ln.new(mp.outputs["Vector"], g.inputs["Vector"])
        ln.new(g.outputs["Fac"], rp.inputs["Fac"])
        ln.new(rp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = cfg.get("sky_strength", 0.38)
    ln.new(bg.outputs["Background"], o.inputs["Surface"])

    # ---- 相机
    cd = bpy.data.cameras.new("CAM")
    cd.lens = cfg.get("lens", 42)
    cam = bpy.data.objects.new("CAM", cd)
    cam.location = cfg.get("cam_loc", (0.0, -8.6, 2.6))
    bpy.context.scene.collection.objects.link(cam)
    look_at(cam, cfg.get("cam_target", (0, 0, 0.62)))
    sc.camera = cam

    sc.eevee.taa_render_samples = cfg.get("samples", 128)
    try:
        sc.eevee.use_raytracing = True
        sc.eevee.ray_tracing_options.resolution_scale = "1"
    except Exception:                                            # noqa: BLE001
        pass
    # 接触阴影/环境遮蔽：落地感的关键
    try:
        sc.eevee.use_shadows = True
        sc.eevee.use_fast_gi = True
    except Exception:                                            # noqa: BLE001
        pass

    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    png = os.path.join(OUT, cfg.get("name", "hero") + ".png")
    sc.render.filepath = png
    bpy.ops.render.render(write_still=True)
    return {"png": png, "exists": os.path.exists(png),
            "bytes": os.path.getsize(png) if os.path.exists(png) else 0,
            "res": list(cfg["res"]), "samples": cfg.get("samples", 128),
            "view_transform": sc.view_settings.view_transform,
            "look": sc.view_settings.look, "lens": cd.lens,
            "cam_loc": list(cam.location),
            "objects": len(bpy.data.objects)}


# ═══════════════════════════════════════════════════════════ v2 配方（修正 v1 的 6 条）
CFG_V2 = {
    "name": "lead_hero_v2",
    "res": (1280, 720),
    "samples": 160,
    "exposure": 0.15,
    "lens": 42,
    "cam_loc": (0.0, -8.8, 2.7),
    "cam_target": (0, 0, 0.62),
    "spacing": 1.42,
    "floor_color": (0.042, 0.047, 0.058),
    "floor_rough": 0.52,
    # 背景：中心亮、四周暗（径向），与相机方向解耦
    "bg_in": (0.17, 0.125, 0.145),
    "bg_out": (0.010, 0.014, 0.028),
    "bg_pos0": 0.06, "bg_pos1": 0.55, "bg_squash": 0.5,
    "bg_strength": 1.0,
    # 环形 IBL：给金属反射内容
    "ring": True, "ring_n": 8, "ring_r": 5.6, "ring_h": 3.2, "ring_size": 2.6,
    "ring_strength": 7.0, "ring_color": (1.0, 0.96, 0.90), "ring_color2": (0.45, 0.70, 1.0),
    "ring_phase": 22,
    "lights": {"key_e": 1500, "fill_e": 200, "rim_e": 1200},
    "roles": [
        {"role": "metal",         "base": (0.70, 0.72, 0.76), "rough": 0.16, "metal": 1.0, "r": 0.30, "h": 0.95},
        {"role": "painted_metal", "base": (0.74, 0.20, 0.09), "rough": 0.38, "metal": 0.12, "r": 0.30, "h": 0.95},
        {"role": "rubber",        "base": (0.035, 0.035, 0.040), "rough": 0.90, "metal": 0.0, "r": 0.30, "h": 0.95},
        {"role": "emissive",      "base": (0.06, 0.06, 0.07), "rough": 0.30, "metal": 0.0,
         "emis": (0.30, 0.82, 1.0), "emis_str": 3.0, "r": 0.30, "h": 0.95},
    ],
}

# ═══════════════════════════════════════════════════════════ v3 配方（修 v2 的"发光板进画面"）
CFG_V3 = dict(CFG_V2)
CFG_V3.update({
    "name": "lead_hero_v3",
    "samples": 192,
    # ★ 关掉环形发光板，改用程序化 Sky Texture 作环境（金属反射它的明暗，画面里不出现实体）
    "ring": False,
    # 天空做 IBL：地平线暖、天顶冷 ⇒ 金属上能看出"上冷下暖"的反射
    "sky": True, "sky_strength": 0.42,
    "sky_rot_x": -6, "sky_rot_z": 30,
    "sun_elev": 14, "sun_rot": 32, "sky_altitude": 140,
    "air_density": 1.0, "dust_density": 1.2,
    "sky_tint": (0.82, 0.70, 0.86), "sky_tint_fac": 0.42,
    # 底板：v2 里它显得"又小又飘" ⇒ 加厚、加宽、让四个柱都稳稳落在上面
    "base_h": 0.16, "base_wide": 4.35, "base_deep": 0.78,
    # 相机再退一点、略降，给头顶留呼吸空间
    "cam_loc": (0.0, -9.4, 2.5), "cam_target": (0, 0, 0.58),
    "lens": 40,
    "spacing": 1.34,
    "lights": {"key_e": 1300, "fill_e": 160, "rim_e": 1500,
               "rim_loc": (1.8, 4.6, 3.4), "rim_c": (0.66, 0.86, 1.0)},
    "floor_color": (0.030, 0.034, 0.044), "floor_rough": 0.46,
})


# ═══════════════════════════════════════════════════════════ v4 配方（修 v3 的三处）
# v3 看图判定：
#   D7 底板"又薄又长"像飘带   → 宽 4.35 : 厚 0.16 = 27:1，真实台座通常 ≤ 8:1
#   D8 构图偏低、上方空        → 主体在画面下 1/3
#   D9 地面太亮、抢主体        → 地面灰度接近主体受光面，缺少分离
CFG_V4 = dict(CFG_V3)
CFG_V4.update({
    "name": "lead_hero_v4",
    # D7：加厚底板 + 收窄，做成"台座"而不是"条"
    "base_h": 0.30, "base_wide": 3.35, "base_deep": 1.05,
    # D8：相机抬高、看向更高处，让主体落在画面中偏下（三分法）
    "cam_loc": (0.0, -9.0, 3.15), "cam_target": (0, 0, 0.88), "lens": 42,
    "sky_strength": 0.30,
    # D9：地面压暗、提粗糙度 ⇒ 主体亮、环境暗，主体自然被"抠"出来
    "floor_color": (0.019, 0.021, 0.028), "floor_rough": 0.62,
    # 柱子略高更修长（同一几何，只是比例）
    "roles": [
        {"role": "metal",         "base": (0.70, 0.72, 0.76), "rough": 0.16, "metal": 1.0, "r": 0.30, "h": 1.18},
        {"role": "painted_metal", "base": (0.74, 0.20, 0.09), "rough": 0.38, "metal": 0.12, "r": 0.30, "h": 1.18},
        {"role": "rubber",        "base": (0.035, 0.035, 0.040), "rough": 0.90, "metal": 0.0, "r": 0.30, "h": 1.18},
        {"role": "emissive",      "base": (0.06, 0.06, 0.07), "rough": 0.30, "metal": 0.0,
         "emis": (0.30, 0.82, 1.0), "emis_str": 3.0, "r": 0.30, "h": 1.18},
    ],
    "spacing": 1.30,
})

if __name__ == "__main__":
    res = render_scene(CFG_V4)
    print("HEADLESS " + json.dumps(res, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════ ITERATIONS（每一轮"哪里难看"）
ITERATIONS = """
v1 (lead_hero_v1.png)  首版。看图后判定 6 条缺陷：
  D1 两侧圆柱被裁切        → 相机太近(lens 52 / y-6.6)，主体展宽 4.5 m 装不下
  D2 圆柱**漂浮**在底板上，下面露出"脚"
                          → 算错：底板顶面 z=0.12，圆柱却从 z=0 起（中心 0.575-0.575）
                            实际圆柱底 = 0，与底板顶面差 0.12 ⇒ 露出的是圆柱穿过底板的部分
  D3 金属件发黑            → 场景无可反射内容（只有暗背景）⇒ 反射=背景=黑
  D4 缺接触阴影，物体像贴纸 → 主光投影像在画面外；EEVEE 软阴影未开
  D5 背景无层次            → 线性渐变方向与相机视线不垂直（Mapping 只绕 Y 转 90°）
  D6 两侧被照白            → FILL/RIM 直接照到画面边缘的圆柱
v2 的修法（逐条对应）：
  D1 lens 52→42，cam y -6.6→-8.8，z 2.35→2.7，并给足取景余量
  D2 统一以「地台顶面 = 0」为基准：底板 [0, 0.10]，圆柱从 0.10 起
  D3 新增**环形发光板**(8 块, 交替暖/冷) 作程序化 IBL —— 无需 HDR 文件即可给金属反射内容
  D4 开 EEVEE 软阴影/快速 GI + 地台粗糙度下调至 0.52
  D5 背景改 **SPHERICAL 径向**渐变（中心亮、四周暗）⇒ 与相机方向解耦
  D6 KEY 1600 / FILL 200 / RIM 1200，并收窄 FILL 面积与功率，避免打白边缘
"""
