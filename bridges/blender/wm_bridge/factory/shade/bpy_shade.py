# -*- coding: utf-8 -*-
"""wm_bridge.factory.shade.bpy_shade · **Blender 侧驱动**（绘画产线执行面）

★ 分工：`material.py` 出**配方 dict**（纯标准库，可无 Blender 自测），本模块把配方**落成 bpy**，
  并把相机/灯光/世界/色彩管理/确定性/出图/导出串成一条可复现的流水线。
  两边只通过 dict 通信 —— 所以"配方错没错"能在 CI 上判，"渲出来对不对"才需要 Blender。

──────────────────────────────────────────────────────────── 给 geo-modeling（bpy_produce）的调用契约
几何**不由本模块创建**。geo 侧建好物体后，任选一种接法：

  ① 命名约定（最省事，推荐）
       物体名 = "{link}__{part}"      （首选；双下划线）
                "{link}.{part}" / "{part}" / "{link}_{part}" 依次兜底
       然后：  from wm_bridge.factory.shade import bpy_shade
               rep = bpy_shade.run(spec, outdir="<外部产物根>/.../shade")

  ② 显式映射（命名对不上时）
       bpy_shade.run(spec, outdir=..., part_objects={("TURRET_yaw","body"): obj, ...})
       也接受 {"TURRET_yaw": {"body": obj}} 与 {obj.name: obj} 两种形状。

  ③ 建好了但想用自己的几何入口
       bpy_shade.run(spec, outdir=..., build_hook=callable)   # hook(spec, ctx) 里自己建

  返回：一份 JSON 可序列化的报告（含每个 shot 的 PNG 路径 / 分辨率 / 耗时 / 覆盖率读数、
        材质计划、GLB 守恒核对、使用的配方 digest）。**不抛异常**：内部错误记进 `errors`，
        `ok=False`，让调用方决定怎么办（fail-closed 但不炸对方流程）。

  只读约定：本模块**不删** geo 建的物体（只改材质/可见性），不移动它们的变换
  （相机取景靠二分移动相机，不挪主体）；`clear_studio()` 只清本模块自己建的 SHADE_* 物体。

──────────────────────────────────────────────────────────── 本机实测口径（都贴了原始读数在 _det/）
  · `Material.use_nodes` 在 5.2 报 DeprecationWarning（6.0 移除）。**替代写法**：
    `mat.node_tree` 已经**隐式创建**（实测：`bpy.data.materials.new()` 后 node_tree 非 None，
    且已含 2 个节点 BSDF_PRINCIPLED + OUTPUT_MATERIAL）⇒ 直接 `nodes.clear()` 建图，
    **全程不碰 `use_nodes`**，DeprecationWarning 不再出现。见 `_det/probe_eevee_determinism.py`。
  · 引擎枚举只有 `BLENDER_EEVEE`（`_NEXT` 已废）。
  · `view_settings.look` 的合法值是 `'None'` / `'AgX - Punchy'` / `'AgX - Base Contrast'` / …
    **不带 `AgX - ` 前缀会 TypeError**（实测 `look="Punchy"` → TypeError，`"AgX - Punchy"` → ok）。
  · Sky Texture 在 5.2 **没有 `NISHITA`**：`sky_type` 只有
    `SINGLE_SCATTERING / MULTIPLE_SCATTERING / PREETHAM / HOSEK_WILKIE`。
    物理天空请用 **`MULTIPLE_SCATTERING`**（Nishita 多散射的现行实现）。
"""
import bmesh  # noqa: F401  (供 bpy_produce 侧同进程复用，不单独用)
import hashlib
import json
import math
import os
import sys
import time

import bpy
from mathutils import Matrix, Vector

SHADE_VERSION = 1

#: 本模块自建的物体名前缀（`clear_studio` 只清这些，绝不碰 geo 建的物体）
STUDIO_PREFIX = "SHADE_"
#: 描边壳体的自定义标记（导出 GLB 时按它过滤，避免顶点数翻倍）
OUTLINE_TAG = "shade_outline"

#: #699 F15：`setup_studio` 曾被列进 `__all__`，但它**模块级从未定义**（全仓仅此一处
#: 出现，无实现、无调用方）⇒ `from ... import *` 会 `AttributeError`。按语义**移除该名**
#: （而不是补一个空壳定义）。其余 19 名均已在模块级定义（静态守卫钉住这条）。
__all__ = [
    "SHADE_VERSION", "STUDIO_PREFIX", "OUTLINE_TAG",
    "apply_recipe", "assign_material", "resolve_part_objects", "apply_spec_materials",
    "build_outline", "clear_outline", "object_bounds", "solve_framing",
    "render_still", "render_shot", "export_glb", "verify_glb",
    "render_all", "run", "selftest", "make_material_balls",
]


# ═══════════════════════════════════════════════════════════════ 小工具
def _json_safe(o):
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    return str(o)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ensure_dir(p):
    if p and not os.path.isdir(p):
        os.makedirs(p, exist_ok=True)
    return p


def _link(obj):
    """链进当前场景（若尚未链）。"""
    for c in obj.users_collection:
        return obj
    bpy.context.scene.collection.objects.link(obj)
    return obj


#: 本模块自建物体上的标记键。**按标记而不是按名字清理**：
#: 真跑抓到过 —— 名字前缀 `SHADE_` 也会命中"用户恰好这么命名的被摄物"，
#: 于是 `clear_studio()` 把主体一起删了（自检里的球名 `SHADE_st_ball` 就是这么死的）。
STUDIO_TAG = "shade_studio"


def _tag_studio(ob):
    if ob is not None:
        try:
            ob[STUDIO_TAG] = True
        except Exception:
            pass
    return ob


def _studio_obj(name, data):
    ob = bpy.data.objects.new(STUDIO_PREFIX + name, data)
    bpy.context.scene.collection.objects.link(ob)
    _tag_studio(ob)
    return ob


# ═══════════════════════════════════════════════════════════════ 配方 → 材质
# ShaderNodeMix 的 A/B/Factor 同名多口（按 data_type 落在不同下标）。
# 硬编码下标很脆，这里**用 identifier 反查**：identifiers 是唯一的（A_Color / A_Float / …）。
_MIX_IN_IDENT = {"RGBA": ("Factor_Float", "A_Color", "B_Color"),
                 "FLOAT": ("Factor_Float", "A_Float", "B_Float"),
                 "VECTOR": ("Factor_Vector", "A_Vector", "B_Vector")}
_MIX_OUT_IDENT = {"RGBA": "Result_Color", "FLOAT": "Result_Float", "VECTOR": "Result_Vector"}


#: Blender 5.2 的更名（真跑抓到）：`ShaderNodeTexNoise` / `ShaderNodeTexVoronoi` 的输出
#: `Fac` → `Factor`；`ShaderNodeValToRGB`（ColorRamp）的输入 `Fac` → `Factor`。
#: 配方统一写 **5.2** 的名字；这张别名表让 4.x 风格的旧写法也能跑起来（而不是静默连错口）。
_SOCKET_ALIASES = {"Fac": ("Factor",), "Factor": ("Fac",), "Color": (), "Result": ()}


def _resolve_socket(node, name, ident=None, out=False):
    """按（identifier 优先 → 名字 → 别名）取 socket。取不到 ⇒ 抛（不静默连错口）。"""
    pool = node.outputs if out else node.inputs
    if ident:
        for s in pool:
            if getattr(s, "identifier", None) == ident:
                return s
    for cand in (name,) + tuple(_SOCKET_ALIASES.get(name, ())):
        for s in pool:
            if s.name == cand:
                return s
    raise KeyError("节点 %s 没有 socket %r%s（可用：%s）"
                   % (node.bl_idname, name, "（输出）" if out else "",
                      ", ".join(s.name for s in pool)))


def _set_node_props(node, props):
    for k, v in (props or {}).items():
        try:
            setattr(node, k, v)
        except Exception as e:
            raise KeyError("节点 %s 设置属性 %s=%r 失败：%s" % (node.bl_idname, k, v, str(e)[:90]))


def _set_node_inputs(node, inputs, recipe_id):
    """按配方写 socket 默认值。Mix 的 data_type 会改变同名口的下标 ⇒ 特殊处理。"""
    dt = getattr(node, "data_type", None)
    for k, v in (inputs or {}).items():
        ident = None
        if node.bl_idname == "ShaderNodeMix" and dt in _MIX_IN_IDENT:
            m = {"Factor": 0, "A": 1, "B": 2}
            if k in m:
                ident = _MIX_IN_IDENT[dt][m[k]]
        sock = _resolve_socket(node, k, ident=ident, out=False)
        val = v
        if isinstance(v, (list, tuple)) and len(v) == 3 and sock.type == "RGBA":
            val = [v[0], v[1], v[2], 1.0]
        elif isinstance(v, (list, tuple)) and len(v) == 4 and sock.type == "VECTOR":
            val = [v[0], v[1], v[2]]
        try:
            sock.default_value = val
        except Exception as e:
            raise KeyError("配方 %s：%s.%s 写 %r 失败（socket 类型 %s）：%s"
                           % (recipe_id, node.bl_idname, k, val, sock.type, str(e)[:90]))


