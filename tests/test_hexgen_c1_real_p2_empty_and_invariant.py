# -*- coding: utf-8 -*-
"""test_hexgen_c1_real_p2_empty_and_invariant · P2 两件守卫（lingshu/gen/hexgen_c1_real.py）
==========================================================================================
本文件钉住同一文件的另两处缺陷（与既有 test_hexgen_c1_real_gap_and_baseline.py /
test_hexgen_c1_real_p1_radial_rgb_count.py 覆盖的条目无重叠）：

#337 `evaluate` 收到空 id 集即 IndexError
    取证：修复前 `lingshu/gen/hexgen_c1_real.py:1124 maj_s = Counter(it["shape"] for it in
    items).most_common(1)[0][0]`、`:1125 maj_c = ...`——`items` 为空（`ids` 与
    `measured["items"]` 无交集）时 `Counter().most_common(1)` 返回 `[]`，裸 `[0][0]` 抛
    `IndexError: list index out of range`。**可达**：`main` 对 calib/hold/loo 三个 tag 各调一次
    `evaluate`（`:1214-1217`），`split_ids`（`:606-615`）按形状分层交替切分，件数极少或某类只有
    1 件时可给出**空的一半**；留一法传入的 id 集亦可能不含任何有真值的件。
    判据＝同函数既有口径（纪律 ④）：`:1135-1136 maj_p = ...most_common(1); maj_ = maj_p[0][0]
    if maj_p else None`、`:1140/:1154 maj_n` 同一写法——无样本时基线**无定义**，如实报 `None`。

#433 `fit` 的 `EXTRA_COMP_GRID` 选格循环把**循环不变量**当循环变量
    取证：修复前 `:803 for fr in EXTRA_COMP_GRID:` … `:808 cl = [t for t in
    objects_of(it["img"], tol=TOL, d=measured.get("d", 4))[0]]`——`objects_of` 的实参
    `(img, tol, d)` **不含 `fr`** ⇒ 它与 `fr` 无关，是选格循环的**循环不变量**；写在 `fr`
    循环内层使每张标定图被**重复测量 `len(EXTRA_COMP_GRID)` 次**（标定 37 件 ⇒ 148 次
    前景/聚类/补洞，而非 37 次），代价 ×4 而读数一字不变。判据＝Python 循环语义（不变量应提到
    循环外）+ 同函数语义「`fr` 只进 `count_objects`，不进 `objects_of`」。
    断言**不靠**「跑得快」而是钉住调用次数与读数等价（见下），且夹具让 `cgrid` 四个候选值**互不
    相同**（否则「每格重测」与「测一次复用」在读数上不可分，断言抓不到回归）。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_hexgen_c1_real_p2_empty_and_invariant.py -q --no-header
"""
from __future__ import annotations

import os
import sys
from collections import Counter

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.gen import hexgen_c1_real as C          # noqa: E402


# ==================== #337：空 id 集不得抛 IndexError ====================
def _mk(iid, shape, color, n, pat="plain", img=None):
    """一件**完整**真值（含 shape/color，与 `parse_prompt` 的产出同键）的合成件。"""
    if img is None:
        img = np.full((48, 48, 3), 250.0, dtype=np.float64)
    return {"id": iid, "shape": shape, "color": color, "img": img,
            "truth": {"n": n, "pattern": pat, "cells": None,
                      "shape": shape, "color": color}}


def test_evaluate_survives_empty_id_set():
    """A 组（#337）：空 id 集（含「ids 与 items 不相交」）必须正常返回，基线如实报 None。"""
    #   A1：完全空的 measured
    r = C.evaluate({"items": [], "by_id": {}, "d": 4}, {}, set(), tag="empty")
    assert r["n_img"] == 0 and r["n_obj"] == 0, f"空集读数 {r['n_img']}/{r['n_obj']}"
    assert r["baseline_shape"] is None and r["baseline_color"] is None, (
        f"无样本时基线应报 None（与 :1136/:1154 口径一致），实得 "
        f"{r['baseline_shape']!r}/{r['baseline_color']!r}")
    assert r["count_rate"] == 0.0 and r["baseline_count"] == [None, None]

    #   A2：measured 非空但 ids 选不中任何件（留出/留一法可达的形态）
    m = {"items": [_mk(1, "circle", "red", 1)], "by_id": {1: []}, "d": 4}
    r2 = C.evaluate(m, {}, {999}, tag="disjoint")
    assert r2["n_img"] == 0 and r2["baseline_shape"] is None, (
        f"ids 与 items 不相交时应报空集与 None，实得 {r2['n_img']}/{r2['baseline_shape']!r}")

    #   A3 夹具辨别力自证：修复前形态（裸 most_common(1)[0][0]）在同一夹具上必须抛 IndexError
    def _buggy_baseline(items):
        return (Counter(it["shape"] for it in items).most_common(1)[0][0],
                Counter(it["color"] for it in items).most_common(1)[0][0])

    try:
        _buggy_baseline([])
    except IndexError as e:
        assert "index" in str(e).lower(), f"修复前形态抛的不是 IndexError：{e!r}"
    else:
        raise AssertionError("夹具失效：修复前形态在空集上未抛 IndexError，抓不到回归")


