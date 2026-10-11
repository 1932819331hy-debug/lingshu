# -*- coding: utf-8 -*-
"""守卫 · `lingshu/gen/hexgen_align_probe.py` 两件 P2 缺陷（#431 / #432）
====================================================================================
本文件只钉「缺陷不再存在」，不是「代码能跑」。两件各带**变异可判红**的判据。

#431（成立）· ② 的「谁在把判决推向 diamond」解释的不是读者的判决量
    取证（HEAD 修复前）：`hexgen_align_probe.py:69-74 def distance_contrib(q,p,w,sc)` 只算
    **加权 L1**（`per = np.abs(q-p)/sc*w`），`probe_self_shape` 的 `ranked` 也按
    `out["dist"][k]["total"]` 排序（`:94`），全文无 `pen` / `cut_prom` / `kdom` 罚项。
    而读者的真判决在 `hexgen_c1_real.py:930-949`：`score = 加权 L1 + pen`，
    `pen ∈ {0,1,2}`（主阶门 `:936-939` ＋ 峰显著度门 `:944-947`）。
    判据来源：`hexgen_c1_real.py:915-949`（`predict` 的判决量定义；`:948 if best is None or
    dist + pen < best`）与 `hexgen_c1_real.py:94 GATE_STRENGTH`、`:813 cut_prom`。
    断言：构造「裸 L1 更近者 ≠ 读者选中者」的夹具（裸 L1：diamond 2.8 < triangle 3.0；
    罚后判决量：diamond 4.8 > triangle 3.0）⇒ ② 报出的 `ranked` 首名必须与 `C.predict`
    的判决一致，且 `pen`/`score` 必须报出。抽掉修复（`ranked` 改回按 `total` 排序）
    ⇒ `ranked[0]` 变 `diamond`，与 `predict` 的 `triangle` 相左，本组断言即红。

#432（成立）· `blackbox_reader()` 每轮 `main()` 被完整重算 16 次
    取证（HEAD 修复前）：`:45 def blackbox_reader()` 无记忆化，而调用链
    `cross_source_hits`×2（`:117` 无 `fit` 形参、`:123 fit, _ = blackbox_reader()`）
    ＋ `head_to_head`×13（`:137` 无 `fit` 形参、`:141 fit, m_box = blackbox_reader()`）
    ＋ `main()` 自己（`:176`）＝ 16 次，每次 `C.load_items` ＋ 逐件 `C.measure_item` ＋ `C.fit`。
    断言：同一进程内连调 3 次 ⇒ `C.load_items` / `C.fit` 各只被调用 **1** 次，且返回对象同一。
    抽掉修复（去掉 `lru_cache`）⇒ 计数变 3，本组断言即红。

**跨批覆盖**：本组守卫只钉本件机制——`ranked` 排序键与 `blackbox_reader` 记忆化；
    与既有 `tests/test_hexgen_self_source_guards.py`（只引 `NAME_MAP_SELF2REAL` 作 docstring
    判据来源）无代偿关系：那条断言的是 `stripe≡square` 几何一致，与本件两件均不同面。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_hexgen_align_probe_p2_decision_and_cache.py -q --no-header
"""
from __future__ import annotations

import numpy as np
import pytest

from lingshu.gen import hexgen_c1_real as C
from lingshu.gen import hexgen_align_probe as P
from lingshu.gen import hexgen_self_source as S


# ==================== #431 · ② 必须按读者判决量（dist + pen）排序 ====================

def _vec(**kw):
    v = np.zeros(len(C.FEATS), dtype=float)
    for k, val in kw.items():
        v[C.FEATS.index(k)] = val
    return v


