# -*- coding: utf-8 -*-
"""test_issue749_escalation_write_gate · #749 升级点**写入面**授权闸与审计留痕
============================================================================
缺陷（#749 · 成立）：
  同族 `LayeredStore.set_escalation_enabled` 早在 #130 就补齐了
  `verify_designer` fail-closed 闸 + `action_logs` 审计，而**同表同治理面**的
  `LayeredStore.add_escalation_point` 至今**无授权、无审计**——签名里根本没有
  `designer_key` 形参、全文无 `verify_designer`。两条写路径口径矛盾 ⇒ 无密钥即
  可注入任意危机触发判据（叠加 #747 的解析缺陷可反转触发语义）。

修法（与 #130 同位同形）：
  ① `LayeredStore.add_escalation_point` 补 `designer_key=None` 形参 +
     `verify_designer` fail-closed（`PermissionError`，句面与
     `adjudicate_promotion` / `set_escalation_enabled` 一致）+ `log_action`
     审计；`SpacetimeMemoryEngine.add_escalation_point` 同步透传。
  ② 构造期播种（`_seed_escalation_points`，源码常量 6 条）拆出**裸写**入口
     `LayeredStore._insert_escalation_point` ⇒ 未配置密钥的进程仍可正常构造引擎
     （「初始化」与「运行期治理写」分离，不是闸的漏洞：播种内容不可由调用方
     控制）。

断言组（回退即红）：
  A 无密钥（未配置 `AEIS_DESIGNER_KEY`）⇒ `PermissionError`，且**零落库**
    （条数不变）、**零审计行**。
  B 错密钥 ⇒ `PermissionError`，零落库、零审计行。
  C 正确密钥 ⇒ 落库（条数 +1、`list_escalation_points` 可见新 code）+ `action_logs`
    新增一行，`action_type`/`outcome` 可查。
  D 引擎门面与 store 层**同形**：两条路径的无密钥/正确密钥读数一致（透传不缺）。
  E **播种分离**（本件边界的判据）：
    · 未配置密钥构造引擎 ⇒ **不抛**、6 条默认升级点在场；
    · 源码锚：`_seed_escalation_points` 体内出现 `_insert_escalation_point(`、
      且**不**出现 `add_escalation_point(`（改回带闸方法 ⇒ 本组红）。
  F 与 #130 口径一致：`set_escalation_enabled` 无密钥仍拒（闸未被本件放松）。

定点变异自证（抽掉关键一行 ⇒ 必红并点名）：
  · 删 `if not verify_designer(designer_key): raise PermissionError(...)` ⇒ A/B 必红。
  · 删 `self.log_action("escalation_add", ...)` ⇒ C 必红。
  · 把 `_seed_escalation_points` 的调用改回 `self.store.add_escalation_point(...)`
    ⇒ E 必红（未配置密钥的构造即抛）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue749_escalation_write_gate.py -q
"""
from __future__ import annotations

import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

CORE_PY = os.path.join(REPO, "lingshu", "core", "core.py")
DUMMY_KEY = "GOV-IMPL-DUMMY-KEY"
SEEDED = 6  # ESC-001..ESC-006


class _Base(unittest.TestCase):
    """共用夹具：清空密钥环境变量，按需在用例内配置并还原。"""

    def setUp(self):
        self._saved = os.environ.pop("AEIS_DESIGNER_KEY", None)
        self.addCleanup(self._restore)
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-749")
        self.addCleanup(self.engine.close)

    def _restore(self):
        os.environ.pop("AEIS_DESIGNER_KEY", None)
        if self._saved is not None:
            os.environ["AEIS_DESIGNER_KEY"] = self._saved

    def _count(self):
        return len(self.engine.list_escalation_points(enabled_only=False))

    def _codes(self):
        return {p["code"] for p in self.engine.list_escalation_points(enabled_only=False)}

    def _log_rows(self):
        c = self.engine.store.conn.cursor()
        c.execute("SELECT action_type, summary, outcome FROM action_logs ORDER BY id")
        return c.fetchall()


class NoKeyRejectedTests(_Base):
    """A/B：无密钥、错密钥 ⇒ 拒 + 零落库 + 零审计。"""

    def test_no_key_rejected_without_side_effect(self):
        before = self._count()
        with self.assertRaises(PermissionError):
            self.engine.add_escalation_point("E-NOKEY", "偏差信号",
                                             "value > 0.9", "up", "high")
        self.assertEqual(self._count(), before)
        self.assertNotIn("E-NOKEY", self._codes())
        self.assertEqual(self._log_rows(), [])

    def test_wrong_key_rejected_without_side_effect(self):
        os.environ["AEIS_DESIGNER_KEY"] = DUMMY_KEY
        before = self._count()
        with self.assertRaises(PermissionError):
            self.engine.add_escalation_point("E-BADKEY", "偏差信号",
                                             "value > 0.9", "up", "high",
                                             designer_key="WRONG-KEY")
        self.assertEqual(self._count(), before)
        self.assertNotIn("E-BADKEY", self._codes())
        self.assertEqual(self._log_rows(), [])

    def test_store_layer_rejected_too(self):
        # 闸落在 store 层（引擎门面只是透传）——绕过门面直调 store 同样拒
        before = self._count()
        with self.assertRaises(PermissionError):
            self.engine.store.add_escalation_point("E-DIRECT", "偏差信号",
                                                   "value > 0.9", "up", "high")
        self.assertEqual(self._count(), before)
        self.assertEqual(self._log_rows(), [])


