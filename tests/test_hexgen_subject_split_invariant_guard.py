# -*- coding: utf-8 -*-
"""test_hexgen_subject_split_invariant_guard · #219 守卫（lingshu/gen/hexgen_subject_split.py）
============================================================================================
缺陷（triage 判「成立」，取证 `hexgen_subject_split.py:454` 与 `:458`）：
    模块头 R319 段声明「`n_proposed`（提议数）与 `n_clusters`（簇数）一并输出，构成
    **簇数 ≤ 实例数 ≤ 提议数** 的不确定带」。修复前 `diag["n_clusters"] = int(n_d)`
    取的是 `cluster_of_pixels` 的**全簇数**，而紧随其后的 `if sel.sum() < min_px: continue`
    会把小于 `MIN_PX`（`hexgen_c1_real.MIN_PX = 50`）的碎屑簇**整个跳过**——它既不产出提议、
    也不产出实例，却照旧被计入簇数 ⇒ 声明被自己的输出打破。
    R321 引入切分遍后，第二段同样失效：切分把 1 个实例拆成 ≥2 个（`_vocab_split_pass`
    每次切分事件净 +1 实例），而 `n_proposed` 只在簇内提议时累加 ⇒ 「实例数 ≤ 提议数」也被打破。

修复（最小）：
    · `n_clusters` 改为**参与聚类**的簇数——在 `min_px` 门**之后**逐簇累加（与实例/提议同口径）；
    · 切分遍后 `n_proposed += n_split_events`（每次切分净 +1 实例，上界同步）。

判据来源：模块自身 docstring 的声明（`hexgen_subject_split.py` 头部 R319 段「簇数 ≤ 实例数
≤ 提议数」）＋ 代码语义（`min_px` 门滤掉的簇不产出任何实例/提议；切分事件净增实例）。
无对应理论章节（本模块为 R318–R321 经验增量，判据即其自带声明）⇒ 属**经验标定**。

断言组（抽掉修复即红）：
  G1 「碎屑簇不参与」：小于 `MIN_PX` 的簇被跳过 ⇒ 该簇**不得**计入 `n_clusters`；
     独立复算「参与簇数」（`fg_mask` + `cluster_of_pixels` + `min_px` 门）并逐值比对，
     且断言该图**确实**含被滤掉的簇（否则断言恒真、量具失效）。
  G2 不确定带成立：G1 图与纯碎屑图、多簇图上的 `n_clusters ≤ n_instances ≤ n_proposed`。
  G3 切分遍不破上界：构造一个**确实触发** `n_split_events ≥ 1` 的连通掩膜（单簇单提议），
     断言 `n_instances ≤ n_proposed`（修复前读 2 ≤ 1 ⇒ 红）。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_hexgen_subject_split_invariant_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.gen import hexgen_subject_split as X     # noqa: E402
from lingshu.gen import hexgen_c1_real as C           # noqa: E402
from lingshu.gen import hexgen_self_source as S       # noqa: E402

SIZE = X.SIZE


def _img(shapes, bg=(20, 20, 20), fg=(230, 230, 230)):
    """按 (shape, cy, cx, rad) 列表合成一张掩膜图（解析原型，确定性）。"""
    img = np.zeros((SIZE, SIZE, 3), np.float64)
    img[:, :] = bg
    for sh, cy, cx, rad in shapes:
        img[S.shape_mask_v3(sh, SIZE, cx, cy, rad) > 0.5] = fg
    return img


def _participating_clusters(img, min_px=C.MIN_PX):
    """独立复算「参与簇数」：前景 → 物体簇（d=6）→ 过 `min_px` 门者。"""
    raw, _ = C.fg_mask(img, tol=C.TOL)
    lab, n = C.cluster_of_pixels(raw, 6)
    return sum(1 for c in range(n)
               if int(((lab == c) & raw).sum()) >= min_px), n


# 碎屑（r=3 ⇒ ~28px，< MIN_PX=50）与主体（r=60）相距很远 ⇒ 两个独立簇，其一被门滤掉。
TINY_PLUS_BIG = [("circle", 120, 120, 3), ("circle", 300, 300, 60)]
ONLY_TINY = [("circle", 120, 120, 3)]
# 连通单簇 + 单种子 + 切分遍确实触发（R321 判据；下方 G3 断言 n_split_events ≥ 1）。
SPLIT_CASE = [("diamond", 256, 256, 94), ("diamond", 158, 329, 94)]


def test_g1_tiny_cluster_is_not_counted_as_cluster():
    """★ 回归：小于 MIN_PX 的簇被跳过 ⇒ 不得计入 n_clusters（旧实现计全簇数）。"""
    img = _img(TINY_PLUS_BIG)
    part, n_raw = _participating_clusters(img)
    # 量具自检：该图必须**确实**含一个被 min_px 门滤掉的簇（否则断言恒真）。
    assert n_raw == 2 and part == 1, (
        "夹具失效：期望 raw 簇 2 / 参与簇 1，实读 %d / %d" % (n_raw, part))

    diag = X.split_subjects(img)["diag"]
    assert diag["n_clusters"] == part, (
        "n_clusters 仍计入了被 MIN_PX 门滤掉的碎屑簇：diag=%r（参与簇数应为 %d）"
        % (diag, part))


def test_g2_uncertainty_band_holds():
    """★ 回归：`簇数 ≤ 实例数 ≤ 提议数` 的不确定带在三种图上都成立。"""
    for name, shapes in (("tiny+big", TINY_PLUS_BIG),
                         ("only tiny", ONLY_TINY),
                         ("two big", [("circle", 250, 250, 60), ("circle", 250, 340, 60)])):
        img = _img(shapes)
        part, _ = _participating_clusters(img)
        d = X.split_subjects(img)["diag"]
        assert d["n_clusters"] == part, (name, d, part)
        assert d["n_clusters"] <= d["n_instances"], (name, d)
        assert d["n_instances"] <= d["n_proposed"], (name, d)


def test_g3_split_pass_does_not_break_upper_bound():
    """★ 回归：切分遍把 1 实例拆成 ≥2 ⇒ `n_proposed` 必须同步（否则 实例数 > 提议数）。"""
    img = _img(SPLIT_CASE)
    d = X.split_subjects(img)["diag"]
    # 夹具自检：本图必须**确实**触发切分（否则本断言抓不到 #219 的切分面）。
    assert d["n_split_events"] >= 1, (
        "夹具失效：本图未触发切分遍（n_split_events=%d），上界回归无从检验" % d["n_split_events"])
    assert d["n_instances"] <= d["n_proposed"], (
        "切分遍抬高了实例数而 n_proposed 未同步：diag=%r" % (d,))


if __name__ == "__main__":
    fns = [test_g1_tiny_cluster_is_not_counted_as_cluster,
           test_g2_uncertainty_band_holds,
           test_g3_split_pass_does_not_break_upper_bound]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as exc:
            failed += 1
            print("FAIL %s | %s" % (fn.__name__, exc))
    print("-- %d passed, %d failed --" % (len(fns) - failed, failed))
    sys.exit(1 if failed else 0)
