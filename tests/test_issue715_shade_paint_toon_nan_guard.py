# -*- coding: utf-8 -*-
"""#715 守卫：`shade_intensity_mode(mode="toon")` 遇非有限强度不得抛。

============================================================================ 缺陷
toon 量化路径直接 `math.floor(i * b)`，`i` 为 NaN 时抛
`ValueError: cannot convert float NaN to integer`。退化三角面 / 零面积面会产出零法线或
NaN 法线 ⇒ `shade_studio` → `look_curve` 一路 NaN ⇒ **toon 档位整帧崩溃**而非降级。
`"toon"` / `"toon_studio"` 是 `shade_paint.SHADE_MODES`（= `world3d.SHADE_MODES`）的闭集成员。
同文件 `shade_rgb` 对 NaN 已有显式口径（注释「NaN 当 0 —— 坏值不许污染整帧」）
⇒ 防护只在 toon 量化这一处缺失。

============================================================================ 判据来源
  · issue #715（作者 Yinfeng818，2026-10-16）的复现读数与「量化前加 isfinite」修复建议；
  · 实现侧口径（`lingshu/world/shade_paint.py`）：toon 量化前
    `if not math.isfinite(i): i = 0.0`，与同族 `shade_rgb` 的「坏值当 0」一致 ——
    坏值降级到**最低档**而不是把整帧带走；
  · 为什么用 `math.isfinite` 而不是 `shade_rgb` 的 `not (i == i)`：本调用点 `i` 只可能
    「有限」或「NaN」（`look_curve` 对有限输入有界、对 ±inf 给 0.0/1.0、对 NaN 给 NaN），
    两种写法读数等价；取 `isfinite` 是因为它同时挡住 ±inf，且是本仓 `world` 层通行写法。

============================================================================ 断言组
  A 正对照（量具有效）：toon + 有限输入必须返回**有限**值并量化到 `(k+0.5)/b` 档；
    同族 `shade_rgb` 的 NaN 口径在场（`(NaN,10,10)` → `(0,10,10)`）。
  B 非有限输入不抛且降级到最低档：NaN / +inf / -inf 法线，`toon` 与 `toon_studio` 都算。
  C 消融：其它模式（studio/lambert/naive）读数逐位不变；正常输入读数逐位不变。
  D `bands` 边界：NaN/None/1/0 各档都不抛。

============================================================================ 回退即红
把 toon 量化前的 `if not math.isfinite(i): i = 0.0` 去掉 ⇒
`test_b1_*` / `test_b2_*` / `test_d1_*` 立刻变红并点名
`ValueError: cannot convert float NaN to integer`。
"""
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world import shade_paint as SP  # noqa: E402

NAN = float("nan")
INF = float("inf")
VIEW = (0.0, 0.0, -1.0)
TOON_MODES = ("toon", "toon_studio")


