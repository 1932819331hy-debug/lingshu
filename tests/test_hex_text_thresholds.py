# -*- coding: utf-8 -*-
"""test_hex_text_thresholds · test_hex_text 两处比例断言必须走「标定常量」而非裸阈值
============================================================================
缺陷（件 #4 ② 与 件 #24，两条均判成立）——tests/test_hex_text.py 原为：
    assert hit / 16 > 0.6     # test_spatial_detect_finds_single_object
    assert ok  / 12 >= 0.6    # test_multimodal_check_clean_vs_corrupt
硬编码魔数、无标定出处、无容差、无版本钉死：0.6 这个判据无法复算（既不是随机
基线，也不是理论值），历史读数 6/16、7/12 与阈值的距离无从判断，故 README 与
gate.yml 只能把整条用例 deselect（#341 的连带面）。
修法：改为具名标定常量 N_SINGLE / HIT_MIN_SINGLE / N_CLEAN / CLEAN_MIN /
CORRUPT_MIN，取值由「随机基线 + 单侧二项显著地板 + 实测区间留余量」推出，
出处逐条写在各用例 docstring（件 #4 ① 的 numpy 版本区间已在 a215d8b 落地）。

断言组（每组都只在「本件机制」上生效——A 组是本件独有夹具：仓内无第二件读
tests/test_hex_text.py 的源码，故不存在与其它守卫互相代偿）：
  A 组（形态 · 本件独有）：ast 解析 tests/test_hex_text.py，三项计数断言
     （左操作数为 hit / ok / rej）必须与具名常量比较（`assert <计数> >= <Name>`）；
     一旦回退成「比例式裸阈值」（BinOp(Div) 比 Constant）即红。
  B 组（标定自洽 · 纯算术复算）：常量 ≥ 随机基线比例、≥ 二项 α=0.01 显著地板、
     ≥ 旧口径等效地板（即收紧而非放宽）；历史红读数 6/16、7/12 必须被判红。
  C 组（数据复算）：与主件同管线训一次网，10 个数据种子实测下界 ≥ 各常量。
  D 组（版本钉死）：pyproject 的 numpy 声明必须是**有界区间**（非零版本约束），
     且当前运行 numpy 落在该区间内。

判据来源（逐条，不编造）：
  · 随机基线 1/9（位置=3×3 九宫格瞎猜）与 1/3（四态里 ACCEPT/DEFER 占 2 态）
    —— 件 #24 取证所指「硬编码阈值」的可复算替代量，非理论章节值。
  · 二项显著地板 —— math.comb 精确计算（本守卫 B 组现场复算，不预存数字）。
  · 门限取值 12/16、9/12、6/12 —— **经验标定 + 二项显著性**（理论稿无「位置
    命中率」「REJECT 率」条目）；实测区间见下。
  · 实测区间（本机 · numpy 2.5.3 · 固定 seed，可复跑）：
    单物体命中 40 种子（seed 5..44）= 1.0；干净非 REJECT 40 种子（17..56）
    = 0.917~1.0；噪声 REJECT 40 种子 = 0.667~1.0。
    版本复跑：numpy 2.3.5 与 2.4.2 下 tests/test_hex_text.py 6 passed（读数一致）。

不适用条件 / 已知边界：
  · 本守卫不重测 image_to_grid 的采样几何（那是 tests/test_hex_grid_extent.py）
    也不重测 corrupt_description / fuse_consistency 口径（tests/test_hex_text_contracts.py）。
  · 同文件内 `both / 12 > 0.4`（test_zero_shot_two_object_split）不在本件范围——
    它非「已知红」两项之一，本件按最小改动不动它。
  · 门限是**经验标定**：模型/晶格口径若再演进（如 #116 之后的新几何），
    C 组会先红，届时须重标定并同步更新本文件 docstring 与主件常量。

定点变异自证（抽掉修复 ⇒ 必红）：
  把主件三处断言改回 `assert hit / 16 > 0.6` / `assert ok / 12 >= 0.6` /
  `assert rej / 12 > 0.3` ⇒ A 组当场报「缺陷形态回归：断言左式仍是比例
  （BinOp，如 hit / 16）」；若连标定常量一并删掉，则 B/C 组报 KeyError。

运行（lingshu 仓根）：python -X utf8 tests/test_hex_text_thresholds.py
                       / python -X utf8 -m pytest tests/test_hex_text_thresholds.py -q
退出码：0 = 全过；1 = 有断言失败
"""
from __future__ import annotations

