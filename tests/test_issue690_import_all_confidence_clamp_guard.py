# -*- coding: utf-8 -*-
"""test_issue690_import_all_confidence_clamp_guard · import_all 的 confidence 钳制守卫（#690 P1-b）
==================================================================================
缺陷（#690 P1-b，复核确认成立）：`import_all` 的值域钳制块（#574 修谱区）只补了
`importance`、漏了**同门面同义务**的 `confidence`——属「修复只补一半字段」。
旧实现绕过 `add_node` 直接 `INSERT OR REPLACE`，畸形 confidence（`1e6` / `-5.0` /
`NaN`）经回灌**原样落库**（实测源库两列直改 `1e6` → `export_all` → 新库
`import_all` 后 `importance=1.0`（已钳）、`confidence=1000000.0`（未钳））。

守卫判据：
  G1 越界值（1e6 / -5.0）经 import_all 落库后必须钳到 [0.0, 1.0]；
  G2 防过修：合法范围内值（0.37）原样通过、逐位不变（两列）；
  G3 同门面同口径：`confidence` 与 `importance` **同输入 ⇒ 同结果**（含
     越界/合法/边界/None/非有限五类）；
  G4 非有限值（NaN/±inf）与 `importance` 同口径回落 0.5（不落 NULL/不越界）。

运行：python -X utf8 -m pytest tests/test_issue690_import_all_confidence_clamp_guard.py -q
"""
from __future__ import annotations

import json
import os
import tempfile

from lingshu.core.core import SpacetimeMemoryEngine


def _import_rows(rows):
    """把给定 nodes 行写进备份 JSON，导入新库，返回 {id: (importance, confidence)}。"""
    d = tempfile.mkdtemp()
    bak = os.path.join(d, "backup.json")
    with open(bak, "w", encoding="utf-8") as f:
        json.dump({"nodes": rows, "edges": []}, f, ensure_ascii=False)
    e = SpacetimeMemoryEngine(os.path.join(d, "dst.db"))
    try:
        e.import_all(bak)
        c = e.store.conn.cursor()
        c.execute("SELECT id, importance, confidence FROM nodes ORDER BY id")
        return {r[0]: (r[1], r[2]) for r in c.fetchall()}
    finally:
        e.close()


def _row(nid, imp, conf):
    """最小节点行（与 test_issue574 同款：只给必要列）。"""
    return {"id": nid, "content": f"节点{nid}", "layer": "knowledge",
            "importance": imp, "confidence": conf}


# --------------------------------------------------------------------------- G1
def test_g1_out_of_range_confidence_clamped():
    """G1：越界 confidence 经 import_all 落库后必须钳到 [0.0, 1.0]。

    缺陷形态：旧实现 `confidence=1e6` 回灌后原样落库 `1000000.0`（此断言红），
    而同处 `importance` 已被 #574 钳到 1.0 —— 正是「只补一半字段」。
    """
    got = _import_rows([
        _row("n_hi", 1e6, 1e6),
        _row("n_lo", -5.0, -5.0),
    ])
    imp_hi, conf_hi = got["n_hi"]
    imp_lo, conf_lo = got["n_lo"]
    assert conf_hi == 1.0, ("#690 P1-b：越界高 confidence 未钳到上界"
                            "（实测 %r；importance 同处为 %r）" % (conf_hi, imp_hi))
    assert conf_lo == 0.0, ("#690 P1-b：越界低 confidence 未钳到下界"
                            "（实测 %r；importance 同处为 %r）" % (conf_lo, imp_lo))
    # 与同处 importance 对照（同缺陷形态、同期望）
    assert imp_hi == 1.0 and imp_lo == 0.0, ("前提失效：importance 未按 #574 钳制",
                                             (imp_hi, imp_lo))


# --------------------------------------------------------------------------- G2
def test_g2_legal_value_unchanged():
    """G2（防过修）：合法范围内 confidence=0.37 回灌后逐位不变（importance 同）。"""
    got = _import_rows([_row("n_ok", 0.37, 0.37)])
    imp, conf = got["n_ok"]
    assert imp == 0.37, ("#690：合法 importance 被误改：%r" % (imp,))
    assert conf == 0.37, ("#690：合法 confidence 被误改（防过修）：%r" % (conf,))


# --------------------------------------------------------------------------- G3
def test_g3_same_domain_as_importance():
    """G3：`confidence` 与 `importance` **同输入 ⇒ 同结果**（同门面同口径）。

    覆盖越界 / 合法 / 边界 / NULL / 非有限五类；两列在同一行给同值，
    回灌后必须逐位相等——否则即「两列口径分叉」（#690 P1-b 的根因形态）。
    """
    cases = [
        ("v_hi", 1e6),
        ("v_lo", -5.0),
        ("v_legal", 0.37),
        ("v_zero", 0.0),
        ("v_one", 1.0),
        ("v_null", None),
        ("v_nan", float("nan")),
        ("v_inf", float("inf")),
        ("v_ninf", float("-inf")),
    ]
    got = _import_rows([_row(nid, v, v) for nid, v in cases])
    for nid, v in cases:
        imp, conf = got[nid]
        assert imp == conf, (
            "#690 P1-b：importance/confidence 同输入而结果分叉"
            "（input=%r ⇒ importance=%r, confidence=%r）" % (v, imp, conf))
    # 并钉死越界/非有限的绝对读数（防止两列「同错」）
    assert got["v_hi"] == (1.0, 1.0), got["v_hi"]
    assert got["v_lo"] == (0.0, 0.0), got["v_lo"]
    assert got["v_legal"] == (0.37, 0.37), got["v_legal"]


# --------------------------------------------------------------------------- G4
def test_g4_non_finite_defaults_same_as_importance():
    """G4：非有限值（NaN/±inf）与 importance 同口径回落 0.5（不落 NULL、不越界）。"""
    got = _import_rows([
        _row("n_nan", float("nan"), float("nan")),
        _row("n_inf", float("inf"), float("inf")),
        _row("n_ninf", float("-inf"), float("-inf")),
    ])
    for nid in ("n_nan", "n_inf", "n_ninf"):
        imp, conf = got[nid]
        assert imp == 0.5, ("#690：非有限 importance 未回落 0.5（%r）" % (imp,))
        assert conf == 0.5, (
            "#690 P1-b：非有限 confidence 未与 importance 同口径回落 0.5"
            "（实测 %r）——落 NULL 会让按 confidence 加权/阈值判定的面失准" % (conf,))


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
