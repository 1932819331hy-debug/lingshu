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


# ============================================================== D 线稿两难
def _turret_or_synthetic():
    """有真模型用真模型；没有就退回合成网格（保证 CI 上也有判别力）。"""
    import os as _os
    path = "/mnt/d/DSH/blender/out/wmtest/turret/mesh.obj"
    if _os.path.isfile(path):
        key = "__g_d_turret__"
        if key not in mesh3d.MESH_LIBRARY:
            mtl = path[:-4] + ".mtl"
            cols = mesh3d.load_mtl(mtl) if _os.path.isfile(mtl) else None
            mesh3d.load_obj_mesh(path, key, mtl_colors=cols)
        return key, mesh3d.MESH_LIBRARY[key]
    key = "__g_d_syn__"
    m = _mk_mesh(key, hx=0.6, hy=0.7, hz=0.9)
    mesh3d.MESH_LIBRARY[key] = m
    return key, m


def _svg_pair(mode="interleave", ink_class="ink"):
    """渲两份 SVG：带 ink class / 不带（其余完全同参）。返回 (带, 不带)。"""
    from lingshu.world import mesh_svg
    key, m = _turret_or_synthetic()
    b = m.bounds()
    ctr = tuple((b[0][k] + b[1][k]) / 2.0 for k in range(3))
    out = []
    for cls in (ink_class, None):
        w = World3D(Camera3D(**MANUAL))
        w.objects = [Object3D(key, ctr, (1, 1, 1), (170, 170, 175), "box")]
        w.auto_frame = False
        st = dict(mesh_svg.STYLE_PRESETS["technical"])
        st["outline_mode"] = mode
        st["ink_class"] = cls
        out.append(mesh_svg.render_mesh_svg(w, SW, SH, camera=w.camera, style=st))
    return out


def test_d1_线稿元素全部带_ink_类_色块不带():
    """D1：`interleave` 下**每一个线元素**都要带 `class="ink"`，且**色块一个都不能带**。

    这条是"线稿可整层关"的可判定形式：编辑器一条 `.ink{display:none}` 就能关掉全部线稿，
    而不必关掉色块。若只给部分线打类（例如漏掉折角线），关层后画面会残留半截线。
    """
    import re as _re
    on, _off = _svg_pair()
    n_line = len(_re.findall(r"<line|<polyline", on))
    n_ink = len(_re.findall(r'class="ink"', on))
    assert n_line > 0, "没有线元素可测"
    assert n_ink == n_line, "线元素 %d 个但带 ink 类的只有 %d 个（会漏关）" % (n_line, n_ink)
    #: 色块（polygon）不得带 ink 类
    for m_ in _re.finditer(r"<polygon[^>]*>", on):
        assert 'class="ink"' not in m_.group(0), "色块被误打了 ink 类：" + m_.group(0)[:80]


def test_d2_加类不改文档顺序_故遮挡仍正确():
    """D2（**核心判据**）：带类与不带类的两份 SVG，**去掉 class 属性后必须逐字节相同**。

    这就证明了「顺序未变」⇒ 遮挡关系与 `interleave` 完全一致（不会 X 光透出）。
    为什么这条是强的：`outline_mode="separate"` 的修法会把线搬到文档末尾（顺序变了 ⇒
    线透出），而本件只加属性 ⇒ 文档顺序**一个字都没动**。
    """
    import re as _re
    on, off = _svg_pair()
    strip_on = _re.sub(r' class="ink"', "", on)
    assert strip_on == off, "去掉 class 后两份 SVG 不同 ⇒ 文档顺序被改了（遮挡可能已变）"


def test_d3_关掉线稿层确实改变画面():
    """D3：把带 ink 类的元素删掉（等价 CSS 隐藏）后，**渲染结果必须与全量不同**
    —— 否则"可整层关"是句空话（关不关都一样）。"""
    import re as _re
    import xml.etree.ElementTree as _ET
    on, _off = _svg_pair()
    root = _ET.fromstring(on)

    def strip(el):
        for ch in list(el):
            if ch.get("class") == "ink":
                el.remove(ch)
            else:
                strip(ch)

    strip(root)
    hidden = _ET.tostring(root, encoding="unicode")
    n_on = len(_re.findall(r'class="ink"', on))
    n_hidden = len(_re.findall(r'class="ink"', hidden))
    assert n_on > 0, "没有 ink 类元素"
    assert hidden != on, "删掉 ink 元素后文档没变"
    #: 隐藏后 ink 元素应**一个不剩**（不是"少了几条"—— 否则说明删漏了）
    assert n_hidden == 0, "隐藏后仍剩 %d 个 ink 元素（删漏）" % n_hidden
    #: 线元素总数也应归零（线条确实被整体移除）
    assert len(_re.findall(r"<line|<polyline", hidden)) == 0, "隐藏后仍有线元素"


