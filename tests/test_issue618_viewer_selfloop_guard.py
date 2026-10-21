#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#618 守卫 —— build_viewer 折叠损耗（自环丢弃 / 同键合并）必须可见，不得静默丢边。

缺陷形态（修复前实测）——3 条输入边 + 同义表把 m_short 折到 m_long（--merge-synonyms）：
  折叠后 [(m_long,m_fin), (m_long,m_fin), (m_long,m_long)]
  ⇒ L1 自环丢弃 1 条（:317 continue）、L2 同键合并 1 条（:319-322 seen_edge 撞键）
  ⇒ viewer_graph.json 只剩 1 条边；stdout 全文零提示、json 无任何 dropped/计数字段，
  而 graph.json/edge_report/meta 仍说 3 条边 ⇒ 三处读数打架且无从对账。

修复形态——损耗**计数可见**，丢弃语义本身不变：
  (a) stdout 汇总行「折叠损耗：自环丢弃 N 条 / 同键合并 M 条」（仅在 N+M>0 时打）；
  (b) viewer_graph.json 顶层新字段 dropped_selfloop / merged_edges（整数，无损耗时也写 0）。
  L3 别名节点剔除是 #410 的刻意设计，不属损耗、不告警（见 G3 钉零回归）。
  注意：自源 graph.json 本身可自带 a→a（export 侧两条路径均无 s==t 防护），
  故默认零开关路径同样必须计数可见（G1b）。

本件为 pytest 式（CLI 子进程 + 临时文件构造，参照 test_coggraph_viewer_security_guard.py
的同款手法），由 `pytest tests/` 自动收集，无需列入 gate.yml 的 SCRIPT_TESTS。

分组：
  G1 损耗可见——告警行计数正确、json 字段正确、输出边数 + 两个计数 == 输入边数（自洽）；
  G2 防过修——无损耗输入零告警、计数字段为 0、边数/节点数/产物结构零回归；
  G3 #410 零回归——显式 --merge-synonyms 下别名节点仍被重映射（merged_aliases 照旧）。

变异验证（两向读数必须记录）：
  把计数/告警/字段回退（去掉告警行与字段写入）⇒ G1 必红；恢复 ⇒ 全绿。
  G2/G3 保证回退修复不牵连既有行为。

运行：python -X utf8 -m pytest tests/test_issue618_viewer_selfloop_guard.py -q
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_VIEWER = os.path.join(HERE, "tools", "coggraph", "build_viewer.py")

# 修复前 viewer_graph.json 的顶层键集（#618 之前的历史口径，G2 用作零回归基线）
LEGACY_VGRAPH_KEYS = {"meta", "nodes", "edges", "layer_count",
                      "edge_type_count", "derived", "merged_aliases"}


def _node(nid, title):
    return {"id": nid, "title": title, "layer": "knowledge", "bucket": "kb",
            "tags": [], "importance": 0, "path": nid + ".md"}


def _write_json(path, doc):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False)