def _verdict_fit():
    """夹具：读者的两个形状原型，使得**裸 L1 更近者 ≠ 读者选中者**。

    · `triangle` 原型：`extent=3.0`、`harm3=1.0`（主阶 3）、`peak_prom=0.9`。
    · `diamond` 原型：`harm4=1.0`（主阶 4）、`peak_prom=0.1`。
    · 观测件：`kdom=3`、`harm3=1.0`、`peak_prom=0.9`（主阶 3、有峰）。
    裸 L1（w=sc=1）：`triangle` = |3.0−0| = **3.0**；`diamond` = 0.8+1.0+1.0 = **2.8**。
    罚（`C.predict` 口径）：`triangle` pen=0；`diamond` pen=2（kdom 门：观测主阶 3 ≠ 原型 4
    且两边主阶强度 > 0.12；峰显著度门：0.9 与 0.1 分居切点 0.5 两侧）⇒ 判决量 3.0 vs 4.8。
    """
    return {"shape_protos": {"triangle": _vec(extent=3.0, harm3=1.0, peak_prom=0.9),
                             "diamond": _vec(harm4=1.0, peak_prom=0.1)},
            "feat_scale": np.ones(len(C.FEATS)),
            "feat_weight": np.ones(len(C.FEATS)),
            "cut_prom": 0.5,
            "color_centroids": {"red": np.zeros(3)},
            "cut_plain": None, "zone": {}}


def _verdict_obj():
    feat = {k: 0.0 for k in C.FEATS}
    feat["harm3"] = 1.0
    feat["peak_prom"] = 0.9
    return {"feat": feat, "kdom": 3, "lab": np.zeros(3), "hp_rel": 0.0,
            "cy": 0.5, "cx": 0.5, "cell": "r4", "clipped": False,
            "tex_rel": 0.0, "edge_rel": 0.0, "coh": 0.0, "area": 100}


def test_431_fixture_discernment_witness():
    """夹具自证：裸 L1 与读者判决量在该夹具上**排序相反**（否则本组断言抓不到回归）。"""
    fit, o = _verdict_fit(), _verdict_obj()
    sc = P.reader_class_scores(o, fit)
    raw_best = min(sc, key=lambda k: sc[k]["dist"])
    verdict_best = min(sc, key=lambda k: (sc[k]["score"], k))
    assert raw_best == "diamond", f"夹具失效：裸 L1 首名应为 diamond，实为 {raw_best}（{sc}）"
    assert verdict_best == "triangle", f"夹具失效：判决量首名应为 triangle，实为 {verdict_best}"
    assert sc["diamond"]["pen"] == 2.0 and sc["triangle"]["pen"] == 0.0, sc
    assert C.predict(o, fit)["shape"] == "triangle", "读者判决应为 triangle（判据 hexgen_c1_real.py:948）"


def test_431_probe_ranking_uses_reader_verdict_not_raw_l1(monkeypatch):
    """② 的 `ranked` 必须按**判决量**（dist+pen）排序 ⇒ 与 `C.predict` 判决一致。

    缺陷形态（按裸 `total` 排序）下 `ranked[0] == "diamond"`，与读者判决 `triangle` 相左。
    """
    fit, o = _verdict_fit(), _verdict_obj()
    monkeypatch.setattr(P.S, "render_self",
                        lambda *a, **k: np.zeros((8, 8, 3), dtype=np.float64))
    monkeypatch.setattr(P.C, "measure_item", lambda *a, **k: ([o], 1))

    pr = P.probe_self_shape(shape="triangle", fit=fit)

    #   读者判决量必须报出（缺陷形态下无这两个字段）
    assert pr["dist"]["diamond"]["pen"] == 2.0, pr["dist"]["diamond"]
    assert pr["dist"]["diamond"]["score"] == 4.8, pr["dist"]["diamond"]
    assert pr["dist"]["triangle"]["score"] == 3.0, pr["dist"]["triangle"]
    assert pr["dist"]["diamond"]["pen_terms"] == ["kdom_gate", "peak_prom_gate"], \
        pr["dist"]["diamond"]["pen_terms"]
    #   核心：排序键＝判决量 ⇒ 首名＝读者判决
    assert pr["ranked"][0] == "triangle", (
        f"② 的 ranked 首名为 {pr['ranked'][0]}，与读者判决 {pr['verdict']} 相左"
        f"（按裸 L1 排序会报 diamond：dist 2.8 < 3.0）")
    assert pr["ranked"][0] == pr["verdict"], (
        f"ranked={pr['ranked']} 与 predict 判决 {pr['verdict']} 不一致")


