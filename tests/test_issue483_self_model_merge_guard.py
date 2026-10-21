# -*- coding: utf-8 -*-
"""test_issue483_self_model_merge_guard · #483 自我模型快照「写前读回合并」守卫
================================================================================
缺陷（lingshu issue #483 · P0 数据丢失）：`_save_self_model`（#33 新增的落库出口）
**不读库**，把本实例内存里的自我模型副本整份 `INSERT OR REPLACE` 回去 ⇒ 凡本实例
内存落后于库（① 多进程/多实例共库时另一写者刚写过；② `export_all → import_all`
灾备恢复后内存未刷新），第一次普通写入（`update_self` / `record_value_change` /
`update_trust_state`）就把别人的 `values` / `value_evolution` / `trust_history` /
`goal` 整份抹掉，且 `integrity_ok=True`、重启 `restore=restored`，无任何信号。

修法（最小 · 写前读回 ＋ 三方合并）：
  · `LayeredStore.merge_meta(key, value_fn)`：读改写单点——「读旧值 → 算新值 → 写回」
    收进**同一条写事务**（`@_transactional` ⇒ 最外层 `BEGIN IMMEDIATE` 先取写锁），
    故事务内读到的是最新已提交态、其间无写者可插入；
  · `_save_self_model` 改走 `merge_meta`，取值回调 `_merge_self_model_value` 做字段级
    三方合并（基线 `_self_model_baseline` ／ local ／ disk）：本实例未改过的字段
    （`local[k] == base[k]`）采纳 disk 现值（他方更新不丢），改过的字段保留 local
    （本实例变更不丢）；基线返回前置为 local（**不是** merged），否则第二笔写入会把
    刚采纳的他方值当成本地改动再覆写回去。

守卫断言组：
  A 组（核心 · 有牙）：同库双实例——旧实例内存陈旧，新实例先写 `values`；旧实例再
     做一次普通写入 ⇒ 重启后 `values` / `value_evolution` 必须仍在（旧实现整份覆盖 ⇒ 必红）。
  B 组（灾备腿）：`export_all → import_all` 恢复后一次普通写入 ⇒ `values` / `current_goal`
     必须仍在（旧实现必红）。
  C 组（反向腿 · 单实例路径不得失效）：单实例 `record_value_change` + `update_trust_state`
     + `update_self` ⇒ 重启全保留（防「整份拒绝写」把正常路径打死）。
  D 组（第二笔不复活丢更新）：采纳 disk 值之后再写一笔 ⇒ 重启仍保留他方值（基线若误置
     merged ⇒ 本组红）。
  E 组（判据有判别力 · 正对照）：本实例**自己改过**的字段必须压过 disk 旧值（证明合并不是
    无条件「disk 胜」）；同一实例连续两次 `record_value_change` 取最新值。
  F 组（健壮性）：库内快照不可解析时按「无他方更新」处置，不阻断写入。

运行（lingshu 仓根）：
    python -X utf8 tests/test_issue483_self_model_merge_guard.py
    python -X utf8 -m pytest tests/test_issue483_self_model_merge_guard.py -q
"""
from __future__ import annotations

import json
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


def _db(name):
    return os.path.join(tempfile.mkdtemp(prefix="lingshu_issue483_"), name + ".db")


def _snap(engine):
    raw = engine.store.get_meta(engine.SELF_MODEL_META_KEY).get(engine.SELF_MODEL_META_KEY)
    return json.loads(raw) if raw else None


# ---------- A 组：同库双实例（另一实例先写、本实例后写） ----------

