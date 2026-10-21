# -*- coding: utf-8 -*-
"""守卫 · `autoframe.frame_points` 必须校验 `fov_deg` 值域（lingshu issue #731）

缺陷（修复前 `lingshu/world/autoframe.py::frame_points`）
    初值 `dist = (scale·0.5)/tan(fov_deg/2)·(1+2·margin_frac)` 的除数在 `fov_deg == 0`
    时为 0（`math.tan(0.0) == 0.0`）⇒ `ZeroDivisionError`。函数对 `points`（空点集）、
    `iters`（`max(1, int(iters))`）、`margin_frac` 均有兜底，唯独 `fov_deg` 无闸。
    实测（本机 Python 3.12.10，基线 29d2a87）：

        frame_points([(0,0,0),(1,1,1)], 800, 600, fov_deg=0.0)
            → ZeroDivisionError: float division by zero
        同路径 fov_deg=50.0（缺省）→ 正常返回 Camera3D
        同路径 fov_deg=-10.0 / 360.0 / 1e9 / nan → **也**「正常返回」，
            但相机 dist ≤ 0 / tan 翻负 / 各字段 NaN —— 静默产出坏相机

判据来源（值域 `0 < fov_deg < 180` 为什么不是拍的）
    · 同仓既有契约：`Camera3D.from_meta`（`lingshu/world/world3d.py`）对同一参数已
      显式 `if not (0.0 < fov < 180.0): raise CameraMetaError(...)` —— 本次与该契约
      对齐，不新造口径。
    · 模块纪律：同模块 `frame_points` / `frame_mesh` / `frame_world` 对空点集一律
      `raise ValueError`，`auto_view` 对空候选同样 raise —— 「显式 raise、不静默
      降级」是本模块既有形态，`fov_deg` 不得例外。
    · 为什么不 clamp 成 50.0：那会把「调用方传了非法视场」译成「照默认视场出图」，
      调用方无从知道自己的参数被丢弃（本波 #732 的 `max(1, …)` 静默升级属同类）。
    · 削掉的判别力（如实标注）：`fov_deg` 恰在 `(0,180)` 开区间之外但物理上「可算」
      的取值（如 `-10.0`、`360.0`）修复前不抛错，现在一律拒 —— 这是**收紧**：全仓
      调用面（`VIEW_CANDIDATES` 固定 50.0、`mesh_svg._auto_camera` 取 style 值、
      测试）无一处依赖这些取值。

定点变异自证（抽掉修复 ⇒ 必红）
    把闸条件回退成永假 ⇒ `test_zero_fov_raises_value_error` 以 ZeroDivisionError 崩；
    把闸收窄成只拦 `== 0.0`（过修/欠修）⇒ `test_out_of_range_fov_rejected` 红。

运行（仓根）：python -X utf8 -m pytest tests/test_issue731_autoframe_fov_guard.py -q --no-header
"""
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lingshu.world.autoframe import frame_points  # noqa: E402

#: 探针点集（非空，硬性前置）。
PTS = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]

#: 合法采样：开区间 (0,180) 内的代表值（含两端邻域）。
VALID_FOVS = (50.0, 1e-12, 1.0, 35.0, 90.0, 179.0, 179.9999999)

#: 非法采样：0 / 负 / ≥180 / 非有限（含 NaN——比较恒 False，属同一闸）。
INVALID_FOVS = (0.0, -0.0, -1e-12, -10.0, 180.0, 360.0, 1e9,
                float("nan"), float("inf"), float("-inf"))


def test_guard_inputs_are_not_empty():
    """硬性断言输入非空：防 `for` 空转成假绿。"""
    assert PTS, "点集不得为空"
    assert len(VALID_FOVS) >= 4, VALID_FOVS
    assert len(INVALID_FOVS) >= 6, INVALID_FOVS


# ==================== ① 核心：非法 fov 一律 ValueError ====================

def test_zero_fov_raises_value_error():
    """★ 核心：`fov_deg=0` 必须显式 `ValueError`（原为 ZeroDivisionError 崩）。"""
    with pytest.raises(ValueError) as ei:
        frame_points(PTS, 800, 600, fov_deg=0.0)
    assert "fov_deg" in str(ei.value), str(ei.value)


def test_out_of_range_fov_rejected():
    """★ 值域：`fov_deg ∉ (0,180)` 一律 `ValueError`，含负数、≥180、NaN/±Inf。

    缺陷形态下这些取值**静默返回坏相机**（dist ≤ 0 / tan 翻负 / 字段 NaN），
    或直接 ZeroDivisionError —— 都不是「显式拒绝」。
    """
    assert INVALID_FOVS, "非法采样集不得为空"
    for fov in INVALID_FOVS:
        with pytest.raises(ValueError) as ei:
            frame_points(PTS, 800, 600, fov_deg=fov)
        assert "fov_deg" in str(ei.value), (fov, str(ei.value))


