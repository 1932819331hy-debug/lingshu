#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coggraph 守卫 · #654 with_edge 与 n_edges 统一「有边」判据（声明归属）。

缺陷态（修复前 main）：export_coggraph.py 相邻两行两套判据——
  · n_edges 按 edge_owner（声明归属）计（#534/#605 钉住的既有语义）；
  · with_edge 按「存活边两端」计（any(e.s==nid or e.t==nid)），且 meta.counts /
    edge_report.md 的「有边节点」不注明口径 ⇒ 同一产物对「谁有边」给出集合
    不重叠的两个答案，无法对账：
      mapping 夹具（a 声明 edges:{b: c}）：n_edges={a:1,b:0,c:0}，两端集合
        {b,c}——唯一声明了边的 a（n_edges=1）被报告的「有边节点 2/3」排除；
      list 夹具（a 声明 edges:[{target: b}]）：被指向的 b（n_edges=0）反而
        计入 with_edge=2/2=100%。

守卫组（每件钉「缺陷不再存在」，不是「代码能跑」）：

G1（主体·可对账）mapping 与 list 两形态：meta.counts.with_edge
   == meta.counts.declared_edge_nodes == count(n_edges>0)——口径统一后数字一致
   （旧码两夹具实得 2 而声明口径均为 1）。附零边传播钉：全悬空
   （n_edges 全 0）⇒ with_edge 必为 0（题面补充守卫建议的下界件）。
G2（主体·报告不误导）mapping 夹具：报告「有边节点」行数到声明者（1 / 3，
   旧码 2 / 3 且指的是 b/c——声明者 a 被「报告说它没边」）；该行必须注明口径
   （含 n_edges>0 字样）；行内数字与 counts.with_edge / counts.nodes 一致，
   density_pct 与 100*with_edge/节点数 复算一致。
G3（防过修）既有口径零回归：
   · #605 边数三口径：counts.edges == sum(edge_types) == sum(n_edges)；
   · #534：mapping 外源 s 的边计数仍归声明节点（d 声明 a→b ⇒ d.n_edges==1，
     不得按 e["s"] 重归属给 a）；n_edges 表与 #605 G4a 同构夹具逐值相同；
   · derived_from 声明边同样进声明口径（b 声明 derived_from: a ⇒ with_edge 计 b）。

变异自查（人工两向，已实跑）：把 with_edge 行改回两端判据
   （any(e["s"]==nid or e["t"]==nid ...)）⇒ G1 两件红、G2 红、G3 的 derived_from
   口径件红（它钉的也是声明口径）；#534/#605 边数与归属断言面仍绿——两端判据
   不触碰边数/n_edges 口径，题面实测既有四守卫对此零判别力，本件即为此而设。
   恢复声明判据 ⇒ 全绿（6 passed）。

运行：
  · pytest：python -X utf8 -m pytest tests/test_issue654_export_caliber_guard.py -q --no-header
  · 脚本式：python -X utf8 tests/test_issue654_export_caliber_guard.py
全部夹具写在 tempfile，绝不读写真源。
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
EXPORT = HERE / "tools" / "coggraph" / "export_coggraph.py"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _doc(nid, title, extra=""):
    return "---\nid: %s\nlayer: knowledge\n%s---\n# 功能名：%s\n" % (nid, extra, title)


def _run(cmd):
    return subprocess.run([sys.executable, "-X", "utf8", *cmd], capture_output=True,
                          text=True, encoding="utf-8", timeout=120,
                          env=dict(os.environ, PYTHONUTF8="1"))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _export(self, src, out):
        p = _run([str(EXPORT), "--root", str(src), "--out", str(out), "--write"])
        self.assertEqual(p.returncode, 0, "导出退出码 %d\nstderr:\n%s" % (p.returncode, p.stderr))
        g = json.loads((Path(out) / "graph.json").read_text(encoding="utf-8"))
        r = (Path(out) / "edge_report.md").read_text(encoding="utf-8")
        return g, r


