# -*- coding: utf-8 -*-
"""#304 守卫：`remember` 的事件时间锚必须来自**运动实际发生的帧区间**。

缺陷面（公开 issue #304）：原语里没有运动帧区间，`remember` 只能拿
`t_start + trajectory_len - 1` 近似 ⇒ 「迟动球」（clip 帧 200..243、运动在尾部）
被记成 [200, 202]——事件锚与运动实际发生的位置差 40 帧。

本件锁（四段场景，报告口径）：
  ① `extract_spatiotemporal_primitives` 产出 `t_first` / `t_last`（片段内运动
     实际发生的闭区间；diffs[t] 对应帧 t→t+1，判定口径与质心轨迹一致 >2 像素；
     无运动帧 ⇒ 整段 [0, n_frames-1]）；
  ② `remember(t_start=片段首帧绝对时刻)` ⇒ 事件区间 = t_start + [t_first, t_last]；
  ③ 旧口径原语（无这两个键）⇒ 原公式不变（向后兼容）。
实现与报告期望的唯一偏离（如实声明）：迟动球事件为 [239, 243] 而非报告期望的
[240, 243]——diff 下标 39（帧 39→40 的过渡）按报告自己的判定口径（>2 变化像素）
也计为运动帧，报告期望与其口径自相矛盾；实现按口径一致取 [239, 243]。
定点变异：`remember` 改回 `trajectory_len` 公式 ⇒ ②的迟动球格红；
`extract` 删 t_first/t_last ⇒ ①与②红。
"""
import numpy as np
import pytest

from lingshu.nn.stcnn import (SpatiotemporalMemory,
                              extract_spatiotemporal_primitives,
                              synth_ball_rolling, synth_static, synth_blinking)


def _ingest(mem, clip, label, t_start):
    p = extract_spatiotemporal_primitives(clip)[0]
    return mem.remember(p, label, t_start=t_start)


# ---------------------------------------------------------------- ① 原语带区间

def test_primitives_carry_motion_interval():
    p = extract_spatiotemporal_primitives(synth_ball_rolling(frames=10, speed_px=2))[0]
    assert p["t_first"] == 0 and p["t_last"] == 9, (
        "全程运动的 clip 运动区间应为整段 [0, 9]：%r" % ((p["t_first"], p["t_last"]),))
    p2 = extract_spatiotemporal_primitives(synth_static(frames=6))[0]
    assert (p2["t_first"], p2["t_last"]) == (0, 5), (
        "无运动帧 ⇒ 取整段 [0, n_frames-1]:%r" % ((p2["t_first"], p2["t_last"]),))


# ---------------------------------------------------------------- ② 四段场景

def test_event_anchors_track_actual_motion():
    mem = SpatiotemporalMemory()
    _ingest(mem, synth_ball_rolling(frames=10, speed_px=2), "球", 0)
    _ingest(mem, synth_static(frames=6), "背景", 100)
    _ingest(mem, synth_blinking(frames=12, period=3), "灯", 150)
    clip = [np.zeros((32, 32), dtype=np.float32) for _ in range(44)]
    clip[40:] = synth_ball_rolling(frames=4, size=32, start=3, speed_px=2)
    _ingest(mem, clip, "迟动球", 200)
    got = {e["label"]: (e["t_start"], e["t_end"]) for e in mem.events}
    assert got["球"] == (0, 9), got
    assert got["背景"] == (100, 105), got
    assert got["灯"] == (150, 160), got
    # 迟动球：运动在 clip 尾部（diffs 39..42）⇒ 事件必须落在 [239, 243]，
    # 而非旧实现的 [200, 202]（锚差 40 帧）。与报告期望 [240,243] 的 1 帧之差
    # 见文件头「如实声明」。
    assert got["迟动球"][0] >= 235 and got["迟动球"][1] == 243, (
        "迟动球事件锚未落在运动实际发生的尾部区间：%r" % (got["迟动球"],))


# ---------------------------------------------------------------- ③ 向后兼容

def test_legacy_primitives_keep_old_formula():
    mem = SpatiotemporalMemory()
    ev = mem.remember({"direction": "向右", "speed": 2.0, "period": None,
                       "moving": True, "trajectory_len": 5}, "手工", t_start=7)
    assert (ev["t_start"], ev["t_end"]) == (7, 11), (
        "旧口径原语（无 t_first/t_last）必须保持原公式：%r" % ((ev["t_start"], ev["t_end"]),))
