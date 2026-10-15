#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coggraph 守卫 · #605 改名回写 id_seen ＋ 边计数统一到悬空剔除后。

每件钉「缺陷不再存在」，不是「代码能跑」：

G1（#605 ① 主体）三份**同 id 同内容**文件 ⇒ 产物节点 id 全唯一（len==set==3）、
   meta.duplicate_ids 的 renamed_to 两两互异（旧码：`<id>#<sha>` 纯内容派生，
   同内容 ⇒ 第 2/3 份算出同一 new_nid，改名不回写 id_seen ⇒ 图里两条同 id 节点，
   len=3 而 set=2，renamed_to 完全相同无法分辨）。附：重跑 id 序列稳定。
G2（防过修）两份同 id **不同内容** ⇒ 仍各自改名、id 唯一、后到者改名恰为
   `<id>#<该文件 sha 前 12 位>`（#358/#360 既有行为零回归——序号后缀只在
   内容派生改名再撞车时才出现）。
G3（#605 ② 主体）悬空边 ⇒ 三口径一致：meta.counts.edges == sum(edge_types)
   == sum(各节点 n_edges)；edge_report.md 不再同时写「显式边 0」与
   「causal: 1」——报告文本的每条边类型行与 json 计数逐条相等，显式边数
   行与 counts.edges 相等（旧码：counts.edges=0 而 sum=1、n_edges=1）。
   含全悬空（口径全 0）与 1 悬空+1 存活（口径全 1）两个夹具。
G4（防过修）无悬空边 ⇒ 各计数与改前同构夹具一致。两件：
   G4a 纯 frontmatter `edges` 夹具（list ＋ mapping ＋ mapping 外源 s）⇒
       counts.edges、edge_types、逐节点 n_edges 与改前逐值相同（含 mapping
       外源 s 计数归声明节点的既有口径，见 #534 守卫）——变异「改前版」上必须绿。
   G4b derived_from 边计入 edge_types：改前 edge_types 只在 `edges` 循环里累加、
       漏掉 derived_from ⇒ 零悬空时 sum(edge_types)=0 而 counts.edges=1（#605②
       「两套口径」同族）；修复后按「剔除后的 edges 上统计」覆盖 derived_from。
       （改前版上本件红＝既有矛盾本身，非过修。）

与既有守卫的断言面：#358（A 组）只测两份不同内容；本件 G1 测三份同内容的
再撞车面。#534 守卫测 mapping 形态不被丢弃；本件 G3/G4 测计数时序口径。
变异自查（人工两向，见 issue）：回退①回写 ⇒ G1 红；回退②时序 ⇒ G3 红。

运行：
  · pytest：python -X utf8 -m pytest tests/test_issue605_export_idseen_count_guard.py -q --no-header
  · 脚本式：python -X utf8 tests/test_issue605_export_idseen_count_guard.py
全部夹具写在 tempfile，绝不读写真源。
"""
from __future__ import annotations

import hashlib
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
        return g, r, p

    def _report_edge_type_lines(self, report):
        """取「## 边类型」小节内的 `- k: v` 行（避开层分布等同形行）。"""
        m = re.search(r"^## 边类型\s*$([\s\S]*?)(?=^## |\Z)", report, re.M)
        self.assertIsNotNone(m, "报告必须有「## 边类型」小节")
        return dict(re.findall(r"^- (\S+): (\d+)$", m.group(1), re.M))


