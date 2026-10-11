#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_issue133_condition_rebuttal_guard —— 「声明域可被求值域反驳」守卫（lingshu #133）

缺陷（triage 判「成立」，取证 lingshu/core/condition_normalize.py:47-50 / :113-117）：
    E1 校验边的**两端**都落在声明域——`input_modality` 与 `color` 在
    `DOMAIN_SOURCE` 里都是 `"asserted"`（卡 v0.2 §二 域14 色彩 = asserted(枚举档)）。
    于是 `validate_edges` 号称的「声明域可被**求值域**反驳」在实现里根本不成立：
    触发条件只是两条**声明**不一致（且可在零像素证据下触发），而真正的求值域读数
    （`line_edge`，`DOMAIN_SOURCE == "evaluated"`）从未参与 E1。

修复：E1 的求值侧由 `color` 改为 `line_edge`（求值域），使 E1 成为真正的
    「声明域 ← 求值域」反驳边。

判据来源：
  · 声明/求值二分与「声明域 → 弱验证：来源可信 + 可被求值域反驳」
    —— `docs/图像大域语义条件卡_v0.2.md` §〇.A（AEIS 私域卡原件；本仓为
    `lingshu` 导出件，卡正文不入本仓。段落名逐字：「声明域……弱验证：来源可信
    + **可被求值域反驳**——如"声明=线稿"但求值器检测到"色块填充"→ 触发
    **校验失败 → DEFER 复核**」）。
  · 域14 色彩 = `asserted(枚举档)/evaluated(连续值)`、域13 线条/边缘 = `evaluated`
    —— 同上卡 §二 域清单（枚举档色彩属声明，线条/边缘属求值）。
  · E1 的求值侧改 `line_edge` 后，「色块填充」= 求值读数 `无线稿`
    —— 同卡 §〇.A 原文例的实装对应。

断言组（抽掉修复即红；本件不依赖任何既有守卫——`validate_edges` 此前无守卫）：
  G1 两端域地位：E1 涉及的两域必须**一为 asserted、一为 evaluated**（旧实现两端皆
     asserted ⇒ 直接红）。同时断言 E1 的求值侧是 `line_edge` 而非 `color`
     （旧实现写死 `color` ⇒ 红）。
  G2 真反驳（正向）：声明=线稿 ∧ 求值线条=无线稿 → `state == DEFER_REVIEW`
     且 violations 里含 edge=E1、domains 报出两域读数。
  G3 只声明冲突**不得**触发（负向，钉住「削掉的判别力」）：声明=线稿 ∧
     声明色彩=柔和（两条声明不一致，无求值证据）→ `state == "consistent"`。
     旧实现此处判 DEFER_REVIEW ⇒ 红。
  G4 不误伤：声明=线稿 ∧ 求值线条=淡线稿（求值域不反驳声明）→ consistent。
  G5 E2（求值↔求值）未受影响：运动姿态 ∧ 对称 → DEFER_REVIEW。
  G6 UNASSERTED 语义不变：两者皆缺 → consistent。

运行（仓根）：python -X utf8 -m pytest tests/test_issue133_condition_rebuttal_guard.py -q --no-header
             / python -X utf8 tests/test_issue133_condition_rebuttal_guard.py
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core import condition_normalize as C  # noqa: E402


def test_g1_e1_sides_are_declared_and_evaluated():
    """G1：E1 必须是「声明域 × 求值域」——一 asserted、一 evaluated。"""
    assert C.DOMAIN_SOURCE["input_modality"] == "asserted", C.DOMAIN_SOURCE
    # 旧实现的求值侧是 color（asserted）⇒ 下面这条会红
    assert C.DOMAIN_SOURCE["line_edge"] == "evaluated", C.DOMAIN_SOURCE
    assert C.DOMAIN_SOURCE["color"] == "asserted", (
        "色彩枚举档属声明域（卡 §二 域14）——E1 不得以它为求值侧")

    # 行为侧再钉一次：真求值读数参与 E1（旧实现写死 color ⇒ 此处无 E1）
    r = C.validate_edges({"input_modality": "线稿", "line_edge": "无线稿"})
    e1 = [v for v in r["violations"] if v["edge"] == "E1"]
    assert e1, "E1 未把求值域 line_edge 纳入判据：%r" % (r,)
    assert set(e1[0]["domains"]) == {"input_modality", "line_edge"}, e1[0]


def test_g2_evaluated_reading_rebuts_declaration():
    """G2：求值域读数与声明冲突 → DEFER_REVIEW（真反驳）。"""
    r = C.validate_edges({"input_modality": "线稿", "line_edge": "无线稿"})
    assert r["state"] == "DEFER_REVIEW", r
    assert [v["edge"] for v in r["violations"]] == ["E1"], r
    assert r["violations"][0]["domains"] == {
        "input_modality": "线稿", "line_edge": "无线稿"}, r["violations"][0]


def test_g3_declaration_vs_declaration_is_not_a_rebuttal():
    """G3：两条声明不一致**不构成**「求值反驳声明」——不得触发 E1。

    这是本修复削掉的判别力：旧实现把「声明=线稿 ∧ 声明色彩=柔和」判为
    「声明被求值反驳」，但两域同属声明域，没有任何求值证据。
    """
    r = C.validate_edges({"input_modality": "线稿", "color": "柔和"})
    assert r["state"] == "consistent", (
        "两条声明域的不一致被误报为「求值域反驳声明」：%r" % (r,))
    assert r["violations"] == [], r


def test_g4_evaluated_reading_consistent_with_declaration():
    """G4：求值域读数与声明一致 → 不触发（不误伤）。"""
    for le in ("淡线稿", "粗线稿"):
        r = C.validate_edges({"input_modality": "线稿", "line_edge": le})
        assert r["state"] == "consistent", (le, r)


def test_g5_e2_evaluated_pair_untouched():
    """G5：E2（求值↔求值）判据不受本次改动影响。"""
    r = C.validate_edges({"pose": "奔跑", "symmetry": "对称"})
    assert r["state"] == "DEFER_REVIEW", r
    assert [v["edge"] for v in r["violations"]] == ["E2"], r
    ok = C.validate_edges({"pose": "站姿", "symmetry": "对称"})
    assert ok["state"] == "consistent", ok


def test_g6_unasserted_domains_do_not_participate():
    """G6：两域皆缺（UNASSERTED）→ 无可校验，不触发。"""
    r = C.validate_edges({})
    assert r["state"] == "consistent", r
    assert r["violations"] == [], r


def main():
    fns = [test_g1_e1_sides_are_declared_and_evaluated,
           test_g2_evaluated_reading_rebuts_declaration,
           test_g3_declaration_vs_declaration_is_not_a_rebuttal,
           test_g4_evaluated_reading_consistent_with_declaration,
           test_g5_e2_evaluated_pair_untouched,
           test_g6_unasserted_domains_do_not_participate]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as exc:
            failed += 1
            print("FAIL %s | %s" % (fn.__name__, exc))
    print("-- %d passed, %d failed --" % (len(fns) - failed, failed))
    print("VERDICT=%s" % ("PASS" if not failed else "FAIL"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
