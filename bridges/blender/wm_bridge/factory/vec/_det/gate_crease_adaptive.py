# -*- coding: utf-8 -*-
"""gate_crease_adaptive · **折角阈值自适应门**（纯标准库 · 零 bpy）

为什么需要
----------
`bpy_vec.DrawObject.edge_roles()` 原来把折角阈值**钉死 25°**（`cos_crease=0.9063`）。
后果（与上游 `mesh_svg.adaptive_crease_deg` 修掉的是**同一类**缺陷）：
面数越高、够 25° 的小棱越多 ⇒ 高面数模型中部糊成"线麻"，而插画要的是**结构线**。

本门把三条性质焊死：
  G1 低面数（≤ 基准 2000 面）阈值**逐位等于** 25° ⇒ **观感不变**（回退即红）；
  G2 高面数阈值**确实上抬**且**有封顶**；
  G3 **锐利结构在高面数下仍被保留**（90° 硬边不会被自适应丢掉）——
     这条是防"自适应过头把结构也删了"的反向护栏。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from wm_bridge.factory.vec import prims as P            # noqa: E402
from wm_bridge.factory.vec.bpy_vec import (             # noqa: E402
    DrawObject, adaptive_crease_deg, CREASE_REF_FACES, CREASE_BASE_DEG, CREASE_CAP_DEG)

_FAILS = []
_N = [0]


def check(tag, ok, detail):
    _N[0] += 1
    print("  %-4s %-56s %s" % ("PASS" if ok else "FAIL", tag, detail))
    if not ok:
        _FAILS.append(tag)


def _sheet(nx, ny):
    vs = []
    for i in range(nx + 1):
        for j in range(ny + 1):
            vs.append((float(i), float(j), 1.0 if i % 2 == 0 else 0.0))
    vid = lambda i, j: i * (ny + 1) + j
    fs = [(vid(i, j), vid(i + 1, j), vid(i + 1, j + 1), vid(i, j + 1))
          for i in range(nx) for j in range(ny)]
    return P.Mesh(vs, fs, "sheet")


def _corrugated(n, amp=0.45):
    """平滑波纹面：相邻面恒成 **~60°** —— 高面数时"每个小棱都够 25°"的线麻来源。"""
    vs = []
    for i in range(n + 1):
        z = 0.0 if i % 2 == 0 else amp
        vs.append((float(i), 0.0, z))
        vs.append((float(i), 1.0, z))
    fs = [(2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2) for i in range(n)]
    return P.Mesh(vs, fs, "corr%d" % n)


def _crease(ob, cos_crease):
    return sum(1 for e in ob.edge_roles(ob.mesh.verts, cos_crease=cos_crease)
               if e[2] == "crease")


def main():
    for n in (1, 100, CREASE_REF_FACES):
        check("G1 低面数阈值 == 基准 %g°（观感不变）" % CREASE_BASE_DEG,
              adaptive_crease_deg(n) == CREASE_BASE_DEG,
              "n=%-6d → %.1f°" % (n, adaptive_crease_deg(n)))
    hi = adaptive_crease_deg(16000)
    check("G2 高面数阈值上抬且有封顶",
          hi > CREASE_BASE_DEG and adaptive_crease_deg(10 ** 9) <= CREASE_CAP_DEG,
          "16000 面 → %.1f°；封顶 %.1f°" % (hi, CREASE_CAP_DEG))

    #: G3 锐利（90°）结构在高面数下必须保住
    ob = DrawObject("t", _sheet(126, 126))
    cf = _crease(ob, math.cos(math.radians(CREASE_BASE_DEG)))
    ca = _crease(ob, None)
    check("G3 高面数下锐利结构仍保留", ca > 0,
          "%d 面：固定 %d 条 → 自适应 %d 条（阈值 %.1f°）"
          % (len(ob.mesh.faces), cf, ca, adaptive_crease_deg(len(ob.mesh.faces))))

    #: G4 回退即红：**平滑**高面数网格上，钉死 25° 会糊成线麻，自适应把它清掉。
    #: ⚠ 本判据第一版用的是 **90° 锐利网格** ⇒ 自适应在那上面**本来就不减**
    #:   （60.9° 阈值仍低于 90°）⇒ 断言虽然通过，但**结论是误导的**。
    #:   换成 ~60° 的波纹面才是这条性质的真实载体。
    ob2 = DrawObject("t2", _corrugated(16000))
    rigid = _crease(ob2, math.cos(math.radians(CREASE_BASE_DEG)))
    adap = _crease(ob2, None)
    check("G4 回退即红：钉死 25° 则平滑高面数必糊", rigid > 10000 and adap == 0,
          "16000 面波纹：钉死 25° → %d 条；自适应 → %d 条" % (rigid, adap))

    print("-" * 78)
    print("通过 %d / 失败 %d" % (_N[0] - len(_FAILS), len(_FAILS)))
    print("VERDICT=%s" % ("PASS" if not _FAILS else "FAIL"))
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
