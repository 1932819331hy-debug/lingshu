# -*- coding: utf-8 -*-
"""探测二：EEVEE 逐字节确定性的**根因**。

假设 H：sha256 不稳来自 scene.render.dither_intensity（默认 1.0）—— 8bit 写出前的随机抖动，
       而不是 EEVEE 采样本身。若 H 成立：dither=0 时同配置连渲应逐字节相同。

对照矩阵：
  D0 : dither=1.0（默认）  RT ON   reproj OFF   → 基线（预期不稳）
  D1 : dither=0.0          RT ON   reproj OFF   → 预期稳
  D2 : dither=0.0          RT OFF  reproj OFF   → 预期稳
  D3 : dither=0.0          RT ON   reproj ON    → 看 reproj 是否也是噪声源
  D4 : dither=0.0          RT ON   reproj OFF   samples=8 → 换采样数后是否仍稳
另外读像素做逐通道 max diff（不只看 sha256）。
"""
import hashlib
import json
import os
import struct
import time
import zlib

import bpy

OUT = os.environ.get("SHADE_OUT") or r"D:\DSH\blender\out\wmopt\shade"
os.makedirs(OUT, exist_ok=True)
R = {"out": OUT, "cases": {}}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def png_pixels(path):
    """纯标准库解 PNG（8bit RGB/RGBA，无隔行）→ (w,h,ch,bytes)。"""
    d = open(path, "rb").read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", "not png"
    i = 8
    idat = b""
    w = h = ch = None
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        typ = d[i + 4:i + 8]
        body = d[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w, h, bits, colortype, comp, filt, inter = struct.unpack(">IIBBBBB", body)
            assert bits == 8 and inter == 0, (bits, inter)
            ch = {0: 1, 2: 3, 4: 2, 6: 4}[colortype]
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        i += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * ch
    prev = bytearray(stride)
    out = bytearray()
    pos = 0
    for _y in range(h):
        f = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if f == 1:
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        out += line
        prev = line
    return w, h, ch, bytes(out)


def diff(a, b):
    wa, ha, ca, pa = png_pixels(a)
    wb, hb, cb, pb = png_pixels(b)
    if (wa, ha, ca) != (wb, hb, cb):
        return {"geom_mismatch": [wa, ha, ca, wb, hb, cb]}
    n = len(pa)
    mx = 0
    over1 = 0
    over2 = 0
    sumd = 0
    nz = 0
    for i in range(n):
        d = abs(pa[i] - pb[i])
        if d:
            nz += 1
            sumd += d
            if d > mx:
                mx = d
            if d > 1:
                over1 += 1
            if d > 2:
                over2 += 1
    return {"channels_total": n, "nonzero_chan": nz, "nz_frac": round(nz / float(n), 8),
            "max_abs_diff": mx, "chans_gt1": over1, "chans_gt2": over2,
            "mean_abs_diff_over_nz": round(sumd / float(nz), 4) if nz else 0.0}


# ── 场景（与探测一同一装置，便于对照）
sc = bpy.context.scene
sc.render.engine = "BLENDER_EEVEE"
ee = sc.eevee
ee.use_raytracing = True
try:
    ee.ray_tracing_method = "SCREEN"
except Exception:
    pass
ee.use_taa_reprojection = False
ee.use_shadow_jitter_viewport = False
ee.use_bokeh_jittered = False

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

import mathutils
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=48, ring_count=24, location=(0, 0, 1.0))
sp = bpy.context.object
mm = bpy.data.materials.new("probe_surf")
b = next(n for n in mm.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
b.inputs["Base Color"].default_value = (0.85, 0.3, 0.15, 1.0)
b.inputs["Roughness"].default_value = 0.35
sp.data.materials.append(mm)
bpy.ops.mesh.primitive_plane_add(size=40.0, location=(0, 0, 0))
g = bpy.context.object
gm = bpy.data.materials.new("probe_gnd")
gb = next(n for n in gm.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
gb.inputs["Base Color"].default_value = (0.06, 0.08, 0.11, 1.0)
gb.inputs["Roughness"].default_value = 0.85
g.data.materials.append(gm)

for nm, (loc, en, sz, col) in {
    "key": ((3.2, -3.6, 4.4), 900.0, 3.0, (1.0, 0.92, 0.82)),
    "fill": ((-4.4, -1.8, 2.0), 220.0, 5.0, (0.75, 0.85, 1.0)),
    "rim": ((-1.4, 4.6, 3.4), 700.0, 2.0, (0.7, 0.85, 1.0)),
}.items():
    ld = bpy.data.lights.new(nm, type="AREA")
    ld.energy, ld.size, ld.color = en, sz, col
    lo = bpy.data.objects.new(nm, ld)
    lo.location = loc
    sc.collection.objects.link(lo)
    lo.rotation_euler = (mathutils.Vector((0, 0, 1.0)) - mathutils.Vector(loc)).to_track_quat("-Z", "Y").to_euler()

camd = bpy.data.cameras.new("cam")
camd.lens = 50.0
cam = bpy.data.objects.new("cam", camd)
cam.location = (4.2, -5.6, 3.4)
cam.rotation_euler = (mathutils.Vector((0, 0, 1.0)) - mathutils.Vector(cam.location)).to_track_quat("-Z", "Y").to_euler()
sc.collection.objects.link(cam)
sc.camera = cam

w = bpy.data.worlds.new("probe_world")
sc.world = w
wnt = w.node_tree
wnt.nodes.clear()
w_out = wnt.nodes.new("ShaderNodeOutputWorld")
w_bg = wnt.nodes.new("ShaderNodeBackground")
w_bg.inputs["Strength"].default_value = 0.25
tc = wnt.nodes.new("ShaderNodeTexCoord")
sep = wnt.nodes.new("ShaderNodeSeparateXYZ")
ramp = wnt.nodes.new("ShaderNodeValToRGB")
els = ramp.color_ramp.elements
els[0].position, els[0].color = 0.0, (0.02, 0.05, 0.09, 1.0)
els.new(1.0).color = (0.32, 0.14, 0.06, 1.0)
wnt.links.new(tc.outputs["Window"], sep.inputs["Vector"])
wnt.links.new(sep.outputs["Y"], ramp.inputs["Fac"])
wnt.links.new(ramp.outputs["Color"], w_bg.inputs["Color"])
wnt.links.new(w_bg.outputs["Background"], w_out.inputs["Surface"])

sc.render.resolution_x, sc.render.resolution_y = 320, 180
sc.render.resolution_percentage = 100
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_mode = "RGBA"
sc.render.film_transparent = False
sc.view_settings.view_transform = "Standard"
sc.view_settings.look = "None"
R["dither_default"] = sc.render.dither_intensity


def shot(tag):
    p = os.path.join(OUT, tag)
    sc.render.filepath = p
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    return {"path": p + ".png", "ms": int((time.time() - t0) * 1000),
            "bytes": os.path.getsize(p + ".png"), "sha256": sha(p + ".png")}


def case(name, dither, rt, reproj, samples):
    sc.render.dither_intensity = dither
    ee.use_raytracing = rt
    ee.use_taa_reprojection = reproj
    ee.taa_render_samples = samples
    c = {"dither": dither, "rt": rt, "reproj": reproj, "samples": samples}
    c["r1"] = shot("det2_%s_1" % name)
    c["r2"] = shot("det2_%s_2" % name)
    c["r3"] = shot("det2_%s_3" % name)
    c["sha_r1_eq_r2"] = c["r1"]["sha256"] == c["r2"]["sha256"]
    c["sha_r1_eq_r3"] = c["r1"]["sha256"] == c["r3"]["sha256"]
    c["pix_r1_vs_r2"] = diff(c["r1"]["path"], c["r2"]["path"])
    c["pix_r1_vs_r3"] = diff(c["r1"]["path"], c["r3"]["path"])
    R["cases"][name] = c
    return c


case("D0_dither1_rtON", 1.0, True, False, 32)
case("D1_dither0_rtON", 0.0, True, False, 32)
case("D2_dither0_rtOFF", 0.0, False, False, 32)
case("D3_dither0_rtON_reprojON", 0.0, True, True, 32)
case("D4_dither0_rtON_s8", 0.0, True, False, 8)

json.dump(R, open(os.path.join(OUT, "probe_determinism2.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, sort_keys=True)
print("HEADLESS " + json.dumps(R, ensure_ascii=False, sort_keys=True))
