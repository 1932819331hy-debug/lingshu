# -*- coding: utf-8 -*-
"""test_issue747_escalation_bound_parser_guard · #747 升级点数值界解析器词元收口
============================================================================
缺陷（#747 · 成立）：
  `_parse_escalation_value_bound` 的数字扫描循环判据是
  `condition[j].isdigit() or condition[j] == "."`——**只吃数字与点**，`e`/`E`
  指数段与尾随子串一律被截断在词元前缀，随后 `float(前缀)` 成功返回 ⇒ **静默
  钳到前缀值**，把书写者的界反转：

    `value > 1e400`（意图永不触发）  →  `('>', 1.0)`   ⇒ value=1e300 **命中**
    `value < 1e400`（意图恒真）      →  `('<', 1.0)`   ⇒ value=0.5 **命中**
    `value > 1e3`                    →  `('>', 1.0)`   ⇒ value=999  **命中**
    `value > 0.5xyz`                 →  `('>', 0.5)`   ⇒ 尾随子串被吞
    `value > 0.1 and value < 0.2`    →  `('>', 0.1)`   ⇒ **上限整段失效**

修法：
  ① 数字扫描改读**完整数字词元**（`_scan_esc_number`，含指数段，指数段必须带
     数字）；
  ② 三类「读不准」显式回落 `None`——**词元被截断**（尾随标识符字符或点）、
     **复合条件**（尾随还有比较符或独立词 `and`/`or`）、**不可表示**（`float()`
     溢出为 `±inf`）。判据是失效方向：`value < inf` 对一切有限值恒真 ⇒ 无条件
     触发（危机触发器最危险的方向）⇒ 不猜。

断言组（回退即红）：
  A 解析表（逐条 `_parse_escalation_value_bound` 读数）：
    · 指数记法读全：`1e3`→1000.0、`1.5e3`→1500.0、`1e-3`→0.001；
    · 溢出 ⇒ `None`：`1e400` / `-1e400`（正负两侧都拒）；
    · 下溢 ⇒ 按 IEEE 近似收下（**如实标注的边界**）：`1e-400`→`('>', 0.0)`；
    · 截断 ⇒ `None`：`0.9abc` / `1_000` / `3or 4` / `1.2.3` / `1e` / `1e+`；
    · 复合 ⇒ `None`：`0.1 and value < 0.2` / `0.9 or 其它` / `1and2`；
    · 既有形态不回归：`0.9` / `>= 0.5` / `> .5` / `> 5.` / `> -0.5` /
      `> 0.9 时上报`（后随非数字文字仍可解析，见 #57 契约）；
    · 左操作数判据不回归：`values` / `deviation` / `value 很大` ⇒ `None`。
  B 端到端（`add_escalation_point` → `check_escalation`；触发器文本**命中**，
    数值界是**追加**过滤 ⇒ 本组断言的是「静默伪造的阈值被移除」，**不是**
    「复合条件被正确求值」）：
    · `value > 1e3` ⇒ 999 **不命中**、1001 命中（修前阈值被钳成 1.0 ⇒ 999 命中）；
    · `value > 1e-3` ⇒ 0.005 **命中**（修前阈值 1.0 ⇒ 0.005 不命中）；
    · `value > 1e400` ⇒ 0.5 **命中**（修前被钳成 `> 1.0` ⇒ 0.5 不命中）
      ——即「不再拿伪造的 1.0 当界」；该点此后按文本匹配触发（#57 既有回落契约）。
    · `value > 0.1 and value < 0.2` ⇒ 0.05 **命中**（修前被钳成 `> 0.1` ⇒ 不命中）
      ——同上：移除首半截的伪造界；**上限仍不被求值**（复合条件回落，需表达式
      引擎，不在本件范围，docstring 边界②已声明）。
    · 正对照 `value > 0.3` ⇒ 0.2 不命中、0.5 命中（合法语义逐位不变）。

定点变异自证（抽掉关键一行 ⇒ 必红并点名）：
  · 把 `_scan_esc_number(condition, i)` 退回
    `while condition[j].isdigit() or condition[j] == "."` 手写扫描 ⇒ A 的指数记法
    与溢出两条、B 的三条必红。
  · 删 `if not math.isfinite(threshold): return None` ⇒ A 溢出两条、B 首条必红。
  · 删 `_esc_has_second_comparison` 判定 ⇒ A 复合三条、B 第三条必红。

运行（仓根）：python -X utf8 -m pytest tests/test_issue747_escalation_bound_parser_guard.py -q
"""
from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    SpacetimeMemoryEngine, _parse_escalation_value_bound,
)

DUMMY_KEY = "GOV-IMPL-DUMMY-KEY"   # #749：写入面闸的夹具密钥


def _designer_key(case):
    """#749 夹具：写入面需密钥；本件断言面是**解析器**，故只做夹具适配。"""
    saved = os.environ.pop("AEIS_DESIGNER_KEY", None)

    def _restore():
        os.environ.pop("AEIS_DESIGNER_KEY", None)
        if saved is not None:
            os.environ["AEIS_DESIGNER_KEY"] = saved

    case.addCleanup(_restore)
    os.environ["AEIS_DESIGNER_KEY"] = DUMMY_KEY
    return DUMMY_KEY


