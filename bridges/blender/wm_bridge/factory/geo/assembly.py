# -*- coding: utf-8 -*-
"""wm_bridge.factory.geo.assembly · **link/关节层级 → 世界位姿求解**（纯标准库 · 零 bpy）

为什么单独成件：位姿是"四条产线的公共真值"。渲染帧、SVG 投影、逐帧真值导出**必须同源**，
否则就是上一轮那族病的再一次发作 —— `shapes.json` 与 `oracle.jsonl` 各拿一个姿态
（实测：机械臂 shapes 是动画末帧 u=1 的 +35°，oracle tick 0 是 −35°）。
所以本模块把"**某一帧的某个 link 在哪、朝哪**"收成**唯一一个纯函数**：
它不 import bpy、不碰场景，可以在没有 Blender 的机器上完整自测（与 `bpy_dump.py`
"Blender 侧只读矩阵"的分工一致）。

-------------------------------------------------------------------------- 位姿口径（与 spec.py / Blender 一致）
  · 全部在 **Blender 空间（Z-up）**，单位米 —— 与 `spec.py` 同口径。
    跨到 lingshu 的换算**不在这里**（`geom.to_lingshu` 是唯一落点）。
  · `link.anchor` 是**静止（rest）时**该 link 枢轴的世界坐标（spec.py 明文）。
  · link **没有** spec 级静止旋转 ⇒ `rest(link) = T(anchor(link))`（纯平移）。

-------------------------------------------------------------------------- 递归式（本模块的定义）
  记 `rest(L) = T(anchor(L))`，`J(L,t)` = 该 link 自身关节在 t 的刚体变换（绕/沿自身枢轴）。

    M(L,t) = [ M(parent,t) · rest(parent)⁻¹ ] · rest(L) · J(L,t)        （有父）
    M(L,t) = rest(L) · J(L,t)                                          （无父）

  `[M(p,t)·rest(p)⁻¹]` 是父链从静止到当前的**刚性增量**（绕父枢轴转动）；
  它作用在子 link 的静止位姿上 ⇒ 子 link 的位置**跟着父关节画圆**，
  这正是 Blender 里 `ob.parent = p; ob.location = anchor − anchor(p)` 的等价物。

  实测校核（hex-model 的 turret，window 与既有 dump 对拍）：
    · `TURRET_root` anchor (0,0,0)，`TURRET_yaw` anchor (0,0,0.27) —— M(yaw,0) 平移 = (0,0,0.27)
      = 既有 dump 的 `links[TURRET_yaw].anchor` ✓
    · `TURRET_tip_i` 是 yaw 的子级、静止锚点 (0.16sin a, 0.61cos a, 0.57)；
      t=1 时 M(tip_0) 绕 z 转 7.5°（linear）⇒ 平移落到圆上 ✓

-------------------------------------------------------------------------- 关节值的精确语义
    static     恒等
    spin       θ(t) = 360° · t/period_s + phase_deg            （绕 axis）
    swing      θ(t) = amp_deg · sin(2π·t/period_s) + phase_deg （绕 axis）
    prismatic  d(t) = amp_m · sin(2π·t/period_s)               （沿 axis）
    ease       value(t) = 对 `keys` 做**分段线性**插值（t 单位秒；<首键取首值，>末键取末值），
               单位 deg ⇒ 绕 axis 转，单位 m ⇒ 沿 axis 平移
    t = frame / tick_hz  （tick_hz = spec.scene.fps）

  ⚠ **`easing` 不在本模块**：`easing="bezier"` 改的是**烘焙方式**（关键帧间隔 + 插值类型），
  不是解析式。本模块给的是"匀速语义下的精确值"；bezier 那一路由 `bpy_produce.py`
  按 Blender 的关键帧求值落进 dump（实测：既有 turret 的 yaw 每 6 帧一个 BEZIER 关键帧，
  tick 1/2/3 = 2.0126/7.4402/15.3678°，而匀速是 7.5/15.0/22.5°）。
  两条路都会在产物里被**分别**核对（见 `_det/`）。
"""
from __future__ import annotations

import math

from . import spec as S

__all__ = [
    "AssemblyError", "IDENT", "mat_mul", "mat_from_trans", "mat_from_axis_angle",
    "mat_inv_rigid", "mat_apply", "rest_mat", "joint_value", "joint_delta",
    "local_offset", "link_world_mats", "part_world_mat", "topo_order",
    "frames_of", "motion_label",
]


class AssemblyError(Exception):
    """层级/关节无法求解（fail-closed：不猜、不静默降级）。"""


