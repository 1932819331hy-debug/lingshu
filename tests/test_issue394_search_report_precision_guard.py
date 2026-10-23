# -*- coding: utf-8 -*-
"""#394 守卫：`search_report` 必须同时报精确率/误报数，退化「到处报」不得满分。

缺陷面（公开 issue #394）：旧报告只有召回口径（`|truth ∩ det| / |truth|`），检出
集合多大都不扣分——把 9 个象限全报一遍的退化检测器 `pos_hit_rate=1.0`。
本件增补 `pos_precision`/`obj_precision`/`pos_false_positives`/`obj_false_positives`，
既有召回键语义不动。本文件锁：

  ① 退化检测器（Scatter，见 issue 复现）：召回仍高，但**精确率低、误报数>0**；
  ② 完美检测器（monkeypatch 返回 truth 本身）：精确率=1.0、误报=0；
  ③ 空检出：精确率 0.0（不伪造成满分）、误报=0；
  ④ 既有键仍在（向后兼容）。
"""
import numpy as np
import pytest

from lingshu.nn.hex_cnn import image_to_grid
from lingshu.nn.hex_hier import HexHierNet
from lingshu.nn.hex_search import search_report
from lingshu.nn import hex_search as hs
from lingshu.nn.hex_text import make_multimodal_scene
from lingshu.nn.hex_train import normalize_lattices


def _data(n=8, seed=17):
    rng = np.random.default_rng(seed)
    xs, metas = [], []
    for _ in range(n):
        im, m = make_multimodal_scene(rng, 48, 2)
        xs.append(im)
        metas.append(m)
    lat = normalize_lattices(np.stack([image_to_grid(im, 32)[0] / 255.0 for im in xs]))
    return lat, metas


# ---------------------------------------------------------------- ① 退化检测器

class _Scatter(HexHierNet):
    """能量处处相同；整图给均匀 logits（→DEFER 下钻），子区域都高置信报 circle|red。"""

    def __init__(self, full):
        super().__init__()
        self._full = full

    def l1(self, x):
        return np.ones(x.shape[:3] + (self.K,)), x.mean(axis=(1, 2))

    def l2_features(self, x):
        return np.full((len(x), 1), float(x.shape[1])), x.mean(axis=(1, 2))

    def l3_logits(self, sf, cf):
        z = np.zeros((len(sf), 9))
        if sf[0, 0] < self._full:
            z[:, 0] = 10.0
        return z


def test_degenerate_scatter_has_low_precision():
    lat, metas = _data()
    rep = search_report(_Scatter(lat.shape[1]), lat, metas, max_depth=2)
    assert rep["pos_hit_rate"] >= 0.9, "召回口径在退化检测器上本就很高：%r" % (rep,)
    assert rep["pos_precision"] <= 0.34, (
        "「到处报」的退化检测器精确率必须低（现报高 ⇒ 指标仍无甄别力）：%r" % (rep,))
    assert rep["pos_false_positives"] > 0, rep


# ---------------------------------------------------------------- ② 完美检测器

def test_perfect_detector_precision_one(monkeypatch):
    lat, metas = _data(n=4, seed=21)

    def _perfect(net, l, max_depth=2):
        # search_report 逐张调用 ⇒ 用计数器按批次顺序返回该张的 truth
        i = _perfect.calls
        _perfect.calls += 1
        found = [{"pos": lb["pos"], "obj": lb["obj"]} for lb in metas[i]["labels"]]
        return found, {"visited": 10, "rejected": 2}

    _perfect.calls = 0
    monkeypatch.setattr(hs, "recursive_search", _perfect)
    rep = search_report(object(), lat, metas, max_depth=2)
    assert rep["pos_hit_rate"] == 1.0 and rep["obj_match_rate"] == 1.0, rep
    assert rep["pos_precision"] == 1.0 and rep["obj_precision"] == 1.0, rep
    assert rep["pos_false_positives"] == 0 and rep["obj_false_positives"] == 0, rep


# ---------------------------------------------------------------- ③ 空检出

def test_empty_detections_precision_zero(monkeypatch):
    lat, metas = _data(n=2, seed=23)
    monkeypatch.setattr(hs, "recursive_search",
                        lambda net, l, max_depth=2: ([], {"visited": 3, "rejected": 1}))
    rep = search_report(object(), lat, metas)
    assert rep["pos_hit_rate"] == 0.0 and rep["pos_precision"] == 0.0, rep
    assert rep["pos_false_positives"] == 0, rep


# ---------------------------------------------------------------- ④ 向后兼容

def test_legacy_keys_still_present(monkeypatch):
    lat, metas = _data(n=2, seed=25)
    monkeypatch.setattr(hs, "recursive_search",
                        lambda net, l, max_depth=2: ([], {"visited": 3, "rejected": 1}))
    rep = search_report(object(), lat, metas)
    assert {"pos_hit_rate", "obj_match_rate", "avg_visited", "prune_rate"} <= set(rep)
