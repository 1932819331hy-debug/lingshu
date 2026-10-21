# -*- coding: utf-8 -*-
"""test_issue299_structural_importance_idempotent_guard · #299 结构重要性重算幂等守卫
============================================================================
缺陷（lingshu issue #299 · P2 · 「重算」实为「累加」）：
`LayeredStore.recalc_structural_importance` 旧实现
    boost = min(beta * ci + gamma * conn, boost_cap)
    new_imp = min(1.0, imp + boost)
每次以**当前**（已被上次提升过的）importance 为底重算再累加 ⇒ 结构一条边未变，
反复调用仍持续抬升：中心节点 importance=0.3、连 6 条边，连续 5 次
`dry_run=False` ⇒ `[(0.45,1),(0.6,1),(0.75,1),(0.9,1),(1.0,1)]`，总提升 0.7，
`BOOST_CAP=0.15` 形同虚设；`dry_run=True` 预览在已应用后仍报同一节点可再升。

修法（最小）：应用时把**本次结构提升总量**写入
`state_attributes.structural_boost`（复用 `_write_influence` 的 `key` 参数）；
重算时以 `base = imp - structural_boost` 为地板，`new_imp = min(1.0, base +
boost)`、`delta = new_imp - imp`——结构增强时只补差额、结构不变时 delta=0。
判据来源：公开 issue #299「修复建议（最小补丁）」＋ 其最小复现实读。

守卫断言组（回退修复 ⇒ A/B/C 必红）：
  A 组（核心判据 · 有牙）：结构不变连续 5 次 `dry_run=False` ⇒ importance 恒为
     首次提升值 0.45、`applied` 只在首次为 1（旧实现 `[(0.45,1)..(1.0,1)]`），
     总提升 ≤ `boost_cap`。
  B 组（幂等 · dry_run）：应用一次后 `dry_run=True` ⇒ 该节点不再出现在 `movers`
     （旧实现 imp=0.45<0.95，仍报 +0.15）。
  C 组（只补差额）：首次以较小 cap 提升，结构不变但 cap 放宽 ⇒ 只补到新目标、
     总提升仍 ≤ 新 `boost_cap`，不叠加两次。
  D 组（标记落库）：应用后 `state_attributes.structural_boost == 首次提升量`，
     且不影响 `concept_influence` 通道（`_write_influence` 默认 key 不变）。

跨批覆盖重叠：`tests/test_issue269_unverified_edge_protection_guard.py`（既有）
在 `recalc_structural_importance` 上只断言「未验证边不得授予永久保护」（走
`_protect_upstream` 分支，命中即 `continue`，**不进入 boost 分支**）；其 Q1/Q4
用 3 条 / 1 条 causal 边、度数 < `min_degree=5`，同样不进 boost 分支 ⇒ 该守卫
**不含**本件机制的判别力，不构成代偿。本守卫 A/B 组为「只让 #299 机制生效」的
夹具（#269 修复不改变 boost 累加行为，抽掉 #299 修复后本守卫必红）。

运行（lingshu 仓根）：
    python -X utf8 tests/test_issue299_structural_importance_idempotent_guard.py
    python -X utf8 -m pytest tests/test_issue299_structural_importance_idempotent_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import warnings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
warnings.filterwarnings("ignore")

from lingshu.core.core import SpacetimeMemoryEngine, EdgeType  # noqa: E402

_PASS, _FAIL = [], []


def ok(cond, name, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


def _engine():
    return SpacetimeMemoryEngine(
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue299_"), "s.db"))


def _hub(e, n_edges=6, imp=0.3):
    hub = e.add_perception("中心节点", importance=imp, skip_dedup=True)
    for i in range(n_edges):
        o = e.add_perception("外围%d号节点内容不同%d" % (i, i),
                             importance=0.1, skip_dedup=True)
        e.add_edge(hub.id, o.id, EdgeType.SIMILAR)
    return hub.id


def _sb(s, nid):
    return s.get_node(nid).state_attributes.get("structural_boost")


# ---------- A 组：结构不变连续 5 次 ⇒ 不叠加 ----------

def group_a_repeated_recalc_no_stacking():
    print("== A 组：结构不变连续 5 次 recalc ⇒ importance 恒 0.45、不叠加 ==")
    e = _engine(); s = e.store
    hub = _hub(e)
    seq = []
    for _ in range(5):
        r = s.recalc_structural_importance(dry_run=False)
        seq.append((round(s.get_node(hub).importance, 4), r["applied"]))
    print("     每次调用后 (importance, applied):", seq)
    ok(seq[0][0] == 0.45 and all(x[0] == 0.45 for x in seq),
       "A1 结构不变 ⇒ importance 恒为首次提升值 0.45（旧实现 (0.45,0.6,0.75,0.9,1.0)）",
       seq)
    ok(seq[0][1] == 1 and all(x[1] == 0 for x in seq[1:]),
       "A2 只有首次 applied=1，其后均为 0（旧实现每次都 applied=1）", seq)
    ok(round(seq[-1][0] - 0.3, 4) <= 0.15 + 1e-9,
       "A3 总提升 ≤ boost_cap=0.15（旧实现 0.7）", round(seq[-1][0] - 0.3, 4))


# ---------- B 组：应用后 dry_run 不再报该节点 ----------

def group_b_dry_run_after_apply_no_movers():
    print("== B 组：应用一次后 dry_run ⇒ hub 不再出现在 movers ==")
    e = _engine(); s = e.store
    hub = _hub(e)
    s.recalc_structural_importance(dry_run=False)
    mv = [m for m in s.recalc_structural_importance(dry_run=True)["movers"]
          if m["node_id"] == hub]
    print("     dry_run movers(hub)=%s importance=%.4f"
          % (mv, s.get_node(hub).importance))
    ok(mv == [],
       "B1 应用后 dry_run 不报该节点（旧实现 imp=0.45<0.95 仍报 +0.15）", mv)
    ok(s.recalc_structural_importance(dry_run=True)["applied"] == 0,
       "B2 dry_run 模式 applied 恒为 0（不写库）")


# ---------- C 组：只补差额，不叠加两次 ----------

def group_c_only_pays_difference():
    print("== C 组：首次 cap=0.05，其后 cap=0.15 ⇒ 只补到 0.45，总提升 ≤ 0.15 ==")
    e = _engine(); s = e.store
    hub = _hub(e)
    r1 = s.recalc_structural_importance(dry_run=False, boost_cap=0.05)
    imp1 = round(s.get_node(hub).importance, 4)
    r2 = s.recalc_structural_importance(dry_run=False, boost_cap=0.15)
    imp2 = round(s.get_node(hub).importance, 4)
    print("     首次 cap=0.05 ⇒ imp=%s (applied=%s)；其后 cap=0.15 ⇒ imp=%s (applied=%s)"
          % (imp1, r1["applied"], imp2, r2["applied"]))
    ok(imp1 == 0.35, "C1 首次 cap=0.05 ⇒ 0.3+0.05=0.35", imp1)
    ok(imp2 == 0.45, "C2 放宽 cap 只补差额 0.10 ⇒ 0.45（非 0.35+0.15=0.50）", imp2)
    ok(round(imp2 - 0.3, 4) <= 0.15 + 1e-9,
       "C3 总提升 ≤ 新 cap=0.15", round(imp2 - 0.3, 4))


# ---------- D 组：标记落库且不串通道 ----------

def group_d_marker_written():
    print("== D 组：structural_boost 标记落库，concept_influence 通道不受影响 ==")
    e = _engine(); s = e.store
    hub = _hub(e)
    s.recalc_structural_importance(dry_run=False)
    sb = _sb(s, hub)
    print("     structural_boost=%s concept_influence=%s"
          % (sb, s.get_node(hub).state_attributes.get("concept_influence")))
    ok(sb is not None and abs(sb - 0.15) < 1e-9,
       "D1 structural_boost 记录本次提升量 0.15（作为下次地板）", sb)
    ok(s.get_node(hub).state_attributes.get("concept_influence") is None,
       "D2 纯度数节点无因果上游度 ⇒ concept_influence 未被误写")


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_a_repeated_recalc_no_stacking()
    group_b_dry_run_after_apply_no_movers()
    group_c_only_pays_difference()
    group_d_marker_written()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（recalc_structural_importance 幂等：结构不变不叠加、"
          "总结构提升 ≤ BOOST_CAP）")
    return 0


def test_issue299_structural_importance_idempotent_guard():
    """pytest 入口（脚本/pytest 双模式）。"""
    run_checks()
    assert not _FAIL, "%d checks failed: %s" % (len(_FAIL), _FAIL)


if __name__ == "__main__":
    sys.exit(run_checks())