# ─────────────────────────────────────────────────────────── 4×4 刚体小代数
# 表示：tuple of 4 tuples（行主序），与 Blender 的 Matrix 元素序一致，便于逐位对拍。
IDENT = ((1.0, 0.0, 0.0, 0.0),
         (0.0, 1.0, 0.0, 0.0),
         (0.0, 0.0, 1.0, 0.0),
         (0.0, 0.0, 0.0, 1.0))


def mat_mul(a, b):
    """4×4 相乘 a·b（先 b 后 a）。"""
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4))


def mat_from_trans(t):
    """纯平移矩阵。"""
    x, y, z = (float(t[0]), float(t[1]), float(t[2]))
    return ((1.0, 0.0, 0.0, x),
            (0.0, 1.0, 0.0, y),
            (0.0, 0.0, 1.0, z),
            (0.0, 0.0, 0.0, 1.0))


def _axis_unit(axis):
    a = str(axis).lower()
    if a == "x":
        return (1.0, 0.0, 0.0)
    if a == "y":
        return (0.0, 1.0, 0.0)
    if a == "z":
        return (0.0, 0.0, 1.0)
    raise AssemblyError("未知轴 %r（允许 x/y/z）" % (axis,))


def mat_from_axis_angle(axis, deg):
    """绕单位轴转 deg 度的旋转矩阵（罗德里格斯，右手、与 Blender 同向）。"""
    ux, uy, uz = _axis_unit(axis)
    r = math.radians(float(deg))
    c, s = math.cos(r), math.sin(r)
    C = 1.0 - c
    return ((c + ux * ux * C, ux * uy * C - uz * s, ux * uz * C + uy * s, 0.0),
            (uy * ux * C + uz * s, c + uy * uy * C, uy * uz * C - ux * s, 0.0),
            (uz * ux * C - uy * s, uz * uy * C + ux * s, c + uz * uz * C, 0.0),
            (0.0, 0.0, 0.0, 1.0))


def mat_inv_rigid(m):
    """刚体矩阵求逆（Rᵀ | −Rᵀt）—— 不调通用求逆，避免把"位姿"算成含缩放的东西。"""
    r = [[m[i][j] for j in range(3)] for i in range(3)]
    t = [m[i][3] for i in range(3)]
    rt = [[r[j][i] for j in range(3)] for i in range(3)]
    nt = [-sum(rt[i][k] * t[k] for k in range(3)) for i in range(3)]
    return ((rt[0][0], rt[0][1], rt[0][2], nt[0]),
            (rt[1][0], rt[1][1], rt[1][2], nt[1]),
            (rt[2][0], rt[2][1], rt[2][2], nt[2]),
            (0.0, 0.0, 0.0, 1.0))


def mat_apply(m, p):
    """m · (p,1) → 3 元组（点变换，含平移）。"""
    x, y, z = float(p[0]), float(p[1]), float(p[2])
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
            m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
            m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3])


def rest_mat(link):
    """link 的静止位姿：纯平移到 anchor。"""
    return mat_from_trans(link["anchor"])


