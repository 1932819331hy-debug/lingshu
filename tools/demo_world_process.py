#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_world_process · 世界过程第 1、2 组端到端演示（core 接入 · 落库 · 对照渲染）
============================================================================
本脚本做四件事，全部走**已有**通路，不新建渲染器、不新建数据库：

1. 经 core 的 world_model 门面把观测逐 tick 送进 UnifiedWorldModel，再从**当前模型
   的真实 history** 识别过程（时间会意）；示教提取落进既有 store，关引擎后按
   memory_id 取回同一条记录，再由该记录做平移回放与关系迁移（示教迁移）。
2. 用 World3D 的既有渲染路径（World3D.render）把源轨迹、平移回放、旋转缩放迁移
   三者的同一 tick 画成三张同视角对照图（默认缩放可直接比），并各出一份 GIF；
   另出三张并排对照图 compare.png。
3. 三成员成组 → 其中一名成员离开：出成组、离开两张画面 + 一段 GIF；把离开的成员
   从本帧可见观测里遮蔽，用组模型（motion_groups）推算其落点，在画面里用红色十字
   标出推算位置。
4. 事件回声与过程补全、过程重组（第 2 组）：一个按固定节奏停一步、转向 90° 的场景
   出事件回声读数，并画一张事件时序图（实施线 + 延迟模型预测的落点标记 + 两模型
   读数）；接近→停留→离开场景的补全出“观测末帧 / 补全末帧”对照图；两段记录重组
   出“前段终点 / 连接点 / 后段终点”对照图。补全与重组各再出一段逐帧动画
   （completion.gif / composition.gif），帧上标 tick、来源与分段。

机器读数写 readings.json：过程结果、memory_id、每张图的实体（eid ↔ 类别 ↔ 颜色 ↔
逐 tick 位置）、组模型、遮蔽推算的推算值与真实值对照、事件回声读数、补全与重组的
连接点读数、两段动画的逐帧 tick / 来源 / 分段与同源帧清单索引、相机参数（各组画面
各一台）。图上实体与读数一一对应（同一份 entities 既进渲染也进 JSON）。

用法（仓根）：
    .venv/Scripts/python.exe -X utf8 -B tools/demo_world_process.py \
        --out-dir artifacts/world_process_demo
参数：
    --out-dir   输出目录（缺省 artifacts/world_process_demo；不存在则创建）
    --width/--height  对照图尺寸（缺省 720x480；GIF 按 2/3 比例缩小）

core 不导入 world：脚本里每新建或重开一个引擎，都先经 opened() 调
lingshu.world.register_world_process 注入实现，之后才用 engine.world_process。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

from PIL import Image, ImageDraw

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine            # noqa: E402
from lingshu.world import register_world_process               # noqa: E402
from lingshu.world.world3d import (Camera3D, Object3D,         # noqa: E402
                                   World3D, default_spec)

#: 实体 → 视觉类别（颜色/形状取 World3D 既有类别先验，不自定义配色表）
CATEGORY = {
    "walker": "person",
    "landmark": "tree",
    "m_a": "person",
    "m_b": "robot",
    "m_c": "fox",
    "courier": "robot",
    "rider": "person",
    "beacon": "tree",
    "hero": "person",
    "mark": "tree",
}

#: 接近→停留→离开的观测位置（输入数据；过程类型由 world 模块判定）
APPROACH = {
    "landmark": [[10, 1, 10]] * 12,
    "walker": [[4, 1, 10], [5, 1, 10], [6, 1, 10], [7, 1, 10], [8, 1, 10],
               [8.2, 1, 10], [8, 1, 10], [8.1, 1, 10], [7, 1, 10], [5, 1, 10],
               [3, 1, 10], [1, 1, 10]],
}

#: 组场景：三名成员共同平移，m_c 从第 7 tick 起独自横移
GROUP_N = 12
GROUP_LEAVE_TICK = 7

# ===== 第 2 组场景数据 =====

#: 事件回声场景：每 13 步一轮，第 3 步原地停一步、第 8 步左转 90°，共 8 轮。
COURIER_PERIOD = 13
COURIER_STOP_OFFSET = 3
COURIER_TURN_OFFSET = 8
COURIER_CYCLES = 8

#: 过程补全场景：接近→停留→离开前缀 6 tick，记录取同一段全 12 tick。
COMPLETION_PREFIX_TICKS = 6

#: 过程重组场景：前段 rider 从离 beacon 4.0 走到 2.0，后段从 2.0 接着走到对侧。
SEGMENT_A = {"beacon": [[0, 1, 0]] * 5,
             "rider": [[4, 1, 0], [3.5, 1, 0], [3, 1, 0], [2.5, 1, 0], [2, 1, 0]]}
