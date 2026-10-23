# -*- coding: utf-8 -*-
"""#593 守卫 · `selfsup_finetune` 的 `init_loss` / `final_loss` 必须同口径（同一评估掩码）

## 判据来源

issue #593（作者 Yinfeng818；本端值守判定「成立」，基线 `55a137fc`）：
`lingshu/nn/hex_cnn.py` 的 `selfsup_finetune` 里

* `init_loss` 在**训练前**抽的掩码 A 下取值；
* `final_loss` 取 `loss_curve[-1]`，即**末 epoch 训练时**抽的掩码 B 下的读数；
* docstring 与 #591 补的 `epochs<=0` 语义（「没有训练就不存在『训练后』的读数」）
  把两者定义为**前后读数** ⇒ 本该**同口径**。实测掩码 A 占比 0.2434、掩码 B 占比
  0.2566，重叠 127 vs 独立抽样期望 122.9 ⇒ 两个掩码是**独立抽样**（读数不可比）。

## 实现侧口径（本件修法）

评估掩码在训练前**抽一次**（就复用 #593 之前 `init_loss` 抽的那一次 ⇒ 训练用的
每-epoch 掩码流、核、`loss_curve` 逐位不变），`init_loss` 与 `final_loss` 都在该
掩码下取值；`loss_curve` 仍是**各 epoch 训练掩码下**的曲线（口径不变）。

## 为什么这样判

* 「前后读数」这一语义要求两个读数**只在被测量对象（核）上不同**，掩码必须相同；
  否则差值里混进「换掩码带来的损失波动」——正是 #593 实测 final 偏差 +51.6% 的来源。
* 判据分两层：**行为层**（用探针记录被传入 `recon_loss` 的掩码并比对，②）与
  **参照系层**（用独立复算的训练回路钉住「训练掩码流/核/曲线逐位不变」，④⑤）。
  前者杀「读数取法回退」（M1/M2），后者杀「过修——把训练掩码也固定」（M3）。
* 不用源码级断言（#591 的 `test_hex_selfsup_epochs_zero.py::test_guard_is_discriminating`
  是源码级）：本件的两条性质都可**行为级**观测，行为级判据不随重构漂移。

运行（仓根）：
    python -X utf8 -m pytest tests/test_issue593_selfsup_loss_same_mask.py -q
"""
import inspect
import os as _os
import sys as _sys

_RP = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # 仓根
_sys.path.insert(0, _RP)

import numpy as np

from lingshu.nn import hex_cnn
from lingshu.nn.hex_cnn import (DEFAULT_KERNELS, hex_conv, image_to_grid,
                                selfsup_finetune)

_KNAME = "center_surround"
_CELLS = 16
_RATIO = 0.25
_EPOCHS = 4
_LR = 0.05
_SEED = 7
_KEYS = {"kernel", "kernel_name", "init_loss", "final_loss", "loss_curve",
         "algo", "epochs", "lr"}


def _fixture():
    """固定合成图（含结构，非纯噪声）→ 亮度晶格。与 tests/test_hex_selfsup.py 同构造。"""
    img = np.zeros((96, 128, 3), dtype=np.uint8)
    img[:, :, :] = 40
    yy, xx = np.mgrid[0:96, 0:128]
    img[(yy - 48) ** 2 + (xx - 64) ** 2 <= 28 ** 2] = (210, 170, 60)
    img[10:30, 10:60] = (60, 190, 120)
    img[60:88, 70:120] = (200, 70, 90)
    img = (img.astype(int)
           + np.random.default_rng(0).integers(0, 12, img.shape)).astype(np.uint8)
    gray = (0.299 * img[..., 0] + 0.587 * img[..., 1]
            + 0.114 * img[..., 2]).astype(np.float32)
    # #351：实现侧亮度已归一到 0..1，独立复算必须同口径（否则 init_loss 差 255² 倍）。
    return img, image_to_grid(gray[..., None].repeat(3, axis=2), _CELLS)[0][..., :1] / 255.0


_IMG, _LUM = _fixture()


