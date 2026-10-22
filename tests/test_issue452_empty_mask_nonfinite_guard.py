# -*- coding: utf-8 -*-
"""#452 守卫：空掩码不得把 NaN 写进卷积核（两条自监督入口）。

缺陷面：`pretrain_selfsup` / `selfsup_finetune` 在随机掩码恰好为空时，
对空数组求 `((pred[hole]-target[hole])**2).mean()` ⇒ NaN；有限差分把 NaN
写进核参数，随后整个前向输出 NaN。修复后空掩码步按 0 损失处理（不更新），
且 ratio>0 时保证至少遮一个 cell。
"""
import numpy as np

from lingshu.nn.hex_cnn import selfsup_finetune
from lingshu.nn.hex_train import HexNet, images_to_lattices, pretrain_selfsup


def _lattice():
    img = np.full((16, 16, 3), 128, dtype=np.uint8)
    x = images_to_lattices(img[None], cells_across=3)
    assert x.shape == (1, 3, 3, 1)
    return x


def test_pretrain_selfsup_all_seeds_finite():
    x = _lattice()
    for seed in range(30):
        net = HexNet(seed=7)
        pretrain_selfsup(net, x, seed=seed)
        assert np.isfinite(net.get_vec()).all(), seed
        assert np.isfinite(net.forward(x)[0]).all(), seed


def test_pretrain_selfsup_zero_ratio_finite():
    x = _lattice()
    net = HexNet(seed=7)
    result = pretrain_selfsup(net, x, mask_ratio=0.0)
    assert np.isfinite(net.get_vec()).all()
    assert np.isfinite(result["init"]) and np.isfinite(result["final"])


def test_selfsup_finetune_default_finite():
    img = np.full((16, 16, 3), 128, dtype=np.uint8)
    result = selfsup_finetune(img, cells_across=3)
    assert np.isfinite(result["init_loss"])
    assert np.isfinite(result["final_loss"])
    assert np.isfinite(result["kernel"]).all()


def test_selfsup_finetune_zero_ratio_finite():
    img = np.full((16, 16, 3), 128, dtype=np.uint8)
    result = selfsup_finetune(img, cells_across=3, mask_ratio=0.0)
    assert np.isfinite(result["init_loss"])
    assert np.isfinite(result["final_loss"])
    assert np.isfinite(result["kernel"]).all()
