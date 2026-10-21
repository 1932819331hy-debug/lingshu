# -*- coding: utf-8 -*-
"""wm_bridge.factory.anim.demo · **演示级 hero 规格**（纯标准库 · 零 bpy）

两条 spec 都直接喂给 `factory.geo.spec.normalize_spec` 过校验，再交给：
    eval.eval_at            → 逐帧真值
    rig.build_rig           → 骨骼 / 驱动器 / 形态键
    bpy_anim.build_scene    → Blender 场景 + 关键帧 + 渲染

`hero_walker_spec()` —— 六足步行机甲（"scuttle"）
--------------------------------------------------------------------------
17 个 link / 17 根骨头，四种关节 kind 全部用上，三个动画面全在场：

    chassis   root             ease(m, x)      整机横向重心摆动（±70 mm）
    spine     chassis          prismatic(z)    机身起伏（±45 mm，周期 1 s）
    body      spine            ease(deg, z)    机身偏航摆（±6.5°）
    turret    body             spin(z)         ★ 驱动器：雷达盘每 2 s 转一圈
    antenna   body             swing(x)        天线前后甩（±16°，周期 1 s）
    thigh_*   6 条腿           swing(z)        ★ 驱动器：腿在水平面里前后摆（±26°）
    shin_*    6 条腿           swing(y)        ★ 驱动器：膝在竖直面里折（±38°，相位滞后 90°）

    ★ 形态键（spec 冻结契约里没有这个字段 ⇒ 由 rig 层声明，见 rig.build_rig）：
        hull 的 `squash`（squash_z，**驱动器**驱动，周期 1 s）—— 机壳随呼吸压扁/回弹
        dome 的 `swell` （scale，**逐帧烘焙**，周期 0.5 s）—— 驾驶舱盖鼓胀

步态是经典**交替三角步**（alternating tripod）：
    L1 / R2 / L3 同相（phase 0），R1 / L2 / R3 反相（phase 180）
左右腿靠**振幅取负**做镜像（spec 的 `amp_deg` 允许负数）——左右同向摆动，
而不是"同号旋转"导致的一前一后。

循环闭合：所有周期分量都整除 2 s（turret 2.0 / 天线 1.0 / 形态键 1.0 / 摆动 2.0），
`chassis` 与 `body` 的 ease 首末键值相等且 smoothstep 两端导数为 0
⇒ 48 帧（24 fps × 2 s）首尾无缝，GIF 循环不跳。
"""
from __future__ import annotations

import math

from ..geo.spec import SPEC_SCHEMA

__all__ = ["hero_walker_spec", "hero_morphs", "mini_spec", "GAIT_PERIOD_S",
           "HERO_FRAMES", "HERO_FPS", "HERO_SIZE"]

#: 步态周期（秒）—— 所有周期分量的公约数
GAIT_PERIOD_S = 2.0
#: hero 的帧率与帧数。
#: ⚠ 这里有一个**只能取整厘秒**的硬约束（实测踩过）：GIF 的帧延时粒度是 **10 ms**，
#: 而一个步态周期是 `GAIT_PERIOD_S = 2.0 s = 200 cs` ⇒ **帧数必须整除 200**，
#: 否则每帧延时取整后**总时长对不上周期** ⇒ 动画速度失真。
#:   原取值 `48 帧 @ 24 fps`（41.67 ms/帧）：作为**帧序列**是精确的（48/24 = 2.000 s ✓），
#:   但 GIF 只能写 4 cs = 40 ms ⇒ 48×40 = **1.920 s** ⇒ **整段动画快了 4.17%**。
#:   改为 `50 帧 @ 25 fps` ⇒ 200/50 = **4 cs 整除** ⇒ GIF 精确 2.000 s，误差 0。
#: （另一条路是让 GIF 导出器重采样成 50 帧，但那样帧序列与 GIF 两套时间轴，
#:   不如让**唯一的时间源**就是 spec。）
HERO_FPS = 25
HERO_FRAMES = 50
#: hero 的默认渲染画幅（GIF 要求的 ≥480 px）
HERO_SIZE = (560, 560)

