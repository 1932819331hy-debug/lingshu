#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#645 守卫 —— build_viewer 消费侧对 R5 同名 phantom 边（#359/#606 口径）必须过滤。
======================================================================================
缺陷形态（修复前实测，本机 Windows / py3.12，main @ 43ece08）——夹具：a1/a2/a3 同标题
「记忆写入规范手册」（8 字，恰过 --r5-min-title-len=8）＋外部提及者 s1/s2：

  derive_edges R5 产出 5 条：a2→a1、a3→a1（phantom：同名节点自己正文首行
  `# 功能名：<自身标题>` 命中组首标题的递归互引，#359 认定「同名声明，非概念互引」）
  ＋ s1→a1、s1→s2、s2→a1（真实外部互引）。

  审计侧 naming_report 在 Q1 deg（:74）与 r5_in（:95-97，#606）两处都按
  「提及者标题 == 被提及标题」过滤；build_viewer 全文无 R5/phantom 处理 ⇒
  5 条全收：viewer body_crossref 含 2 条 phantom，body_crossref 又在
  DEFAULT_EDGE_ON 默认勾选集合 ⇒ 被审计判「不存在」的边默认可见、计入 degree
  （改前读数：a1=7 / a2=4 / a3=5 / s1=4 / s2=5，a1 被 phantom 虚增 2、
  a2/a3 各虚增 1 出边且挂错误语义标签「正文互引」）。

修法（消费侧口径，derive_edges 产出侧不动——审计与视图共用真源）：
  build_viewer 折叠循环之前，从 graph.json 节点建 title_of（id→strip(title)），
  对 d_edges 中 rule=="R5_body_crossref" 且 s/t 标题相同的边剔除——与
  naming_report 完全同口径；损耗可见（#618 纪律）：stdout 折叠损耗行新增
  「同名 phantom N 条」一类，viewer_graph.json 顶层 phantom_r5_dropped 恒写（含 0）。

守卫判据（端到端跑真实 CLI：derive_edges --write → build_viewer）：
  G1 phantom 过滤——viewer 的 body_crossref 只剩外部 3 条（a2/a3 的 phantom 不在）；
     顶层 phantom_r5_dropped==2；stdout 损耗行含「同名 phantom 2 条」；
     degree 虚增被剔（a1=5/a2=3/a3=4）；derived_edges.json 真源侧 R5 仍 5 条（含 2 phantom）。
  G2 真实外部互引零回归——s1→a1 / s1→s2 / s2→a1 照在，s1/s2 degree 不受牵连（4/5）。
  G3（防过修）非 R5 边不受影响——explicit/same_bucket/in_bucket 逐类计数不变；
     无 phantom 输入（标题互异）⇒ phantom_r5_dropped==0 且 stdout 无损耗行。

变异验证（两向读数已记录）：
  回退过滤（phantom 边照收）⇒ G1 的 body_crossref/计数字段/degree 断言与
  G2 的 a1 degree 断言红；恢复 ⇒ 全绿。

运行：python -X utf8 -m pytest tests/test_issue645_viewer_r5_phantom_guard.py -q
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
DERIVE = os.path.join(HERE, "tools", "coggraph", "derive_edges.py")
VIEWER = os.path.join(HERE, "tools", "coggraph", "build_viewer.py")

# 夹具标题（≥8 字，避 --r5-min-title-len 默认 8 的长度闸）
T_A = "记忆写入规范手册"      # a1/a2/a3 同名组共用
T_S1 = "外部审计流程说明"
T_S2 = "外部复核制度文档"


def _node(nid, title, bucket):
    return {"id": nid, "layer": "knowledge", "title": title, "bucket": bucket,
            "tags": [], "importance": 0, "created": 0, "sha": nid,
            "ref_root": "", "ref_dir": "", "n_edges": 0, "path": nid + ".md"}


def _write_json(path, doc):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False)


