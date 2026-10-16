# -*- coding: utf-8 -*-
"""test_issue130_escalation_gate · 升级点匹配修正 + 授权闸 + 审计留痕（issue #130）
============================================================================
缺陷（#130 · 部分成立）：
  ① 匹配异常（`check_escalation`）：判据 `signal_type in point["trigger"] or
     signal_type in point["condition"]` **方向反了**——本意应是「触发词出现在信号
     里」。旁证读数：精确触发词命中 1、含词长句命中 0、**空串命中全部 6 条**。
  ② `set_escalation_enabled` 无授权、无审计（`action_logs` 前后 0→0）。

修法（最小 · 复用既有先例）：
  ① 空串/纯空白短路返回 `[]`；包含方向双向判定（保留既有调用方以 token 作信号的
     用法，如私有侧 `check_escalation("deviation", d_norm)`）。
  ② `set_escalation_enabled` 加 `designer_key` + `verify_designer`（与
     `adjudicate_promotion` 同款 fail-closed）+ 落 `action_logs`。

断言组（回退即红）：
  A 匹配：空串 / 纯空白 ⇒ 0；含词长句命中触发词（方向修正）；既有 token 用法不回归。
  B 无密钥 / 错误密钥 ⇒ `PermissionError`，且开关**未变**、无审计行。
  C 正确密钥 ⇒ 开关生效 + `action_logs` 新增一行且 `outcome` 可查。
  D 修前读数对照（改坏方向即红）：空串不得命中全部。

★本件 2026-10-16 追加（#130 裁定「路 B」的剩余项守卫，四条逐条落地 · 只追加，
  既有 A–D 断言**一字未动**）：
  G1 `needs_designer=True ⇒ recursive_reflect 的 status != "reflected"`（＝
     `pending_designer`）+ 防误杀（可逆 ⇒ 仍 `reflected`、深度上限 ⇒ 仍
     `structural_blindspot`）。
  G2 待办表**非空**（登记动作真的落了库；返回体 todo_id 与库内行同源）。
  G3 `set_escalation_enabled` 在 **SUB** 上被拒（即便持正确密钥）；PRIMARY 可关。
  G4 **启动按 code 补齐**（表非空但缺一条 ⇒ 启动后该条在场，且既有行未被改写）。
  H  悬空 `designer_decide` 收口（不新造该函数；`action` 文案指向待办 id）。
  逐条判别力自证见下方各类的类注释。

运行（仓根）：python -X utf8 tests/test_issue130_escalation_gate.py
或：python -X utf8 -m pytest tests/test_issue130_escalation_gate.py -v
退出码：0 = 全过；1 = 有断言失败
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    LayeredStore, Role, SpacetimeMemoryEngine)

CORE_PY = os.path.join(REPO, "lingshu", "core", "core.py")
DUMMY_KEY = "GOV-IMPL-DUMMY-KEY"
SEEDED = 6  # ESC-001..ESC-006


class EscalationMatchTests(unittest.TestCase):
    """匹配修正（无需密钥）。"""

    def setUp(self):
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-130m")
        self.addCleanup(self.engine.close)

    def _codes(self, matches):
        return {p["code"] for p in matches}

    def test_seed_count_unchanged(self):
        # 前置：默认播种 6 条（空串命中的「6」正是它们）
        self.assertEqual(len(self.engine.list_escalation_points(enabled_only=False)),
                         SEEDED)

    def test_empty_and_whitespace_signal_match_nothing(self):
        # 核心修正：空串不得命中全部（修前 = 6）
        self.assertEqual(self.engine.check_escalation(""), [])
        self.assertEqual(self.engine.check_escalation("   "), [])

    def test_exact_trigger_word_hits_its_point(self):
        self.assertEqual(self._codes(self.engine.check_escalation("自维持迹象")),
                         {"ESC-001"})

    def test_long_sentence_containing_trigger_now_hits(self):
        # 方向修正：修前命中 0；触发词出现在信号里 ⇒ 命中 ESC-001
        self.assertIn("ESC-001",
                      self._codes(self.engine.check_escalation("关于自维持迹象的报告")))

    def test_existing_token_usage_does_not_regress(self):
        # 回归：私有侧既有调用方以 token 作信号（signal in condition）
        self.assertIn("ESC-004", self._codes(self.engine.check_escalation("deviation")))
        self.assertIn("ESC-002", self._codes(self.engine.check_escalation("P0")))
        self.assertIn("ESC-002", self._codes(self.engine.check_escalation("结构威胁")))

    def test_unknown_signal_matches_nothing(self):
        self.assertEqual(self.engine.check_escalation("绝不相干的信号XYZ"), [])


class EscalationGateAuditTests(unittest.TestCase):
    """授权闸 + 审计留痕。"""

    def setUp(self):
        self._saved = os.environ.pop("AEIS_DESIGNER_KEY", None)
        self.addCleanup(self._restore)
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-130g")
        self.addCleanup(self.engine.close)
        self.target = self.engine.list_escalation_points(enabled_only=False)[0]["id"]

    def _restore(self):
        os.environ.pop("AEIS_DESIGNER_KEY", None)
        if self._saved is not None:
            os.environ["AEIS_DESIGNER_KEY"] = self._saved

    def _enabled(self, esc_id):
        for p in self.engine.list_escalation_points(enabled_only=False):
            if p["id"] == esc_id:
                return p["enabled"]
        raise AssertionError("升级点不存在: " + esc_id)

    def _log_rows(self):
        c = self.engine.store.conn.cursor()
        c.execute("SELECT action_type, summary, outcome FROM action_logs"
                  " ORDER BY id")
        return c.fetchall()

    # ---- B 无密钥 / 错误密钥 ⇒ 拒 + 未变 + 无审计 ----
    def test_no_key_rejected_and_state_unchanged_no_audit(self):
        with self.assertRaises(PermissionError):
            self.engine.set_escalation_enabled(self.target, False)
        self.assertTrue(self._enabled(self.target))
        self.assertEqual(len(self._log_rows()), 0)

    def test_wrong_key_rejected_no_audit(self):
        os.environ["AEIS_DESIGNER_KEY"] = DUMMY_KEY
        with self.assertRaises(PermissionError):
            self.engine.set_escalation_enabled(self.target, False,
                                               designer_key="WRONG-KEY")
        self.assertTrue(self._enabled(self.target))
        self.assertEqual(len(self._log_rows()), 0)

    # ---- C 正确密钥 ⇒ 生效 + 审计可查 ----
    def test_correct_key_applies_and_writes_audit(self):
        os.environ["AEIS_DESIGNER_KEY"] = DUMMY_KEY
        self.engine.set_escalation_enabled(self.target, False,
                                           designer_key=DUMMY_KEY)
        self.assertFalse(self._enabled(self.target))
        rows = self._log_rows()
        self.assertEqual(len(rows), 1)
        action_type, summary, outcome = rows[0]
        self.assertEqual(action_type, "escalation_toggle")
        self.assertIn(self.target, summary)
        self.assertEqual(json.loads(outcome),
                         {"escalation_id": self.target, "enabled": False})

    def test_reenable_also_audited(self):
        os.environ["AEIS_DESIGNER_KEY"] = DUMMY_KEY
        self.engine.set_escalation_enabled(self.target, False,
                                           designer_key=DUMMY_KEY)
        self.engine.set_escalation_enabled(self.target, True,
                                           designer_key=DUMMY_KEY)
        self.assertTrue(self._enabled(self.target))
        rows = self._log_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(json.loads(rows[1][2])["enabled"], True)


# =============================================================================
# #130 路 B（裁定帖「守卫」节 · 四条断言逐条落地）
# -----------------------------------------------------------------------------
# 裁定：**路 B —— 只登记不阻断 ＋ 按 code 直查 ＋ 启动补齐**。上面两组钉的是
# #130 的**匹配面与密钥闸**（既有断言一字未动）；下面四组钉的是裁定帖点名的
# 「本件剩余项尚无守卫」那四条：
#   G1 `needs_designer=True ⇒ recursive_reflect 的 status != "reflected"`
#      （＝ `pending_designer`）＋ 防误杀（可逆 ⇒ 仍 `reflected`）
#   G2 待办表**非空**（登记动作真的落了库）
#   G3 `set_escalation_enabled` 在 **SUB** 上被拒（PRIMARY 可关）
#   G4 **启动按 code 补齐**（表非空但缺一条 ⇒ 启动后该条在场，且既有行未被改写）
# 另加 H 组钉「悬空字符串」的收口（`designer_decide` 全仓无定义 ⇒ 文案改指待办 id）。
#
# 判别力自证（逐条把修复回退到缺陷形态 ⇒ 必红；还原 ⇒ 复绿且 git diff 为空）：
#   G1 回退 `report["status"] = "reflected"`（恒真）⇒ G1 红
#   G2 回退「不登记待办」（去掉 add_designer_todo 调用）⇒ G2 红
#   G3 回退角色判（删 `if self.role != Role.PRIMARY: raise`）⇒ G3 红
#   G4 回退「仅空表播种」（`if 表非空: return`）⇒ G4 红
# =============================================================================


class NeedsDesignerStatusTests(unittest.TestCase):
    """G1：`needs_designer=True` ⇒ `status` 不再是 `reflected`（路 B 的返回契约扩展）。

    缺陷形态（回退即红）：`recursive_reflect` 无论 `needs_designer` 取值都固定
    `report["status"] = "reflected"` ⇒ `needs_designer` 是一个**已置位、无人读**
    的标志（裁定帖「问题重述」原文）。
    """

    def setUp(self):
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-130g1")
        self.addCleanup(self.engine.close)

    def test_irreversible_claim_yields_pending_designer(self):
        # 判据：不可逆（「删除」非否定式）+ 结构级影响 ⇒ needs_designer=True
        rep = self.engine.recursive_reflect("删除用户数据")
        self.assertIs(rep["verdict"]["needs_designer"], True, rep["verdict"])
        self.assertNotEqual(rep["status"], "reflected",
                            "needs_designer=True 仍返回 reflected（死标志未接线）")
        self.assertEqual(rep["status"], "pending_designer")

    def test_reversible_claim_still_reflected(self):
        # 防误杀：可逆 claim 的 status 逐字不变（扩展不得改写既有取值）
        rep = self.engine.recursive_reflect("不删除用户数据，只做只读查询")
        self.assertIs(rep["verdict"]["needs_designer"], False, rep["verdict"])
        self.assertEqual(rep["status"], "reflected")

    def test_deep_recursion_status_unchanged(self):
        # 防误杀：深度上限那档取值与行为逐字不变
        rep = self.engine.recursive_reflect("任意主张", depth=3, max_depth=3)
        self.assertEqual(rep["status"], "structural_blindspot")


class DesignerTodoRegistrationTests(unittest.TestCase):
    """G2：待办表**非空**（登记动作真的落了库，不是只改了返回体文案）。"""

    def setUp(self):
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-130g2")
        self.addCleanup(self.engine.close)

    def _todo_rows(self):
        c = self.engine.store.conn.cursor()
        c.execute("SELECT id, code, claim, action, status, consumed_at"
                  " FROM designer_todos ORDER BY created_at, rowid")
        return c.fetchall()

    def test_todo_row_landed_and_listed(self):
        rep = self.engine.recursive_reflect("删除用户数据")
        rows = self._todo_rows()
        self.assertEqual(len(rows), 1, "待办表为空：登记动作没落库（%r）" % (rows,))
        tid, code, claim, action, status, consumed_at = rows[0]
        self.assertEqual(code, "ESC-002")
        self.assertEqual(claim, "删除用户数据")
        self.assertEqual(status, "open")
        self.assertIsNone(consumed_at)
        # 返回体与库内一致（同一 id），且 action 文案指向待办 id（不再是悬空函数名）
        self.assertEqual(rep["verdict"]["todo_id"], tid)
        self.assertIn("待办 #" + tid, rep["verdict"]["action"])
        # 只读列举面同源可见
        listed = self.engine.list_designer_todos()
        self.assertEqual([r["id"] for r in listed], [tid])
        self.assertEqual(self.engine.list_designer_todos(status="consumed"), [])

    def test_no_todo_for_reversible_claim(self):
        # 防误杀：不需要设计者的反思不得登记待办
        self.engine.recursive_reflect("不删除用户数据，只做只读查询")
        self.assertEqual(self._todo_rows(), [])

    def test_todo_table_present_in_schema_lists(self):
        # 结构面：新表必须在两处清单里（否则老库建不出表 / 备份不含它）
        self.assertIn("designer_todos", LayeredStore._SCHEMA_TABLES)
        self.assertIn("designer_todos", SpacetimeMemoryEngine.M13_TABLES)
        self.assertGreaterEqual(LayeredStore.SCHEMA_VERSION, 3,
                                "新增表未升 SCHEMA_VERSION：已有库会被启动只读化守卫"
                                "跳过建表块 ⇒ 运行时报 no such table")


class SubRoleRejectedTests(unittest.TestCase):
    """G3：`set_escalation_enabled` 在 **SUB** 上被拒（即便持正确密钥）；PRIMARY 可关。

    缺陷形态（回退即红）：只有 `verify_designer` 密钥闸、**无角色判** ⇒ SUB 实例
    只要持有密钥（同部署下天然持有，`#586`）即可关掉共享治理表里的全部升级点。
    """

    def setUp(self):
        self._saved = os.environ.pop("AEIS_DESIGNER_KEY", None)
        self.addCleanup(self._restore)
        os.environ["AEIS_DESIGNER_KEY"] = DUMMY_KEY
        self.primary = SpacetimeMemoryEngine(":memory:", identity="guard-130g3p",
                                             role=Role.PRIMARY)
        self.addCleanup(self.primary.close)
        self.sub = SpacetimeMemoryEngine(":memory:", identity="guard-130g3s",
                                         role=Role.SUB)
        self.addCleanup(self.sub.close)

    def _restore(self):
        os.environ.pop("AEIS_DESIGNER_KEY", None)
        if self._saved is not None:
            os.environ["AEIS_DESIGNER_KEY"] = self._saved

    def _target(self, engine):
        return engine.list_escalation_points(enabled_only=False)[0]

    def test_sub_with_valid_key_is_rejected(self):
        pt = self._target(self.sub)
        with self.assertRaises(PermissionError):
            self.sub.set_escalation_enabled(pt["id"], False, designer_key=DUMMY_KEY)
        # 零副作用：开关未变、零审计行（拒在写之前）
        self.assertTrue(self._target(self.sub)["enabled"])
        self.assertEqual(self._audit_rows(self.sub), 0)

    def test_primary_with_valid_key_still_applies(self):
        pt = self._target(self.primary)
        self.primary.set_escalation_enabled(pt["id"], False, designer_key=DUMMY_KEY)
        self.assertFalse(self._target(self.primary)["enabled"])
        self.assertEqual(self._audit_rows(self.primary), 1)

    def test_sub_rejection_does_not_relax_the_key_gate(self):
        # 无密钥 ⇒ 报错文案仍是既有 D-007 句面（密钥闸在前、逐字保留）
        pt = self._target(self.sub)
        with self.assertRaises(PermissionError) as ctx:
            self.sub.set_escalation_enabled(pt["id"], False)
        self.assertIn("D-007 设计者认证失败", str(ctx.exception))

    def _audit_rows(self, engine):
        return engine.store.conn.execute(
            "SELECT COUNT(*) FROM action_logs").fetchone()[0]


class SeedByCodeBackfillTests(unittest.TestCase):
    """G4：启动**按 code 补齐**（表非空但缺一条 ⇒ 启动后该条在场，既有行未被改写）。

    缺陷形态（回退即红）：`_seed_escalation_points` 旧口径「仅空表播种」
    （`if self.store.list_escalation_points(enabled_only=False): return`）——
    任何一次运行期新增/回灌都会让表非空，此后内置 6 条**永久补不齐**。
    """

    def setUp(self):
        self._saved = os.environ.pop("AEIS_DESIGNER_KEY", None)
        self.addCleanup(self._restore)
        self._dir = tempfile.mkdtemp(prefix="lingshu130g4_")
        self.addCleanup(shutil.rmtree, self._dir, True)
        self.db = os.path.join(self._dir, "seed.db")

    def _restore(self):
        os.environ.pop("AEIS_DESIGNER_KEY", None)
        if self._saved is not None:
            os.environ["AEIS_DESIGNER_KEY"] = self._saved

    def test_missing_code_backfilled_and_existing_row_untouched(self):
        eng = SpacetimeMemoryEngine(self.db, identity="guard-130g4a")
        self.addCleanup(eng.close)
        self.assertEqual(len(eng.list_escalation_points(enabled_only=False)), SEEDED)

        c = eng.store.conn.cursor()
        # ① 既有行改写探针：把 ESC-001 的 trigger 改掉（若被播种覆盖即红）
        c.execute("UPDATE escalation_points SET trigger=? WHERE code=?",
                  ("被人工改写的触发词", "ESC-001"))
        # ② 缺一条：删掉 ESC-004（表**仍非空**——这正是旧口径误判为「已播种」的形态）
        c.execute("DELETE FROM escalation_points WHERE code=?", ("ESC-004",))
        c.execute("UPDATE escalation_points SET enabled=0 WHERE code=?", ("ESC-006",))
        eng.store.conn.commit()
        eng.close()

        again = SpacetimeMemoryEngine(self.db, identity="guard-130g4b")
        self.addCleanup(again.close)
        rows = {p["code"]: p for p in again.list_escalation_points(enabled_only=False)}
        # ③ 缺的那条必须在场（旧口径下此处缺失 ⇒ 红）
        self.assertIn("ESC-004", rows, "启动未按 code 补齐：ESC-004 缺席（%r）"
                      % (sorted(rows),))
        self.assertEqual(rows["ESC-004"]["trigger"], "交叉验证偏差")
        # ④ 既有行未被改写、未被覆盖
        self.assertEqual(rows["ESC-001"]["trigger"], "被人工改写的触发词",
                         "既有 code 的行被播种覆盖（裁定帖：已有 code 的行不动）")
        # ⑤ 不重复补齐、不重新启用（启停是治理动作，构造期无权代作）
        self.assertEqual(len(rows), SEEDED)
        self.assertFalse(rows["ESC-006"]["enabled"],
                         "构造期补齐不得擅自重新启用已停用的升级点")

    def test_partially_filled_table_is_not_treated_as_seeded(self):
        # 更极端的缺陷形态：表里只有 1 条非内置 code ⇒ 旧口径整块跳过
        eng = SpacetimeMemoryEngine(self.db, identity="guard-130g4c")
        c = eng.store.conn.cursor()
        c.execute("DELETE FROM escalation_points")
        c.execute("INSERT INTO escalation_points VALUES (?,?,?,?,?,?,?,?)",
                  ("esc_probe", "E-PROBE", "探针触发词", "探针条件", "探针动作",
                   "low", 1, 0.0))
        eng.store.conn.commit()
        eng.close()

        again = SpacetimeMemoryEngine(self.db, identity="guard-130g4d")
        self.addCleanup(again.close)
        codes = {p["code"] for p in again.list_escalation_points(enabled_only=False)}
        for i in range(1, SEEDED + 1):
            self.assertIn("ESC-%03d" % i, codes, "内置 code 未补齐：%r" % (sorted(codes),))
        self.assertIn("E-PROBE", codes, "非内置 code 的行被动了（只许补齐、不许删改）")


class DanglingDesignerDecideTests(unittest.TestCase):
    """H：悬空 `designer_decide` 字符串收口（裁定帖：不新造该函数，改文案指向待办表）。"""

    def setUp(self):
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-130h")
        self.addCleanup(self.engine.close)

    def test_no_dangling_function_defined_anywhere(self):
        """AST 判据：`lingshu/**` 里**不得**出现名为 `designer_decide` 的函数定义。

        用 AST 而非文本子串：本仓多处 docstring **正文**会提到这个名字（如
        `_terminal_judgment` 写明「不新造 `designer_decide`」），文本判据会把
        正当的说明文字也判红。判据只针对**真的定义了一个函数**这件事。
        """
        root = os.path.join(REPO, "lingshu")
        hits = []
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                if "designer_decide" not in text:
                    continue
                tree = ast.parse(text, filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and node.name == "designer_decide":
                        hits.append("%s:%d" % (path, node.lineno))
        self.assertEqual(hits, [],
                         "新造了 designer_decide：与「结构层写入走三段式」既有契约冲突"
                         "（本仓设计者终裁唯一合规通路 = propose → verify → adjudicate）")

    def test_action_text_points_to_todo_not_function_name(self):
        rep = self.engine.recursive_reflect("删除用户数据")
        action = rep["verdict"]["action"]
        self.assertNotIn("designer_decide", action)
        self.assertIn("待办 #", action)


if __name__ == "__main__":
    unittest.main(verbosity=2)
