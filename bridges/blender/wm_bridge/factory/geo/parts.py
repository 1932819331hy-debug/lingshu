# -*- coding: utf-8 -*-
"""wm_bridge.factory.geo.parts · **图元零件生成**（box / cylinder / cone / sphere / torus + 倒角）

本模块只干一件事：把 spec 的一条 `part` 变成**一个 bmesh + 一个场景对象**。
位姿（谁在哪、朝哪）不在这里 —— 那是 `assembly.py`；材质不在这里 —— 那是驱动器。

-------------------------------------------------------------------------- 与 runtime 的关系（Lead 纪律：先找一遍，别再造轮子）
已查 `_evidence/factory/CAPABILITY-MAP.md` 与 `runtime/geo.py`（37 个公开函数）。结论：

  ✅ **复用**（本模块直接转交，不重写）：
     · `geo.solid(name, bm, coll, owner, bevel_mm, seg, angle_deg, smooth_angle)`
       —— "bmesh → 对象"的**唯一落地点**（法线朝外 → 归属 → 倒角 → 平滑）。
     · `geo.bevel/shade_auto/recalc_normals/collection/own` 由 `geo.solid` 内部调用。
     · （驱动器侧另用 `geo.principled` 建材质 —— 见 `bpy_produce.py`。）

  ❌ **runtime 里没有、故本模块新增**（`geo.py` 无此件）：
     · **球体**（`runtime/` 全域 grep `uv_sphere|ico_sphere|def sphere` 只命中
       `planner.py` 里一处 `bpy.ops.mesh.primitive_uv_sphere_add`，那是 planner 的内部调用，
        不是可复用的图元入口）。既有资产的 `TURRET_hub`/`TURRET_lamp_*`/`LAMP_finial`/`LAMP_core`
        都是 `bmesh.ops.create_uvsphere`，故此处照抄同源调用。
     · **圆环**（torus）：runtime 无。
     · **"吃 (pos, rot_deg, axis, size) 的图元入口"**：`geo.box` 吃**区间** `(min,max)` 且不支持旋转；
       `geo.cyl` 吃**两点定轴**并用 `rotation_difference` 贴轴。

  ⚠ **为什么 `geo.cyl` 不能直接顶替圆柱**（这是一条会静默改几何的差异，必须写清）：
     既有资产（`hex-model/build_models.py::cyl`）的朝向是 **Euler 复合**：
         `R_leg = R_obj(rot_deg) · R_axis(90°绕X或Y)`
     `geo.cyl` 用的是 `Vector((0,0,1)).rotation_difference(d)` —— **最小旋转**。
     两者**只在"滚转（绕自身轴的转角）"上可能不同**：
       · 当 `R_leg` 的转轴 ⊥ d 时，滚转 = 0 ⇒ 两者**恒等**。实测 turret 的炮管正是这种
         （`R_leg = Rz(−a)·Rx(90°)`，轴 = `(cos a, −sin a, 0)` ⊥ `d = (−sin a, −cos a, 0)`）
         ⇒ 用 `geo.cyl` 也能逐字节复现；
       · 但当 `R_leg` 与 d **反向/共线**（如 `axis="y"` + `rot_deg=(90,0,0)` ⇒ d = −Z），
         最小旋转**退化**（轴不唯一），Blender 取哪一根是未声明的 ⇒ 会静默换一个滚转。
     ⇒ 本模块取 **Euler 复合**这条与既有资产同源的路，并**不假设**滚转为 0。
        这样"复现既有件"与"新造件"走的是同一条、可推演的路径。

-------------------------------------------------------------------------- `size` 的口径（**标称**外接尺度，不是多边形实际包围盒）
  · box        `(sx, sy, sz)`   —— 精确
  · cylinder/cone  `(2r, 2r, h)` 按 `axis` 摆好之后的三轴**标称**尺度（内接圆直径口径）
  · sphere     `(2r, 2r, 2r)`
  · torus      `(2(R+r), 2(R+r), 2r)`
  ⚠ 多边形柱的实际包围盒**略小于**标称值（例：8 段的 `2r` 实际是 `1.4339` 而不是 `1.44`）。
    这是"声明的是标称尺度、dump 报的是实际包围盒"，**不是**不一致；驱动器会把两者都记进产物。

-------------------------------------------------------------------------- `pos` 的口径
  件的**原点**相对 link 锚点（米）。配 `assembly.part_world_mat` 使用。
  ⇒ 若你把"造件时烘进网格的局部偏移 `off`"换成"把 `off` 折进 `pos`"，
     `pos` 就等于该件**包围盒中心**相对锚点的位置 —— 与 `geom.part_from_blender` 的
     `pos`（`world_trans + R·local_center − anchor`）**逐位相同**（已验证）。
"""
from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

