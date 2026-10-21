# -*- coding: utf-8 -*-
"""探针 4：Line Art 需要相机吗？

探针 3 的 0 笔画很可能是**我自己把相机删了**（del_all 把 factory-startup 的 Camera 一起删了），
Line Art 的 SCENE 模式按场景相机投影。本探针加回相机再试，并测两种模式 + bake。
"""
import json
import math
import os
import sys
import traceback

import bpy

R = {}
outdir = sys.argv[-1] if sys.argv else "."
if not os.path.isdir(outdir):
    outdir = os.path.abspath(".")


def jline(tag, obj):
    print("PROBE %s %s" % (tag, json.dumps(obj, ensure_ascii=False, default=str)))


def step(name, fn):
    try:
        v = fn()
        R.setdefault("steps", []).append({"step": name, "ok": True, "val": v})
        return v
    except Exception as e:
        R.setdefault("steps", []).append(
            {"step": name, "ok": False, "error": "%s: %s" % (type(e).__name__, e),
             "tb": traceback.format_exc()[-400:]})
        return None


def gp_count(d):
    n_s = n_p = 0
    for lyr in getattr(d, "layers", []):
        for fr in lyr.frames:
            dr = getattr(fr, "drawing", None)
            if dr is None:
                continue
            n_s += len(dr.strokes)
            n_p += sum(len(s.points) for s in dr.strokes)
    return n_s, n_p


step("clean", lambda: [bpy.data.objects.remove(o, do_unlink=True)
                       for o in list(bpy.data.objects)])
step("cube", lambda: bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0)))
step("sphere", lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=1.2, location=(1.0, 0.5, 0.8)))


def add_cam():
    bpy.ops.object.camera_add(location=(7.36, -6.93, 4.96))
    cam = bpy.context.active_object
    cam.rotation_euler = (math.radians(63.6), 0.0, math.radians(46.7))
    cam.data.lens = 50
    bpy.context.scene.camera = cam
    return cam.name


step("camera", add_cam)


def add_lineart():
    bpy.ops.object.grease_pencil_add(type="LINEART_SCENE")
    ob = bpy.context.active_object
    return {"name": ob.name, "mods": [(m.name, m.type, m.source_type, m.use_cache)
                                      for m in ob.modifiers]}


step("gp_lineart_scene", add_lineart)


def eval_now(tag):
    def f():
        bpy.context.scene.frame_set(1)
        bpy.context.view_layer.update()
        ob = bpy.context.active_object
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        ns_e, np_e = gp_count(ev.data)
        ns_o, np_o = gp_count(ob.data)
        return {"evaluated": [ns_e, np_e], "original": [ns_o, np_o],
                "frames_orig": [len(fr.drawing.strokes) if getattr(fr, "drawing", None) else 0
                                for lyr in ob.data.layers for fr in lyr.frames][:6]}
    return f


R["pre_bake"] = eval_now("pre")()

step("bake_plain", lambda: str(bpy.ops.object.lineart_bake_strokes(bake_all=True)))
R["post_bake"] = eval_now("post")()


def export_svg():
    svg = os.path.join(outdir, "probe4_lineart.svg")
    r = bpy.ops.wm.grease_pencil_export_svg(filepath=svg)
    txt = open(svg, encoding="utf-8").read() if os.path.isfile(svg) else ""
    return {"ret": str(r), "bytes": len(txt.encode("utf-8")), "paths": txt.count("<path"),
            "head": txt[:300]}


step("export", export_svg)

# ---- 也测 LINEART_OBJECT 模式（不依赖相机）
step("clean2", lambda: [bpy.data.objects.remove(o, do_unlink=True)
                        for o in list(bpy.data.objects)
                        if o.type in ("GREASEPENCIL",)])
step("cube2", lambda: bpy.ops.mesh.primitive_cube_add(size=2))
step("sphere2", lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=1.2, location=(1, 0.5, 0.8)))
step("cam2", add_cam)


def lineart_object():
    bpy.ops.object.grease_pencil_add(type="LINEART_OBJECT")
    ob = bpy.context.active_object
    m = ob.modifiers[0]
    # 指一个源对象
    m.source_type = "OBJECT"
    m.source_object = bpy.data.objects["Cube"]
    bpy.ops.object.lineart_bake_strokes(bake_all=True)
    ns_o, np_o = gp_count(ob.data)
    dg = bpy.context.evaluated_depsgraph_get()
    ns_e, np_e = gp_count(ob.evaluated_get(dg).data)
    return {"name": ob.name, "orig": [ns_o, np_o], "eval": [ns_e, np_e]}


step("lineart_object_mode", lineart_object)

jline("result", R)
print("PROBE_DONE")