def _fixture(tmp, same_title_group=True):
    """真源夹具：同名三节点（可选）＋两外部提及者；返回 (graph, bodies_root)。

    R5 产出（同名组在时）：a2→a1、a3→a1（phantom）＋ s1→a1、s1→s2、s2→a1（真实）。
    显式边 s2→a3 与 R1 同桶边混入，供 G3 钉「非 R5 边不受影响」。
    """
    base = tmp
    bodies = os.path.join(base, "bodies")
    os.makedirs(bodies, exist_ok=True)
    docs = {
        "a1.md": "# 功能名：%s\n\n组首本体，记录记忆写入的规范流程。\n" % T_A,
        "a2.md": "# 功能名：%s\n\n同名副本，正文首行即标题行。\n" % T_A,
        "a3.md": "# 功能名：%s\n\n第三个同名副本，标题行同样命中组首标题。\n" % T_A,
        "s1.md": "# 功能名：%s\n\n审计流程参见 %s 的约定执行，复核走 %s。\n" % (T_S1, T_A, T_S2),
        "s2.md": "# 功能名：%s\n\n复核制度引用 %s 的写入规范。\n" % (T_S2, T_A),
    }
    if not same_title_group:   # G3 无 phantom 输入：同名组标题互异 ⇒ R5 无 phantom
        docs["a2.md"] = "# 功能名：%s\n\n互异标题副本。\n" % "镜像校验操作手册"
        docs["a3.md"] = "# 功能名：%s\n\n互异标题副本。\n" % "索引重建操作手册"
    for fn, txt in docs.items():
        with io.open(os.path.join(bodies, fn), "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
    graph = os.path.join(base, "graph.json")
    t2 = "镜像校验操作手册" if not same_title_group else T_A
    t3 = "索引重建操作手册" if not same_title_group else T_A
    _write_json(graph, {"meta": {"counts": {"nodes": 5, "edges": 1}, "source_sha": "i645"},
                        "nodes": [_node("a1", T_A, "mem"), _node("a2", t2, "mem"),
                                  _node("a3", t3, "mem"), _node("s1", T_S1, "audit"),
                                  _node("s2", T_S2, "audit")],
                        "edges": [{"s": "s2", "t": "a3", "type": "explicit",
                                   "derived": False}]})
    return graph, bodies


def _stub_cyto(out):
    """离线 cytoscape 桩（>100000 字节 + LINGSHU_CYTO_SHA256 声明其指纹，#620 自备轨）。"""
    os.makedirs(out, exist_ok=True)
    content = "// offline stub\n" + "x" * 100001
    with io.open(os.path.join(out, "cytoscape.min.js"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(content)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _run_chain(same_title_group=True):
    """端到端：夹具 → derive_edges --write → build_viewer。

    返回 (p_viewer, derived_doc, vg, tmp)；derived_doc 可能 None（derive 失败时）。
    """
    tmp = tempfile.TemporaryDirectory(prefix="i645_")
    graph, bodies = _fixture(tmp.name, same_title_group=same_title_group)
    dout = os.path.join(tmp.name, "derived")
    p1 = subprocess.run(
        [sys.executable, "-X", "utf8", DERIVE, "--graph", graph, "--out", dout,
         "--bodies-root", bodies, "--write"],
        capture_output=True, text=True, encoding="utf-8", cwd=HERE, timeout=120)
    derived_doc = None
    dpath = os.path.join(dout, "derived_edges.json")
    if os.path.isfile(dpath):
        with io.open(dpath, encoding="utf-8") as f:
            derived_doc = json.load(f)
    vout = os.path.join(tmp.name, "viewer")
    sha = _stub_cyto(vout)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LINGSHU_CYTO_SHA256=sha)
    p2 = subprocess.run(
        [sys.executable, "-X", "utf8", VIEWER, "--graph", graph,
         "--derived", dpath, "--out", vout, "--allow-unofficial-cyto"],
        capture_output=True, text=True, encoding="utf-8", cwd=HERE, env=env,
        timeout=120)
    vg = None
    vg_path = os.path.join(vout, "viewer_graph.json")
    if os.path.isfile(vg_path):
        with io.open(vg_path, encoding="utf-8") as f:
            vg = json.load(f)
    return p1, p2, derived_doc, vg, tmp


def _pairs(doc, etype):
    return {(e["s"], e["t"]) for e in doc["edges"] if e.get("type") == etype}


def _deg(vg, nid):
    return {n["id"]: n["degree"] for n in vg["nodes"]}[nid]


# ============================================================ G1 · phantom 过滤

class PhantomFilterTests(unittest.TestCase):
    """#645 主体：同名 phantom 必须在消费侧被滤掉，且损耗计数可见、真源不动。"""

    def setUp(self):
        p1, p2, derived_doc, vg, tmp = _run_chain()
        self.addCleanup(tmp.cleanup)
        self.p1, self.p2 = p1, p2
        self.derived, self.vg = derived_doc, vg

    def test_g1_rc0_and_true_source_keeps_phantom(self):
        """真源不动：derive/build 两轮 rc=0；derived_edges.json 的 R5 仍 5 条含 2 phantom。"""
        self.assertEqual(self.p1.returncode, 0, self.p1.stderr[-300:])
        self.assertEqual(self.p2.returncode, 0, (self.p2.stdout[-300:], self.p2.stderr[-300:]))
        r5 = _pairs(self.derived, "body_crossref")
        self.assertEqual(len([e for e in self.derived["edges"]
                              if e.get("rule") == "R5_body_crossref"]), 5,
                         "夹具判别力：R5 应产出 5 条（2 phantom + 3 真实外部）")
        self.assertIn(("a2", "a1"), r5)
        self.assertIn(("a3", "a1"), r5, "phantom a3→a1 应仍在真源（过滤是消费侧口径）")
        self.assertIn(("s1", "a1"), r5)

    def test_g1_viewer_body_crossref_only_external(self):
        """viewer 的 body_crossref 只剩外部 3 条；a2/a3 的 phantom 不在。"""
        bc = _pairs(self.vg, "body_crossref")
        self.assertEqual(bc, {("s1", "a1"), ("s1", "s2"), ("s2", "a1")},
                         "viewer 应只剩真实外部互引，实得 %r" % bc)
        self.assertNotIn(("a2", "a1"), bc, "phantom a2→a1 不得画成「正文互引」（#645）")
        self.assertNotIn(("a3", "a1"), bc, "phantom a3→a1 不得画成「正文互引」（#645）")

    def test_g1_count_field_and_stdout_line(self):
        """顶层 phantom_r5_dropped==2；stdout 折叠损耗行含「同名 phantom 2 条」。"""
        self.assertIn("phantom_r5_dropped", self.vg, "viewer_graph.json 缺 phantom_r5_dropped 字段")
        self.assertEqual(self.vg["phantom_r5_dropped"], 2,
                         "phantom_r5_dropped 应为 2，实得 %r" % self.vg.get("phantom_r5_dropped"))
        m = re.search(r"同名\s*phantom\s*(\d+)\s*条", self.p2.stdout)
        self.assertIsNotNone(m, "stdout 损耗行缺「同名 phantom N 条」：%r" % self.p2.stdout)
        self.assertEqual(int(m.group(1)), 2, "stdout 应告警 2 条 phantom：%r" % self.p2.stdout)

    def test_g1_degree_inflation_removed(self):
        """degree 虚增被剔：a1=5 / a2=3 / a3=4（改前 7/4/5，各含 phantom 虚增）。"""
        self.assertEqual(_deg(self.vg, "a1"), 5,
                         "a1 degree 应剔掉 2 条 phantom 入边（改前 7）")
        self.assertEqual(_deg(self.vg, "a2"), 3, "a2 degree 应剔掉 1 条 phantom 出边（改前 4）")
        self.assertEqual(_deg(self.vg, "a3"), 4, "a3 degree 应剔掉 1 条 phantom 出边（改前 5）")


# ============================================================ G2 · 真实互引零回归

class RealCrossrefZeroRegressionTests(unittest.TestCase):

    def setUp(self):
        p1, p2, derived_doc, vg, tmp = _run_chain()
        self.addCleanup(tmp.cleanup)
        self.p2, self.vg = p2, vg

    def test_g2_real_external_edges_survive(self):
        """s1→a1 / s1→s2 / s2→a1 照在（提及者标题 ≠ 被提及标题，非 phantom）。"""
        bc = _pairs(self.vg, "body_crossref")
        for pair in (("s1", "a1"), ("s1", "s2"), ("s2", "a1")):
            self.assertIn(pair, bc, "真实外部互引 %s 不得被误滤（防过修）" % (pair,))

    def test_g2_external_degrees_untouched(self):
        """s1=4 / s2=5 与改前逐位相同（外部提及者的度不受 phantom 过滤牵连）。"""
        self.assertEqual(_deg(self.vg, "s1"), 4, _deg(self.vg, "s1"))
        self.assertEqual(_deg(self.vg, "s2"), 5, _deg(self.vg, "s2"))

    def test_g2_a1_crossref_inneighbors_exactly_external(self):
        """a1 的 body_crossref 入邻居恰为 {s1,s2}（degree 相应：只数真实外部提及）。"""
        inn = {e["s"] for e in self.vg["edges"]
               if e["type"] == "body_crossref" and e["t"] == "a1"}
        self.assertEqual(inn, {"s1", "s2"},
                         "a1 的互引入邻居应恰为外部提及者（回退过滤时会掺入 a2/a3）：%r" % inn)


# ============================================================ G3 · 防过修

class NoOverfixTests(unittest.TestCase):

    def test_g3_non_r5_edges_untouched(self):
        """非 R5 边逐类不受影响：explicit 1 / same_bucket 4 / in_bucket 5 照旧。"""
        p1, p2, derived_doc, vg, tmp = _run_chain()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(p2.returncode, 0, p2.stderr[-300:])
        etc = vg["edge_type_count"]
        self.assertEqual(etc.get("explicit"), 1, "显式边不得被 phantom 过滤误伤")
        self.assertEqual(etc.get("same_bucket"), 4, "R1 同桶成对边不得受影响")
        self.assertEqual(etc.get("in_bucket"), 5, "R1 桶枢纽边不得受影响")
        self.assertIn(("s2", "a3"), _pairs(vg, "explicit"))

    def test_g3_no_phantom_input_zero_count_and_silent(self):
        """无 phantom 输入（标题互异）：phantom_r5_dropped==0、stdout 无损耗告警行。"""
        p1, p2, derived_doc, vg, tmp = _run_chain(same_title_group=False)
        self.addCleanup(tmp.cleanup)
        self.assertEqual(p2.returncode, 0, (p2.stdout[-300:], p2.stderr[-300:]))
        self.assertEqual(vg.get("phantom_r5_dropped"), 0,
                         "无 phantom 时计数字段应恒写 0，实得 %r" % vg.get("phantom_r5_dropped"))
        for bad in ("折叠损耗", "同名 phantom"):
            self.assertNotIn(bad, p2.stdout, "无 phantom 输入不该打损耗告警（%s）：%r" % (bad, p2.stdout))
        # 真实互引仍在：s1→a1 / s1→s2 / s2→a1（互异标题的 R5 全是真实边）
        bc = _pairs(vg, "body_crossref")
        self.assertTrue({("s1", "a1"), ("s1", "s2"), ("s2", "a1")} <= bc, bc)


if __name__ == "__main__":
    unittest.main()
