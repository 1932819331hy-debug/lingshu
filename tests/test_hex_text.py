# -*- coding: utf-8 -*-
"""hex_text 测试 · M4 桥:图文共同识别与子物体拆分
运行: python -m pytest tests/test_hex_text.py -v
纪律: 训练网 fixture 模块级只训一次(150 步),全部断言小样本。
"""
import os as _os, sys as _sys
_RP = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # 仓根
_sys.path.insert(0, _RP)
import numpy as np
import pytest

from lingshu.nn.hex_cnn import image_to_grid
from lingshu.nn.hex_hier import HexHierNet, train_hier
from lingshu.nn.hex_text import (corrupt_description, make_multimodal_scene,
                           multimodal_check, parse_description,
                           render_clause, spatial_detect)
from lingshu.nn.hex_train import normalize_lattices


# ==================== 判据门限（具名标定常量，非魔数）====================
# 缺陷（件 #4 ② / 件 #24，成立）：本文件两处「已知红」断言原为裸比例阈值
#   `hit / 16 > 0.6`、`ok / 12 >= 0.6`——硬编码、无标定/容差/版本钉死，读数无法复算。
# 现改为具名常量：取值由「随机基线 + 单侧二项显著地板 + 实测区间留余量」推出，
# 出处逐条写在各用例 docstring；守卫 tests/test_hex_text_thresholds.py 同源复算并守门。
N_SINGLE, N_CLEAN = 16, 12
HIT_MIN_SINGLE = 12     # ≥ 12/16 = 0.75；随机基线 1/9，二项显著地板 6/16（α=0.01）
CLEAN_MIN = 9           # ≥ 9/12 = 0.75；= 二项显著地板（n=12/p=1/3/α=0.01）
CORRUPT_MIN = 6         # ≥ 6/12 = 0.50；实测区间 0.667~1.0（40 种子），留 2 样本余量


def _dataset(n, n_objects=1, seed=7):
    rng = np.random.default_rng(seed)
    xs, metas = [], []
    for _ in range(n):
        im, meta = make_multimodal_scene(rng, 48, n_objects)
        xs.append(im)
        metas.append(meta)
    feats = [image_to_grid(im, cells_across=16)[0] / 255.0 for im in xs]
    lat = normalize_lattices(np.stack(feats))
    return np.stack(xs), metas, lat


@pytest.fixture(scope="module")
def net():
    """象限裁剪训练一次(训练分布=推理分布:检测时网络看到的就是象限子图)。"""
    xs, metas, lat = _dataset(96, n_objects=1, seed=11)
    r3, c3 = lat.shape[1] // 3, lat.shape[2] // 3
    crops, labs = [], {"shape": [], "color": [], "obj": [], "pos": []}
    for b, m in enumerate(metas):
        lb = m["labels"][0]
        qy, qx = divmod(int(lb["pos"][1]), 3)
        crops.append(lat[b, qy * r3:(qy + 1) * r3, qx * c3:(qx + 1) * c3])
        labs["shape"].append(lb["obj"].split("|")[0])
        labs["color"].append(lb["obj"].split("|")[1])
        labs["obj"].append(lb["obj"])
        labs["pos"].append("r4")          # 裁剪图物体居中
    n = HexHierNet(seed=7)
    train_hier(n, np.stack(crops),
               {k: np.array(v) for k, v in labs.items()},
               steps=150, samples_per_step=20, lr=0.1)
    return n


def test_parse_description_roundtrip():
    text = "左上有红色圆形,右下有绿色三角"
    clauses = parse_description(text)
    assert len(clauses) == 2
    assert clauses[0]["shape"] == "circle" and clauses[0]["color"] == "red"
    assert clauses[0]["pos"] == "r0" and clauses[1]["pos"] == "r8"
    assert "红" in render_clause(clauses[0]) and "圆" in render_clause(clauses[0])


def test_corrupt_description_changes_words():
    desc = "左上有红色圆形"
    rng = np.random.default_rng(0)
    changed = sum(corrupt_description(desc, rng, 1.0) != desc for _ in range(10))
    assert changed >= 8, f"corrupt_rate=1.0 应几乎必改: {changed}/10"


