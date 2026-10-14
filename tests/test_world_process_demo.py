# -*- coding: utf-8 -*-
"""test_world_process_demo · 演示脚本的过程补全与过程重组动画（第 2 组）
================================================================================
交接要求（第 2 组终审报告 H1）：演示对过程补全与过程重组只出静态 PNG，看不出尾部
接续与两段重绑定。本件钉住新增的两段动画：completion.gif（观测前缀 → 生成补全尾部）
与 composition.gif（第一过程 → 接点 → 第二过程）。

判据：
  G1 实跑：演示在 tmp_path 目录里跑一次，退出码为 0，写出可解析的 readings.json；
  G2 画面真动：用 Pillow 打开**实际保存**的 GIF，n_frames ≥ 2，且裁掉标题带后仍有
     ≥ 2 帧不同——只变 tick 标题不算运动；
  G3 帧数据同源：动画逐帧的 tick / 来源 / 分段，与 readings 里 list + list_index
     指向的帧清单逐条相等；来源标记只有 observed（观测前缀）与 generated（core 生成帧），
     且顺序是先观测后生成、先第一过程后第二过程；
  G4 相机可用：两台动画相机的六参数齐全；按读数重建相机后，全部动画帧的实体中心
     都落在画面内（相机取的是全轨迹并集，运动不出画）。

运行（仓根）：
    .venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_world_process_demo.py \
        -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import pytest
from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.world.world3d import Camera3D                     # noqa: E402

DEMO = os.path.join(REPO, "tools", "demo_world_process.py")

#: 比较画面时从顶部裁掉的高度：label 的标签只占 16 px，多裁 8 px 留边。
TITLE_CROP = 24

#: 两段动画：(readings 键, GIF 文件名)。
ANIMATIONS = (("completion", "completion.gif"), ("composition", "composition.gif"))

CAMERA_KEYS = ("cx", "cy", "cz", "yaw", "pitch", "fov_deg")


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """在临时目录实跑一次演示（整个会话只跑这一回）。"""
    out = str(tmp_path_factory.mktemp("world_process_demo"))
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", DEMO, "--out-dir", out],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    path = os.path.join(out, "readings.json")
    readings = None
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            readings = json.load(fh)
    return {"out": out, "proc": proc, "readings": readings, "path": path}


@pytest.fixture(scope="session")
def report(demo):
    assert demo["readings"] is not None, (
        "演示未写出 readings.json（退出码 %s）；stderr 末尾：\n%s"
        % (demo["proc"].returncode, demo["proc"].stderr[-2000:]))
    return demo["readings"]


def test_demo_exit_code_and_readings(demo):
    """G1：退出码为 0，readings.json 写出且带两段动画读数。"""
    proc = demo["proc"]
    assert proc.returncode == 0, (
        "演示退出码 %s；stderr 末尾：\n%s" % (proc.returncode, proc.stderr[-2000:]))
    assert demo["readings"] is not None, "演示未写出 readings.json"


@pytest.mark.parametrize("key,gif_name", ANIMATIONS)
def test_gif_exists_with_moving_frames(demo, report, key, gif_name):
    """G2：GIF 实际保存、帧数 ≥ 2，裁掉标题带后仍有 ≥ 2 帧不同。"""
    anim = report[key]["animation"]
    assert anim["gif"] == gif_name
    path = os.path.join(demo["out"], gif_name)
    assert os.path.isfile(path), "%s 未写出" % gif_name

    with Image.open(path) as img:
        assert img.format == "GIF", "%s 不是 GIF" % gif_name
        assert img.n_frames >= 2, "%s 只有 %d 帧" % (gif_name, img.n_frames)
        assert img.n_frames == anim["frame_count"], (
            "GIF 帧数 %d 与读数 frame_count %d 不等" % (img.n_frames, anim["frame_count"]))
        size = [img.width, img.height]
        frames = []
        for index in range(img.n_frames):
            img.seek(index)
            frames.append(img.convert("RGB").copy())

    assert size == anim["size"], "GIF 尺寸 %s 与读数 %s 不等" % (size, anim["size"])
    crop = max(int(anim["title_height"]) + 8, TITLE_CROP)
    bodies = [f.crop((0, crop, f.width, f.height)) for f in frames]
    distinct = len({b.tobytes() for b in bodies})
    assert distinct >= 2, (
        "%s 裁掉标题带后 %d 帧里只有 %d 种画面——只变 tick 标题不算运动"
        % (gif_name, len(bodies), distinct))


@pytest.mark.parametrize("key,gif_name", ANIMATIONS)
def test_animation_frames_match_source_lists(report, key, gif_name):
    """G3：逐帧 tick / 来源 / 分段与同源帧清单逐条相等，帧序单调。"""
    section = report[key]
    anim = section["animation"]
    lists = section["frame_lists"]
    frames = anim["frames"]

    assert len(frames) == anim["frame_count"]
    assert [f["index"] for f in frames] == list(range(len(frames)))
    ticks = [f["tick"] for f in frames]
    assert ticks == sorted(ticks), "动画帧 tick 不是递增：%s" % ticks
    assert len(set(ticks)) == len(ticks), "动画帧 tick 有重复：%s" % ticks
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert set(gaps) <= {1}, "演示场景逐帧相接，tick 间隔应为 1，实测 %s" % sorted(set(gaps))

    for frame in frames:
        assert frame["list"] in lists, "帧指向未知清单 %r" % frame["list"]
        assert frame["segment"], "帧缺分段标记（第 %d 帧）" % frame["index"]
        source = lists[frame["list"]][frame["list_index"]]
        assert source["tick"] == frame["tick"], (
            "第 %d 帧 tick %s 与清单 tick %s 不等"
            % (frame["index"], frame["tick"], source["tick"]))
        assert source["source"] == frame["source"], (
            "第 %d 帧来源 %s 与清单来源 %s 不等"
            % (frame["index"], frame["source"], source["source"]))
        assert source["entities"], "清单第 %d 条没有实体" % frame["list_index"]

    sources = {f["source"] for f in frames}
    assert sources <= {"observed", "generated"}, "来源标记超出约定：%s" % sorted(sources)


def test_completion_animation_prefix_then_generated_tail(report):
    """G3：补全动画先观测前缀、后生成尾部，分段与来源标记严格对应。"""
    section = report["completion"]
    frames = section["animation"]["frames"]
    observed_n = len(section["frame_lists"]["observed"])
    generated_n = len(section["frame_lists"]["generated"])

    assert observed_n >= 1 and generated_n >= 1
    assert [f["list"] for f in frames] == (["observed"] * observed_n
                                           + ["generated"] * generated_n)
    assert [f["segment"] for f in frames] == (["prefix"] * observed_n
                                              + ["tail"] * generated_n)
    assert {f["source"] for f in frames[:observed_n]} == {"observed"}
    assert {f["source"] for f in frames[observed_n:]} == {"generated"}
    first_generated = frames[observed_n]["tick"]
    assert first_generated == frames[observed_n - 1]["tick"] + 1, (
        "补全尾部首帧 tick %s 未接在观测末帧之后" % first_generated)
    assert section["frame_lists"]["generated"][0]["tick"] == first_generated


def test_composition_animation_segments_and_joint(report):
    """G3：重组动画按第一过程 → 接点帧 → 第二过程排序。"""
    section = report["composition"]
    frames = section["animation"]["frames"]
    first_n = len(section["frame_lists"]["segment_1"])
    joint_tick = section["joint"]["tick"] + section["transform"]["tick_gap"]

    assert [f["segment"] for f in frames[:first_n]] == ["seg1"] * first_n
    assert frames[first_n]["segment"] == "joint"
    assert set(f["segment"] for f in frames[first_n + 1:]) <= {"seg2"}
    assert frames[first_n]["tick"] == joint_tick, (
        "接点帧 tick %s 与 joint.tick + tick_gap %s 不等"
        % (frames[first_n]["tick"], joint_tick))
    assert frames[first_n]["list"] == "segment_2"


@pytest.mark.parametrize("key,gif_name", ANIMATIONS)
def test_animation_camera_readings_usable(report, key, gif_name):
    """G4：相机六参数齐全、按读数重建后覆盖全部动画帧的实体。"""
    section = report[key]
    anim = section["animation"]
    params = report["camera"][anim["camera"]]

    assert set(params) == set(CAMERA_KEYS), "相机读数缺键：%s" % sorted(params)
    for name in CAMERA_KEYS:
        assert math.isfinite(float(params[name])), "相机参数 %s 不是有限数" % name
    assert 0.0 < float(params["fov_deg"]) < 180.0, "视场角超出开区间 (0, 180)"

    camera = Camera3D(**{name: float(params[name]) for name in CAMERA_KEYS})
    width, height = anim["size"]
    outside = []
    for frame in anim["frames"]:
        entities = section["frame_lists"][frame["list"]][frame["list_index"]]["entities"]
        for eid, pos in entities.items():
            point = camera.project(tuple(float(v) for v in pos), width, height)
            if point is None or not (0.0 <= point[0] <= width and 0.0 <= point[1] <= height):
                outside.append((frame["tick"], eid, point))
    assert not outside, (
        "%s 的相机没有覆盖全轨迹，%d 个实体中心落在画面外：%s"
        % (gif_name, len(outside), outside[:5]))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
