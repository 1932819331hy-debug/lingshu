# -*- coding: utf-8 -*-
"""_det/probe_env.py · 环境探针（诊断用，非交付件）

回答四件事：
  1. Blender 版本 / 可用渲染引擎枚举（5.2 是 BLENDER_EEVEE）
  2. headless 下 bpy.ops.mesh.primitive_* 是否可用（建图元）
  3. collection.all_objects 的**返回顺序**（决定 dump 的 nodes/tracks 是否确定）
  4. 对象按名排序 vs 插入序 —— 我据此决定 collect 的排序口径
输出必须单行 JSON（headless 契约）。
"""
import json

import bpy

def main():
    out = {"blender": bpy.app.version_string}

    # 1) 引擎枚举
    try:
        items = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
        out["engines"] = list(items)
    except Exception as exc:                                   # noqa: BLE001
        out["engines"] = "ERR:%s" % exc

    # 清场
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)

    # 2) 建一个集合，按**故意乱序**的名字插入对象
    col = bpy.data.collections.new("PROBE")
    bpy.context.scene.collection.children.link(col)

    names = ["P_m", "P_a", "P_z", "P_b"]
    made = []
    for nm in names:
        try:
            bpy.ops.mesh.primitive_cube_add(size=1.0)
            ob = bpy.context.active_object
            ob.name = nm
            for c in list(ob.users_collection):
                c.objects.unlink(ob)
            col.objects.link(ob)
            made.append(nm)
        except Exception as exc:                               # noqa: BLE001
            out["cube_add_err"] = "%s: %s" % (type(exc).__name__, exc)
            break
    out["created_in_order"] = made

    # 3) all_objects 顺序
    try:
        out["all_objects"] = [o.name for o in col.all_objects]
    except Exception as exc:                                   # noqa: BLE001
        out["all_objects"] = "ERR:%s" % exc
    out["col_objects"] = [o.name for o in col.objects]

    # 4) 图元顶点序是否稳定 + 各图元顶点数
    prims = {}
    for kind, kw in (("cube", {"size": 1.0}),
                     ("cylinder_v8", {"vertices": 8, "radius": 1.0, "depth": 1.0}),
                     ("cylinder_v10", {"vertices": 10, "radius": 1.0, "depth": 1.0}),
                     ("cylinder_v12", {"vertices": 12, "radius": 1.0, "depth": 1.0}),
                     ("cylinder_v28", {"vertices": 28, "radius": 1.0, "depth": 1.0}),
                     ("sphere_s18r10", {"segments": 18, "ring_count": 10, "radius": 1.0}),
                     ("cone_v12", {"vertices": 12, "radius1": 1.0, "radius2": 0.0, "depth": 1.0}),
                     ("torus", {"major_segments": 12, "minor_segments": 6,
                                "major_radius": 1.0, "minor_radius": 0.25})):
        me = None
        try:
            if kind == "cube":
                bpy.ops.mesh.primitive_cube_add(**kw)
            elif kind.startswith("cylinder"):
                bpy.ops.mesh.primitive_cylinder_add(**kw)
            elif kind.startswith("sphere"):
                bpy.ops.mesh.primitive_uv_sphere_add(**kw)
            elif kind.startswith("cone"):
                bpy.ops.mesh.primitive_cone_add(**kw)
            elif kind == "torus":
                bpy.ops.mesh.primitive_torus_add(**kw)
            ob = bpy.context.active_object
            me = ob.data
            me.calc_loop_triangles()
            vs = [[round(float(c), 6) for c in v.co] for v in me.vertices]
            prims[kind] = {"verts": len(me.vertices), "polys": len(me.polygons),
                           "tris": len(me.loop_triangles),
                           "v0": vs[0] if vs else None,
                           "v1": vs[1] if len(vs) > 1 else None,
                           "bbox": [round(max(v[i] for v in vs) - min(v[i] for v in vs), 6)
                                    for i in range(3)]}
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception as exc:                               # noqa: BLE001
            prims[kind] = "ERR:%s: %s" % (type(exc).__name__, exc)
    out["prims"] = prims

    # 5) 引擎赋值实测（BLENDER_EEVEE vs BLENDER_EEVEE_NEXT）
    for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            bpy.context.scene.render.engine = eng
            out.setdefault("engine_set", {})[eng] = "OK"
        except Exception as exc:                               # noqa: BLE001
            out.setdefault("engine_set", {})[eng] = "%s" % type(exc).__name__

    print("PROBE " + json.dumps(out, ensure_ascii=False))


main()