class CaliberParityTests(_Base):
    """G1：同产物可对账——with_edge / declared_edge_nodes / count(n_edges>0) 三数一致。"""

    def _assert_parity(self, g):
        counts = g["meta"]["counts"]
        declared = sum(1 for n in g["nodes"] if n["n_edges"] > 0)
        self.assertIn("declared_edge_nodes", counts,
                      "meta.counts 必须带口径锚点字段 declared_edge_nodes（#654 自证字段）")
        self.assertEqual(counts["with_edge"], declared,
                         "with_edge 必须与 count(n_edges>0) 同口径同值（旧码按存活边两端计："
                         "mapping 形态声明者 n_edges=1 却被排除、list 形态被指向者 n_edges=0 "
                         "反而计入）：%r vs 声明口径 %d" % (counts["with_edge"], declared))
        self.assertEqual(counts["declared_edge_nodes"], declared,
                         "declared_edge_nodes 是声明口径的显式锚点，必须与 count(n_edges>0) 相等")
        # 与 #605 边数三口径合起来构成完整对账面：sum(n_edges) == counts.edges
        self.assertEqual(sum(n["n_edges"] for n in g["nodes"]), counts["edges"])

    def test_g1_mapping_form_declarer_is_counted(self):
        src = self.base / "src"
        _write(str(src / "knowledge" / "x" / "a.md"), _doc("a", "甲", extra="edges:\n  b: c\n"))
        _write(str(src / "knowledge" / "x" / "b.md"), _doc("b", "乙"))
        _write(str(src / "knowledge" / "x" / "c.md"), _doc("c", "丙"))
        g, _ = self._export(src, self.base / "out")
        n_by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(n_by_id["a"]["n_edges"], 1, "夹具不变量：a 声明 1 条（mapping 归一为 b→c）")
        self.assertEqual(g["meta"]["counts"]["with_edge"], 1,
                         "唯一声明了边的 a 必须被计为「有边节点」（旧码两端口径=2，指 b/c）")
        self._assert_parity(g)

    def test_g1_list_form_pointed_target_not_counted(self):
        src = self.base / "src"
        _write(str(src / "knowledge" / "x" / "a.md"),
               _doc("a", "甲", extra="edges:\n  - {target: b, type: causal}\n"))
        _write(str(src / "knowledge" / "x" / "b.md"), _doc("b", "乙"))
        g, _ = self._export(src, self.base / "out")
        n_by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(n_by_id["b"]["n_edges"], 0, "夹具不变量：b 未声明边")
        self.assertEqual(g["meta"]["counts"]["with_edge"], 1,
                         "被指向的 b（n_edges=0）不得计入 with_edge（旧码两端口径=2/2=100%）")
        self._assert_parity(g)

    def test_g1_all_dangling_zero_propagation(self):
        # 题面补充守卫建议的下界件：n_edges 全 0 ⇒ with_edge 必为 0（口径再漂也
        # 不允许「零声明而有边节点」的自相矛盾读数）。
        src = self.base / "src"
        _write(str(src / "knowledge" / "only" / "a.md"),
               _doc("a", "单点", extra="edges:\n  - {s: a, t: ghost, type: causal}\n"))
        g, _ = self._export(src, self.base / "out")
        self.assertTrue(all(n["n_edges"] == 0 for n in g["nodes"]))
        self.assertEqual(g["meta"]["counts"]["with_edge"], 0,
                         "n_edges 全 0 ⇒ with_edge 必为 0")
        self._assert_parity(g)


class ReportCaliberTests(_Base):
    """G2：报告「有边节点」行不再误导——数到声明者并注明口径。"""

    def test_g2_mapping_form_report_counts_declarer_and_names_caliber(self):
        src = self.base / "src"
        _write(str(src / "knowledge" / "x" / "a.md"), _doc("a", "甲", extra="edges:\n  b: c\n"))
        _write(str(src / "knowledge" / "x" / "b.md"), _doc("b", "乙"))
        _write(str(src / "knowledge" / "x" / "c.md"), _doc("c", "丙"))
        g, r = self._export(src, self.base / "out")
        counts = g["meta"]["counts"]
        m = re.search(r"^- \*\*有边节点 (\d+) / (\d+) = ([\d.]+)%\*\*(.*)$", r, re.M)
        self.assertIsNotNone(m, "报告必须有「有边节点 N / M = P%%」行，实得报告：\n%s" % r)
        self.assertEqual(int(m.group(1)), 1,
                         "报告必须数到声明者 a（旧码=2，指的是 b/c——声明者 a 被「报告说它没边」）")
        self.assertEqual(int(m.group(1)), counts["with_edge"], "报告数字与 counts.with_edge 一致")
        self.assertEqual(int(m.group(2)), counts["nodes"], "报告分母与 counts.nodes 一致")
        self.assertEqual(float(m.group(3)), round(100.0 * counts["with_edge"] / max(counts["nodes"], 1), 1),
                         "报告百分比与 100*with_edge/节点数 复算一致")
        self.assertEqual(counts["density_pct"], round(100.0 * counts["with_edge"] / max(counts["nodes"], 1), 2),
                         "density_pct 与 with_edge 同口径复算一致（旧夹具改前 66.67/100.0 为两端口径）")
        note = m.group(4)
        self.assertIn("口径", note, "报告行必须注明口径（旧码两套判据不注明 ⇒ 同产物不可对账）")
        self.assertIn("n_edges>0", note, "口径注明必须指向 n_edges>0 的声明判据，实得：%r" % note)


