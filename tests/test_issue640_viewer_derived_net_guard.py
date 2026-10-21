# -*- coding: utf-8 -*-
"""#640 守卫 · viewer_graph["derived"] 必须是折叠后净数（侧栏「全库 N 边」不再高估）。

缺陷（issue #640，判定线 II 判成立，口径经本端裁定=源头修）：旧实现
`d_edge_count = len(d_edges)` 在折叠**前**定死，而 #618 新增的
dropped_selfloop / merged_edges 正是从这批边里剔掉的 ⇒ 侧栏「全库 N 边」
（`G.meta.counts.edges + G.derived`）系统性高估，偏差恒等于损耗量；
既有 413 守卫夹具零损耗 ⇒ 结构上测不出该口径冲突（报告变异测试坐实）。

判据（回退修复 ⇒ 本文件必红）：
  G1 有损耗夹具下 `derived == 派生输入 − dropped_selfloop − merged_edges`；
  G2 对账恒等矩阵（0/0、1/0、0/1、3/0、1/1 五组＝报告五场景逐行），
     且显式侧零损耗时 `counts.edges + derived == len(edges)`；
  G3 零损耗不回归（derived == 输入边数，钉 413 加法语义不变）；
  G4 #618 两字段恒存在（含 0），后续任何人删掉都报警；
  产物自洽：derived == len([e for e in edges if e["derived"]])。

不适用条件：不改前端 JS（侧栏直用 G.edges.length 的方案已被裁否）；
hubs 的 count 属节点数语义（R4，既有测试已锁），不在本件口径内。

pytest 式（含 test_*），由 pytest tests/ 自动收集；无需登记 gate.yml SCRIPT_TESTS。
运行：python -X utf8 -m pytest tests/test_issue640_viewer_derived_net_guard.py -q
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_VIEWER = os.path.join(HERE, "tools", "coggraph", "build_viewer.py")

_STUB = "// offline stub\n" + "x" * 100001
_STUB_SHA = hashlib.sha256(_STUB.encode("utf-8")).hexdigest()


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _d_edge(s, t):
    return {"s": s, "t": t, "type": "in_bucket", "derived": True, "rule": "R1_same_bucket"}


def _run_case(d_edges):
    """跑 build_viewer 端到端（显式侧 1 条边、零损耗；派生侧按场景构造），返回 vgraph。"""
    tmp = tempfile.TemporaryDirectory(prefix="i640_")
    base = tmp.name
    out = os.path.join(base, "out")
    os.makedirs(out, exist_ok=True)
    graph = os.path.join(base, "graph.json")
    _write(graph, json.dumps({
        "meta": {"counts": {"nodes": 2, "edges": 1}, "source_sha": "t640"},
        "nodes": [{"id": "n1", "title": "甲", "layer": "knowledge", "bucket": "kb",
                   "tags": [], "importance": 0, "path": "n1.md"},
                  {"id": "n2", "title": "乙", "layer": "knowledge", "bucket": "kb",
                   "tags": [], "importance": 0, "path": "n2.md"}],
        "edges": [{"s": "n1", "t": "n2", "type": "explicit", "derived": False}],
    }, ensure_ascii=False))
    dv = os.path.join(base, "derived.json")
    _write(dv, json.dumps({
        "hubs": [{"id": "bucket:kb", "kind": "bucket", "label": "桶 kb", "count": 2}],
        "edges": d_edges}, ensure_ascii=False))
    _write(os.path.join(out, "cytoscape.min.js"), _STUB)
    cmd = [sys.executable, "-X", "utf8", BUILD_VIEWER, "--graph", graph,
           "--derived", dv, "--out", out, "--allow-unofficial-cyto"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LINGSHU_CYTO_SHA256=_STUB_SHA)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       cwd=HERE, env=env)
    assert p.returncode == 0, (p.stdout[-300:], p.stderr[-300:])
    with io.open(os.path.join(out, "viewer_graph.json"), encoding="utf-8") as f:
        vg = json.load(f)
    tmp.cleanup()
    return vg


# 报告五场景矩阵：(名, 派生输入边列表, dropped, merged, 净数)
def _surv(i):
    return _d_edge("bucket:kb", "n1" if i % 2 else "n2")


SCENARIOS = [
    ("零损耗", [_surv(0), _surv(1)], 0, 0, 2),
    ("1自环", [_surv(0), _d_edge("bucket:kb", "bucket:kb")], 1, 0, 1),
    ("1合并", [_surv(0), dict(_surv(0))], 0, 1, 1),
    ("3自环", [_d_edge("bucket:kb", "bucket:kb") for _ in range(3)], 3, 0, 0),
    ("混合", [_d_edge("bucket:kb", "bucket:kb"), _surv(0), dict(_surv(0)), _surv(1)], 1, 1, 2),
]


class DerivedNetTests(unittest.TestCase):
    def test_g1_lossy_scenarios_derived_is_net(self):
        """G1：每场景 derived == 输入 − dropped − merged == 产物 derived 边存活数。"""
        for name, d_edges, drop, merged, net in SCENARIOS:
            with self.subTest(场景=name):
                vg = _run_case(d_edges)
                d_in = len(d_edges)
                self.assertEqual(vg["dropped_selfloop"], drop,
                                 "%s 夹具自环计数不符：%r" % (name, vg.get("dropped_selfloop")))
                self.assertEqual(vg["merged_edges"], merged,
                                 "%s 夹具合并计数不符：%r" % (name, vg.get("merged_edges")))
                self.assertEqual(vg["derived"], d_in - drop - merged,
                                 "%s derived 非净数（折叠前旧口径）：%r，应为 %r"
                                 % (name, vg.get("derived"), d_in - drop - merged))
                self.assertEqual(
                    vg["derived"], len([e for e in vg["edges"] if e["derived"]]),
                    "%s derived 与产物 edges 自洽性破裂" % name)

    def test_g2_reconciliation_identity_all_scenarios(self):
        """G2：侧栏加法恒等（显式侧零损耗）counts.edges + derived == len(edges)。"""
        for name, d_edges, drop, merged, net in SCENARIOS:
            with self.subTest(场景=name):
                vg = _run_case(d_edges)
                self.assertEqual(vg["meta"]["counts"]["edges"] + vg["derived"],
                                 len(vg["edges"]),
                                 "%s 侧栏「全库边数」与产物边数不等（高估=损耗未扣）" % name)
                # 旧口径偏差 ≡ 损耗量 ⇒ 净数修法偏差为 0
                self.assertEqual(len(d_edges) - vg["derived"], drop + merged,
                                 "%s 偏差 != dropped+merged（疑似只修一半）" % name)

    def test_g3_zero_loss_not_regressed(self):
        """G3：零损耗场景 derived == 输入边数（钉 413 加法语义未被顺手改错）。"""
        vg = _run_case(SCENARIOS[0][1])
        self.assertEqual(vg["derived"], 2, "零损耗时 derived 应仍为输入 2，实得 %r" % vg.get("derived"))
        self.assertEqual(vg["meta"]["counts"]["edges"] + vg["derived"], 3)

    def test_g4_loss_fields_always_present(self):
        """G4：#618 两字段恒存在（含 0）——零损耗场景也必须在场，删字段即报警。"""
        vg = _run_case(SCENARIOS[0][1])
        self.assertIn("dropped_selfloop", vg)
        self.assertIn("merged_edges", vg)
        self.assertEqual(vg["dropped_selfloop"], 0)
        self.assertEqual(vg["merged_edges"], 0)


if __name__ == "__main__":
    unittest.main()
