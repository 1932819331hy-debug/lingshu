# -*- coding: utf-8 -*-
"""探针 3：LINEART_SCENE 的正确用法 —— 笔画能否在无头下出来（求值面 / bake 面）。

探针 1/2 结论：
  · `wm.grease_pencil_export_svg` 存在、返回 FINISHED，但无笔画时导出**空文档**（width/height=0）
  · `object.grease_pencil_add` 有 LINEART_SCENE / LINEART_COLLECTION / LINEART_OBJECT 三种
  · `object.lineart_bake_strokes` 存在，但无头下 `poll() failed, context is incorrect`
本探针查：① LINEART_SCENE 建出的对象在**求值后**有没有笔画；② bake 算子要什么上下文。
"""
import json
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


def gp_stroke_count(d):
    """GP datablock → (strokes, points)；兼容 5.x 的 drawing 结构与旧 strokes 结构。"""
    n_s = n_p = 0
    detail = []
    for lyr in getattr(d, "layers", []):
        for fr in lyr.frames:
            try:
                dr = fr.drawing
            except Exception as e:
                detail.append({"err": "%s: %s" % (type(e).__name__, e)})
                continue
            if dr is None:
                detail.append({"layer": lyr.name, "drawing": None})
                continue
            ns = len(dr.strokes)
            np_ = sum(len(s.points) for s in dr.strokes)
            n_s += ns
            n_p += np_
            detail.append({"layer": lyr.name, "frame": fr.frame_number,
                           "strokes": ns, "points": np_})
    return n_s, n_p, detail


step("clean", lambda: [bpy.data.objects.remove(o, do_unlink=True)
                       for o in list(bpy.data.objects)])
step("cube", lambda: bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0)))
step("sphere", lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=1.2, location=(1.0, 0.5, 0.8)))


def add_lineart_scene():
    bpy.ops.object.grease_pencil_add(type="LINEART_SCENE")
    ob = bpy.context.active_object
    return {"name": ob.name, "type": ob.type,
            "mods": [(m.name, m.type,
                      getattr(m, "source_type", None),
                      getattr(m, "use_cache", None)) for m in ob.modifiers]}


gpinfo = step("gp_add_LINEART_SCENE", add_lineart_scene)
jline("gpinfo", gpinfo)


def inspect_original():
    ob = bpy.context.active_object
    n_s, n_p, det = gp_stroke_count(ob.data)
    return {"obj": ob.name, "layers": len(ob.data.layers),
            "strokes": n_s, "points": n_p, "detail": det}


step("original_data", inspect_original)


def inspect_evaluated():
    sc = bpy.context.scene
    sc.frame_set(1)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ob = bpy.context.active_object
    ev = ob.evaluated_get(dg)
    n_s, n_p, det = gp_stroke_count(ev.data)
    return {"obj": ev.name, "strokes": n_s, "points": n_p, "detail": det}


step("evaluated_data", inspect_evaluated)


def try_bake():
    ob = bpy.context.active_object
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    # 先看这个算子要哪些 props / poll 依赖
    rna = bpy.ops.object.lineart_bake_strokes.get_rna_type()
    props = [(p.identifier, p.type) for p in rna.properties if p.identifier != "rna_type"]
    out = {"props": props}
    for attempt in ("plain", "temp_override"):
        try:
            if attempt == "plain":
                r = bpy.ops.object.lineart_bake_strokes()
            else:
                win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
                with bpy.context.temp_override(window=win, scene=bpy.context.scene,
                                               active_object=ob, selected_objects=[ob],
                                               object=ob):
                    r = bpy.ops.object.lineart_bake_strokes()
            out[attempt] = str(r)
            n_s, n_p, det = gp_stroke_count(ob.data)
            out[attempt + "_after"] = {"strokes": n_s, "points": n_p, "detail": det}
        except Exception as e:
            out[attempt] = "%s: %s" % (type(e).__name__, e)
    return out


step("bake_attempt", try_bake)


def export_svg():
    svg = os.path.join(outdir, "probe3_lineart.svg")
    r = bpy.ops.wm.grease_pencil_export_svg(filepath=svg)
    txt = open(svg, encoding="utf-8").read() if os.path.isfile(svg) else ""
    return {"ret": str(r), "bytes": len(txt.encode("utf-8")),
            "paths": txt.count("<path"), "head": txt[:400]}


step("export", export_svg)

jline("result", R)
print("PROBE_DONE")
