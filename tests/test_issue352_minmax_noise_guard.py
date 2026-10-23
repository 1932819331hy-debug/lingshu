# -*- coding: utf-8 -*-
"""#352 守卫：`frames_to_voxel` 不得把任意小噪声放大到满量程。

缺陷面（公开 issue #352）：整段 min-max 归一化没有量程锚点——静止画面 + σ=1e-6
噪声的 motion_magnitude=236.9，**大于**真实滚动小球的 90.0（运动检测与真实运动
幅度无关）。本件把归一化锚定到**绝对量纲**（[0,1] 原样 / [0,255] 除以 255；仅自造
浮点域退回 min-max），本文件锁：

  ① 量纲序：静止微小噪声的 motion 随噪声幅值**单调**，且 σ=1e-6 ≪ 真实小球；
  ② 修前复现格：σ=1e-6 静止画面的 motion 不得再超过真实小球（旧值 236.9 vs 90.0）；
  ③ 真实运动仍被检出：小球 motion=90.0、moving=True、direction=向右（逐位不变）；
  ④ 纯静止仍为 0。
定点变异：`frames_to_voxel` 改回无条件整段 min-max ⇒ ①② 红。
"""
import numpy as np
import pytest

from lingshu.nn.stcnn import (extract_spatiotemporal_primitives,
                              frames_to_voxel, synth_ball_rolling, synth_static)


def _motion(frames):
    return extract_spatiotemporal_primitives(frames)[0]["motion_magnitude"]


@pytest.fixture(scope="module")
def ball_motion():
    return _motion(synth_ball_rolling(frames=10, speed_px=2))


# ---------------------------------------------------------------- ① 单调 + 量纲序

def test_noise_motion_is_monotonic_and_far_below_real(ball_motion):
    rng = np.random.default_rng(0)
    mags = {}
    for amp in (0.0, 1e-6, 1e-4, 1e-2):
        fr = [f + amp * rng.standard_normal(f.shape).astype(np.float32)
              for f in synth_static(frames=6)]
        mags[amp] = _motion(fr)
    assert mags[0.0] == 0.0
    assert mags[1e-6] <= mags[1e-4] <= mags[1e-2], (
        "噪声幅值增大 motion 应单调不减：%r" % (mags,))
    assert mags[1e-6] < ball_motion * 0.01, (
        "σ=1e-6 噪声的 motion(%r) 仍与真实运动(%r)同量级——噪声仍被放大到满量程"
        % (mags[1e-6], ball_motion))


# ---------------------------------------------------------------- ② 修前复现格

def test_tiny_noise_does_not_exceed_real_motion(ball_motion):
    rng = np.random.default_rng(0)
    fr = [f + 1e-6 * rng.standard_normal(f.shape).astype(np.float32)
          for f in synth_static(frames=6)]
    m = _motion(fr)
    assert m < ball_motion, (
        "静止画面 + σ=1e-6 噪声的 motion(%r) 仍大于真实滚动小球(%r)（#352 的原读数）"
        % (m, ball_motion))


# ---------------------------------------------------------------- ③ 真实运动不变

def test_real_ball_motion_unchanged(ball_motion):
    p = extract_spatiotemporal_primitives(synth_ball_rolling(frames=10, speed_px=2))[0]
    assert p["motion_magnitude"] == 90.0, (
        "真实运动的 motion 读数被改动：%r（应逐位保持 90.0）" % (p["motion_magnitude"],))
    assert p["moving"] is True and p["direction"] == "向右"


# ---------------------------------------------------------------- ④ 纯静止为零

def test_static_zero_noise_is_zero(ball_motion):
    assert _motion(synth_static(frames=6)) == 0.0