import ast
import math
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import numpy as np  # noqa: E402
import pytest  # noqa: E402

MAIN_TEST = os.path.join(REPO, "tests", "test_hex_text.py")

# 主件标定常量（本守卫只读、不复算到别处——值本身由 A/B 组守门）
COUNT_NAMES = ("hit", "ok", "rej")


def _load_constants():
    """从主件模块级赋值里读出标定常量（不 import，避免与 pytest 收集耦合）。"""
    tree = ast.parse(open(MAIN_TEST, encoding="utf-8").read())
    consts = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                consts[tgt.id] = value
            elif isinstance(tgt, ast.Tuple) and isinstance(value, tuple) \
                    and len(tgt.elts) == len(value):
                for el, v in zip(tgt.elts, value):
                    if isinstance(el, ast.Name):
                        consts[el.id] = v
    return consts


def _binom_floor(n, p, alpha):
    """单侧二项地板：最小 k 使 P(X≥k) < alpha（X~Bin(n,p)）。"""
    for k in range(n + 1):
        tail = math.fsum(math.comb(n, j) * p ** j * (1 - p) ** (n - j)
                         for j in range(k, n + 1))
        if tail < alpha:
            return k
    return n + 1


def _count_asserts():
    """主件里「涉及 hit / ok / rej 计数」的断言节点清单（两种形态都收）。

    收两种形态：修复后 `assert <计数> >= <Name>`（左式是 Name）与缺陷形态
    `assert <计数> / N > 0.6`（左式是 BinOp，其左又是 Name）——后者必须能被
    A 组**点名**为「比例式裸阈值」，而不是静默当成「断言被删」。
    """
    tree = ast.parse(open(MAIN_TEST, encoding="utf-8").read())
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)):
            continue
        left = node.test.left
        if isinstance(left, ast.BinOp) and isinstance(left.left, ast.Name) \
                and left.left.id in COUNT_NAMES:
            found.append(node)
        elif isinstance(left, ast.Name) and left.id in COUNT_NAMES:
            found.append(node)
    return found


# ==================== A 组：形态（本件独有夹具）====================

def test_a_assertions_compare_named_calibrated_constants():
    """A：三项计数断言必须 `assert <计数> >= <具名常量>`，不得是比例式裸阈值。"""
    asserts = _count_asserts()
    assert len(asserts) == len(COUNT_NAMES), \
        f"主件里 hit/ok/rej 计数断言应有 {len(COUNT_NAMES)} 条，实见 {len(asserts)} 条" \
        "（被删或被改写 ⇒ 本件判别面失效）"
    consts = _load_constants()
    names = set()
    for node in asserts:
        cmp = node.test
        assert len(cmp.ops) == 1 and len(cmp.comparators) == 1
        left, right = cmp.left, cmp.comparators[0]
        assert not isinstance(left, ast.BinOp), \
            "缺陷形态回归：断言左式仍是比例（BinOp，如 hit / 16）——" \
            "裸阈值不可复算，须改用具名标定常量"
        assert isinstance(left, ast.Name) and left.id in COUNT_NAMES, ast.dump(left)
        assert isinstance(right, ast.Name), \
            f"缺陷形态回归：比较对象是字面量 {ast.dump(right)}（裸阈值），" \
            "须为具名标定常量"
        assert isinstance(cmp.ops[0], ast.GtE), \
            f"{right.id}：须用 >= 与计数比较（离散计数上 > 有 off-by-one 歧义）"
        names.add(right.id)
    assert names == {"HIT_MIN_SINGLE", "CLEAN_MIN", "CORRUPT_MIN"}, names
    for n in names:
        assert isinstance(consts.get(n), int), f"标定常量 {n} 不是模块级整数字面量"


# ==================== B 组：标定自洽（纯算术复算）====================