def _loss(wv, hole):
    """与实现侧 `recon_loss` 同式的独立复算（同掩码、同 MSE 口径）。"""
    m = _LUM.copy()
    m[hole] = 0.0
    pred = hex_conv(m, wv)[..., :1]
    return float(((pred[hole] - _LUM[hole]) ** 2).mean())


def _run(epochs=_EPOCHS, lr=_LR, ratio=_RATIO, seed=_SEED):
    return selfsup_finetune(_IMG, _KNAME, cells_across=_CELLS, epochs=epochs,
                            lr=lr, mask_ratio=ratio, seed=seed)


def _run_spied(epochs=_EPOCHS, lr=_LR, ratio=_RATIO, seed=_SEED):
    """跑一次并记录**每一次**传给 `recon_loss` 的掩码（按 `hex_conv` 收到的
    `masked` 输入还原：实现侧 `masked = lum.copy(); masked[hole] = 0.0`，
    而本 fixture 的亮度晶格无零值 ⇒ `masked == 0` 即该次调用的掩码）。

    返回 (结果, 掩码列表, 掩码形状)。
    """
    assert (_LUM > 0).all(), "fixture 亮度晶格含零值 ⇒ `masked==0` 不再是掩码的唯一判据"
    seen = []
    orig = hex_cnn.hex_conv

    def _spy(feature, kernel7, channels=None):
        seen.append(np.array(feature, copy=True))
        return orig(feature, kernel7, channels)

    hex_cnn.hex_conv = _spy
    try:
        res = _run(epochs=epochs, lr=lr, ratio=ratio, seed=seed)
    finally:
        hex_cnn.hex_conv = orig
    holes = [(m == 0.0)[..., 0] for m in seen]
    return res, holes, seen


def _reference_loop(w0, holes, lr):
    """独立复算的训练回路：给定**显式**的每-epoch 掩码序列，返回 (核, 曲线)。

    与实现侧逐步同式（中心差分 7 参数 / 步长 1e-3 / 步后归一化），
    但掩码由调用方给死 ⇒ 可用来钉「训练用的是哪一组掩码」。
    """
    w = np.array(w0, dtype=np.float32).copy()
    curve = []
    for hole in holes:
        grads = np.zeros(7, dtype=np.float32)
        eps = 1e-3
        for i in range(7):
            wp = w.copy(); wp[i] += eps
            wm = w.copy(); wm[i] -= eps
            grads[i] = (_loss(wp, hole) - _loss(wm, hole)) / (2 * eps)
        w -= lr * grads
        w /= (abs(w).sum() + 1e-8)
        curve.append(_loss(w, hole))
    return w, curve


def _prefix_training_holes(epochs=_EPOCHS, ratio=_RATIO, seed=_SEED):
    """改前实现里训练用的那 `epochs` 个掩码：`default_rng(seed)` 的第 2..epochs+1 次抽样。

    改前第 1 次抽样是 `init_loss` 用的掩码 A，其后每次循环抽一个（末次即掩码 B）。
    本件修法把**第 1 次抽样**提为评估掩码并**不额外消耗** `rng` ⇒ 训练掩码流不变。
    """
    rng = np.random.default_rng(seed)
    rng.random(_LUM.shape[:2])                       # 改前的掩码 A（= 本件的评估掩码）
    return [rng.random(_LUM.shape[:2]) < ratio for _ in range(epochs)]


# ==================== ① 同 seed 两次调用逐位一致 ====================

def test_same_seed_two_calls_bit_identical():
    """① 同 seed 两次调用 ⇒ `init_loss` / `final_loss`（及核、曲线）逐位一致。

    口径可复算的下限判据：读数必须是 `(seed, 参数)` 的确定函数，不得掺入
    「哪一次调用」这类不可复算的状态。
    """
    a, b = _run(), _run()
    assert a["init_loss"] == b["init_loss"], (a["init_loss"], b["init_loss"])
    assert a["final_loss"] == b["final_loss"], (a["final_loss"], b["final_loss"])
    assert np.array_equal(a["kernel"], b["kernel"]), "同 seed 的核必须逐位一致"
    assert a["loss_curve"] == b["loss_curve"], "同 seed 的曲线必须逐位一致"


# ==================== ② init / final 的评估掩码同一 ====================

