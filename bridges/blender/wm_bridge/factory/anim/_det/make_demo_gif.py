# -*- coding: utf-8 -*-
"""make_demo_gif · 产出**可审计的动画成品**（世界模型自己的出口：physics → 逐帧 → GIF）

产物（全部写工作区内路径）：
    frames/*.png     确定性 PNG 帧序列（自带编码器，逐字节可复现）
    physics_demo.gif  真 GIF（>=48 帧 / 560px）；回读 n_frames / size / duration / loop
    physics_strip.gif 胶片条（**静态**，回读 n_frames == 1 ⇒ 判据能把两者分开）
    demo_readings.json 全部原始读数（含逐帧前景质心位移）

跑法：`PYTHONPATH=. python3 -X utf8 wm_bridge/factory/anim/_det/make_demo_gif.py <outdir>`
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))
#: ⚠ 原来是**机器专属的绝对路径**（`/home/sixtyseven67/...`）⇒ 换台机器就断。
#: 改为走 `factory/_deps.py` 的显式解析（`WM_LINGSHU_ROOT` 优先 + 仓库相对候选）。
from wm_bridge.factory import _deps as _DEP                    # noqa: E402
if _DEP.lingshu_root() not in sys.path:
    sys.path.insert(0, _DEP.lingshu_root())

from wm_bridge.factory.anim import seq as Q          # noqa: E402
from lingshu.world import physics as PH              # noqa: E402

FPS = 24
N = 56                      # >= 24 帧（要求）
SIZE = (560, 560)           # >= 480 px（要求）


def build_tracks():
    """三条物理运动合成一个"多体"场景：抛体（带弹跳）+ 阻尼弹簧 + 匀速圆周。"""
    proj = {"p0": (-2.6, 0.9, -1.4), "v0": (3.4, 7.4, 1.9), "up": 1,
            "bounce": True, "ground_z": 0.0, "restitution": 0.55, "max_bounces": 6}
    spring = {"omega0": 5.2, "zeta": 0.22, "amp": 1.5, "unit": "m"}
    circ = {"up": 1, "center": (2.1, 1.5, 1.6), "radius": 0.85, "omega": 2.6,
            "theta0": 0.0, "v_up": 0.0, "t_ref": 0.0}
    tracks = []
    for i in range(N):
        t = i / float(FPS)
        p_proj, v_proj = PH.projectile_state(proj, t)
        # 弹簧：直接用**解析包络**（`predict_at` 的闭集是六类预测模型，不含 spring 这一"运动源"）
        x_s = (-2.6 + spring["amp"] * float(PH.decay_envelope(t, spring["omega0"], spring["zeta"])),
               0.9, 1.5)
        p_c, v_c = PH.predict_at(dict(circ, kind="circular"), t)
        # 三体合成到一张图上：主点 = 抛体（有拖尾），副点用速度矢量表示
        tracks.append((t, p_proj, v_proj, x_s, p_c))
    return tracks, {"projectile": proj, "spring": spring, "circular": circ}


def render(tracks):
    """把三条轨迹画进逐帧图（不经过 Blender —— 这是世界模型自己的出口）。

    ⚠ 取景**故意是硬编码**的（`k = 62.0` + 原点 `(0.42w, 0.74h)`）：让地面网格**延伸出画面**，
    画面才不空。第 28 阶段我一度把它改成"按轨迹范围自动适配"，**看图后回退** ——
    把整块网格塞进画面会让所有元素一起缩小、观感更差。
    另：不要用"前景像素占比"来判这类**线稿式轨迹图**的空/满：它与实体渲染不可比
    （六足实体 81.7% vs 本图 2~3%，后者**正常**）。
    """
    from PIL import Image, ImageDraw
    w, h = SIZE
    k = 62.0
    ox, oy = w * 0.42, h * 0.74

    def proj(p):
        return (ox + (p[0] + 0.45 * p[2]) * k, oy - (p[1] - 0.32 * p[2]) * k)

    frames = []
    for i, (_t, p, v, sp, cp) in enumerate(tracks):
        im = Image.new("RGB", (w, h), (12, 16, 28))
        d = ImageDraw.Draw(im)
        for g in range(-6, 7):                                   # 地面网格
            a = proj((g * 0.6, 0.0, -3.6)); b = proj((g * 0.6, 0.0, 3.6))
            d.line([a, b], fill=(26, 32, 50), width=1)
            a = proj((-3.6, 0.0, g * 0.6)); b = proj((3.6, 0.0, g * 0.6))
            d.line([a, b], fill=(26, 32, 50), width=1)
        # 抛体拖尾（渐隐）
        lo = max(0, i - 22)
        pts = [proj(tracks[j][1]) for j in range(lo, i + 1)]
        for j in range(1, len(pts)):
            f = j / float(max(1, len(pts) - 1))
            d.line([pts[j - 1], pts[j]], fill=(int(40 + 120 * f), int(90 + 130 * f), 255), width=3)
        # 拖尾的**历史落点**（弹跳点）
        for j in range(lo, i + 1):
            z = tracks[j][1][1]
            if z < 0.02:
                q = proj(tracks[j][1])
                d.ellipse([q[0] - 3, q[1] - 1, q[0] + 3, q[1] + 1], fill=(255, 150, 60))
        sx, sy = proj(p)
        d.ellipse([sx - 9, sy - 9, sx + 9, sy + 9], fill=(140, 235, 255))
        d.ellipse([sx - 4, sy - 4, sx + 4, sy + 4], fill=(255, 255, 255))
        va, vb = proj(p), proj((p[0] + v[0] * 0.18, p[1] + v[1] * 0.18, p[2] + v[2] * 0.18))
        d.line([va, vb], fill=(255, 190, 80), width=3)
        qx, qy = proj(sp)                                        # 弹簧振子
        d.rectangle([qx - 6, qy - 6, qx + 6, qy + 6], fill=(120, 255, 170))
        cxx, cyy = proj(cp)                                      # 圆周体
        d.ellipse([cxx - 7, cyy - 7, cxx + 7, cyy + 7], fill=(255, 120, 190))
        cc = proj(circ_center_for_draw())
        d.ellipse([cc[0] - 2, cc[1] - 2, cc[0] + 2, cc[1] + 2], fill=(90, 90, 120))
        d.text((12, 12), "t=%.3fs  frame %d/%d" % (_t, i, N - 1), fill=(190, 210, 235))
        frames.append(im)
    return frames


def circ_center_for_draw():
    return (2.1, 1.5, 1.6)


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "_evidence", "anim")
    outdir = os.path.abspath(outdir)
    frames_dir = os.path.join(outdir, "frames")
    tracks, models = build_tracks()
    images = render(tracks)
    paths = Q.save_frames(images, frames_dir, prefix="phys")
    hashes1 = Q.frame_hashes(paths)
    # 重跑一次，逐字节复现
    paths2 = Q.save_frames(render(build_tracks()[0]), frames_dir + "_rerun", prefix="phys")
    hashes2 = Q.frame_hashes(paths2)
    same = [a["sha256"] == b["sha256"] for a, b in zip(hashes1, hashes2)]

    gif = os.path.join(outdir, "physics_demo.gif")
    obs = Q.to_gif(images, gif, duration_ms=40, loop=0)
    rb = Q.read_gif_info(gif)
    strip = Q.to_strip(images, cols=8)
    strip_gif = os.path.join(outdir, "physics_strip.gif")
    strip_png = os.path.join(outdir, "physics_strip.png")
    Q._lazy_image()
    strip.save(strip_gif)
    strip.save(strip_png)
    strip_rb = Q.read_gif_info(strip_gif)

    mt = Q.motion_track(paths)

    # 对抗性（每条都必须 raise，且**不留下坏文件**）
    adv = []
    probe = os.path.join(outdir, "_should_not_exist.gif")

    def _try(name, fn):
        try:
            fn()
            adv.append({"case": name, "raised": None})
        except Exception as e:                                        # noqa: BLE001
            adv.append({"case": name, "raised": type(e).__name__, "msg": str(e)[:80],
                        "file_left": os.path.exists(probe)})

    _try("N=0", lambda: Q.to_gif([], probe))
    _try("N=1", lambda: Q.to_gif(images[:1], probe))
    _try("尺寸不一致", lambda: Q.to_gif([images[0], images[0].resize((64, 64))], probe))
    _try("duration=0", lambda: Q.to_gif(images, probe, duration_ms=0))
    _try("duration=-10", lambda: Q.to_gif(images, probe, duration_ms=-10))
    _try("duration=105(非10倍数)", lambda: Q.to_gif(images, probe, duration_ms=105))
    _try("loop=-1", lambda: Q.to_gif(images, probe, loop=-1))
    _try("save_frames 空", lambda: Q.save_frames([], outdir))

    # RGBA fail-open 回归：合成必须**被记录**，不许静默丢
    rgba = [im.convert("RGBA").copy() for im in images[:4]]
    for im in rgba:
        px = im.load()
        for yy in range(0, 40, 4):
            for xx in range(0, 40, 4):
                px[xx, yy] = (255, 0, 0, 0)
    alpha_gif = os.path.join(outdir, "alpha_probe.gif")
    obs_alpha = Q.to_gif(rgba, alpha_gif, duration_ms=40, background=(0, 0, 0))

    out = {
        "schema": "wm_bridge.anim/demo/1",
        "fps": FPS, "frames": N, "size": SIZE,
        "models": models,
        "frame_hashes": hashes1,
        "rerun_identical": all(same),
        "rerun_identical_count": sum(1 for s in same if s),
        "gif_obs_returned": obs,
        "gif_readback": rb,
        "strip_readback": strip_rb,
        "strip_is_single_frame": strip_rb["frames"] == 1,
        "gif_is_animation": rb["frames"] == N,
        "rgba_probe": obs_alpha,
        "rgba_composited_recorded": obs_alpha.get("alpha_composited") == 4,
        "adversarial": adv,
        "motion": {"analysis_size": mt["analysis_size"], "full_size": mt["full_size"],
                   "max_disp_px": mt["max_disp_px"], "mean_disp_px": mt["mean_disp_px"],
                   "min_disp_px": mt["min_disp_px"], "median_disp_px": mt["median_disp_px"],
                   "jerk_ratio": mt["jerk_ratio"],
                   "temporal_std_moving_frac": mt["temporal_std_moving_frac"],
                   "mean_abs_interframe_diff": mt["mean_abs_interframe_diff"],
                   "scale_to_full": mt["scale_to_full"],
                   "disp_px_series": mt["disp_px"],
                   "centroid_series": [(f["cx"], f["cy"], f["fg_frac"]) for f in mt["frames"]]},
        "artifacts": {"gif": gif, "strip_gif": strip_gif, "strip_png": strip_png,
                      "frames_dir": frames_dir, "n_frames": len(paths),
                      "first_frame": paths[0], "last_frame": paths[-1]},
    }
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "demo_readings.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("DEMO " + json.dumps({
        "gif": gif, "readback": rb, "strip_frames": strip_rb["frames"],
        "rerun_identical": out["rerun_identical"],
        "max_disp_px": round(mt["max_disp_px"], 3),
        "mean_disp_px": round(mt["mean_disp_px"], 3),
        "min_disp_px": round(mt["min_disp_px"], 3),
        "moving_frac": round(mt["temporal_std_moving_frac"], 4),
        "mean_abs_interframe_diff": round(mt["mean_abs_interframe_diff"], 3),
        "adversarial_raised": sum(1 for a in adv if a["raised"]),
        "alpha_composited": obs_alpha.get("alpha_composited"),
    }, ensure_ascii=False))
    print("READINGS " + os.path.join(outdir, "demo_readings.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