def test_b_thresholds_are_calibrated_not_magic():
    """B：门限 ≥ 随机基线 / 二项显著地板 / 旧口径等效地板；历史红读数仍被判红。"""
    c = _load_constants()
    n_single, n_clean = c["N_SINGLE"], c["N_CLEAN"]
    hit_min, clean_min, corr_min = c["HIT_MIN_SINGLE"], c["CLEAN_MIN"], c["CORRUPT_MIN"]

    # 随机基线：位置瞎猜 1/9；四态里非 REJECT 占 2/4
    assert hit_min / n_single > 1 / 9, "单物体门限不高于随机基线 ⇒ 无判别力"
    assert clean_min / n_clean > 1 / 3, "干净侧门限不高于随机基线 ⇒ 无判别力"
    assert corr_min / n_clean > 1 / 9, "噪声侧门限不高于随机基线 ⇒ 无判别力"

    # 二项显著地板（现场复算）：α=0.01
    assert hit_min >= _binom_floor(n_single, 1 / 9, 0.01), \
        f"单物体门限 {hit_min} 低于 α=0.01 二项地板 {_binom_floor(n_single, 1 / 9, 0.01)}"
    assert clean_min >= _binom_floor(n_clean, 1 / 3, 0.01), \
        f"干净侧门限 {clean_min} 低于 α=0.01 二项地板 {_binom_floor(n_clean, 1 / 3, 0.01)}"

    # 旧口径等效地板（`hit/16 > 0.6` ⇒ 10/16；`ok/12 >= 0.6` ⇒ 8/12；
    # `rej/12 > 0.3` ⇒ 4/12）——新门限必须 ≥，即收紧而非放宽
    old_single = next(k for k in range(n_single + 1) if k / n_single > 0.6)
    old_clean = next(k for k in range(n_clean + 1) if k / n_clean >= 0.6)
    old_corrupt = next(k for k in range(n_clean + 1) if k / n_clean > 0.3)
    assert hit_min >= old_single and clean_min >= old_clean and corr_min >= old_corrupt, \
        f"门限被放宽：新 {hit_min}/{clean_min}/{corr_min} vs 旧地板 " \
        f"{old_single}/{old_clean}/{old_corrupt}"

    # 历史红读数（README / gate.yml 记录）必须仍被判红
    assert 6 < hit_min, f"历史红读数 6/16 不再被判红（门限 {hit_min}）"
    assert 7 < clean_min, f"历史红读数 7/12 不再被判红（门限 {clean_min}）"
    # 旧地板也须被判红（收紧的证据，非「改后抓不到、改前能抓」）
    assert old_single < hit_min and old_clean < clean_min and old_corrupt < corr_min


# ==================== C 组：数据复算（与主件同管线）====================

def _dataset(n, n_objects=1, seed=7):
    from lingshu.nn.hex_cnn import image_to_grid
    from lingshu.nn.hex_text import make_multimodal_scene
    from lingshu.nn.hex_train import normalize_lattices
    rng = np.random.default_rng(seed)
    xs, metas = [], []
    for _ in range(n):
        im, meta = make_multimodal_scene(rng, 48, n_objects)
        xs.append(im)
        metas.append(meta)
    feats = [image_to_grid(im, cells_across=16)[0] / 255.0 for im in xs]
    return np.stack(xs), metas, normalize_lattices(np.stack(feats))


@pytest.fixture(scope="module")
def net():
    """与 tests/test_hex_text.py 的 fixture 逐参数同管线（象限裁剪，150 步）。"""
    from lingshu.nn.hex_hier import HexHierNet, train_hier
    _, metas, lat = _dataset(96, n_objects=1, seed=11)
    r3, c3 = lat.shape[1] // 3, lat.shape[2] // 3
    crops, labs = [], {"shape": [], "color": [], "obj": [], "pos": []}
    for b, m in enumerate(metas):
        lb = m["labels"][0]
        qy, qx = divmod(int(lb["pos"][1]), 3)
        crops.append(lat[b, qy * r3:(qy + 1) * r3, qx * c3:(qx + 1) * c3])
        labs["shape"].append(lb["obj"].split("|")[0])
        labs["color"].append(lb["obj"].split("|")[1])
        labs["obj"].append(lb["obj"])
        labs["pos"].append("r4")
    n = HexHierNet(seed=7)
    train_hier(n, np.stack(crops),
               {k: np.array(v) for k, v in labs.items()},
               steps=150, samples_per_step=20, lr=0.1)
    return n


