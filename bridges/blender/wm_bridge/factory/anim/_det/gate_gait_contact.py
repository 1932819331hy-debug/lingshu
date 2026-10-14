# -*- coding: utf-8 -*-
"""gate_gait_contact · **足端接触门**（六足步态贴合现实的判据 · 纯标准库 · 零 bpy）

为什么需要这道门
----------------
本桥的 hero 六足（`anim/demo.py`）在改动前实测：**足端最低 z = −0.0156 m ⇒ 扎进地面
1.6 cm**，且"支撑相"其实是 **−0.01 ~ +0.01 上下浮**，不是踩住。

根因（解析可证，不是调参问题）：
    `thigh` 绕 **z** 转 ⇒ 足端 z **不变**（纯前后摆，正确）；
    `shin` 绕 **y** 折 ⇒ 足端 z 的贡献是 `L·(1 − cos θ)`，**恒 ≥ 0**
      ⇒ 折膝**只能把脚抬高、不能压更低**；
    而 `spine` 的机身起伏原来是**对称正弦**（±45 mm）
      ⇒ 机身上升的那一半**无法**用屈膝补偿，脚被生生拽离地面；
        下降的那一半则把脚压进地面。
⇒ 正确形态是：**机身只往下沉（单边），且由膝关节逐帧精确吸收**（真机正是靠腿的屈伸）。
    cos θ = 1 + (b(t) − want) / L     （b = 机身高低，want = 该帧足端目标高度）

本门把这条**焊死**：任何让脚穿地、或让支撑脚不再贴地的改动都会判红。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from wm_bridge.factory.anim import demo as D          # noqa: E402
from wm_bridge.factory.anim import eval as E          # noqa: E402

#: 容许的接触误差（米）—— 逐帧采样后实测 0.0000；留 1 mm 给浮点/插值
TOL_M = 1e-3
#: 摆动相最少抬脚高度（米）—— 实测 0.090
MIN_LIFT_M = 0.030

_FAILS = []
_N = [0]                       #: check() 调用次数（避免手写总数写错）


def check(tag, ok, detail):
    _N[0] += 1
    print("  %-4s %-52s %s" % ("PASS" if ok else "FAIL", tag, detail))
    if not ok:
        _FAILS.append(tag)


def _tip(pose, name, shin_len):
    p, q = pose[name]["pos"], pose[name]["quat"]
    o = E.quat_rotate(q, (0.0, 0.0, -shin_len))
    return (p[0] + o[0], p[1] + o[1], p[2] + o[2])


def main():
    spec = D.hero_walker_spec()
    n = spec["scene"]["frame_end"] + 1
    legs = [(s, i) for s in ("l", "r") for i in ("1", "2", "3")]
    zs = {s + i: [] for s, i in legs}
    for f in range(n):
        pose = E.eval_at(spec, f)
        for s, i in legs:
            zs[s + i].append(_tip(pose, "shin_%s%s" % (s, i), D._SHIN_L)[2])

    lo = min(min(v) for v in zs.values())
    check("G1 足端全程不穿地", lo >= -TOL_M, "最低 z = %+.5f m（阈值 %.3f）" % (lo, -TOL_M))
    check("G2 摆动相确实抬脚",
          max(max(v) for v in zs.values()) >= MIN_LIFT_M,
          "最高 z = %+.4f m（阈值 %.3f）" % (max(max(v) for v in zs.values()), MIN_LIFT_M))

    #: 支撑期（该腿 φ∈[0.25,0.75]）的 z 波动 —— 越小越"钉在地上"
    worst = 0.0
    for s, i in legs:
        phi0 = 0.0 if (s, {"1": "f", "2": "m", "3": "b"}[i]) in D._PHASE0 else 0.5
        st = [zs[s + i][f] for f in range(n)
              if 0.25 <= ((f / n) + phi0) % 1.0 <= 0.75]
        worst = max(worst, max(st) - min(st) if st else 9.9)
    check("G3 支撑相足端贴地不浮（波动 < 2 mm）", worst < 2e-3,
          "最差支撑期波动 = %.4f m" % worst)

    #: 机身起伏必须**单边向下**（对称正弦会让"上升"那一半无法补偿）
    bob = [D._bob_m(D.GAIT_PERIOD_S * f / n) for f in range(n + 1)]
    check("G4 机身起伏单边向下", max(bob) <= 1e-9 and min(bob) < -0.01,
          "bob ∈ [%+.4f, %+.4f]" % (min(bob), max(bob)))

    #: 步态相位：交替三角步 = 两组相差 180°
    ph = {j["link"]: j.get("phase_deg", 0.0)
          for j in spec["joints"] if j["link"].startswith("thigh")}
    a = sum(1 for v in ph.values() if abs(v) < 1e-9)
    check("G5 交替三角步 3+3", a == 3 and len(ph) == 6, "相位 0 的腿 %d/6" % a)

    #: G6（**回退即红**）：按**改动前**的运动律解析复算，必须**本来就会判红** G1。
    #: 旧律 = 机身对称正弦起伏 + 小腿对称正弦折角（左右取负镜像）。
    #: 这条证明 G1~G4 有判别力，而不是"无论怎么改都通过"。
    old_hip_z = 0.46                                   #: 改动前的髋高（直腿时足端悬空）
    base = old_hip_z - D._THIGH_L * math.sin(math.radians(D._THIGH_DEG)) - D._SHIN_L
    lo_old = 9.9
    for f in range(n):
        t = D.GAIT_PERIOD_S * f / n
        b = 0.045 * math.sin(2.0 * math.pi * t / 1.0)                  #: 对称
        th = math.radians(38.0 * math.sin(math.pi * t + math.pi / 2))  #: 对称 + 90° 滞后
        lo_old = min(lo_old, base + b + D._SHIN_L * (1.0 - math.cos(th)))
    check("G6 回退即红：旧运动律必被判红", lo_old < -TOL_M,
          "旧律最低 z = %+.4f m ⇒ G1 会红（实测改动前 −0.0156）" % lo_old)

    print("-" * 78)
    print("通过 %d / 失败 %d" % (_N[0] - len(_FAILS), len(_FAILS)))
    print("VERDICT=%s" % ("PASS" if not _FAILS else "FAIL"))
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