SEGMENT_B = {"beacon": [[0, 1, 0]] * 5,
             "rider": [[2, 1, 0], [1.5, 1, 0.5], [1, 1, 1], [0.5, 1, 1.5], [0, 1, 2]]}

#: 事件时序图的颜色：实施线按事件类型分色，预测落点固定用红色标记。
EVENT_COLORS = {"stop": (70, 140, 255), "turn": (255, 170, 40)}
PREDICT_COLOR = (255, 60, 60)
TIMELINE_SIZE = (960, 220)


def opened(db_path):
    """新建引擎并显式注册世界过程实现（core 不导入 world，每个引擎各注册一次）。"""
    engine = SpacetimeMemoryEngine(db_path=db_path)
    register_world_process(engine)
    return engine


def flat(history):
    """观测记录的新列表（渲染与读数共用同一份 entities）。"""
    return [{"tick": r["tick"], "entities": {e: [float(v) for v in p]
                                             for e, p in r["entities"].items()}}
            for r in history]


def approach_history():
    """接近→停留→离开的观测历史（12 tick）。"""
    return flat([{"tick": i + 1, "entities": {e: t[i] for e, t in APPROACH.items()}}
                 for i in range(len(APPROACH["walker"]))])


def group_history(n=GROUP_N, leave_tick=GROUP_LEAVE_TICK):
    """三成员共同平移的历史；m_c 从 leave_tick 起独自横移。"""
    out = []
    for i in range(n):
        step = 0.25 * i
        away = 0.8 * (i - leave_tick + 1) if i + 1 >= leave_tick else 0.0
        out.append({"tick": i + 1,
                    "entities": {"m_a": [step, 1.0, 0.0],
                                 "m_b": [1.0 + step, 1.0, 0.0],
                                 "m_c": [2.0 + step + away, 1.0, 0.0]}})
    return flat(out)


def courier_track():
    """事件回声场景的轨迹：计划步上原地停一步或左转 90°，其余步直行一步。"""
    steps = COURIER_CYCLES * COURIER_PERIOD
    kinds = {}
    for cycle in range(COURIER_CYCLES):
        for offset, name in ((COURIER_STOP_OFFSET, "stop"),
                             (COURIER_TURN_OFFSET, "turn")):
            step = cycle * COURIER_PERIOD + offset
            if 1 <= step <= steps:
                kinds.setdefault(step, []).append(name)
    dx, dz, x, z = 1.0, 0.0, 0.0, 0.0
    track = [[0.0, 1.0, 0.0]]
    for step in range(1, steps + 1):
        kind = kinds.get(step) or []
        if kind == ["stop"]:
            track.append([x, 1.0, z])
            continue
        if "turn" in kind:
            dx, dz = -dz, dx
        x, z = x + dx, z + dz
        track.append([x, 1.0, z])
    return track


def courier_history():
    """事件回声场景的观测历史（eid = courier）。"""
    return flat([{"tick": i + 1, "entities": {"courier": pos}}
                 for i, pos in enumerate(courier_track())])


def segment_history(tracks):
    """一段观测历史：tracks = {eid: [[x, y, z], …]}，每 tick 一帧。"""
    return flat([{"tick": i + 1, "entities": {e: t[i] for e, t in tracks.items()}}
                 for i in range(len(next(iter(tracks.values()))))])


def drive(engine, history):
    """经 core 的 world_model 门面逐 tick 观测（历史由模型自己记，不由脚本伪造）。"""
    engine.world_model("init", {"size": 24, "seed": 7})
    for record in history:
        engine.world_model("perceive", {
            "tool": "demo-observer", "tick": record["tick"],
            "observations": [{"eid": e, "category": CATEGORY[e], "pos": p}
                             for e, p in record["entities"].items()]})


# ================= 渲染（World3D 既有图像路径） =================

def camera_for(position_sets):
    """按所有变体的位置并集定一台固定相机：三张对照图共用同一视角。"""
    points = [p for group in position_sets for p in group]
    xs = [p[0] for p in points]
    zs = [p[2] for p in points]
    cx = (min(xs) + max(xs)) / 2.0
    cz = (min(zs) + max(zs)) / 2.0
    span = max(max(xs) - min(xs), max(zs) - min(zs), 4.0)
    eye = (cx, 1.0 + 0.5 * span, cz - 2.0 * span)
    return Camera3D.look_at(eye, (cx, 1.4, cz), fov_deg=48.0)


def camera_readings(camera):
    """相机六参数：对照图与 GIF 先写进 camera 节，组画面相机随后补进同一节。"""
    return {"cx": camera.cx, "cy": camera.cy, "cz": camera.cz,
            "yaw": round(camera.yaw, 6),
            "pitch": round(camera.pitch, 6),
            "fov_deg": camera.fov_deg}