# ---------------------------------------------------------------- 机身尺寸（米）
_HIP_X = 0.50          # 髋关节的 |x|
_HIP_Y = {"f": 0.26, "m": 0.0, "b": -0.26}
_THIGH_L = 0.36        # 大腿长
_THIGH_DEG = 32.0      # 大腿下倾角
_SHIN_L = 0.26         # 小腿长
#: 髋关节高度 —— **由"直腿时足端正好落在 z=0"反解**（不是随手取的数）：
#:   足端 z = _HIP_Z − _THIGH_L·sin(_THIGH_DEG) − _SHIN_L
#: 实测原值 0.46 会让直腿足端停在 z=+0.0092（悬空），机身一沉就压到 −0.036。
_HIP_Z = _THIGH_L * math.sin(math.radians(_THIGH_DEG)) + _SHIN_L

_THIGH_AMP = 26.0      # 大腿前后摆幅（度）
_SHIN_AMP = 38.0       # 膝折幅（度）——仅作 shin 的**上界参考**，见 _shin_fold_deg
#: 机身竖直起伏幅度（米）——**单边向下**（不是对称正弦）：见 _bob_m 的说明
_BOB_M = 0.045
#: 摆动相足端抬离地面高度（米）
_LIFT_M = 0.090
_SHIN_LAG = 90.0       # 膝相对大腿的相位滞后（度）—— 抬腿发生在**前摆**窗口里

#: 交替三角步：这些腿相位 0，其余 180
_PHASE0 = {("l", "f"), ("r", "m"), ("l", "b")}

_COL = {
    "hull": (0.72, 0.24, 0.12),        # 橙红机壳
    "hull_dark": (0.30, 0.12, 0.07),
    "plate": (0.26, 0.28, 0.32),       # 深灰金属
    "leg": (0.20, 0.22, 0.26),         # 腿：暗金属
    "joint": (0.82, 0.62, 0.20),       # 关节：黄铜
    "boot": (0.07, 0.07, 0.08),        # 脚垫：橡胶
    "dome": (0.30, 0.62, 0.88),        # 驾驶舱：蓝玻璃
    "tip": (0.15, 0.95, 1.00),         # 天线尖：青发射
    "dish": (0.86, 0.86, 0.90),
    "fin": (0.14, 0.42, 0.30),
}


def _part(name, kind, pos, size, role, color, rot=(0.0, 0.0, 0.0), **kw):
    p = {"name": name, "kind": kind, "pos": [float(x) for x in pos],
         "rot_deg": [float(x) for x in rot], "size": [float(x) for x in size],
         "material": {"role": role, "color": [float(c) for c in color]}}
    for k, v in kw.items():
        if v is None:
            continue
        if k in ("roughness", "metallic"):
            p["material"][k] = float(v)
        else:
            p[k] = v
    return p


def _bob_m(t):
    """机身竖直偏移（米）：**单边向下**，周期 1 s（每步态周期两次），峰谷 −`_BOB_M`。

    为什么不做对称正弦：足端的膝关节贡献是 `L(1 − cos θ)`，**恒 ≥ 0**（折膝只能把脚
    抬高、不能压更低）⇒ 机身**上升**的那一半无法用屈膝补偿，脚会被生生拽离地面；
    只有"机身只往下沉"这一半才可能被膝关节**精确吸收**。真机的机身起伏也正是
    由腿的屈伸吸收的。
    """
    return -_BOB_M * (1.0 - math.cos(2.0 * math.pi * t / 1.0)) / 2.0


