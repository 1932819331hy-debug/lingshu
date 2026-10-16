# -*- coding: utf-8 -*-
"""test_issue733_subject_split_edt_pad · `hexgen_subject_split` 的 `EDT_PAD` 死常量已随换代删除（#733）
======================================================================================================
缺陷（#733，改前实测复现）：``lingshu/gen/hexgen_subject_split.py`` 里
``EDT_PAD = 64``（注释「距离场补零留白（≥ 词表最大半径 51 ⇒ 腐蚀一定收敛到空）」）
与 ``_ultimate_erosion_seeds`` 的 docstring 段「⇒ 必须**补零留白**（`EDT_PAD`）」双重承诺了
一件实现里**零引用**的事：全仓 ``git grep -n EDT_PAD`` 只有这两处，无任何代码名引用。

**性质判定（本件的关键，先取证再动手）**：这不是「本该接线却被误删」——
  · 消费者读数：仓内 + 源仓（AEIS `experiments/hexgen_subject_split.py`）历史里该名**只有声明 +
    docstring** 两处，从未有调用点 ⇒ 不是「调用点被误删」；
  · 行为读数：现行取法（R318 起的**最终腐蚀**：逐层腐蚀到空、取大分量最多的那层）**不用距离场**，
    因此在「距离场那一版真正会翻车」的输入上照样收敛——紧贴边界（``cy = r - 1``）与**满幅前景**
    （补集为空）都给出非空种子且结果平移不变（见下 G2/G3）。
⇒ 判定为**过期声明**（该常量描述的是已被弃用的 R318 首版实现），最小动作＝删声明、把 docstring
里那段改成「该版需要补零留白、常量已随换代删除」的历史说明。

判据（本件）：
  G1 ``EDT_PAD`` 不得再作为**代码名**出现（AST 扫 ``ast.Name``；注释/docstring 里的文字说明不算）
      —— 回退到缺陷形态（重新声明 ``EDT_PAD = 64``）即红；
  G2 紧贴边界的子图：``_ultimate_erosion_seeds`` 收敛且与**居中**同形掩膜**种子数相同**
      （证明现行实现不需要补零留白）；
  G3 满幅前景（补集为空，距离场那版在此**永不收敛**）：照样给出非空种子 —— 这是「不需要
      ``EDT_PAD``」的决定性行为证据。
正/负对照：
  C1 正对照：居中圆盘读出**恰好 1 个**种子（量具不空转、也不是恒 0）；
  C2 负对照：用「补集膨胀法」直接模拟被弃用的距离场版循环，在**满幅前景**上必须打满上限
      （证明「是否收敛」这把尺子确实能测到「没有外部可腐蚀」这一失效形态）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue733_subject_split_edt_pad.py -q
"""
from __future__ import annotations

import ast
import inspect
import io

import numpy as np

from lingshu.gen import hexgen_c1_real as C
from lingshu.gen import hexgen_subject_split as X


def _disc(shape, cy, cx, r):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return ((yy - cy) ** 2 + (xx - cx) ** 2) <= r * r


def _dilate_complement_loop(m, cap=600):
    """被弃用的 R318 首版循环：`m <- ~dilate(~m)` 直到空。返回 (迭代数, 是否仍非空)。"""
    cur = m.copy()
    n = 0
    while cur.any() and n < cap:
        cur = ~C._dilate(~cur, 1)
        n += 1
    return n, bool(cur.any())


def test_g1_edt_pad_not_a_code_name():
    """G1：`EDT_PAD` 不得作为代码名出现（回退成缺陷形态即红）。"""
    src = io.open(inspect.getfile(X), encoding="utf-8").read()
    names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}
    assert "EDT_PAD" not in names, (
        "#733 EDT_PAD 仍作为代码名存在（死常量未删/未接线）：该常量随 R318 换代已废弃")


def test_g2_boundary_touching_mask_converges_same_as_centered():
    """G2：紧贴边界的子图与居中同形掩膜给出**相同种子数**（现行实现不需要补零留白）。"""
    center = _disc((256, 256), 128, 128, 60)
    touch = _disc((256, 256), 59, 128, 60)        # cy - r = -1 ⇒ 紧贴顶边
    corner = _disc((256, 256), 59, 59, 60)        # 紧贴左上角
    for tag, m in (("center", center), ("top-edge", touch), ("corner", corner)):
        assert int(m.sum()) > 0, "构造异常：%s 掩膜为空" % tag
    sc = X._ultimate_erosion_seeds(center, min_core=X.MIN_CORE)
    st = X._ultimate_erosion_seeds(touch, min_core=X.MIN_CORE)
    sk = X._ultimate_erosion_seeds(corner, min_core=X.MIN_CORE)
    assert len(sc) == len(st) == len(sk) == 1, (
        "#733 紧贴边界掩膜种子数 %r/%r 与居中 %r 不一致（#733 判定现行实现无需补零留白）"
        % (len(st), len(sk), len(sc)))


def test_g3_full_frame_foreground_still_converges():
    """G3：满幅前景（补集为空）照样给出非空种子 —— 决定性证据（距离场那版在此永不收敛）。"""
    full = np.ones((256, 256), dtype=bool)
    assert int(full.sum()) == 256 * 256, "构造异常：不是满幅"
    seeds = X._ultimate_erosion_seeds(full, min_core=X.MIN_CORE)
    assert seeds, "#733 满幅前景下种子为空（现行实现应逐层腐蚀到空并回退到质心）"
    assert len(seeds) == 1, "#733 满幅前景应给 1 个种子，实得 %d" % len(seeds)


def test_c1_positive_control_centered_disc_reads_one_seed():
    """C1 正对照：居中圆盘读出恰好 1 个种子（量具不空转、不恒 0）。"""
    m = _disc((256, 256), 128, 128, 60)
    assert int(m.sum()) > 0, "构造异常：掩膜为空"
    seeds = X._ultimate_erosion_seeds(m, min_core=X.MIN_CORE)
    assert len(seeds) == 1, "正对照失败：居中圆盘种子数 %d" % len(seeds)
    y, x, a = seeds[0]
    assert abs(y - 128) <= 1.0 and abs(x - 128) <= 1.0, (
        "正对照失败：质心 (%r,%r) 偏离 128,128" % (y, x))


def test_c2_negative_control_distance_field_version_never_converges_on_full_frame():
    """C2 负对照：模拟被弃用的距离场版循环，在满幅前景上必须打满上限（不收敛）。"""
    full = np.ones((256, 256), dtype=bool)
    n, still = _dilate_complement_loop(full, cap=600)
    assert n == 600 and still, (
        "负对照失败：满幅前景下补集膨胀循环 %d 步后仍非空=%r（应打满 600 且非空）" % (n, still))
    #   同一把尺子在「有外部可腐蚀」的输入上必须**能**收敛 ⇒ 不是恒红
    m = _disc((256, 256), 128, 128, 60)
    n2, still2 = _dilate_complement_loop(m, cap=600)
    assert n2 < 600 and not still2, (
        "负对照失败：居中圆盘应收敛，实得 %d 步 / 仍非空=%r" % (n2, still2))
