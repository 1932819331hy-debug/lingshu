# -*- coding: utf-8 -*-
"""selfsup_finetune 边界回归 · `epochs <= 0` 不得抛 IndexError（issue #17 遗留项）

缺陷（改前实测，main @ 9dd0beb）：
    `selfsup_finetune(..., epochs=0)` 在收尾构造返回字典时对空列表取
    `loss_curve[-1]` ⇒ `IndexError: list index out of range`。
    `epochs=0` 是**合法入参**（"不训练，只取初始状态"），调用方拿不到读数却拿到崩溃。

改法：`final_loss` 取 `loss_curve[-1] if loss_curve else init_loss`。
`epochs <= 0` 时曲线为空 ⇒ `final_loss` 回落为 `init_loss`——
没有训练就不存在「训练后」的读数，二者取同一个值在语义上是自洽的。

判据（本件）：
  G1 `epochs=0` ⇒ 不抛；`loss_curve` 为空；`final_loss == init_loss`；返回键与训练路径一致；
  G2 `epochs=-1` ⇒ 同 `epochs=0`（与上游 `explore(ticks)` 把负数钳 0 的既有口径一致）；
  G3 `epochs=1` ⇒ 曲线长 1，`final_loss` 仍取自曲线末项（既非 None 也非 init）；
  G4 `epochs=0` 的核必须**逐位等于**输入核（不训练就不该改变核）；
  G5 定点变异（行为级）：把回落式还原成 `loss_curve[-1]` ⇒ G1 的
     「不抛」必红 —— 证明 G1 钉的是这个缺陷本身。

运行（仓根）：
    python -X utf8 -m pytest tests/test_hex_selfsup_epochs_zero.py -q
    python -X utf8 tests/test_hex_selfsup_epochs_zero.py
"""
import os as _os
import sys as _sys

_RP = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # 仓根
_sys.path.insert(0, _RP)

import numpy as np

from lingshu.nn.hex_cnn import DEFAULT_KERNELS, selfsup_finetune

_KNAME, _CELLS, _RATIO, _LR = "center_surround", 24, 0.25, 0.02


def _fixture():
    """固定合成图（含结构，非纯噪声）→ 亮度晶格。与 test_hex_selfsup.py 同款构造。"""
    img = np.zeros((96, 96, 3), dtype=np.uint8)
    img[:, :, :] = 40
    yy, xx = np.mgrid[0:96, 0:96]
    img[(yy - 48) ** 2 + (xx - 48) ** 2 <= 24 ** 2] = (210, 170, 60)
    img[8:28, 8:48] = (60, 190, 120)
    img[60:88, 60:90] = (200, 70, 90)
    img = (img.astype(int)
           + np.random.default_rng(0).integers(0, 12, img.shape)).astype(np.uint8)
    gray = (0.299 * img[..., 0] + 0.587 * img[..., 1]
            + 0.114 * img[..., 2]).astype(np.float32)
    return img


_IMG = _fixture()

#: 训练路径的返回键全集（G1 用来核对退化路径没有少给键）
_KEYS = {"kernel", "kernel_name", "init_loss", "final_loss", "loss_curve",
         "algo", "epochs", "lr"}


def _run(epochs):
    return selfsup_finetune(_IMG, _KNAME, cells_across=_CELLS, epochs=epochs,
                            lr=_LR, mask_ratio=_RATIO, seed=7)


# ==================== G1 `epochs=0` 不崩且读数自洽 ====================

def test_epochs_zero_does_not_raise_and_is_self_consistent():
    res = _run(0)                                      # 改前：IndexError 在此抛出
    assert res["loss_curve"] == [], (
        "epochs=0 不该产生任何曲线点：%r" % (res["loss_curve"],))
    assert res["final_loss"] == res["init_loss"], (
        "epochs=0 时没有「训练后」读数 ⇒ final_loss 应等于 init_loss：final=%r init=%r"
        % (res["final_loss"], res["init_loss"]))
    assert isinstance(res["final_loss"], float), (
        "final_loss 必须是有限 float，不得为 None：%r" % (res["final_loss"],))
    assert np.isfinite(res["final_loss"]), res["final_loss"]
    assert set(res) == _KEYS, (
        "退化路径的返回键须与训练路径一致：缺 %s / 多 %s"
        % (sorted(_KEYS - set(res)), sorted(set(res) - _KEYS)))


# ==================== G2 负数同 0 ====================

def test_negative_epochs_behave_like_zero():
    r0, rn = _run(0), _run(-1)
    assert rn["loss_curve"] == [], rn["loss_curve"]
    assert rn["final_loss"] == rn["init_loss"] == r0["init_loss"], (
        "epochs=-1 应与 epochs=0 同形（上游 explore(ticks) 对负数即钳 0）：%r vs %r"
        % (rn, r0))


# ==================== G3 正常路径不缩水 ====================

def test_positive_epochs_still_take_final_from_curve():
    """防过修：`epochs>=1` 时 final_loss 必须仍取自曲线末项。"""
    r1 = _run(1)
    assert len(r1["loss_curve"]) == 1, r1["loss_curve"]
    assert r1["final_loss"] == round(r1["loss_curve"][-1], 5), (
        "epochs=1 的 final_loss 应取自曲线末项：%r vs %r"
        % (r1["final_loss"], r1["loss_curve"][-1]))
    r3 = _run(3)
    assert len(r3["loss_curve"]) == 3, r3["loss_curve"]
    assert r3["final_loss"] == round(r3["loss_curve"][-1], 5)


# ==================== G4 不训练就不改核 ====================

def test_epochs_zero_leaves_kernel_untouched():
    res = _run(0)
    w0 = DEFAULT_KERNELS[_KNAME]
    assert res["kernel"].shape == w0.shape, (res["kernel"].shape, w0.shape)
    assert np.array_equal(res["kernel"], w0), (
        "epochs=0 表示零次更新 ⇒ 核必须逐位等于输入核（max|Δ|=%.3g）"
        % float(np.max(np.abs(res["kernel"] - w0))))


# ==================== G5 定点回退守卫 ====================

def test_guard_is_discriminating():
    """源码级定点守卫：`final_loss` 的取法必须**带空曲线回落**。

    改前是 `round(loss_curve[-1], 5)`，改后是
    `round(loss_curve[-1] if loss_curve else init_loss, 5)`。
    把回落式还原 ⇒ 本断言必红。

    为什么用源码级断言而不是「构造空曲线试一下」：后者只能重述
    `list[-1] on []` 抛 IndexError 这条 Python 语义，**与本次修复无关**
    （初版就是这么写的，实为一个恒真的空断言）。源码级断言钉的是
    「这一行到底长什么样」，是回退唯一的、直接的判别面。
    """
    import inspect

    from lingshu.nn import hex_cnn

    src = inspect.getsource(hex_cnn.selfsup_finetune)
    line = [ln.strip() for ln in src.splitlines() if '"final_loss"' in ln]
    assert len(line) == 1, (
        "selfsup_finetune 里应恰有 1 行写 final_loss，实际 %d 行：%r" % (len(line), line))
    expr = line[0]
    assert "if loss_curve else" in expr, (
        "final_loss 的取法缺少空曲线回落 ⇒ epochs<=0 会重新变成 IndexError：%s" % expr)
    assert "loss_curve[-1]" in expr, (
        "回落不能以牺牲训练路径为代价（epochs>=1 仍须取曲线末项）：%s" % expr)


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