__all__ = [
    "PartsError", "PRIMITIVE_KINDS", "DEFAULT_SEGMENTS", "build_bmesh",
    "land", "make_part", "axis_rotation",
]

#: 本模块承认的图元名（spec.part.kind 的闭集 —— `spec.py` 只校验"非空字符串"，
#: 闭集在这里执行；未知值 fail-closed）
PRIMITIVE_KINDS = ("box", "cylinder", "cone", "sphere", "torus")

#: 分段数缺省 —— 与既有资产 `hex-model/build_models.py` 的 `cyl(seg=20)` / `sph(u=18,v=10)` 同值
DEFAULT_SEGMENTS = {"cylinder": 20, "cone": 20, "sphere": 18}
DEFAULT_SEGMENTS_V = {"sphere": 10, "torus": 12}
DEFAULT_TORUS_MAJOR_SEG = 12


class PartsError(Exception):
    """图元无法生成（fail-closed）。"""


def axis_rotation(axis):
    """`axis` → 把 +Z 摆到该轴的**前旋转**矩阵（照抄既有资产）：

        "z" → 单位阵        "y" → 绕 X 转 90°        "x" → 绕 Y 转 90°
    """
    a = str(axis or "z").lower()
    if a == "z":
        return Matrix.Identity(3)
    if a == "y":
        return Matrix.Rotation(math.radians(90.0), 3, "X")
    if a == "x":
        return Matrix.Rotation(math.radians(90.0), 3, "Y")
    raise PartsError("axis 须为 x/y/z 之一，实为 %r" % (axis,))


def _int_ge(v, lo, what):
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise PartsError("%s 须为 >= %d 的整数，实为 %r" % (what, lo, v))
    return int(v)


def build_bmesh(kind, size, axis="z", segments=None, segments_v=None):
    """按 spec 的一条 part 生成**以原点为中心**的 bmesh。

    `size` = 标称三轴尺度（见模块 docstring）。`axis` 只对 cylinder/cone 有意义。
    返回的 bmesh 不含任何 `pos`/`rot_deg` —— 那是 `assembly.part_world_mat` 的事。
    """
    k = str(kind or "box").lower()
    if k not in PRIMITIVE_KINDS:
        raise PartsError("未知图元 %r（允许 %s）" % (kind, list(PRIMITIVE_KINDS)))
    sx, sy, sz = (float(size[0]), float(size[1]), float(size[2]))
    if min(sx, sy, sz) <= 0:
        raise PartsError("size 三分量须 > 0，实为 %r" % (size,))
    ax = str(axis or "z").lower()
    R = axis_rotation(ax)

    bm = bmesh.new()
    if k == "box":
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)

    elif k in ("cylinder", "cone"):
        seg = _int_ge(segments, 3, "segments") if segments is not None \
            else DEFAULT_SEGMENTS[k]
        # 轴向上取深度、另两轴上取直径（标称）
        if ax == "z":
            r, h = sx / 2.0, sz
        elif ax == "y":
            r, h = sx / 2.0, sy
        else:
            r, h = sy / 2.0, sx
        r2 = 0.0 if k == "cone" else r
        kw = dict(cap_ends=True, cap_tris=False, segments=int(seg), depth=float(h))
        try:
            bmesh.ops.create_cone(bm, radius1=float(r), radius2=float(r2), **kw)
        except TypeError:                          # 极老版本的 diameter 口径
            bmesh.ops.create_cone(bm, diameter1=float(r) * 2.0,
                                  diameter2=float(r2) * 2.0, **kw)
        if ax != "z":
            bmesh.ops.rotate(bm, verts=bm.verts, cent=(0.0, 0.0, 0.0), matrix=R)

    elif k == "sphere":
        u = _int_ge(segments, 3, "segments") if segments is not None \
            else DEFAULT_SEGMENTS["sphere"]
        v = _int_ge(segments_v, 2, "segments_v") if segments_v is not None \
            else DEFAULT_SEGMENTS_V["sphere"]
        r = sx / 2.0
        try:
            bmesh.ops.create_uvsphere(bm, u_segments=int(u), v_segments=int(v),
                                      radius=float(r))
        except TypeError:
            bmesh.ops.create_uvsphere(bm, u_segments=int(u), v_segments=int(v),
                                      diameter=float(r) * 2.0)

    else:  # torus
        ms = _int_ge(segments, 3, "segments") if segments is not None \
            else DEFAULT_TORUS_MAJOR_SEG
        ns = _int_ge(segments_v, 3, "segments_v") if segments_v is not None \
            else DEFAULT_SEGMENTS_V["torus"]
        minor_r = sz / 2.0
        major_r = sx / 2.0 - minor_r
        if major_r <= 0:
            raise PartsError("torus：size[0]/2 (=%.6f) 必须大于 size[2]/2 (=%.6f)"
                             % (sx / 2.0, minor_r))
        try:
            bmesh.ops.create_cone(bm, cap_ends=False, cap_tris=False, segments=int(ns),
                                  radius1=float(minor_r), radius2=float(minor_r),
                                  depth=0.0)
        except TypeError:
            bmesh.ops.create_cone(bm, cap_ends=False, cap_tris=False, segments=int(ns),
                                  diameter1=float(minor_r) * 2.0,
                                  diameter2=float(minor_r) * 2.0, depth=0.0)
        # 把构成环的截面圆推到主半径上并按主段数阵列 —— 手写环（runtime 无 torus）
        base = bmesh.new()
        base.from_mesh(_bm_to_mesh(bm))
        bm.free()
        bm = base
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0.0, 0.0, 0.0),
                         matrix=Matrix.Rotation(math.radians(90.0), 3, "Y"))
        bmesh.ops.translate(bm, verts=bm.verts, vec=Vector((major_r, 0.0, 0.0)))
        # 绕 Z 复制 ms 份并桥接 —— 用 spin 一次成型
        edges = [e for e in bm.edges]
        bmesh.ops.spin(bm, geom=bm.verts[:] + edges, cent=(0.0, 0.0, 0.0),
                       axis=(0.0, 0.0, 1.0), dvec=(0.0, 0.0, 0.0),
                       angle=2.0 * math.pi, steps=int(ms), use_duplicate=False)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-9)
    return bm


