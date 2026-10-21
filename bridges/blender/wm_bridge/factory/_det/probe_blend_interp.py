# -*- coding: utf-8 -*-
"""_det/probe_blend_interp.py · 从既有 .blend **读**关键帧插值类型（诊断用）

Lead 要求：`keyframe_point.interpolation` 读出来，别猜。

Blender 5.2 踩坑：`Action.fcurves` 已废（layered/slotted action）⇒ 走
`action.layers[*].strips[*].channelbags[*].fcurves`。本探针把两条路都试一遍并如实回报。
"""
import json
import os

import bpy

BLEND = r"<外部产物根>\wmtest\wmtest_models.blend"

out = {"blend": BLEND, "exists": os.path.exists(BLEND)}
try:
    bpy.ops.wm.open_mainfile(filepath=BLEND)
    out["opened"] = True
except Exception as exc:                                        # noqa: BLE001
    out["opened"] = "ERR:%s: %s" % (type(exc).__name__, exc)

out["objects"] = sorted(o.name for o in bpy.data.objects)
yaw = bpy.data.objects.get("TURRET_yaw")
out["has_yaw"] = yaw is not None


def _fcurves_via_layers(act):
    """Blender 5.2 layered action：layers → strips → channelbags → fcurves。"""
    got = []
    for lay in getattr(act, "layers", []):
        for strip in getattr(lay, "strips", []):
            for cb in getattr(strip, "channelbags", []):
                got.extend(list(cb.fcurves))
    return got


if yaw is not None:
    ad = yaw.animation_data
    out["has_anim"] = ad is not None
    act = getattr(ad, "action", None) if ad else None
    out["action"] = getattr(act, "name", None)
    out["act_attrs"] = sorted(a for a in dir(act)
                              if "fcurve" in a.lower() or "slot" in a.lower()
                              or "layer" in a.lower()) if act else None
    out["n_layers"] = len(getattr(act, "layers", [])) if act else None
    out["n_slots"] = len(getattr(act, "slots", [])) if act else None

    fcs = _fcurves_via_layers(act) if act else []
    out["n_fcurves_layered"] = len(fcs)
    detail = []
    for fc in fcs:
        kps = list(fc.keyframe_points)
        detail.append({
            "data_path": fc.data_path,
            "array_index": fc.array_index,
            "n_keys": len(kps),
            "interps": sorted({kp.interpolation for kp in kps}),
            "handles": sorted({kp.handle_left_type for kp in kps}),
            "frames": [round(float(kp.co[0]), 6) for kp in kps][:8],
            "values": [round(float(kp.co[1]), 6) for kp in kps][:8],
        })
    out["fcurves"] = detail

    # 「猜」的对照：从偏好设置看默认新建插值类型
    try:
        out["pref_default_interp"] = \
            bpy.context.preferences.edit.keyframe_new_interpolation_type
    except Exception as exc:                                    # noqa: BLE001
        out["pref_default_interp"] = "ERR:%s" % type(exc).__name__

    # 实测 tick 1/2/3 的 yaw 角（与既有 oracle 对拍的那三个数）
    import math
    sc = bpy.context.scene
    ang = {}
    for t in (0, 1, 2, 3, 6):
        sc.frame_set(t)
        bpy.context.view_layer.update()
        z = yaw.rotation_euler.z
        ang[str(t)] = round(math.degrees(math.atan2(math.sin(z), math.cos(z))), 6)
    out["yaw_deg_at"] = ang

print("PROBEBI " + json.dumps(out, ensure_ascii=False))
