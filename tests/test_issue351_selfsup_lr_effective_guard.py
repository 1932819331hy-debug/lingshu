# -*- coding: utf-8 -*-
"""#351 守卫：`selfsup_finetune` 的 `lr` 必须真的改变训练轨迹。

缺陷面（公开 issue #351）：亮度保持 0..255 量纲 ⇒ 重建残差 ~1e4、数值梯度 ~1e4+，
而核元素 |w|~O(1)；`w -= lr*grad` 后 `lr*grad ≫ |w|`，紧接着的 L1 归一化把旧核整段
抹掉 ⇒ 每步核只由 `-grad/‖grad‖₁` 决定，**lr 跨 5000 倍核几乎不动**。

本件把亮度归一到 0..1（与 `hex_train.images_to_lattices` 同口径），守卫锁：

  ① lr 敏感性：lr ∈ {1e-3, 0.05, 0.5} 学出的核两两不同（1e-3 vs 0.5 的最大逐元差 > 2e-2）；
  ② 默认 lr 仍能降损：同一评估掩码下 `final_loss < init_loss`（不是「lr 变了但不学了」）；
  ③ 白箱约束仍在：核 L1 归一化；
  ④ 反面对照：lr=0 ⇒ 核与输入核逐位相同（训练步为零，归一化不改变已归一化的核）。
"""
import numpy as np
import pytest

from lingshu.nn.hex_cnn import DEFAULT_KERNELS, selfsup_finetune

CELLS = 32
EPOCHS = 10
KNAME = "center_surround"


def _img():
    yy, xx = np.mgrid[0:120, 0:160]
    img = np.zeros((120, 160, 3), np.uint8)
    img[..., 0] = (128 + 100 * np.sin(xx / 7.0) * np.cos(yy / 9.0)).astype(np.uint8)
    img[..., 1] = img[..., 0]
    img[..., 2] = (xx + yy) % 256
    return img


def _run(lr):
    return selfsup_finetune(_img(), kernel_name=KNAME, cells_across=CELLS,
                            epochs=EPOCHS, lr=lr, seed=7)


# ---------------------------------------------------------------- ① lr 敏感

def test_lr_changes_the_learned_kernel():
    k_small = _run(1e-3)["kernel"]
    k_mid = _run(0.05)["kernel"]
    k_big = _run(0.5)["kernel"]
    d_small_big = float(np.abs(k_big - k_small).max())
    assert d_small_big > 2e-2, (
        "lr 跨 500 倍后核几乎不动（lr 仍无效）：max|k(0.5)-k(1e-3)|=%.4g" % d_small_big)
    assert float(np.abs(k_mid - k_small).max()) > 1e-3, "lr 0.05 与 1e-3 学出的核不应相同"
    assert float(np.abs(k_big - k_mid).max()) > 1e-3, "lr 0.5 与 0.05 学出的核不应相同"


# ---------------------------------------------------------------- ② 默认 lr 仍收敛

def test_default_lr_still_reduces_loss():
    out = _run(0.05)
    assert out["final_loss"] < out["init_loss"], (
        "lr 恢复真实步长后默认值仍须能降损：init=%r final=%r"
        % (out["init_loss"], out["final_loss"]))
    assert out["final_loss"] < 1.0, (
        "归一化后损失应在 0..1 量纲（实读 %r；若仍在 1e4 量纲说明亮度未归一）"
        % (out["final_loss"],))


# ---------------------------------------------------------------- ③ 白箱约束

def test_kernel_stays_normalized():
    out = _run(0.05)
    assert abs(float(np.abs(out["kernel"]).sum()) - 1.0) < 1e-5


# ---------------------------------------------------------------- ④ lr=0 反面对照

@pytest.mark.parametrize("lr", [0.0])
def test_zero_lr_keeps_initial_kernel(lr):
    out = _run(lr)
    w0 = DEFAULT_KERNELS[KNAME].copy()
    # lr=0 ⇒ 训练步为零；实现每步仍做一次 L1 归一化，故期望值是「输入核归一化后」的形态。
    expected = w0 / (np.abs(w0).sum() + 1e-8)
    assert np.allclose(out["kernel"], expected, atol=1e-6), (
        "lr=0 时核不得偏离「输入核归一化」：%r vs %r" % (out["kernel"], expected))
    # final_loss 与 init_loss 可以不同：实现每步仍做一次 L1 归一化（lr=0 只把输入核
    # 归一化一次），故这里不锁「两读数相等」，只锁「核没有被梯度推动」。
