# -*- coding: utf-8 -*-
"""test_issue301_insight_verify_tag_hygiene_guard · #301 洞见验证标签卫生守卫
============================================================================
缺陷（lingshu issue #301 · P2 · 写入侧标签残留）：`LayeredStore.insight_verify`
重写标签时剥离清单只含**小写** `refuted`：
    tags = [t for t in node.tags
            if t not in ("pending", "verified", "refuted", "V1", "V2", "V3")]
    tags += ["insight_event", status, level]
剥离清单漏了本函数自己会写入的**大写级别**（`REFUTED`/`X`/任意调用方级别），
也没剥 `insight_event` 自身却又无条件追加 ⇒ ① 反复验证会**累积重复标签**
（`insight_event` 越叠越多）；② 已验证洞见仍挂旧 `REFUTED`，按标签取证伪的
查询会把它当证伪样本。

修法（最小）：剥离集合补 `insight_event` 与**上一轮记录的级别** `prev_level`。
判据来源：公开 issue #301「修复建议（最小补丁）」——剥离集合改为
`{pending,verified,refuted,V1,V2,V3,insight_event,prev_level}`；状态判定、
importance 提升逻辑均不变。

守卫断言组（回退修复 ⇒ A/B/C/D 必红）：
  A 组：REFUTED → V2 → V3 ⇒ `insight_event` 恰 1 条、无残留 `REFUTED`、无残留
     旧级别 `V2`（缺陷形态下 insight_event=4、REFUTED 仍在）。
  B 组：V2 → X（反向）⇒ 无残留 `V2`、`X` 为当前级别、`insight_event` 恰 1 条。
  C 组：同一等级连续 5 次验证 ⇒ 标签列**不随调用次数增长**（`insight_event`
     恒 1 条，标签总数恒定）——钉住「无界增长」。
  D 组：正常多轮验证后标签集合等于 {insight-layer, insight_event, status, level}
     ——钉住剥离面完整（不含任何旧状态/旧级别）。

跨批覆盖重叠：本仓 main 上 `tests/test_issue38_insight_verify_evidence_gate_guard.py`
是本函数既有守卫，但它只断言「零证据 ⇒ 不得 verified/不得抬 importance」，
**不读标签条数与残留**（grep 全 `tests/` 无第二处引用 `insight_event` 标签计数）
⇒ 与本件不互相代偿；本守卫的 A/C 组为「只让 #301 机制生效」的夹具（#38 修复
不改变标签累积行为，抽掉 #301 修复后本守卫必红）。

运行（lingshu 仓根）：
    python -X utf8 tests/test_issue301_insight_verify_tag_hygiene_guard.py
    python -X utf8 -m pytest tests/test_issue301_insight_verify_tag_hygiene_guard.py
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
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue301_"), "s.db")).store


# ---------- A 组：REFUTED → V2 → V3 后不得残留旧级别/重复标签 ----------

def group_a_refute_then_reverify():
    print("== A 组：REFUTED → V2 → V3 ⇒ 无残留 REFUTED、insight_event 恰 1 条 ==")
    s = _store()
    nid = s.insight_record("先被证伪、后被复现的洞见")["node_id"]
    s.insight_verify(nid, "REFUTED", "e1")
    s.insight_verify(nid, "V2", "e2")
    r = s.insight_verify(nid, "V3", "e3")
    tags = s.get_node(nid).tags
    print("     status=%s tags=%s" % (r["status"], tags))
    ok("REFUTED" not in tags,
       "A1 已验证后不得残留大写级别 REFUTED（旧实现残留 ⇒ 证伪查询误判）", tags)
    ok("V2" not in tags,
       "A2 不得残留上一轮级别 V2（prev_level 未剥）", tags)
    ok(tags.count("insight_event") == 1,
       "A3 insight_event 恰 1 条（旧实现每验证一次多叠一条）",
       tags.count("insight_event"))
    ok(tags.count("V3") == 1 and tags.count("verified") == 1,
       "A4 当前级别/状态各恰 1 条", tags)


# ---------- B 组：反向 V2 → X ----------

def group_b_verified_then_refute():
    print("== B 组：V2 → X ⇒ 无残留 V2、X 为当前级别 ==")
    s = _store()
    nid = s.insight_record("先验证后被证伪的洞见")["node_id"]
    s.insight_verify(nid, "V2", "e1")
    r = s.insight_verify(nid, "X", "e2")
    tags = s.get_node(nid).tags
    print("     status=%s tags=%s" % (r["status"], tags))
    ok("V2" not in tags, "B1 不得残留旧级别 V2", tags)
    ok("X" in tags and tags.count("X") == 1, "B2 X 为当前级别且恰 1 条", tags)
    ok(tags.count("insight_event") == 1, "B3 insight_event 恰 1 条",
       tags.count("insight_event"))
    ok(r["status"] == "refuted", "B4 X ⇒ status=refuted（状态判定未改）", r["status"])


# ---------- C 组：同等级连续 5 次 ⇒ 标签列不增长 ----------

def group_c_no_unbounded_growth():
    print("== C 组：同等级连续 5 次验证 ⇒ 标签列不随调用次数增长 ==")
    s = _store()
    nid = s.insight_record("反复验证的洞见")["node_id"]
    counts = []
    for _ in range(5):
        s.insight_verify(nid, "V2", "复现实验")
        t = s.get_node(nid).tags
        counts.append((t.count("insight_event"), len(t)))
    print("     每次调用后 (insight_event 条数, 标签总数):", counts)
    ok(all(c[0] == 1 for c in counts),
       "C1 insight_event 恒 1 条（旧实现逐次累加 1,2,3,4,5）", counts)
    ok(len({c[1] for c in counts}) == 1,
       "C2 标签总数恒定（标签列不随验证次数无界增长）", counts)


# ---------- D 组：标签集合恰为 {insight-layer, insight_event, status, level} ----------

def group_d_exact_tag_set():
    print("== D 组：多轮验证后标签集合恰为 {insight-layer, insight_event, status, level} ==")
    s = _store()
    nid = s.insight_record("多轮验证的洞见")["node_id"]
    for lv in ("REFUTED", "V1", "V2", "V3"):
        s.insight_verify(nid, lv, "证据-%s" % lv)
    tags = s.get_node(nid).tags
    print("     tags=%s" % tags)
    ok(sorted(tags) == sorted(["insight-layer", "insight_event", "verified", "V3"]),
       "D1 剥离面完整：只剩非状态标签 + 当前 status/level", tags)


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_a_refute_then_reverify()
    group_b_verified_then_refute()
    group_c_no_unbounded_growth()
    group_d_exact_tag_set()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（insight_verify 重写标签时剥除旧状态/旧级别/insight_event；"
          "标签列不随验证次数累积）")
    return 0


def test_issue301_insight_verify_tag_hygiene_guard():
    """pytest 入口（脚本/pytest 双模式）。"""
    run_checks()
    assert not _FAIL, "%d checks failed: %s" % (len(_FAIL), _FAIL)


if __name__ == "__main__":
    sys.exit(run_checks())
