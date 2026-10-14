# -*- coding: utf-8 -*-
"""wm_bridge.factory.vec.prims · 程序化图元网格（纯标准库）
================================================================================
用途有两处，故单独成文件（而不是塞进 `geom2d`）：
  1. **无 Blender 也能测**：`geom2d` / `svg` 的投影与生成需要真几何，本模块提供
     确定性的图元（盒/柱/球/环/锥），使纯 python 侧可完整自测；
  2. **在 Blender 里建真场景**：`bpy_vec.to_blender_mesh()` 把同样的顶点/面
     写进 `bpy.data.meshes` —— 保证"矢量导出的几何"与"Blender 渲染的几何"**同源**
     （口径漂移是六轮返工的常见病根，同源是唯一的解法）。

约定：右手系、**Z-up**（与 `geo/spec.py` 一致）；面为逆时针（CCW）朝外；
所有图元以自身中心/底面为原点，由 `transform()` 摆位。
"""
from __future__ import annotations

import math

__all__ = ["Mesh", "box", "cylinder", "sphere", "torus", "cone", "quad_grid",
           "merge", "transform", "euler_matrix", "apply_matrix"]


class Mesh:
    """`verts` = [(x,y,z)]，`faces` = [(i,j,k[,l]), ...]（CCW 朝外）。"""

    __slots__ = ("verts", "faces", "name")

    def __init__(self, verts, faces, name=""):
        self.verts = [(float(v[0]), float(v[1]), float(v[2])) for v in verts]
        self.faces = [tuple(int(i) for i in f) for f in faces]
        self.name = name

    def bounds(self):
        if not self.verts:
            return None
        lo = [min(v[i] for v in self.verts) for i in range(3)]
        hi = [max(v[i] for v in self.verts) for i in range(3)]
        return (tuple(lo), tuple(hi))

    def center(self):
        b = self.bounds()
        if b is None:
            return (0.0, 0.0, 0.0)
        return tuple((b[0][i] + b[1][i]) / 2.0 for i in range(3))

    def __repr__(self):                 # pragma: no cover
        return "Mesh(%s, v=%d, f=%d)" % (self.name or "-", len(self.verts), len(self.faces))


# ─────────────────────────────────────────────────────────── 线性代数（3×3 行主序）
def euler_matrix(rx_deg, ry_deg, rz_deg, order="XYZ"):
    """欧拉角（度）→ 3×3 矩阵。**与 Blender 的 XYZ 欧拉口径一致**（R = Rz·Ry·Rx）。"""
    rx, ry, rz = (math.radians(rx_deg), math.radians(ry_deg), math.radians(rz_deg))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    my = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    mz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))
    r = _mm(mz, _mm(my, mx))
    if order != "XYZ":
        raise ValueError("只实现 XYZ 序（与 geo/spec.py 的 rot_deg 口径一致）")
    return r


def _mm(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
                 for i in range(3))


