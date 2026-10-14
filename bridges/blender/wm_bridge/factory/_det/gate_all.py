# -*- coding: utf-8 -*-
"""gate_all · **统一验收入口**：一次跑完本产线的所有门，给**单一 VERDICT**。

跑法（在本桥根目录，用带 numpy/PIL 的 Python）：
    python -X utf8 wm_bridge/factory/_det/gate_all.py
    WM_SVG_IOU_DIR=<含 model_flat.svg 与 model_render.png 的目录> \
        python -X utf8 wm_bridge/factory/_det/gate_all.py

为什么需要它
------------
四条产线的门本来就是**独立可跑**的（各自带 `VERDICT=`），但调用方式各不相同：
有的不收参数，有的要 `WM_LINGSHU_ROOT`，`gate_svg_iou` 还要一份"Blender 跑出来的产物目录"。
结果就是**没人会去逐个跑**——而"没跑过"与"没通过"在观感上是一样的。
本入口把这些收成一条命令，并遵守两条：

1. **需要外部产物的门（`svg_iou`）在拿不到产物时标 `SKIP`，绝不算通过**
   ——`SKIP` 与 `PASS` 在汇总里分开计数，最后的 `VERDICT` 也把 SKIP 数写清楚。
2. 子进程一律显式 `encoding="utf-8", errors="replace"`
   （本仓 #455 纪律：非 UTF-8 locale 下不加 encoding 会静默拿到 `None`）。
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FACTORY = os.path.join(ROOT, "wm_bridge", "factory")

#: `(名字, 脚本相对路径, 是否需要外部产物)`
GATES = (
    ("gait_contact", os.path.join(FACTORY, "anim", "_det", "gate_gait_contact.py"), False),
    ("crease_adaptive", os.path.join(FACTORY, "vec", "_det", "gate_crease_adaptive.py"), False),
    ("physics", os.path.join(FACTORY, "anim", "_det", "gate_physics.py"), False),
    ("svg_iou", os.path.join(FACTORY, "vec", "_det", "gate_svg_iou.py"), True),
)


def _run(script, extra=()):
    p = subprocess.run([sys.executable, "-X", "utf8", script, *extra],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    verdict = "NO-VERDICT"
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith("VERDICT="):
            verdict = s.split("=", 1)[1].strip()
    #: 摘要取**判定行**本身，不取末行 —— 有的门在 `VERDICT=` 之后还会打 `raw: …`
    #: （本入口第一版就显示成了 `raw: …`，等于把最要紧的那一格填成了路径）。
    detail = ""
    for ln in out.splitlines():
        if ln.strip().startswith("通过 ") or "通过 " in ln and "失败" in ln:
            detail = ln.strip()
    if not detail:
        detail = "exit=%d" % p.returncode
    return p.returncode, verdict, detail


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    svg_dir = None
    for i, a in enumerate(argv):
        if a == "--svg-iou-dir" and i + 1 < len(argv):
            svg_dir = argv[i + 1]
    svg_dir = svg_dir or os.environ.get("WM_SVG_IOU_DIR")
    rows, n_pass, n_fail, n_skip = [], 0, 0, 0
    for name, script, needs_art in GATES:
        if needs_art:
            ok_art = bool(svg_dir) and os.path.isfile(
                os.path.join(svg_dir or "", "model_flat.svg")) and os.path.isfile(
                os.path.join(svg_dir or "", "model_render.png"))
            if not ok_art:
                #: 如实标 SKIP —— **不算通过**（拿不到 Blender 产物就不假装验过）
                rows.append((name, "SKIP", "需要外部产物：请给 WM_SVG_IOU_DIR 或 --svg-iou-dir"))
                n_skip += 1
                print("  %-4s %-18s %-10s %s" % ("SKIP", name, "SKIP",
                      "拿不到 SVG/渲染产物（设 WM_SVG_IOU_DIR）——**不计入通过**"))
                continue
            code, verdict, detail = _run(script, [svg_dir])
        else:
            code, verdict, detail = _run(script)
        ok = (verdict == "PASS")
        n_pass += 1 if ok else 0
        n_fail += 0 if ok else 1
        rows.append((name, verdict, detail))
        print("  %-4s %-18s %-10s %s" % ("PASS" if ok else "FAIL", name, verdict, detail))
    print("-" * 78)
    print("通过 %d / 失败 %d / 跳过 %d（共 %d 门）"
          % (n_pass, n_fail, n_skip, len(GATES)))
    print("VERDICT=%s" % ("PASS" if (n_fail == 0 and n_skip == 0)
                          else ("PASS-WITH-SKIP" if n_fail == 0 else "FAIL")))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
