# 由 blender_rt_headless 执行：跑 bpy_shade 的端到端自检（真建材质 / 真渲 / 真出 GLB 回读）
import json
import os
import sys

REPO = r"<家目录>\DSH\测试\测试\blender-wm-bridge"
if REPO not in sys.path:
    sys.path.insert(0, REPO)

OUT = r"<外部产物根>\wmopt\shade\_selftest"
os.makedirs(OUT, exist_ok=True)

from wm_bridge.factory.shade import bpy_shade as BS          # noqa: E402
from wm_bridge.factory.shade import material as M           # noqa: E402
from wm_bridge.factory.shade import render as R             # noqa: E402

result = {"steps": {}, "errors": []}
try:
    # 0. 清掉默认场景里的方块（自检要干净起点）
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    result["steps"]["clear"] = True
except Exception as e:
    result["errors"].append("clear: %s: %s" % (type(e).__name__, str(e)[:200]))

# 1. 纯函数可测（配方逐字节相同 + 10 角色互异）
try:
    a = M.material_recipe("metal")
    b = M.material_recipe("metal")
    result["steps"]["pure_same_recipe"] = (M.canonical_json(a) == M.canonical_json(b),
                                          a["digest"])
    result["steps"]["pure_10_distinct"] = len(
        {M.material_recipe(r)["digest"] for r in M.ROLE_BOARD_ORDER}) == 10
    result["steps"]["runtime_routed_roles"] = [
        r for r in M.ROLE_BOARD_ORDER if M.material_recipe(r)["runtime"]]
except Exception as e:
    result["errors"].append("pure: %s: %s" % (type(e).__name__, str(e)[:200]))

# 2. 配方真的能落成 bpy 材质（含 runtime 路由），以及 NPR / 描边
built = {}
for role in M.ROLE_BOARD_ORDER:
    try:
        rec = M.material_recipe(role)
        mat = BS.apply_recipe(rec, name="ST_" + role)
        built[role] = {"material": mat.name,
                       "built_by": str(mat.get("shade_built_by") or "?"),
                       "runtime_error": str(mat.get("shade_runtime_error") or "") or None,
                       "nodes": len(mat.node_tree.nodes)}
    except Exception as e:
        result["errors"].append("build %s: %s: %s" % (role, type(e).__name__, str(e)[:200]))
result["steps"]["built_roles"] = built

try:
    npr = BS.apply_recipe(M.material_recipe("metal", style="npr"), name="ST_npr")
    result["steps"]["npr_built"] = {"nodes": len(npr.node_tree.nodes)}
except Exception as e:
    result["errors"].append("npr: %s: %s" % (type(e).__name__, str(e)[:200]))

# 3. 真渲一张 hero（含 studio 搭景）+ 出 GLB 并回读守恒
try:
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=48, ring_count=24,
                                        location=(0.0, 0.0, 0.5))
    ob = bpy.context.object
    ob.name = "ST_ball"
    for _p in ob.data.polygons:      # 5.2 没有 Object.shade_smooth()
        _p.use_smooth = True
    BS.assign_material(ob, built and bpy.data.materials[built["metal"]["material"]])
    rec = R.render_recipe("hero", subject_radius=0.5, scale=0.5)
    rep = BS.render_shot("hero", OUT, rec, [ob])
    result["steps"]["hero"] = {
        "png": rep["png"], "resolution": rep["resolution"], "samples": rep["samples"],
        "framing": rep.get("framing"), "color_management": rep.get("color_management"),
        "determinism_ok": (rep.get("determinism") or {}).get("ok"),
        "determinism_mismatch": (rep.get("determinism") or {}).get("mismatch"),
        "recipe_digest": rep.get("recipe_digest"),
    }
    # 确定性：同配置连渲第二张，比较**规范化 PNG** 的 sha256 与像素差
    rep2 = BS.render_shot("hero", OUT, R.render_recipe("hero", subject_radius=0.5,
                                                       scale=0.5), [ob])
    h1, h2 = rep["png"]["sha256"], rep2["png"]["sha256"]
    result["steps"]["determinism_two_runs"] = {
        "raw_sha_equal": h1 == h2,
        "raw_sha_1": h1, "raw_sha_2": h2,
        "ms_1": rep["png"]["ms"], "ms_2": rep2["png"]["ms"],
    }
    gp = os.path.join(OUT, "st.glb")
    exp = BS.export_glb(gp, [ob], kind="strict")
    positions = set()
    mw = ob.matrix_world
    for v in ob.data.vertices:
        p = mw @ v.co
        positions.add((round(p[0], 5), round(p[1], 5), round(p[2], 5)))
    ver = BS.verify_glb(gp, expect={
        "material_count": exp["material_count"],
        "triangles": sum(len(p.vertices) - 2 for p in ob.data.polygons),
        "positions": positions})
    result["steps"]["glb"] = {"export": exp, "verify": ver}
except Exception as e:
    import traceback
    result["errors"].append("render/glb: %s: %s | %s"
                            % (type(e).__name__, str(e)[:250],
                               traceback.format_exc()[-700:]))

# 4. 内建 selftest（另外一份更全的回路）
try:
    result["selftest"] = BS.selftest(os.path.join(OUT, "inner"))
except Exception as e:
    import traceback
    result["errors"].append("selftest: %s: %s | %s"
                            % (type(e).__name__, str(e)[:200],
                               traceback.format_exc()[-500:]))

result["ok"] = not result["errors"]
with open(os.path.join(OUT, "bpy_shade_selftest.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1, sort_keys=True)
print("HEADLESS " + json.dumps(result, ensure_ascii=False, sort_keys=True))
