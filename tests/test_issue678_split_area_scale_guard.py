# -*- coding: utf-8 -*-
"""test_issue678_split_area_scale_guard · 切分面积上限门按**输入画幅**（#678）
==========================================================================
缺陷（#678，改前实测复现）：``_vocab_split_one`` 的面积上限门乘的是模块常量
``SIZE = 512``（``area > SPLIT_MAX_AREA_FRAC * SIZE * SIZE`` ⇒ 门固定 131072px），
与输入画幅无关。#523 让非 512×512 输入能跑之后二者分叉：

    1200×1200  L 形（占画幅 0.101）  _vocab_split_one=None  n_instances=1  ← 误挡
    300×300   整幅前景（占画幅 1.0） _vocab_split_one=split   漏挡

修法：上限改用 ``mask.shape[0] * mask.shape[1]``（输入画幅）。``SPLIT_MIN_AREA``
保持绝对像素（按 512 词表标称半径 51 锚定，不在本件范围）。

判据（本件）：
  G1 大画幅（1200）普通对象不再被误挡 ⇒ ``_vocab_split_one`` 返回 (a, b)，
     且 ``split_subjects`` 实例数与 512 档一致（同为 2）；
  G2 512 原尺寸行为一字不变（防过修）；
  G3 真整幅前景（占满画幅，面积 > 0.5·H·W）仍被挡 ⇒ 返回 None。

运行（仓根）：python -X utf8 -m pytest tests/test_issue678_split_area_scale_guard.py -q
"""
from __future__ import annotations

import numpy as np

from lingshu.gen import hexgen_subject_split as X


def _scene(s):
    """同一个 L 形（竖条 + 横条）按画幅等比缩放；前景占画幅比例在各尺寸下相同（≈0.101）。"""
    k = s / 512
    yy, xx = np.mgrid[0:s, 0:s]
    R = lambda y0, y1, x0, x1: (yy >= y0 * k) & (yy < y1 * k) & (xx >= x0 * k) & (xx < x1 * k)
    m = R(150, 370, 150, 220) | R(300, 370, 220, 380)
    img = np.full((s, s, 3), 255.0)
    img[m] = 0
    return img, m


def test_g1_large_canvas_object_not_blocked():
    """G1：1200×1200 上占画幅 0.101 的 L 形不再被面积门误挡（改前 None）。"""
    img, m = _scene(1200)
    frac = int(m.sum()) / float(1200 * 1200)
    assert frac < X.SPLIT_MAX_AREA_FRAC, "构造异常：占比 %r 不应接近整幅" % frac
    assert X._vocab_split_one(m, X.SPLIT_R_MIN, X.SPLIT_MARGIN) is not None, (
        "#678 1200×1200 上占比 %.3f 的普通对象仍被面积门误挡" % frac)
    n = len(X.split_subjects(img)["instances"])
    assert n == 2, "#678 1200×1200 实例数 %r（期望与 512 档一致＝2）" % n


def test_g2_512_behavior_unchanged():
    """G2：512×512 原尺寸行为不变（防过修）——仍切分且实例数为 2。"""
    img, m = _scene(512)
    assert X._vocab_split_one(m, X.SPLIT_R_MIN, X.SPLIT_MARGIN) is not None, (
        "#678 512×512 既有行为被改变（应仍切分）")
    n = len(X.split_subjects(img)["instances"])
    assert n == 2, "#678 512×512 实例数 %r（期望 2）" % n


def test_g3_full_canvas_foreground_still_blocked():
    """G3：真整幅前景（占满画幅，面积 > 0.5·H·W）仍被挡（改前 300×300 漏挡）。"""
    full = np.ones((300, 300), bool)
    assert int(full.sum()) > X.SPLIT_MAX_AREA_FRAC * 300 * 300, "构造异常：非整幅"
    assert X._vocab_split_one(full, X.SPLIT_R_MIN, X.SPLIT_MARGIN) is None, (
        "#678 300×300 整幅前景未被面积上限门挡住")