class SameIdSameContentTests(_Base):
    """G1/G2：#605 ① —— 改名回写 id_seen。"""

    def test_g1_three_same_id_same_content_all_unique(self):
        src = self.base / "src"
        body = _doc("dup", "重复内容")
        for d in ("aaa", "bbb", "ccc"):
            _write(str(src / "knowledge" / d / "n.md"), body)
        # 夹具不变量：三份内容逐字相同（否则「内容派生改名再撞车」不成立，G1 恒真）
        shas = {hashlib.sha256((src / "knowledge" / d / "n.md").read_bytes()).hexdigest()[:12]
                for d in ("aaa", "bbb", "ccc")}
        self.assertEqual(len(shas), 1, "夹具不变量：三份文件内容必须相同：%r" % shas)

        g, _, p = self._export(src, self.base / "out")
        ids = [n["id"] for n in g["nodes"]]
        self.assertEqual(len(ids), 3, "三份文件 ⇒ 三条节点：%r" % ids)
        self.assertEqual(len(set(ids)), 3,
                         "节点 id 必须两两唯一（#605：同 id 同内容的第 2/3 份算出同一 "
                         "`<id>#<sha>` 且改名不回写 id_seen ⇒ len=3 而 set=2）：%r" % ids)
        self.assertEqual(g["meta"]["counts"]["nodes"], 3)

        dup = g["meta"]["duplicate_ids"]
        self.assertEqual(len(dup), 2, "两条冲突登记：%r" % dup)
        renamed = [d["renamed_to"] for d in dup]
        self.assertEqual(len(set(renamed)), len(renamed),
                         "renamed_to 必须两两互异（旧码两条完全相同无法分辨）：%r" % renamed)
        self.assertIn("dup#%s" % shas.pop(), renamed,
                      "首个改名仍是逐内容定值的 <id>#<sha>：%r" % renamed)
        self.assertIn("重复", p.stderr, "重复 id 仍须 stderr 告警（#358 既有行为）")

        # 重跑稳定（序号兜底确定性：dirnames.sort + sorted(filenames，#360））
        g2, _, _ = self._export(src, self.base / "out2")
        self.assertEqual([n["id"] for n in g2["nodes"]], ids, "同一真源重跑必须得到同一 id 序列")

    def test_g2_two_same_id_different_content_no_regression(self):
        src = self.base / "src"
        _write(str(src / "knowledge" / "aaa" / "note.md"), _doc("note-1", "笔记甲"))
        _write(str(src / "knowledge" / "bbb" / "note.md"), _doc("note-1", "笔记乙"))
        g, _, _ = self._export(src, self.base / "out")
        ids = [n["id"] for n in g["nodes"]]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2, "不同内容 ⇒ 各自改名后 id 仍唯一：%r" % ids)
        bbb_sha = hashlib.sha256((src / "knowledge" / "bbb" / "note.md").read_bytes()).hexdigest()[:12]
        self.assertIn("note-1#%s" % bbb_sha, ids,
                      "不同内容的后到者改名恰为 <id>#<sha>（#358 既有公式，不得被序号后缀污染）：%r" % ids)
        dup = g["meta"]["duplicate_ids"]
        self.assertEqual(len(dup), 1, "恰一条冲突登记：%r" % dup)


