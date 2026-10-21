# -*- coding: utf-8 -*-
"""test_issue384_insight_verify_evidence_dedup_guard · #384 洞见证据「本次提交内去重」守卫
============================================================================
缺陷（lingshu issue #384 · P2 · 验证门槛可被同一句重复绕过）：
`LayeredStore.insight_verify` 的证据合并为
    ev = ev + [e for e in add if e not in ev]
只与**合并前的旧列表**比 ⇒ 本次传入的 `add` **内部重复不去重**。V1 判据是
`len(ev) >= 3`（「多次复现」），于是传 `['x','x','x']`（同一句证据重复 3 遍）即得
`evidence_count=3` ⇒ 误判 `verified`、importance 直升 0.9；而分 3 次提交同一句
（每次 1 条、与旧列表比被去重）仍是 `pending`——同一证据量、两种结论。

修法（最小）：逐条与**已合并结果**比后再追加（保持出现顺序），同时覆盖旧列表
与本次提交内部两处重复；状态判定与 importance 逻辑不变。
判据来源：公开 issue #384 标题所述最小复现（`['x','x','x']` ⇒ verified）。

守卫断言组（回退修复 ⇒ A/B 必红）：
  A 组（核心判据 · 有牙）：V1 + `['x','x','x']` ⇒ `evidence_count == 1`、
     `status == "pending"`、importance 不抬到 0.9。缺陷形态必红（count=3、
     verified、0.9）。
  B 组（等价性）：`['x','x','x']` 一次提交 与 分 3 次各提交 `'x'` 的
     `evidence_count`/`status` 必须一致——钉住「同证据量同结论」。
  C 组（正腿 · 防过度收紧）：V1 + 3 条**不同**证据 ⇒ 仍 verified（去重不得
     把真·多条证据也压成 1 条）。
  D 组（增量去重）：旧列表已有 `['a','b']`，本次追加 `['a','b','c']` ⇒
     count == 3 且 `'c'` 入库——钉住跨次与本次两处重复都被剥、且新证据不丢。

跨批覆盖重叠：`tests/test_issue38_insight_verify_evidence_gate_guard.py`（既有）
的 B4 用 `['e1','e2','e3']` 三条**不同**证据判 V1 verified，抽掉 #384 修复后
B4 仍绿（不同证据去重行为不变）⇒ 该守卫**不含**本件机制的判别力，不构成代偿；
本守卫 A/B 组为「只让 #384 机制生效」的夹具。

运行（lingshu 仓根）：
    python -X utf8 tests/test_issue384_insight_verify_evidence_dedup_guard.py
    python -X utf8 -m pytest tests/test_issue384_insight_verify_evidence_dedup_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_PASS, _FAIL = [], []


def ok(cond, name, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


def _store():
    return SpacetimeMemoryEngine(
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue384_"), "s.db")).store


# ---------- A 组：本次提交内重复不得凑数 ----------

def group_a_in_batch_duplicates_not_counted():
    print("== A 组：V1 + ['x','x','x'] ⇒ count=1、pending、不抬 importance ==")
    s = _store()
    nid = s.insight_record("V1 同句重复三遍")["node_id"]
    r = s.insight_verify(nid, "V1", ["x", "x", "x"])
    n = s.get_node(nid)
    print("     evidence_count=%s status=%s importance=%s"
          % (r["evidence_count"], r["status"], n.importance))
    ok(r["evidence_count"] == 1,
       "A1 同一句重复 3 遍 ⇒ evidence_count 必须为 1（旧实现=3 凑数）",
       r["evidence_count"])
    ok(r["status"] == "pending",
       "A2 去重后不足 3 条 ⇒ status=pending（旧实现误判 verified）", r["status"])
    ok(not (n.importance >= 0.9),
       "A3 不得因凑数证据把 importance 抬到 0.9", n.importance)


# ---------- B 组：一次提交 vs 分三次提交，结论必须一致 ----------

def group_b_batch_vs_sequential_equivalence():
    print("== B 组：['x','x','x'] 一次提交 ≡ 分 3 次各提交 'x' ==")
    s = _store()
    nid1 = s.insight_record("一次提交同句 3 遍")["node_id"]
    r1 = s.insight_verify(nid1, "V1", ["x", "x", "x"])
    nid2 = s.insight_record("分 3 次提交同句")["node_id"]
    r2 = None
    for _ in range(3):
        r2 = s.insight_verify(nid2, "V1", "x")
    print("     一次提交: count=%s status=%s | 分 3 次: count=%s status=%s"
          % (r1["evidence_count"], r1["status"], r2["evidence_count"], r2["status"]))
    ok(r1["evidence_count"] == r2["evidence_count"] == 1,
       "B1 两种提交方式 evidence_count 一致且为 1",
       (r1["evidence_count"], r2["evidence_count"]))
    ok(r1["status"] == r2["status"] == "pending",
       "B2 两种提交方式 status 一致且为 pending",
       (r1["status"], r2["status"]))


# ---------- C 组：真·多条证据仍判 verified（防过度收紧） ----------

def group_c_distinct_evidence_still_verified():
    print("== C 组：V1 + 3 条不同证据 ⇒ 仍 verified（去重不误伤真·多证据） ==")
    s = _store()
    nid = s.insight_record("V1 三条不同证据")["node_id"]
    r = s.insight_verify(nid, "V1", ["e1", "e2", "e3"])
    print("     evidence_count=%s status=%s" % (r["evidence_count"], r["status"]))
    ok(r["evidence_count"] == 3, "C1 三条不同证据 count=3", r["evidence_count"])
    ok(r["status"] == "verified", "C2 三条不同证据仍 verified（旧口径保留）",
       r["status"])


# ---------- D 组：跨次 + 本次重复一并剥，且新证据不丢 ----------

def group_d_incremental_dedup_keeps_new():
    print("== D 组：旧 ['a','b'] + 本次 ['a','b','c'] ⇒ count=3、'c' 入库 ==")
    s = _store()
    nid = s.insight_record("增量去重")["node_id"]
    s.insight_verify(nid, "V1", ["a", "b"])
    r = s.insight_verify(nid, "V1", ["a", "b", "c"])
    sa = s.get_node(nid).state_attributes
    ev = sa["insight"]["verification"]["evidence"]
    print("     evidence=%s count=%s status=%s" % (ev, r["evidence_count"], r["status"]))
    ok(r["evidence_count"] == 3, "D1 跨次与本次重复都剥、count=3", r["evidence_count"])
    ok(ev == ["a", "b", "c"], "D2 新证据 'c' 入库且保持出现顺序", ev)
    ok(r["status"] == "verified", "D3 累计 3 条不同证据 ⇒ verified", r["status"])


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_a_in_batch_duplicates_not_counted()
    group_b_batch_vs_sequential_equivalence()
    group_c_distinct_evidence_still_verified()
    group_d_incremental_dedup_keeps_new()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（insight_verify 证据在本次提交内部与跨次提交两处均去重；"
          "同证据量同结论）")
    return 0


def test_issue384_insight_verify_evidence_dedup_guard():
    """pytest 入口（脚本/pytest 双模式）。"""
    run_checks()
    assert not _FAIL, "%d checks failed: %s" % (len(_FAIL), _FAIL)


if __name__ == "__main__":
    sys.exit(run_checks())
