# -*- coding: utf-8 -*-
"""test_issue735_multi_seed_stability_order · `summarize` ① 号「读出稳定性」必须与行序无关（#735）
================================================================================================
缺陷（#735，改前实测复现）：``lingshu/gen/hexgen_multi_seed.py::summarize`` 的 ① 号块用
``zip(rs, rs[1:])`` 比较**相邻**两张，而相邻关系由调用方给的 ``reads`` **行序**决定。
同一批 5 张读数（3 张读 circle、2 张读 square），只换行序：

    seed-order  [A B C D E]  shape_stability = 0.75
    interleaved [A D B E C]  shape_stability = 0.00      ← 同语料，读数差 0.75
    reverse     [E D C B A]  shape_stability = 0.75

「稳定度」是同一批渲染之间读出是否一致的**集合性质**，不该依赖输入的排列。

修法（最小）：① 号块比较前按**规范序**（先 seed，再读出内容）排序 ⇒ 读数只由 ``reads`` 的
**多重集**决定。命令行两条路径（``--gen``/``--measure``）产出的行序恰为 seed 升序 ⇒
既有读数**逐位不变**。

判据（本件）：
  G1 同多重集**任意置换** ⇒ ``stability`` 逐位相同（含 4 个定向置换与 24 个全置换）；
  G2 行序为 seed 升序时，读数 == 旧口径在**该序**上的相邻对读数（命令行默认跑不受影响）；
  G3 真实不同的语料读数**仍不同**（防改成恒等/常量）。
正/负对照：
  C1 正对照：同输入两次调用结果全等（确定性，不确定性只来自行序）；
  C2 负对照：把**旧口径**（相邻对）在同一多重集上重算，读数**确实随行序变**——证明 G1
      测的是一个真实现象、有判别力（不是恒真断言）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue735_multi_seed_stability_order.py -q
"""
from __future__ import annotations

import itertools

from lingshu.gen import hexgen_c1_real as C
from lingshu.gen import hexgen_multi_seed as MS

SID = "s1"
ITEMS = [{"sid": SID, "shape": "circle",
          "truth": {"shape": "circle", "color": "red", "pattern": "plain",
                    "n": 3, "cells": {"r4"}}}]


def _mk(seed, shape, n=3, color="red"):
    """一条 read（形状多重集 = shape × n）。`feats` 与形状相关 ⇒ ③ 号块统计有定义。"""
    base = {"circle": 0.2, "square": 0.9}.get(shape, 0.5)
    fv = [base + 0.01 * i for i in range(len(C.FEATS))]
    return {"sid": SID, "seed": seed, "n_read": n,
            "shapes": sorted([shape] * n), "colors": sorted([color] * n),
            "patterns": sorted(["plain"] * n), "cells": sorted(["r4"] * n),
            "feats": [fv for _ in range(n)]}


#   同一批：seed 11/22/33 读 circle，44/55 读 square（与 #735 立案读数同形）
BASE = [_mk(11, "circle"), _mk(22, "circle"), _mk(33, "circle"),
        _mk(44, "square"), _mk(55, "square")]
OTHER = [_mk(11, "circle"), _mk(22, "circle"), _mk(33, "circle"),
         _mk(44, "square"), _mk(66, "circle")]          # 4 circle + 1 square


def _stab(rows):
    return MS.summarize(rows, ITEMS)["stability"]


def _old_stab(rows):
    """**旧口径**（改前实现）逐字复制：按给定行序做相邻对比较。"""
    out = {}
    for k in ("shapes", "colors", "patterns", "cells"):
        out["shape" if k == "shapes" else k[:-1]] = (
            sum(1 for a, b in zip(rows, rows[1:]) if a[k] == b[k]) / (len(rows) - 1))
    out["count"] = (sum(1 for a, b in zip(rows, rows[1:]) if a["n_read"] == b["n_read"])
                    / (len(rows) - 1))
    return out


def test_g1_stability_is_permutation_invariant():
    """G1：同多重集任意置换 ⇒ ① 号读数逐位相同（改前 seed 序 0.75 vs 交错序 0.0）。"""
    assert len(BASE) >= 2, "构造异常：样本数不足"
    assert len({(r["seed"], tuple(r["shapes"])) for r in BASE}) == len(BASE), \
        "构造异常：样本不是互异读数"
    ref = _stab(BASE)
    vals = []
    perms = list(itertools.permutations(BASE))
    assert len(perms) == 120, "构造异常：5 张应有 120 个置换，实得 %d" % len(perms)
    for perm in perms:
        got = _stab(list(perm))
        vals.append(got["shape"])
        assert got == ref, "#735 行序 %r 下读数 %r != 规范读数 %r" % (
            [r["seed"] for r in perm], got, ref)
    assert max(vals) - min(vals) == 0.0, "#735 shape_stability 随行序波动 %r" % (
        max(vals) - min(vals))


def test_g2_seed_order_readings_unchanged_vs_legacy_adjacent_pair():
    """G2：行序为 seed 升序时，读数 == 旧口径在该序上的相邻对读数（默认跑不受影响）。"""
    rows = sorted(BASE, key=lambda r: r["seed"])
    assert [r["seed"] for r in rows] == [11, 22, 33, 44, 55], "构造异常：序不对"
    got, old = _stab(rows), _old_stab(rows)
    for k in ("shape", "color", "pattern", "count", "cell"):
        assert abs(got[k] - old[k]) < 1e-9, (
            "#735 seed 序下 %s 读数 %r 与旧口径 %r 不一致（命令行默认跑被改动）" % (k, got[k], old[k]))


def test_g3_different_corpus_still_reads_differently():
    """G3：真实不同的语料读数**仍不同**（防改成恒等）。"""
    a = _stab(sorted(BASE, key=lambda r: r["seed"]))
    b = _stab(sorted(OTHER, key=lambda r: r["seed"]))
    assert a["shape"] != b["shape"], (
        "#735 不同语料读数相同（%r）⇒ 指标被改成恒等/失去判别力" % a["shape"])
    assert a["shape"] == 0.75 and b["shape"] == 0.5, (
        "#735 期望 {3 circle,2 square}=0.75、{4 circle,1 square}=0.5，实得 %r / %r"
        % (a["shape"], b["shape"]))


def test_c1_positive_control_deterministic_on_fixed_input():
    """C1 正对照：同输入两次调用结果全等（确定性；不确定性只来自行序）。"""
    rows = list(BASE)
    assert MS.summarize(rows, ITEMS) == MS.summarize(list(rows), ITEMS), \
        "正对照失败：同输入两次调用不等"


def test_c2_negative_control_legacy_metric_is_order_sensitive():
    """C2 负对照：旧口径（相邻对）在同一多重集上读数**确实随行序变** ⇒ G1 有判别力。"""
    vals = {_old_stab(list(p))["shape"] for p in itertools.permutations(BASE)}
    assert len(vals) > 1, "负对照失败：旧口径读数不随行序变（%r）⇒ G1 无判别力" % vals
    assert 0.0 in vals and 0.75 in vals, (
        "负对照失败：期望旧口径在置换里同时出现 0.0 与 0.75，实得 %r" % sorted(vals))