def group_a_stale_instance_does_not_clobber():
    print("== A 组：同库双实例——陈旧实例的普通写入不得抹掉他方 values ==")
    db = _db("a")
    # 旧实例（先启动：库内尚无快照 ⇒ 内存=默认；此后不再刷新）
    stale = SpacetimeMemoryEngine(db, identity="灵枢-B")
    # 新实例先写 values（values 的唯一写路径）+ goal
    fresh = SpacetimeMemoryEngine(db, identity="灵枢-A")
    fresh.self_model.record_value_change("诚实优先", "设计者校准")
    fresh.update_self({"current_goal": "灾备演练"})
    print("     新实例写入后库内 values=%s goal=%r"
          % (_snap(fresh)["values"], _snap(fresh)["current_goal"]))
    print("     旧实例内存仍是陈旧副本 values=%s" % (stale.self_model.values,))

    stale.update_trust_state(0.8, 1)     # 旧实例的**普通写入**（旧实现即在此整份覆盖）
    disk = _snap(stale)
    print("     旧实例写入后库内 values=%s goal=%r trust=%s"
          % (disk["values"], disk["current_goal"],
             [h["t_total"] for h in disk["trust_history"]]))
    stale.close(); fresh.close()

    re_ = SpacetimeMemoryEngine(db, identity="灵枢-A")
    print("     重启 values=%s goal=%r value_evolution=%d trust=%s"
          % (re_.self_model.values, re_.self_model.current_goal,
             len(re_.self_model.value_evolution),
             [h["t_total"] for h in re_.self_model.trust_history]))
    ok(list(re_.self_model.values) == ["诚实优先"],
       "A1 陈旧实例的普通写入未抹掉他方 values（旧实现整份覆盖 ⇒ 必红）",
       re_.self_model.values)
    ok(re_.self_model.current_goal == "灾备演练",
       "A2 陈旧实例的普通写入未抹掉他方 current_goal（同属未改动字段 ⇒ 采纳库内值）",
       re_.self_model.current_goal)
    ok(len(re_.self_model.value_evolution) > 0,
       "A3 他方 value_evolution 溯源记录一并存活", len(re_.self_model.value_evolution))
    ok([h["t_total"] for h in re_.self_model.trust_history] == [0.8],
       "A4 本实例（后写者）的 trust_history 亦不丢——字段级归属而非整份后写胜",
       [h["t_total"] for h in re_.self_model.trust_history])
    re_.close()


# ---------- B 组：灾备恢复后一次普通写入 ----------

def group_b_after_import_all():
    print("== B 组：export_all → import_all 恢复后一次普通写入 ==")
    d = tempfile.mkdtemp(prefix="lingshu_issue483_b_")
    key = "guard483-key"
    os.environ["AEIS_DESIGNER_KEY"] = key
    try:
        src = SpacetimeMemoryEngine(os.path.join(d, "src.db"), identity="灵枢-A")
        src.self_model.record_value_change("诚实优先", "设计者校准")
        src.update_trust_state(0.91, 7)
        src.update_self({"current_goal": "完成M13灾备演练"})
        bk = os.path.join(d, "backup.json")
        src.export_all(bk)
        src.close()

        dst = SpacetimeMemoryEngine(os.path.join(d, "dst.db"), identity="灵枢-A")
        r = dst.import_all(bk, designer_key=key)
        print("     IMPORT integrity_ok=%s | 库内快照 values=%s | 内存 values=%s"
              % (r["integrity_ok"], _snap(dst)["values"], dst.self_model.values))
        dst.update_trust_state(0.5, 1)   # 恢复后任意一次普通写入
        dst.close()

        re_ = SpacetimeMemoryEngine(os.path.join(d, "dst.db"), identity="灵枢-A")
        print("     RESTART values=%s goal=%r trust=%s"
              % (re_.self_model.values, re_.self_model.current_goal,
                 [h["t_total"] for h in re_.self_model.trust_history]))
        ok(list(re_.self_model.values) == ["诚实优先"],
           "B1 恢复后第一次普通写入未抹掉备份里的 values（旧实现必红）",
           re_.self_model.values)
        ok(re_.self_model.current_goal == "完成M13灾备演练",
           "B2 恢复后第一次普通写入未抹掉备份里的 current_goal", re_.self_model.current_goal)
        re_.close()
    finally:
        os.environ.pop("AEIS_DESIGNER_KEY", None)


# ---------- C 组：反向腿——单实例路径必须仍然工作 ----------

def group_c_single_instance_still_works():
    print("== C 组（反向腿）：单实例正常路径不得被改坏 ==")
    db = _db("c")
    e = SpacetimeMemoryEngine(db, identity="灵枢-A")
    e.self_model.record_value_change("诚实优先", "设计者校准")
    e.update_trust_state(0.77, 3)
    e.update_self({"current_goal": "单实例目标", "state_description": "运行中"})
    e.close()

    re_ = SpacetimeMemoryEngine(db, identity="灵枢-A")
    print("     RESTART values=%s goal=%r state=%r trust=%s restore=%s"
          % (re_.self_model.values, re_.self_model.current_goal,
             re_.self_model.state_description,
             [h["t_total"] for h in re_.self_model.trust_history],
             re_._self_restore["status"]))
    ok(list(re_.self_model.values) == ["诚实优先"], "C1 单实例 values 落库并恢复",
       re_.self_model.values)
    ok(re_.self_model.current_goal == "单实例目标"
       and re_.self_model.state_description == "运行中", "C2 单实例 goal/state 落库并恢复")
    ok([h["t_total"] for h in re_.self_model.trust_history] == [0.77],
       "C3 单实例 trust_history 落库并恢复")
    ok(re_._self_restore.get("status") == "restored", "C4 恢复状态 restored",
       re_._self_restore)
    re_.close()


