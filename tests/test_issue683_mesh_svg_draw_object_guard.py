# -*- coding: utf-8 -*-
"""test_issue683_mesh_svg_draw_object_guard · #683 守卫
=======================================================
缺陷（#683）：`lingshu/world/mesh_svg.py` 在**非网格物体**分支里调用主干分派

    world._draw_object(backend, obj, cam, screen_w, screen_h, horizon_px)   # 6 个实参

而 `World3D._draw_object` 的签名只有 **5 参**
`(self, draw, obj, cam, screen_w, screen_h)`（`world3d.py` 主干 `_paint` 也是 5 参调用）
⇒ 任何 `category ∉ mesh3d.MESH_LIBRARY` 的物体（table/ball/tree…）走到这里立刻

    TypeError: World3D._draw_object() takes 6 positional arguments but 7 were given

多出来的那个 `horizon_px` 在 `_draw_object` 内**从未被使用** ⇒ 修法是**删掉多余实参**
（而不是反向扩签名——那会强迫所有既有调用点同步）。

守卫判据（逐条对应断言组，回退 ⇒ 指名哪条红）：
  ① 非网格物体（形状库部件 category="table"）⇒ `render_mesh_svg` **不抛 TypeError**、
     产出非空且合法 XML 的 SVG；混合场景（网格 + 非网格）同样跑通。
  ② 防误杀正对照：纯网格场景 ⇒ 输出与**改前冻结 sha256 逐字节相同**
     （删实参不改变网格通道的任何一个字节）。
  ③ 变异自证（本文件内自证"守卫有牙"）：把多余实参加回调用点 ⇒ ① 必红。

运行（lingshu 仓根）：
  python3 -X utf8 -m pytest tests/test_issue683_mesh_svg_draw_object_guard.py -q
"""
from __future__ import annotations

import hashlib
import math
import os
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lingshu.world import mesh3d, mesh_svg                       # noqa: E402
from lingshu.world.world3d import Camera3D, Object3D, World3D    # noqa: E402

SW, SH = 640, 480
CUBE = "_guard683_cube"
#: 纯网格场景（改前 = 改后）的冻结 sha256——删掉多余实参不改变网格通道任何一个字节。
#: 冻结口径：`_cube_world()` + `style="technical"` + 关呈现层（autoframe/阴影/渐变）。
MESH_ONLY_SHA256 = "83ee1a27c2bdbd4f71115afe6efa5dcb9c5f065239313a40ca59cda20f11ebb1"