class NoOverfixTests(_Base):
    """G3：防过修——#534/#605 既有（边数/声明归属）口径零回归。"""

    def test_g3_534_mapping_external_s_attributed_to_declarer(self):
        # 与 #605 G4a 同构夹具：list ×2（a 声明）＋ mapping s=c（c 声明）＋
        # mapping 外源 s=a（d 声明——计数归 d，#534 钉住的口径）。n_edges 表必须
        # 与 #605 G4a 逐值相同（a=2/b=0/c=1/d=1）；with_edge 按声明口径数
        # {a,c,d} = 3（b 被两条边指向却不声明，不计）。
        src = self.base / "src"
        _write(str(src / "knowledge" / "x" / "a.md"),
               _doc("a", "甲", extra="edges:\n  - {s: a, t: b, type: causal}\n  - {t: c, type: untyped}\n"))
        _write(str(src / "knowledge" / "x" / "b.md"), _doc("b", "乙"))
        _write(str(src / "knowledge" / "x" / "c.md"), _doc("c", "丙", extra="edges:\n  c: a\n"))
        _write(str(src / "knowledge" / "x" / "d.md"), _doc("d", "丁", extra="edges:\n  a: b\n"))
        g, _ = self._export(src, self.base / "out")
        counts = g["meta"]["counts"]
        self.assertEqual(counts["edges"], 4, "4 条存活边")
        self.assertEqual(counts["edges"], sum(g["meta"]["edge_types"].values()),
                         "#605 三口径：counts.edges == sum(edge_types)")
        n_by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(n_by_id["a"]["n_edges"], 2, "a 声明 2 条（list ×2），不得因作为外源 s 被重归属")
        self.assertEqual(n_by_id["b"]["n_edges"], 0, "b 未声明边")
        self.assertEqual(n_by_id["c"]["n_edges"], 1, "c 声明 1 条（mapping s=c）")
        self.assertEqual(n_by_id["d"]["n_edges"], 1,
                         "mapping 外源 s=a 的边计数仍归声明节点 d（#534 既有口径）")
        self.assertEqual(counts["edges"], sum(n["n_edges"] for n in g["nodes"]),
                         "#605 三口径：counts.edges == sum(n_edges)")
        self.assertEqual(counts["with_edge"], 3, "声明口径有边集合 {a,c,d}（b 被指向/被派生不声明，不计）")
        self.assertEqual(counts["declared_edge_nodes"], 3)

    def test_g3_derived_from_declarer_counted(self):
        # #605 G4b 同构夹具：b 声明 derived_from: a。derived_from 也是声明边
        # （edge_owner 登记 b）⇒ with_edge 数 b（=1），被派生源 a（n_edges=0，
        # 旧两端口径会因 a 是端点而计入=2）不计——声明口径对显式/派生边一视同仁。
        src = self.base / "src"
        _write(str(src / "knowledge" / "x" / "a.md"), _doc("a", "甲"))
        _write(str(src / "knowledge" / "x" / "b.md"), _doc("b", "乙", extra="derived_from: a\n"))
        g, _ = self._export(src, self.base / "out")
        counts = g["meta"]["counts"]
        self.assertEqual(counts["edges"], 1)
        self.assertEqual(g["meta"]["edge_types"], {"derived_from": 1})
        n_by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(n_by_id["b"]["n_edges"], 1, "声明节点计数不变（#605 G4b 既有读数）")
        self.assertEqual(n_by_id["a"]["n_edges"], 0)
        self.assertEqual(counts["with_edge"], 1, "with_edge 数声明者 b，不数端点 a")
        self.assertEqual(counts["declared_edge_nodes"], 1)
        self.assertEqual(counts["edges"], sum(n["n_edges"] for n in g["nodes"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