# ---------- D 组：第二笔写入不复活丢更新（基线不得置为 merged） ----------

def group_d_second_write_does_not_resurrect_clobber():
    print("== D 组：采纳他方值后再写一笔，仍不得抹掉他方值 ==")
    db = _db("d")
    stale = SpacetimeMemoryEngine(db, identity="灵枢-B")
    fresh = SpacetimeMemoryEngine(db, identity="灵枢-A")
    fresh.self_model.record_value_change("诚实优先", "设计者校准")
    fresh.close()

    stale.update_trust_state(0.8, 1)     # 第一笔：采纳 disk 的 values
    stale.update_trust_state(0.6, 2)     # 第二笔：不得把已采纳的 values 当成本地改动覆写
    stale.close()

    re_ = SpacetimeMemoryEngine(db, identity="灵枢-A")
    print("     两笔写入后重启 values=%s trust=%s"
          % (re_.self_model.values, [h["t_total"] for h in re_.self_model.trust_history]))
    ok(list(re_.self_model.values) == ["诚实优先"],
       "D1 第二笔写入未复活丢更新（基线若置为 merged ⇒ 本组红）", re_.self_model.values)
    re_.close()


# ---------- E 组：判据有判别力（正对照） ----------

def group_e_local_changes_win():
    print("== E 组（正对照）：本实例改过的字段必须压过库内旧值 ==")
    db = _db("e")
    a = SpacetimeMemoryEngine(db, identity="灵枢-A")
    a.self_model.record_value_change("诚实优先", "初值")   # 库内 values=[诚实优先]
    b = SpacetimeMemoryEngine(db, identity="灵枢-B")       # b 启动时已读到该值（基线一致）
    a.close()

    b.self_model.record_value_change("结构完整", "本地校准")  # b 自己改 values ⇒ 本地胜
    b.update_self({"current_goal": "本地目标"})
    b.close()

    re_ = SpacetimeMemoryEngine(db, identity="灵枢-B")
    print("     重启 values=%s goal=%r" % (re_.self_model.values, re_.self_model.current_goal))
    ok(list(re_.self_model.values) == ["结构完整"],
       "E1 本实例改过的字段压过库内旧值（证明合并非无条件「disk 胜」）",
       re_.self_model.values)
    ok(re_.self_model.current_goal == "本地目标", "E2 本实例 goal 同样落库", re_.self_model.current_goal)
    re_.close()

    # E3：同实例连续两次 record_value_change ⇒ 取最新值
    db2 = _db("e2")
    c = SpacetimeMemoryEngine(db2, identity="灵枢-C")
    c.self_model.record_value_change("第一值", "t1")
    c.self_model.record_value_change("第二值", "t2")
    c.close()
    re2 = SpacetimeMemoryEngine(db2, identity="灵枢-C")
    ok(list(re2.self_model.values) == ["第二值"],
       "E3 同实例连续两次 record_value_change ⇒ 重启取最新值", re2.self_model.values)
    re2.close()


# ---------- F 组：坏快照不阻断写入 ----------

def group_f_corrupt_disk_snapshot():
    print("== F 组：库内快照不可解析 ⇒ 按「无他方更新」处置，不阻断写入 ==")
    db = _db("f")
    e = SpacetimeMemoryEngine(db, identity="灵枢-F")
    e.update_self({"current_goal": "g0"})
    e.store.set_meta(e.SELF_MODEL_META_KEY, "{not json")   # 人为写坏
    raised = None
    try:
        e.update_trust_state(0.5, 1)                       # 不得抛
    except Exception as ex:  # noqa: BLE001
        raised = "%s: %s" % (type(ex).__name__, ex)
    print("     坏快照后写入 raised=%r goal=%r" % (raised, e.self_model.current_goal))
    ok(raised is None, "F1 坏快照不阻断普通写入", raised)
    e.close()


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_a_stale_instance_does_not_clobber()
    group_b_after_import_all()
    group_c_single_instance_still_works()
    group_d_second_write_does_not_resurrect_clobber()
    group_e_local_changes_win()
    group_f_corrupt_disk_snapshot()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（#483：陈旧实例的普通写入按字段采纳库内现值，他方 values/"
          "value_evolution/trust_history/goal 不再被整份抹掉；单实例正常路径与"
          "本地改动优先序均保持）")
    return 0


def test_issue483_self_model_merge_guard():
    """pytest 入口（脚本/pytest 双模式）。"""
    run_checks()
    assert not _FAIL, "%d checks failed: %s" % (len(_FAIL), _FAIL)


if __name__ == "__main__":
    sys.exit(run_checks())