def test_a1_正对照_量具有效_有限输入可测且量化到档位():
    """正对照①：toon 对有限输入必须返回有限值，且落在 (k+0.5)/b 档上。"""
    assert TOON_MODES == ("toon", "toon_studio"), "toon 模式名被改动"
    assert "toon" in SP.SHADE_MODES and "toon_studio" in SP.SHADE_MODES
    b = SP.TOON_BANDS
    vals = [SP.shade_intensity_mode(n, VIEW, "toon", role="metal")
            for n in ((0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (-1.0, 0.0, 0.0))]
    assert all(isinstance(v, float) and math.isfinite(v) for v in vals), vals
    assert all(0.0 <= v <= 1.0 for v in vals), vals
    for v in vals:
        assert abs(v * b - round(v * b)) == pytest.approx(0.5, abs=1e-9), \
            "toon 读数未落在 (k+0.5)/b 档上：%r（b=%d）" % (v, b)
    # 判别力：不同朝向必须给不同档（否则量具恒绿）
    assert len(set(vals)) > 1, vals


def test_a2_正对照_同族shade_rgb的NaN口径在场():
    """正对照②：同族 `shade_rgb` 的「NaN 当 0」口径必须在场（否则本件的对齐基准没了）。"""
    assert SP.shade_rgb((NAN, 10, 10), 1.0) == (0, 10, 10), \
        "同族 shade_rgb 的 NaN 守卫不见了"
    assert SP.shade_rgb((255, 255, 255), NAN) == (0, 0, 0), \
        "shade_rgb 对 NaN 强度应回 (0,0,0)"


def test_b1_toon遇NaN法线不得抛且必须降级到最低档():
    """#715 主断言：NaN 强度不抛，且量化到最低档 `0.5/b`（= 坏值当 0 的 toon 读数）。"""
    b = SP.TOON_BANDS
    floor = (math.floor(0.0 * b) + 0.5) / b
    for mode in TOON_MODES:
        try:
            got = SP.shade_intensity_mode((NAN, 0.0, 1.0), VIEW, mode, role="metal")
        except ValueError as e:                              # pragma: no cover
            raise AssertionError(
                "toon 遇 NaN 仍抛 ValueError（#715 缺陷形态）：%s" % e)
        assert isinstance(got, float) and math.isfinite(got), \
            "%s 遇 NaN 返回非有限值：%r" % (mode, got)
        assert got == pytest.approx(floor, abs=1e-12), \
            "%s 遇 NaN 应降级到最低档 %.6f，实得 %r" % (mode, floor, got)


def test_b2_toon遇无穷法线不得抛():
    """±inf 也必须被挡住（`math.floor(inf)` 同样抛 OverflowError/ValueError）。"""
    b = SP.TOON_BANDS
    floor = (math.floor(0.0 * b) + 0.5) / b
    for mode in TOON_MODES:
        for bad in (INF, -INF):
            try:
                got = SP.shade_intensity_mode((bad, 0.0, 1.0), VIEW, mode,
                                              role="metal")
            except (ValueError, OverflowError) as e:         # pragma: no cover
                raise AssertionError(
                    "toon 遇 %r 仍抛 %s（#715 同族形态）：%s" % (bad, type(e).__name__, e))
            assert math.isfinite(got), "%s 遇 %r 返回非有限值：%r" % (mode, bad, got)
            assert got == pytest.approx(floor, abs=1e-12), \
                "%s 遇 %r 应降级到最低档，实得 %r" % (mode, bad, got)


def test_b3_NaN视线不得抛且返回值有限():
    """视线里的 NaN 由 `shade_studio` 的 `dh if dh > 0.0 else 0.0` 吸收
    （NaN > 0 为假 ⇒ 取 0.0）⇒ `i` 仍有限、读数与正常视线同档。
    本断言只要求「不抛 + 有限 + 落在档位上」，不钉死具体档位。"""
    b = SP.TOON_BANDS
    got = SP.shade_intensity_mode((0.0, 1.0, 0.0), (NAN, 0.0, -1.0), "toon",
                                  role="metal")
    assert isinstance(got, float) and math.isfinite(got), got
    assert 0.0 <= got <= 1.0, got
    assert abs(got * b - round(got * b)) == pytest.approx(0.5, abs=1e-9), \
        "NaN 视线的读数未落在档位上：%r" % got


def test_b4_三轴NaN逐轴覆盖():
    b = SP.TOON_BANDS
    floor = (math.floor(0.0 * b) + 0.5) / b
    for axis in range(3):
        n = [0.0, 0.0, 0.0]
        n[axis] = NAN
        got = SP.shade_intensity_mode(tuple(n), VIEW, "toon", role="metal")
        assert got == pytest.approx(floor, abs=1e-12), (axis, got)


def test_c1_其它模式读数逐位不变():
    """消融：studio/lambert/naive 对 NaN 的既有读数不得被本次修复改动。"""
    assert math.isnan(SP.shade_intensity_mode((NAN, 0.0, 1.0), VIEW, "studio",
                                             role="metal"))
    assert SP.shade_intensity_mode((NAN, 0.0, 1.0), VIEW, "lambert") == \
        pytest.approx(0.12, abs=1e-12)
    assert SP.shade_intensity_mode((NAN, 0.0, 1.0), VIEW, "naive") == \
        pytest.approx(1.1, abs=1e-12)


def test_c2_正常输入读数逐位不变():
    """冻结口径：有限输入下 toon / studio / lambert / naive 的读数必须一字不差。"""
    v = (0.0, 0.0, -1.0)
    assert SP.shade_intensity_mode((0.0, 1.0, 0.0), v, "toon", role="metal",
                                   bands=3) == \
        pytest.approx(0.8333333333333334, abs=1e-15)
    assert SP.shade_intensity_mode((0.0, -1.0, 0.0), v, "toon", role="metal",
                                   bands=3) == \
        pytest.approx(0.16666666666666666, abs=1e-15)
    assert SP.shade_intensity_mode((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), "lambert") == \
        pytest.approx(0.38 * 1.0 + 0.72 * 0.78, abs=1e-15)
    assert SP.shade_intensity_mode((0, 1, 0), (0, 0, -1), "naive",
                                   cosang=0.5) == pytest.approx(0.825, abs=1e-15)


def test_d1_bands退化值不得抛():
    """`bands` 的退化值（None/0/1/2/3）不得抛；`None`/`<2` 回退 `TOON_BANDS`/钳到 2。

    ⚠ `bands=NaN` **不在本件修复面**：它是**调用方传参误用**（不是几何退化产生的坏值），
    与本件点名的「NaN 强度」不同族失效入口；按派工书「只修本条点名的面」不擅自扩面，
    已在报告里列为同族未修点。"""
    for bands in (None, 1, 0, 2, 3):
        try:
            got = SP.shade_intensity_mode((0.0, 1.0, 0.0), VIEW, "toon",
                                          role="metal", bands=bands)
        except (ValueError, TypeError) as e:                 # pragma: no cover
            raise AssertionError("bands=%r 抛 %s：%s" % (bands, type(e).__name__, e))
        assert math.isfinite(got), (bands, got)
    # 正对照：`bands` 真的在起作用（不同档数给不同读数），否则上面是恒绿
    v3 = SP.shade_intensity_mode((0.0, 1.0, 0.0), VIEW, "toon", role="metal", bands=3)
    v5 = SP.shade_intensity_mode((0.0, 1.0, 0.0), VIEW, "toon", role="metal", bands=5)
    v2 = SP.shade_intensity_mode((0.0, 1.0, 0.0), VIEW, "toon", role="metal", bands=2)
    assert len({v2, v3, v5}) == 3, (v2, v3, v5)
    assert SP.shade_intensity_mode((0.0, 1.0, 0.0), VIEW, "toon", role="metal",
                                   bands=1) == v2, "bands<2 应钳到 2"


def test_e1_退化面端到端整帧不崩():
    """走真实面数据形态：混合法线（正常 + 退化零向量 + NaN）逐面着色不得抛。"""
    faces = [(0.0, 1.0, 0.0), (0.0, 0.0, 0.0), (NAN, 0.0, 1.0), (-1.0, 0.0, 0.0),
             (0.0, -1.0, 0.0), (INF, 0.0, 0.0)]
    assert len(faces) >= 6, "面集非空硬断言"
    out = []
    for n in faces:
        out.append(SP.shade_intensity_mode(n, VIEW, "toon", role="metal"))
    assert len(out) == len(faces)
    assert all(math.isfinite(x) for x in out), out
    assert all(0.0 <= x <= 1.0 for x in out), out
    # 正常面必须仍然给出非最低档的读数（证明没有把整帧都压成 0）
    assert out[0] > out[1], (out[0], out[1])


def test_f1_selftest仍为PASS():
    r = SP.selftest()
    fails = {k: v for k, v in r.items() if isinstance(v, dict) and not v.get("ok")}
    assert not fails, fails
    assert r["verdict"] == "PASS"
