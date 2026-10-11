# -*- coding: utf-8 -*-
"""test_issue57_declared_param_contract · 公开 API 声明参数必须兑现（lingshu issue #57）
============================================================================
缺陷（#57 · 成立）：三处公开 API 的**声明参数在函数体内零引用**，契约不兑现：

  A. `SpacetimeMemoryEngine.check_escalation(signal_type, value=None)`
     —— `value` 一次都没出现；匹配只用 `signal_type` 做字符串包含。故
     `condition` 写互斥数值界（`"value > 0.9"` / `"value > 0.1"`）时，0.05 与
     0.99 得到**完全相同**的匹配（修前读数：v=0.05/0.5/0.99/None 全为两条）。
  B. `SpacetimeMemoryEngine.export_structured_trajectories(limit=10)`
     —— 函数体直接 `return self.export_trajectory(None, output_path)`，`limit`
     未用（修前 `limit used? False`），docstring 承诺的「最近 N 条」不成立。
  C. `lingshu.nn.stcnn.detect_period(signal, min_period=2)`
     —— `min_period` 从未参与判定（修前 `min_period=2/3/5/99 -> 2/2/2/2`）。

修法（逐处独立判断，见各函数 docstring）：
  A. 手写解析器 `_parse_escalation_value_bound`（**不用 eval/exec**）识别
     `value <op> <number>`；能解析则求值、`value is None` 该点不匹配；
     解析不出则**回落旧行为**（字符串包含）。
  B. `limit` 透传 `export_trajectory` 的 `_limit`，按 `temporal_coordinate`
     降序取全库最近 N 条；返回体形状不变。
  C. `period < min_period` ⇒ 返回 `None`（与既有 `count<2 ⇒ None` 同口径）。

同型面（如实标注）：全仓「签名声明参数、函数体零引用」同型共 **29 处**
（AST 扫描 `lingshu/**/*.py`），本件只修**有实害的 3 处**，其余 26 处未动。

断言组（回退即红）：
  T1 数值界求值生效：0.05 ⇒ []；0.5 ⇒ 仅 E-02；0.99 ⇒ E-01+E-02；None ⇒ []。
  T2 回落路径不回归：`condition` 无 `value` 标识符（如 `"...deviation>0.3"`）
     仍按包含匹配命中（`check_escalation("deviation")` 命中）。
  T3 解析器只认**独立词** `value`：`values` / `my_value` 不被当作参数名。
  T4 无 `eval` / `exec`（源码扫描 `core.py` 的解析器面）。
  T5 `limit` 真截断且返回体形状不变（`{"cycles","meta"}`）；`limit<0` 不截断。
  T6 `min_period` 参与判定（period=2 时 min_period=3 ⇒ None）。
  T7 既有 #130 守卫口径不回归（空串/长句/token 三档，见 test_issue130）。

定点变异自证（抽掉关键一行 ⇒ 必红并点名）：
  · 删 `check_escalation` 的 `if bound is not None:` 数值界块 ⇒ T1 红。
  · 把 `export_trajectory` 的 `_limit` 截断块退回 `limit=10` ⇒ T5 红。
  · 删 `detect_period` 的 `if period < min_period: return None` ⇒ T6 红。

运行（仓根）：python -X utf8 -m pytest tests/test_issue57_declared_param_contract.py -q
"""
from __future__ import annotations

import ast
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    SpacetimeMemoryEngine, _parse_escalation_value_bound,
)
from lingshu.nn.stcnn import detect_period  # noqa: E402

CORE_PY = os.path.join(REPO, "lingshu", "core", "core.py")