def _shin_fold_deg(t, phase_deg, n_float=0.0):
    """该腿小腿在时刻 `t` 的折角（度）—— 由**足端目标高度**反解（不是手摆的正弦）。

    推导：足端 z = `_bob_m(t)` + `_SHIN_L`·(1 − cos θ)   （θ = 折角，正值抬高）
      ⇒ 令足端 z = `want`  ⇒  cos θ = 1 + (b(t) − want) / `_SHIN_L`

    支撑期（该腿相位 φ ∈ [0.25, 0.75]）取 `want = 0` ⇒ **严格贴地**；
    摆动期取 `want = _LIFT_M`·sin(πu) ⇒ 平滑抬起再落回（两端 `want = 0`，与支撑期连续）。
    """
    phi = (t / GAIT_PERIOD_S + phase_deg / 360.0) % 1.0
    if 0.25 <= phi <= 0.75:
        want = 0.0
    else:
        u = (phi - 0.75) / 0.5 if phi > 0.75 else (phi + 0.25) / 0.5
        want = _LIFT_M * math.sin(math.pi * u)
    c = 1.0 + (_bob_m(t) - want) / _SHIN_L
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _bob_keys(frames):
    """机身起伏的 `ease` 关键帧表：**逐帧采样** `_bob_m`。

    ⚠ 为什么必须逐帧（本处实测踩过）：起伏原本每 0.25 s 才一个键，而小腿折角是按
    `_bob_m(t)` **解析**补偿的 ⇒ 两者在键之间不相等（实测最大差 0.47 cm），
    正好表现为"支撑足仍穿地 0.54 cm"。逐帧采样后两者在**每个渲染帧**上严格相等。
    """
    n = max(1, int(frames))
    return [[GAIT_PERIOD_S * j / n, _bob_m(GAIT_PERIOD_S * j / n)] for j in range(n + 1)]


def _shin_keys(phase_deg, frames=HERO_FRAMES):
    """把 `_shin_fold_deg` 采样成 `ease` 关键帧表（秒 → 度）。

    同样**逐帧采样**：关键帧与渲染帧一一对应 ⇒ "支撑足贴地"在**每一帧**上都严格成立，
    而不是只在这条曲线自己的采样点上成立。
    """
    n = max(1, int(frames))
    return [[GAIT_PERIOD_S * j / n, _shin_fold_deg(GAIT_PERIOD_S * j / n, phase_deg)]
            for j in range(n + 1)]


def _leg_links(side, idx, y):
    """一条腿 → (thigh_link, shin_link, knee 世界坐标)。"""
    sgn = 1.0 if side == "r" else -1.0
    th = math.radians(_THIGH_DEG)
    hip = (_HIP_X * sgn, y, _HIP_Z)
    knee_off = (_THIGH_L * math.cos(th), 0.0, -_THIGH_L * math.sin(th))
    knee = (hip[0] + sgn * knee_off[0], y, hip[2] + knee_off[2])
    thigh_center = (0.5 * knee_off[0] * sgn, 0.0, 0.5 * knee_off[2])
    thn = "thigh_%s%s" % (side, idx)
    shn = "shin_%s%s" % (side, idx)
    thigh = {
        "name": thn, "anchor": hip,
        "parts": [
            _part("thigh_body_%s%s" % (side, idx), "box", thigh_center,
                  (_THIGH_L, 0.105, 0.105), "painted_metal", _COL["leg"],
                  rot=(0.0, _THIGH_DEG * sgn, 0.0)),
            _part("hip_joint_%s%s" % (side, idx), "cyl", (0.0, 0.0, 0.0),
                  (0.075, 0.075, 0.10), "metal", _COL["joint"], axis="y"),
        ],
    }
    shin = {
        "name": shn, "parent": thn, "anchor": knee,
        "parts": [
            _part("shin_body_%s%s" % (side, idx), "box", (0.0, 0.0, -_SHIN_L * 0.5),
                  (0.095, 0.095, _SHIN_L), "painted_metal", _COL["leg"]),
            _part("knee_%s%s" % (side, idx), "cyl", (0.0, 0.0, 0.0),
                  (0.065, 0.065, 0.09), "metal", _COL["joint"], axis="y"),
            _part("boot_%s%s" % (side, idx), "box", (0.0, 0.0, -_SHIN_L + 0.018),
                  (0.14, 0.14, 0.05), "rubber", _COL["boot"]),
        ],
    }
    return thigh, shin


