# -*- coding: utf-8 -*-
"""呈现层优化守卫 —— 自动取景 / 接触阴影 / 混合场景（**全部合成几何**，CI 可跑）。

本文件钉住三类改动，每类都给出"抽掉修复 ⇒ 哪组断言转红"的实测口径：

A 组 · 自动取景（`autoframe` + `world3d.render` 接入）
    缺陷（改前）：`render(..., camera=None)` 沿用调用方随手设的 `world.camera`；
    实测三个真实模型**全部裁切**（turret 底 −1.9、arm 左 −4.2、hex01 左 −155.6 下 −182.4），
    主体压在画面一角（中心 0.23~0.25）。
    判据：所有顶点投影后**四边留边 > 0**、包围盒中心落在画布中心（1e-3 画布）、
    左右留边对称。
    回退即红：`w.auto_frame = False` ⇒ A3 的"合格"分支转红（几何判据失去满足）。

B 组 · 接触阴影（`world3d._draw_contact_shadow`）+ 地面渐变
    缺陷：`shade_paint` 自认的天花板之一 —— 没有接触阴影，物体像浮在地面上。
    判据：含网格场景开/关 ⇒ **像素必须有差**；**全图元场景必须逐字节不变**（B1）。
    回退即红：`contact_shadow` 默认改 False ⇒ B1 红。

C 组 · 混合场景（`mesh3d.ZBuffer.polygon` 的真 bug）
    缺陷：`polygon` 只写 12 字段而 `rasterize_triangles` 读 15 ⇒ **图元+网格同场必崩**。
    回退即红：把 `polygon` 改回 12 字段 ⇒ C1 红（IndexError）。

跑法：`python -m pytest tests/test_svg_paint_opt_guard.py -q`
"""
from __future__ import annotations

import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world import autoframe as AF                                   # noqa: E402
from lingshu.world import mesh3d                                            # noqa: E402
from lingshu.world.world3d import Camera3D, Object3D, World3D               # noqa: E402

SW, SH = 480, 360
#: 改前调用方的"随手设"相机（三个真实模型的取证图就是这么渲出来的）——本文件用同一台
MANUAL = dict(fov_deg=50.0, cx=2.6, cy=1.4, cz=-2.9, yaw=0.5, pitch=-0.22)


def _mk_mesh(name: str, hx=0.3, hy=0.45, hz=1.3) -> mesh3d.MeshGeometry:
    """细长长方体（默认 0.6 × 0.9 × 2.6），**底面贴地**（y ∈ [0, 2hy]）。

    为什么细长：`auto_view` 的判据是"最长轴垂直于视线"，立方体上无区分度。
    为什么底面贴地：中心在原点会让物体**一半埋在地下**，接触阴影（画在 y=0 平面）
    被物体自己盖住 ⇒ 像素无变化，B 组会误判"阴影没生效"（本判据第一次就是这么红的）。
    """
    v = [(-hx, 0.0, -hz), (hx, 0.0, -hz), (hx, 2 * hy, -hz), (-hx, 2 * hy, -hz),
         (-hx, 0.0, hz), (hx, 0.0, hz), (hx, 2 * hy, hz), (-hx, 2 * hy, hz)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3)]
    faces = []
    for a, b, c, d in quads:
        for (i, j, k) in ((a, b, c), (a, c, d)):
            p0, p1, p2 = v[i], v[j], v[k]
            ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
            wx, wy, wz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
            nx, ny, nz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
            nl = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            faces.append(mesh3d.Face((i, j, k), (150, 160, 175),
                                     (nx / nl, ny / nl, nz / nl)))
    return mesh3d.MeshGeometry(v, faces, name=name, source="<synthetic>")


def _scene(name: str, mesh=None, *, auto=True):
    """造一个**单网格**场景；相机刻意给"随手设"的那台（模拟改前的调用方行为）。"""
    mesh = mesh or _mk_mesh(name)
    mesh3d.MESH_LIBRARY[name] = mesh
    b = mesh.bounds()
    ctr = tuple((b[0][k] + b[1][k]) / 2.0 for k in range(3))
    w = World3D(Camera3D(**MANUAL))
    w.objects = [Object3D(name, ctr, (1, 1, 1), (150, 160, 175), "box")]
    w.auto_frame = auto
    return w, mesh


