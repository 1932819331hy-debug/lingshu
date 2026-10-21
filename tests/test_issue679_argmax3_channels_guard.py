# -*- coding: utf-8 -*-
"""test_issue679_argmax3_channels_guard · argmax3 head 按实际通道数解包（#679）
==========================================================================
缺陷（#679，改前实测复现）：``argmax3`` 的 head 写死 ``reshape(9, k3 + 3)``，
而 ``feat = concat([d3, color])`` 的维数是 ``k3 + C``（``color = lat.mean(axis=(1,2))``
长度＝输入实际通道数 C）⇒ 只要 C≠3，``feat @ head.T`` 直接 ValueError：

    C=1  ValueError: … (size 5 is different from 3)
    C=4  ValueError: … (size 5 is different from 6)

``_layer``（PR #97）已按实际通道数展开，此处是漏改。

修法：head 改用 ``k3 + lat.shape[-1]``（C=3 时长度不变 ⇒ 逐位一致；C≠3 不再崩）。

判据（本件）：
  G1 C=1/2/3/4 的 ``argmax3`` 都返回长度 B 的整数数组（改前 C≠3 抛 ValueError）；
  G2 C=3 的结果与「head 写死 k3+3」的参考实现**逐位一致**（同一 lat/vec，防过修）；
  G3 ``argmax3`` 全仓仍无调用方（只命中定义处）——保持「接线前先修契约」的边界。

运行（仓根）：python -X utf8 -m pytest tests/test_issue679_argmax3_channels_guard.py -q
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from lingshu.nn import hex_nn_preview as P

K1, K2, K3 = 4, 3, 2
B = 2


def _vec_len(C):
    return K1 * 7 + K2 * K1 * 7 + K3 * K2 * 7 + 9 * (K3 + C)


def _make(C, seed=0):
    rng = np.random.default_rng(seed)
    lat = rng.random((B, 8, 8, C))
    vec = rng.standard_normal(_vec_len(C))
    return lat, vec


def _argmax3_ref_head3(lat, vec, k1, k2, k3):
    """参考实现：head 写死 ``k3 + 3``（旧实现，仅 C=3 可用）。"""
    i = 0
    conv1 = vec[i:i + k1 * 7].reshape(k1, 7); i += k1 * 7
    conv2 = vec[i:i + k2 * k1 * 7].reshape(k2, k1, 7); i += k2 * k1 * 7
    conv3 = vec[i:i + k3 * k2 * 7].reshape(k3, k2, 7); i += k3 * k2 * 7
    head = vec[i:].reshape(9, k3 + 3)
    h1 = P._layer(lat, conv1, k1)
    h2 = P._layer(h1, conv2, k2)
    h3 = P._layer(h2, conv3, k3)
    d3 = np.sqrt((h3 ** 2).mean(axis=(1, 2)) + 1e-12)
    color = lat.mean(axis=(1, 2))
    feat = np.concatenate([d3, color], axis=1)
    return (feat @ head.T).argmax(axis=1)


def test_g1_all_channel_counts_run():
    """G1：C=1/2/3/4 都返回长度 B 的整数数组（改前 C≠3 ValueError）。"""
    for C in (1, 2, 3, 4):
        lat, vec = _make(C)
        out = P.argmax3(lat, vec, K1, K2, K3)
        assert out.shape == (B,), "#679 C=%d 返回形状 %r（期望 (%d,)）" % (C, out.shape, B)
        assert np.issubdtype(out.dtype, np.integer), "#679 C=%d 非整数数组" % C


def test_g2_c3_bit_identical_to_hardcoded3():
    """G2：C=3 与「head 写死 3」的参考实现逐位一致（防过修）。"""
    lat, vec = _make(3)
    got = P.argmax3(lat, vec, K1, K2, K3)
    ref = _argmax3_ref_head3(lat, vec, K1, K2, K3)
    assert np.array_equal(got, ref), (
        "#679 C=3 结果与写死 3 的参考实现不一致：%r vs %r" % (got.tolist(), ref.tolist()))


def test_g3_argmax3_has_no_callers():
    """G3：argmax3 在生产源码里仍无调用方（只命中定义处）——不新增接线。

    只扫 ``lingshu/`` 源码树（``-- lingshu/``）：本守卫自身与其它测试件会引用
    ``argmax3``，那不是生产调用方，不能算数。
    """
    root = Path(__file__).resolve().parents[1]
    try:
        r = subprocess.run(["git", "grep", "-n", "argmax3", "--", "lingshu/"],
                           cwd=str(root), capture_output=True, text=True, encoding="utf-8")
    except (OSError, FileNotFoundError):
        import pytest
        pytest.skip("git 不可用，跳过调用方计数")
    if r.returncode != 0:
        import pytest
        pytest.skip("git grep 无输出（可能非 git 检出），跳过")
    defs = [ln for ln in r.stdout.splitlines() if "def argmax3" in ln]
    assert len(defs) == 1, "#679 argmax3 定义处应为 1 个，实得 %r" % defs
    callers = [ln for ln in r.stdout.splitlines() if "def argmax3" not in ln]
    assert not callers, "#679 argmax3 出现生产调用方（本件边界＝无调用方）：%r" % callers