def test_c_seed_sweep_stays_above_calibrated_floors(net):
    """C：10 个数据种子实测下界 ≥ 各标定常量（门限不是卡线值）。"""
    from lingshu.nn.hex_text import (corrupt_description, multimodal_check,
                                     spatial_detect)
    c = _load_constants()
    n_single, n_clean = c["N_SINGLE"], c["N_CLEAN"]
    singles, cleans, corrs = [], [], []
    for seed in range(5, 15):
        _, metas, lat = _dataset(n_single, n_objects=1, seed=seed)
        dets = spatial_detect(net, lat)
        singles.append(sum(1 for i in range(n_single)
                           if any(d["pos"] == metas[i]["labels"][0]["pos"]
                                  for d in dets[i])))
    for seed in range(17, 27):
        _, metas, lat = _dataset(n_clean, n_objects=1, seed=seed)
        rng = np.random.default_rng(seed)
        v = [multimodal_check(net, lat[i:i + 1], metas[i]["description"])["verdict"]
             for i in range(n_clean)]
        cleans.append(v.count("ACCEPT") + v.count("DEFER"))
        corrs.append(sum(
            multimodal_check(net, lat[i:i + 1],
                             corrupt_description(metas[i]["description"], rng, 1.0)
                             )["verdict"] == "REJECT" for i in range(n_clean)))
    assert min(singles) >= c["HIT_MIN_SINGLE"], \
        f"单物体命中下界 {min(singles)}/{n_single} < 门限 {c['HIT_MIN_SINGLE']}（{singles}）"
    assert min(cleans) >= c["CLEAN_MIN"], \
        f"干净非 REJECT 下界 {min(cleans)}/{n_clean} < 门限 {c['CLEAN_MIN']}（{cleans}）"
    assert min(corrs) >= c["CORRUPT_MIN"], \
        f"噪声 REJECT 下界 {min(corrs)}/{n_clean} < 门限 {c['CORRUPT_MIN']}（{corrs}）"
    print(f"  [C] 单物体 {singles} / 干净 {cleans} / 噪声 {corrs}"
          f" · numpy {np.__version__}")


# ==================== D 组：版本钉死 ====================

def _numpy_spec():
    text = open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8").read()
    # 解析器只取 world extra 里的 **numpy 那一条**（#253 后 world extra 不再只有
    # numpy——同层还声明了 Pillow；旧正则 `\["numpy([^"]*)"\]` 要求列表恰含一项，
    # 会把「world 声明了 Pillow」误读成「world 未声明 numpy」）。
    # 断言本体（有界区间 + 运行版本落在区间内）逐字未改，只放宽解析面。
    m = re.search(r'^world\s*=\s*\[([^\]]*)\]', text, re.M)
    assert m, "pyproject 的 world extra 未声明（零版本约束回归）"
    n = re.search(r'"numpy([^"]*)"', m.group(1))
    assert n, "pyproject 的 world extra 未声明 numpy 版本（零版本约束回归）"
    return n.group(1)


def test_d_numpy_declared_as_bounded_range_including_runtime():
    """D：numpy 声明须是有界区间，且当前运行版本落在区间内（标定版本被钉住）。"""
    spec = _numpy_spec()
    lo = re.search(r">=\s*(\d+)\.(\d+)", spec)
    hi = re.search(r"<\s*(\d+)\.(\d+)", spec)
    assert lo and hi, f"numpy 声明 {spec!r} 非有界区间（缺陷 #4 ① 形态）"
    lo_v = (int(lo.group(1)), int(lo.group(2)))
    hi_v = (int(hi.group(1)), int(hi.group(2)))
    cur = tuple(int(x) for x in np.__version__.split(".")[:2])
    assert lo_v <= cur < hi_v, \
        f"运行 numpy {np.__version__} 不在声明区间 {spec} 内（标定版本失锚）"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