def render_frame(camera, entities, size, marker=None):
    """一帧：按固定相机渲染该帧实体，返回 (图像, 物体清单)。

    物体清单取自 world.objects——图像画的就是这批物体，读数 JSON 用这份清单做一一对应。
    marker 给出世界坐标时，在同一张图上画红色十字（遮蔽成员的推算位置）。
    """
    world = World3D(camera=camera)
    for eid, pos in sorted(entities.items()):
        spec = default_spec(CATEGORY[eid])
        world.objects.append(Object3D(
            category=CATEGORY[eid], center=tuple(float(v) for v in pos),
            size=spec.size, color=spec.color, shape=spec.shape,
            confidence=1.0, source="demo_world_process", eid=eid))
    image = world.render(size[0], size[1], camera=camera)
    if marker is not None:
        draw = ImageDraw.Draw(image)
        point = camera.project(tuple(float(v) for v in marker), size[0], size[1])
        if point is not None:
            x, y = point
            draw.line([(x - 9, y), (x + 9, y)], fill=(255, 60, 60), width=3)
            draw.line([(x, y - 9), (x, y + 9)], fill=(255, 60, 60), width=3)
    return image, world.objects


def label(image, text):
    """在图像左上角加一行 ASCII 标签（对照图靠标签分辨变体）。"""
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 8 + 7 * len(text), 16], fill=(0, 0, 0))
    draw.text((4, 3), text, fill=(255, 255, 255))
    return image


def side_by_side(images, labels):
    """三张同尺寸图像并排成一张对照图。"""
    width = sum(img.width for img in images)
    height = max(img.height for img in images)
    sheet = Image.new("RGB", (width, height), (0, 0, 0))
    x = 0
    for img, text in zip(images, labels):
        sheet.paste(label(img.copy(), text), (x, 0))
        x += img.width
    return sheet


def save_gif(images, path, duration=180):
    """逐帧 GIF（首帧以外的帧作为 append_images）。"""
    first, rest = images[0], images[1:]
    first.save(path, save_all=True, append_images=rest, duration=duration, loop=0)


#: 动画帧说明带的高度：label 的标签只占顶部 16 px，比较两帧时可裁掉整条带。
FRAME_TITLE_HEIGHT = 16


def frame_caption(tick, source, segment):
    """一帧的说明：tick、来源（observed / generated）与所属分段。默认字体无中文字形，故用 ASCII。"""
    return "tick %d | %s | %s" % (tick, source, segment)


def build_animation(name, camera_name, camera, entries, size):
    """按固定相机把带说明的帧序列写成 GIF，返回逐帧读数。

    entries 的每一项是 {"tick", "source", "segment", "entities", "list", "index"}。
    渲染用的 entities 与 list/index 指向的帧清单是同一条目：图与读数共用一份坐标，
    读数不另抄一份实体位置。camera_name 指向 report["camera"] 里的六参数读数。
    """
    images, objects = [], []
    for entry in entries:
        image, drawn = render_frame(camera, entry["entities"], size)
        images.append(label(image, frame_caption(entry["tick"], entry["source"],
                                                entry["segment"])))
        objects.extend(drawn)
    path = "%s.gif" % name
    save_gif(images, os.path.join(OUT_DIR, path))
    identity, seen = [], set()
    for obj in sorted(objects, key=lambda o: o.eid):
        if obj.eid in seen:
            continue
        seen.add(obj.eid)
        identity.append({"eid": obj.eid, "category": obj.category,
                         "color": list(obj.color), "shape": obj.shape})
    return {"gif": path, "camera": camera_name, "size": [size[0], size[1]],
            "title_height": FRAME_TITLE_HEIGHT,
            "frame_count": len(entries),
            "frames": [{"index": i, "tick": e["tick"], "source": e["source"],
                        "segment": e["segment"], "list": e["list"],
                        "list_index": e["index"]} for i, e in enumerate(entries)],
            "objects": identity}


def readings(objects):
    """图上物体 → 机器读数（eid ↔ 类别 ↔ 颜色 ↔ 中心）。"""
    return [{"eid": obj.eid, "category": obj.category,
             "color": list(obj.color), "shape": obj.shape,
             "center": [round(float(v), 6) for v in obj.center]}
            for obj in sorted(objects, key=lambda o: o.eid)]