class ParserTokenTests(unittest.TestCase):
    """A：解析表逐条（#747 的核心判据面）。"""

    def _p(self, cond):
        return _parse_escalation_value_bound(cond)

    # ---- 指数记法必须读全（修前一律截断到前缀）----
    def test_exponent_notation_is_read_whole(self):
        self.assertEqual(self._p("value > 1e3"), (">", 1000.0))
        self.assertEqual(self._p("value > 1.5e3"), (">", 1500.0))
        self.assertEqual(self._p("value > 1e-3"), (">", 0.001))
        self.assertEqual(self._p("value >= 2E2"), (">=", 200.0))
        self.assertEqual(self._p("value > 1e+3"), (">", 1000.0))

    # ---- 溢出 ⇒ 不猜（None）----
    def test_overflow_falls_back_instead_of_pinning_infinity(self):
        self.assertIsNone(self._p("value > 1e400"))
        self.assertIsNone(self._p("value < 1e400"))
        self.assertIsNone(self._p("value < -1e400"))

    # ---- 下溢：如实标注的边界（IEEE 近似收下，不拒）----
    def test_underflow_is_accepted_as_ieee_approximation(self):
        self.assertEqual(self._p("value > 1e-400"), (">", 0.0))
        self.assertEqual(self._p("value > 0"), (">", 0.0))

    # ---- 词元被截断 ⇒ None ----
    def test_truncated_token_falls_back(self):
        for cond in ("value > 0.9abc", "value > 1_000", "value > 3or 4",
                     "value > 1.2.3", "value > 1e", "value > 1e+",
                     "value > 1.5e3x"):
            self.assertIsNone(self._p(cond), cond)

    # ---- 复合条件 ⇒ None（不只取首个界）----
    def test_compound_condition_falls_back(self):
        for cond in ("value > 0.1 and value < 0.2",
                     "value > 1e400 and value < 2",
                     "value > 0.9 or 其它",
                     "value>1and2",
                     "value == 0.5 and value > 0.1"):
            self.assertIsNone(self._p(cond), cond)

    # ---- 既有形态不回归（#57 契约：后随非数字文字仍可解析）----
    def test_legit_forms_unchanged(self):
        self.assertEqual(self._p("value > 0.9"), (">", 0.9))
        self.assertEqual(self._p("value >= 0.5"), (">=", 0.5))
        self.assertEqual(self._p("value > .5"), (">", 0.5))
        self.assertEqual(self._p("value > 5."), (">", 5.0))
        self.assertEqual(self._p("value > -0.5"), (">", -0.5))
        self.assertEqual(self._p("value > 0.9 时上报"), (">", 0.9))
        self.assertEqual(self._p("value > 0.9（持续3轮）"), (">", 0.9))

    # ---- 左操作数判据不回归（只认独立词 value）----
    def test_left_operand_judgement_unchanged(self):
        for cond in ("values > 0.9", "my_value > 0.9", "deviation > 0.3",
                     "value 很大", "", None):
            self.assertIsNone(self._p(cond), cond)


class EndToEndTests(unittest.TestCase):
    """B：`add_escalation_point` → `check_escalation` 端到端。"""

    def setUp(self):
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-747")
        self.addCleanup(self.engine.close)
        self.key = _designer_key(self)

    def _add(self, code, condition):
        self.engine.add_escalation_point(code, "偏差信号", condition, "up", "high",
                                         designer_key=self.key)

    def _codes(self, value):
        return {p["code"] for p in self.engine.check_escalation("偏差信号", value)}

    def test_overflow_condition_no_longer_uses_fabricated_threshold(self):
        # 修前：('>', 1.0) ⇒ 0.5 不命中（被静默钳成 >1.0 的界挡住）
        # 修后：解析回落 ⇒ 该点不再被伪造阈值过滤（按文本匹配触发）
        self._add("EXP-UP", "value > 1e400")
        self.assertIn("EXP-UP", self._codes(0.5))

    def test_exponent_condition_uses_whole_number(self):
        # 修前：('>', 1.0) ⇒ 999 命中
        self._add("EXP-INT", "value > 1e3")
        self.assertNotIn("EXP-INT", self._codes(999.0))
        self.assertIn("EXP-INT", self._codes(1001.0))

    def test_negative_exponent_condition_uses_whole_number(self):
        # 修前：('>', 1.0) ⇒ 0.005 不命中（被钳到 1.0）
        self._add("EXP-SMALL", "value > 1e-3")
        self.assertNotIn("EXP-SMALL", self._codes(0.0005))
        self.assertIn("EXP-SMALL", self._codes(0.005))

    def test_compound_condition_first_half_no_longer_fabricated(self):
        # 修前：('>', 0.1) ⇒ 0.05 不命中；修后：回落 ⇒ 不再被首半截的界过滤。
        # ★本断言**不**主张「上限被正确求值」——复合条件回落，上限仍不求值。
        self._add("AND-ESC", "value > 0.1 and value < 0.2")
        self.assertIn("AND-ESC", self._codes(0.05))

    def test_legit_bound_semantics_unchanged(self):
        # 正对照：合法写法逐位不变
        self._add("CTL", "value > 0.3")
        self.assertNotIn("CTL", self._codes(0.2))
        self.assertIn("CTL", self._codes(0.5))

    def test_fallback_path_does_not_regress_token_usage(self):
        # 回落语义（#57）：condition 无独立词 value ⇒ 仍按文本包含命中
        self._add("FB", "偏差信号 deviation>0.3")
        self.assertIn("FB", self._codes(0.1))
        self.assertIn("FB", self._codes(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
