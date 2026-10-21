# -*- coding: utf-8 -*-
"""#125 增量复现 · `escalation_points` 导入**同 id 覆写面**守卫（lingshu/core/core.py）
============================================================================
缺陷（公开 issue #125 的**第三方增量复现**，Yinfeng818 2026-10-16T06:52:15Z；
本端已发 `<!-- VERIFY-125-INC -->` 核验帖确认成立）：

  `M13_TABLES` 含 `escalation_points`（DDL 首列 `id TEXT PRIMARY KEY`），而
  `SpacetimeMemoryEngine.import_all` 的**三集合密钥判据**（`immutable_rows` =
  nodes 不可变层行 / `guarded_meta` = engine_meta 治理键 / `id_log_rows` = 带 id 的
  action_logs 行）**无一含该表** ⇒ 它落**通用腿**
  `INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({placeholders})`：
  **无密钥、无闸、无审计**。最重形态是**同 id 覆写**——无密钥备份以**既有种子 id**
  写入 ⇒ 整行替换 ⇒ 不止能关升级点（`enabled=0`），还能**反转触发语义、替换处置
  动作**（原 `action` 无副本），且走完 `action_logs` 增量 0。
  同表 API 写面 `add_escalation_point` 已在 #749（`a7d1596`）接 `verify_designer`
  fail-closed + `log_action("escalation_add", …)` ⇒ 同一张表两个治理写入面口径矛盾。

修法（**只收窄覆写面**，落点 `import_all` 的 `escalation_points` 写腿）：
  ① 该表写入前按库内既有 `id` 集合预筛：库内已有同 id ⇒ **不写**，并计入返回值
     `skipped_existing_rows`（按表计数；与 #652② `missing_columns` / #721
     `skipped_rows` 同形：非空才出现该表键）⇒ **显式上报、不静默丢弃**；
  ② SQL 侧同步由 `INSERT OR REPLACE` 换为 `INSERT OR IGNORE`（兜底备份自身含重复
     id）⇒ 本表上**不再存在任何整行替换路径**；
  ③ `imported["escalation_points"]` 口径仍是**实际写入行数**（跳过的既有行不计入）；
  ④ 其它表逐字不变（仍走通用腿 `INSERT OR REPLACE`）。

**本件刻意不做的另一半**（遗留 · 决策面，见交付回执 T6）：
  无密钥仍可**新增**任意 `escalation_points` 行（新 id），注入的 `trigger`/`condition`
  会参与 `check_escalation` 终裁匹配。这一半要动既有契约——`export_all` 产物**必然**
  携带种子行（该表在 `M13_TABLES`、构造期即播种 6 条），而「无密钥导入 export 产物
  照旧通过」由 `tests/test_issue125_import_boundary_guard.py::
  test_e_knowledge_roundtrip_unaffected` 钉住 ⇒ 若把该表并入密钥判据，每次知识层
  往返都要密钥、该组当场转红。故本件**不动**它，断言 C 反而把它**钉住**（如实标注）。

断言组（回退即红）：
  A 无密钥备份以**既有种子 id** 写入不同 `trigger`/`condition`/`action`/`enabled`
    ⇒ 该行**逐列不变**（修前必红：整行被替换、`action` 被换、升级点被关）。
  B **不许静默丢弃**：备份里每一条 `escalation_points` 行都必须有着落——写入
    （`imported`）或**显式上报**（`skipped_existing_rows` / `skipped_rows`）三者相加
    等于备份行数；且被跳过的既有行**不得**混进 `skipped_rows`（那是「坏行」桶）。
  C **新 id 行仍能落库**（不得因收窄覆写面把新增面一并关掉——那是另一半）：
    `imported == 1`、行在场、`requires_review is False`、且注入的触发词确实参与
    `check_escalation` 终裁匹配（把遗留面**钉在明面上**）。
  D **防误杀**（keyless 往返契约）：无密钥导入纯知识层备份 ⇒ 不抛、
    `requires_review is False`、`nodes` 计数不变、目标库 6 条种子行**逐列不变**。
  E **其它表逐字不变**：`blindspots` / `skills` 的同 id 导入**照旧整行替换**
    （证明只收窄了 `escalation_points` 一张表、没把通用腿一起改掉），且它们的
    `skipped_existing_rows` 恒空。
  F **API 面**（`add_escalation_point` 无密钥）仍 `PermissionError`、零落库、零审计
    （同表另一写面未被本线放松）。

`--mutate` 变异自证（把收窄改回通用腿 ⇒ **恰好点红断言 A**，其余不动）：
  python -X utf8 tests/test_issue125_escalation_import_overwrite_guard.py --mutate
  变异体＝①摘掉 `escalation_points` 的既有 id 预筛段 ＋②写腿动词回到
  `INSERT OR REPLACE`（即修复前的通用腿）。运行后逐条比对 pytest 结果，
  只允许 A 红；结束时按字节 sha256 校验 `core.py` **原样还原**。

运行（仓根）：python -X utf8 -m pytest tests/test_issue125_escalation_import_overwrite_guard.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

CORE_PY = os.path.join(REPO, "lingshu", "core", "core.py")
SEEDED = 6                      # ESC-001..ESC-006（构造期播种）
NEW_ID = "esc_import_new_1"     # 备份携带的**新** id（本件不动的那一半）
INJ_TRIGGER = "注入触发词"
INJ_ACTION = "注入处置动作"
INJ_CONDITION = "注入条件词"


# ---------------------------------------------------------------- 夹具/工具

@pytest.fixture(autouse=True)
def _keyless():
    """全部用例都在「未配置设计者密钥」下跑（本缺陷面即无密钥路径）。"""
    saved = os.environ.pop("AEIS_DESIGNER_KEY", None)
    try:
        yield
    finally:
        os.environ.pop("AEIS_DESIGNER_KEY", None)
        if saved is not None:
            os.environ["AEIS_DESIGNER_KEY"] = saved


def _esc_row(rid, code="ESC-001", trigger="被篡改触发词", condition="被篡改条件",
             action="被篡改处置动作", severity="low", enabled=0):
    """备份里的 `escalation_points` 行（列集＝DDL 列集）。"""
    return {"id": rid, "code": code, "trigger": trigger, "condition": condition,
            "action": action, "severity": severity, "enabled": enabled,
            "created_at": time.time()}


def _backup(tmp_path, payload, name="backup.json"):
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict({"meta": {"version": "v1.6"}}, **payload), fh,
                  ensure_ascii=False)
    return str(path)


def _live(e, rid):
    """库内该 id 的**逐列真值**（DDL 列序：id/code/trigger/condition/action/
    severity/enabled/created_at），不存在返回 None。"""
    row = e.store.conn.execute(
        "SELECT id, code, trigger, condition, action, severity, enabled, created_at"
        " FROM escalation_points WHERE id=?", (rid,)).fetchone()
    return None if row is None else tuple(row)


def _esc_count(e):
    return e.store.conn.execute(
        "SELECT COUNT(*) FROM escalation_points").fetchone()[0]


def _log_count(e):
    return e.store.conn.execute("SELECT COUNT(*) FROM action_logs").fetchone()[0]


def _seed_row(e):
    """取一条构造期种子行（既有 id + 其原值）。"""
    rows = e.list_escalation_points(enabled_only=False)
    assert len(rows) == SEEDED, "前置：构造期应播种 %d 条，实测 %r" % (SEEDED, len(rows))
    return rows[0]


# ------------------------------------------------------------------- 断言 A

def test_a_existing_seed_id_not_overwritten_without_key(tmp_path):
    """A：无密钥、以既有种子 id 写入 ⇒ 该行**逐列不变**（修前整行被替换）。"""
    e = SpacetimeMemoryEngine(":memory:", identity="guard-125esc-a")
    try:
        seed = _seed_row(e)
        sid = seed["id"]
        before = _live(e, sid)
        assert before is not None, "前置：种子行须在库内"

        # 攻击形状：同 id、改 trigger/condition/action、并把升级点关掉（enabled=0）
        path = _backup(tmp_path, {"escalation_points": [
            _esc_row(sid, code=seed["code"], trigger="被篡改触发词",
                     condition="被篡改条件", action="被篡改处置动作",
                     severity="low", enabled=0)]})
        ret = e.import_all(path)

        after = _live(e, sid)
        assert after == before, (
            "A 既有种子行被备份整行替换（同 id 覆写面未收窄）：\n  修前=%r\n  修后=%r"
            % (before, after))
        # 逐列点名（失败信息可读）
        assert after[4] == before[4], (
            "A 处置动作被替换：%r -> %r" % (before[4], after[4]))
        assert after[2] == before[2], (
            "A 触发词被反转：%r -> %r" % (before[2], after[2]))
        assert after[3] == before[3], (
            "A 触发条件被替换：%r -> %r" % (before[3], after[3]))
        assert after[6] == 1, (
            "A 升级点被静默关闭（enabled=0）：%r" % (after[6],))
        assert _esc_count(e) == SEEDED, (
            "A 行数变化（既不该多也不该少）：%r" % (_esc_count(e),))
        assert ret["imported"]["escalation_points"] == 0, (
            "A 被跳过的既有行仍被计入写入数：%r" % (ret["imported"],))
        assert ret["requires_review"] is False, (
            "A 本面不涉密钥放行，不应标注复核：%r" % (ret.get("requires_review"),))
    finally:
        e.close()


# ------------------------------------------------------------------- 断言 B

def test_b_skipped_existing_rows_are_explicitly_reported(tmp_path):
    """B：备份每一行都有着落（写入 ∪ 显式上报），既有行**不静默丢弃**。"""
    e = SpacetimeMemoryEngine(":memory:", identity="guard-125esc-b")
    try:
        sid = _seed_row(e)["id"]
        path = _backup(tmp_path, {"escalation_points": [
            _esc_row(sid),                                   # 既有 id ⇒ 应被跳过并上报
            _esc_row(NEW_ID, code="ESC-INJ", trigger=INJ_TRIGGER,
                     condition=INJ_CONDITION, action=INJ_ACTION,
                     severity="high", enabled=1),            # 新 id ⇒ 照旧落库
        ]})
        ret = e.import_all(path)

        assert "skipped_existing_rows" in ret, (
            "B 返回体缺 `skipped_existing_rows` 上报键（口径不可观测）：%r"
            % (sorted(ret.keys()),))
        n_backup = 2
        n_written = ret["imported"]["escalation_points"]
        n_existing = ret["skipped_existing_rows"].get("escalation_points", 0)
        n_bad = ret["skipped_rows"].get("escalation_points", 0)
        assert n_written + n_existing + n_bad == n_backup, (
            "B 有行被静默丢弃：备份 %d 行，写入 %d + 跳过既有 %d + 跳坏行 %d ≠ %d（返回体 %r）"
            % (n_backup, n_written, n_existing, n_bad, n_backup, ret))
        assert n_bad == 0, (
            "B 被跳过的既有行混进了「坏行」桶 skipped_rows（口径错位）：%r"
            % (ret["skipped_rows"],))
    finally:
        e.close()


# ------------------------------------------------------------------- 断言 C

def test_c_new_id_still_lands_and_participates_in_escalation(tmp_path):
    """C：新 id 行仍能落库（本线不动的那一半）——并把它**钉在明面上**。"""
    e = SpacetimeMemoryEngine(":memory:", identity="guard-125esc-c")
    try:
        path = _backup(tmp_path, {"escalation_points": [
            _esc_row(NEW_ID, code="ESC-INJ", trigger=INJ_TRIGGER,
                     condition=INJ_CONDITION, action=INJ_ACTION,
                     severity="high", enabled=1)]})
        ret = e.import_all(path)

        assert ret["imported"]["escalation_points"] == 1, (
            "C 新 id 行未落库（收窄覆写面时把新增面一并关掉了）：%r"
            % (ret["imported"],))
        row = _live(e, NEW_ID)
        assert row is not None and row[4] == INJ_ACTION, (
            "C 新 id 行未按备份落库：%r" % (row,))
        assert _esc_count(e) == SEEDED + 1, (
            "C 行数应为 %d：%r" % (SEEDED + 1, _esc_count(e)))
        assert ret["requires_review"] is False, (
            "C 新 id 面不涉密钥，不应标注复核：%r" % (ret.get("requires_review"),))

        # 遗留面（如实钉住，非本件目标）：注入的触发词确实参与终裁匹配
        hits = e.check_escalation(INJ_TRIGGER)
        assert any(h["id"] == NEW_ID for h in hits), (
            "C 遗留面读数变了（无密钥新增行的触发词本应参与 check_escalation）：%r"
            % (hits,))
    finally:
        e.close()


# ------------------------------------------------------------------- 断言 D

def test_d_keyless_knowledge_roundtrip_unaffected(tmp_path):
    """D：防误杀——无密钥知识层往返照旧通过，且目标库种子行逐列不变。"""
    src = SpacetimeMemoryEngine(":memory:", identity="guard-125esc-d-src")
    dst = SpacetimeMemoryEngine(":memory:", identity="guard-125esc-d-dst")
    try:
        src.add_perception("往返测试记忆", skip_dedup=True)
        path = str(tmp_path / "kb.json")
        src.export_all(path)

        before_seed = {r["id"]: _live(dst, r["id"])
                       for r in dst.list_escalation_points(enabled_only=False)}
        ret = dst.import_all(path)                        # 无密钥，不抛

        assert ret["imported"]["nodes"] == 1, (
            "D 知识层往返节点数不符：%r" % (ret["imported"],))
        assert ret.get("requires_review") is False, (
            "D 知识层被误标需复核：%r" % (ret,))
        assert ret.get("discarded_keys") == {}, (
            "D 合法备份被误报丢弃键：%r" % (ret.get("discarded_keys"),))
        # 目标库 6 条种子行**逐列不变**（export 产物必然携带种子行 ⇒ 若无收窄即被覆盖）
        for sid, val in before_seed.items():
            assert _live(dst, sid) == val, (
                "D 目标库种子行被 export 产物覆写：%r -> %r" % (val, _live(dst, sid)))
        assert _esc_count(dst) == SEEDED * 2, (
            "D 种子行 + 备份携带行计数不符：%r" % (_esc_count(dst),))
    finally:
        src.close()
        dst.close()


# ------------------------------------------------------------------- 断言 E

def test_e_other_tables_import_leg_unchanged(tmp_path):
    """E：其它表（blindspots/skills）同 id 导入**照旧整行替换**（通用腿未被改）。"""
    e = SpacetimeMemoryEngine(":memory:", identity="guard-125esc-e")
    try:
        bid = e.store.add_blindspot("BS-OLD", "旧盲区描述")
        sid = e.store.add_skill("旧技能名", "旧描述", "旧步骤")
        path = _backup(tmp_path, {
            "blindspots": [{"id": bid, "code": "BS-OLD", "description": "被替换的描述",
                            "severity": "low", "category": "operational",
                            "status": "open", "created_at": time.time(),
                            "resolved_at": None,
                            "predictability": "pending_assessment"}],
            "skills": [{"id": sid, "name": "新技能名", "description": "新描述",
                        "procedure": "新步骤", "confidence": 0.9, "version": 2,
                        "created_at": time.time(), "updated_at": time.time()}],
        })
        ret = e.import_all(path)

        assert ret["imported"]["blindspots"] == 1 and ret["imported"]["skills"] == 1, (
            "E 其它表未按旧口径落库：%r" % (ret["imported"],))
        assert e.store.conn.execute(
            "SELECT description FROM blindspots WHERE id=?", (bid,)
        ).fetchone()[0] == "被替换的描述", (
            "E blindspots 同 id 覆盖行为被改动（通用腿不该动）")
        assert e.store.conn.execute(
            "SELECT name FROM skills WHERE id=?", (sid,)
        ).fetchone()[0] == "新技能名", (
            "E skills 同 id 覆盖行为被改动（通用腿不该动）")
        assert ret["skipped_existing_rows"] == {}, (
            "E 收窄溢出到其它表（本应只关 escalation_points 一张表）：%r"
            % (ret["skipped_existing_rows"],))
    finally:
        e.close()


# ------------------------------------------------------------------- 断言 F

def test_f_api_face_still_requires_key(tmp_path):
    """F：同表另一写面 `add_escalation_point` 无密钥仍拒（未被本线放松）。"""
    e = SpacetimeMemoryEngine(":memory:", identity="guard-125esc-f")
    try:
        before, logs = _esc_count(e), _log_count(e)
        with pytest.raises(PermissionError):
            e.add_escalation_point("E-NOKEY", "信号", "value > 0.9", "up", "high")
        assert _esc_count(e) == before, "F 被拒后仍有落库"
        assert _log_count(e) == logs, "F 被拒后仍写了审计"
        assert e.store.conn.execute(
            "SELECT COUNT(*) FROM escalation_points WHERE code='E-NOKEY'"
        ).fetchone()[0] == 0, "F 被拒行仍落库"
    finally:
        e.close()


# ============================ --mutate 变异自证 ============================

#: 变异体＝把「收窄」改回修复前的**通用腿**：
#:  ① 摘掉 `escalation_points` 的既有 id 预筛段（连同跳过计数上报）；
#:  ② 写腿动词回到 `INSERT OR REPLACE`。
_MUTATIONS = (
    (
        '            if table == "escalation_points":\n'
        '                existing_ids = {r[0] for r in c.execute("SELECT id FROM escalation_points")}\n'
        '                kept = [r for r in rows if r.get("id") not in existing_ids]\n'
        '                n_existing = len(rows) - len(kept)\n'
        '                if n_existing:\n'
        '                    skipped_existing_rows[table] = n_existing\n'
        '                rows = kept\n'
        '                if not rows:\n'
        '                    counts[table] = 0\n'
        '                    continue\n',
        '',
    ),
    (
        '                _verb = ("INSERT OR IGNORE" if table == "escalation_points"\n'
        '                         else "INSERT OR REPLACE")\n',
        '                _verb = "INSERT OR REPLACE"\n',
    ),
)

_EXPECT_RED = {"test_a_existing_seed_id_not_overwritten_without_key"}


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _read_core():
    """读 `core.py` 原文，返回 `(归一为 \\n 的文本, 原换行符)`。

    归一后变异锚点只需按 `\\n` 书写；`_write_core` 按**原换行符**写回 ⇒
    CRLF 检出（本仓 `core.autocrlf=true`）与 LF 检出都能按字节原样还原。
    """
    with open(CORE_PY, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    sep = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), sep


def _write_core(text, sep):
    with open(CORE_PY, "w", encoding="utf-8", newline=sep) as fh:
        fh.write(text)


def _run_pytest():
    """在（可能已被变异的）源码上跑本守卫件，返回 {测试名: PASSED|FAILED|ERROR}。

    `-rA` 让 pytest 在 short summary 里逐条打印 `PASSED/FAILED/ERROR <nodeid>`
    （`-v` 的行尾是 `[ 16%]` 百分比后缀，不可直接判尾）。
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.pop("AEIS_DESIGNER_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", os.path.abspath(__file__),
         "-q", "-rA", "--tb=no", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=280)
    outcomes = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        for tag in ("PASSED", "FAILED", "ERROR"):
            prefix = tag + " "
            if line.startswith(prefix) and "::" in line:
                outcomes[line[len(prefix):].split(" ")[0].split("::")[-1]] = tag
    return outcomes, proc