def render_event_timeline(report, size=TIMELINE_SIZE):
    """事件时序图：横轴 = tick，实线按事件类型分色；延迟模型的预测落点用红色标记。

    图只画 report 里的读数（事件流、留出段切分、两模型的命中与偏差），不另算判据。
    """
    image = Image.new("RGB", size, (18, 18, 22))
    draw = ImageDraw.Draw(image)
    events = report["events"]
    if not events:
        return image, {"events": 0, "ticks": []}
    ticks = [e["tick"] for e in events]
    t0, t1 = min(ticks), max(ticks)
    span = max(1, t1 - t0)
    left, right, top, bottom = 40, size[0] - 20, 40, size[1] - 40

    def x_of(tick):
        return left + (tick - t0) * (right - left) / span

    split_tick = report["split"]["boundary_tick"]
    if split_tick is not None:
        draw.line([(x_of(split_tick), top - 10), (x_of(split_tick), bottom + 10)],
                  fill=(120, 120, 120), width=1)
    for event in events:
        x = x_of(event["tick"])
        color = EVENT_COLORS.get(event["event"], (200, 200, 200))
        draw.line([(x, top), (x, bottom)], fill=color, width=2)
    for row in report["evaluation"]["lag_readings"]:
        if row["predicted_tick"] is None:
            continue
        x, hit = x_of(row["predicted_tick"]), row["hit"]
        color = PREDICT_COLOR if hit else (140, 90, 90)
        draw.line([(x - 5, top - 12), (x + 5, top - 12)], fill=color, width=2)
        draw.line([(x, top - 17), (x, top - 7)], fill=color, width=2)
    draw.line([(left, bottom), (right, bottom)], fill=(90, 90, 90), width=1)
    draw.text((left, 10),
              "events %d | split t%s | lag %d/%d | no-delay %d/%d"
              % (len(events), split_tick,
                 report["evaluation"]["lag_model"]["hits"],
                 report["evaluation"]["lag_model"]["predictions"],
                 report["evaluation"]["no_delay_model"]["hits"],
                 report["evaluation"]["no_delay_model"]["predictions"]),
              fill=(235, 235, 235))
    draw.text((left, bottom + 12), "tick %d" % t0, fill=(180, 180, 180))
    draw.text((right - 44, bottom + 12), "tick %d" % t1, fill=(180, 180, 180))
    def prediction_rows(key):
        """两模型的逐条原始读数：预测、实际、状态与偏差全部照抄模块返回。"""
        return [{"tick": r["tick"], "predicted_tick": r["predicted_tick"],
                 "predicted_eid": r["predicted_eid"],
                 "predicted_event": r["predicted_event"],
                 "actual_tick": r["actual_tick"], "actual_eid": r["actual_eid"],
                 "actual_event": r["actual_event"], "hit": r["hit"],
                 "scored": r["scored"], "end_state": r["end_state"],
                 "abs_tick_error": r["abs_tick_error"]}
                for r in report["evaluation"][key]]

    return image, {"events": len(events), "ticks": ticks, "boundary_tick": split_tick,
                   "colors": {k: list(v) for k, v in EVENT_COLORS.items()},
                   "predict_color": list(PREDICT_COLOR),
                   "split": report["split"],
                   "associations": report["associations"],
                   "lag_model": report["evaluation"]["lag_model"],
                   "no_delay_model": report["evaluation"]["no_delay_model"],
                   "lag_predictions": prediction_rows("lag_readings"),
                   "no_delay_predictions": prediction_rows("no_delay_readings")}


def positions_of(frames, eid):
    """某一实体在生成帧里的逐 tick 位置（缺帧不出现在读数里）。"""
    return [{"tick": f["tick"], "pos": [round(float(v), 6) for v in f["entities"][eid]]}
            for f in frames if eid in f["entities"]]


# ================= 主流程 =================

def build_panel(engine, name, transform, record, png_camera, gif_camera,
                png_size, gif_size, tick_index):
    """一个变体：迁移生成帧 → 参考图 + GIF + 读数切片。"""
    moved = engine.world_process("transfer", dict(transform, record=record))
    if moved.get("status") != "ok":
        raise SystemExit("迁移失败：%s" % moved)
    frames = moved["transfer"]["frames"]
    eids = sorted({e for f in frames for e in f["entities"]})
    positions = [f["entities"] for f in frames]

    sheet, objects = render_frame(png_camera, frames[tick_index]["entities"],
                                  png_size, None)
    png_path = "%s.png" % name
    label(sheet, name).save(os.path.join(OUT_DIR, png_path))

    gif_frames = [render_frame(gif_camera, f["entities"], gif_size, None)[0]
                  for f in frames]
    gif_path = "%s.gif" % name
    save_gif(gif_frames, os.path.join(OUT_DIR, gif_path))

    return {"name": name,
            "transform": moved["transfer"]["transform"],
            "image": png_path, "gif": gif_path,
            "ticks": [frames[0]["tick"], frames[-1]["tick"]],
            "tick_index_shown": tick_index,
            "tick_shown": frames[tick_index]["tick"],
            "generated_source": frames[0]["source"],
            "entities": {eid: {
                "category": CATEGORY[eid],
                "color": list(default_spec(CATEGORY[eid]).color),
                "positions": positions_of(frames, eid)} for eid in eids},
            "objects_in_shown_frame": readings(objects)}, sheet


