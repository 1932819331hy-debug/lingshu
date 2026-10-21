# -*- coding: utf-8 -*-
"""守卫 · conv3d 对「时间维小于核」的输入不得抛 negative dimensions（lingshu issue #598）

缺陷（HEAD 修复前，`lingshu/nn/stcnn.py`）
    `conv3d` 的输出尺寸 ot = (T - kt) // st + 1 在 T < kt 时，Python `//` 对负数
    地板取整 ⇒ ot 可为负（触发条件 kt - T > st），`np.zeros((ot, ...))` 抛
    `ValueError: negative dimensions are not allowed`。同文件 `max_pool3d` 用
    `T // kt` 天然非负截断，同输入返回空张量——同类函数边界口径不一致。
    改前实测读数（本机 Python 3.x，st=sh=sw=1）：

        voxel=(1,5,5) kernel=(3,3,3) → conv3d ValueError；max_pool3d OK (0,1,1)
        voxel=(1,5,5) kernel=(5,3,3) → conv3d ValueError；max_pool3d OK (0,1,1)
        voxel=(2,5,5) kernel=(4,3,3) → conv3d ValueError；max_pool3d OK (0,1,1)
        voxel=(1,6,6) kernel=(3,3,3) → conv3d ValueError；max_pool3d OK (0,2,2)

    conv3d 是模块级公开函数（docstring 明确「核可自定义」），自定义大核 + 短片段
    即崩；主线演示不触发（MOTION_KERNEL kt=2）。

修复
    ot/oh/ow 各自 `max(0, ...)` 截断到非负——输入比核小时返回空张量（T 维为 0），
    与 max_pool3d 同口径，下游拿到空特征图可照常 continue。max_pool3d 不改。

判据来源
    · 题面读数即判据：公开 issue #598 的 4 组实测。
    · G2/G3 的既有行为锚点：(2,5,5)/(3,3,3)→(0,3,3)、(4,4,4)/(5,3,3)→(0,2,2)
      （T-kt=-1 时原式 ot=0 本就不抛，属既有「空张量」行为，须保持）；
      max_pool3d 读数不变。口径与 max_pool3d 的 `T // kt` 非负截断对齐属
      经验标定（同文件既有约定），非理论推导。

定点变异自证（抽掉修复 ⇒ 必红）
    把 `conv3d` 的 `max(0, ...)` 回退为原式 `ot = (T - kt) // st + 1`（oh/ow 同）
    ⇒ G1 两组红（ValueError: negative dimensions are not allowed），
    G2/G3 仍绿（正常路径原式与截断式恒等）。恢复 `max(0, ...)` ⇒ 全绿。

运行（仓根）：python -X utf8 -m pytest tests/test_issue598_conv3d_time_dim_guard.py -q --no-header
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from lingshu.nn.stcnn import conv3d, max_pool3d  # noqa: E402


# ---------- G1：题面 4 组「T<kt 或 kt-T>st」输入，改后全部返回合法空张量 ----------
def test_g1_issue_cases_return_empty_tensor_not_raise():
    cases = [
        ((1, 5, 5), (3, 3, 3), (0, 3, 3)),
        ((1, 5, 5), (5, 3, 3), (0, 3, 3)),
        ((2, 5, 5), (4, 3, 3), (0, 3, 3)),
        ((1, 6, 6), (3, 3, 3), (0, 4, 4)),
    ]
    for vshape, kshape, expect in cases:
        v = np.random.RandomState(598).rand(*vshape).astype(np.float32)
        k = np.ones(kshape, dtype=np.float32)
        out = conv3d(v, k)  # 改前此处抛 ValueError: negative dimensions
        assert out.shape == expect, f"{vshape}/{kshape}: {out.shape} != {expect}"
        assert out.dtype == np.float32
        assert out.size == 0  # T 维为 0 的空张量，下游可照常 continue
        assert float(np.abs(out).sum()) == 0.0  # 空图聚合读数可用且为 0


def test_g1_stride_amplified_negative_dim_not_raise():
    """stride 放大情形：kt-T=3 > st=2 ⇒ 原式 ot=(1-4)//2+1=-1，同属 #598 触发面。"""
    v = np.random.RandomState(598).rand(1, 7, 7).astype(np.float32)
    k = np.ones((4, 3, 3), dtype=np.float32)
    out = conv3d(v, k, stride=(2, 1, 1))
    assert out.shape == (0, 5, 5)


# ---------- G2：正常路径零回归 ----------
def test_g2_existing_empty_tensor_behaviors_kept():
    """T-kt=-1（ot=0 但非负）是既有行为，改前就不抛，必须原样保持。"""
    v1 = np.random.RandomState(598).rand(2, 5, 5).astype(np.float32)
    assert conv3d(v1, np.ones((3, 3, 3), np.float32)).shape == (0, 3, 3)
    v2 = np.random.RandomState(598).rand(4, 4, 4).astype(np.float32)
    assert conv3d(v2, np.ones((5, 3, 3), np.float32)).shape == (0, 2, 2)


def test_g2_nonempty_normal_path_shape_unchanged():
    v = np.random.RandomState(598).rand(5, 5, 5).astype(np.float32)
    assert conv3d(v, np.ones((3, 3, 3), np.float32)).shape == (3, 3, 3)


def test_g2_max_pool3d_readings_unchanged():
    """max_pool3d 本件未改，读数必须与改前一致（守卫防顺手误伤）。"""
    v1 = np.random.RandomState(598).rand(1, 5, 5).astype(np.float32)
    assert max_pool3d(v1, (3, 3, 3)).shape == (0, 1, 1)
    v2 = np.random.RandomState(598).rand(1, 6, 6).astype(np.float32)
    assert max_pool3d(v2, (3, 3, 3)).shape == (0, 2, 2)


# ---------- G3：防过修——正常大输入数值与改前逐元素一致 ----------
def _conv3d_prefix_formula(voxel, kernel, stride=(1, 1, 1)):
    """改前原式独立参考实现（原式无 max(0,...)）——正常正维输入下与修复版恒等。"""
    T, H, W = voxel.shape
    kt, kh, kw = kernel.shape
    st, sh, sw = stride
    ot = (T - kt) // st + 1
    oh = (H - kh) // sh + 1
    ow = (W - kw) // sw + 1
    out = np.zeros((ot, oh, ow), dtype=np.float32)
    for t in range(ot):
        for i in range(oh):
            for j in range(ow):
                patch = voxel[t * st:t * st + kt,
                              i * sh:i * sh + kh,
                              j * sw:j * sw + kw]
                out[t, i, j] = float((patch * kernel).sum())
    return out


def test_g3_normal_large_input_bitwise_equal_to_prefix():
    """非平凡 case（含 stride≠1）：同 seed 同核，逐元素与改前原式完全相等。"""
    rs = np.random.RandomState(598)
    v = rs.rand(6, 7, 8).astype(np.float32)
    k = rs.rand(2, 3, 3).astype(np.float32) - 0.5  # 非对称核，避免平凡
    for stride in [(1, 1, 1), (2, 1, 2)]:
        ref = _conv3d_prefix_formula(v, k, stride)
        got = conv3d(v, k, stride=stride)
        assert got.shape == ref.shape
        assert got.dtype == ref.dtype == np.float32
        assert np.array_equal(got, ref), f"stride={stride}: 逐元素不等（过修）"
