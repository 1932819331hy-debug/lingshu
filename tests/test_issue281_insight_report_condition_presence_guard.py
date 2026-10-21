# -*- coding: utf-8 -*-
"""test_issue281_insight_report_condition_presence_guard · #281 条件统计口径守卫
============================================================================
缺陷（lingshu issue #281 · P2 · 报告层分组口径失真）：`LayeredStore.insight_report`
的条件统计旧实现
    with_ = [x for x in candidates if x["conditions"].get(k)]
    without = [x for x in candidates if not x["conditions"].get(k)]
按 `get(k)` 的**真值**分组，把「**键存在但值为假**」（`False`/`0`/`""`）与
「**键不存在**」（未记录该条件）混同——两者都落进 `without` 组，于是未测样本被
当作「该条件为假」的样本，统计口径失真（`n_without` 虚高、对照基准被污染）。

修法（最小）：判据由「键存在」收窄为「**显式提供**」——`_normalize_conditions`
早已写明「显式提供 = 0.7 ／ 默认回落 = 0.3」并把它落在 `conditions.confidence`
里；本件只是**把它读出来**：`confidence[k] >= 0.7` 才算 present。无 `confidence`
标记时（旧库样本／裸 SQL 构造）**回落**旧判据「键存在」。
判据来源：公开 issue #281「修复建议 1」——「未显式提供（confidence=0.3）的条件
不进入对应统计」。

★ **A–D 组与 E 组是两代守卫，勿混淆**：
  · A–D 组（`f0a9dc7` 落地）用 `_raw_node` **裸 SQL** 构造样本——那种样本的
    `conditions` 里**没有 `confidence` 标记**，只能走「回落」分支，因此**对真实
    生产路径零判别力**：经 `insight_record` 写入的节点 5 个条件键**恒存在**（默认
    值补齐），A–D 组的判据在真实路径下依然全绿而真实路径仍坏。
  · **E 组（本件新增）用 `insight_record` + `insight_verify` 生产路径构造**，
    这才是钉住真实缺陷形态的那一组。回退修复 ⇒ E 组必红（A–D 组仍绿）。

守卫断言组（回退修复 ⇒ A/B 或 E 必红）：
  A 组（核心判据 · 有牙）：5 present-truthy(verified) + 5 present-falsy(pending)
     + 5 key-absent(pending)。`premise_questioned` 的 `n_without` 必须为 **5**
     （只数 present-falsy），缺陷形态下为 **10**（把 5 条 absent 也计入）。
  B 组：absent 样本**不得**进入任一组的判据——`n_with + n_without` 必须等于
     present 样本数（10），而非候选总数（15）。
  C 组（正腿 · 防过度收紧）：`n_with == 5`（present-truthy 仍全数计入）。
  D 组（对照口径）：同数据下若把 absent 也当 without（缺陷形态），`n_without`
     会是 10 ⇒ 本组用「构造 absent 数 = 5」反推，断言 n_without 严格小于候选
     总数 15，钉住「absent 未混入」。
  E 组（**生产路径** · 本件新增 · 回退即红）：
     5 条 `insight_record(conditions={"premise_questioned": True})` + V3 验证
     （⇒ verified）、5 条 `insight_record(conditions={"premise_questioned": False})`
     **不验证**（⇒ pending）、10 条 `insight_record()` **不传该条件** + V3 验证
     （⇒ verified）。E1/E2 断言 `n_without == 5`、`cer_without == 0.0`；E3 断言
     `n_with + n_without == 10`（未显式提供的 10 条不进本条件统计）。
     缺陷形态（判据回落为「键存在」）下：20 条键全在 ⇒ without 组 = 5 显式 False
     + 10 默认 False = **15**、`cer_without = 10/15 = 0.667` ⇒ E1/E2/E3 全红。

跨批覆盖重叠：`tests/` 下无其它文件引用 `insight_report`（grep 全 `tests/` 无命中）
⇒ 本件守卫无前批守卫可代偿；A/B 组为「只让 #281 机制生效」的夹具（#301/#384 的
修复只动 `insight_verify` 的标签/证据，不影响本函数的条件分组）。

运行（lingshu 仓根）：
    python -X utf8 tests/test_issue281_insight_report_condition_presence_guard.py
    python -X utf8 -m pytest tests/test_issue281_insight_report_condition_presence_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_PASS, _FAIL = [], []


def ok(cond, name, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


def _raw_node(s, nid, status, conds):
    """直接落一条 insight_event 节点（可控 conditions.values 的键存在性）。"""
    sa = json.dumps(
        {"insight": {"verification": {"status": status},
                     "conditions": {"values": conds}}},
        ensure_ascii=False)
    now = time.time()
    s.conn.execute(
        "INSERT INTO nodes (id, content, modality, temporal_coordinate, importance, "
        "confidence, layer, access_count, last_access, created_at, tags, "
        "semantic_coordinates, state_attributes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (nid, "x", "text", now, 0.5, 0.5, "knowledge", 0, now, now,
         json.dumps(["insight_event", "insight-layer"]), "{}", sa))
    s.conn.commit()


def _fixture():
    """5 present-truthy(verified) + 5 present-falsy(pending) + 5 absent(pending)。"""
    s = SpacetimeMemoryEngine(
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue281_"), "s.db")).store
    for i in range(5):
        _raw_node(s, "t%d" % i, "verified", {"premise_questioned": True})
    for i in range(5):
        _raw_node(s, "f%d" % i, "pending", {"premise_questioned": False})
    for i in range(5):
        _raw_node(s, "a%d" % i, "pending", {})   # 键不存在
    return s


def _stat(s, key="premise_questioned"):
    rep = s.insight_report()
    for c in rep["conditions"]:
        if c["condition"] == key:
            return c, rep
    return None, rep


# ---------- A 组：n_without 只数 present-falsy ----------

def group_a_without_excludes_absent():
    print("== A 组：present-falsy 5 条 ⇒ n_without=5（absent 不得计入） ==")
    s = _fixture()
    st, rep = _stat(s)
    print("     total=%s stat=%s" % (rep["total"], st))
    ok(st is not None, "A1 premise_questioned 进入统计（两组各 ≥5）", st)
    if st is None:
        return
    ok(st["n_without"] == 5,
       "A2 n_without 只数「键存在但值为假」=5（缺陷形态把 5 条键不存在的也计入 ⇒ 10）",
       st["n_without"])


# ---------- B 组：absent 不进任一组 ----------

def group_b_absent_not_in_either_group():
    print("== B 组：n_with + n_without == present 数（10），而非候选总数（15） ==")
    s = _fixture()
    st, rep = _stat(s)
    if st is None:
        ok(False, "B1 premise_questioned 进入统计", st)
        return
    print("     n_with=%s n_without=%s total=%s"
          % (st["n_with"], st["n_without"], rep["total"]))
    ok(st["n_with"] + st["n_without"] == 10,
       "B1 两组样本和=10（present 样本数）；absent 5 条不进入本条件统计",
       (st["n_with"], st["n_without"]))
    ok(st["n_with"] + st["n_without"] != rep["total"],
       "B2 两组样本和 ≠ 候选总数 15（absent 未被混入）",
       (st["n_with"] + st["n_without"], rep["total"]))


# ---------- C 组：present-truthy 仍全数计入 ----------

def group_c_present_truthy_counted():
    print("== C 组：present-truthy 5 条仍全数计入 with_ ==")
    s = _fixture()
    st, _ = _stat(s)
    if st is None:
        ok(False, "C1 premise_questioned 进入统计", st)
        return
    ok(st["n_with"] == 5, "C1 n_with=5（present-truthy 未被误排除）", st["n_with"])
    ok(st["cer_with"] == 1.0, "C2 with 组 CER=1.0（5 条全 verified）", st["cer_with"])
    ok(st["cer_without"] == 0.0, "C3 without 组 CER=0.0（5 条全 pending）",
       st["cer_without"])


# ---------- D 组：absent 数变多不得改变分组 ----------

def group_d_absent_count_invariant():
    print("== D 组：absent 样本数从 5 增到 15 ⇒ n_without 仍为 5 ==")
    s = SpacetimeMemoryEngine(
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue281d_"), "s.db")).store
    for i in range(5):
        _raw_node(s, "t%d" % i, "verified", {"premise_questioned": True})
    for i in range(5):
        _raw_node(s, "f%d" % i, "pending", {"premise_questioned": False})
    for i in range(15):
        _raw_node(s, "a%d" % i, "pending", {})
    st, rep = _stat(s)
    print("     total=%s stat=%s" % (rep["total"], st))
    if st is None:
        ok(False, "D1 premise_questioned 进入统计", st)
        return
    ok(st["n_without"] == 5,
       "D1 absent 增多不改 n_without（缺陷形态下 absent 越多 n_without 越大）",
       st["n_without"])


# ---------- E 组：生产路径夹具（insight_record/insight_verify） ----------

def _prod_fixture():
    """生产路径构造：5 显式 True + V3(verified) ／ 5 显式 False + 不验证(pending)
    ／ 10 不传该条件 + V3(verified)。

    ★与 A–D 组的关键差别：这里经 `insight_record` ⇒ `_normalize_conditions` 给
    **未显式提供**的条件也填了默认值（`premise_questioned=False`），但 `confidence`
    标记为 0.3（默认回落）⇒ 「键存在」判据在这批样本上**恒真**，未测条件被混入
    `without`；只有「显式提供（confidence=0.7）」判据才能把它们排出去。
    """
    s = SpacetimeMemoryEngine(
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue281e_"), "s.db")).store
    for i in range(5):
        r = s.insight_record("e_true_%d" % i, conditions={"premise_questioned": True})
        s.insight_verify(r["node_id"], level="V3", evidence="ev_true_%d" % i)
    for i in range(5):
        s.insight_record("e_false_%d" % i, conditions={"premise_questioned": False})
    for i in range(10):
        r = s.insight_record("e_absent_%d" % i)          # 不传该条件
        s.insight_verify(r["node_id"], level="V3", evidence="ev_absent_%d" % i)
    return s


def group_e_production_path_explicit_only():
    print("== E 组（生产路径 insight_record）：n_without=5 / cer_without=0.0 ==")
    s = _prod_fixture()
    st, rep = _stat(s)
    print("     total=%s stat=%s" % (rep["total"], st))
    if st is None:
        ok(False, "E1 premise_questioned 进入统计", st)
        return
    ok(st["n_without"] == 5,
       "E1 n_without=5（只数显式提供且为假的 5 条；缺陷形态把 10 条未显式提供的"
       "默认 False 也计入 ⇒ 15）", st["n_without"])
    ok(st["cer_without"] == 0.0,
       "E2 cer_without=0.0（without 组 5 条全 pending；缺陷形态 = 10/15 = 0.667）",
       st["cer_without"])
    ok(st["n_with"] + st["n_without"] == 10,
       "E3 两组样本和=10（显式提供的样本数）；未显式提供的 10 条不进本条件统计",
       (st["n_with"], st["n_without"]))
    ok(st["n_with"] == 5 and st["cer_with"] == 1.0 and st["judge"] == "确认",
       "E4 n_with=5 / cer_with=1.0 / judge=确认（正腿：显式 True 者全 verified）",
       (st["n_with"], st["cer_with"], st["judge"]))


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_a_without_excludes_absent()
    group_b_absent_not_in_either_group()
    group_c_present_truthy_counted()
    group_d_absent_count_invariant()
    group_e_production_path_explicit_only()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（insight_report 条件统计只计**显式提供**的条件"
          "（confidence>=0.7）；默认回落与键不存在的样本均不进入对应统计）")
    return 0


def test_issue281_insight_report_condition_presence_guard():
    """pytest 入口（脚本/pytest 双模式）。"""
    run_checks()
    assert not _FAIL, "%d checks failed: %s" % (len(_FAIL), _FAIL)


if __name__ == "__main__":
    sys.exit(run_checks())