def main(argv=None):
    global OUT_DIR
    parser = argparse.ArgumentParser(description="世界过程第 1 组端到端演示")
    parser.add_argument("--out-dir", default=os.path.join("artifacts", "world_process_demo"),
                        help="输出目录（缺省 artifacts/world_process_demo）")
    parser.add_argument("--width", type=int, default=720, help="对照图宽（缺省 720）")
    parser.add_argument("--height", type=int, default=480, help="对照图高（缺省 480）")
    args = parser.parse_args(argv)

    OUT_DIR = os.path.abspath(args.out_dir)
    os.makedirs(OUT_DIR, exist_ok=True)
    png_size = (args.width, args.height)
    gif_size = (int(args.width * 2 / 3), int(args.height * 2 / 3))
    report = {"generated_by": "tools/demo_world_process.py", "out_dir": OUT_DIR,
              "camera": {}}

    # ---- ① 时间会意：从当前 UnifiedWorldModel 的真实 history 识别 ----
    engine = opened(os.path.join(OUT_DIR, "world_process.db"))
    history = approach_history()
    drive(engine, history)
    recognized = engine.world_process("recognize", {})
    if recognized.get("status") != "ok":
        raise SystemExit("过程识别失败：%s" % recognized)
    processes = recognized["processes"]
    print("时间会意：history %d tick，识别到 %s"
          % (recognized["history_len"], [p["type"] for p in processes]))

    # ---- ② 示教提取落库 → 关引擎 → 新引擎按 memory_id 取回 ----
    extracted = engine.world_process("extract", {"actor": "walker", "ref": "landmark"})
    if extracted.get("status") != "ok":
        raise SystemExit("示教提取失败：%s" % extracted)
    memory_id = extracted["memory_id"]
    record = extracted["record"]
    engine.close()

    reopened = opened(os.path.join(OUT_DIR, "world_process.db"))
    loaded = reopened.world_process("load", {"memory_id": memory_id})
    record_equal = loaded.get("record") == record
    print("示教提取：memory_id=%s，样本 %d 条，重建引擎取回一致=%s"
          % (memory_id, len(record["samples"]), record_equal))
    report["memory"] = {"db": os.path.join(OUT_DIR, "world_process.db"),
                        "memory_id": memory_id,
                        "kind": record["kind"],
                        "actor": record["actor"], "ref": record["ref"],
                        "samples": len(record["samples"]),
                        "ticks": record["ticks"],
                        "reopened_equal": record_equal}

    # ---- ③ 示教迁移：源 / 平移 / 旋转缩放 三对照 ----
    variants = [
        ("source", {"actor": "walker", "ref": "landmark",
                    "translation": (0.0, 0.0, 0.0), "yaw": 0.0, "scale": 1.0}),
        ("translated", {"actor": "walker", "ref": "landmark",
                        "translation": (4.0, 0.0, 0.0), "yaw": 0.0, "scale": 1.0}),
        ("rotated_scaled", {"actor": "walker", "ref": "landmark",
                            "translation": (4.0, 0.0, 0.0), "yaw": -math.pi / 4,
                            "scale": 2.0}),
    ]
    probe = [reopened.world_process("transfer", dict(t, record=record))["transfer"]["frames"]
             for _n, t in variants]
    # 对照 tick 取 actor 离 ref 最近的一帧（接近→停留的顶点，差异最直观）
    walker, landmark = "walker", "landmark"
    tick_index = min(range(len(probe[0])),
                     key=lambda i: math.dist(probe[0][i]["entities"][walker],
                                             probe[0][i]["entities"][landmark]))
    # 对照图与 GIF 各定一台固定相机：对照图按**变体在同一 tick 的并集**取景
    # （裁掉位移轨迹两端 ⇒ 主体更大、差异更直观），GIF 按全帧并集取景（运动不出画）。
    png_camera = camera_for([[f["entities"][e] for e in f["entities"]]
                             for f in [frames[tick_index] for frames in probe]])
    gif_camera = camera_for([[f["entities"][e] for f in frames for e in f["entities"]]
                             for frames in probe])

    panels, sheets = [], []
    for name, transform in variants:
        panel, sheet = build_panel(reopened, name, transform, record, png_camera,
                                   gif_camera, png_size, gif_size, tick_index)
        panels.append(panel)
        sheets.append(sheet)
        print("对照图：%s（tick %d，平移 %s / 旋转 %.3f rad / 缩放 %.1f）→ %s + %s"
              % (name, panel["tick_shown"], transform["translation"],
                 transform["yaw"], transform["scale"], panel["image"], panel["gif"]))
    compare = side_by_side(sheets, [p["name"] for p in panels])
    compare.save(os.path.join(OUT_DIR, "compare.png"))
    print("并排对照图：compare.png（左=源，中=平移，右=旋转+缩放）")
    report["camera"] = {name: camera_readings(cam)
                        for name, cam in (("panel", png_camera),
                                          ("gif", gif_camera))}
    report["processes"] = processes
    report["panels"] = panels
    report["compare_image"] = "compare.png"
    reopened.close()

    # ---- ④ 运动组合体：三成员成组 → 一名成员离开；遮蔽成员推算 ----
    group_engine = opened(os.path.join(OUT_DIR, "motion_groups.db"))
    grouped_history = group_history()
    drive(group_engine, grouped_history)
    seen = grouped_history[:GROUP_LEAVE_TICK - 1]      # m_c 仍在组里的观察窗口
    early = group_engine.world_process("groups", {"history": seen, "window": 6})
    late = group_engine.world_process("groups", {})
    last = grouped_history[-1]
    hidden = "m_c"
    masked = group_engine.world_process("predict_groups",
                                        {"history": seen, "window": 6,
                                         "hide": [hidden]})
    predicted = masked["predictions"][hidden]
    actual = seen[-1]["entities"][hidden]
    error = math.dist(predicted["pos"], actual)
    print("运动组合体：前 %d tick 成员 %s；全窗口成员 %s（m_c 已离开）"
          % (len(seen), early["model"]["groups"][0]["members"],
             [g["members"] for g in late["model"]["groups"]]))
    print("遮蔽推算：隐藏 %s → 推算 %s，真实 %s，误差 %.6f"
          % (hidden, predicted["pos"], actual, error))

    group_camera = camera_for([[p for f in grouped_history for p in f["entities"].values()]])
    report["camera"]["group"] = camera_readings(group_camera)
    shown = {e: p for e, p in seen[-1]["entities"].items() if e != hidden}
    together = render_frame(group_camera, seen[-1]["entities"], png_size)[0]
    label(together, "3 members together (tick %d)" % seen[-1]["tick"]
          ).save(os.path.join(OUT_DIR, "groups_together.png"))
    leaving = render_frame(group_camera, last["entities"], png_size)[0]
    label(leaving, "1 member left (tick %d)" % last["tick"]
          ).save(os.path.join(OUT_DIR, "groups_leave.png"))
    masked_img, masked_objects = render_frame(group_camera, shown, png_size,
                                              marker=predicted["pos"])
    label(masked_img, "hidden %s, cross = predicted" % hidden
          ).save(os.path.join(OUT_DIR, "groups_hidden.png"))
    save_gif([render_frame(group_camera, f["entities"], gif_size)[0]
              for f in grouped_history], os.path.join(OUT_DIR, "groups.gif"))
    print("组画面：groups_together.png / groups_leave.png / groups_hidden.png + groups.gif")

    report["groups"] = {
        "early": early["model"], "late": late["model"],
        "leave_tick": GROUP_LEAVE_TICK,
        "images": {"together": "groups_together.png", "leave": "groups_leave.png",
                   "hidden": "groups_hidden.png", "gif": "groups.gif"},
        "entities": {eid: {"category": CATEGORY[eid],
                                  "color": list(default_spec(CATEGORY[eid]).color),
                                  "positions": positions_of(grouped_history, eid)}
                     for eid in sorted(CATEGORY) if eid.startswith("m_")}}
    report["masked_prediction"] = {
        "hidden": hidden, "tick": masked["tick"],
        "group_ids": masked["group_ids"],
        "predicted": predicted, "actual": actual,
        "abs_error": error, "unresolved": masked["unresolved"],
        "objects_in_hidden_frame": readings(masked_objects)}
    group_engine.close()

    # ---- ⑤ 事件回声：事件时序图 + 两模型读数 ----
    echo_engine = opened(os.path.join(OUT_DIR, "world_process.db"))
    courier = courier_history()
    drive(echo_engine, courier)
    echoed = echo_engine.world_process("echo", {})
    if echoed.get("status") != "ok":
        raise SystemExit("事件回声失败：%s" % echoed)
    echo_report = echoed["report"]
    timeline, timeline_reading = render_event_timeline(echo_report)
    label(timeline, "event echo: bars = events, red marks = lag predictions")
    timeline.save(os.path.join(OUT_DIR, "echo_timeline.png"))
    print("事件回声：事件 %d 条（停止 %d、转向 %d），开发段 %d 条、留出段 %d 条（边界 tick %s）"
          % (len(echo_report["events"]),
             sum(1 for e in echo_report["events"] if e["event"] == "stop"),
             sum(1 for e in echo_report["events"] if e["event"] == "turn"),
             echo_report["split"]["dev_events"],
             echo_report["split"]["holdout_events"],
             echo_report["split"]["boundary_tick"]))
    for name in ("lag_model", "no_delay_model"):
        model = echo_report["evaluation"][name]
        print("  %s：预测 %d、命中 %d、可评分 %d、无预测 %d、超窗未决 %d、"
              "命中率 %.6f、覆盖率 %.6f、平均绝对偏差 %s"
              % (name, model["predictions"], model["hits"], model["scored"],
                 model["no_prediction"], model["prediction_beyond_window"],
                 model["hit_rate"], model["coverage"],
                 model["mean_abs_tick_error"]))
    print("事件时序图：echo_timeline.png")
    report["echo"] = {
        "thresholds": echo_report["thresholds"], "split": echo_report["split"],
        "associations": echo_report["associations"],
        "lag_model": echo_report["evaluation"]["lag_model"],
        "no_delay_model": echo_report["evaluation"]["no_delay_model"],
        "events": echo_report["events"],
        "image": "echo_timeline.png", "timeline": timeline_reading}

    # ---- ⑥ 过程补全：观测末帧 / 补全末帧对照 + 不命中反例 ----
    # 记录按本场景的观测历史提取（不取当前模型的 courier 历史，两者实体不同）。
    record = echo_engine.world_process("extract", {"history": approach_history(),
                                                   "actor": "walker", "ref": "landmark"})
    if record.get("status") != "ok":
        raise SystemExit("示教提取失败：%s" % record)
    completed = echo_engine.world_process("complete", {
        "history": approach_history()[:COMPLETION_PREFIX_TICKS], "actor": "walker",
        "ref": "landmark", "memory_id": record["memory_id"]})
    if completed.get("status") != "ok":
        raise SystemExit("过程补全失败：%s" % completed)
    completion = completed["completion"]
    if completion["match"] is None:
        raise SystemExit("过程补全未命中：%s" % completion["candidates"])
    missed = echo_engine.world_process("complete", {
        "history": segment_history({"beacon": [[10, 1, 10]] * 6,
                                    "walker": [[30, 1, 10], [28, 1, 10], [26, 1, 10],
                                               [24, 1, 10], [22, 1, 10], [20, 1, 10]]}),
        "actor": "walker", "ref": "landmark", "record": record["record"]})
    if missed.get("status") != "ok":
        raise SystemExit("过程补全反例失败：%s" % missed)
    observed_frame = approach_history()[COMPLETION_PREFIX_TICKS - 1]["entities"]
    completed_frame = completion["frames"][-1]["entities"]
    completion_camera = camera_for([[p for p in observed_frame.values()],
                                    [p for p in completed_frame.values()]])
    observed_img, observed_objects = render_frame(completion_camera, observed_frame,
                                                  png_size, None)
    completed_img, completed_objects = render_frame(completion_camera, completed_frame,
                                                    png_size, None)
    side_by_side([observed_img, completed_img],
                 ["observed tick %d" % approach_history()[COMPLETION_PREFIX_TICKS - 1]["tick"],
                  "completion tick %d" % completion["ticks"][1]]
                 ).save(os.path.join(OUT_DIR, "completion.png"))
    # 补全动画：观测前缀逐帧接上 core 返回的补全尾部逐帧。观测帧取观测输入本身，
    # 生成帧取 completion["frames"]；两边都不写回实际观测历史。
    observed_list = [{"tick": r["tick"], "source": "observed", "entities": r["entities"]}
                     for r in approach_history()[:COMPLETION_PREFIX_TICKS]]
    generated_list = [{"tick": f["tick"], "source": f["source"], "entities": f["entities"]}
                      for f in completion["frames"]]
    completion_gif_camera = camera_for(
        [[p for p in f["entities"].values()] for f in observed_list + generated_list])
    completion_entries = (
        [{"tick": f["tick"], "source": f["source"], "segment": "prefix",
          "entities": f["entities"], "list": "observed", "index": i}
         for i, f in enumerate(observed_list)]
        + [{"tick": f["tick"], "source": f["source"], "segment": "tail",
            "entities": f["entities"], "list": "generated", "index": i}
           for i, f in enumerate(generated_list)])
    completion_animation = build_animation("completion", "completion_gif",
                                           completion_gif_camera, completion_entries,
                                           gif_size)
    print("过程补全：命中来源下标 %d、匹配分数 %.3f、补全 tick %s、不命中反例 match=%s"
          % (completion["match"]["source"]["index"], completion["match"]["match_score"],
             completion["ticks"], missed["completion"]["match"]))
    print("补全对照图：completion.png")
    print("补全动画：completion.gif（%d 帧 = 观测 %d + 生成 %d）"
          % (completion_animation["frame_count"], len(observed_list),
             len(generated_list)))
    report["camera"]["completion_gif"] = camera_readings(completion_gif_camera)
    report["completion"] = {
        "prefix_ticks": COMPLETION_PREFIX_TICKS,
        "match": completion["match"], "seam": completion["seam"],
        "ticks": completion["ticks"], "candidates": completion["candidates"],
        "miss": {"match": missed["completion"]["match"],
                 "frames": missed["completion"]["frames"],
                 "candidates": missed["completion"]["candidates"]},
        "image": "completion.png",
        "frame_lists": {"observed": observed_list, "generated": generated_list},
        "animation": completion_animation,
        "objects_in_observed_frame": readings(observed_objects),
        "objects_in_completed_frame": readings(completed_objects)}

    # ---- ⑦ 过程重组：前段终点 / 连接点 / 后段终点对照 ----
    seg_a = echo_engine.world_process("extract", {"history": segment_history(SEGMENT_A),
                                                  "actor": "rider", "ref": "beacon"})
    seg_b = echo_engine.world_process("extract", {"history": segment_history(SEGMENT_B),
                                                  "actor": "rider", "ref": "beacon"})
    composed = echo_engine.world_process("compose", {
        "first_id": seg_a["memory_id"], "second_id": seg_b["memory_id"],
        "roles": {"actor": "hero", "ref": "mark"}, "translation": (10.0, 0.0, 5.0)})
    if composed.get("status") != "ok":
        raise SystemExit("过程重组失败：%s" % composed)
    composition = composed["composition"]
    joint_views = [composition["segments"][0]["frames"][-1]["entities"],
                   composition["segments"][1]["frames"][0]["entities"],
                   composition["segments"][1]["frames"][-1]["entities"]]
    joint_camera = camera_for([[p for p in entities.values()] for entities in joint_views])
    joint_panels = [render_frame(joint_camera, entities, png_size, None)
                    for entities in joint_views]
    side_by_side([image for image, _ in joint_panels],
                 ["seg1 end t%d" % composition["joint"]["tick"],
                  "joint t%d" % (composition["joint"]["tick"] + composition["transform"]["tick_gap"]),
                  "seg2 end t%d" % composition["ticks"][1]]
                 ).save(os.path.join(OUT_DIR, "composition.png"))
    # 重组动画：第一过程逐帧接上第二过程逐帧，接点帧单独标出。两段帧都取 core
    # 返回的 segments[*]["frames"]，脚本不自己拼轨迹。
    joint_tick = composition["joint"]["tick"] + composition["transform"]["tick_gap"]
    segment_lists = {
        "segment_1": [{"tick": f["tick"], "source": f["source"],
                       "entities": f["entities"]}
                      for f in composition["segments"][0]["frames"]],
        "segment_2": [{"tick": f["tick"], "source": f["source"],
                       "entities": f["entities"]}
                      for f in composition["segments"][1]["frames"]]}
    composition_gif_camera = camera_for(
        [[p for p in f["entities"].values()]
         for entries in segment_lists.values() for f in entries])
    composition_entries = (
        [{"tick": f["tick"], "source": f["source"], "segment": "seg1",
          "entities": f["entities"], "list": "segment_1", "index": i}
         for i, f in enumerate(segment_lists["segment_1"])]
        + [{"tick": f["tick"], "source": f["source"],
            "segment": "joint" if f["tick"] == joint_tick else "seg2",
            "entities": f["entities"], "list": "segment_2", "index": i}
           for i, f in enumerate(segment_lists["segment_2"])])
    composition_animation = build_animation("composition", "composition_gif",
                                            composition_gif_camera,
                                            composition_entries, gif_size)
    print("过程重组：ref_gap=%.6f、actor_gap=%.6f、连续=%s、tick %s"
          % (composition["joint"]["ref_gap"], composition["joint"]["actor_gap"],
             composition["joint"]["continuous"], composition["ticks"]))
    print("重组对照图：composition.png")
    print("重组动画：composition.gif（%d 帧 = 第一过程 %d + 第二过程 %d，接点 tick %d）"
          % (composition_animation["frame_count"],
             len(segment_lists["segment_1"]), len(segment_lists["segment_2"]),
             joint_tick))
    report["composition"] = {
        "joint": composition["joint"], "relations": composition["relations"],
        "transform": composition["transform"], "ticks": composition["ticks"],
        "image": "composition.png",
        "frame_lists": segment_lists, "animation": composition_animation,
        "objects_in_panels": readings([obj for _, objects in joint_panels for obj in objects])}
    report["camera"]["echo"] = camera_readings(completion_camera)
    report["camera"]["joint"] = camera_readings(joint_camera)
    report["camera"]["composition_gif"] = camera_readings(composition_gif_camera)
    echo_engine.close()

    path = os.path.join(OUT_DIR, "readings.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    print("机器读数：%s" % path)
    print("输出目录：%s" % OUT_DIR)
    return 0


OUT_DIR = os.path.join(REPO, "artifacts", "world_process_demo")

if __name__ == "__main__":
    sys.exit(main())