def apply_matrix(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def transform(mesh, translate=(0, 0, 0), rot_deg=(0, 0, 0), scale=(1, 1, 1), name=None):
    """缩放 → 旋转 → 平移（与 Blender 的对象变换同序）。返回**新** Mesh。"""
    m = euler_matrix(*rot_deg)
    out = []
    for v in mesh.verts:
        s = (v[0] * scale[0], v[1] * scale[1], v[2] * scale[2])
        r = apply_matrix(m, s)
        out.append((r[0] + translate[0], r[1] + translate[1], r[2] + translate[2]))
    return Mesh(out, mesh.faces, name or mesh.name)


def merge(meshes, name=""):
    """合并多个 Mesh（索引偏移），便于"一个部件一次进 Blender"。"""
    verts, faces = [], []
    for m in meshes:
        off = len(verts)
        verts.extend(m.verts)
        faces.extend(tuple(i + off for i in f) for f in m.faces)
    return Mesh(verts, faces, name)


# ─────────────────────────────────────────────────────────── 图元
def box(size=(1, 1, 1), name="box"):
    """轴对齐盒（中心在原点），面 CCW 朝外。"""
    sx, sy, sz = (size[0] / 2.0, size[1] / 2.0, size[2] / 2.0)
    v = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
         (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    f = [(0, 3, 2, 1),      # bottom (-Z)
         (4, 5, 6, 7),      # top (+Z)
         (0, 1, 5, 4),      # -Y
         (1, 2, 6, 5),      # +X
         (2, 3, 7, 6),      # +Y
         (3, 0, 4, 7)]      # -X
    return Mesh(v, f, name)


def cylinder(radius=1.0, height=1.0, segments=24, axis="z", name="cylinder",
             radius_top=None):
    """圆柱/圆台（`radius_top` 给不同值即圆台）。底面在 z=0，顶面在 z=height。"""
    rt = radius if radius_top is None else radius_top
    n = max(3, int(segments))
    v, f = [], []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        v.append((radius * math.cos(a), radius * math.sin(a), 0.0))
    for i in range(n):
        a = 2.0 * math.pi * i / n
        v.append((rt * math.cos(a), rt * math.sin(a), height))
    for i in range(n):
        j = (i + 1) % n
        f.append((i, j, j + n, i + n))          # 侧面
    f.append(tuple(range(n - 1, -1, -1)))       # 底面（朝下 ⇒ 逆序）
    f.append(tuple(range(n, 2 * n)))            # 顶面
    m = Mesh(v, f, name)
    if axis == "z":
        return m
    if axis == "x":
        return transform(m, translate=(-height / 2.0, 0, 0), rot_deg=(0, 90, 0), name=name)
    if axis == "y":
        return transform(m, translate=(0, -height / 2.0, 0), rot_deg=(-90, 0, 0), name=name)
    raise ValueError("axis 须为 x/y/z，实为 %r" % (axis,))


def cone(radius=1.0, height=1.0, segments=24, name="cone"):
    return cylinder(radius=radius, height=height, segments=segments,
                    radius_top=0.0, name=name)


def sphere(radius=1.0, segments=24, rings=12, name="sphere"):
    """UV 球（`rings` 为纬向分段）。"""
    n, m = max(3, int(segments)), max(2, int(rings))
    v, f = [], []
    for r in range(m + 1):
        phi = math.pi * r / m
        z = radius * math.cos(phi)
        rr = radius * math.sin(phi)
        for i in range(n):
            a = 2.0 * math.pi * i / n
            v.append((rr * math.cos(a), rr * math.sin(a), z))
    for r in range(m):
        for i in range(n):
            j = (i + 1) % n
            a = r * n + i
            b = r * n + j
            c = (r + 1) * n + j
            d = (r + 1) * n + i
            if r == 0:
                f.append((a, c, d))
            elif r == m - 1:
                f.append((a, b, c))
            else:
                f.append((a, b, c, d))
    return Mesh(v, f, name)


def torus(major=1.0, minor=0.25, seg_major=32, seg_minor=12, name="torus"):
    """环面（Z 轴为对称轴）。"""
    nm, ns = max(3, int(seg_major)), max(3, int(seg_minor))
    v, f = [], []
    for i in range(nm):
        a = 2.0 * math.pi * i / nm
        ca, sa = math.cos(a), math.sin(a)
        for j in range(ns):
            b = 2.0 * math.pi * j / ns
            rr = major + minor * math.cos(b)
            v.append((rr * ca, rr * sa, minor * math.sin(b)))
    for i in range(nm):
        i2 = (i + 1) % nm
        for j in range(ns):
            j2 = (j + 1) % ns
            f.append((i * ns + j, i2 * ns + j, i2 * ns + j2, i * ns + j2))
    return Mesh(v, f, name)


def quad_grid(size=(10.0, 10.0), divs=(10, 10), z=0.0, name="grid"):
    """水平网格面（地面用；CCW 朝 +Z）。"""
    nx, ny = int(divs[0]), int(divs[1])
    sx, sy = size[0] / 2.0, size[1] / 2.0
    v, f = [], []
    for j in range(ny + 1):
        for i in range(nx + 1):
            v.append((-sx + size[0] * i / nx, -sy + size[1] * j / ny, z))
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            f.append((a, a + 1, a + nx + 2, a + nx + 1))
    return Mesh(v, f, name)
