# -*- coding: utf-8 -*-
"""test_issue269_unverified_edge_protection_guard · lingshu issue #269 守卫
============================================================================
缺陷（lingshu issue #269 · 基线 92a46a3）：
  `LayeredStore.recalc_structural_importance(dry_run=False)` 的「因果上游度传播」
  读**全部** causal 边，不区分 `verified` / `confidence` / `source_evidence`：
  一条 importance 0.3 的随口猜想只要用 `add_edge` 默认参数（未验证、confidence
  0.5、source_evidence='inferred'）连出 3 条 causal 边到 importance 0.8 的节点，
  `concept_influence = 0.5×0.8×3 = 1.2 ≥ protect_threshold(1.0)` ⇒ 被
  `protect_node` 登记 + `no_forget` 标签 + importance 保底 0.9。
  保护一旦授予即形成闭环：`decay_cycle` 对触及受保护节点的边豁免衰减，**授予保护
  的未验证边本身也永不衰减**；且重算只加不撤，人工删掉全部边后再重算，保护 /
  no_forget / 0.9 原样保留（单向棘轮，全仓无撤销路径）。

修法（最小 · 按 issue 建议 1 主选项）：
  上游度传播只统计**已验证**（`verified=1`）的 causal 边——`SELECT ... FROM edges
  WHERE verified = 1`（本实现读 verified 列后在 Python 侧过滤，度数/出度统计不受
  影响）。未验证 / inferred / 默认 0.5 的边不再能授予永久保护。

判据来源：公开 issue #269「建议修复 1」——「`SELECT ... FROM edges WHERE verified
= 1`（或 `confidence >= 0.7 AND source_evidence = 'extracted'`）后再计算
influence」＋ 其最小复现实读（3 条未验证默认边 ⇒ 保护 + 0.9；对照 1 条边不保护）
＋ 分诊表 `triage_lingshu_ALL.md` 行 269（「不看 verified/confidence；3 条 0.5 边
即可越 1.0 → 永久保护+保底 0.9（单向棘轮）」）。仓内无规定该判据的理论章节，属
经验标定（本件 #269）。
边界（削掉的判别力，如实标注）：① 只收「未验证边不得授予永久保护」一条口径，
未采纳括号里的 `confidence>=0.7 AND source_evidence='extracted'` 替代口径——已
验证但 confidence 低的边仍计入（归一化问题 #274 不在本件）；② 不做保护撤销
（建议 2）与 `decay_cycle` 豁免面收窄（建议 3）。

断言组（把修复改回「读全部边」⇒ Q1/Q3/Q4 红）：
  Q1 默认未验证边不再授予保护：3 条 `add_edge(..., source_evidence='inferred')`
     后重算 ⇒ 节点不在 protections、无 no_forget、importance 保持 0.3、
     无 concept_influence、`protected == 0`。
  Q2 已验证边仍能授予保护（判别力保留）：同样 3 条边但 `verify_edge` 后重算 ⇒
     保护登记 + no_forget + importance 0.9（修复不得把「结构确认」整个删掉）。
  Q3 自我封存闭环消失：未验证边授予保护的路径不存在 ⇒ 猜想节点的 3 条未验证边
     在 300 轮 `decay_cycle` 后与对照组的边一样被删除（旧形态：受保护 ⇒ 不衰减）。
  Q4 对照量守恒：只连 1 条未验证边的对照猜想同样不被保护（避免只对 3 条特判）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue269_unverified_edge_protection_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _p(e, txt, imp):
    return e.add_perception(txt, importance=imp, skip_dedup=True).id


def _unprotected(s, nid):
    n = s.get_node(nid)
    return (nid not in s.get_protected_nodes()
            and "no_forget" not in n.tags
            and n.state_attributes.get("concept_influence") is None)


def test_q1_unverified_edges_grant_no_protection():
    """Q1：3 条默认（未验证/inferred/0.5）causal 边不得授予永久保护。"""
    e = _engine()
    s = e.store
    facts = [_p(e, "服务器 A 在 3 号凌晨宕机", 0.8),
             _p(e, "当晚订单量下降 40%", 0.8),
             _p(e, "客服工单激增", 0.8)]
    guess = _p(e, "猜测：都是因为机房空调坏了", 0.3)
    for f in facts:
        e.add_edge(guess, f, source_evidence="inferred")

    r = s.recalc_structural_importance(dry_run=False)
    n = s.get_node(guess)
    assert n.importance == 0.3, (
        "#269：未验证边把 importance 抬到了 %r（旧形态：0.9 单向棘轮）" % n.importance)
    assert guess not in s.get_protected_nodes(), (
        "#269：未验证边仍授予了保护登记（protections）——旧形态根因")
    assert "no_forget" not in n.tags, "#269：未验证边仍打了 no_forget 标签"
    assert n.state_attributes.get("concept_influence") is None, (
        "#269：未验证边仍写入了 concept_influence=%r"
        % n.state_attributes.get("concept_influence"))
    assert r["protected"] == 0 and r["influence_nodes"] == 0, (
        "#269：重算仍把未验证边计入上游度——protected=%r influence_nodes=%r"
        % (r["protected"], r["influence_nodes"]))


def test_q2_verified_edges_still_grant_protection():
    """Q2：判别力保留——已验证的 3 条 causal 边仍应授予保护（不得整段删掉）。"""
    e = _engine()
    s = e.store
    facts = [_p(e, "服务器 A 在 3 号凌晨宕机", 0.8),
             _p(e, "当晚订单量下降 40%", 0.8),
             _p(e, "客服工单激增", 0.8)]
    up = _p(e, "已验证的上游概念", 0.3)
    for f in facts:
        e.verify_edge(e.add_edge(up, f, source_evidence="extracted").id)

    r = s.recalc_structural_importance(dry_run=False)
    n = s.get_node(up)
    assert up in s.get_protected_nodes(), (
        "#269 非回归：已验证边不再授予保护（修复削掉了『结构确认』判别力）")
    assert "no_forget" in n.tags and abs(n.importance - 0.9) < 1e-6, (
        "#269 非回归：已验证边未抬 importance 到保底 0.9——importance=%r tags=%r"
        % (n.importance, n.tags))
    assert r["protected"] == 1, "非回归：protected=%r" % r["protected"]


def test_q3_self_sealing_loop_gone():
    """Q3：未验证边不再被保护冻结——300 轮衰减后与对照组的边一样被删除。"""
    e = _engine()
    s = e.store
    facts = [_p(e, "服务器 A 在 3 号凌晨宕机", 0.8),
             _p(e, "当晚订单量下降 40%", 0.8),
             _p(e, "客服工单激增", 0.8)]
    guess = _p(e, "猜测：都是因为机房空调坏了", 0.3)
    guess_edges = [e.add_edge(guess, f, source_evidence="inferred") for f in facts]
    ctrl = _p(e, "猜测：是 CDN 抖动", 0.3)
    ctrl_edge = e.add_edge(ctrl, facts[0], source_evidence="inferred")

    s.recalc_structural_importance(dry_run=False)
    for _ in range(300):
        e.decay_cycle()

    def conf(eid):
        row = s.conn.execute("SELECT confidence FROM edges WHERE id=?",
                             (eid,)).fetchone()
        return "已删除" if row is None else row[0]

    assert all(conf(x.id) == "已删除" for x in guess_edges), (
        "#269：猜想的未验证边仍被保护冻结——旧形态（受保护 ⇒ 300 轮不衰减）：%r"
        % [conf(x.id) for x in guess_edges])
    assert conf(ctrl_edge.id) == "已删除", (
        "前置对照：未被保护的对照边应已衰减删除——实测 %r" % conf(ctrl_edge.id))


def test_q4_single_edge_control_also_unprotected():
    """Q4：对照量守恒——1 条未验证边同样不授予保护（非 3 条特判）。"""
    e = _engine()
    s = e.store
    fact = _p(e, "服务器 A 在 3 号凌晨宕机", 0.8)
    guess = _p(e, "猜测：是 CDN 抖动", 0.3)
    e.add_edge(guess, fact, source_evidence="inferred")

    s.recalc_structural_importance(dry_run=False)
    assert _unprotected(s, guess), (
        "#269：单条未验证边也授予了保护——%r" % s.get_node(guess).state_attributes)
