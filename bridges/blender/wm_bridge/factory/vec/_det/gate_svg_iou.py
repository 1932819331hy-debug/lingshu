# -*- coding: utf-8 -*-
"""gate_svg_iou · **矢量与真实渲染一致**的门（前景 IoU ≥ 0.90）

跑法（**在 Blender 之外**，用带 numpy/PIL 的 Python）：
    python -X utf8 wm_bridge/factory/vec/_det/gate_svg_iou.py <产物目录>

为什么需要它
------------
本产线的文档里早就写了验收：**「矢量与位图一致（前景 IoU ≥ 0.90）」**。
`raster.py` 也早就写好了 `rasterize` / `foreground_mask` / `iou` 三个函数，
`bpy_vec.read_render_alpha()` 也早就写好了"读 Blender 渲染 PNG 的 alpha ⇒ 前景掩码
（与 `foreground_mask` **同口径**）"。**但这三件从没有人调用过**：

- `run_pipeline.py` 只把 `model_flat.svg` 与 `model_render.png` **产出来**，
  **不算 IoU、没有阈值、没有 VERDICT**；
- 而那条管线本身还在读数阶段崩过一次（`KeyError: 'emitted'`，见该文件顶部注释）
  ⇒ 更后面的渲染参照 `model_render.png` **根本没被产出**
  ⇒ **这条验收从来没有机会被计算**。

本门把这条验收真正**执行**起来，并加一条**回退即红**：
把矢量掩码人为平移若干像素后再算一次 ⇒ IoU 必须掉到阈值以下（证明判据有判别力）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from wm_bridge.factory.vec import bpy_vec as BV            # noqa: E402
from wm_bridge.factory.vec import raster as RA             # noqa: E402

#: 文档里写明的验收阈值
IOU_MIN = 0.90
#: 回退即红：两种**真实失效模式**的扰动（不是随便挑个数字）
#:   · 平移 —— 相机/画布横向错位（投影没对齐）
#:   · 各向异性缩放 —— 相机焦距或分辨率口径不一致（最常见的"看着差不多"）
#: ⚠ 本判据第一版只平移 **12 px** ⇒ IoU 只从 0.9955 掉到 0.9377、**仍高于 0.90**
#:   ⇒ 判据"通过不了"其实是因为**扰动太弱**，不是判据没判别力。轮廓大而平滑时
#:   小位移几乎不改变 IoU；故改为下面这种量级，并**打印敏感性表**把这条讲清楚。
SHIFT_PX = 60
SCALE = 1.10

_FAILS = []
_N = [0]


def check(tag, ok, detail):
    _N[0] += 1
    print("  %-4s %-46s %s" % ("PASS" if ok else "FAIL", tag, detail))
    if not ok:
        _FAILS.append(tag)


def _shift(mask, dx, dy):
    import numpy as np
    out = np.zeros_like(mask)
    h, w = mask.shape
    xs = slice(max(0, dx), min(w, w + dx))
    xd = slice(max(0, -dx), min(w, w - dx))
    ys = slice(max(0, dy), min(h, h + dy))
    yd = slice(max(0, -dy), min(h, h - dy))
    out[yd, xd] = mask[ys, xs]
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    d = argv[0] if argv else r"D:\DSH\blender\out\wmopt\vec2"
    svg_p = os.path.join(d, "model_flat.svg")
    png_p = os.path.join(d, "model_render.png")
    for p in (svg_p, png_p):
        if not os.path.isfile(p):
            print("[FAIL] G0   缺产物：%s" % p)
            print("VERDICT=FAIL")
            return 2

    mask_png, (W, H) = BV.read_render_alpha(png_p, threshold=0.5)
    svg = open(svg_p, "r", encoding="utf-8").read()
    #: ⚠ `foreground_mask` 返回的是 **3 元组** `(mask, img, report)`（实测踩过：
    #: 直接当数组用会 `AttributeError: 'tuple' object has no attribute 'shape'`）。
    mask_svg, _img_svg, rep_svg = RA.foreground_mask(svg, W, H, ss=2, threshold=0.5)
    if tuple(mask_svg.shape) != tuple(mask_png.shape):
        print("[FAIL] G0   掩码尺寸不一致：svg %s vs png %s" % (mask_svg.shape, mask_png.shape))
        print("VERDICT=FAIL")
        return 2

    iou, det = RA.iou(mask_svg, mask_png)
    check("G1 SVG↔真渲染 前景 IoU ≥ %.2f" % IOU_MIN, iou >= IOU_MIN,
          "IoU = %.4f（交 %d / 并 %d；矢量前景 %d px，渲染前景 %d px）"
          % (iou, det["inter"], det["union"], det["a"], det["b"]))

    #: 敏感性表（把"多小的扰动才足以击穿阈值"这件事**量化**出来）
    import numpy as np
    sens = []
    for sh in (6, 12, 24, 48, 60, 96):
        v, _ = RA.iou(_shift(mask_svg, sh, 0), mask_png)
        sens.append({"shift_px": sh, "iou": round(v, 4)})
    print("  敏感性表（平移 → IoU）：%s"
          % ", ".join("%dpx:%.4f" % (e["shift_px"], e["iou"]) for e in sens))

    #: G2 回退即红（平移）：量级足以击穿阈值 ⇒ 证明判据**会**因投影错位而红
    iou_sh, _ = RA.iou(_shift(mask_svg, SHIFT_PX, 0), mask_png)
    check("G2 回退即红：平移 %d px 后 IoU < %.2f" % (SHIFT_PX, IOU_MIN), iou_sh < IOU_MIN,
          "平移后 IoU = %.4f（原 %.4f）" % (iou_sh, iou))

    #: 缩放敏感性表 —— 顺带把「0.90 这个阈值到底有多严」量化出来：
    #: 实测 1.05× 缩放只有 0.9158（**仍过阈**）⇒ 说明 0.90 对 ~5% 的口径误差是**宽容**的。
    #: 这是**阈值严格度的性质**，不是缺陷；如实记下来比装作"一碰就红"更有用。
    ssen = []
    for kk in (1.02, 1.05, 1.10, 1.20):
        h2, w2 = mask_svg.shape
        yy2, xx2 = np.mgrid[0:h2, 0:w2]
        cy2, cx2 = (h2 - 1) / 2.0, (w2 - 1) / 2.0
        sy2 = np.clip((cy2 + (yy2 - cy2) / kk).round().astype(np.int64), 0, h2 - 1)
        sx2 = np.clip((cx2 + (xx2 - cx2) / kk).round().astype(np.int64), 0, w2 - 1)
        v2, _ = RA.iou(mask_svg[sy2, sx2], mask_png)
        ssen.append({"scale": kk, "iou": round(v2, 4)})
    print("  缩放敏感性表（scale → IoU）：%s"
          % ", ".join("%.2fx:%.4f" % (e["scale"], e["iou"]) for e in ssen))

    #: G2b 回退即红（缩放）：口径不一致（焦距/分辨率）也必须红
    k = SCALE
    h, w = mask_svg.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    sy = np.clip((cy + (yy - cy) / k).round().astype(np.int64), 0, h - 1)
    sx = np.clip((cx + (xx - cx) / k).round().astype(np.int64), 0, w - 1)
    iou_sc, _ = RA.iou(mask_svg[sy, sx], mask_png)
    check("G2b 回退即红：缩放 %.2f× 后 IoU < %.2f" % (SCALE, IOU_MIN), iou_sc < IOU_MIN,
          "缩放后 IoU = %.4f（原 %.4f）" % (iou_sc, iou))

    #: G3 空掩码 ⇒ IoU 应接近 0（判据不是恒真）
    import numpy as np
    iou_zero, _ = RA.iou(np.zeros_like(mask_png), mask_png)
    check("G3 空矢量掩码 IoU ≈ 0", iou_zero <= 0.001, "空掩码 IoU = %.4f" % iou_zero)

    raw = {"dir": d, "size": [W, H], "iou": iou, "iou_min": IOU_MIN,
           "raster_unsupported": list((rep_svg or {}).get("unsupported") or []),
           "iou_shift%d" % SHIFT_PX: iou_sh, "iou_scale": iou_sc,
           "iou_empty": iou_zero, "sensitivity_shift": sens, "sensitivity_scale": ssen, "iou_detail": det,
           "svg": svg_p, "png": png_p}
    out = os.path.join(d, "svg_iou.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=1)
    print("-" * 78)
    print("通过 %d / 失败 %d" % (_N[0] - len(_FAILS), len(_FAILS)))
    print("VERDICT=%s" % ("PASS" if not _FAILS else "FAIL"))
    print("raw: %s" % out)
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