def _stub_cyto(out):
    """离线 cytoscape 桩（>100000 字节 + 用 LINGSHU_CYTO_SHA256 声明其指纹）。"""
    os.makedirs(out, exist_ok=True)
    content = "// offline stub\n" + "x" * 100001
    with io.open(os.path.join(out, "cytoscape.min.js"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(content)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _run_viewer(graph, extra=()):
    """CLI 子进程模式跑 build_viewer；返回 (CompletedProcess, viewer_graph dict, out_dir)。"""
    tmp = tempfile.TemporaryDirectory(prefix="i618_")
    base = tmp.name
    out = os.path.join(base, "out")
    sha = _stub_cyto(out)
    # #620：桩为非官方副本 ⇒ 自备轨须经显式开关（夹具语义不变）
    cmd = [sys.executable, "-X", "utf8", BUILD_VIEWER,
           "--graph", graph, "--out", out] + [str(a) for a in extra] + \
          ["--allow-unofficial-cyto"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LINGSHU_CYTO_SHA256=sha)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       cwd=HERE, env=env)
    vg = None
    vg_path = os.path.join(out, "viewer_graph.json")
    if os.path.isfile(vg_path):
        with io.open(vg_path, encoding="utf-8") as f:
            vg = json.load(f)
    return p, vg, out, tmp


# ============================================================ G1 · 损耗可见

class FoldLossVisibilityTests(unittest.TestCase):
    """#618 主体：自环丢弃 / 同键合并必须计数可见，且读数自洽。"""

    def _g1_fixture(self):
        """3 节点 + 3 输入边 + 同义表（m_short → m_long），折叠后必得 1 自环 + 1 撞键。

        折叠后边序列（按输入顺序）：
          (m_long,m_fin) 保留；(m_short,m_fin)→(m_long,m_fin) 撞键；(m_long,m_short)→自环。
        """
        tmp = tempfile.TemporaryDirectory(prefix="i618g1_")
        self.addCleanup(tmp.cleanup)
        base = tmp.name
        graph = os.path.join(base, "graph.json")
        edges = [
            {"s": "m_long", "t": "m_fin", "type": "explicit"},
            {"s": "m_short", "t": "m_fin", "type": "explicit"},
            {"s": "m_long", "t": "m_short", "type": "explicit"},
        ]
        _write_json(graph, {"meta": {"counts": {"nodes": 3, "edges": 3},
                                     "source_sha": "test"},
                            "nodes": [_node("m_long", "多头长期"),
                                      _node("m_short", "空头短期"),
                                      _node("m_fin", "融资节点")],
                            "edges": edges})
        syn = os.path.join(base, "synonym_groups.json")
        _write_json(syn, {"groups": [{"canonical_id": "m_long",
                                      "members": [{"id": "m_long"}, {"id": "m_short"}],
                                      "kind": "synonym", "rule": "L1"}]})
        return graph, syn, 3

    def _g1_run(self):
        graph, syn, n_in = self._g1_fixture()
        p, vg, out, _ = _run_viewer(graph, ["--synonyms", syn, "--merge-synonyms"])
        return p, vg, out, n_in

    def test_g1_rc0_and_warning_counts_in_stdout(self):
        """rc=0；stdout 必须含「折叠损耗」行，且「自环丢弃 1 条」「同键合并 1 条」。"""
        p, vg, out, n_in = self._g1_run()
        self.assertEqual(p.returncode, 0, (p.stdout[-300:], p.stderr[-300:]))
        self.assertIn("折叠损耗", p.stdout, "stdout 缺折叠损耗汇总行（#618 缺陷形态：静默丢边）")
        m1 = re.search(r"自环丢弃\s*(\d+)\s*条", p.stdout)
        m2 = re.search(r"同键合并\s*(\d+)\s*条", p.stdout)
        self.assertIsNotNone(m1, "stdout 缺「自环丢弃 N 条」计数：%r" % p.stdout)
        self.assertIsNotNone(m2, "stdout 缺「同键合并 N 条」计数：%r" % p.stdout)
        self.assertEqual(int(m1.group(1)), 1,
                         "自环丢弃计数应为 1（夹具：m_long→m_short 折叠成自环），stdout=%r" % p.stdout)
        self.assertEqual(int(m2.group(1)), 1,
                         "同键合并计数应为 1（夹具：m_short→m_fin 折叠后撞键），stdout=%r" % p.stdout)

    def test_g1_json_fields_and_self_consistency(self):
        """json：dropped_selfloop==1、merged_edges>=1；输出边数 + 两个计数 == 输入边数。"""
        p, vg, out, n_in = self._g1_run()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("dropped_selfloop", vg, "viewer_graph.json 缺 dropped_selfloop 字段")
        self.assertIn("merged_edges", vg, "viewer_graph.json 缺 merged_edges 字段")
        self.assertEqual(vg["dropped_selfloop"], 1,
                         "dropped_selfloop 应为 1，实得 %r" % vg.get("dropped_selfloop"))
        self.assertGreaterEqual(vg["merged_edges"], 1,
                                "merged_edges 应 >=1，实得 %r" % vg.get("merged_edges"))
        n_out = len(vg["edges"])
        total = n_out + vg["dropped_selfloop"] + vg["merged_edges"]
        self.assertEqual(total, n_in,
                         "读数不自洽：输出 %d + 丢弃 %r + 合并 %r != 输入 %d"
                         % (n_out, vg["dropped_selfloop"], vg["merged_edges"], n_in))
        # 夹具不变量：3 条输入折叠后确实只剩 1 条（1 丢 + 1 并）——否则本组无判别力
        self.assertEqual(n_out, 1, "夹具折叠后应只剩 1 条边，实得 %r" % vg["edges"])

    def test_g1b_default_flags_raw_selfloop_from_true_source(self):
        """默认零开关（无 --synonyms/--merge-synonyms）下，真源自带 a→a 也必须计数可见。

        export_coggraph 两条路径均无 s==t 防护 ⇒ graph.json 可携带原生自环；
        旧实现在该路径同样静默丢弃（issue 标题即此场景）。
        """
        tmp = tempfile.TemporaryDirectory(prefix="i618g1b_")
        self.addCleanup(tmp.cleanup)
        graph = os.path.join(tmp.name, "graph.json")
        _write_json(graph, {"meta": {"counts": {"nodes": 2, "edges": 2},
                                     "source_sha": "test"},
                            "nodes": [_node("a", "甲"), _node("b", "乙")],
                            "edges": [{"s": "a", "t": "b", "type": "explicit"},
                                      {"s": "a", "t": "a", "type": "explicit"}]})
        p, vg, out, _ = _run_viewer(graph)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(vg["edges"]), 1, "原生自环应仍被丢弃（语义不变），实得 %r" % vg["edges"])
        self.assertEqual(vg.get("dropped_selfloop"), 1,
                         "默认路径的自环丢弃也必须计数（实得 %r）" % vg.get("dropped_selfloop"))
        self.assertEqual(vg.get("merged_edges"), 0)
        self.assertIn("自环丢弃", p.stdout, "默认路径的损耗告警行不得缺席")