class KeyAcceptedTests(_Base):
    """C/D：正确密钥 ⇒ 落库 + 审计；门面与 store 同形。"""

    def test_correct_key_applies_and_writes_audit(self):
        os.environ["AEIS_DESIGNER_KEY"] = DUMMY_KEY
        before = self._count()
        eid = self.engine.add_escalation_point("E-OK", "偏差信号",
                                               "value > 0.9", "up", "high",
                                               designer_key=DUMMY_KEY)
        self.assertIsInstance(eid, str)
        self.assertEqual(self._count(), before + 1)
        self.assertIn("E-OK", self._codes())

        rows = self._log_rows()
        self.assertEqual(len(rows), 1)
        action_type, summary, outcome = rows[0]
        self.assertEqual(action_type, "escalation_add")
        self.assertIn(eid, summary)
        payload = json.loads(outcome)
        self.assertEqual(payload["escalation_id"], eid)
        self.assertEqual(payload["code"], "E-OK")

    def test_facade_and_store_share_the_same_shape(self):
        # D：门面透传不缺——两路径都要密钥、都能落库，且签名形参同形
        import inspect
        store_sig = inspect.signature(self.engine.store.add_escalation_point)
        facade_sig = inspect.signature(self.engine.add_escalation_point)
        self.assertIn("designer_key", store_sig.parameters)
        self.assertIn("designer_key", facade_sig.parameters)
        self.assertIsNone(store_sig.parameters["designer_key"].default)
        self.assertIsNone(facade_sig.parameters["designer_key"].default)

        os.environ["AEIS_DESIGNER_KEY"] = DUMMY_KEY
        self.engine.add_escalation_point("E-F1", "信号", "value > 0.1", "up",
                                         "low", designer_key=DUMMY_KEY)
        self.engine.store.add_escalation_point("E-F2", "信号", "value > 0.2", "up",
                                               "low", designer_key=DUMMY_KEY)
        self.assertIn("E-F1", self._codes())
        self.assertIn("E-F2", self._codes())
        self.assertEqual(len(self._log_rows()), 2)


class SeedingSeparationTests(unittest.TestCase):
    """E：构造期播种不经带闸方法（否则未配置密钥的进程构造即抛）。"""

    def setUp(self):
        self._saved = os.environ.pop("AEIS_DESIGNER_KEY", None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop("AEIS_DESIGNER_KEY", None)
        if self._saved is not None:
            os.environ["AEIS_DESIGNER_KEY"] = self._saved

    def test_engine_constructs_without_key_and_seeds_six(self):
        eng = SpacetimeMemoryEngine(":memory:", identity="guard-749e")
        self.addCleanup(eng.close)
        rows = eng.list_escalation_points(enabled_only=False)
        self.assertEqual(len(rows), SEEDED)
        self.assertEqual({r["code"] for r in rows},
                         {"ESC-%03d" % i for i in range(1, SEEDED + 1)})
        # 播种不写审计（初始化不是治理动作）
        c = eng.store.conn.cursor()
        c.execute("SELECT COUNT(*) FROM action_logs")
        self.assertEqual(c.fetchone()[0], 0)

    def test_seed_uses_bare_write_not_gated_method(self):
        """源码锚：`_seed_escalation_points` 体内走裸写入口。"""
        with open(CORE_PY, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        start = next(i for i, l in enumerate(lines)
                     if l.strip().startswith("def _seed_escalation_points("))
        # 方法体：直到下一个同缩进的 def
        body = []
        for l in lines[start + 1:]:
            if l.strip().startswith("def ") and not l.startswith("        "):
                break
            body.append(l)
        text = "\n".join(body)
        self.assertIn("_insert_escalation_point(", text)
        self.assertNotIn("add_escalation_point(", text)


class CrossIssue130ConsistencyTests(_Base):
    """F：本件未放松 #130 已补的开关面闸。"""

    def test_toggle_still_requires_key(self):
        target = self.engine.list_escalation_points(enabled_only=False)[0]["id"]
        with self.assertRaises(PermissionError):
            self.engine.set_escalation_enabled(target, False)
        self.assertEqual(self._log_rows(), [])

    def test_both_write_paths_use_the_same_sentence(self):
        target = self.engine.list_escalation_points(enabled_only=False)[0]["id"]
        with self.assertRaises(PermissionError) as a:
            self.engine.add_escalation_point("E-S", "信号", "value > 0.1", "up", "low")
        with self.assertRaises(PermissionError) as b:
            self.engine.set_escalation_enabled(target, False)
        self.assertEqual(str(a.exception), str(b.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