def test_init_and_final_use_identical_eval_mask():
    """② 探针判据（本件的**核心判别面**）：`init_loss` 与 `final_loss` 的评估掩码同一。

    实现侧调用序：第 1 次 `hex_conv` 调用是训练前的 `init_loss`；末 1 次是训练后的
    `final_loss` 重算。两者收到的 `masked` 输入必须**逐位相同**（同掩码 ⇒ 同输入）。

    缺陷态（`final_loss = loss_curve[-1]`）与「评估掩码每-epoch 重抽」两种回退下，
    末次调用的掩码都是末 epoch 的训练掩码 ≠ 首次调用的掩码 ⇒ 本断言必红。
    """
    res, holes, seen = _run_spied()
    # 调用序：1 次 init + 每 epoch（7×2 次差分 + 1 次曲线记录）共 15 次 + 1 次 final 重算
    assert len(seen) == 1 + _EPOCHS * 15 + 1, (
        "调用次数不符（1 次 init + 每 epoch 7×2 次差分 + 1 次曲线记录 + 1 次 final 重算）："
        "%d" % len(seen))
    assert holes[0].any(), "init 的掩码不得为空（否则断言空转）"
    assert np.array_equal(holes[0], holes[-1]), (
        "init_loss 与 final_loss 的评估掩码必须同一：首末两次 recon_loss 收到的掩码"
        "重叠 %d / 各自 %d、%d" % (np.count_nonzero(holes[0] & holes[-1]),
                                   np.count_nonzero(holes[0]),
                                   np.count_nonzero(holes[-1])))
    # 两个读数必须**确实是**这两个掩码下的读数（同式独立复算）
    w0 = DEFAULT_KERNELS[_KNAME]
    assert res["init_loss"] == round(_loss(w0, holes[0]), 5), (
        "init_loss 与「评估掩码 × 输入核」下的复算不符：%r vs %r"
        % (res["init_loss"], round(_loss(w0, holes[0]), 5)))
    assert res["final_loss"] == round(_loss(res["kernel"], holes[-1]), 5), (
        "final_loss 与「评估掩码 × 训练后核」下的复算不符：%r vs %r"
        % (res["final_loss"], round(_loss(res["kernel"], holes[-1]), 5)))


# ==================== ③ `epochs<=0` 语义不变（#591） ====================

def test_epochs_zero_and_negative_fall_back_to_init():
    """③ 没有训练 ⇒ 不存在「训练后」读数：`final_loss == init_loss`、曲线为空、核不变。"""
    for ep in (0, -1):
        r = _run(epochs=ep)
        assert r["loss_curve"] == [], (ep, r["loss_curve"])
        assert r["final_loss"] == r["init_loss"], (
            "epochs=%d 时 final_loss 应等于 init_loss：%r vs %r"
            % (ep, r["final_loss"], r["init_loss"]))
        assert np.array_equal(r["kernel"], DEFAULT_KERNELS[_KNAME]), (
            "epochs=%d 表示零次更新 ⇒ 核必须逐位等于输入核" % ep)
        assert set(r) == _KEYS, (ep, sorted(set(r) ^ _KEYS))


# ==================== ④ `loss_curve` 仍是训练掩码下的曲线 ====================

def test_loss_curve_is_still_training_mask_curve():
    """④ 正对照（防过修）：曲线长度 == `epochs`，且**逐位等于**用改前那组训练掩码
    独立复算出的曲线（曲线口径未被动过）。"""
    res = _run()
    assert len(res["loss_curve"]) == _EPOCHS, len(res["loss_curve"])
    assert res["loss_curve"], "曲线不得为空（否则本断言空转）"
    _, ref_curve = _reference_loop(DEFAULT_KERNELS[_KNAME],
                                   _prefix_training_holes(), _LR)
    assert res["loss_curve"] == ref_curve, (
        "loss_curve 必须仍是各 epoch 训练掩码下的曲线（逐位）：\n  实现 %r\n  复算 %r"
        % (res["loss_curve"], ref_curve))


# ==================== ⑤ 训练路径逐位不变（最关键的正对照） ====================