def _proj_stats(cam, mesh, center=(0.0, 0.0, 0.0), sw=SW, sh=SH) -> dict:
    """网格顶点投影后的读数：四边留边（画布空间）/中心归一化/是否越界。

    ⚠ `center` 必须传 `obj.center`：`mesh.verts` 是**局部坐标**，渲染时顶点会加上
    `Object3D.center`（`_draw_mesh` 的平移）。漏掉这一项会让读数整体偏移 ——
    本判据第一次就是这么把"自动取景合格"误报成"裁切 + 中心偏"的。
    """
    cx0, cy0, cz0 = center
    pts = [cam.project((v[0] + cx0, v[1] + cy0, v[2] + cz0), sw, sh) for v in mesh.verts]
    pts = [p for p in pts if p is not None]
    assert pts, "全部顶点都落在相机后方"
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    l, r, t, b = min(xs), max(xs), min(ys), max(ys)
    return {"ml": l, "mr": sw - r, "mt": t, "mb": sh - b,
            "cx": (l + r) / 2 / sw, "cy": (t + b) / 2 / sh,
            "clipped": bool(l < -0.5 or t < -0.5 or r > sw + 0.5 or b > sh + 0.5)}


def _bad_center(g: dict) -> list:
    bad = []
    if g["clipped"]:
        bad.append("裁切")
    if abs(g["cx"] - 0.5) > 1e-3 or abs(g["cy"] - 0.5) > 1e-3:
        bad.append("中心 (%.4f,%.4f) 偏" % (g["cx"], g["cy"]))
    return bad


# ============================================================== A 自动取景
def test_a1_frame_points_收敛到画布中心():
    """A1：`frame_points` 迭代后投影包围盒中心 = 画布中心（1e-3 内）。"""
    m = _mk_mesh("__g_a1__")
    lo, hi = m.bounds()
    corners = [(lo[0] if i & 1 else hi[0], lo[1] if i & 2 else hi[1],
                lo[2] if i & 4 else hi[2]) for i in range(8)]
    g = _proj_stats(AF.frame_points(corners, SW, SH), m)
    assert not _bad_center(g), _bad_center(g)


def test_a2_frame_mesh_四边留边为正且左右对称():
    """A2：`frame_mesh` 后四边留边 > 0，且左右/上下留边基本对称（差 < 2 px）。"""
    m = _mk_mesh("__g_a2__")
    b_ = m.bounds()
    ctr = tuple((b_[0][k] + b_[1][k]) / 2.0 for k in range(3))
    #: `frame_mesh` 也要拿到同一个 `center`（它就是渲染时的 `obj.center`）
    g = _proj_stats(AF.frame_mesh(m, SW, SH, center=ctr), m, ctr)
    assert min(g["ml"], g["mr"], g["mt"], g["mb"]) > 0.0, g
    assert abs(g["ml"] - g["mr"]) < 2.0 and abs(g["mt"] - g["mb"]) < 2.0, g


def test_a3_frame_world_合格而随手设的相机不合格():
    """A3（**回退即红**）：`frame_world`（就是 `render` 内部走的那条）必须让构图合格；
    而调用方"随手设"的那台相机**必须不合格** —— 后者若也合格，本判据就没有判别力。
    把 `render` 的自动取景抽掉（`w.auto_frame=False`）⇒ 生效的就是那台不合格的相机。"""
    w, m = _scene("__g_a3__")
    good = _proj_stats(AF.frame_world(w, SW, SH), m, w.objects[0].center)
    assert not _bad_center(good), "自动取景后构图仍不合格：%s" % _bad_center(good)
    manual = _proj_stats(w.camera, m, w.objects[0].center)
    assert _bad_center(manual), "随手设的相机竟然也合格 ⇒ 本判据失去判别力（回退即红失效）"


def test_a4_auto_view_让最长轴垂直于视线():
    """A4：`auto_view` 选出的视角，其 `|cos(长轴, 视线)|` 必须 < 0.35。

    为什么这条重要（实测教训）：我第一版判据是"投影面积最大"，arm 因此选了 el=−8°
    —— 量化判据（不裁切/居中/覆盖率）**全部合格**，但画面上只剩底座、连杆全被叠掉。
    """
    m = _mk_mesh("__g_a4__")
    rep = {}
    _cam, (az, el, _area) = AF.auto_view(m, SW, SH, report=rep)
    al = abs(AF.orbit_dir(az, el)[2])
    assert al < 0.35, "几乎沿长轴看（|cos|=%.3f）⇒ 主体被透视缩短成一坨" % al


