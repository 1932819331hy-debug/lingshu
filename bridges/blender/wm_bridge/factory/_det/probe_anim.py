# -*- coding: utf-8 -*-
"""_det/probe_anim.py · 复现原脚本的关键帧烘焙口径（诊断用 · 第 2 版）

第 1 版踩了 Blender 5.2 的 layered action：`Action.fcurves` 已废
⇒ 改**从偏好设置**指定新关键帧的插值类型（`preferences.edit.keyframe_new_interpolation_type`），
不遍历 fcurve（skill §6.5.6 已登记这条）。

问题：既有 `turret/oracle.jsonl` 的 yaw 角在 tick 1/2/3 = 2.0126 / 7.4402 / 15.3678 度，
而"匀速 3 圈 / 144 帧"应是 7.5 / 15.0 / 22.5 度 —— 说明原脚本的 6 帧间隔
默认 BEZIER 插值**不是**匀速。本探针验证：照原样烘焙能否逐位复现那三个数。
"""
import json
import math

import bpy

FRAMES = 144
PROBE_TICKS = (0, 1, 2, 3, 4, 5, 6, 7, 12, 24, 48, 143)


def _fresh_yaw(pref_interp):
    """建一个 yaw 空物体；把"新建关键帧的插值类型"设成 pref_interp 并回读。"""
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    try:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = pref_interp
        got = bpy.context.preferences.edit.keyframe_new_interpolation_type
    except Exception as exc:                                    # noqa: BLE001
        got = "ERR:%s" % type(exc).__name__
    ob = bpy.data.objects.new("P_yaw", None)
    bpy.context.scene.collection.objects.link(ob)
    ob.rotation_mode = "XYZ"
    return ob, got


def _bake(ob, step):
    for f in range(0, FRAMES + 1, step):
        ob.rotation_euler = (0.0, 0.0, 2 * math.pi * (f / FRAMES) * 3.0)
        ob.keyframe_insert("rotation_euler", frame=f)


def _angles(ob):
    sc = bpy.context.scene
    out = {}
    for t in PROBE_TICKS:
        sc.frame_set(t)
        bpy.context.view_layer.update()
        z = ob.rotation_euler.z
        out[str(t)] = round(math.degrees(math.atan2(math.sin(z), math.cos(z))), 6)
    return out


def main():
    out = {}

    ob, got = _fresh_yaw("BEZIER")
    _bake(ob, 6)
    out["legacy_pref_readback"] = got
    out["legacy_bake_deg"] = _angles(ob)          # 应为 2.0126 / 7.4402 / 15.3678

    ob, got = _fresh_yaw("LINEAR")
    _bake(ob, 1)
    out["linear_pref_readback"] = got
    out["linear_bake_deg"] = _angles(ob)          # 应为 7.5 / 15.0 / 22.5

    out["uniform_expect_deg"] = {
        str(t): round(math.degrees(math.atan2(
            math.sin(math.radians(7.5 * t)), math.cos(math.radians(7.5 * t)))), 6)
        for t in PROBE_TICKS}

    # 抽样数：BEZIER 6 帧步长 vs LINEAR 1 帧步长（只报关键帧总数）
    out["n_keys_legacy"] = len(range(0, FRAMES + 1, 6))
    out["n_keys_linear"] = len(range(0, FRAMES + 1, 1))

    print("PROBEANIM " + json.dumps(out, ensure_ascii=False))


main()
