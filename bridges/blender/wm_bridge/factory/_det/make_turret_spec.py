# -*- coding: utf-8 -*-
"""_det/make_turret_spec.py · 从既有资产**反向写**出基准 spec（纯标准库 · 诊断/授权工具）

输入：`hex-model/build_models.py::build_turret`（逐行读出来的参数，见下方 LEGACY 表）
输出：`wm_bridge/factory/geo/specs/turret.spec.json`（`wmfactory.spec/1`）

★ 唯一的换算：**把"烘进网格的局部偏移 `off`"折进 `pos`**
    legacy:  mesh = off + 图元（图元以原点为中心）；object.location = loc；object.rot = R
    spec :  mesh = 图元（以原点为中心）；               object.pos   = loc + R·off
  两者**世界顶点完全相同**，且 `geom.part_from_blender` 的 `pos`
  （= 世界包围盒中心 − 锚点 = `world_trans + R_world·local_center`）**逐位相同** ——
  因为 `off` 与 `local_center` 在那一式里正好抵消。这条代数已在报告里给出。

跑法：python3 -X utf8 wm_bridge/factory/_det/make_turret_spec.py
"""
from __future__ import annotations

import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_DIR = os.path.abspath(os.path.join(HERE, os.pardir, "geo", "specs"))
OUT = os.path.join(SPEC_DIR, "turret.spec.json")

# ── 既有资产的材料（照抄 build_models.py::build_turret 的 mat_get 参数）
MATS = {
    "dark":  {"role": "metal", "color": [0.07, 0.075, 0.085], "metallic": 1.0,
              "roughness": 0.4, "material_name": "M_dark"},
    "steel": {"role": "metal", "color": [0.18, 0.19, 0.21], "metallic": 1.0,
              "roughness": 0.45, "material_name": "M_steel"},
    "brass": {"role": "metal", "color": [0.55, 0.36, 0.13], "metallic": 1.0,
              "roughness": 0.28, "material_name": "M_brass"},
    "glow":  {"role": "emissive", "color": [0.9, 0.5, 0.06], "metallic": 0.0,
              "roughness": 0.35, "emission": [1.0, 0.5, 0.06],
              "emission_strength": 5.0, "material_name": "M_glow"},
}


def euler_apply(rot_deg, v):
    """R = Rz·Ry·Rx（Blender Euler 'XYZ'）作用在点上。"""
    rx, ry, rz = (math.radians(float(x)) for x in rot_deg)
    x, y, z = (float(v[0]), float(v[1]), float(v[2]))
    # Rx
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    # Ry
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    # Rz
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
    return (x, y, z)


def part(name, kind, size, loc=(0, 0, 0), rot=(0, 0, 0), off=(0, 0, 0),
         mat="steel", **kw):
    """一条 part：`pos = loc + R(rot)·off`（见模块 docstring 的折叠口径）。"""
    d = euler_apply(rot, off)
    pos = [round(float(loc[i]) + float(d[i]), 10) for i in range(3)]
    out = {"name": name, "kind": kind,
           "size": [round(float(s), 10) for s in size],
           "pos": pos,
           "rot_deg": [round(float(r), 10) for r in rot],
           "material": dict(MATS[mat])}
    out["material_name"] = out["material"].pop("material_name")
    out.update(kw)
    return out