def test_training_path_bit_identical_to_prefix_masks():
    """⑤ 正对照（最关键）：训练用的每-epoch 掩码**仍是改前那组**（`default_rng(seed)`
    的第 2..epochs+1 次抽样）⇒ 同 seed 下 `kernel` 与改前逐位一致。

    这是「只改评估掩码、没动训练」的可执行形式：若把训练掩码也改成固定的评估掩码
    （过修 M3），或改用独立 rng 流导致训练掩码流整体位移，本断言必红。
    """
    res, holes, _ = _run_spied()
    prefix = _prefix_training_holes()
    assert holes, "探针未记录到任何掩码"
    for k, ref in enumerate(prefix):
        # 每个 epoch 的第 1 次调用（7×2 次差分里的第 1 次）带着该 epoch 的掩码
        got = holes[1 + k * 15]
        assert np.array_equal(got, ref), (
            "第 %d 个 epoch 的训练掩码已偏离改前掩码流（过修/位移）：重叠 %d vs %d"
            % (k, np.count_nonzero(got & ref), np.count_nonzero(ref)))
    ref_w, _ = _reference_loop(DEFAULT_KERNELS[_KNAME], prefix, _LR)
    assert np.array_equal(res["kernel"], ref_w), (
        "同 seed 下 kernel 必须与改前逐位一致（max|Δ|=%.3g）"
        % float(np.max(np.abs(res["kernel"] - ref_w))))


# ==================== ⑥ 极值 mask_ratio 下 ① 仍成立 ====================

def test_extreme_mask_ratios_keep_mask_identity():
    """⑥ 大/小 `mask_ratio` 两个极值下，评估掩码同一 + 同 seed 可复算仍成立。"""
    for ratio in (0.05, 0.9):
        res, holes, _ = _run_spied(epochs=2, ratio=ratio)
        assert holes[0].any() and holes[-1].any(), (
            "mask_ratio=%s 下掩码不得为空（否则断言空转）" % ratio)
        assert np.array_equal(holes[0], holes[-1]), (
            "mask_ratio=%s：init/final 的评估掩码不同一（重叠 %d / %d、%d）"
            % (ratio, np.count_nonzero(holes[0] & holes[-1]),
               np.count_nonzero(holes[0]), np.count_nonzero(holes[-1])))
        a, b = _run(epochs=2, ratio=ratio), _run(epochs=2, ratio=ratio)
        assert a["init_loss"] == b["init_loss"] and a["final_loss"] == b["final_loss"], (
            "mask_ratio=%s：同 seed 两次调用读数不一致：%r/%r vs %r/%r"
            % (ratio, a["init_loss"], a["final_loss"], b["init_loss"], b["final_loss"]))


# ==================== ⑦ 签名与返回体契约不变 ====================

def test_signature_and_return_contract_unchanged():
    """⑦ 防「顺手改签名/返回键」：参数名序与默认值、返回键全集都不变。"""
    sig = inspect.signature(selfsup_finetune)
    assert list(sig.parameters) == ["img_arr", "kernel_name", "cells_across",
                                    "epochs", "lr", "mask_ratio", "seed"], list(sig.parameters)
    dflt = {k: v.default for k, v in sig.parameters.items() if v.default is not inspect._empty}
    assert dflt == {"kernel_name": "center_surround", "cells_across": 48,
                    "epochs": 30, "lr": 0.05, "mask_ratio": 0.25, "seed": 7}, dflt
    res = _run(epochs=1)
    assert set(res) == _KEYS, sorted(set(res) ^ _KEYS)
    assert isinstance(res["init_loss"], float) and isinstance(res["final_loss"], float)
    assert np.isfinite(res["init_loss"]) and np.isfinite(res["final_loss"])


if __name__ == "__main__":
    import traceback

    _failed = 0
    for _name in sorted(n for n in list(globals()) if n.startswith("test_")):
        try:
            globals()[_name]()
            print("  PASS %s" % _name)
        except AssertionError as exc:
            _failed += 1
            print("  FAIL %s: %s" % (_name, exc))
            traceback.print_exc()
    print("\n%s — %d failed" % ("FAILED" if _failed else "OK", _failed))
    _sys.exit(1 if _failed else 0)