class DanglingEdgeCountParityTests(_Base):
    """G3/G4：#605 ② —— 边计数统一到悬空剔除之后。"""

    def _assert_parity(self, g, report):
        counts_edges = g["meta"]["counts"]["edges"]
        sum_types = sum(g["meta"]["edge_types"].values())
        sum_n_edges = sum(n["n_edges"] for n in g["nodes"])
        self.assertEqual(counts_edges, sum_types,
                         "counts.edges 必须等于 sum(edge_types)（旧码剔除前/后两套口径）：%r vs %r"
                         % (counts_edges, g["meta"]["edge_types"]))
        self.assertEqual(counts_edges, sum_n_edges,
                         "counts.edges 必须等于 sum(各节点 n_edges)：%r vs %r" % (counts_edges, sum_n_edges))
        # 报告文本与 json 一致：显式边行 == counts.edges；边类型行逐条 == edge_types
        m = re.search(r"^- 显式边 (\d+)（悬空已剔除 (\d+)）$", report, re.M)
        self.assertIsNotNone(m, "报告必须有「显式边 N（悬空已剔除 M）」行")
        self.assertEqual(int(m.group(1)), counts_edges,
                         "报告显式边数必须与 counts.edges 一致（旧码同时写 0 与 causal: 1）")
        # 注：悬空数（group(2)）无法从 graph.json 复原（剔除边不落盘），只断言在场。
        reported = self._report_edge_type_lines(report)
        want = {k: str(v) for k, v in g["meta"]["edge_types"].items()}
        self.assertEqual(reported, want,
                         "报告边类型小节必须与 meta.edge_types 逐条相等（旧码报告写剔除前的 causal: 1）")
        # 反向钉：counts.edges==0 时不得出现任何非零边类型行（题面自相矛盾读数的直接否定）
        if counts_edges == 0:
            self.assertEqual(want, {}, "counts.edges==0 ⇒ edge_types 必须为空")

    def test_g3_all_dangling_single_node(self):
        src = self.base / "src"
        _write(str(src / "knowledge" / "only" / "a.md"),
               _doc("a", "单点", extra="edges:\n  - {s: a, t: ghost, type: causal}\n"))
        g, r, _ = self._export(src, self.base / "out")
        self.assertEqual(g["meta"]["counts"]["edges"], 0, "悬空边已剔除：存活 0")
        self.assertEqual(g["nodes"][0]["n_edges"], 0,
                         "悬空边不得计入 n_edges（旧码=1，与 counts.edges=0 并存）")
        self._assert_parity(g, r)
        self.assertIn("- 显式边 0（悬空已剔除 1）", r)
        self.assertNotIn("causal", r, "报告不得再写已剔除边的类型行（旧码同报告 0 与 1 并存）")

    def test_g3_mixed_one_dangling_one_alive(self):
        src = self.base / "src"
        _write(str(src / "knowledge" / "x" / "a.md"),
               _doc("a", "甲", extra="edges:\n  - {s: a, t: b, type: relates_to}\n  - {s: a, t: ghost, type: causal}\n"))
        _write(str(src / "knowledge" / "x" / "b.md"), _doc("b", "乙"))
        g, r, _ = self._export(src, self.base / "out")
        self.assertEqual(g["meta"]["counts"]["edges"], 1)
        self.assertEqual(g["meta"]["edge_types"], {"relates_to": 1},
                         "悬空边类型不得计入 edge_types（旧码 causal:1 与 relates_to:1 并存）")
        n_by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(n_by_id["a"]["n_edges"], 1,
                         "n_edges 只计存活边（旧码=2，含悬空的 causal）")
        self._assert_parity(g, r)

    def test_g4a_no_dangling_frontmatter_counts_unchanged(self):
        src = self.base / "src"
        # 无悬空纯 frontmatter 夹具：显式 list 边 ×2（a 声明）＋ mapping 形态
        # （c 声明 s=c）＋ mapping 外源 s（d 声明 s=a——计数归声明节点 d，#534
        # 守卫钉住的口径）。改前（#605 之前）同夹具读数即下列期望值（变异验证：
        # 改前版跑本件亦绿）。
        _write(str(src / "knowledge" / "x" / "a.md"),
               _doc("a", "甲", extra="edges:\n  - {s: a, t: b, type: causal}\n  - {t: c, type: untyped}\n"))
        _write(str(src / "knowledge" / "x" / "b.md"), _doc("b", "乙"))
        _write(str(src / "knowledge" / "x" / "c.md"), _doc("c", "丙", extra="edges:\n  c: a\n"))
        _write(str(src / "knowledge" / "x" / "d.md"), _doc("d", "丁", extra="edges:\n  a: b\n"))
        g, r, _ = self._export(src, self.base / "out")
        self.assertEqual(g["meta"]["counts"]["edges"], 4, "4 份文件 4 条存活边")
        self.assertEqual(g["meta"]["edge_types"], {"causal": 1, "untyped": 3},
                         "无悬空 ⇒ 边类型计数与改前同构读数逐值一致")
        n_by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(n_by_id["a"]["n_edges"], 2, "a 声明 2 条（list ×2）")
        self.assertEqual(n_by_id["b"]["n_edges"], 0, "b 未声明边")
        self.assertEqual(n_by_id["c"]["n_edges"], 1, "c 声明 1 条（mapping s=c）")
        self.assertEqual(n_by_id["d"]["n_edges"], 1,
                         "mapping 外源 s=a 的边计数归声明节点 d（#534 既有口径，不得被重归属）")
        self._assert_parity(g, r)

    def test_g4b_derived_from_edges_counted_in_parity(self):
        # 改前 edge_types 只在 `edges` 循环累加、漏掉 derived_from ⇒ 零悬空时
        # sum(edge_types)=0 而 counts.edges=1（#605②「两套口径」同族）。修复后
        # 「在剔除后的 edges 上统计」⇒ derived_from 与其他存活边同面计数。
        src = self.base / "src"
        _write(str(src / "knowledge" / "x" / "a.md"), _doc("a", "甲"))
        _write(str(src / "knowledge" / "x" / "b.md"), _doc("b", "乙", extra="derived_from: a\n"))
        g, r, _ = self._export(src, self.base / "out")
        self.assertEqual(g["meta"]["counts"]["edges"], 1)
        self.assertEqual(g["meta"]["edge_types"], {"derived_from": 1},
                         "derived_from 存活边必须计入 edge_types（改前漏计 ⇒ 与 counts.edges 两套口径）")
        n_by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(n_by_id["b"]["n_edges"], 1, "声明节点计数不变（改前即 1）")
        self._assert_parity(g, r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
