# -*- coding: utf-8 -*-
"""wm_bridge.factory.bpy_produce · **spec → Blender 场景 → 与 bpy_dump 同构的 dump**

★★ 这个文件是全仓**唯一的 Blender 侧生产驱动**，也是整条线能不能闭环的支点。

分工（与 `wm_bridge/bpy_dump.py` 刻意对称）：
    spec.py / assembly.py / parts.py   **纯标准库或纯机制**：规格、位姿、图元
    bpy_produce.py                     **只做"落地 + 抽取"**：把 spec 变成真的 Blender 场景，
                                        再交给 `bpy_dump.collect()` 抽原始量
    geom.py / oracle.py                **全部换算与契约**：轴换算、拟合、schema

★ 为什么产物必须是"**与 bpy_dump 同构的 dump**"（= schema `wm_bridge.dump/1`）：
  这样 `geom.write_from_dump()` / `oracle.doc_from_dump()` **一个字节都不用改**就能消费它，
  "spec 驱动生成 → 既有导出面"自动闭环。**抽取本身直接调用 `bpy_dump.collect()`** ——
  不另写一份，就不可能有"两份实现慢慢长歪"（本 fork 已经因为两份 OBJ 格式器差过 28 行）。

-------------------------------------------------------------------------- 与 runtime 的关系
`runtime/geo.py` 的**公开名会被内核直接注入到"被执行的脚本"的 globals**
（实测：`box/cyl/prism/loft/solid/bevel/principled/shade_auto/boolean/collection/own`）。
本文件因此**接受一个 `rt` 映射**（驱动器由 `_det/run_produce.py` 从自己的 globals 里取），
再转交给 `parts.land()` → `geo.solid()`。
⚠ 不 import `runtime/geo.py`：它顶层就 `raise RuntimeError("需要共享内核 K.dsh_kit")`，
  且 `K.dsh_kit.kapi("geo")["dispatch"]` 走 JSON 契约，**传不了 bmesh** ⇒ 只能走注入的 globals。

-------------------------------------------------------------------------- 确定性（"基准可信"的前提）
跨进程逐字节相同的三个条件，本文件逐条钉死：
  1. **对象遍历序**：bpy_dump 的 `nodes`/`tracks` 按 `collection.all_objects` 的**插入序**
     （实测：`all_objects` 是插入序，不是名字序）。⇒ 本文件**按 spec 的 link 声明序**建
     EMPTY，并且 `meshes` 由 bpy_dump 自己按名字排序，故两条都确定。
  2. **命名**：对象名一律 = spec 里的名字，且建场景前 `purge()` 清空 ⇒ 不会有
     `Cube.001` 这种随进程变化的尾巴。
  3. **帧值与插值**：`--bake linear` 在**每一帧**打关键帧并设 `LINEAR`（解析式的精确采样）；
     `--bake bezier` 复刻既有资产的 `BEZIER/AUTO_CLAMPED` 采样（`--bezier-step`，默认 6，
     实测出处：`wmtest_models.blend` 的 `TURRET_yawAction` = 25 个关键帧、步长 6、BEZIER）。

用法（在 Blender 内跑；`_det/run_produce.py` 是带内核注入的引导壳）：
    python -m wm_bridge.factory.bpy_produce --spec specs/turret.spec.json --out dump.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback

from .geo import assembly as A
from .geo import parts as P
from .geo import spec as S

#: 已有资产的 BEZIER 采样步长（实测出处见模块 docstring）。这是**唯一** spec 外的烘焙旋钮，
#: 已报 Lead；它不改变 `easing` 的语义（linear/bezier），只决定 bezier 那一路怎么离散化。
BEZIER_BAKE_STEP = 6

#: 倒角角度限定（度）—— 既有资产 `build_models.py::mk` 用的就是 35°
BEVEL_ANGLE_DEG = 35.0

#: 从 runtime 借用的公开名（由 `_det/run_produce.py` 从注入的 globals 里挑出来传进来）
RT_NAMES = ("solid", "bevel", "principled", "shade_auto", "recalc_normals",
            "collection", "own", "boolean")


class ProduceError(Exception):
    """规格无法落地（fail-closed：不猜、不降级、不产半成品）。"""


# ─────────────────────────────────────────────────────────── 场景清空
def purge_scene():
    """清空场景 + 集合 + 孤儿数据。

    为什么必须清：对象名冲突会让 Blender 追加 `.001`，而**后缀取决于当前进程里已有什么** ——
    那正是"两次独立进程产物不同"最隐蔽的来源。
    """
    import bpy
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)
    for m in list(bpy.data.materials):
        if m.users == 0:
            bpy.data.materials.remove(m)


# ─────────────────────────────────────────────────────────── 材质
def _principled(mat):
    """取 Principled 节点 —— 优先走 runtime/geo.principled（按 bl_idname 找）。"""
    try:
        return __import__("geo", fromlist=["principled"]).principled(mat)
    except Exception:                                          # noqa: BLE001
        pass
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return None
    return next((n for n in nt.nodes
                 if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)


def material_key(part):
    """材质数据块的身份：显式 `material_name` 优先，否则按 (role, color) 归并。

    为什么按 role+color 归并而不是只按 role：同一 role（如 `metal`）下可以有多种颜色，
    只按 role 会把它们合成**一个**材质 —— 颜色被静默丢掉。
    """
    m = part.get("material") or {}
    nm = part.get("material_name")
    if nm:
        return ("name", nm)
    col = m.get("color")
    return ("role", m.get("role") or "painted_metal",
            tuple(col) if col else None)


def build_materials(norm_spec):
    """spec → {材质身份: (材质名, 材质对象)}；同名不同参数 ⇒ fail-closed。

    返回的材质名 = `part.material_name`（有则用），否则 `M_<role>[_RRGGBB]`。
    """
    import bpy
    want = {}
    for _l, p in S.iter_parts(norm_spec):
        m = p.get("material") or {}
        key = material_key(p)
        spec_mat = {
            "role": m.get("role") or "painted_metal",
            "color": list(m["color"]) if m.get("color") else None,
            "roughness": m.get("roughness"),
            "metallic": m.get("metallic"),
            "emission": list(m["emission"]) if m.get("emission") else None,
            "emission_strength": m.get("emission_strength"),
            "explicit_name": p.get("material_name"),
        }
        if key in want and want[key]["spec"] != spec_mat:
            raise ProduceError(
                "材质身份 %r 被两套不同参数声明（%r vs %r）—— 同一数据块只能有一套参数，"
                "spec 冲突必须当场拒收" % (key, want[key]["spec"], spec_mat))
        want[key] = {"spec": spec_mat}

    out = {}
    for key, rec in want.items():
        sm = rec["spec"]
        if sm["explicit_name"]:
            name = sm["explicit_name"]
        else:
            name = "M_" + sm["role"]
            if sm["color"]:
                name += "_%02X%02X%02X" % tuple(
                    max(0, min(255, int(round(c * 255.0)))) for c in sm["color"])
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = bpy.data.materials.new(name)
        try:
            mat.use_nodes = True
        except Exception as exc:                               # noqa: BLE001
            raise ProduceError("材质 %s 开不了节点树：%s" % (name, exc))
        b = _principled(mat)
        if b is None:
            raise ProduceError("材质 %s 找不到 Principled BSDF 节点（角色→节点的映射"
                               "本该在 shade 侧，这里只要一个能落色的最简实现）" % name)
        col = sm["color"] or (0.6, 0.6, 0.62)
        b.inputs["Base Color"].default_value = (col[0], col[1], col[2], 1.0)
        for k, v in (("Metallic", sm["metallic"]), ("Roughness", sm["roughness"])):
            if v is not None and k in b.inputs:
                b.inputs[k].default_value = float(v)
        if sm["emission"] is not None:
            e = sm["emission"]
            slot = None
            for k in ("Emission Color", "Emission"):
                if k in b.inputs:
                    slot = k
                    break
            if slot is None:
                raise ProduceError("材质 %s 没有发光色输入（5.x 属性名变了？）" % name)
            b.inputs[slot].default_value = (e[0], e[1], e[2], 1.0)
            if "Emission Strength" in b.inputs:
                b.inputs["Emission Strength"].default_value = \
                    float(sm["emission_strength"] or 0.0)
            # 回读校验：不许静默失败（§6 纪律）。
            # ⚠ 容差 1e-6 不是随手写的：Blender 的**颜色/数值 socket 是 float32**，
            #   实测 0.06 写进去读回是 0.05999999865889549（量化误差 1.3e-9）。
            #   把容差压到 1e-9 会在"写对了"的地方假失败 —— 这条判据要抓的是
            #   "压根没写进去 / 写到别的 socket 上"（那种偏差是量级级，不是 1e-9 级）。
            got = [float(v) for v in b.inputs[slot].default_value[:3]]
            if max(abs(got[i] - e[i]) for i in range(3)) > 1e-6:
                raise ProduceError("材质 %s 的发光色写不进：写了 %r 得 %r" % (name, e, got))
        out[key] = (name, mat)
    return out


# ─────────────────────────────────────────────────────────── 建场景
def build_scene(norm_spec, collection=None, rt=None):
    """按 spec 建整个场景；返回 (collection 名, info)。

    `info` 记下每个 link / 每个件实际落地用了什么（分段数、倒角、材质名），供回执审计。
    """
    import bpy
    sc = bpy.context.scene
    scene_spec = norm_spec["scene"]
    sc.render.fps = int(round(float(scene_spec["fps"])))
    sc.frame_start = int(scene_spec["frame_start"])
    sc.frame_end = int(scene_spec["frame_end"])

    coll_name = collection or norm_spec["name"].upper()
    coll = bpy.data.collections.new(coll_name)
    sc.collection.children.link(coll)

    mats = build_materials(norm_spec)

    # ---- link（关节空物体）：**按 spec 声明序**建，保证 all_objects 的插入序确定
    by_name = {l["name"]: l for l in norm_spec["links"]}
    empties = {}
    for l in norm_spec["links"]:
        ob = bpy.data.objects.new(l["name"], None)
        ob.empty_display_type = "PLAIN_AXES"
        ob.empty_display_size = 0.2
        coll.objects.link(ob)
        ob.rotation_mode = "XYZ"
        ob.location = A.local_offset(l, by_name)
        if l.get("parent"):
            ob.parent = empties[l["parent"]]
        empties[l["name"]] = ob

    # ---- parts
    info = {"links": [], "parts": []}
    for l in norm_spec["links"]:
        info["links"].append({"name": l["name"], "anchor": list(l["anchor"]),
                              "parent": l.get("parent"),
                              "n_parts": len(l["parts"])})
        for p in l["parts"]:
            ob, pinfo = P.make_part(p, coll_name, owner="geo-modeling", rt=rt,
                                    name=p["name"])
            ob.parent = empties[l["name"]]
            ob.rotation_mode = "XYZ"
            ob.rotation_euler = tuple(math.radians(float(v))
                                      for v in (p.get("rot_deg") or (0, 0, 0)))
            ob.location = tuple(float(v) for v in (p.get("pos") or (0, 0, 0)))
            nm, mat = mats[material_key(p)]
            ob.data.materials.append(mat)
            pinfo.update({"name": p["name"], "link": l["name"],
                          "material": nm, "pos": list(ob.location),
                          "rot_deg": [math.degrees(v) for v in ob.rotation_euler],
                          "size_declared": list(p["size"])})
            info["parts"].append(pinfo)
    return coll_name, info


# ─────────────────────────────────────────────────────────── 关节烘焙
def bake_joints(norm_spec, bake="linear", bezier_step=BEZIER_BAKE_STEP):
    """把关节声明烘成关键帧。

    · `bake="linear"`：**每一帧**一个关键帧、插值 `LINEAR` ⇒ 帧值 = `assembly` 的解析式
      （即 spec 的"匀速/正弦"语义**精确**落地）。
    · `bake="bezier"`：每 `bezier_step` 帧一个关键帧、`BEZIER`/`AUTO_CLAMPED`
      ⇒ 复刻既有资产（实测出处见模块 docstring）。

    返回烘焙回执（每 link 的关键帧数、插值类型读回值）。
    """
    import bpy
    hz = float(norm_spec["scene"]["fps"])
    n_frames = A.frames_of(norm_spec)
    if bake == "linear":
        try:
            bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
            got = bpy.context.preferences.edit.keyframe_new_interpolation_type
        except Exception as exc:                               # noqa: BLE001
            raise ProduceError("写不进关键帧插值偏好（%s）——不许静默降级" % exc)
        if got != "LINEAR":
            raise ProduceError("插值偏好回读不符：写了 LINEAR 得 %r" % (got,))
        ticks = list(range(0, n_frames))
    elif bake == "bezier":
        step = int(bezier_step)
        if step < 1:
            raise ProduceError("--bezier-step 须 >= 1，实为 %r" % (bezier_step,))
        try:
            bpy.context.preferences.edit.keyframe_new_interpolation_type = "BEZIER"
            got = bpy.context.preferences.edit.keyframe_new_interpolation_type
        except Exception as exc:                               # noqa: BLE001
            raise ProduceError("写不进关键帧插值偏好（%s）" % exc)
        if got != "BEZIER":
            raise ProduceError("插值偏好回读不符：写了 BEZIER 得 %r" % (got,))
        ticks = list(range(0, n_frames + 1, step))   # +1：与既有资产一样多打一帧（144）
    else:
        raise ProduceError("--bake 须为 linear/bezier，实为 %r" % (bake,))

    report = {"bake": bake, "interp_readback": got, "n_keyframe_ticks": len(ticks),
              "joints": []}
    sc = bpy.context.scene
    by = {l["name"]: l for l in norm_spec["links"]}
    for j in (norm_spec.get("joints") or []):
        name = j["link"]
        ob = bpy.data.objects[name]
        kind = j.get("kind") or "static"
        for t in ticks:
            v = A.joint_value(j, t, hz)
            if v is None:
                continue
            mode, val = v
            if mode == "rot":
                e = [0.0, 0.0, 0.0]
                e[("x", "y", "z").index(j.get("axis") or "z")] = math.radians(val)
                ob.rotation_mode = "XYZ"
                ob.rotation_euler = e
                ob.keyframe_insert("rotation_euler", frame=t)
            else:
                ax = ("x", "y", "z").index(j.get("axis") or "z")
                loc = list(A.local_offset(by[name], by))
                loc[ax] += float(val)
                ob.location = loc
                ob.keyframe_insert("location", frame=t)
        report["joints"].append({"link": name, "kind": kind,
                                 "easing": j.get("easing") or "linear",
                                 "axis": j.get("axis") or "z"})
    sc.frame_set(0)
    bpy.context.view_layer.update()
    return report


# ─────────────────────────────────────────────────────────── 抽取
def canonicalize_tris(dump):
    """把 `meshes[].tris` 换成**规范面序**（`geom.canonical_tris`）。

    ★ 为什么必须做（这是"跨进程逐字节可复现"的唯一拦路虎，实测定位）：
      `bpy_dump.collect()` 直接落 `me.calc_loop_triangles()` 的**原始迭代序**，
      而 Blender 对 **ngon/四边面** 的三角化**按内存地址序迭代**（受 ASLR 影响）
      ⇒ **顶点流逐位相同（实测 max_abs = 0.0）**，但面序每跑一次进程就变一次：
        实测：同一个 spec 三次独立进程 dump 出三个 sha256，差异**全部**落在
        `TURRET_hub` / `TURRET_lamp_0..2` 这 4 个 UV 球（164 顶点）的 `tris` 顺序上，
        其余 15 个网格、864 条 track、全部 link 的数值 **max_abs = 0.0**。
      （这与 DESIGN §6.2 #3 记录的是**同一个**根因，那里是在 OBJ 写侧修的。）

    ★ 为什么不改 bpy_dump.py：它是既有导出面，**只读**。这里在抽取后补一次规范化。

    ★ 与下游的关系：`geom.write_obj` 内部**还会**再 canonize 一次 —— 本函数是**幂等**的
      （规范形再规范一次还是它自己），所以对既有导出面的行为**零影响**；
      而 dump 本身从此与进程无关。
    """
    from .. import geom as G
    for m in dump.get("meshes") or []:
        tris = m.get("tris") or []
        if tris:
            m["tris"] = [list(t) for t in G.canonical_tris([tuple(t) for t in tris])]
    return dump


def collect_dump(norm_spec, coll_name, frame, mesh_source="evaluated",
                 allow_raw_with_modifiers=False):
    """调 `bpy_dump.collect()` 抽原始量 —— **不另写一份抽取**，只补一步面序规范化。"""
    from .. import bpy_dump as BD
    import bpy
    sc = norm_spec["scene"]
    n_frames = A.frames_of(norm_spec)
    coll = bpy.data.collections[coll_name]
    scene_extra = {"cycle_frames": n_frames, "motion": A.motion_label(norm_spec)}
    if sc.get("seed") is not None:
        scene_extra["seed"] = int(sc["seed"])
    dump = BD.collect([coll], frame=int(frame), frames=int(n_frames),
                      name=norm_spec["name"], tick_hz=float(sc["fps"]),
                      world_size=float(sc["world_size"]),
                      scene_extra=scene_extra, with_tracks=True,
                      translate=(0.0, 0.0, 0.0), mesh_source=mesh_source,
                      allow_raw_with_modifiers=allow_raw_with_modifiers)
    return canonicalize_tris(dump)


def write_dump(dump, path):
    """按 `bpy_dump` 的字节形态写盘（`sort_keys=True` / LF / 无缩进）。"""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dump, f, ensure_ascii=False, sort_keys=True)
    return path


# ─────────────────────────────────────────────────────────── 主入口
def produce(spec_path, out_path, collection=None, frame=None,
            mesh_source="evaluated", bake="linear", bezier_step=BEZIER_BAKE_STEP,
            allow_raw_with_modifiers=False, rt=None, details=None):
    """spec 文件 → dump 文件。返回回执 dict（含 dump 摘要）。"""
    with open(spec_path, encoding="utf-8") as f:
        raw = json.load(f)
    norm = S.normalize_spec(raw)                 # 非法即 raise（fail-closed）
    fs = int(norm["scene"]["frame_start"])
    if fs != 0:
        raise ProduceError(
            "scene.frame_start=%d ≠ 0 —— bpy_dump 的 tick 域是硬 0-based（collect 里 "
            "【for t in range(frames)】），非 0 起点会让「规格声明的帧区间」与"
            "「产物里的 tick」静默错位。要非 0 起点请改 bpy_dump 的契约，别在这里挪。"
            % fs)
    if frame is None:
        frame = fs

    purge_scene()
    coll_name, info = build_scene(norm, collection=collection, rt=rt)
    bake_rep = bake_joints(norm, bake=bake, bezier_step=bezier_step)
    dump = collect_dump(norm, coll_name, frame, mesh_source=mesh_source,
                        allow_raw_with_modifiers=allow_raw_with_modifiers)
    write_dump(dump, out_path)

    if details is not None:
        details.update({"scene": info, "bake": bake_rep})
    return {
        "schema": dump["schema"], "name": dump["name"], "frame": dump["frame"],
        "frames": dump["frames"], "out": out_path,
        "collection": coll_name,
        "meshes": len(dump["meshes"]), "links": len(dump["links"]),
        "tracks": len(dump["tracks"]), "nodes": dump["nodes"],
        "mesh_source": dump.get("mesh_source"),
        "with_live_modifiers": (dump.get("modifier_report") or {}).get(
            "with_live_modifiers"),
        "bake": bake, "bezier_step": int(bezier_step),
        "spec_digest": S.spec_digest(norm),
        "bytes": os.path.getsize(out_path),
    }


def main(argv=None, rt=None):
    ap = argparse.ArgumentParser(
        prog="wm_bridge.factory.bpy_produce",
        description="spec（wmfactory.spec/1）→ Blender 场景 → 与 bpy_dump 同构的 dump")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--collection", default=None,
                    help="集合名（缺省 = spec.name 大写）")
    ap.add_argument("--frame", type=int, default=None,
                    help="几何/部件位姿取自哪一帧（缺省 = scene.frame_start）")
    ap.add_argument("--mesh-source", dest="mesh_source", default="evaluated",
                    choices=["raw", "evaluated"])
    ap.add_argument("--allow-raw-with-modifiers", dest="allow_raw",
                    action="store_true")
    ap.add_argument("--bake", default="linear", choices=["linear", "bezier"],
                    help="linear（每帧 LINEAR，= spec 的匀速语义）/ "
                         "bezier（复刻既有资产的 BEZIER 采样）")
    ap.add_argument("--bezier-step", dest="bezier_step", type=int,
                    default=BEZIER_BAKE_STEP)
    ap.add_argument("--receipt", default=None, help="回执 JSON 落盘路径")
    args = ap.parse_args([a for a in (argv if argv is not None else sys.argv[1:])
                          if not a.startswith("--dsh")])
    details = {}
    try:
        r = produce(args.spec, args.out, collection=args.collection, frame=args.frame,
                    mesh_source=args.mesh_source, bake=args.bake,
                    bezier_step=args.bezier_step,
                    allow_raw_with_modifiers=args.allow_raw, rt=rt, details=details)
    except (S.SpecError, A.AssemblyError, P.PartsError, ProduceError) as exc:
        print("BPYPRODUCE " + json.dumps(
            {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
            ensure_ascii=False))
        return 2
    except Exception as exc:                                   # noqa: BLE001
        print("BPYPRODUCE " + json.dumps(
            {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
             "tb": traceback.format_exc()[-800:]}, ensure_ascii=False))
        return 2
    r["ok"] = True
    if args.receipt:
        with open(args.receipt, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"receipt": r, "details": details}, f,
                      ensure_ascii=False, sort_keys=True, indent=1)
    print("BPYPRODUCE " + json.dumps(r, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