def test_evaluate_baseline_unchanged_on_nonempty():
    """A4（#337 反向）：非空 id 集上基线仍取真多数类（确认修复只补了空集分支）。"""
    m = {"items": [_mk(1, "circle", "red", 1), _mk(2, "circle", "red", 1),
                   _mk(3, "triangle", "blue", 1)],
         "by_id": {1: [], 2: [], 3: []}, "d": 4}
    r = C.evaluate(m, {}, {1, 2, 3})
    assert r["baseline_shape"] == "circle", f"多数类形状应 circle，实得 {r['baseline_shape']!r}"
    assert r["baseline_color"] == "red", f"多数类颜色应 red，实得 {r['baseline_color']!r}"


# ==================== #433：选格循环不得重测循环不变量 ====================
def _blobs(rs, gap=4, n=72, color=(200, 40, 40)):
    """并排的实心圆盘（间隙 gap 使它们同属一簇、但分量级可被 `count_objects` 加计）。"""
    yy, xx = np.mgrid[0:n, 0:n]
    img = np.full((n, n, 3), 255.0)
    x = n // 2 - (sum(2 * r for r in rs) + gap * (len(rs) - 1)) // 2
    for r in rs:
        cx = x + r
        img[((yy - n // 2) ** 2 + (xx - cx) ** 2) <= r * r] = color
        x += 2 * r + gap
    return img


def _disk(n=72, R=14, c=(40, 40, 200)):
    yy, xx = np.mgrid[0:n, 0:n]
    img = np.full((n, n, 3), 255.0)
    img[((yy - n / 2 + 0.5) ** 2 + (xx - n / 2 + 0.5) ** 2) <= R * R] = c
    return img


def _grid_fixture():
    """两件标定件：件1＝两个大小不等的圆盘（真值 n=2）、件2＝单个圆盘（真值 n=1）。

    实测（本夹具，d=4/tol=40）：件1 的 `count_objects` 在 fr∈{0.15,0.25} 读 2、在
    fr∈{0.35,0.50} 读 1 ⇒ `cgrid` 四格**互不相同**（2/2/1/1），选格键真正在工作。
    """
    items = [_mk(1, "circle", "red", 2, img=_blobs((14, 8))),
             _mk(2, "triangle", "blue", 1, img=_disk())]
    by_id = {it["id"]: C.measure_item(it, tol=C.TOL, d=4)[0] for it in items}
    return {"items": items, "by_id": by_id, "d": 4}


def test_fit_measures_each_calibration_image_once(monkeypatch):
    """B 组（#433）：`fit` 里 `objects_of` 的调用次数 = 标定件数（每图测一次），不是 ×4。"""
    measured = _grid_fixture()
    ids = {1, 2}
    n_cal = len([it for it in measured["items"] if it["id"] in ids and it["truth"]])
    assert n_cal == 2, f"夹具失效：标定件数 {n_cal}"

    calls = {"n": 0}
    orig = C.objects_of

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(C, "objects_of", counting)
    f = C.fit(measured, ids)
    got = calls["n"]

    #   主断言：`fit` 中每张标定图只测一次
    grid = len(C.EXTRA_COMP_GRID)
    assert got == n_cal, (
        f"`fit` 中 objects_of 调用 {got} 次，应为标定件数 {n_cal}（每图测一次）——"
        f"报 {n_cal * grid} 次即在 fr 循环内层重测了循环不变量")

    #   夹具辨别力自证：修复前形态（在 fr 循环内层重测）在同一夹具上调用 = n_cal × 网格长度
    buggy = 0
    for _fr in C.EXTRA_COMP_GRID:                       # 复刻 :803-808 的循环结构（只计数）
        for it in measured["items"]:
            if it["id"] not in ids or not it["truth"]:
                continue
            orig(it["img"], tol=C.TOL, d=measured.get("d", 4))
            buggy += 1
    assert buggy == n_cal * grid > got, (
        f"夹具失效：修复前形态调用 {buggy} 次未多于修复后 {got} 次，抓不到回归")

    #   B2 读数等价：选格结果必须等于「独立测一遍、四格复用」的参考值（提循环外不改读数）
    ref = {}
    for fr in C.EXTRA_COMP_GRID:
        ok = 0
        for it in measured["items"]:
            if it["id"] not in ids or not it["truth"]:
                continue
            cl = orig(it["img"], tol=C.TOL, d=measured.get("d", 4))[0]
            ok += int(C.count_objects(cl, frac=fr) == it["truth"]["n"])
        ref[str(fr)] = ok
    assert f["extra_comp_grid"] == ref, f"cgrid 与参考 {ref} 不一致：{f['extra_comp_grid']}"
    #   夹具自证（续）：四格读数确实互不相同 ⇒ 断言对「读错格/读漏格」有辨别力
    assert len(set(ref.values())) > 1, f"夹具失效：cgrid 各格读数相同 {ref}，选格键无判别力"
    assert f["extra_comp_frac"] == float(max(C.EXTRA_COMP_GRID, key=lambda x: ref[str(x)])), (
        f"选出的面积门 {f['extra_comp_frac']} 与 cgrid {ref} 的最优格不符")