# ─────────────────────────────────────────────────────────── 关节值
def _ease_value(keys, t, unit):
    """分段线性查表（t 秒）。出界取端点值（不外推 —— 外推会造出规格没声明的运动）。"""
    ks = [(float(k[0]), float(k[1])) for k in keys]
    if t <= ks[0][0]:
        return ks[0][1]
    if t >= ks[-1][0]:
        return ks[-1][1]
    for i in range(1, len(ks)):
        t0, v0 = ks[i - 1]
        t1, v1 = ks[i]
        if t0 <= t <= t1:
            if t1 == t0:
                return v1
            u = (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * u
    return ks[-1][1]


def joint_value(joint, frame, tick_hz):
    """该关节在 `frame` 的**标量值**：("rot", 度) / ("trans", 米) / None（static）。

    ⚠ 只看解析式（匀速语义）。`easing="bezier"` 的烘焙差异由驱动器负责。
    """
    t = float(frame) / float(tick_hz)
    kind = joint.get("kind") or "static"
    if kind == "static":
        return None
    if kind == "spin":
        deg = 360.0 * t / float(joint["period_s"]) + float(joint.get("phase_deg") or 0.0)
        return ("rot", deg)
    if kind == "swing":
        ph = math.radians(float(joint.get("phase_deg") or 0.0))
        a = 2.0 * math.pi * t / float(joint["period_s"])
        return ("rot", float(joint["amp_deg"]) * math.sin(a + ph))
    if kind == "prismatic":
        a = 2.0 * math.pi * t / float(joint["period_s"])
        return ("trans", float(joint["amp_m"]) * math.sin(a))
    if kind == "ease":
        v = _ease_value(joint["keys"], t, joint.get("unit") or "deg")
        return ("rot", v) if (joint.get("unit") or "deg") == "deg" else ("trans", v)
    raise AssemblyError("未知关节类型 %r" % (kind,))


def joint_delta(joint, frame, tick_hz):
    """该关节在 `frame` 的**刚体变换**（绕/沿自身枢轴）；static 返回单位阵。"""
    v = joint_value(joint, frame, tick_hz)
    if v is None:
        return IDENT
    mode, val = v
    if mode == "rot":
        return mat_from_axis_angle(joint.get("axis") or "z", val)
    ax = _axis_unit(joint.get("axis") or "z")
    return mat_from_trans((ax[0] * val, ax[1] * val, ax[2] * val))


# ─────────────────────────────────────────────────────────── 层级求解
def topo_order(norm_links):
    """父在前的 link 顺序（spec 已保证无环，这里仍做一次性防御）。"""
    by = {l["name"]: l for l in norm_links}
    out, done = [], set()
    for l in norm_links:
        chain, cur = [], l
        while cur is not None and cur["name"] not in done:
            chain.append(cur)
            cur = by[cur["parent"]] if cur["parent"] else None
        for c in reversed(chain):
            if c["name"] not in done:
                done.add(c["name"])
                out.append(c)
    return out


def local_offset(link, by_name):
    """该 link 相对父 link 的**局部平移**（Blender `ob.location` 口径 = anchor − anchor(parent)）。

    与 `link_world_mats` 的递归式**等价**（rest 全为纯平移）：这是给
    "要在 Blender 里真的建父子 EMPTY"那条路用的。
    """
    p = link.get("parent")
    if p is None:
        return tuple(float(v) for v in link["anchor"])
    pa = by_name[p]["anchor"]
    return tuple(float(link["anchor"][i]) - float(pa[i]) for i in range(3))


def link_world_mats(norm_spec, frame, tick_hz=None):
    """{link 名 → 4×4 世界矩阵}（给定帧）。

    `norm_spec` 必须是 `spec.normalize_spec` 的返回值（或等价结构）。
    """
    links = norm_spec["links"]
    hz = float(tick_hz if tick_hz is not None else norm_spec["scene"]["fps"])
    if hz <= 0:
        raise AssemblyError("tick_hz 须 > 0，实为 %r" % (hz,))
    by = {l["name"]: l for l in links}
    jt = {j["link"]: j for j in (norm_spec.get("joints") or [])}
    out = {}
    for l in topo_order(links):
        name = l["name"]
        j = jt.get(name)
        J = joint_delta(j, frame, hz) if j else IDENT
        p = l.get("parent")
        if p is None:
            m = mat_mul(rest_mat(l), J)
        else:
            if p not in out:
                raise AssemblyError("link %r 的父 %r 尚未求解（层级序有误）" % (name, p))
            delta_p = mat_mul(out[p], mat_inv_rigid(rest_mat(by[p])))
            m = mat_mul(mat_mul(delta_p, rest_mat(l)), J)
        out[name] = m
    return out


def part_world_mat(link_mat, part):
    """件的世界矩阵 = M(link) · T(pos) · R_euler_XYZ(rot_deg)。

    ⚠ 局部网格**不含** spec 未声明的偏移：`pos` 就是件的原点（相对 link 锚点）。
    """
    m = mat_mul(link_mat, mat_from_trans(part.get("pos") or (0.0, 0.0, 0.0)))
    e = part.get("rot_deg") or (0.0, 0.0, 0.0)
    rx, ry, rz = (math.radians(float(e[0])), math.radians(float(e[1])),
                  math.radians(float(e[2])))
    # 与 Blender Euler('XYZ') 同序：R = Rz · Ry · Rx
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = ((1, 0, 0, 0), (0, cx, -sx, 0), (0, sx, cx, 0), (0, 0, 0, 1))
    Ry = ((cy, 0, sy, 0), (0, 1, 0, 0), (-sy, 0, cy, 0), (0, 0, 0, 1))
    Rz = ((cz, -sz, 0, 0), (sz, cz, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
    return mat_mul(mat_mul(m, mat_mul(Rz, mat_mul(Ry, Rx))), IDENT)


def frames_of(norm_spec):
    """该 spec 的 tick 数 = frame_end − frame_start + 1（spec 的帧区间是闭区间）。"""
    sc = norm_spec["scene"]
    return int(sc["frame_end"]) - int(sc["frame_start"]) + 1


def motion_label(norm_spec):
    """从关节类型**派生** scene.motion 标签（不编造：只看声明里有什么）。"""
    kinds = {j.get("kind") or "static" for j in (norm_spec.get("joints") or [])}
    if kinds & {"spin", "swing", "prismatic"}:
        if "ease" in kinds:
            return "mixed"
        return "periodic_rotation" if "spin" in kinds else "periodic_motion"
    if "ease" in kinds:
        return "nonperiodic_eased"
    return "static"