def test_spatial_detect_finds_single_object(net):
    """单物体位置检测：命中数须过「随机基线 + 二项显著地板」标定门限。

    判据来源（逐条，不编造）：
      · 随机基线 1/9 —— 位置是 3×3 九宫格，瞎猜命中率 = 1/9（件 #24 取证所指
        「硬编码阈值」的可复算替代量；非理论章节值）。
      · 门限 12/16 = 0.75 —— 由二项分布给出：n=16、p=1/9 时 P(X≥6) = 5.6e-3
        （α=0.01 地板）、P(X≥10) = 1.2e-6（旧口径 `>0.6` 的等效地板）。
        取 12 对应 α≈4.2e-9，且低于 40 个数据种子（seed 5..44）的实测区间下界
        （区间 = 1.0，逐种子可复现）。**这是经验标定 + 二项显著性，不是理论值。**
      · 削掉的判别力：无。旧口径 `hit/16 > 0.6` 等效地板是 10/16；新门限 12/16
        是**上调（收紧）**，历史红读数 6/16 与旧地板 10/16 都仍被本门限判红。
    """
    xs, metas, lat = _dataset(N_SINGLE, n_objects=1, seed=5)
    dets = spatial_detect(net, lat)
    hit = sum(1 for i in range(N_SINGLE)
              if any(d["pos"] == metas[i]["labels"][0]["pos"]
                     for d in dets[i]))
    assert hit >= HIT_MIN_SINGLE, \
        f"单物体位置检测命中数低于标定门限 {HIT_MIN_SINGLE}/{N_SINGLE}: {hit}/{N_SINGLE}"


def test_zero_shot_two_object_split(net):
    """零样本子物体拆分:单物体训练的网络,双物体场景拆出两个位置。"""
    xs, metas, lat = _dataset(12, n_objects=2, seed=13)
    dets = spatial_detect(net, lat)
    both = sum(1 for i in range(12)
               if len({d["pos"] for d in dets[i]}) >= 2)
    assert both / 12 > 0.4, f"双物体零样本拆分: {both}/12"


def test_multimodal_check_clean_vs_corrupt(net):
    """一致性判定：干净描述须多数非 REJECT、噪声描述须显著 REJECT——两门限均标定。

    判据来源（逐条，不编造）：
      · 干净侧基线 1/3 —— 四态判定里 ACCEPT/DEFER 占 2 态 ⇒ 随机基线 1/3。
        门限 9/12 = 0.75 = 二项显著地板（n=12、p=1/3、α=0.01 时地板即 9）；
        40 个数据种子（seed 17..56）实测区间 0.917~1.0，本门限低于该下界。
        旧口径 `ok/12 >= 0.6` 等效地板 8/12 ⇒ 本次为**上调（收紧）**。
      · 噪声侧基线 1/9 —— 位置瞎猜命中率；`corrupt_description` 已被
        tests/test_hex_text_contracts.py 钉住「rate=1.0 时形状与颜色都真被改」，
        故正确实现下 REJECT 应远高于瞎猜。门限 6/12 = 0.50，实测区间
        0.667~1.0（40 种子），留 2 个样本余量；旧口径 `rej/12 > 0.3` 等效地板
        4/12 ⇒ 本次同为**上调（收紧）**。
      · **经验标定 + 二项显著性，不是理论值**（理论稿无「REJECT 率」条目）。
      · 削掉的判别力：无——两门限都由旧口径上调，历史红读数（ok=7/12、rej=0/12）
        与旧地板（8/12、4/12）均仍被判红。
    """
    xs, metas, lat = _dataset(N_CLEAN, n_objects=1, seed=17)
    rng = np.random.default_rng(3)
    verdicts = []
    for i in range(N_CLEAN):
        r = multimodal_check(net, lat[i:i + 1], metas[i]["description"])
        verdicts.append(r["verdict"])
    ok = verdicts.count("ACCEPT") + verdicts.count("DEFER")
    assert ok >= CLEAN_MIN, \
        f"干净描述非 REJECT 数低于标定门限 {CLEAN_MIN}/{N_CLEAN}: {verdicts}"
    rej = 0
    for i in range(N_CLEAN):
        bad = corrupt_description(metas[i]["description"], rng, 1.0)
        r = multimodal_check(net, lat[i:i + 1], bad)
        rej += (r["verdict"] == "REJECT")
    assert rej >= CORRUPT_MIN, \
        f"噪声描述 REJECT 数低于标定门限 {CORRUPT_MIN}/{N_CLEAN}: {rej}/{N_CLEAN}"


def test_multimodal_structure(net):
    xs, metas, lat = _dataset(4, n_objects=1, seed=5)
    r = multimodal_check(net, lat[0:1], metas[0]["description"])
    assert set(r) >= {"verdict", "score", "align", "clauses", "detections"}
    assert 0.0 <= r["score"] <= 1.0 and 0.0 <= r["align"] <= 1.0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