def _mutate():
    orig, sep = _read_core()
    digest = _sha256(CORE_PY)
    mutated = orig
    for old, new in _MUTATIONS:
        assert old in mutated, (
            "变异锚点未命中（守卫须随源码同步维护）：%r" % (old[:80],))
        mutated = mutated.replace(old, new, 1)
    assert mutated != orig, "变异未产生任何改动"
    bad = 0
    try:
        _write_core(mutated, sep)
        outcomes, proc = _run_pytest()
        if not outcomes:
            print("  变异运行未解析到用例结果，stdout 尾行：\n%s" % (proc.stdout[-1500:],))
            return 1
        red = {k for k, v in outcomes.items() if v != "PASSED"}
        for name in sorted(outcomes):
            print("  %-58s %s" % (name, outcomes[name]))
        print("  期望点红：%r" % (sorted(_EXPECT_RED),))
        print("  实际点红：%r" % (sorted(red),))
        if red == _EXPECT_RED:
            print("  变异自证 ✓（恰好点红断言 A，其余不动）")
        else:
            print("  变异自证 ✗：点红集合与期望不符（漏红＝假守卫 / 多红＝判据不纯）")
            bad += 1
    finally:
        _write_core(orig, sep)
        assert _sha256(CORE_PY) == digest, (
            "变异后 core.py 未按字节原样还原（须立即人工核对）")
        print("  core.py 已按字节 sha256 校验还原 ✓")
    return bad


def main():
    if "--mutate" not in sys.argv:
        print("用法：python -X utf8 tests/test_issue125_escalation_import_overwrite_guard.py"
              " --mutate")
        return 2
    print("=== #125 增量复现 · escalation_points 同 id 覆写面：变异自证 ===")
    bad = _mutate()
    print("===== " + ("变异自证全绿" if not bad else "变异自证失败") + " =====")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
