# 探针：① runtime.material.material_build 到底为什么失败（要完整 traceback）
#       ② 同配置两渲 → canonical PNG 的 sha256 是否相同（像素是否真的可复现）
import json, os, sys, traceback
REPO = r"\\wsl.localhost\Ubuntu\home\sixtyseven67\DSH\测试\测试\blender-wm-bridge"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
OUT = r"D:\DSH\blender\out\wmopt\shade\_selftest"
os.makedirs(OUT, exist_ok=True)
R = {}

from wm_bridge.factory.shade import bpy_shade as BS, material as M, render as Rc

# ---- ① runtime 路由的真实失败原因
rt = BS.runtime_modules()
R["runtime_modules"] = {k: (type(v).__name__ if v is not None else None) for k, v in rt.items() if k != "errors"}
R["runtime_import_errors"] = rt.get("errors")
probe = {}
try:
    rec = M.material_recipe("metal")
    R["hint"] = rec.get("runtime")
    mat = rt["material"].material_build("RT_probe", preset=rec["runtime"]["preset"],
                                       params=dict(rec["runtime"]["params"]))
    probe["ok"] = True
    probe["receipt_type"] = type(mat).__name__
    probe["receipt"] = (mat if isinstance(mat, str) else str(mat))[:400]
    _mm = bpy.data.materials.get("RT_probe")
    probe["resolved_material"] = (_mm.name if _mm else None)
    probe["nodes"] = ([(n.bl_idname) for n in _mm.node_tree.nodes] if _mm else None)
except Exception as e:
    probe["ok"] = False
    probe["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    probe["traceback"] = traceback.format_exc()[-1800:]
R["material_build_direct"] = probe

# ---- ② 确定性：同配置两渲 → 原始 PNG vs canonical PNG
try:
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=32, ring_count=16, location=(0, 0, 0.5))
    ob = bpy.context.object
    ob.name = "DET_ball"
    for p in ob.data.polygons:
        p.use_smooth = True
    BS.assign_material(ob, BS.apply_recipe(M.material_recipe("metal"), name="DET_mat"))
    rec = Rc.render_recipe("hero", subject_radius=0.5, scale=0.5)
    outs = []
    for k in (1, 2):
        r = BS.render_shot("det_%d" % k, OUT, rec, [ob])
        outs.append(r["png"])
    import importlib
    sys.path.insert(0, os.path.join(REPO, "wm_bridge", "factory", "shade", "_det"))
    pm = importlib.import_module("pngmetrics")
    det = {"run1": outs[0], "run2": outs[1],
           "raw_sha_equal": outs[0]["sha256"] == outs[1]["sha256"]}
    a, b = outs[0]["path"], outs[1]["path"]
    det["pixel_diff"] = pm.diff_files(a, b)
    ca = pm.canonical_file(a, os.path.join(OUT, "det_1.canon.png"))
    cb = pm.canonical_file(b, os.path.join(OUT, "det_2.canon.png"))
    det["canonical"] = {"run1": ca, "run2": cb, "sha_equal": ca["sha256"] == cb["sha256"]}
    # 逐 chunk 找出原始 PNG 的唯一差异来源
    da, db = open(a, "rb").read(), open(b, "rb").read()
    cha = pm.png_chunks(da); chb = pm.png_chunks(db)
    diffs = []
    for (ta, ba), (tb, bb) in zip(cha, chb):
        if ba != bb:
            diffs.append({"type": ta, "len_a": len(ba), "len_b": len(bb),
                          "a_head": ba[:48].decode("latin1", "replace"),
                          "b_head": bb[:48].decode("latin1", "replace")})
    det["raw_chunk_diffs"] = diffs
    det["chunk_types_equal"] = [t for t, _ in cha] == [t for t, _ in chb]
    R["determinism"] = det
except Exception as e:
    R["determinism_error"] = "%s: %s | %s" % (type(e).__name__, str(e)[:200], traceback.format_exc()[-800:])

with open(os.path.join(OUT, "probe_runtime_route.json"), "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
print("HEADLESS " + json.dumps(R, ensure_ascii=False, sort_keys=True, default=str))