# ============================================================ G2 · 防过修（无损耗静默）

class NoLossSilenceTests(unittest.TestCase):
    """无损耗输入：零告警、计数字段写 0、边数原样、既有产物结构零回归。"""

    def _g2_run(self):
        tmp = tempfile.TemporaryDirectory(prefix="i618g2_")
        self.addCleanup(tmp.cleanup)
        graph = os.path.join(tmp.name, "graph.json")
        edges = [
            {"s": "n1", "t": "n2", "type": "explicit"},
            {"s": "n2", "t": "n3", "type": "causal"},
            {"s": "n1", "t": "n3", "type": "part_of"},
        ]
        _write_json(graph, {"meta": {"counts": {"nodes": 3, "edges": 3},
                                     "source_sha": "test"},
                            "nodes": [_node("n1", "甲"), _node("n2", "乙"), _node("n3", "丙")],
                            "edges": edges})
        # 不带 --synonyms / --merge-synonyms（默认路径）
        return _run_viewer(graph)

    def test_g2_no_warning_and_zero_counts(self):
        p, vg, out, _ = self._g2_run()
        self.assertEqual(p.returncode, 0, p.stderr)
        for bad in ("折叠损耗", "自环丢弃", "同键合并"):
            self.assertNotIn(bad, p.stdout, "无损耗输入不该打告警（%s）：%r" % (bad, p.stdout))
        self.assertEqual(vg.get("dropped_selfloop"), 0,
                         "无损耗时 dropped_selfloop 应写 0（恒写口径），实得 %r" % vg.get("dropped_selfloop"))
        self.assertEqual(vg.get("merged_edges"), 0,
                         "无损耗时 merged_edges 应写 0（恒写口径），实得 %r" % vg.get("merged_edges"))

    def test_g2_edge_and_node_counts_unchanged(self):
        p, vg, out, _ = self._g2_run()
        self.assertEqual(len(vg["edges"]), 3, "无折叠损耗时边数必须原样保留（防过修：误伤正常边）")
        self.assertEqual(sorted(n["id"] for n in vg["nodes"]), ["n1", "n2", "n3"])

    def test_g2_existing_artifacts_zero_regression(self):
        """既有产物结构零回归：顶层旧键全在、三件产物齐、产物汇总行格式照旧。"""
        p, vg, out, _ = self._g2_run()
        self.assertEqual(p.returncode, 0, p.stderr)
        missing = LEGACY_VGRAPH_KEYS - set(vg.keys())
        self.assertEqual(missing, set(), "viewer_graph.json 顶层旧键缺失（回归）：%s" % missing)
        for fn in ("index.html", "viewer_graph.json", "serve.py", "cytoscape.min.js"):
            self.assertTrue(os.path.isfile(os.path.join(out, fn)), "产物缺 %s" % fn)
        self.assertRegex(p.stdout, r"viewer_graph\.json\(3 节点 3 边\)",
                         "产物汇总行的既有格式不得被本次修复改写：%r" % p.stdout)