def build():
    links = []
    links.append({"name": "TURRET_root", "anchor": [0, 0, 0], "parent": None,
                  "parts": [
                      part("TURRET_base", "cylinder", (1.44, 1.44, 0.20),
                           off=(0, 0, 0.10), mat="dark", segments=8, bevel=0.016),
                      part("TURRET_ring", "cylinder", (1.20, 1.20, 0.07),
                           off=(0, 0, 0.235), mat="brass", segments=32, bevel=0.008),
                  ]})

    yaw_parts = [
        part("TURRET_body", "cylinder", (0.92, 0.92, 0.42), off=(0, 0, 0.21),
             mat="dark", segments=10, bevel=0.018),
        part("TURRET_cap", "cylinder", (0.68, 0.68, 0.10), off=(0, 0, 0.47),
             mat="steel", segments=10, bevel=0.012),
    ]
    for i in range(4):
        a = math.radians(45 + i * 90)
        yaw_parts.append(part(
            "TURRET_barrel_%d" % i, "cylinder", (0.09, 0.62, 0.09),
            loc=(0, 0, 0.30), rot=(0, 0, -math.degrees(a)), axis="y",
            off=(0.16 * math.sin(a), 0.30 * math.cos(a), 0),
            mat="steel", segments=12, bevel=0.005))
        yaw_parts.append(part(
            "TURRET_muzzle_%d" % i, "cylinder", (0.124, 0.05, 0.124),
            loc=(0.16 * math.sin(a), 0.61 * math.cos(a), 0.30),
            rot=(0, 0, -math.degrees(a)), axis="y", mat="brass", segments=12))
    yaw_parts += [
        part("TURRET_counter", "box", (0.5, 0.36, 0.24), off=(0, -0.40, 0.22),
             mat="steel", bevel=0.012),
        part("TURRET_mast", "cylinder", (0.07, 0.07, 0.44), off=(0, -0.40, 0.50),
             mat="steel", segments=10),
        part("TURRET_dish", "cylinder", (0.68, 0.68, 0.035), off=(0, -0.40, 0.72),
             mat="steel", segments=28, bevel=0.006),
        part("TURRET_hub", "sphere", (0.14, 0.14, 0.14), loc=(0, -0.40, 0.72),
             mat="brass", segments=18, segments_v=10),
    ]
    for i in range(3):
        a = math.radians(i * 120)
        yaw_parts.append(part(
            "TURRET_lamp_%d" % i, "sphere", (0.09, 0.09, 0.09),
            loc=(0.34 * math.sin(a), 0.34 * math.cos(a), 0.45),
            mat="glow", segments=18, segments_v=10))
    links.append({"name": "TURRET_yaw", "anchor": [0, 0, 0.27],
                  "parent": "TURRET_root", "parts": yaw_parts})

    for i in range(4):
        a = math.radians(45 + i * 90)
        links.append({"name": "TURRET_tip_%d" % i,
                      "anchor": [round(0.16 * math.sin(a), 10),
                                 round(0.61 * math.cos(a), 10), 0.57],
                      "parent": "TURRET_yaw", "parts": []})

    joints = [
        # ★ 运动面**实测真值**：既有 wmtest_models.blend 的 TURRET_yawAction
        #   = 25 键 / 步长 6 / BEZIER / AUTO_CLAMPED ⇒ 不是匀速（见报告逐 tick 读数）。
        #   3 圈 / 144 帧 / 24fps = 6 s ⇒ period_s = 2.0
        {"link": "TURRET_yaw", "kind": "spin", "axis": "z", "period_s": 2.0,
         "easing": "bezier"},
    ]
    return {
        "schema": "wmfactory.spec/1",
        "name": "turret",
        "scene": {"fps": 24, "frame_start": 0, "frame_end": 143,
                  "world_size": 50.0, "units": "m", "seed": 0},
        "links": links,
        "joints": joints,
        "notes": ("hex-model/build_models.py::build_turret 的 spec 化基准。"
                  "pos 口径 = loc + R(rot)·off（把烘进网格的局部偏移折进件原点）；"
                  "easing=bezier 是**实测**（wmtest_models.blend 的 TURRET_yawAction："
                  "25 键 / 步长 6 / BEZIER / AUTO_CLAMPED），不是默认值。"),
    }


def main():
    spec = build()
    os.makedirs(SPEC_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(spec, f, ensure_ascii=False, sort_keys=True, indent=1)
        f.write("\n")
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, os.pardir, os.pardir,
                                                    os.pardir)))
    from wm_bridge.factory.geo import spec as S
    n = S.normalize_spec(spec)
    print("MAKETURRET " + json.dumps(
        {"out": OUT, "summary": S.spec_summary(spec),
         "bytes": os.path.getsize(OUT)}, ensure_ascii=False))


main()