def test_camera_returned_by_frame_points_is_never_degenerate():
    """★ 后果断言：合法域内返回的相机不得退化（dist 有限正、字段非 NaN）。

    这条把「静默产出坏相机」这一后果钉在返回值上：`fov=1e-12` 与 `fov=179.999`
    这类极端合法值下 `Camera3D` 的位置必须仍是有限数。
    """
    assert VALID_FOVS, "合法采样集不得为空"
    for fov in VALID_FOVS:
        cam = frame_points(PTS, 800, 600, fov_deg=fov)
        assert cam.fov_deg == fov, (fov, cam.fov_deg)
        for field in ("cx", "cy", "cz"):
            v = getattr(cam, field)
            assert math.isfinite(v), (fov, field, v)


# ==================== ② 正对照（量具有效性） ====================

def test_positive_control_default_and_common_fovs_still_work():
    """正对照 1：缺省与常用视角必须照旧返回 Camera3D（证明守卫不是恒红）。

    若修复把合法域也拒掉，本断言立刻变红。
    """
    cam = frame_points(PTS, 800, 600)
    assert cam.fov_deg == 50.0, cam.fov_deg
    for fov in (35.0, 50.0, 60.0, 90.0, 120.0):
        cam = frame_points(PTS, 800, 600, fov_deg=fov)
        assert cam.fov_deg == fov, (fov, cam.fov_deg)


def test_positive_control_other_boundaries_unchanged():
    """正对照 2：空点集与其它入参路径读数不得被本修复改变（同模块既有口径）。"""
    with pytest.raises(ValueError) as ei:
        frame_points([], 800, 600)
    assert "点集为空" in str(ei.value), str(ei.value)

    # `fit_points=[]` 不是「空拟合集」：既有实现 `fit_points or points` 把空列表当
    # 缺省（falsy）⇒ 回落 points，故仍正常返回。这是既有口径，本修复未改它。
    cam = frame_points(PTS, 800, 600, fit_points=[])
    assert cam.fov_deg == 50.0, cam.fov_deg

    # 非法 fov 必须先于几何计算被拒：即使点集同时为空，报的也应是 fov 而非别处
    # （闸在点集校验之后 ⇒ 空点集仍报点集；这条钉住两条闸的先后不被颠倒）
    with pytest.raises(ValueError) as ei:
        frame_points([], 800, 600, fov_deg=0.0)
    assert "点集为空" in str(ei.value), str(ei.value)


def test_positive_control_guard_is_not_typeerror():
    """正对照 3：合法 float 值不得被闸误判成类型错（量具类型正确）。"""
    for fov in (0.5, 179.5):
        cam = frame_points(PTS, 800, 600, fov_deg=float(fov))
        assert math.isfinite(cam.cz), (fov, cam.cz)


# ==================== ③ 消费者面（同一条闸不得被上层 except 吞掉） ====================

def test_frame_mesh_propagates_fov_error_instead_of_silently_falling_back():
    """`frame_mesh` 的上层路径必须以 `ValueError` 拒非法 fov，而不是别的异常。

    `frame_mesh` 的可见顶点分支带 `except (AttributeError, TypeError, ValueError,
    ZeroDivisionError): pass`。缺陷形态实测：`fov=0` 在该分支内先除零被吞，
    随后**角点兜底**路径再调 `frame_points`，仍以 `ZeroDivisionError` 抛出
    （不是静默兜底成功）——但异常类型是除零而非可读的入参拒绝，且「吞一次再抛」
    说明分支里那次 `_dist` 计算同样无闸。修复后两条路径都先撞 `frame_points` 的
    闸 ⇒ 统一 `ValueError`（不依赖除零）。
    """
    class _FakeMesh:
        verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 1.0)]

        class _F:
            def __init__(self, idx):
                self.idx = idx

        faces = [_F((0, 1, 2)), _F((1, 2, 3))]

        def bounds(self):
            return ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))

    from lingshu.world.autoframe import frame_mesh

    with pytest.raises(ValueError) as ei:
        frame_mesh(_FakeMesh(), 800, 600, fov_deg=0.0)
    assert "fov_deg" in str(ei.value), str(ei.value)
    # 正对照：同一网格在合法 fov 下正常出相机
    assert frame_mesh(_FakeMesh(), 800, 600, fov_deg=50.0).fov_deg == 50.0
