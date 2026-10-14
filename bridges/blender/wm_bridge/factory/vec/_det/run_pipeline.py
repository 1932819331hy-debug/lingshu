# -*- coding: utf-8 -*-
"""_det/run_pipeline.py · 一次 Blender 会话内跑完整矢量产线并打印原始读数。

产出（写到 argv[-1] 指定的目录，缺省 `D:\\DSH\\blender\\out\\wmopt\\vec`）：
  model_flat.svg   与渲染 PNG **同分辨率、同机位、透明背景**（IoU 比对用）
  poster.svg       彩色矢量海报（1200×1500，画框 + 标题）
  layered.svg      分层矢量稿（背景/地面/主体/前景 + 线稿层）
  anim.svg         SMIL 环视动画（d 路径 morph + 描边滚动）
  model_render.png Cycles 渲染（film_transparent ⇒ 前景 = alpha 通道）
  pipeline.json    全部读数
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time

outdir = None
for a in sys.argv[::-1]:
    if os.path.isdir(a):
        outdir = a
        break
if outdir is None:
    outdir = r"D:\DSH\blender\out\wmopt\vec"
os.makedirs(outdir, exist_ok=True)

import bpy                                                    # noqa: E402
from wm_bridge.factory.vec import bpy_vec as BV                # noqa: E402
from wm_bridge.factory.vec import geom2d as G                  # noqa: E402
from wm_bridge.factory.vec import prims as P                   # noqa: E402
from wm_bridge.factory.vec._det import demo_scene as DS        # noqa: E402


def _add_sun_and_frame(doc, res):
    """版面层：暖色太阳盘 + 地平线晕 + 四角标记 + 标题/副标题（纯矢量）。"""
    W, H = res
    L = doc.layer("frame", label="版面/画框")
    doc.circle((W * 0.74, H * 0.235), min(W, H) * 0.145, layer=L,
               fill=(1.0, 0.54, 0.22), fill_opacity=0.14)
    doc.circle((W * 0.74, H * 0.235), min(W, H) * 0.085, layer=L,
               fill=(1.0, 0.62, 0.28), fill_opacity=0.22)
    m = 34
    for (x0, y0, x1, y1) in ((m, m, m + 90, m), (m, m, m, m + 90),
                             (W - m, m, W - m - 90, m), (W - m, m, W - m, m + 90),
                             (m, H - m, m + 90, H - m), (m, H - m, m, H - m - 90),
                             (W - m, H - m, W - m - 90, H - m), (W - m, H - m, W - m, H - m - 90)):
        doc.line((x0, y0), (x1, y1), layer=L, stroke="#e8e6df", stroke_width=2.0,
                 opacity=0.75)
    doc.rect([m, m, W - m, H - m], layer=L, fill=None, stroke="#e8e6df",
             stroke_width=1.0, stroke_opacity=0.28)
    doc.text(m + 26, H - m - 74, "WM-BRIDGE", layer=L, fill="#f2efe6",
             font_family="Helvetica,Arial,sans-serif", font_size=44, font_weight="700")
    doc.text(m + 28, H - m - 38, "3D GEOMETRY  →  LAYERED VECTOR  ·  SMIL",
             layer=L, fill="#9fd8e6", font_family="Helvetica,Arial,sans-serif",
             font_size=19, font_weight="400")
    doc.text(W - m - 26, m + 52, "SPEC wmfactory.spec/1", layer=L, fill="#9fd8e6",
             font_family="Helvetica,Arial,sans-serif", font_size=17,
             text_anchor="end")
    return L


def jline(tag, obj):
    print("PIPE %s %s" % (tag, json.dumps(obj, ensure_ascii=False, default=str)))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


R = {"blender": bpy.app.version_string, "outdir": outdir}
t0 = time.time()

# ─────────────────────────────────────────────────────────── 1. 场景
objs, items = DS.build_scene()
DS.add_camera_and_lights()
DS.setup_world()
role_by_name = {nm: role for nm, _m, _c, role in items}
col_by_name = {nm: col for nm, _m, col, _r in items}
bpy.context.view_layer.update()

draw_objs, skipped = BV.collect_scene_objects(
    role_of=lambda name, ob: role_by_name.get(name),
    layer_of=lambda name, ob: "ground" if name.startswith("ground_") else
                              ("fg" if name.startswith("float_") else "body"))
def _bnds(objs_):
    lo = [1e9] * 3
    hi = [-1e9] * 3
    for o in objs_:
        b = o.world_mesh().bounds()
        for i in range(3):
            lo[i] = min(lo[i], b[0][i])
            hi[i] = max(hi[i], b[1][i])
    return (tuple(round(x, 4) for x in lo), tuple(round(x, 4) for x in hi))


R["scene"] = {"objects": len(draw_objs), "skipped": skipped,
              "faces": sum(len(o.mesh.faces) for o in draw_objs),
              "verts": sum(len(o.mesh.verts) for o in draw_objs),
              "bounds": [list(x) for x in _bnds(draw_objs)]}

# ─────────────────────────────────────────────────────────── 2. 四种导出
INK = "#070810"
HIDDEN = {"hidden": "dash", "hidden_dash": "3 4", "hidden_opacity": 0.5}
BASE_STYLE = {"ink": INK, "ink_soft": "#2a2f3d", "bands": 4,
              "lw_contour": 2.2, "lw_crease": 1.0, "light": (-0.42, -0.66, 0.62),
              "hidden": "dash", "hidden_dash": "3 4", "hidden_opacity": 0.5}

# (a) IoU 用：透明背景、无版面元素、与渲染同分辨率
RES_IOU = (800, 1000)
cam_iou = BV.camera_from_blender(width=RES_IOU[0], height=RES_IOU[1])
_doc, st_flat = BV.export_document(draw_objs, cam_iou,
                                   os.path.join(outdir, "model_flat.svg"),
                                   dict(BASE_STYLE, background=None))
R["model_flat"] = st_flat
jline("model_flat", {k: st_flat[k] for k in ("emitted", "parsed_drawable", "counts_ok",
                                             "faces_drawn", "faces_culled", "bytes",
                                             "hidden_runs", "ambiguous_samples",
                                             "stroke_mean", "stroke_std")})

# (b) 海报：1200×1500，天空渐变 + 画框 + 标题
RES_POSTER = (1200, 1500)
cam_p = BV.camera_from_blender(width=RES_POSTER[0], height=RES_POSTER[1])
sky_top, sky_bot = "#0b1020", "#2a1830"
doc_p, st_p = BV.build_document(draw_objs, cam_p, dict(
    BASE_STYLE, background=None, sky_gradient=(sky_top, sky_bot),
    title="巡检机 · VECTOR SHEET"))
doc_p.raw('<rect x="0" y="0" width="%d" height="%d" fill="url(#sky)"/>'
          % (RES_POSTER[0], RES_POSTER[1]), layer="bg")
_add_sun_and_frame(doc_p, RES_POSTER)
svg_p = doc_p.to_svg()
with open(os.path.join(outdir, "poster.svg"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write(svg_p)
R["poster"] = dict(st_p, verify=doc_p.verify(svg_p),
                   bytes=len(svg_p.encode("utf-8")))
jline("poster", {k: R["poster"][k] for k in ("emitted", "faces_drawn", "faces_culled",
                                             "hidden_runs", "bytes", "stroke_mean",
                                             "stroke_std")})

# (c) 分层稿：同机位、透明背景（设计师自己放底），图层命名齐全
doc_l, st_l = BV.build_document(draw_objs, cam_p, dict(
    BASE_STYLE, background=None, title="巡检机 · LAYERED"))
svg_l = doc_l.to_svg()
with open(os.path.join(outdir, "layered.svg"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write(svg_l)
R["layered"] = dict(st_l, verify=doc_l.verify(svg_l), bytes=len(svg_l.encode("utf-8")))
jline("layered", {k: R["layered"][k] for k in ("emitted", "bytes")})


# (d) SMIL 动画：5 关键帧环视 morph
def _cam_factory(az_delta):
    az0 = math.atan2(cam_p.eye[1] - 0.25, cam_p.eye[0])
    r = math.hypot(cam_p.eye[0], cam_p.eye[1] - 0.25)
    el = math.atan2(cam_p.eye[2] - 3.75, r)
    az = az0 + math.radians(az_delta)
    target = (0.0, 0.25, 3.75)
    eye = (target[0] + r * math.cos(az) * math.cos(el),
           target[1] + r * math.sin(az) * math.cos(el),
           target[2] + r * math.sin(el))
    return G.Camera(eye, target, up=(0, 0, 1), fov_deg=cam_p.fov_deg,
                    width=cam_p.width, height=cam_p.height)


try:
    doc_a, st_a = BV.export_anim_document(
        draw_objs, _cam_factory, os.path.join(outdir, "anim.svg"),
        keyframes=5, dur=14.0, az_span=20.0,
        style=dict(BASE_STYLE, background=None, sky_gradient=(sky_top, sky_bot)))
    R["anim"] = st_a
    jline("anim", st_a)
except Exception as e:
    R["anim"] = {"error": "%s: %s" % (type(e).__name__, e)}
    jline("anim", R["anim"])

# ─────────────────────────────────────────────────────────── 3. 渲染 PNG（IoU 参照）
sc = bpy.context.scene
sc.render.resolution_x, sc.render.resolution_y = RES_IOU
sc.render.resolution_percentage = 100
sc.render.film_transparent = True
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_mode = "RGBA"
sc.render.engine = "CYCLES"
try:
    sc.cycles.device = "CPU"
    sc.cycles.samples = 16
    sc.cycles.use_denoising = False
except Exception:
    pass
png = os.path.join(outdir, "model_render.png")
sc.render.filepath = png
t1 = time.time()
bpy.ops.render.render(write_still=True)
R["render"] = {"png": png, "seconds": round(time.time() - t1, 2),
               "bytes": os.path.getsize(png) if os.path.isfile(png) else None}
jline("render", R["render"])

# ─────────────────────────────────────────────────────────── 4. 内置 Line Art 读数
try:
    la_path = os.path.join(outdir, "builtin_lineart.svg")
    R["builtin_lineart"] = BV.export_builtin_lineart_svg(la_path, frame=1)
except Exception as e:
    R["builtin_lineart"] = {"error": "%s: %s" % (type(e).__name__, e)}
jline("builtin_lineart", R["builtin_lineart"])

# ─────────────────────────────────────────────────────────── 5. 哈希
hashes = {}
for name in ("model_flat.svg", "poster.svg", "layered.svg", "anim.svg",
             "model_render.png", "builtin_lineart.svg"):
    p = os.path.join(outdir, name)
    if os.path.isfile(p):
        hashes[name] = {"sha256": sha256(p), "bytes": os.path.getsize(p)}
R["hashes"] = hashes
jline("hashes", hashes)
R["seconds_total"] = round(time.time() - t0, 2)
with open(os.path.join(outdir, "pipeline.json"), "w", encoding="utf-8") as fh:
    json.dump(R, fh, ensure_ascii=False, indent=1, default=str)
jline("done", {"seconds": R["seconds_total"]})
print("PIPE_DONE")
