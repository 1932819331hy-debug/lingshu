# -*- coding: utf-8 -*-
"""_det/run_produce.py · **带内核注入的引导壳**（诊断/运行用）

为什么需要它：`runtime/geo.py` 的公开名（`solid`/`bevel`/`principled`/…）由 KERNEL_BOOTSTRAP
注入到**被执行的脚本**的 globals —— import 进来的模块看不见它们。所以由这个**被执行的脚本**
把注入名挑出来，作为 `rt` 传给 `bpy_produce.main()`。

用法（在 Blender 内）：
    blender_rt_headless(script_file=".../_det/run_produce.py",
                        args="--spec <spec> --out <dump.json> [--bake linear|bezier]")
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir, os.pardir))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

RT_NAMES = ("solid", "bevel", "principled", "shade_auto", "recalc_normals",
            "collection", "own", "boolean")
RT = {k: v for k, v in ((n, globals().get(n)) for n in RT_NAMES) if callable(v)}

_argv = list(sys.argv[1:])
if "--" in _argv:
    _argv = _argv[_argv.index("--") + 1:]
_argv = [a for a in _argv if not a.startswith("--dsh")]

_mod = importlib.import_module("wm_bridge.factory.bpy_produce")
print("RUNPRODUCE_RT " + ",".join(sorted(RT)))
raise SystemExit(_mod.main(_argv, rt=RT))