def hero_walker_spec(fps=HERO_FPS, frames=HERO_FRAMES, name="hero-walker"):
    """六足步行机甲 spec（可直接过 `spec.normalize_spec`）。"""
    links = []
    joints = []
    period = GAIT_PERIOD_S

    # ---- chassis：整机横向重心摆动（ease，米）
    links.append({"name": "chassis", "anchor": (0.0, 0.0, 0.42), "parts": [
        _part("pelvis", "box", (0.0, 0.0, 0.0), (0.96, 0.60, 0.15),
              "metal", _COL["plate"], roughness=0.35, metallic=0.9),
        _part("pelvis_lip", "box", (0.0, 0.0, -0.10), (0.72, 0.46, 0.07),
              "painted_metal", _COL["hull_dark"]),
    ]})
    joints.append({"link": "chassis", "kind": "ease", "axis": "x", "unit": "m",
                   "keys": [[0.0, 0.0], [period * 0.25, 0.070], [period * 0.5, 0.0],
                            [period * 0.75, -0.070], [period, 0.0]]})

    # ---- spine：机身起伏（prismatic，z）
    links.append({"name": "spine", "parent": "chassis", "anchor": (0.0, 0.0, 0.56), "parts": [
        _part("spine_col", "cyl", (0.0, 0.0, 0.0), (0.16, 0.16, 0.14),
              "metal", _COL["plate"], axis="z", roughness=0.4, metallic=0.85),
    ]})
    #: 机身竖直起伏：**单边向下**的 `ease`（周期 1 s = 每步态周期两次，与真机一致）。
    #: ⚠ 为什么不能再用 `prismatic`（对称正弦）：足端 z 的膝关节贡献是 `L(1−cosθ)`，
    #: **恒 ≥ 0（只能抬高、不能下压）** ⇒ 机身上升时脚会被拽离地、无法用屈膝补偿；
    #: 只有"机身只往下沉"这一半才可能由膝关节**精确吸收**（见 `_shin_fold`）。
    joints.append({"link": "spine", "kind": "ease", "axis": "z", "unit": "m",
                   "keys": _bob_keys(frames)})

    # ---- body：机身偏航摆（ease，度）+ 形态键载体
    links.append({"name": "body", "parent": "spine", "anchor": (0.0, 0.0, 0.66), "parts": [
        _part("hull", "box", (0.0, 0.0, 0.07), (1.02, 0.70, 0.26),
              "painted_metal", _COL["hull"], roughness=0.42),
        _part("hull_belly", "box", (0.0, 0.0, -0.09), (0.86, 0.58, 0.08),
              "metal", _COL["plate"]),
        _part("dome", "sphere", (0.0, -0.14, 0.22), (0.20, 0.20, 0.17),
              "glass", _COL["dome"], roughness=0.08),
        _part("fin_l", "box", (-0.44, 0.16, 0.20), (0.06, 0.30, 0.16),
              "plastic", _COL["fin"], rot=(0.0, -18.0, 0.0)),
        _part("fin_r", "box", (0.44, 0.16, 0.20), (0.06, 0.30, 0.16),
              "plastic", _COL["fin"], rot=(0.0, 18.0, 0.0)),
        _part("stripe_l", "box", (-0.52, 0.0, 0.07), (0.03, 0.66, 0.09),
              "emissive", (0.10, 0.85, 1.00)),
        _part("stripe_r", "box", (0.52, 0.0, 0.07), (0.03, 0.66, 0.09),
              "emissive", (0.10, 0.85, 1.00)),
    ]})
    joints.append({"link": "body", "kind": "ease", "axis": "z", "unit": "deg",
                   "keys": [[0.0, 0.0], [period * 0.25, 6.5], [period * 0.5, 0.0],
                            [period * 0.75, -6.5], [period, 0.0]]})

    # ---- turret：雷达盘（spin，驱动器）
    links.append({"name": "turret", "parent": "body", "anchor": (0.0, 0.06, 0.90), "parts": [
        _part("mast", "cyl", (0.0, 0.0, 0.09), (0.07, 0.07, 0.20),
              "metal", _COL["joint"], axis="z", metallic=0.9, roughness=0.25),
        _part("dish", "box", (0.0, 0.0, 0.24), (0.52, 0.045, 0.20),
              "metal", _COL["dish"], rot=(0.0, 14.0, 0.0), roughness=0.2, metallic=0.8),
        _part("dish_hub", "sphere", (0.0, 0.0, 0.22), (0.07, 0.07, 0.07),
              "emissive", (1.00, 0.42, 0.10)),
    ]})
    joints.append({"link": "turret", "kind": "spin", "axis": "z",
                   "period_s": period, "phase_deg": 0.0})

    # ---- antenna：天线甩动（swing，x）
    links.append({"name": "antenna", "parent": "body", "anchor": (0.34, 0.24, 0.88), "parts": [
        _part("ant_rod", "cyl", (0.0, 0.0, 0.16), (0.028, 0.028, 0.32),
              "metal", _COL["plate"], axis="z"),
        _part("ant_tip", "sphere", (0.0, 0.0, 0.33), (0.06, 0.06, 0.06),
              "emissive", _COL["tip"]),
    ]})
    joints.append({"link": "antenna", "kind": "swing", "axis": "x",
                   "amp_deg": 16.0, "period_s": 1.0, "phase_deg": 0.0})

    # ---- 六条腿
    for side in ("l", "r"):
        for idx, pos in (("1", "f"), ("2", "m"), ("3", "b")):
            y = _HIP_Y[pos]
            thigh, shin = _leg_links(side, idx, y)
            thigh["parent"] = "body"
            links.append(thigh)
            links.append(shin)
            mirror = 1.0 if side == "r" else -1.0
            phase = 0.0 if (side, pos) in _PHASE0 else 180.0
            joints.append({"link": thigh["name"], "kind": "swing", "axis": "z",
                           "amp_deg": _THIGH_AMP * mirror, "period_s": period,
                           "phase_deg": phase})
            #: 膝：由**足端目标高度**反解折角（`_shin_fold_deg`）⇒ 支撑期严格贴地、
            #: 摆动期抬起。⚠ 不再乘 `mirror`：折角**正负两侧都是"抬高"**（`L(1−cosθ)`），
            #: 原来乘 −1 会让左腿的脚往下折，反而更穿地。
            joints.append({"link": shin["name"], "kind": "ease", "axis": "y", "unit": "deg",
                           "keys": _shin_keys(phase, frames)})

    fs = int(fps)
    n = int(frames)
    return {
        "schema": SPEC_SCHEMA,
        "name": name,
        "notes": "anim demo: hexapod scuttle, alternating tripod gait; "
                 "%d frames @ %d fps (%.2fs loop)" % (n, fs, GAIT_PERIOD_S),
        "scene": {"fps": fs, "frame_start": 0, "frame_end": n - 1,
                  "world_size": 20.0, "units": "m"},
        "links": links,
        "joints": joints,
    }