# ==================== #432 · blackbox_reader 记忆化（16 次重算 → 1 次） ====================

def _synthetic_corpus():
    """合成语料：用自家渲染件充当黑箱件（`C.measure_item` 能抠出物体即够用）。

    3 个形状类（triangle/circle/stripe）各 1 件 ⇒ `C.fit` 能建原型与尺度。
    """
    def mk(iid, shape, color, tup):
        return {"file": f"fake://{iid}", "id": iid, "shape": shape, "color": color,
                "img": S.render_self(tup).astype(np.float64), "prompt": None,
                "truth": {"n": 1, "shape": shape, "color": color, "pattern": "plain",
                          "zone_text": "center", "cells": ["r4"]}}
    return [mk(1, "triangle", "red", (1, "triangle", "red", "solid", ("r4",))),
            mk(2, "circle", "blue", (1, "circle", "blue", "solid", ("r4",))),
            mk(3, "stripe", "green", (1, "stripe", "green", "solid", ("r4",)))]


def test_432_blackbox_reader_is_memoized(monkeypatch):
    """同一进程内连调 3 次 ⇒ `C.load_items` / `C.fit` 各只被调用 1 次，且返回对象同一。

    缺陷形态（无记忆化）下计数为 3 ⇒ 本断言红。夹具自证：计数是**真计数**
    （首次调用必须 ≥1，否则断言空转）。
    """
    items = _synthetic_corpus()
    calls = {"load": 0, "fit": 0}
    real_fit = C.fit

    def counted_load(*a, **k):
        calls["load"] += 1
        return items

    def counted_fit(m, ids):
        calls["fit"] += 1
        return real_fit(m, ids)

    monkeypatch.setattr(P.C, "load_items", counted_load)
    monkeypatch.setattr(P.C, "fit", counted_fit)
    P.blackbox_reader.cache_clear()          # 清掉前序测试可能留下的缓存
    try:
        r1 = P.blackbox_reader()
        r2 = P.blackbox_reader()
        r3 = P.blackbox_reader()
    finally:
        P.blackbox_reader.cache_clear()

    #   夹具自证：确实跑过一次完整计算
    assert calls["load"] >= 1 and calls["fit"] >= 1, f"夹具空转：{calls}"
    assert calls["load"] == 1, f"C.load_items 被调用 {calls['load']} 次（应 1 次＝记忆化）"
    assert calls["fit"] == 1, f"C.fit 被调用 {calls['fit']} 次（应 1 次＝记忆化）"
    assert r1 is r2 and r2 is r3, "记忆化未生效：三次调用返回了不同对象"


def test_432_main_chain_reader_calls_collapse(monkeypatch):
    """端到端口径：`main()` 的调用链请求读者 16 次 ⇒ 修复后底层只算 1 次。

    直接按调用链的请求次数（`cross_source_hits` 2 次 ＋ `head_to_head` 13 次 ＋ 1 次）
    复现请求序，断言底层 `C.fit` 计数为 1。
    """
    items = _synthetic_corpus()
    calls = {"fit": 0}
    real_fit = C.fit

    def counted_load(*a, **k):
        return items

    def counted_fit(m, ids):
        calls["fit"] += 1
        return real_fit(m, ids)

    monkeypatch.setattr(P.C, "load_items", counted_load)
    monkeypatch.setattr(P.C, "fit", counted_fit)
    P.blackbox_reader.cache_clear()
    try:
        n_requests = 16                        # 2（cross_source_hits）+ 13（head_to_head）+ 1（main）
        for _ in range(n_requests):
            P.blackbox_reader()
    finally:
        P.blackbox_reader.cache_clear()
    assert calls["fit"] == 1, (
        f"{n_requests} 次请求触发了 {calls['fit']} 次完整重算（应 1 次＝记忆化）")
