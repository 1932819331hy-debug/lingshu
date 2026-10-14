# -*- coding: utf-8 -*-
"""Smoke: Blender 版本 / 引擎枚举 / EEVEE vs CYCLES 能否在 -b 下出图。"""
import json, os, sys, time
import bpy

out = {
    "blender": bpy.app.version_string,
    "version": list(bpy.app.version),
    "engines": [],
    "render": {},
}
try:
    items = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    out["engines"] = [i.identifier for i in items]
except Exception as e:  # noqa
    out["engines_error"] = "%s: %s" % (type(e).__name__, e)

sc = bpy.context.scene
sc.render.resolution_x = 64
sc.render.resolution_y = 64
sc.render.image_settings.file_format = "PNG"
if not any(o.type == "CAMERA" for o in sc.objects):
    cam = bpy.data.objects.new("smoke_cam", bpy.data.cameras.new("smoke_cam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
cam = next(o for o in sc.objects if o.type == "CAMERA")
cam.location = (3.0, -3.0, 2.0)
cam.rotation_euler = (1.05, 0.0, 0.785)
if not any(o.type == "LIGHT" for o in sc.objects):
    li = bpy.data.objects.new("smoke_light", bpy.data.lights.new("smoke_light", type="SUN"))
    sc.collection.objects.link(li)
    li.rotation_euler = (0.7, 0.2, 0.4)

for name in ("foo",):
    pass

tmpdir = bpy.app.tempdir or "/tmp"
for eng in ("BLENDER_EEVEE", "BLENDER_WORKBENCH", "CYCLES"):
    if eng not in out["engines"]:
        out["render"][eng] = {"skipped": "not in enum"}
        continue
    try:
        sc.render.engine = eng
        if eng == "CYCLES":
            sc.cycles.samples = 8
            sc.cycles.device = "CPU"
        p = os.path.join(tmpdir, "smoke_%s.png" % eng)
        sc.render.filepath = p
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        out["render"][eng] = {"ok": True, "sec": round(time.time() - t0, 3),
                              "bytes": os.path.getsize(p) if os.path.exists(p) else None}
    except Exception as e:  # noqa
        out["render"][eng] = {"ok": False, "err": "%s: %s" % (type(e).__name__, e)}

print("SMOKE " + json.dumps(out, ensure_ascii=False))