def _bm_to_mesh(bm):
    me = bpy.data.meshes.new("__parts_tmp")
    bm.to_mesh(me)
    return me


def land(bm, name, collection, owner=None, bevel=0.0, seg=2, angle_deg=35.0, rt=None):
    """bmesh → 场景对象。**首选转交 `runtime/geo.solid`**（法线 → 归属 → 倒角）。

    `rt` = runtime 公开名映射（驱动器从内核注入的 globals 里取）；
    取不到时用一条**与之等价**的本地落地（本函数在无内核上下文里也能自测）。
    """
    if rt and callable(rt.get("solid")):
        return rt["solid"](name, bm, coll=collection, owner=owner,
                           bevel_mm=float(bevel) * 1000.0, seg=int(seg),
                           angle_deg=float(angle_deg), smooth_angle=None)
    # —— 本地等价落地（不引入内核依赖；行为与 geo.solid(…, smooth_angle=None) 对齐）
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(str(name))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(str(name), me)
    bpy.context.scene.collection.objects.link(ob)
    col = bpy.data.collections.get(collection)
    if col is None:
        col = bpy.data.collections.new(collection)
        bpy.context.scene.collection.children.link(col)
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    col.objects.link(ob)
    if owner is not None:
        ob["dsh.owner"] = owner
    if bevel and float(bevel) > 0:
        md = ob.modifiers.new("bevel", "BEVEL")
        md.width = float(bevel)
        md.segments = int(seg)
        try:
            md.limit_method = "ANGLE"
            md.angle_limit = math.radians(float(angle_deg))
        except Exception as exc:                               # noqa: BLE001
            raise PartsError("BEVEL 修改器的角度限定写不进（%s: %s）——"
                             "5.x 改了属性名？不许静默降级" % (type(exc).__name__, exc))
        if abs(float(md.width) - float(bevel)) > 1e-12:
            raise PartsError("bevel.width 回读不符：写了 %r 得 %r"
                             % (bevel, md.width))
    return ob


def make_part(part, collection, owner=None, rt=None, name=None):
    """spec 的一条 part → 场景对象（**不含位姿** —— 位姿由驱动器按 `assembly` 设）。

    返回 (ob, info)；info 记录了实际用的分段数/倒角，供驱动器写进回执（可审计）。
    """
    bm = build_bmesh(part.get("kind") or "box", part["size"],
                     axis=part.get("axis") or "z",
                     segments=part.get("segments"),
                     segments_v=part.get("segments_v"))
    bv = float(part.get("bevel") or 0.0)
    ob = land(bm, name or part["name"], collection, owner=owner,
              bevel=bv, seg=2, angle_deg=35.0, rt=rt)
    return ob, {"kind": (part.get("kind") or "box").lower(),
                "segments": part.get("segments"),
                "segments_v": part.get("segments_v"),
                "bevel": bv,
                "n_modifiers": len(ob.modifiers)}
