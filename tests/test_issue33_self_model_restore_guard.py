# -*- coding: utf-8 -*-
"""test_issue33_self_model_restore_guard · #33 自我模型跨重启恢复守卫
============================================================================
缺陷（lingshu issue #33 · P1）：SELF 层声明「不可遗忘」，但
`SpacetimeMemoryEngine.__init__` **无条件**新建默认 `SelfModel`，全仓无任何从库内
SELF 快照重建 `self_model` 的代码路径（`SelfModel` 无 `from_dict`）⇒ 重启后
`values` / `current_goal` / `state_description` / `trust_state` 全部回默认，而
`self_check.self_ok` 只判「SELF 层有节点」⇒ 看不见失忆（静默回默认）。
（数据在库未丢，属**恢复失败**而非数据丢失。）

修法（最小 · 对齐 `docs/plans/删除恢复持久化完整性_修复设计_v0.1.md` §5.3/§5.4）：
  ① `SelfModel.SERIAL_VERSION` ＋ `SelfModel.from_dict`（未知键忽略 / 缺失键取默认）；
  ② `__init__` 里 `SelfModel(...)` **之后**从持久化快照回填（`_restore_self_model`）：
     权威键 `engine_meta[self_model_snapshot]` 优先，回退 SELF 层最新 `self_snapshot`
     节点；identity 冲突**快照优先**并显式标注；失败**回退默认且显式标注**；
  ③ 恢复状态经 `self_check()["self_restore"]` 可观（只增字段，`self_ok` 判据未动）；
  ④ 落库出口收在 `SelfModel` 自身的变更出口（`update` / `record_value_change` /
     `update_trust_state` 经 `_persist_hook`）——`record_value_change`（values 的
     唯一写路径）由仓外自我认知引擎驱动，引擎层无法逐个包住。

守卫断言组：
  A 组（核心 · 有牙）：写自定义 `values` + `current_goal` → `close()` → 同 db 重开
     → `get_self_model()` 的 values/current_goal 仍是自定义值（旧实现回默认 ⇒ 必红）。
  B 组（信任态同取 · D-28「同取或同弃」）：`trust_state` 与 `trust_history` **一并**
     恢复，且 `e_weight` 与历史自洽（非「取态弃史」）。
  C 组（对照 · 防误杀）：无快照的新库重开仍取默认。
  D 组（可见性 · C-2/D-21）：`self_check()["self_restore"]` 报告
     `empty` / `restored` / `failed`；坏快照 ⇒ `failed` 且**不阻断启动**、回退默认。
  E 组（identity 优先序 · D-29）：快照 identity 优先于构造入参，冲突显式标注。

运行（lingshu 仓根）：
    python -X utf8 tests/test_issue33_self_model_restore_guard.py
    python -X utf8 -m pytest tests/test_issue33_self_model_restore_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine, SelfModel  # noqa: E402

_PASS, _FAIL = [], []


def ok(cond, name, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


def _db(name):
    return os.path.join(tempfile.mkdtemp(prefix="lingshu_issue33_"), name + ".db")


# ---------- A 组：重启后人格字段必须恢复 ----------

def group_a_restore_personality():
    print("== A 组：values / current_goal 跨重启恢复（旧实现回默认） ==")
    db = _db("a")
    e = SpacetimeMemoryEngine(db, identity="实例A")
    e.self_model.record_value_change("诚实", "init")     # values 的唯一写路径
    e.update_self({"current_goal": "目标X", "state_description": "运行中"})
    before = (list(e.self_model.values), e.self_model.current_goal,
              e.self_model.state_description)
    e.close()

    e2 = SpacetimeMemoryEngine(db, identity="实例A")
    after = (list(e2.self_model.values), e2.self_model.current_goal,
             e2.self_model.state_description)
    print("     重启前 %r" % (before,))
    print("     重启后 %r" % (after,))
    ok(after == before,
       "A1 重启后 values/current_goal/state_description 与重启前一致"
       "（旧实现全部回默认 ⇒ 必红）", "before=%r after=%r" % (before, after))
    ok(e2._self_restore.get("status") == "restored",
       "A2 恢复状态标 restored", e2._self_restore)
    e2.close()


# ---------- B 组：信任态与信任史同取 ----------

def group_b_trust_state_and_history():
    print("== B 组：trust_state 与 trust_history 同取（D-28 同取或同弃） ==")
    db = _db("b")
    e = SpacetimeMemoryEngine(db, identity="实例B")
    for i in range(1, 5):
        e.update_trust_state(0.6 + 0.05 * i, i, p_trust=0.7, p_gap=0.2)
    ts_before = dict(e.self_model.trust_state)
    th_len_before = len(e.self_model.trust_history)
    e.close()

    e2 = SpacetimeMemoryEngine(db, identity="实例B")
    ts_after = dict(e2.self_model.trust_state)
    th_len_after = len(e2.self_model.trust_history)
    print("     trust_state 前=%r 后=%r" % (ts_before, ts_after))
    print("     trust_history 长度 前=%d 后=%d" % (th_len_before, th_len_after))
    ok(ts_after.get("t_total") == ts_before.get("t_total") and th_len_after == th_len_before,
       "B1 trust_state 与 trust_history **一并**恢复（非「取态弃史」）",
       "ts=%r/%r th=%d/%d" % (ts_before, ts_after, th_len_before, th_len_after))
    ok(ts_after.get("e_weight") == ts_before.get("e_weight"),
       "B2 e_weight 与恢复后的 trust_history 自洽（同取 ⇒ 不自相矛盾）",
       "e_weight=%r/%r" % (ts_before.get("e_weight"), ts_after.get("e_weight")))
    e2.close()


# ---------- C 组：无快照的新库仍取默认（防误杀） ----------

def group_c_fresh_db_defaults():
    print("== C 组：对照——无快照的新库重开仍取默认 ==")
    db = _db("c")
    e = SpacetimeMemoryEngine(db, identity="实例C")
    e.close()
    e2 = SpacetimeMemoryEngine(db, identity="实例C")
    ok(list(e2.self_model.values) == ["存在优先", "信任深化", "结构完整"],
       "C1 新库无快照 ⇒ values 取默认（恢复未误伤空库）", e2.self_model.values)
    ok(e2.self_model.current_goal == "" and e2._self_restore.get("status") == "empty",
       "C2 新库 current_goal 空、恢复状态标 empty", e2._self_restore)
    e2.close()


# ---------- D 组：恢复失败可见 + 不阻断启动 ----------

def test_d_failed_restore_visible():
    print("== D 组：坏快照 ⇒ 回退默认 + 显式标注 failed（不阻断启动） ==")
    db = _db("d")
    e = SpacetimeMemoryEngine(db, identity="实例D")
    e.update_self({"current_goal": "会被丢弃"})
    # 人为把权威键与 SELF 层快照都写坏（不可解析 JSON）
    e.store.set_meta(e.SELF_MODEL_META_KEY, "{not json")
    c = e.store.conn.cursor()
    c.execute("UPDATE nodes SET content=? WHERE layer='self' AND tags LIKE ?",
              ("{also not json", "%self_snapshot%"))
    e.store.conn.commit()
    e.close()

    e2 = SpacetimeMemoryEngine(db, identity="实例D")   # 不得抛（不阻断启动）
    st = e2._self_restore.get("status")
    print("     坏快照重启 ⇒ status=%s values=%s" % (st, e2.self_model.values))
    ok(st == "failed",
       "D1 快照不可解析 ⇒ 恢复状态标 failed（旧实现静默回默认、无任何标注）", st)
    ok(e2.self_check()["self_restore"]["status"] == "failed",
       "D2 self_check()['self_restore'] 可观失败（只增字段）",
       e2.self_check()["self_restore"])
    ok(list(e2.self_model.values) == ["存在优先", "信任深化", "结构完整"],
       "D3 失败时回退默认（可用性不破）", e2.self_model.values)
    e2.close()


# ---------- E 组：identity 优先序（快照优先 + 冲突标注） ----------

def test_e_identity_conflict():
    print("== E 组：identity 冲突 ⇒ 快照优先 + 显式标注 ==")
    db = _db("e")
    e = SpacetimeMemoryEngine(db, identity="实例E")
    e.update_self({"current_goal": "g"})
    e.close()
    e2 = SpacetimeMemoryEngine(db, identity="另一个身份")   # 与快照 identity 冲突
    print("     identity=%r conflict=%s" % (e2.self_model.identity,
                                            e2._self_restore.get("identity_conflict")))
    ok(e2.self_model.identity == "实例E",
       "E1 快照 identity 优先于构造入参（D-29）", e2.self_model.identity)
    ok(e2._self_restore.get("identity_conflict") is True,
       "E2 冲突被显式标注（不静默换人格）", e2._self_restore)
    e2.close()


# ---------- F 组：序列化版本（D-27） ----------

def test_f_serial_version_and_from_dict():
    print("== F 组：快照带 version；from_dict 未知键忽略/缺失键取默认 ==")
    d = SelfModel(identity="X").to_dict()
    ok(d.get("version") == SelfModel.SERIAL_VERSION,
       "F1 to_dict() 带 version 键（旧快照无 version 视为 v1）", d.get("version"))
    m = SelfModel.from_dict({"values": ["自定义"], "未来字段": 1})
    ok(m.values == ["自定义"] and not hasattr(m, "未来字段"),
       "F2 from_dict 未知键忽略、已知键恢复", m.values)
    m2 = SelfModel.from_dict({})
    ok(list(m2.values) == ["存在优先", "信任深化", "结构完整"],
       "F3 from_dict 缺失键取默认（空 dict ⇒ 默认模型）", m2.values)
    m3 = SelfModel.from_dict(None)
    ok(list(m3.values) == ["存在优先", "信任深化", "结构完整"],
       "F4 from_dict(None) 回退全默认（不抛）")


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_a_restore_personality()
    group_b_trust_state_and_history()
    group_c_fresh_db_defaults()
    test_d_failed_restore_visible()
    test_e_identity_conflict()
    test_f_serial_version_and_from_dict()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（自我模型跨重启恢复：人格字段与信任态/史一并回填；失败显式"
          "标注；identity 冲突快照优先；无快照新库仍取默认）")
    return 0


def test_issue33_self_model_restore_guard():
    """pytest 入口（脚本/pytest 双模式）。"""
    run_checks()
    assert not _FAIL, "%d checks failed: %s" % (len(_FAIL), _FAIL)


if __name__ == "__main__":
    sys.exit(run_checks())