# ============================================================ G3 · #410 零回归

class Issue410MergeBehaviorTests(unittest.TestCase):
    """#410 既有行为：显式 --merge-synonyms 才重映射别名节点，merged_aliases 照旧。"""

    def _run(self, extra=()):
        tmp = tempfile.TemporaryDirectory(prefix="i618g3_")
        self.addCleanup(tmp.cleanup)
        base = tmp.name
        graph = os.path.join(base, "graph.json")
        _write_json(graph, {"meta": {"counts": {"nodes": 2, "edges": 1},
                                     "source_sha": "test"},
                            "nodes": [_node("k1", "同义甲"), _node("k2", "同义乙")],
                            "edges": [{"s": "k1", "t": "k2", "type": "similar"}]})
        syn = os.path.join(base, "synonym_groups.json")
        _write_json(syn, {"groups": [{"canonical_id": "k1",
                                      "members": [{"id": "k1"}, {"id": "k2"}],
                                      "kind": "synonym", "rule": "L1"}]})
        p, vg, out, _ = _run_viewer(graph, ["--synonyms", syn] + list(extra))
        self.assertEqual(p.returncode, 0, (p.stdout[-300:], p.stderr[-300:]))
        return vg

    def test_g3_explicit_merge_still_remaps_alias_nodes(self):
        """显式 --merge-synonyms：别名 k2 仍被重映射掉、merged_aliases==1（#410 照旧）。"""
        vg = self._run(extra=["--merge-synonyms"])
        ids = sorted(n["id"] for n in vg["nodes"])
        self.assertEqual(ids, ["k1"], "显式开关下别名节点 k2 应被重映射掉（#410 行为不得被本次修复改变）")
        self.assertEqual(vg.get("merged_aliases"), 1)

    def test_g3_default_keeps_all_nodes(self):
        """默认（只给 --synonyms）：节点全保留、merged_aliases 为 0（#410 照旧）。"""
        vg = self._run()
        ids = sorted(n["id"] for n in vg["nodes"])
        self.assertEqual(ids, ["k1", "k2"], "默认模式不得并节点（#410）")
        self.assertFalse(vg.get("merged_aliases"))
        # k1→k2 在默认模式下既非自环也不撞键：计数必须为 0（不误告警）
        self.assertEqual(vg.get("dropped_selfloop"), 0)
        self.assertEqual(vg.get("merged_edges"), 0)

    def test_g3_merged_edge_case_counts_are_new_fields_only(self):
        """#410 归并引发的损耗只落新字段：merged_aliases 语义不变（节点数，非边数）。"""
        vg = self._run(extra=["--merge-synonyms"])
        # k1→k2 折叠成 k1→k1 自环 ⇒ 损耗走新字段；merged_aliases 仍只数节点（=1）
        self.assertEqual(vg.get("dropped_selfloop"), 1)
        self.assertEqual(vg.get("merged_aliases"), 1,
                         "merged_aliases 必须仍是节点计数口径，不得被边级合并污染")


if __name__ == "__main__":
    unittest.main()
