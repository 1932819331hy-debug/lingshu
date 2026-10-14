# -*- coding: utf-8 -*-
"""_det/probe_write.py · 探测 Blender(Windows) 能否写 D: 产物目录

这是关键路径决定：WSL 侧 /mnt/d 对我是只读（沙箱），但 Blender 是 Windows 进程。
若它能写，产物就直接落 D:\\DSH\\blender\\out\\wmopt\\geo\\；否则落工作区再设法搬运。
"""
import json
import os

TARGETS = [
    r"D:\DSH\blender\out\wmopt\geo",
    "/mnt/d/DSH/blender/out/wmopt/geo",
]

out = {}
for t in TARGETS:
    try:
        os.makedirs(t, exist_ok=True)
        p = os.path.join(t, "_probe_write.txt")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("blender-write-ok\n")
        with open(p, encoding="utf-8") as f:
            back = f.read().strip()
        out[t] = {"ok": True, "path": p, "readback": back}
    except Exception as exc:                                   # noqa: BLE001
        out[t] = {"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)}

out["cwd"] = os.getcwd()
out["DSH_OUT_env"] = os.environ.get("DSH_OUT")
print("PROBEW " + json.dumps(out, ensure_ascii=False))