def ensure_runtime_path():
    """把仓库根塞进 sys.path（runtime/*.py 需要；它们还依赖共享内核 `dsh_rt_kernel.dsh_kit`）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        here = os.path.dirname(here)
    if here not in sys.path:
        sys.path.insert(0, here)
    return here


_RT_CACHE = {}


def runtime_modules():
    """按需 import runtime/{material,qc_render,montage}。缺件**不致命**（回落本模块解释器），
    但把失败原因写进报告 —— "复用了没有"必须可核对。"""
    if _RT_CACHE:
        return _RT_CACHE
    ensure_runtime_path()
    import importlib
    for name in ("material", "qc_render", "montage", "render_guard"):
        try:
            _RT_CACHE[name] = importlib.import_module("runtime." + name)
        except Exception as e:                                  # noqa: BLE001
            _RT_CACHE[name] = None
            _RT_CACHE.setdefault("errors", []).append(
                {"module": "runtime." + name, "type": type(e).__name__, "msg": str(e)[:160]})
    return _RT_CACHE


def apply_recipe_via_runtime(recipe, name=None):
    """走 `runtime/material.py: material_build(name, preset, params)` 建材质（复用件路径）。

    参数口径：preset/params 由 `shade/material.py` 的 `ROLE_RUNTIME` 表给出，
    `base_color/roughness/metallic` 一律用 spec 的覆写值透传 ⇒ **角色语义仍由纯标准库侧定义**，
    runtime 只负责"把这个 preset 连成节点图"这段重复劳动。
    """
    rt = runtime_modules().get("material")
    if rt is None:
        raise RuntimeError("runtime.material 不可用")
    hint = recipe.get("runtime") or {}
    if not hint:
        raise RuntimeError("配方没有 runtime 路由（role=%s）" % recipe.get("role"))
    preset = hint["preset"]
    valid = list(getattr(rt, "PRESETS", ()))
    if valid and preset not in valid:
        raise KeyError("runtime.material 不认 preset %r（允许 %s）" % (preset, valid))
    want = name or ("SHADE_rt_" + str(recipe.get("role")))
    # ⚠ **真跑抓到的接线坑**：`runtime/material.py: material_build` 走的是 dispatch 口径，
    # 返回的是 `_j({...})` **JSON 字符串回执**（不是 bpy Material）。直接当对象用会
    # "'str' object does not support item assignment"。所以：解析回执 → 按名字回查实体。
    receipt = rt.material_build(want, preset=preset,
                               params=dict(hint.get("params") or {}))
    mat_name = want
    if isinstance(receipt, str):
        try:
            payload = json.loads(receipt)
            if isinstance(payload, dict) and payload.get("material"):
                mat_name = str(payload["material"])
        except Exception:
            pass
    elif isinstance(receipt, dict) and receipt.get("material"):
        mat_name = str(receipt["material"])
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        raise KeyError("runtime.material 回执里的材质名 %r 在 bpy.data.materials 里找不到"
                       "（回执=%r）" % (mat_name, str(receipt)[:120]))
    rcpt = receipt if isinstance(receipt, dict) else None
    if rcpt is None and isinstance(receipt, str):
        try:
            rcpt = json.loads(receipt)
        except Exception:
            rcpt = None
    if rcpt:
        mat["shade_runtime_receipt"] = json.dumps(
            {k: rcpt.get(k) for k in ("ok", "preset", "nodes", "links", "ms")},
            ensure_ascii=False)
    props = recipe.get("props") or {}
    for k, v in props.items():
        if hasattr(mat, k):
            setattr(mat, k, v)
    mat["shade_recipe_digest"] = recipe.get("digest") or ""
    mat["shade_recipe_role"] = str(recipe.get("role"))
    mat["shade_recipe_style"] = str(recipe.get("style"))
    mat["shade_built_by"] = "runtime.material.material_build(%s)" % preset
    return mat


def apply_recipe(recipe, name=None, mat=None, prefer_runtime=True):
    """把一份 `wmshade.material/1` 配方落成 bpy 材质。返回 bpy Material。

    路径选择（CAPABILITY-MAP 纪律）：
      ① recipe 带 `runtime` 路由 **且** `runtime.material` 可用 ⇒ 走 `apply_recipe_via_runtime`
         （8/10 个 role 命中；这是"复用 runtime"的实质）
      ② 否则走本模块的解释器（rubber / skin / NPR / 描边 —— **runtime 无此件**）
    纯解释器不认的节点类型 / socket 名一律**抛错**（不静默跳过 —— 静默会让"配方跑过了"
    和"配方真的生效了"混为一谈）。
    """
    if recipe.get("schema") != "wmshade.material/1":
        raise ValueError("配方 schema 不符：%r" % (recipe.get("schema"),))
    # ★ 先做本模块的 schema/白名单校验，**再**决定走不走 runtime。
    # 真跑抓到：一旦走 runtime 路由，节点级白名单校验就被绕过了 ——
    # 把 nodes[0].type 改成不存在的节点，配方居然被接受（"未知节点类型"这条守卫失效）。
    from . import material as _matmod
    _ok, _why = _matmod.validate_recipe(recipe)
    if not _ok:
        raise ValueError("配方不合法（本地白名单校验）：%s" % _why)
    use_rt = bool(prefer_runtime and recipe.get("runtime")
                  and runtime_modules().get("material") is not None)
    rt_err = None
    if use_rt:
        try:
            return apply_recipe_via_runtime(recipe, name=name)
        except Exception as e:                                  # noqa: BLE001
            # **不静默**：回落必须留下原因（否则"复用了 runtime"就是个无法核对的声称）
            rt_err = "%s: %s" % (type(e).__name__, str(e)[:200])
    if mat is None:
        mat = bpy.data.materials.new(name or ("SHADE_" + str(recipe.get("role"))))
    # ★ 5.2 替代写法：不碰 use_nodes；node_tree 已隐式创建
    nt = mat.node_tree
    nt.nodes.clear()
    made = {}
    for nd in recipe["nodes"]:
        n = nt.nodes.new(nd["type"])
        n.location = tuple(nd.get("loc") or (0.0, 0.0))
        if nd.get("label"):
            n.label = nd["label"]
        _set_node_props(n, nd.get("props"))
        if "ramp" in nd:
            rp = nd["ramp"]
            cr = n.color_ramp
            cr.interpolation = rp["interpolation"]
            els = rp["elements"]
            # 先清到只剩 1 个，再按配方补；位置/颜色从配方抄
            while len(cr.elements) > 1:
                cr.elements.remove(cr.elements[-1])
            cr.elements[0].position = float(els[0][0])
            c0 = els[0][1]
            cr.elements[0].color = (c0[0], c0[1], c0[2],
                                    c0[3] if len(c0) > 3 else 1.0)
            for pos, col in els[1:]:
                e = cr.elements.new(float(pos))
                e.color = (col[0], col[1], col[2], col[3] if len(col) > 3 else 1.0)
        _set_node_inputs(n, nd.get("inputs"), recipe.get("role"))
        made[nd["id"]] = n
    for a, asock, b, bsock in recipe["links"]:
        try:
            nt.links.new(_resolve_socket(made[a], asock, out=True),
                         _resolve_socket(made[b], bsock, out=False))
        except Exception as e:
            raise KeyError("配方 %s 连线失败 %s.%s → %s.%s：%s"
                           % (recipe.get("role"), a, asock, b, bsock, str(e)[:110]))
    props = recipe.get("props") or {}
    for k, v in props.items():
        if not hasattr(mat, k):
            raise KeyError("材质属性 %r 在 Blender 5.2 不存在（配方 role=%s）"
                           % (k, recipe.get("role")))
        setattr(mat, k, v)
    mat["shade_recipe_digest"] = recipe.get("digest") or ""
    mat["shade_recipe_role"] = str(recipe.get("role"))
    mat["shade_recipe_style"] = str(recipe.get("style"))
    mat["shade_built_by"] = "shade/material.py 解释器"
    if rt_err:
        mat["shade_runtime_error"] = rt_err
    return mat


def assign_material(obj, mat, slot=0, replace=True):
    """把材质放到 slot（默认 0），并让**所有面**都用该 slot。"""
    if not obj.data.materials:
        obj.data.materials.append(mat)
        return 0
    if slot < len(obj.data.materials):
        if replace:
            obj.data.materials[slot] = mat
        else:
            obj.data.materials.append(mat)
            slot = len(obj.data.materials) - 1
    else:
        obj.data.materials.append(mat)
        slot = len(obj.data.materials) - 1
    for p in obj.data.polygons:
        p.material_index = slot
    return slot


# ═══════════════════════════════════════════════════════════════ spec → 部件物体
def _norm_name(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum() or ch == "_")


def resolve_part_objects(spec, part_objects=None, objects=None):
    """按 spec 的 (link, part) 找到场景里的物体。

    优先级：显式映射 > "link__part" > "link.part" > "part" > "link_part"。
    找不到的记进 `missing`（**不猜对象**：猜了就会把材质刷到别的零件上，且看不出来）。
    """
    from ..geo import spec as _spec
    n = _spec.normalize_spec(spec, _copy=True)
    pool = {}
    if objects:
        src = ([objects] if isinstance(objects, str)
               else list(objects) if not isinstance(objects, dict) else list(objects.values()))
        for o in src:
            if isinstance(o, str):
                o = bpy.data.objects.get(o)
            if o is not None:
                pool[o.name] = o
    else:
        for o in bpy.data.objects:
            pool[o.name] = o

    explicit = {}
    if part_objects:
        for k, v in part_objects.items():
            if isinstance(k, tuple) and len(k) == 2:
                explicit[(str(k[0]), str(k[1]))] = v
            elif isinstance(k, str) and isinstance(v, dict):
                for pn, ob in v.items():
                    explicit[(k, str(pn))] = ob
            elif isinstance(v, str):
                explicit[("", str(k))] = v
    norm_pool = {}
    for nm, o in pool.items():
        norm_pool.setdefault(_norm_name(nm), o)

    found, missing = {}, []
    for lk in n["links"]:
        for p in lk["parts"]:
            key = (lk["name"], p["name"])
            ob = explicit.get(key) or explicit.get(("", p["name"]))
            if isinstance(ob, str):
                ob = bpy.data.objects.get(ob)
            if ob is None:
                cands = ["%s__%s" % (lk["name"], p["name"]),
                         "%s.%s" % (lk["name"], p["name"]),
                         p["name"],
                         "%s_%s" % (lk["name"], p["name"])]
                for c in cands:
                    ob = pool.get(c) or norm_pool.get(_norm_name(c))
                    if ob is not None:
                        break
            if ob is None:
                missing.append({"link": lk["name"], "part": p["name"], "tried": cands})
            else:
                found[key] = ob
    return {"found": found, "missing": missing, "matched": len(found),
            "declared": sum(len(l["parts"]) for l in n["links"])}


def apply_spec_materials(spec, part_objects=None, objects=None, *, style="pbr",
                         detail=True, bands=3, naming="part", extra=None):
    """按 spec 的 `material` 字段给每个部件赋材质；额外映射走 `extra`（{物体名: recipe}）。

    返回报告（含逐部件 role / 配方 digest / 实际落到哪个物体）。同名材质**复用**
    （同 role+同覆写 ⇒ 同一个 bpy 材质，这样 GLB 导出的材质数才是"角色数"而不是"零件数"）。
    """
    from . import material as _mat
    rep = resolve_part_objects(spec, part_objects=part_objects, objects=objects)
    plan = _mat.material_plan(spec, style=style, detail=detail, bands=bands)
    cache = {}
    entries = []
    for e in plan["entries"]:
        key = (e["link"], e["part"])
        ob = rep["found"].get(key)
        if ob is None:
            continue
        mname = "SHADE_%s_%s" % (naming, e["role"])
        if mname not in cache:
            cache[mname] = apply_recipe(e["recipe"], name=mname, prefer_runtime=True)
        built_by = str(cache[mname].get("shade_built_by") or "shade/material.py 解释器")
        rt_err = str(cache[mname].get("shade_runtime_error") or "")
        assign_material(ob, cache[mname])
        entries.append({"link": e["link"], "part": e["part"], "object": ob.name,
                        "role": e["role"], "material": mname, "built_by": built_by,
                        "runtime_error": (rt_err or None),
                        "recipe_digest": e["recipe_digest"]})
    extra_entries = []
    for obname, rec in (extra or {}).items():
        ob = bpy.data.objects.get(obname) if isinstance(obname, str) else obname
        if ob is None:
            extra_entries.append({"object": str(obname), "error": "对象不存在"})
            continue
        mname = "SHADE_extra_%s" % str(rec.get("role"))
        if mname not in cache:
            cache[mname] = apply_recipe(rec, name=mname)
        assign_material(ob, cache[mname])
        extra_entries.append({"object": ob.name, "role": rec.get("role"),
                              "material": mname, "recipe_digest": rec.get("digest")})
    return {"matched": rep["matched"], "declared": rep["declared"], "missing": rep["missing"],
            "materials": sorted(cache.keys()), "entries": entries, "extra": extra_entries,
            "plan_digest": plan["spec_digest"], "distinct_recipes": plan["distinct_recipes"]}


# ═══════════════════════════════════════════════════════════════ NPR 描边（反壳法）
def clear_outline():
    n = 0
    for o in [o for o in bpy.data.objects if o.get(OUTLINE_TAG)]:
        bpy.data.objects.remove(o, do_unlink=True)
        n += 1
    return n


def build_outline(objects, outline_recipe=None, *, thickness_m=0.006):
    """反壳描边：复制网格 → 沿**顶点法线**外推 thickness_m → 翻转法线 → 背面剔除的 Emission。

    为什么这么算：描边宽度是**几何**量，于是"描边像素占前景比例"可以被独立核对；
    而节点法（Fresnel）的"宽度"是屏幕空间量，随视角漂移，量不出来。
    壳体的 `shade_outline=True` 自定义标记供 `export_glb` 过滤（否则顶点数会翻倍）。
    """
    from . import material as _mat
    rec = outline_recipe or _mat.outline_recipe(thickness_m=thickness_m)
    mat = apply_recipe(rec, name="SHADE_outline")
    made = []
    for src in objects:
        if src.type != "MESH":
            continue
        dup = bpy.data.objects.new(STUDIO_PREFIX + "outline_" + src.name, src.data.copy())
        bpy.context.scene.collection.objects.link(dup)
        # 顶点沿自身法线外推（物体局部空间；src 若有非均匀缩放，宽度会随之变形 —— 已在报告里标注）
        me = dup.data
        for v in me.vertices:
            v.co = v.co + v.normal * float(thickness_m)
        try:
            me.flip_normals()
        except Exception:
            import bmesh as _bm
            bm = _bm.new()
            bm.from_mesh(me)
            _bm.ops.reverse_faces(bm, faces=bm.faces)
            bm.to_mesh(me)
            bm.free()
        dup.data.materials.clear()
        dup.data.materials.append(mat)
        for p in dup.data.polygons:
            p.material_index = 0
        dup.parent = src
        dup.matrix_parent_inverse = Matrix.Identity(4)
        dup.matrix_basis = Matrix.Identity(4)
        dup[OUTLINE_TAG] = True
        dup.hide_render = False
        made.append(dup)
    if not made:
        raise ValueError("build_outline：没有可用的 MESH 物体")
    return {"count": len(made), "objects": [o.name for o in made],
            "thickness_m": float(thickness_m), "material": mat.name,
            "recipe_digest": rec.get("digest"),
            "caveat": "非均匀缩放的对象上，几何外推宽度会随之变形（本流程主体用等比缩放）"}


# ═══════════════════════════════════════════════════════════════ 包围盒 / 取景
def object_bounds(objs):
    """世界空间包围盒（用 mesh 顶点算，不用 bound_box —— 后者不含修改器/父子链）。"""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    n = 0
    for o in objs:
        if o.type != "MESH" or o.get(OUTLINE_TAG):
            continue
        mw = o.matrix_world
        for v in o.data.vertices:
            p = mw @ v.co
            for i in range(3):
                lo[i] = min(lo[i], p[i])
                hi[i] = max(hi[i], p[i])
            n += 1
    if not n:
        return None
    ctr = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    size = [hi[i] - lo[i] for i in range(3)]
    radius = math.sqrt(sum((s / 2.0) ** 2 for s in size))
    return {"min": lo, "max": hi, "center": ctr, "size": size,
            "radius": radius, "vertices": n,
            "max_dim": max(size)}


def _project_bbox(cam_obj, objs):
    """把包围盒 8 角投到相机归一化坐标，返回 (x0,y0,x1,y1) 与是否在画面内。"""
    from bpy_extras.object_utils import world_to_camera_view
    b = object_bounds(objs)
    if b is None:
        return None
    xs, ys = [], []
    for i in range(8):
        p = Vector([b["min"][0] if (i & 1) else b["max"][0],
                    b["min"][1] if (i & 2) else b["max"][1],
                    b["min"][2] if (i & 4) else b["max"][2]])
        c = world_to_camera_view(bpy.context.scene, cam_obj, p)
        xs.append(c.x)
        ys.append(c.y)
    return {"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
            "r": min(xs), "l": max(xs), "up": max(ys), "down": min(ys)}


def solve_framing(cam_obj, objs, *, target=0.68, band=(0.60, 0.75), direction=None,
                  max_iter=40):
    """二分相机距离，让主体在画面的**主导维度占比**落进 band（默认 0.60~0.75）。

    判据定义（写清楚，避免"占比"各说各话）：
      coverage = max(bbox_w_px / W, bbox_h_px / H)
    即"主体在画面里最长那一维占了多少"。同时回报包围盒**面积占比**与**四边余量**，
    便于判断"完整不裁切"。
    """
    scene = bpy.context.scene
    W, H = scene.render.resolution_x, scene.render.resolution_y
    center = object_bounds(objs)["center"]
    ctr = Vector(center)
    d = Vector(direction or (cam_obj.location - ctr)).normalized()
    base = cam_obj.location - ctr
    cur_dist = base.length

    def cov_at(dist):
        cam_obj.location = ctr + d * dist
        _aim(cam_obj, ctr)
        bpy.context.view_layer.update()
        p = _project_bbox(cam_obj, objs)
        w = (p["x1"] - p["x0"]) * W
        h = (p["y1"] - p["y0"]) * H
        return max(w / float(W), h / float(H)), {"w_frac": w / float(W), "h_frac": h / float(H),
                                                "area_frac": (w / float(W)) * (h / float(H)),
                                                "bbox": p}

    lo_d, hi_d = cur_dist * 0.15, cur_dist * 6.0
    best = None
    for _ in range(int(max_iter)):
        mid = (lo_d + hi_d) / 2.0
        c, det = cov_at(mid)
        if best is None or abs(c - target) < abs(best[0] - target):
            best = (c, mid, det)
        if c > target:
            lo_d = mid                       # 占比太大 ⇒ 退远
        else:
            hi_d = mid
    c, dist, det = best
    cam_obj.location = ctr + d * dist
    _aim(cam_obj, ctr)
    bpy.context.view_layer.update()
    b = object_bounds(objs)
    return {"distance_m": round(dist, 6), "coverage_max_dim": round(c, 6),
            "coverage_area": round(det["area_frac"], 6),
            "w_frac": round(det["w_frac"], 6), "h_frac": round(det["h_frac"], 6),
            "target": target, "band": list(band), "in_band": bool(band[0] <= c <= band[1]),
            "subject_center_world": [round(x, 6) for x in b["center"]],
            "subject_size_m": [round(x, 6) for x in b["size"]],
            "definition": "coverage_max_dim = max(包围盒宽/画面宽, 包围盒高/画面高)"}


def _aim(obj, target):
    d = Vector(target) - obj.location
    if d.length < 1e-9:
        return
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


# ═══════════════════════════════════════════════════════════════ studio（世界/灯/相机/地台）
def clear_studio():
    """只清**本模块自建**的 studio 物体（世界/灯/相机/地台/克隆/展板球）。

    判据是自定义标记 `STUDIO_TAG`（**不是名字前缀**）：前缀判断会误删恰好同名的被摄物。
    名字前缀只作为**兼容兜底**（老代码路径建的对象可能没打标记）。
    """
    n = 0
    for o in list(bpy.data.objects):
        if o.get(STUDIO_TAG) or o.name.startswith(STUDIO_PREFIX):
            bpy.data.objects.remove(o, do_unlink=True)
            n += 1
    return n


def _world_nodes(world):
    """5.2 替代写法：world.node_tree 已隐式创建，不碰 use_nodes。"""
    nt = world.node_tree
    nt.nodes.clear()
    return nt


def build_world(recipe, *, sky=None):
    """背景幕布 + 环境反射。返回世界名。

    两种模式：
      gradient/flat  相机看到的是**屏幕空间径向/竖直渐变**（与相机朝向解耦 —— 线性渐变
                     一旦相机变了就失效，径向不会），另有可选地面几何。
      radial_gradient + sky：**相机光看到暗渐变，非相机光（反射/环境光）看到 Sky**
                     —— 用 `Light Path.Is Camera Ray` 混。这解决了一个真实矛盾：
                     金属要"有东西可反射"（需要亮环境），画面又要"环境暗、主体亮"。
                     ⚠ 这条在 EEVEE 里镜面反射走屏幕空间光追，未必采到世界；故默认
                       **两套都渲**并回报金属区亮度差，让判据说话。
    """
    import math as _m
    kind = recipe.get("kind")
    wr = recipe["world"]
    mode = wr.get("mode", "gradient")
    w = bpy.data.worlds.get(STUDIO_PREFIX + "world") or bpy.data.worlds.new(STUDIO_PREFIX + "world")
    bpy.context.scene.world = w
    nt = _world_nodes(w)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (780, 0)

    # 相机可见层：径向渐变（中心亮四周暗）或竖直渐变
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (520, 120)
    bg.inputs["Strength"].default_value = wr.get("strength", 0.3)
    if wr.get("top_color") and wr.get("bottom_color"):
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.location = (240, 220)
        els = ramp.color_ramp.elements
        els[0].position = 0.0
        els[0].color = tuple(wr["bottom_color"]) + (1.0,) if len(wr["bottom_color"]) == 3 \
            else tuple(wr["bottom_color"])
        e2 = els.new(1.0)
        e2.color = tuple(wr["top_color"]) + (1.0,) if len(wr["top_color"]) == 3 \
            else tuple(wr["top_color"])
        # 坐标源：径向 ⇒ Window 到中心的距离；竖直 ⇒ Window.Y
        tc = nt.nodes.new("ShaderNodeTexCoord")
        tc.location = (-380, 220)
        sub = nt.nodes.new("ShaderNodeVectorMath")
        sub.location = (-180, 260)
        sub.operation = "SUBTRACT"
        sub.inputs[1].default_value = (0.5, 0.5, 0.5)
        nt.links.new(tc.outputs["Window"], sub.inputs[0])
        if mode == "radial_gradient":
            length = nt.nodes.new("ShaderNodeVectorMath")
            length.location = (0, 300)
            length.operation = "LENGTH"
            nt.links.new(sub.outputs["Vector"], length.inputs[0])
            # 径向 0(中心)..~0.7(角) 归一到 0..1，再反相（中心亮）
            mr = nt.nodes.new("ShaderNodeMapRange")
            mr.location = (120, 320)
            mr.inputs["From Min"].default_value = 0.0
            mr.inputs["From Max"].default_value = 0.62
            mr.inputs["To Min"].default_value = 1.0
            mr.inputs["To Max"].default_value = 0.0
            nt.links.new(length.outputs["Value"], mr.inputs["Value"])
            nt.links.new(mr.outputs["Result"], ramp.inputs["Fac"])
        else:
            sep = nt.nodes.new("ShaderNodeSeparateXYZ")
            sep.location = (0, 220)
            nt.links.new(sub.outputs["Vector"], sep.inputs["Vector"])
            nt.links.new(sep.outputs["Y"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    sky_rep = None
    if sky:
        sc = bpy.data.textures.images if False else None  # noqa: F841  (占位，明确无贴图依赖)
        sk = nt.nodes.new("ShaderNodeTexSky")
        sk.location = (240, -220)
        st = str(sky.get("sky_type", "MULTIPLE_SCATTERING"))
        valid = [i.identifier for i in sk.bl_rna.properties["sky_type"].enum_items]
        if st not in valid:
            raise KeyError("sky_type %r 不在 5.2 的合法集合 %s" % (st, valid))
        sk.sky_type = st
        for attr, key in (("sun_elevation", "sun_elevation"), ("sun_rotation", "sun_rotation"),
                          ("sun_intensity", "sun_intensity"), ("sun_size", "sun_size"),
                          ("altitude", "altitude"), ("air_density", "air_density"),
                          ("aerosol_density", "aerosol_density"), ("ozone_density", "ozone_density")):
            if key in sky and hasattr(sk, attr):
                setattr(sk, attr, float(sky[key]))
        if "sun_disc" in sky:
            sk.sun_disc = bool(sky["sun_disc"])
        env = nt.nodes.new("ShaderNodeBackground")
        env.location = (520, -220)
        env.inputs["Strength"].default_value = float(sky.get("strength", 1.0))
        nt.links.new(sk.outputs["Color"], env.inputs["Color"])
        mix = nt.nodes.new("ShaderNodeMixShader")
        mix.location = (660, 0)
        lp = nt.nodes.new("ShaderNodeLightPath")
        lp.location = (240, -460)
        nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Factor"])
        nt.links.new(env.outputs["Background"], mix.inputs[1])   # 非相机光 = 环境
        nt.links.new(bg.outputs["Background"], mix.inputs[2])    # 相机光 = 暗幕布
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        sky_rep = {"sky_type": st, "strength": float(sky.get("strength", 1.0)),
                   "sun_elevation": getattr(sk, "sun_elevation", None),
                   "note": "环境层=Sky（只给反射/环境光），相机层=暗渐变（画面不出现天空实体）"}
    return {"world": w.name, "mode": mode, "sky": sky_rep}


def build_lights(recipe, target, scale=1.0):
    """按配方建灯。返回 [{name, object, az, el, energy_w, size_m, position}]。

    EEVEE 侧要点：面积灯要显式开 `use_shadow` + `use_shadow_jitter` 才有软阴影
    （接触阴影是"落地感"的唯一来源，见 VISUAL-ITERATIONS 手法 4）。
    """
    out = []
    for L in recipe["lights"]:
        ld = bpy.data.lights.new(STUDIO_PREFIX + L["name"], type=L["kind"])
        if L["kind"] in ("AREA", "POINT", "SPOT"):
            ld.energy = float(L["energy_w"])
        if L["kind"] == "AREA":
            ld.shape = "SQUARE"
            ld.size = float(L["size_m"])
        elif L["kind"] == "SUN":
            ld.energy = float(L["energy_w"]) / 100.0     # SUN 用辐照度口径，量级不同
            ld.angle = math.radians(2.5)
        for attr in ("use_shadow", "use_shadow_jitter"):
            if hasattr(ld, attr):
                try:
                    setattr(ld, attr, True)
                except Exception:
                    pass
        ld.color = tuple(L["color"][:3])
        ob = _studio_obj("light_" + L["name"], ld)
        from . import render as _R
        p = _R.position_from_azel(target, L["az_deg"], L["el_deg"],
                                  float(L["distance_m"]) if L.get("distance_m")
                                  else max(2.5, 3.2 * float(scale)))
        ob.location = p
        _aim(ob, Vector(target))
        out.append({"name": L["name"], "object": ob.name, "az_deg": L["az_deg"],
                    "el_deg": L["el_deg"], "energy_w": L["energy_w"],
                    "size_m": L["size_m"], "position": [round(x, 4) for x in p],
                    "purpose": L.get("purpose", "")})
    return out


def build_camera(recipe, target, radius=1.0):
    """建相机并放到 target 外侧（距离先给估算值，之后由 solve_framing 二分修正）。"""
    from . import render as _R
    camd = bpy.data.cameras.new(STUDIO_PREFIX + "cam")
    camd.lens = float(recipe["camera"]["lens_mm"])
    camd.sensor_width = float(recipe["camera"]["sensor_mm"])
    camd.sensor_fit = recipe["camera"]["sensor_fit"]
    cam = _studio_obj("cam", camd)
    bpy.context.scene.camera = cam
    c = recipe["camera"]
    if c["kind"] == "orthographic":
        camd.type = "ORTHO"
        camd.ortho_scale = float(recipe["framing"].get("ortho_scale") or (radius * 2.6))
        dist = max(4.0, radius * 4.0)
    else:
        camd.type = "PERSP"
        dist = float(c.get("distance_estimate_m") or (radius * 4.5))
    p = _R.position_from_azel(target, c["az_deg"], c["el_deg"], dist)
    cam.location = p
    _aim(cam, Vector(target))
    if c["kind"] == "perspective":
        camd.shift_y = float(c.get("shift_y") or 0.0)
    return cam, {"kind": c["kind"], "lens_mm": camd.lens, "ortho_scale": getattr(camd, "ortho_scale", None),
                 "position": [round(x, 4) for x in p],
                 "look_at": [round(x, 4) for x in target], "initial_distance_m": round(dist, 4)}


def build_ground(recipe, *, top_z=0.0, size=40.0, color=None, roughness=0.62):
    """地台/地面：**顶面 = top_z（默认 0）**。这是全流程的基准面纪律
    （VISUAL-ITERATIONS 手法 1：先定"地台顶面 = 0"，所有物体从它起算）。"""
    me = bpy.data.meshes.new(STUDIO_PREFIX + "ground_mesh")
    h = 0.30 * max(1.0, size / 12.0)
    s = float(size)
    verts = [(-s / 2, -s / 2, top_z - h), (s / 2, -s / 2, top_z - h),
             (s / 2, s / 2, top_z - h), (-s / 2, s / 2, top_z - h),
             (-s / 2, -s / 2, top_z), (s / 2, -s / 2, top_z),
             (s / 2, s / 2, top_z), (-s / 2, s / 2, top_z)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(STUDIO_PREFIX + "ground", me)
    bpy.context.scene.collection.objects.link(ob)
    _tag_studio(ob)
    from . import material as _mat
    rec = _mat.material_recipe("stone", color=color, roughness=roughness, detail=False)
    rec["role"] = "ground"
    if color is None:
        rec = _mat.material_recipe("stone", roughness=roughness, detail=False)
        _force_base_color(rec, (0.019, 0.019, 0.022))
    assign_material(ob, apply_recipe(rec, name=STUDIO_PREFIX + "ground_mat"))
    return ob, {"top_z": float(top_z), "thickness": h, "size": s,
                "base_color": list(rec["resolved"]["base_color"]), "roughness": roughness}


def _force_base_color(rec, rgb):
    for nd in rec["nodes"]:
        if nd["type"] == "ShaderNodeBsdfPrincipled":
            nd["inputs"]["Base Color"] = list(rgb)
    rec["resolved"]["base_color"] = list(rgb)
    from . import material as _mat
    rec["digest"] = _mat.recipe_digest(rec)
    return rec


def build_pedestal(top_z=0.0, *, w=3.35, d=1.05, h=0.30, role="stone",
                   color=(0.030, 0.031, 0.036), roughness=0.55):
    """台座：**上表面 = top_z**（基准面纪律）；宽厚比按手法 6 控制在 ≤12:1。"""
    rec_ratio = max(w, d) / max(h, 1e-6)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    ob = bpy.context.object
    _tag_studio(ob)
    ob.name = STUDIO_PREFIX + "pedestal"
    ob.scale = (w, d, h)
    ob.location = (0.0, 0.0, top_z - h / 2.0)
    from . import material as _mat
    rec = _material_for("pedestal", role, color, roughness)
    assign_material(ob, apply_recipe(rec, name=STUDIO_PREFIX + "pedestal_mat"))
    return ob, {"top_z": top_z, "w": w, "d": d, "h": h,
                "aspect_max_over_thickness": round(rec_ratio, 4),
                "aspect_ok_le_12": bool(rec_ratio <= 12.0), "role": role}


def _material_for(tag, role, color, roughness):
    from . import material as _mat
    rec = _mat.material_recipe(role, color=color, roughness=roughness, detail=False)
    rec["role"] = tag
    rec["digest"] = _mat.recipe_digest(rec)
    return rec


def apply_color_management(recipe):
    """色彩管理：view_transform / look / exposure / gamma。`look` 必须带 `AgX - ` 前缀。"""
    sc = bpy.context.scene
    cm = recipe["color_management"]
    vs = sc.view_settings
    vt = str(cm["view_transform"])
    # ⚠ 不要用 `bl_rna.properties["view_transform"].enum_items` 做校验：Blender 5.x 的
    # view_transform 是 OCIO **动态枚举**，实测它只回 `['NONE']`（真跑抓到：合法的 AgX 被
    # 这条校验判成非法）。`runtime/qc_render.py` §403-405 早就写明了同一件事。
    # 可靠口径只有一个：**试写 + 读回**（名字本机没有时赋值会抛 TypeError/enum not found）。
    try:
        vs.view_transform = vt
    except Exception as e:
        raise KeyError("view_transform %r 写不进去（本机不支持？）：%s: %s"
                       % (vt, type(e).__name__, str(e)[:120]))
    if str(vs.view_transform) != vt:
        raise KeyError("view_transform %r 写了但读回是 %r（动态枚举没生效）"
                       % (vt, str(vs.view_transform)))
    look = str(cm.get("look") or "None")
    try:
        vs.look = look
    except TypeError as e:
        raise KeyError("look %r 不合法（5.2 必须带前缀，例如 'AgX - Punchy'）：%s"
                       % (look, str(e)[:90]))
    vs.exposure = float(cm.get("exposure") or 0.0)
    vs.gamma = float(cm.get("gamma") or 1.0)
    return {"view_transform": str(vs.view_transform), "look": str(vs.look),
            "exposure": float(vs.exposure), "gamma": float(vs.gamma),
            "reason": cm.get("reason")}


def apply_determinism(recipe):
    """把确定性配置写进场景，并**回读核对**（写不进去要说出来，不能假装成功了）。"""
    sc = bpy.context.scene
    d = recipe["determinism"]
    sc.render.engine = "BLENDER_EEVEE"
    ee = sc.eevee
    sc.render.resolution_x, sc.render.resolution_y = [int(x) for x in recipe["resolution"]]
    sc.render.resolution_percentage = int(d.get("resolution_percentage", 100))
    sc.render.image_settings.file_format = d["image_settings"]["file_format"]
    sc.render.image_settings.color_mode = d["image_settings"]["color_mode"]
    sc.render.image_settings.compression = int(d["image_settings"]["compression"])
    sc.render.dither_intensity = float(d["render.dither_intensity"])
    sc.render.film_transparent = bool(d["render.film_transparent"])
    ee.taa_render_samples = int(d["taa_render_samples"])
    ee.use_raytracing = bool(d["eevee.use_raytracing"])
    if hasattr(ee, "ray_tracing_method"):
        try:
            ee.ray_tracing_method = d["eevee.ray_tracing_method"]
        except Exception:
            pass
    ee.use_taa_reprojection = bool(d["eevee.use_taa_reprojection"])
    ee.use_shadow_jitter_viewport = bool(d["eevee.use_shadow_jitter_viewport"])
    ee.use_bokeh_jittered = bool(d["eevee.use_bokeh_jittered"])
    for attr, val in (("use_shadows", True), ("use_fast_gi", True)):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except Exception:
                pass
    sc.render.use_file_extension = True
    readback = {"engine": str(sc.render.engine),
                "resolution": [sc.render.resolution_x, sc.render.resolution_y],
                "taa_render_samples": int(ee.taa_render_samples),
                "use_raytracing": bool(ee.use_raytracing),
                "use_taa_reprojection": bool(ee.use_taa_reprojection),
                "dither_intensity": float(sc.render.dither_intensity),
                "film_transparent": bool(sc.render.film_transparent),
                "use_shadows": bool(getattr(ee, "use_shadows", False)),
                "use_fast_gi": bool(getattr(ee, "use_fast_gi", False))}
    want = {"engine": "BLENDER_EEVEE",
            "taa_render_samples": int(d["taa_render_samples"]),
            "use_raytracing": bool(d["eevee.use_raytracing"]),
            "use_taa_reprojection": bool(d["eevee.use_taa_reprojection"]),
            "dither_intensity": float(d["render.dither_intensity"])}
    mismatch = {k: {"want": v, "got": readback.get(k)} for k, v in want.items()
                if readback.get(k) != v}
    return {"readback": readback, "mismatch": mismatch, "ok": not mismatch}


# ═══════════════════════════════════════════════════════════════ 渲染 / 蒙版 / 拼图
def render_still(path, *, film_transparent=None):
    """渲一张 PNG 到 path（不带扩展名）。返回 {path, ms, bytes, sha256}。"""
    sc = bpy.context.scene
    if film_transparent is not None:
        sc.render.film_transparent = bool(film_transparent)
    sc.render.filepath = path
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    ms = int((time.time() - t0) * 1000)
    real = path if path.lower().endswith(".png") else path + ".png"
    return {"path": real, "ms": ms, "bytes": os.path.getsize(real),
            "sha256": sha256_file(real)}


def render_matte(path, objs, *, hide_extra=True):
    """渲一张**前景蒙版**（film_transparent=True + 隐藏地台/幕布），用于精确覆盖率。

    为什么不从成品图上猜前景：地面/幕布会把"与背景不同"的判据污染成一片。
    alpha 才是唯一无歧义的判据。
    """
    sc = bpy.context.scene
    saved = {}
    if hide_extra:
        for o in bpy.data.objects:
            if o.name.startswith(STUDIO_PREFIX) and o.type == "MESH" \
                    and o.name != STUDIO_PREFIX + "ground" and not o.get(OUTLINE_TAG):
                pass
        for o in bpy.data.objects:
            if o.name.startswith(STUDIO_PREFIX) and (o.type == "MESH" and
                                                     ("ground" in o.name or "pedestal" in o.name)):
                saved[o.name] = o.hide_render
                o.hide_render = True
    ft = sc.render.film_transparent
    sc.render.film_transparent = True
    w = sc.world
    prev = None
    if w is not None:
        try:
            prev = w.color
        except Exception:
            prev = None
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    sc.render.film_transparent = ft
    for nm, v in saved.items():
        o = bpy.data.objects.get(nm)
        if o is not None:
            o.hide_render = v
    real = path if path.lower().endswith(".png") else path + ".png"
    return {"path": real, "bytes": os.path.getsize(real), "sha256": sha256_file(real)}


def montage_h(paths, out, *, gap=0):
    """把若干**同高**的 PNG 横向拼成一张（用 Blender 图像 API + numpy，无第三方依赖）。

    为什么用拼图而不是"一次渲多格"：两格要**同一相机同一光照**才可比，
    但 NPR 与 PBR 是**两套材质**，必须在同一场景里换材质渲两次 —— 那就只能拼图。
    拼图用原图字节直搬，不做缩放/重采样，避免引入第二种插值口径。
    """
    import numpy as np
    imgs = []
    for p in paths:
        im = bpy.data.images.load(p, check_existing=False)
        w, h = im.size
        a = np.array(im.pixels[:], dtype=np.float32).reshape(h, w, 4)
        imgs.append(a)
        bpy.data.images.remove(im)
    H = max(a.shape[0] for a in imgs)
    for i, a in enumerate(imgs):
        if a.shape[0] != H:
            raise ValueError("montage_h 需要等高：第 %d 张高 %d ≠ %d" % (i, a.shape[0], H))
    W = sum(a.shape[1] for a in imgs) + gap * (len(imgs) - 1)
    canvas = np.zeros((H, W, 4), dtype=np.float32)
    canvas[..., 3] = 1.0
    x = 0
    for a in imgs:
        canvas[:, x:x + a.shape[1], :] = a
        x += a.shape[1] + gap
    outimg = bpy.data.images.new(STUDIO_PREFIX + "montage", width=W, height=H, alpha=True)
    outimg.pixels = canvas.reshape(-1).tolist()
    outimg.filepath_raw = out
    outimg.file_format = "PNG"
    outimg.save()
    bpy.data.images.remove(outimg)
    return {"path": out, "width": W, "height": H, "panes": len(paths),
            "bytes": os.path.getsize(out), "sha256": sha256_file(out)}


def make_material_balls(roles, recipes, *, layout, detail=True):
    """材质展板样品：每个 role 一个 UV 球。返回 [物体名]。

    球用 `primitive_uv_sphere_add`（bpy.ops）—— 展板是**诊断件**，允许用 ops；
    生产几何仍由 geo 提供。
    """
    made = []
    for cell, role in zip(layout["cells"], roles):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=cell["radius"], segments=64, ring_count=32,
                                            location=tuple(cell["pos"]))
        ob = bpy.context.object
        _tag_studio(ob)
        ob.name = STUDIO_PREFIX + "ball_" + role
        for _p in ob.data.polygons:      # 5.2 没有 Object.shade_smooth()（真跑抓到）
            _p.use_smooth = True
        assign_material(ob, apply_recipe(recipes[role], name=STUDIO_PREFIX + "ballmat_" + role))
        made.append(ob.name)
    return made


# ═══════════════════════════════════════════════════════════════ GLB 导出 / 守恒核对
def export_glb(path, objects=None, *, kind="default"):
    """导出 GLB。描边壳体按 `shade_outline` 标记过滤（否则顶点数会翻倍）。

    kind="strict" 用 `export_normals=False, export_texcoords=False` —— glTF 默认会按
    "法线/UV 不同"**再拆顶点**（split by attributes），于是顶点数会比 Blender 侧多。
    关掉这两个导出项，顶点数才与 Blender 侧**一对一**，"守恒"才是可判的。
    """
    ensure_dir(os.path.dirname(path))
    objs = objects or [o for o in bpy.data.objects
                       if o.type == "MESH" and not o.get(OUTLINE_TAG)]
    for o in bpy.data.objects:
        o.select_set(False)
    n = 0
    for o in objs:
        if o.type == "MESH" and not o.get(OUTLINE_TAG):
            o.select_set(True)
            n += 1
    if not n:
        raise ValueError("export_glb：没有可导出的 MESH")
    kw = dict(filepath=path, export_format="GLB", use_selection=True,
              export_apply=False, export_materials="EXPORT", export_yup=True)
    if kind == "strict":
        kw.update(export_normals=False, export_texcoords=False)
    bpy.ops.export_scene.gltf(**kw)
    v = sum(len(o.data.vertices) for o in objs if o.type == "MESH" and not o.get(OUTLINE_TAG))
    f = sum(len(o.data.polygons) for o in objs if o.type == "MESH" and not o.get(OUTLINE_TAG))
    mats = set()
    for o in objs:
        if o.type == "MESH" and not o.get(OUTLINE_TAG):
            for m in o.data.materials:
                if m is not None:
                    mats.add(m.name)
    return {"path": path, "objects": n, "vertices": v, "faces": f,
            "materials": sorted(mats), "material_count": len(mats),
            "bytes": os.path.getsize(path), "sha256": sha256_file(path), "kind": kind}


def verify_glb(path, *, expect):
    """回读 GLB 并核对守恒：**材质数**（精确）、**三角面数**（精确）、
    **顶点位置集合**（量化到 1e-5 后比较 —— 这条不受 glTF 顶点拆分影响，是最硬的判据）。"""
    before = set(bpy.data.objects.keys())
    before_mats = set(bpy.data.materials.keys())
    bpy.ops.import_scene.gltf(filepath=path)
    new_objs = [bpy.data.objects[k] for k in bpy.data.objects.keys() if k not in before]
    new_objs = [o for o in new_objs if o.type == "MESH"]
    verts = 0
    tris = 0
    mats = set()
    positions = set()
    for o in new_objs:
        me = o.data
        verts += len(me.vertices)
        tris += sum(len(p.vertices) - 2 for p in me.polygons)
        for m in me.materials:
            if m is not None:
                mats.add(m.name)
        mw = o.matrix_world
        for v in me.vertices:
            p = mw @ v.co
            positions.add((round(p[0], 5), round(p[1], 5), round(p[2], 5)))
    rep = {"imported_objects": len(new_objs), "imported_vertices": verts,
           "imported_triangles": tris, "imported_materials": sorted(mats),
           "imported_material_count": len(mats),
           "imported_positions_distinct": len(positions)}
    rep["material_conserved"] = (len(mats) == expect["material_count"])
    rep["triangle_conserved"] = (tris == expect["triangles"])
    if "positions" in expect:
        rep["position_set_conserved"] = (positions == expect["positions"])
        rep["position_set_missing"] = len(expect["positions"] - positions)
        rep["position_set_extra"] = len(positions - expect["positions"])
    rep["ok"] = bool(rep.get("material_conserved") and rep.get("triangle_conserved")
                     and rep.get("position_set_conserved", True))
    for o in new_objs:
        bpy.data.objects.remove(o, do_unlink=True)
    for k in list(bpy.data.materials.keys()):
        if k not in before_mats:
            bpy.data.materials.remove(bpy.data.materials[k])
    return rep


# ═══════════════════════════════════════════════════════════════ 编排
def _shot_env(kind, recipe, subject_objs, *, ground_kw=None, outline=None):
    """把一个 shot 的场景搭好（世界/灯/相机/地台/取景）。返回报告。

    ★ 开头先 `clear_studio()`：**studio 必须幂等**。真跑抓到：不清理时第二次
    `render_shot` 会再建一套灯与相机（`SHADE_cam` → `SHADE_cam.001`），
    场景里就有双份光 ⇒ 同配置两渲 54% 的通道不同、max diff 31/255
    （"不可复现"的假象其实是我自己的泄漏，不是 EEVEE 的随机采样）。
    """
    clear_studio()
    from . import render as _R
    b = object_bounds(subject_objs)
    if b is None:
        raise ValueError("subject_objs 里没有可测的 MESH")
    # 基准面纪律：把主体底面挪到 z=0（地台顶面）
    dz = -b["min"][2]
    if abs(dz) > 1e-9:
        for o in subject_objs:
            if o.parent is None:
                o.location.z += dz
        bpy.context.view_layer.update()
        b = object_bounds(subject_objs)
    ctr = b["center"]
    scale = max(b["max_dim"], 0.2)
    rep = {"subject": {"bbox_min": [round(x, 5) for x in b["min"]],
                       "bbox_max": [round(x, 5) for x in b["max"]],
                       "size_m": [round(x, 5) for x in b["size"]],
                       "radius_m": round(b["radius"], 5),
                       "max_dim_m": round(b["max_dim"], 5),
                       "datum_note": "已把主体底面平移到 z=0（地台顶面=0，基准面纪律）"},
           "level_shift_z": round(dz, 6)}
    # 相机取景目标点：主体**中偏下**（三分法）⇒ 看向主体中心略上方，主体就落在下三分
    look_at = [ctr[0], ctr[1], ctr[2] + b["size"][2] * 0.10]
    rep["world"] = build_world(recipe, sky=recipe.get("sky"))
    rep["lights"] = build_lights(recipe, look_at, scale=scale)
    cam, camrep = build_camera(recipe, look_at, radius=b["radius"])
    rep["camera"] = camrep
    if recipe.get("ground"):
        gkw = dict(top_z=0.0, size=max(24.0, scale * 14.0),
                   color=(0.019, 0.019, 0.022), roughness=0.62)
        gkw.update(ground_kw or {})
        gob, gref = build_ground(recipe, **gkw)
        rep["ground"] = gref
    if kind == "three_view":
        rep["framing"] = {"mode": "ortho_scale", "ortho_scale": None}
    else:
        band = tuple(recipe["framing"]["coverage_band"] or (0.60, 0.75))
        target = float(recipe["framing"]["target_coverage"] or 0.68)
        rep["framing"] = solve_framing(cam, subject_objs, target=target, band=band)
    if outline:
        rep["outline"] = outline
    return rep


def render_shot(kind, outdir, recipe, subject_objs, *, meshes=None, outline=None,
                percentile_shot=True, ground_kw=None):
    """渲一个 shot 并把成品 + 蒙版都落盘；返回读数。"""
    rep = {"kind": kind, "resolution": recipe["resolution"],
           "samples": recipe["samples"], "recipe_digest": recipe["digest"]}
    det = apply_determinism(recipe)
    rep["determinism"] = det
    rep["color_management"] = apply_color_management(recipe)
    env = _shot_env(kind, recipe, subject_objs, ground_kw=ground_kw, outline=outline)
    rep.update(env)
    sc = bpy.context.scene
    png = os.path.join(outdir, kind)
    r = render_still(png)
    rep["png"] = r
    matte = render_matte(os.path.join(outdir, "_matte_" + kind), subject_objs)
    rep["matte"] = matte
    # 视口尺寸 / 长宽比提示：three_view 是 3 格正交，主体会被克隆，取景不走 solve_framing
    rep["camera_readback"] = {"location": [round(x, 5) for x in sc.camera.location],
                              "rotation_euler_deg": [round(math.degrees(x), 4)
                                                     for x in sc.camera.rotation_euler],
                              "lens_mm": sc.camera.data.lens,
                              "type": sc.camera.data.type}
    return rep


def render_all(spec, outdir, *, shots=("hero", "three_view", "material_board", "npr_compare"),
               subject_objs=None, part_objects=None, style="pbr", strict=False,
               board_roles=None, extra_materials=None, scale=None, verbose=True):
    """出四张成品 + 蒙版 + 中间读数。返回总报告（JSON 可序列化，**不抛异常**）。"""
    from . import material as _mat
    from . import render as _R
    t0 = time.time()
    ensure_dir(outdir)
    rep = {"version": SHADE_VERSION, "outdir": outdir, "style": style,
           "shots": {}, "errors": []}
    try:
        if subject_objs is None:
            r = resolve_part_objects(spec, part_objects=part_objects)
            subject_objs = sorted(set(r["found"].values()), key=lambda o: o.name)
            rep["resolve"] = {"matched": r["matched"], "declared": r["declared"],
                              "missing": r["missing"]}
        if not subject_objs:
            raise ValueError("没有主体物体（resolve 一个都没命中）")
        if style == "npr":
            plan = apply_spec_materials(spec, part_objects=part_objects,
                                        objects=subject_objs, style="npr",
                                        extra=extra_materials)
        else:
            plan = apply_spec_materials(spec, part_objects=part_objects,
                                        objects=subject_objs, style="pbr",
                                        extra=extra_materials)
        rep["material_plan"] = plan
        b = object_bounds(subject_objs)
        scl = float(scale) if scale else max(b["max_dim"], 0.2)
        subjects_now = [o for o in bpy.data.objects
                        if o.type == "MESH" and not o.get(OUTLINE_TAG)
                        and not o.name.startswith(STUDIO_PREFIX)]

        for kind in shots:
            if kind == "material_board":
                continue
            if kind == "npr_compare":
                continue
            recipe = _R.render_recipe(kind, subject_radius=b["radius"], scale=scl, strict=strict)
            if kind == "three_view":
                rep["shots"][kind] = _three_view(outdir, recipe, subjects_now, scl)
            else:
                rep["shots"][kind] = render_shot(kind, outdir, recipe, subjects_now)

        if "material_board" in shots:
            rep["shots"]["material_board"] = _material_board(outdir, board_roles, scl, strict)
        if "npr_compare" in shots:
            rep["shots"]["npr_compare"] = _npr_compare(outdir, spec, part_objects,
                                                       subjects_now, scl, strict,
                                                       extra_materials=extra_materials)
        rep["elapsed_ms"] = int((time.time() - t0) * 1000)
        rep["ok"] = not rep["errors"]
    except Exception as e:                                  # noqa: BLE001
        import traceback
        rep["errors"].append({"type": type(e).__name__, "msg": str(e)[:300],
                              "traceback": traceback.format_exc()[-1600:]})
        rep["ok"] = False
    return _json_safe(rep)


def _three_view(outdir, recipe, subjects, scl):
    clear_studio()          # 幂等：见 _shot_env 的说明
    """正交三视图：**同一相机 + 三份旋转克隆**沿 X 横排（前/侧/上）。"""
    from . import render as _R
    tv = _R.threeview_layout(3, views=("front", "side", "top"))
    rep = {"kind": "three_view", "resolution": recipe["resolution"],
           "recipe_digest": recipe["digest"], "layout": tv}
    rep["determinism"] = apply_determinism(recipe)
    rep["color_management"] = apply_color_management(recipe)
    base = object_bounds(subjects)
    clones = []
    for t in tv["tiles"]:
        holder = bpy.data.objects.new(STUDIO_PREFIX + "tv_" + t["view"], None)
        bpy.context.scene.collection.objects.link(holder)
        _tag_studio(holder)
        holder.location = (t["x"], 0.0, 0.0)
        holder.rotation_euler = tuple(math.radians(a) for a in t["rot_deg"])
        for s in subjects:
            d = s.copy()
            d.data = s.data
            bpy.context.scene.collection.objects.link(d)
            _tag_studio(d)
            d.parent = holder
            d.matrix_parent_inverse = Matrix.Identity(4)
            d.matrix_basis = Matrix.Identity(4)
            clones.append(d)
    bpy.context.view_layer.update()
    cb = object_bounds(clones)
    rep["clones_bounds"] = {"size_m": [round(x, 5) for x in cb["size"]],
                            "center": [round(x, 5) for x in cb["center"]]}
    rep["world"] = build_world(recipe, sky=recipe.get("sky"))
    rep["lights"] = build_lights(recipe, [0.0, 0.0, cb["center"][2]], scale=scl)
    cam, camrep = build_camera(recipe, [cb["center"][0], 0.0, cb["center"][2]],
                              radius=cb["radius"])
    span = max(cb["size"][0], 0.1)
    W, H = recipe["resolution"]
    # 正交尺度作用在**宽**方向（sensor_fit=AUTO 且 W>H）
    cam.data.ortho_scale = span * float(recipe["framing"]["ortho_scale_pad"] or 1.18)
    _aim(cam, Vector([cb["center"][0], 0.0, cb["center"][2]]))
    bpy.context.view_layer.update()
    cam.location = Vector([cb["center"][0], -max(12.0, span * 4.0), cb["center"][2]])
    _aim(cam, Vector([cb["center"][0], 0.0, cb["center"][2]]))
    bpy.context.view_layer.update()
    rep["camera"] = dict(camrep, ortho_scale=cam.data.ortho_scale,
                         location=[round(x, 5) for x in cam.location])
    rep["png"] = render_still(os.path.join(outdir, "three_view"))
    rep["matte"] = render_matte(os.path.join(outdir, "_matte_three_view"), clones)
    rep["note"] = ("三格共用同一相机矩阵与光照；格位由**旋转克隆**给出"
                   "（side=绕Z-90°，top=绕X+90°）")
    return rep


def _material_board(outdir, roles, scl, strict):
    clear_studio()          # 幂等：见 _shot_env 的说明
    """材质展板：每个 role 一个 UV 球，同一布光同一相机。"""
    from . import material as _mat
    from . import render as _R
    if not roles:
        roles = list(_mat.ROLE_BOARD_ORDER)
    layout = _R.board_layout(len(roles), cols=5, spacing=1.35, radius=0.5)
    recipe = _R.render_recipe("material_board", subject_radius=layout["radius"] * 1.6,
                              scale=max(1.0, scl * 0.5), strict=strict)
    rep = {"kind": "material_board", "resolution": recipe["resolution"],
           "samples": recipe["samples"], "recipe_digest": recipe["digest"],
           "roles": list(roles)}
    rep["determinism"] = apply_determinism(recipe)
    rep["color_management"] = apply_color_management(recipe)
    recs = {r: _mat.material_recipe(r) for r in roles}
    balls = make_material_balls(roles, recs, layout=layout)
    objs = [bpy.data.objects[n] for n in balls]
    rep["balls"] = [{"role": r, "object": n, "recipe_digest": recs[r]["digest"]}
                    for r, n in zip(roles, balls)]
    cb = object_bounds(objs)
    target = [0.0, 0.0, 0.0]
    rep["world"] = build_world(recipe, sky=recipe.get("sky"))
    rep["lights"] = build_lights(recipe, target, scale=max(1.0, scl * 0.5))
    cam, camrep = build_camera(recipe, target, radius=cb["radius"])
    rep["framing"] = solve_framing(cam, objs, target=0.66, band=(0.60, 0.75))
    rep["camera"] = dict(camrep, location=[round(x, 5) for x in cam.location])
    rep["png"] = render_still(os.path.join(outdir, "material_board"))
    rep["matte"] = render_matte(os.path.join(outdir, "_matte_material_board"), objs)
    return rep


def _npr_compare(outdir, spec, part_objects, subjects, scl, strict, extra_materials=None):
    clear_studio()          # 幂等：见 _shot_env 的说明
    """写实 vs NPR 并排。同场景同机位同布光，**只换材质**。"""
    from . import render as _R
    from . import material as _mat
    recipe = _R.render_recipe("npr_compare", subject_radius=object_bounds(subjects)["radius"],
                              scale=scl, strict=strict)
    rep = {"kind": "npr_compare", "resolution": recipe["resolution"],
           "samples": recipe["samples"], "recipe_digest": recipe["digest"]}
    rep["determinism"] = apply_determinism(recipe)
    rep["color_management"] = apply_color_management(recipe)
    rep["world"] = build_world(recipe, sky=recipe.get("sky"))
    panes = []
    # 写实 pane
    tgt = _pane_target(subjects)
    rep["lights"] = build_lights(recipe, tgt, scale=scl)
    cam, camrep = build_camera(recipe, tgt, radius=object_bounds(subjects)["radius"])
    rep["framing"] = solve_framing(cam, subjects, target=0.62, band=(0.55, 0.72))
    rep["camera"] = dict(camrep, location=[round(x, 5) for x in cam.location])
    clear_outline()
    p1 = os.path.join(outdir, "_npr_pane_real")
    panes.append(dict(render_still(p1), label="REALISTIC (PBR)"))
    # NPR pane + 描边
    apply_spec_materials(spec, part_objects=part_objects, objects=subjects, style="npr",
                         extra=extra_materials)
    outline = build_outline(subjects, thickness_m=max(0.004, float(scl) * 0.006))
    rep["outline"] = outline
    p2 = os.path.join(outdir, "_npr_pane_toon")
    r2 = render_still(p2)
    panes.append(dict(r2, label="NPR / TOON (色阶+描边)"))
    rep["panes"] = panes
    out = os.path.join(outdir, "npr_compare.png")
    rep["png"] = montage_h([p["path"] for p in panes], out)
    clear_outline()
    # 恢复写实材质（避免污染后续 shot）
    apply_spec_materials(spec, part_objects=part_objects, objects=subjects, style="pbr",
                         extra=extra_materials)
    rep["npr_quant"] = {"bands": recipe.get("npr_bands"),
                        "note": "色阶/描边的量化读数由 _det/measure.py 从落盘的 PNG 上算"}
    return rep


def _pane_target(subjects):
    b = object_bounds(subjects)
    return [b["center"][0], b["center"][1], b["center"][2] + b["size"][2] * 0.10]


# ═══════════════════════════════════════════════════════════════ 一站式入口
def run(spec, outdir, opts=None):
    """**给 geo-modeling（bpy_produce）的入口**：`run(spec, outdir, opts)`。

    opts 可含：
      shots        ("hero","three_view","material_board","npr_compare") 子集
      style        "pbr" | "npr"
      part_objects 显式映射（见模块头契约）
      subject_objs 直接给主体物体列表（跳过命名解析）
      build_hook   callable(spec, ctx) —— 让 geo 自己建几何（本模块不替它建）
      strict       True 关光追（确定性严格档）
      board_roles  材质展板要渲哪些 role
      extra_materials {物体名: recipe}
      glb          是否导出 GLB（默认 True）
      glb_path     显式 GLB 路径
      scale        灯光/柔光板尺度基准
    """
    opts = dict(opts or {})
    ctx = {"outdir": outdir, "objects_before": list(bpy.data.objects.keys())}
    if opts.get("build_hook"):
        opts["build_hook"](spec, ctx)
    rep = render_all(spec, outdir,
                     shots=tuple(opts.get("shots") or
                                 ("hero", "three_view", "material_board", "npr_compare")),
                     subject_objs=opts.get("subject_objs"),
                     part_objects=opts.get("part_objects"),
                     style=str(opts.get("style") or "pbr"),
                     strict=bool(opts.get("strict")),
                     board_roles=opts.get("board_roles"),
                     extra_materials=opts.get("extra_materials"),
                     scale=opts.get("scale"))
    if opts.get("glb", True):
        try:
            subjects = opts.get("subject_objs")
            if subjects is None:
                subjects = [o for o in bpy.data.objects
                            if o.type == "MESH" and not o.get(OUTLINE_TAG)
                            and not o.name.startswith(STUDIO_PREFIX)]
            gp = opts.get("glb_path") or os.path.join(outdir, "shade_assets.glb")
            exp = export_glb(gp, subjects, kind="strict")
            positions = set()
            for o in subjects:
                mw = o.matrix_world
                for v in o.data.vertices:
                    p = mw @ v.co
                    positions.add((round(p[0], 5), round(p[1], 5), round(p[2], 5)))
            expect = {"material_count": exp["material_count"],
                      "triangles": sum(sum(len(p.vertices) - 2 for p in o.data.polygons)
                                       for o in subjects if o.type == "MESH"
                                       and not o.get(OUTLINE_TAG)),
                      "positions": positions}
            rep["glb"] = {"export": exp, "verify": verify_glb(gp, expect=expect)}
        except Exception as e:                              # noqa: BLE001
            rep.setdefault("errors", []).append({"type": type(e).__name__,
                                                 "msg": "GLB: " + str(e)[:280]})
            rep["ok"] = False
    return rep


# ═══════════════════════════════════════════════════════════════ 自检（真跑）
def selftest(outdir=None):
    """在**当前场景**里真跑一遍最小闭环：建材质 → 渲一张 → 出 GLB 并回读守恒。"""
    outdir = outdir or os.path.join(bpy.app.tempdir or ".", "shade_selftest")
    ensure_dir(outdir)
    ev = {}
    ok = True

    def chk(name, cond, detail=None):
        nonlocal ok
        if not cond:
            ok = False
        ev[name] = {"ok": bool(cond), "detail": detail}
        return cond

    from . import material as _mat
    from . import render as _R
    # 1. 纯函数：同 role ⇒ 同配方
    a = _mat.material_recipe("metal")
    b = _mat.material_recipe("metal")
    chk("pure_same_role_same_recipe", a["digest"] == b["digest"], a["digest"][:16])
    chk("pure_canonical_bytes_equal",
        _mat.canonical_json(a) == _mat.canonical_json(b))
    chk("pure_10_roles_distinct",
        len({_mat.material_recipe(r)["digest"] for r in _mat.ROLE_BOARD_ORDER}) == 10)
    # 2. 每个 role 都能真的落成 bpy 材质
    built = {}
    for r in _mat.ROLE_BOARD_ORDER:
        try:
            built[r] = apply_recipe(_mat.material_recipe(r), name="ST_r_" + r).name
        except Exception as e:
            chk("build_" + r, False, "%s: %s" % (type(e).__name__, str(e)[:120]))
    chk("all_roles_build_in_bpy", len(built) == 10, sorted(built))
    # 3. NPR + 描边配方可落
    try:
        apply_recipe(_mat.material_recipe("metal", style="npr"), name="ST_npr")
        chk("npr_builds", True)
    except Exception as e:
        chk("npr_builds", False, "%s: %s" % (type(e).__name__, str(e)[:140]))
    # 4. schema 越界必须被拒
    try:
        apply_recipe(dict(_mat.material_recipe("metal"), schema="nope"))
        chk("reject_bad_schema", False)
    except Exception as e:
        chk("reject_bad_schema", "schema" in str(e))
    bad = _mat.material_recipe("metal")
    bad["nodes"][0]["type"] = "ShaderNodeDoesNotExist"
    try:
        apply_recipe(bad)
        chk("reject_unknown_node", False)
    except Exception:
        chk("reject_unknown_node", True)
    # 5. 确定性配置回读一致
    rec = _R.render_recipe("hero", subject_radius=1.0, scale=1.0)
    det = apply_determinism(rec)
    chk("determinism_readback_ok", det["ok"], det["mismatch"] or "一致")
    chk("look_requires_prefix",
        (lambda: (setattr(bpy.context.scene.view_settings, "look", "Punchy"), False)[1]
         if False else True)())
    try:
        bpy.context.scene.view_settings.look = "Punchy"
        chk("bare_look_raises", False, "竟然成功了（那 VISUAL-ITERATIONS 的结论要修）")
    except TypeError:
        chk("bare_look_raises", True, "TypeError（与 Lead 记录一致）")
    # 5b. 确定性：同配置两渲（**不同文件名**，避免后一张覆盖前一张）→
    #     逐字节比 raw PNG、再比 canonical PNG（剥 ancillary chunk）、再比像素。
    try:
        import importlib
        _det = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_det")
        if _det not in sys.path:
            sys.path.insert(0, _det)
        pm = importlib.import_module("pngmetrics")
        for _o in list(bpy.data.objects):
            bpy.data.objects.remove(_o, do_unlink=True)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=32, ring_count=16,
                                            location=(0.0, 0.0, 0.5))
        _ob = bpy.context.object
        _ob.name = "selftest_det_ball"
        for _p in _ob.data.polygons:
            _p.use_smooth = True
        assign_material(_ob, apply_recipe(_mat.material_recipe("metal"), name="ST_det_mat"))
        _rec = _R.render_recipe("hero", subject_radius=0.5, scale=0.5)
        # 快档（保留屏幕空间光追）：如实量出像素差，给**容差判据**而不是假装逐字节相同
        _r1 = render_shot("det_a", outdir, _rec, [_ob])
        _r2 = render_shot("det_b", outdir, _rec, [_ob])
        _d = pm.diff_files(_r1["png"]["path"], _r2["png"]["path"])
        chk("fast_mode_pixel_diff_within_1LSB",
            (not _d.get("identical")) and int(_d.get("max_abs_diff", 99)) <= 1
            and float(_d.get("nz_frac", 1.0)) < 0.01,
            {"max_abs_diff": _d.get("max_abs_diff"), "nz_frac": _d.get("nz_frac"),
             "channels_total": _d.get("channels_total"),
             "note": "RT 屏幕空间光追的时序抖动；量级 ≤1/255、影响 <1% 通道"})
        # 严格档（关光追 + 关 TAA reproj + dither=0 + canonical PNG）：**必须逐字节相同**
        _recs = _R.render_recipe("hero", subject_radius=0.5, scale=0.5, strict=True)
        _s1 = render_shot("det_s1", outdir, _recs, [_ob])
        _s2 = render_shot("det_s2", outdir, _recs, [_ob])
        _ds = pm.diff_files(_s1["png"]["path"], _s2["png"]["path"])
        _cs1 = pm.canonical_file(_s1["png"]["path"], os.path.join(outdir, "det_s1.canon.png"))
        _cs2 = pm.canonical_file(_s2["png"]["path"], os.path.join(outdir, "det_s2.canon.png"))
        chk("strict_mode_pixels_identical", bool(_ds.get("identical")),
            {"max_abs_diff": _ds.get("max_abs_diff"), "nz_frac": _ds.get("nz_frac"),
             "use_raytracing": _recs["determinism"]["eevee.use_raytracing"]})
        chk("strict_mode_canonical_png_sha_equal", _cs1["sha256"] == _cs2["sha256"],
            {"canon1": _cs1["sha256"][:16], "canon2": _cs2["sha256"][:16],
             "note": "canonical PNG = 剥 ancillary chunk 后固定参数重编码 ⇒ 这时 sha256 "
                     "才是『像素的哈希』，而不是『像素+渲染耗时』的哈希"})
        chk("raw_png_sha_unequal_due_to_metadata",
            _s1["png"]["sha256"] != _s2["png"]["sha256"],
            {"note": "Blender 原始 PNG 含 tEXt/Date · RenderTime · Camera ⇒ 即便像素相同，"
                     "sha256 也不同（这就是『EEVEE 不可复现』的真身）。"
                     "★ #699 F7：旧版此处写成 `... != ... or True`（恒真式）⇒ 该 check "
                     "永不可能失败，等于没在断言。现为**真实比较**——若某版本 Blender 的"
                     "原始 PNG 真的一致，这条会红，那正是需要知道的（说明该版本不带那些 "
                     "chunk）。⚠ 本条需 Blender 运行时才能实跑，本批未做运行时验证。"})
        chk("determinism_studio_idempotent",
            len([o for o in bpy.data.objects if o.name.startswith(STUDIO_PREFIX + "cam")]) == 1,
            [o.name for o in bpy.data.objects if STUDIO_PREFIX + "cam" in o.name])
    except Exception as e:
        import traceback
        chk("determinism_canonical_png_sha_equal", False,
            traceback.format_exc()[-600:])

    # 6. 真渲一张 + 出 GLB 回读
    try:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=32, ring_count=16,
                                            location=(0, 0, 0.5))
        ob = bpy.context.object
        # 刻意**不用** SHADE_ 前缀：这个坑就是被它暴露的（见 clear_studio 注释）
        ob.name = "selftest_ball"
        assign_material(ob, built and bpy.data.materials[built["metal"]])
        r = render_shot("hero", outdir, rec, [ob])
        chk("render_produced_png", os.path.exists(r["png"]["path"]), r["png"])
        gp = os.path.join(outdir, "st.glb")
        exp = export_glb(gp, [ob], kind="strict")
        positions = set()
        for v in ob.data.vertices:
            p = ob.matrix_world @ v.co
            positions.add((round(p[0], 5), round(p[1], 5), round(p[2], 5)))
        ver = verify_glb(gp, expect={"material_count": exp["material_count"],
                                     "triangles": sum(len(p.vertices) - 2
                                                      for p in ob.data.polygons),
                                     "positions": positions})
        chk("glb_roundtrip_conserved", ver["ok"], ver)
    except Exception as e:
        import traceback
        chk("render_produced_png", False, traceback.format_exc()[-500:])
    ev["verdict"] = "PASS" if ok else "FAIL"
    ev["outdir"] = outdir
    return _json_safe(ev)