def hero_morphs():
    """hero 的形态键声明（`rig.build_rig(spec, morphs=...)` 的入参）。"""
    return [
        {"part": "hull", "name": "squash", "kind": "squash_z",
         "amp": 0.16, "period_s": 1.0, "phase_deg": 0.0, "driven": True},
        {"part": "dome", "name": "swell", "kind": "scale",
         "amp": 0.14, "period_s": 0.5, "phase_deg": 90.0, "driven": False},
    ]


def mini_spec(name="mini-2link", fps=24):
    """最小对照 spec：两节臂 + 一个绕轴自转的炮塔（gate 里做解析校验用）。"""
    return {
        "schema": SPEC_SCHEMA,
        "name": name,
        "scene": {"fps": fps, "frame_start": 0, "frame_end": 47, "world_size": 10.0},
        "links": [
            {"name": "base", "anchor": (0.0, 0.0, 0.0), "parts": [
                _part("base_box", "box", (0.0, 0.0, 0.05), (0.6, 0.6, 0.1),
                      "metal", _COL["plate"])]},
            {"name": "arm", "parent": "base", "anchor": (0.0, 0.0, 0.5), "parts": [
                _part("arm_box", "box", (0.0, 0.0, 0.25), (0.1, 0.1, 0.5),
                      "painted_metal", _COL["hull"])]},
            {"name": "tip", "parent": "arm", "anchor": (0.0, 0.0, 1.0), "parts": [
                _part("tip_box", "box", (0.0, 0.0, 0.0), (0.18, 0.18, 0.18),
                      "emissive", _COL["tip"])]},
        ],
        "joints": [
            {"link": "arm", "kind": "swing", "axis": "x", "amp_deg": 35.0, "period_s": 2.0},
            {"link": "tip", "kind": "spin", "axis": "z", "period_s": 4.0},
        ],
    }