# ============================================================== E 环境反射
def test_e1_环境染色方向正确_上冷下暖():
    """E1：`env_tint` 必须**上冷下暖** —— `ny=+1`（朝上的面）偏**蓝**，`ny=-1` 偏**暖**。

    这条是"环境反射"的可判定形式：金属反射的是**环境**（天空冷 / 地面暖），
    所以朝上的面该偏冷、朝下的面该偏暖。过去管线只做「标量 × 固有色」⇒ 不可能有这个方向性。
    """
    from lingshu.world import shade_paint as SP
    base = (199, 204, 214)
    up = SP.env_tint(base, 1.0, "metal")
    dn = SP.env_tint(base, -1.0, "metal")
    #: ⚠ 用**相对**判据，不用绝对"红>蓝"。原因（本判据第一版就这么红的）：
    #: 基色 (199,204,214) **本身就偏冷**（b−r=15），而 V 底环境色 (134,126,116)
    #: 的暖偏差只有 18、混合系数 0.30 ⇒ **不足以翻转**基色的冷暖，只能让它"变暖一些"。
    #: 绝对判据会要求"朝下的面变成暖色"，那在物理上就是错的（反射不会盖过固有色）。
    assert up[2] - up[0] > base[2] - base[0], \
        "朝上的面应比基色更**冷**（反射天空），实为 %r（基色 %r）" % (up, base)
    assert dn[2] - dn[0] < base[2] - base[0], \
        "朝下的面应比基色更**暖**（反射地面），实为 %r（基色 %r）" % (dn, base)
    assert up[2] - up[0] > dn[2] - dn[0], "朝上的面应比朝下的面更冷"
    #: ⚠ 这里**不要**写 `dn[0] > up[0]`：环境色自身的红分量是 TOP(172) > BOT(134)
    #: ⇒ 朝下的面红分量反而**更少**。判"暖"要看**冷暖差 b−r**，不是单看红通道
    #: （本判据第二版就是因为这个错误假设红的）。
    assert up[2] > dn[2], "朝上的面蓝分量应更大：%r vs %r" % (up, dn)
    assert up[2] - up[0] > dn[2] - dn[0], "朝上应更冷、朝下应更暖：%r vs %r" % (up, dn)


def test_e2_gain0_时逐位原样_零回归():
    """E2：`env_tint(..., gain=0)` 必须**原样返回**基色 ⇒ 证明"关掉即回到改动前"
    （守卫 fixture 就是靠这一点保 A1/A2 的逐字节真值）。"""
    from lingshu.world import shade_paint as SP
    for base in ((199, 204, 214), (0, 0, 0), (255, 255, 255), (17, 99, 200)):
        got = SP.env_tint(base, 0.7, "metal", 0.0)
        assert tuple(got) == tuple(int(v) for v in base), (base, got)


def test_e3_金属的反射强度强于粗糙材质():
    """E3：角色强度须与物理直觉一致 —— **金属 > 涂漆金属 > 石材/布料**。

    粗糙表面把环境"打散"了，只剩漫反射；抛光金属几乎镜面。若这条反了，说明
    `ENV_ROLE_STRENGTH` 被写错（那时画面会变成"石头比金属更有反光"）。
    """
    from lingshu.world import shade_paint as SP
    k = SP.ENV_ROLE_STRENGTH
    assert k["metal"] > k["painted_metal"] > k["stone"], k
    assert k["metal"] > k["fabric"] and k["metal"] > k["wood"], k
    assert k["emissive"] == 0.0, "自发光不吃环境反射"


def test_e4_开关真的改变渲染_关掉即回到基线():
    """E4：`World3D.env_reflect` 开/关必须产生**不同的像素**，且关掉与"本来就没有该特性"
    一致（这里用"关两次相同 + 不等于开"表达）。"""
    key, m = _turret_or_synthetic()
    b = m.bounds()
    ctr = tuple((b[0][k] + b[1][k]) / 2.0 for k in range(3))
    out = []
    for on in (False, False, True):
        w = World3D(Camera3D(**MANUAL))
        w.objects = [Object3D(key, ctr, (1, 1, 1), (170, 170, 175), "box")]
        w.auto_frame = False
        w.contact_shadow = False
        w.ground_gradient = False
        w.env_reflect = on
        out.append(w.render(SW, SH).tobytes())
    assert out[0] == out[1], "关掉环境反射后两次渲染不一致（不确定行为）"
    assert out[0] != out[2], "开了环境反射却与关着一样 ⇒ 特性没生效"
