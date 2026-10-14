# -*- coding: utf-8 -*-
"""探针 2：Line Art 能否在无头下真的烘出笔画，并被 grease_pencil_export_svg 导出。

探针 1 结论：算子存在、返回 FINISHED，但 SVG 是**空的**（width/height=0，
layer 下无元素）——因为没有笔画。本探针查：怎么让笔画真的出现。
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
             "tb": traceback.format_exc()[-500:]})
        return None


# ---- 0. 枚举相关算子
names = []
for attr in dir(bpy.ops):
    if attr.startswith("_"):
        continue
    mod = getattr(bpy.ops, attr)
    for op in dir(mod):
        if op.startswith("_"):
            continue
        low = op.lower()
        if "lineart" in low or "line_art" in low or ("bake" in low and "gp" in low):
            names.append("%s.%s" % (attr, op))
jline("ops_matching", sorted(names))

# ---- 1. grease_pencil_add 的 type 闭集
try:
    rna = bpy.ops.object.grease_pencil_add.get_rna_type()
    for p in rna.properties:
        if p.identifier == "type":
            jline("gp_add_types", [i.identifier for i in p.enum_items])
except Exception as e:
    jline("gp_add_types_err", str(e))

# ---- 2. 建场景
step("clean", lambda: [bpy.data.objects.remove(o, do_unlink=True)
                       for o in list(bpy.data.objects)])
step("cube", lambda: bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0)))
step("sphere", lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=1.2, location=(1.0, 0.5, 0.8)))

gp = step("gp_add_LINE_ART", lambda: str(bpy.ops.object.grease_pencil_add(type="LINE_ART")))


def inspect_gp():
    ob = bpy.context.active_object
    info = {"name": ob.name, "mods": [(m.name, m.type) for m in ob.modifiers]}
    d = ob.data
    info["layers"] = []
    for lyr in d.layers:
        li = {"name": lyr.name, "frames": []}
        for fr in lyr.frames:
            fi = {"frame": fr.frame_number}
            try:
                dr = fr.drawing
                fi["strokes"] = len(dr.strokes) if dr else 0
                fi["has_drawing"] = dr is not None
            except Exception as e:
                fi["err"] = str(e)
            li["frames"].append(fi)
        info["layers"].append(li)
    return info


step("inspect_after_add", inspect_gp)

# ---- 3. 强制求值（depsgraph）后看 evaluated object 的笔画
def eval_count():
    sc = bpy.context.scene
    for f in (1, 2):
        sc.frame_set(f)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ob = bpy.context.active_object
    ev = ob.evaluated_get(dg)
    d = ev.data
    out = {"layers": len(d.layers), "detail": []}
    for lyr in d.layers:
        for fr in lyr.frames:
            n = 0
            np_ = 0
            try:
                dr = fr.drawing
                if dr:
                    n = len(dr.strokes)
                    for s in dr.strokes:
                        np_ += len(s.points)
            except Exception as e:
                out["detail"].append({"err": str(e)})
                continue
            out["detail"].append({"layer": lyr.name, "frame": fr.frame_number,
                                  "strokes": n, "points": np_})
    return out


step("evaluated_count", eval_count)

# ---- 4. 试着 bake（若存在算子）
for cand in sorted(names):
    if "bake" in cand.lower():
        try:
            r = eval("bpy.ops." + cand)()

            def c():
                ob = bpy.context.active_object
                n = 0
                for lyr in ob.data.layers:
                    for fr in lyr.frames:
                        try:
                            n += len(fr.drawing.strokes) if fr.drawing else 0
                        except Exception:
                            pass
                return n
            jline("bake_try", {"op": cand, "ret": str(r), "strokes_after": c()})
        except Exception as e:
            jline("bake_try", {"op": cand, "error": "%s: %s" % (type(e).__name__, e)})

# ---- 5. 导出
svg = os.path.join(outdir, "probe2_lineart.svg")
step("export", lambda: str(bpy.ops.wm.grease_pencil_export_svg(filepath=svg)))
R["svg"] = svg
R["svg_bytes"] = os.path.getsize(svg) if os.path.isfile(svg) else None
if os.path.isfile(svg):
    with open(svg, "r", encoding="utf-8") as fh:
        t = fh.read()
    R["svg_head"] = t[:600]
    R["svg_path_count"] = t.count("<path")

jline("result", R)
print("PROBE_DONE")
