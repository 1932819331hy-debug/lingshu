#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_world_process · 世界过程第 1 组端到端演示（core 接入 · 落库 · 对照渲染）
============================================================================
本脚本只做三件事，全部走**已有**通路，不新建渲染器、不新建数据库：

1. 经 core 的 world_model 门面把观测逐 tick 送进 UnifiedWorldModel，再从**当前模型
   的真实 history** 识别过程（时间会意）；示教提取落进既有 store，关引擎后按
   memory_id 取回同一条记录，再由该记录做平移回放与关系迁移（示教迁移）。
2. 用 World3D 的既有渲染路径（World3D.render）把源轨迹、平移回放、旋转缩放迁移
   三者的同一 tick 画成三张同视角对照图（默认缩放可直接比），并各出一份 GIF；
   另出三张并排对照图 compare.png。
3. 三成员成组 → 其中一名成员离开：出成组、离开两张画面 + 一段 GIF；把离开的成员
   从本帧可见观测里遮蔽，用组模型（motion_groups）推算其落点，在画面里用红色十字
   标出推算位置。

机器读数写 readings.json：过程结果、memory_id、每张图的实体（eid ↔ 类别 ↔ 颜色 ↔
逐 tick 位置）、组模型、遮蔽推算的推算值与真实值对照、相机参数（对照图、GIF 与组画面
三台）。图上实体与读数一一对应（同一份 entities 既进渲染也进 JSON）。

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


def readings(objects):
    """图上物体 → 机器读数（eid ↔ 类别 ↔ 颜色 ↔ 中心）。"""
    return [{"eid": obj.eid, "category": obj.category,
             "color": list(obj.color), "shape": obj.shape,
             "center": [round(float(v), 6) for v in obj.center]}
            for obj in sorted(objects, key=lambda o: o.eid)]


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
    report = {"generated_by": "tools/demo_world_process.py", "out_dir": OUT_DIR}

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