def test_a5_显式给相机时一律尊重调用方():
    """A5：`render(camera=…)` 必须用调用方给的相机（可复现对照的前提）。"""
    w, m = _scene("__g_a5__")
    cam = Camera3D(fov_deg=50.0, cx=0.0, cy=0.9, cz=-4.0, yaw=0.0, pitch=0.0)
    img = w.render(SW, SH, camera=cam)
    assert img.size == (SW, SH)
    g_given = _proj_stats(cam, m, w.objects[0].center)
    g_auto = _proj_stats(AF.frame_world(w, SW, SH), m, w.objects[0].center)
    assert (g_given["cx"], g_given["cy"]) != (g_auto["cx"], g_auto["cy"]), \
        "显式相机与自动取景结果相同 ⇒ 覆盖与尊重无法区分，可复现对照失效"


# ============================================================== B 接触阴影
def test_b1_含网格场景_阴影与渐变确实改变像素():
    """B1（**回退即红**）：含网格场景默认带接触阴影 + 地面渐变 ⇒ 关掉后像素必须不同。
    把 `contact_shadow` 默认改 False（且渐变同关）⇒ 本组红。"""
    w, _m = _scene("__g_b1__")
    a = w.render(SW, SH).tobytes()
    w.contact_shadow = False
    w.ground_gradient = False
    b = w.render(SW, SH).tobytes()
    assert a != b, "关掉阴影+渐变后像素完全相同 ⇒ 两者都没生效"


def test_b2_全图元场景逐字节不变_B1():
    """B2：**全图元无网格**场景的位图必须逐字节不变（B1 硬约束）——
    新特性只允许加在"含网格"那一支上。"""
    w = World3D(Camera3D(fov_deg=52.0, cx=2.2, cy=1.5, cz=-3.0, yaw=0.42, pitch=-0.22))
    w.objects = [Object3D("table", (0.0, 0.0, 0.0), (1.4, 0.8, 0.9), (150, 110, 80), "box"),
                 Object3D("ball", (-1.4, 0.3, -0.4), (0.5, 0.5, 0.5), (200, 90, 90), "sphere")]
    assert not w._has_mesh(), "本组前提：场景里没有网格"
    a = w.render(SW, SH).tobytes()
    w.contact_shadow = False
    w.ground_gradient = False
    w.auto_frame = False
    b = w.render(SW, SH).tobytes()
    assert a == b, "全图元场景的位图被新特性改动了 ⇒ 违反 B1（该支必须零改动）"


def test_b3_阴影开关是确定行为():
    """B3：关阴影两次结果一致、且 ≠ 开阴影（确定性 + 确实生效）。"""
    w, _m = _scene("__g_b3__")
    w.contact_shadow = False
    a = w.render(SW, SH).tobytes()
    b = w.render(SW, SH).tobytes()
    w.contact_shadow = True
    c = w.render(SW, SH).tobytes()
    assert a == b, "关阴影后两次渲染不一致（不确定行为）"
    assert a != c, "开了阴影却与关着一样 ⇒ 阴影没生效"


# ============================================================== C 混合场景
def test_c1_图元与网格同场_不崩且非空():
    """C1（**回退即红**）：图元 + 网格同场渲染必须成功且非空。
    把 `ZBuffer.polygon` 改回 12 字段 ⇒ 本组红（IndexError）。"""
    w, _m = _scene("__g_c1__")
    w.objects.append(Object3D("__c1_box__", (3.0, 0.3, 0.0), (0.5, 0.5, 0.5),
                              (200, 120, 90), "box"))
    assert w._has_mesh(), "本组前提：场景含网格（才能走到 ZBuffer 的图元分支）"
    img = w.render(SW, SH)
    assert img.size == (SW, SH)
    assert len(set(img.tobytes())) > 1, "混场渲染出来是纯色 ⇒ 图元分支没画出来"


def test_c2_ZBuffer_polygon_三角形字段数与内核一致():
    """C2：直接钉死 12 → 15 字段那处契约（字段数不对 ⇒ `rasterize_triangles` 抛 IndexError）。"""
    zb = mesh3d.ZBuffer(32, 32, (0, 0, 0))
    zb.set_depth(0.5)
    zb.polygon([(2, 2), (20, 2), (20, 20), (2, 20)], fill=(200, 60, 60))
    assert len(set(zb.to_bytes())) > 1, "polygon 没写进任何像素"
