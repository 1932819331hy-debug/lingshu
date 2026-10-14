# -*- coding: utf-8 -*-
"""_det/emit_artifacts.py · 把基准 spec 的三件产物落到**世界模型侧成员能读到的位置**（诊断/交付用）

为什么要在 Blender 里跑：WSL 侧的 `<外部产物根>` 对本会话**只读**（沙箱 workspace-write），
只有 Blender（Windows 进程）能写 `<盘符>:\\…`。这三件本身**不需要 bpy**（纯标准库），
这里只是借 Blender 的写盘能力跑一次 `wm_bridge.cli.main`。

落点（**刻意避开既有 `wmtest/turret/`，不覆盖原始基准**）：
    <外部产物根>\\wmtest\\spec_driven\\turret\\
        dump.json  mesh.obj  mesh.mtl  shapes.json  oracle.jsonl  receipt.json
"""
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir, os.pardir))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

SRC_DUMP = r"<外部产物根>\wmopt\geo\bez\a\dump.json"
DST = r"<外部产物根>\wmtest\spec_driven\turret"

os.makedirs(DST, exist_ok=True)
# 先把 dump 本体抄过去（它是"唯一输入"，消费者要能拿到）
with open(SRC_DUMP, "rb") as f:
    blob = f.read()
with open(os.path.join(DST, "dump.json"), "wb") as f:
    f.write(blob)

cli = importlib.import_module("wm_bridge.cli")
obj = os.path.join(DST, "mesh.obj")
mtl = os.path.join(DST, "mesh.mtl")
shp = os.path.join(DST, "shapes.json")
orc = os.path.join(DST, "oracle.jsonl")
dmp = os.path.join(DST, "dump.json")

rc = {
    "export-obj": cli.main(["export-obj", "--spec", dmp, "--out", obj, "--mtl", mtl]),
    "export-shapes": cli.main(["export-shapes", "--spec", dmp, "--out", shp]),
    "export-oracle": cli.main(["export-oracle", "--spec", dmp, "--out", orc]),
}
sizes = {os.path.basename(p): os.path.getsize(p)
         for p in (dmp, obj, mtl, shp, orc) if os.path.exists(p)}
with open(os.path.join(DST, "receipt.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump({"dst": DST, "src_dump": SRC_DUMP, "rc": rc, "sizes": sizes},
              f, ensure_ascii=False, sort_keys=True, indent=1)
print("EMIT " + json.dumps({"dst": DST, "rc": rc, "sizes": sizes},
                           ensure_ascii=False, separators=(",", ":")))