class EscalationValueBoundTests(unittest.TestCase):
    """A：`check_escalation` 的 `value` 真参与判定。"""

    def setUp(self):
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-57a")
        self.addCleanup(self.engine.close)
        self.engine.add_escalation_point("E-01", "偏差信号", "value > 0.9", "up", "high")
        self.engine.add_escalation_point("E-02", "偏差信号", "value > 0.1", "up", "high")

    def _codes(self, v):
        return {p["code"] for p in self.engine.check_escalation("偏差信号", v)}

    # ---- T1 数值界求值生效（修前四档全等 ⇒ 本条必红）----
    def test_t1_value_bound_is_evaluated(self):
        self.assertEqual(self._codes(0.05), set())            # 低于两个界
        self.assertEqual(self._codes(0.5), {"E-02"})          # 只过 0.1
        self.assertEqual(self._codes(0.99), {"E-01", "E-02"})  # 过两个界
        self.assertEqual(self._codes(None), set())            # 无值不可判

    def test_t1b_boundary_inclusive_ops(self):
        e = SpacetimeMemoryEngine(":memory:", identity="guard-57a2")
        self.addCleanup(e.close)
        e.add_escalation_point("G", "信号", "value >= 0.5", "up", "high")
        self.assertEqual({p["code"] for p in e.check_escalation("信号", 0.5)}, {"G"})
        self.assertEqual({p["code"] for p in e.check_escalation("信号", 0.49)}, set())

    # ---- T2 回落路径不回归（condition 无 value 标识符 ⇒ 旧包含匹配）----
    def test_t2_fallback_when_no_value_identifier(self):
        e = SpacetimeMemoryEngine(":memory:", identity="guard-57a3")
        self.addCleanup(e.close)
        e.add_escalation_point("E-03", "交叉", "deviation>0.3", "up", "high")
        # 左操作数是 deviation 而非 value ⇒ 不作数值界，含 token 仍命中
        self.assertIn("E-03", {p["code"] for p in e.check_escalation("deviation")})
        # 即便给了 value 也不因「无值不可判」而丢命中
        self.assertIn("E-03",
                      {p["code"] for p in e.check_escalation("deviation", 0.1)})

    def test_t2b_builtin_seeds_unchanged(self):
        # 内置 ESC-001..006 文本形态不是简单比较 ⇒ 一律回落包含匹配
        e = SpacetimeMemoryEngine(":memory:", identity="guard-57a4")
        self.addCleanup(e.close)
        self.assertEqual({p["code"] for p in e.check_escalation("自维持迹象")},
                         {"ESC-001"})
        self.assertEqual({p["code"] for p in e.check_escalation("P0")}, {"ESC-002"})
        self.assertEqual(e.check_escalation(""), [])
        self.assertEqual(e.check_escalation("绝不相干的信号XYZ"), [])

    # ---- T3 解析器只认独立词 `value` ----
    def test_t3_parser_only_matches_standalone_value(self):
        self.assertEqual(_parse_escalation_value_bound("value > 0.9"), (">", 0.9))
        self.assertEqual(_parse_escalation_value_bound("value>=0.5 时上报"),
                         (">=", 0.5))
        self.assertIsNone(_parse_escalation_value_bound("values > 0.9"))
        self.assertIsNone(_parse_escalation_value_bound("my_value > 0.9"))
        self.assertIsNone(_parse_escalation_value_bound("deviation > 0.3"))
        self.assertIsNone(_parse_escalation_value_bound("value 很大"))

    # ---- T4 禁 eval/exec ----
    def test_t4_no_eval_exec_in_parser(self):
        with open(CORE_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id in ("eval", "exec")]
        self.assertEqual(calls, [],
                         "core.py 出现 eval/exec 调用：%r" % [c.lineno for c in calls])


class ExportLimitTests(unittest.TestCase):
    """B：`export_structured_trajectories` 的 `limit` 真截断。"""

    def setUp(self):
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-57b")
        self.addCleanup(self.engine.close)
        for i in range(5):
            self.engine.add_perception(
                content=json.dumps({"cycle_id": "c%d" % i}),
                tags=["cycle_record_full"])
        c = self.engine.store.conn.cursor()
        rows = c.execute(
            "SELECT id FROM nodes WHERE tags LIKE '%cycle_record_full%'").fetchall()
        for i, (nid,) in enumerate(rows):
            c.execute("UPDATE nodes SET temporal_coordinate=? WHERE id=?",
                      (1000.0 + i, nid))
        self.engine.store.conn.commit()

    # ---- T5 真截断 + 形状不变（修前 limit=1 也返回全部/10 ⇒ 本条必红）----
    def test_t5_limit_truncates_to_most_recent(self):
        t1 = self.engine.export_structured_trajectories(limit=1)
        self.assertEqual(len(t1["cycles"]), 1)
        self.assertEqual(t1["cycles"][0]["cycle_id"], "c4")   # 最近（temporal 最大）
        t2 = self.engine.export_structured_trajectories(limit=2)
        self.assertEqual([x["cycle_id"] for x in t2["cycles"]], ["c4", "c3"])
        t0 = self.engine.export_structured_trajectories(limit=0)
        self.assertEqual(t0["cycles"], [])

    def test_t5b_shape_unchanged_and_negative_means_all(self):
        t = self.engine.export_structured_trajectories(limit=-1)
        self.assertEqual(set(t.keys()), {"cycles", "meta"})
        self.assertEqual(len(t["cycles"]), 5)


class DetectPeriodMinTests(unittest.TestCase):
    """C：`detect_period` 的 `min_period` 真参与判定。"""

    def _blink(self, period, cycles=6):
        sig = []
        for _ in range(cycles):
            sig += [0.0] * (period - 1) + [1.0]
        return sig

    # ---- T6 min_period 生效（修前 mp=99 也返回 2 ⇒ 本条必红）----
    def test_t6_min_period_participates(self):
        sig2 = self._blink(2)
        self.assertEqual(detect_period(sig2, min_period=2), 2)
        self.assertIsNone(detect_period(sig2, min_period=3))
        self.assertIsNone(detect_period(sig2, min_period=99))
        sig4 = self._blink(4)
        self.assertEqual(detect_period(sig4, min_period=2), 4)
        self.assertIsNone(detect_period(sig4, min_period=5))

    def test_t6b_default_unchanged(self):
        # 默认 min_period=2：既有回归（period=3）不变
        self.assertEqual(detect_period(self._blink(3)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