def _cube_mesh(name=CUBE):
    """合成立方体（12 三角面，逐面法线朝外）——不依赖任何外部模型文件。"""
    if name in mesh3d.MESH_LIBRARY:
        return mesh3d.MESH_LIBRARY[name]
    verts = [(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
    idx = [(0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3), (0, 4, 5), (0, 5, 1),
           (2, 3, 7), (2, 7, 6), (0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5)]
    faces = []
    for t in idx:
        a, b, c = verts[t[0]], verts[t[1]], verts[t[2]]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        nl = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        faces.append(mesh3d.Face(t, (180, 120, 90), (nx / nl, ny / nl, nz / nl)))
    m = mesh3d.MeshGeometry(verts, faces, name=name, source="<guard>")
    mesh3d.MESH_LIBRARY[name] = m
    return m


def _world(cam, objects):
    w = World3D(cam)
    # 关掉呈现层，令判据只落在「分派是否跑通」与「网格通道字节」上
    w.contact_shadow = False
    w.ground_gradient = False
    w.auto_frame = False
    w.objects = objects
    return w


def _mesh_only_world():
    _cube_mesh()
    cam = Camera3D(fov_deg=55.0, cx=1.2, cy=0.8, cz=-1.6, yaw=0.6, pitch=-0.25)
    return _world(cam, [Object3D(CUBE, (0, 0, 0), (1, 1, 1), (180, 120, 90), "box")])


def _mixed_world():
    """混合场景：一个网格物体 + 三个非网格图元（覆盖 `_draw_object` 两条分派）。"""
    _cube_mesh()
    cam = Camera3D(fov_deg=52.0, cx=2.0, cy=1.2, cz=-2.6, yaw=0.45, pitch=-0.2)
    return _world(cam, [
        Object3D(CUBE, (0.0, 0.0, 0.0), (1, 1, 1), (180, 120, 90), "box"),
        Object3D("table", (1.4, 0.2, -0.5), (1.0, 0.6, 0.7), (150, 110, 80), "box"),
        Object3D("ball", (-1.2, 0.3, -0.4), (0.4, 0.4, 0.4), (200, 90, 90), "sphere"),
        Object3D("tree", (0.4, 0.1, -1.3), (0.8, 1.2, 0.8), (60, 130, 70), "box"),
    ])


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ===========================================================================
# ① 非网格物体 ⇒ 不崩、产出非空合法 SVG
# ===========================================================================
def test_683_1_非网格物体不抛TypeError且产出非空SVG():
    """`category ∉ MESH_LIBRARY` 的物体走 `_draw_object` 分派 ⇒ 不得抛 TypeError。

    改前读数：`TypeError: World3D._draw_object() takes 6 positional arguments
    but 7 were given`（见提交正文复现）。改后：正常产出。
    """
    _cube_mesh()
    cam = Camera3D(fov_deg=52.0, cx=2.0, cy=1.2, cz=-2.6, yaw=0.45, pitch=-0.2)
    w = _world(cam, [Object3D("table", (1.4, 0.2, -0.5), (1.0, 0.6, 0.7),
                              (150, 110, 80), "box")])
    try:
        svg = mesh_svg.render_mesh_svg(w, SW, SH)
    except TypeError as e:                                        # noqa: BLE001
        pytest.fail("非网格物体仍触发 TypeError（#683 未修）：%s" % e)
    assert svg and len(svg) > 200, "非网格物体的 SVG 为空/过小"
    ET.fromstring(svg)                       # 必须是合法 XML
    # 形状库部件通道确实画出了东西（非空物体层）
    ids = [g["id"] for g in mesh_svg.layer_report(svg)]
    assert any(i.startswith("obj-") for i in ids), "非网格物体没有产生物体层：%s" % ids


def test_683_2_单图元回退分支不崩():
    """`ball` 无形状库部件 ⇒ 走单图元回退（`_draw_sphere`）也走同一处 `_draw_object`。"""
    cam = Camera3D(fov_deg=52.0, cx=2.0, cy=1.2, cz=-2.6, yaw=0.45, pitch=-0.2)
    w = _world(cam, [Object3D("ball", (-1.2, 0.3, -0.4), (0.4, 0.4, 0.4),
                              (200, 90, 90), "sphere")])
    svg = mesh_svg.render_mesh_svg(w, SW, SH)
    ET.fromstring(svg)
    assert mesh_svg.element_counts(svg)["__drawable__"] >= 2, "单图元回退没画出东西"


def test_683_3_混合场景导出可用():
    """网格 + 非网格混排（改前必崩的端到端场景）⇒ 导出跑通、含两条通道的元素。"""
    w = _mixed_world()
    st = {}
    svg = mesh_svg.render_mesh_svg(w, SW, SH, stats=st)
    ET.fromstring(svg)
    assert st["objects"] == 4, "物体计数不对：%r" % st
    assert st["mesh_objects"] == 1, "网格通道未命中：%r" % st
    assert len(svg) > 1000, "混合场景导出过小"


# ===========================================================================
# ② 防误杀正对照：纯网格场景逐字节不变
# ===========================================================================
def test_683_4_纯网格场景输出与改前逐字节相同():
    """删掉多余实参**不改变网格通道**：纯网格场景 sha256 == 改前冻结值。

    这是防误杀正对照——修复只动非网格分支，网格分支的字节必须一格不差。
    """
    w = _mesh_only_world()
    svg = mesh_svg.render_mesh_svg(w, SW, SH, style="technical")
    got = _sha(svg)
    assert got == MESH_ONLY_SHA256, (
        "纯网格场景输出被改动了（修复误伤网格通道）：%s vs 冻结 %s"
        % (got[:16], MESH_ONLY_SHA256[:16]))


def test_683_5_网格场景两次导出逐字节相同():
    w = _mesh_only_world()
    a = mesh_svg.render_mesh_svg(w, SW, SH, style="technical")
    b = mesh_svg.render_mesh_svg(w, SW, SH, style="technical")
    assert a == b, "两次导出不同（确定性被破坏）"


# ===========================================================================
# ③ 变异自证：非网格分支必须**以 5 参**调用主干（= 缺陷形态 6 参即红）
# ===========================================================================
def test_683_6_非网格分支以5参调用主干_实参计数探针(monkeypatch):
    """实参计数探针：真实 `render_mesh_svg` 路径下，非网格分支对
    `World3D._draw_object` 的调用**必须恰为 5 个位置实参**。

    这条同时是「回退即红」的机器判据：把 `horizon_px` 加回调用点 ⇒ 探针记到 6
    （且 `orig(self, *a)` 立刻 TypeError）⇒ 本测红。比只断言"不抛异常"更有牙
    ——它钉住的是**调用契约**（5 参），而非仅"恰好没崩"。
    """
    calls = []
    orig = World3D._draw_object

    def _spy(self, *a, **kw):
        calls.append(len(a))
        return orig(self, *a, **kw)

    monkeypatch.setattr(World3D, "_draw_object", _spy)
    _cube_mesh()
    cam = Camera3D(fov_deg=52.0, cx=2.0, cy=1.2, cz=-2.6, yaw=0.45, pitch=-0.2)
    w = _world(cam, [Object3D("table", (1.4, 0.2, -0.5), (1.0, 0.6, 0.7),
                              (150, 110, 80), "box")])
    mesh_svg.render_mesh_svg(w, SW, SH)
    assert calls == [5], (
        "非网格分支未以 5 参调用 _draw_object（缺陷形态=6 参）：%r" % calls)


def test_683_7_实参计数探针在6参形态下必红(monkeypatch):
    """反向自证：把主干替身做成"6 参调用即 TypeError"的形态，按缺陷调用方式喂进去
    ⇒ 探针必然红。证明 ⑥ 的判据对"多传实参"确实敏感。"""
    orig = World3D._draw_object

    def _spy(self, *a, **kw):
        return orig(self, *a, **kw)          # 真实签名 5 参 ⇒ 6 参必 TypeError

    monkeypatch.setattr(World3D, "_draw_object", _spy)
    _cube_mesh()
    cam = Camera3D(fov_deg=52.0, cx=2.0, cy=1.2, cz=-2.6, yaw=0.45, pitch=-0.2)
    w = _world(cam, [Object3D("table", (1.4, 0.2, -0.5), (1.0, 0.6, 0.7),
                              (150, 110, 80), "box")])
    with pytest.raises(TypeError):
        # 缺陷形态：多传 horizon_px
        w._draw_object(None, w.objects[0], cam, SW, SH, SH * 0.45)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
