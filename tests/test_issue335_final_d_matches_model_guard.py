# -*- coding: utf-8 -*-
"""#335 守卫：`train_infogap` / `train_hier` 的 `final_D` 必须是**最终模型**的损失。

缺陷面（公开 issue #335）：两条训练路径都在参数更新**前**测 d，更新后把旧 d 放进
history/curve，再以其末项当 `final_D` ⇒ 报告数值与最终模型不一致（实测 2 步训练
报 0.676、模型实际 0.636）。

本件锁：训练 ≥1 步后，`final_D` 与「当前模型在同一评估切片上现算的损失」一致
（train_infogap 用 x[:batch]/y[:batch]；train_hier 用 lat/labels 的前 batch 条）；
`steps==0` ⇒ 回落 d0（无更新即无「训练后」）。`history`/`curve`（过程曲线）语义不动。
定点变异：把 `final_D` 改回 `history[-1]["D"]` ⇒ 本文件必红。
"""
import numpy as np
import pytest

from lingshu.nn.hex_train import HexNet, train_infogap
from lingshu.nn.hex_hier import HexHierNet, train_hier
from lingshu.nn.hex_train import normalize_lattices
from lingshu.nn.hex_text import make_multimodal_scene
from lingshu.nn.hex_cnn import image_to_grid


def test_infogap_final_d_matches_final_model():
    net = HexNet(n_kernels=1, n_mix=2, n_class=2, seed=7)
    x = np.ones((2, 4, 4, 1)); y = np.zeros(2, dtype=int)
    res = train_infogap(net, x, y, max_steps=2, batch=2,
                        samples_per_step=net.n_params(), max_growth=0)
    now = net.loss(x[:2], y[:2])
    assert res["final_D"] == pytest.approx(now, abs=1e-4), (
        "final_D(%r) 与最终模型损失(%r) 不一致——报告的不是最终模型" % (res["final_D"], now))
    assert res["final_D"] < res["history"][0]["D"] + 1.0  # 有限性哨兵（防 NaN/inf 混入）


def test_infogap_zero_steps_returns_d0():
    net = HexNet(n_kernels=1, n_mix=2, n_class=2, seed=7)
    x = np.ones((2, 4, 4, 1)); y = np.zeros(2, dtype=int)
    res = train_infogap(net, x, y, max_steps=0, batch=2,
                        samples_per_step=net.n_params(), max_growth=0)
    assert res["final_D"] == pytest.approx(net.loss(x[:2], y[:2]), abs=1e-4)


def test_hier_final_d_matches_final_model():
    rng = np.random.default_rng(17)
    xs, metas = [], []
    for _ in range(12):
        im, m = make_multimodal_scene(rng, 48, 1)
        xs.append(im); metas.append(m)
    lat = normalize_lattices(np.stack([image_to_grid(im, 32)[0] / 255.0 for im in xs]))
    labs = {"shape": np.array([m["labels"][0]["obj"].split("|")[0] for m in metas]),
            "obj": np.array([m["labels"][0]["obj"] for m in metas])}
    net = HexHierNet(n_kernels=4, n_kernels2=4, stacked=True, seed=7)
    res = train_hier(net, lat, labs, steps=6, batch=12,
                     samples_per_step=8, seed=7)
    batch = 12
    # joint_loss 是 train_hier 内部闭包 ⇒ 守卫按同式独立复算（物体 CE + 0.5·形状 CE）。
    from lingshu.nn.hex_hier import ce_loss, softmax, SHAPES
    sf, cf = net.l2_features(lat[:batch])
    logits = net.l3_logits(sf, cf)
    p = softmax(logits)
    p_shape = np.stack([p[:, i * 3:(i + 1) * 3].sum(axis=1) for i in range(3)], axis=1)
    shape_idx = {sname: i for i, sname in enumerate(SHAPES)}
    sh_idx = np.array([shape_idx[v] for v in labs["shape"][:batch]])
    from lingshu.nn.hex_hier import OBJ
    d_obj = ce_loss(logits, labs["obj"][:batch], {o: i for i, o in enumerate(OBJ)})
    d_shape = float(-np.log(p_shape[np.arange(len(sh_idx)), sh_idx] + 1e-12).mean())
    now = d_obj + 0.5 * d_shape
    assert res["final_D"] == pytest.approx(now, abs=2e-3), (
        "final_D(%r) 与最终模型联合损失(%r) 不一致" % (res["final_D"], now))
