# -*- coding: utf-8 -*-
"""探针：Blender 内置 Grease Pencil → SVG 导出路径可用性（只读评估，不改场景文件）。

评估的问题：能否用「Grease Pencil Line Art 修改器 + bpy.ops.wm.grease_pencil_export_svg」
把真实 3D 场景导成 SVG？报告实际读数，不猜测。

跑法：blender_rt_headless(script_file=..., outdir=...)
输出：单行 JSON 前缀 "PROBE " + json
"""
import json
import os
import sys
import traceback

import bpy

R = {"blender": bpy.app.version_string}


def jline(tag, obj):
    print("PROBE %s %s" % (tag, json.dumps(obj, ensure_ascii=False, default=str)))


def has_op(full):
    mod, name = full.rsplit(".", 1)
    o = bpy.ops
    for p in mod.split("."):
        o = getattr(o, p, None)
        if o is None:
            return False
    return hasattr(o, name)


# ---------------------------------------------------------------- 1. 算子存在性
jline("ops", {
    "export": has_op("wm.grease_pencil_export_svg"),
    "import": has_op("wm.grease_pencil_import_svg"),
})
for full in ("wm.grease_pencil_export_svg", "wm.grease_pencil_import_svg"):
    if has_op(full):
        try:
            rna = eval("bpy.ops." + full).get_rna_type()
            props = []
            for p in rna.properties:
                if p.identifier in ("rna_type",):
                    continue
                props.append({"id": p.identifier, "type": p.type,
                              "default": getattr(p, "default", None)})
            jline("rna:" + full, {"doc": (rna.description or "")[:400],
                                  "props": props})
        except Exception as e:
            jline("rna:" + full, {"error": "%s: %s" % (type(e).__name__, e)})

# ---------------------------------------------------------------- 2. Line Art 修改器
try:
    mt = bpy.types.GreasePencilLineartModifier
    jline("lineart_modifier", {"available": True, "props": sorted(
        p.identifier for p in mt.bl_rna.properties if p.identifier != "rna_type")})
except Exception as e:
    jline("lineart_modifier", {"available": False, "error": "%s: %s" % (type(e).__name__, e)})

# ---------------------------------------------------------------- 3. 端到端尝试
# 真场景：一个立方体 + 一个球（有遮挡关系），加 Grease Pencil 对象 + Line Art 修改器
outdir = sys.argv[-1] if sys.argv else "."
if not os.path.isdir(outdir):
    outdir = os.path.abspath(".")
out_svg = os.path.join(outdir, "probe_lineart.svg")

steps = []


def step(name, fn):
    try:
        v = fn()
        steps.append({"step": name, "ok": True, "val": v})
        return v
    except Exception as e:
        steps.append({"step": name, "ok": False,
                      "error": "%s: %s" % (type(e).__name__, e),
                      "tb": traceback.format_exc()[-600:]})
        return None


# 干净场景（factory-startup 已保证，但再保险）
step("del_all", lambda: [bpy.data.objects.remove(o, do_unlink=True)
                         for o in list(bpy.data.objects)])
step("add_cube", lambda: bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0)))
step("add_sphere", lambda: bpy.ops.mesh.primitive_uv_sphere_add(
    radius=1.1, location=(0.9, 0.4, 0.7)))

gp_name = None


def add_gp():
    global gp_name
    bpy.ops.object.grease_pencil_add(type="EMPTY")
    ob = bpy.context.active_object
    gp_name = ob.name
    return ob.name


step("add_gp_empty", add_gp)


def add_mod():
    ob = bpy.data.objects[gp_name]
    m = ob.modifiers.new(name="LineArt", type="LINEART")
    return {"modifier": m.type, "source_type": getattr(m, "source_type", None),
            "use_cache": getattr(m, "use_cache", None),
            "target_layer": getattr(m, "target_layer", None)}


step("add_lineart_modifier", add_mod)


def bake():
    """Line Art 默认 bake 到场景帧，用 update_tag + 取帧触发。"""
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    ob = bpy.data.objects[gp_name]
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    d = ev.data
    n_layers = len(d.layers)
    n_strokes = 0
    n_pts = 0
    for lyr in d.layers:
        for fr in lyr.frames:
            n_strokes += len(fr.strokes)
            for s in fr.strokes:
                n_pts += len(s.points)
    return {"layers": n_layers, "strokes": n_strokes, "points": n_pts}


step("lineart_baked", bake)


def do_export():
    return bpy.ops.wm.grease_pencil_export_svg(filepath=out_svg)


step("export_svg", do_export)

R["steps"] = steps
R["svg_path"] = out_svg
R["svg_exists"] = os.path.isfile(out_svg)
R["svg_size"] = os.path.getsize(out_svg) if os.path.isfile(out_svg) else None

jline("result", R)
print("PROBE_DONE")
