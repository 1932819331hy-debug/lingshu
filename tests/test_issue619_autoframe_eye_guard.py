# -*- coding: utf-8 -*-
"""test_issue619_autoframe_eye_guard · frame_mesh 可见性判据量纲守卫（#619）
================================================================================
缺陷（#619，成立）：`frame_mesh` 的可见集判据把 `orbit_dir` 的**单位方向**当
eye **位置**用（``n·(eye_dir − 面心) > 0``，面心取相对 bbox 中心）——隐含假设
相机在距中心 1 米处，与注释自述「与 `prepare_triangles` 的背面剔除同一口径：
``n·(eye − 面心) > 0``（eye = 真实相机位置）」不符。真实取景距离是物体尺度
的数倍，两形态都错：

  · 大网格（半尺度 > 1）：谓词变成 ``n·u > R``（|n·u| ≤ 1 < R）恒 False ⇒
    **可见集 = 0** ⇒ ``len(vis) < 8`` 静默退回拟合**全部**顶点（背面顶点把
    拟合框拉偏——正是该可见集机制要防的形态）；
  · 小网格（半尺度 < 1）：``n·u > R`` 几乎全过 ⇒ 大半个背面被当可见。

改前读数（本机，Windows / py3.12，main @ 375cf55，UV 球 62 顶点/120 面）：
  R=5.0：旧判据可见 0（正确 26）——frame_mesh 拟合集 = 62（全部顶点）；
  R=0.1：旧判据可见 37（正确 26）——拟合集被 11 个背面顶点污染。
  正确判据（eye = dist·d，dist 用 frame_points 初值公式）两尺度下同为 26
  （前半球，尺度无关）。

修法：可见集抽成纯函数 `_visible_face_verts(vs, faces, ctr, eye_rel)`，
调用方传**显式相机位置** ``dist·d``。

守卫判据（注入式断言，回退修复即红）：
  G1  `_visible_face_verts` 与「prepare_triangles 口径的独立参考实现」
      （显式 eye、逐面 ``n·(eye−面心)>0``）在 R=5 与 R=0.1 两球上逐点一致；
  G2  端到端：frame_mesh（截获 frame_points 收到的拟合集）在两尺度下都
      **只拟合可见顶点**（26），不再是全部顶点（62）——大网格不再静默退化、
      小网格不再背面混入；
  G3（防过修）无面网格（faces 空）仍走既有回退：拟合全部顶点。

判据来源：issue #619 取证 + `mesh3d.prepare_triangles` 的世界空间背面剔除
实现（同仓姊妹模块的既有口径）。
运行：pytest tests/test_issue619_autoframe_eye_guard.py
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.world import autoframe as AF  # noqa: E402
from lingshu.world.mesh3d import Face, MeshGeometry  # noqa: E402

RINGS, SECTORS = 6, 12


def sphere_mesh(radius):
    """合成 UV 球（法线 = 归一化面心方向，球心在原点时即外向法线）。"""
    verts = []
    for i in range(RINGS + 1):
        phi = math.pi * i / RINGS
        if i in (0, RINGS):
            verts.append((0.0, radius * math.cos(phi), 0.0))
            continue
        for j in range(SECTORS):
            th = 2.0 * math.pi * j / SECTORS
            verts.append((radius * math.sin(phi) * math.cos(th),
                          radius * math.cos(phi),
                          radius * math.sin(phi) * math.sin(th)))
    tris = []
    top, bot = 0, len(verts) - 1

    def ring(k):
        return 1 + (k - 1) * SECTORS

    for k in range(1, RINGS - 1):
        b0, b1 = ring(k), ring(k + 1)
        for j in range(SECTORS):
            a, bb = b0 + j, b0 + (j + 1) % SECTORS
            c, dd = b1 + j, b1 + (j + 1) % SECTORS
            tris.append((a, bb, c))
            tris.append((bb, dd, c))
    for j in range(SECTORS):
        tris.append((top, ring(1) + (j + 1) % SECTORS, ring(1) + j))
        tris.append((bot, ring(RINGS - 1) + j, ring(RINGS - 1) + (j + 1) % SECTORS))
    faces = []
    for t in tris:
        fc = tuple(sum(verts[k][ax] for k in t) / 3.0 for ax in range(3))
        nn = math.sqrt(sum(v * v for v in fc)) or 1.0
        faces.append(Face(idx=t, color=(200, 200, 200),
                          normal=tuple(v / nn for v in fc)))
    return MeshGeometry(verts=verts, faces=faces, name="sphere_r%.1f" % radius)


def _framing_dist(mesh, fov=50.0, margin=AF.DEFAULT_MARGIN_FRAC):
    """frame_points 同款初值距离公式。"""
    b = mesh.bounds()
    scale = max(1e-6, max(b[1][k] - b[0][k] for k in range(3)))
    half = math.radians(fov) / 2.0
    return (scale * 0.5) / math.tan(half) * (1.0 + 2.0 * margin)


def _reference_visible(mesh, eye_rel):
    """独立参考实现：prepare_triangles 口径（显式 eye、面心相对 bbox 中心）。"""
    b = mesh.bounds()
    ctr = tuple((b[0][k] + b[1][k]) / 2.0 for k in range(3))
    vis = set()
    for fc in mesh.faces:
        f = tuple(sum(mesh.verts[k][ax] for k in fc.idx) / 3.0 - ctr[ax]
                  for ax in range(3))
        n = fc.normal
        if (n[0] * (eye_rel[0] - f[0]) + n[1] * (eye_rel[1] - f[1])
                + n[2] * (eye_rel[2] - f[2])) > 0.0:
            vis.update(fc.idx)
    return vis


def test_visible_set_matches_explicit_eye_reference():
    """G1：_visible_face_verts 与显式 eye 的独立参考实现逐点一致（两尺度）。"""
    for radius in (5.0, 0.1):
        m = sphere_mesh(radius)
        d = AF.orbit_dir(35.0, 22.0)
        eye_rel = AF._mul(d, _framing_dist(m))
        got = AF._visible_face_verts(m.verts, m.faces, (0.0, 0.0, 0.0), eye_rel)
        ref = _reference_visible(m, eye_rel)
        assert got == ref, (
            "R=%.1f：可见集与 prepare_triangles 口径（显式 eye）不一致（#619）："
            "差集 %r" % (radius, got ^ ref))
        # 参考集本身要非平凡（前半球 ≈ 一半顶点），防恒等比较空对空
        assert len(ref) == 26, (radius, len(ref))


def test_frame_mesh_fits_only_visible_verts():
    """G2：frame_mesh 拟合集 = 可见顶点（26），不是全部顶点（62）——两尺度。"""
    import pytest

    for radius in (5.0, 0.1):
        m = sphere_mesh(radius)
        captured = {}
        orig = AF.frame_points

        def fake(points, sw, sh, **kw):
            captured["fit"] = [tuple(p) for p in (kw.get("fit_points") or ())]
            return "SPY"

        AF.frame_points = fake
        try:
            AF.frame_mesh(m, 640, 480)
        finally:
            AF.frame_points = orig
        assert len(captured.get("fit", ())) == 26, (
            "R=%.1f：frame_mesh 拟合集 %d 个顶点（#619：应为 26 个可见顶点——"
            "旧实现大网格静默退回全部 62、小网格混入背面 37）"
            % (radius, len(captured.get("fit", ()))))


def test_mesh_without_faces_still_fits_all_verts():
    """G3（防过修）：无面网格仍走既有回退——拟合全部顶点。"""
    m = sphere_mesh(5.0)
    m.faces = []
    captured = {}
    orig = AF.frame_points

    def fake(points, sw, sh, **kw):
        captured["fit"] = [tuple(p) for p in (kw.get("fit_points") or ())]
        return "SPY"

    AF.frame_points = fake
    try:
        AF.frame_mesh(m, 640, 480)
    finally:
        AF.frame_points = orig
    assert len(captured["fit"]) == len(m.verts) == 62


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
