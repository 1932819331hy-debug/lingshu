# -*- coding: utf-8 -*-
"""#634 守卫 · derive_edges R3 泛化标签剪枝：阈值参数化 + 超限可观测（不静默归零）。

缺陷（issue #634，判定线 II 判成立 P2）：旧实现 `if len(owners) > 400: continue`
硬编码、整条跳过、stderr 与 meta 零可观测 ⇒ owners 400→401 时该标签贡献的全部
候选对**阶跃归零**，`R3_tag_jaccard: 0` 与「确实无强关联」读数完全不可区分；
且与同文件 R5 泛化词剪枝（主动告警）口径不对称。

修法：`--tag-max-owners`（默认 400＝旧值，默认路径行为逐点兼容）；超限标签记入
skipped_generic，stderr 告警（与 R5 同口径、只报计数不报标签名＝公开面卫生），
产物 meta 恒写 `skipped_generic_tags` 与 `tag_max_owners` 两字段（含 0）。

判别力（回退修复 ⇒ 本文件必红）：
  G1 N=401 时 meta.skipped_generic_tags==1 且 stderr 含 [R3]——旧口径字段缺失、零告警，红；
  G2 边界精确：N=400 有边 / N=401 无边（钉 `>` 而非 `>=`，防过宽修法）；
  G3 非一刀砍：401 owners 场景下由**非泛标签**作桥的节点对边仍在；
  G4 与 R5 同类剪枝都告警（超限场景 stderr 含 [R3] 行）；
  G5 边界点 PYTHONHASHSEED 变体下边集逐条一致（剪枝改动不得引入新的顺序依赖）；
  G6 参数生效：--tag-max-owners 401 使 401 owners 恢复有边、--tag-max-owners 5 使
     小库 10 owners 归零且同样可观测。
回归面：默认路径兼容由既有守卫钉（test_coggraph_derive_dedup_cap / generic_tag /
determinism / export_id_order），本件不重复放宽。

pytest 式（含 test_*），由 pytest tests/ 自动收集；无需登记 gate.yml SCRIPT_TESTS。
运行：python -X utf8 -m pytest tests/test_issue634_tag_max_owners_guard.py -q
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DERIVE = os.path.join(HERE, "tools", "coggraph", "derive_edges.py")


def _node(nid, tags):
    return {"id": nid, "title": "节点" + nid, "layer": "knowledge", "bucket": "kb",
            "tags": tags, "importance": 0, "path": nid + ".md"}


def _write_json(path, doc):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False)


def _graph_big(n):
    """n 个节点共享标签 big（桥节点由用例自行 append，见 test_g3）。"""
    nodes = [_node("x%03d" % i, ["big"]) for i in range(n)]
    return {"meta": {"counts": {"nodes": len(nodes), "edges": 0}, "source_sha": "t634"},
            "nodes": nodes, "edges": []}


def _run_derive(graph_doc, extra=(), seed=None, base_flags=("--tag-jac", "0", "--min-shared", "1")):
    tmp = tempfile.TemporaryDirectory(prefix="i634_")
    base = tmp.name
    graph = os.path.join(base, "graph.json")
    _write_json(graph, graph_doc)
    out = os.path.join(base, "out")
    cmd = [sys.executable, "-X", "utf8", DERIVE, "--graph", graph, "--out", out,
           "--write"] + list(base_flags) + list(extra)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if seed is not None:
        env["PYTHONHASHSEED"] = str(seed)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       cwd=HERE, env=env)
    assert p.returncode == 0, (p.stdout[-300:], p.stderr[-400:])
    with io.open(os.path.join(out, "derived_edges.json"), encoding="utf-8") as f:
        doc = json.load(f)
    tmp.cleanup()
    return p, doc


def _r3_edges(doc):
    return sorted((min(e["s"], e["t"]), max(e["s"], e["t"]))
                  for e in doc["edges"] if e.get("rule") == "R3_tag_jaccard")


class TagMaxOwnersObservabilityTests(unittest.TestCase):
    def test_g1_401_generic_zeroed_but_observable(self):
        """G1（判别力锁）：401 owners ⇒ R3=0，但 meta.skipped_generic_tags==1、stderr 有告警。"""
        p, doc = _run_derive(_graph_big(401))
        self.assertEqual(doc["meta"]["rules"]["R3_tag_jaccard"], 0)
        self.assertIn("skipped_generic_tags", doc["meta"],
                      "meta 缺 skipped_generic_tags（#634 静默剪枝缺陷形态）")
        self.assertEqual(doc["meta"]["skipped_generic_tags"], 1)
        self.assertEqual(doc["meta"].get("tag_max_owners"), 400)
        self.assertIn("[R3]", p.stderr, "超限剪枝未告警（与 R5 口径不对称）：%r" % p.stderr)

    def test_g2_boundary_exclusive_400_in_401_out(self):
        """G2：400 owners 正常参与（无边被剪、无告警）；401 归零——钉 `>` 而非 `>=`。"""
        p400, doc400 = _run_derive(_graph_big(400))
        self.assertGreater(doc400["meta"]["rules"]["R3_tag_jaccard"], 0,
                           "400 owners 不应被剪（边界判据必须保持 >）")
        self.assertEqual(doc400["meta"]["skipped_generic_tags"], 0)
        self.assertNotIn("[R3]", p400.stderr, "零剪枝时不应告警")
        p401, doc401 = _run_derive(_graph_big(401))
        self.assertEqual(doc401["meta"]["rules"]["R3_tag_jaccard"], 0)
        self.assertEqual(doc401["meta"]["skipped_generic_tags"], 1)
        self.assertIn("[R3]", p401.stderr)

    def test_g3_bridge_pair_survives_generic_skip(self):
        """G3（防一刀砍空）：big(401+2 owners) 被剪时，由独立桥标签相连的 (bx,by) 边仍在。

        口径说明：被剪的只是 big 的**两两枚举贡献**；jac 仍用完整 content_of 计算，
        桥标签（owners=2 ≤ 阈值）照常进 cand ⇒ 桥对不被泛标签剪枝误伤。
        """
        g = _graph_big(401)
        g["nodes"].append(_node("bx", ["big", "bpairxby"]))
        g["nodes"].append(_node("by", ["big", "bpairxby"]))
        _, doc = _run_derive(g)
        self.assertEqual(doc["meta"]["skipped_generic_tags"], 1)
        self.assertIn(("bx", "by"), _r3_edges(doc),
                      "非泛标签桥接对被泛标签剪枝误伤（一刀砍空代偿）：%r" % _r3_edges(doc))

    def test_g6_flag_raises_threshold_restores_edges(self):
        """G6：--tag-max-owners 401 ⇒ 401 owners 恢复有边、计数归 0。"""
        p, doc = _run_derive(_graph_big(401), extra=("--tag-max-owners", "401"))
        self.assertEqual(doc["meta"]["skipped_generic_tags"], 0)
        self.assertEqual(doc["meta"].get("tag_max_owners"), 401)
        self.assertGreater(doc["meta"]["rules"]["R3_tag_jaccard"], 0)
        self.assertNotIn("[R3]", p.stderr)

    def test_g6b_flag_lowers_threshold_generic_cut_still_observable(self):
        """G6b：--tag-max-owners 5 时 10 owners 标签被剪——同样计数+告警，不是静默。"""
        p, doc = _run_derive(_graph_big(10), extra=("--tag-max-owners", "5"))
        self.assertEqual(doc["meta"]["rules"]["R3_tag_jaccard"], 0)
        self.assertEqual(doc["meta"]["skipped_generic_tags"], 1)
        self.assertIn("[R3]", p.stderr)

    def test_default_small_library_field_present_zero(self):
        """默认路径兼容：正常小库无剪枝 ⇒ 字段恒存在且为 0、零告警、边照常产出。"""
        nodes = [_node("d%d" % i, ["t1", "t2", "t3"]) for i in range(6)]
        doc_g = {"meta": {"counts": {"nodes": 6, "edges": 0}, "source_sha": "t634"},
                 "nodes": nodes, "edges": []}
        p, doc = _run_derive(doc_g)
        self.assertGreater(doc["meta"]["rules"]["R3_tag_jaccard"], 0)
        self.assertEqual(doc["meta"]["skipped_generic_tags"], 0)
        self.assertNotIn("[R3]", p.stderr)

    def test_g5_boundary_determinism_under_hashseed(self):
        """G5：400/401 边界点在 PYTHONHASHSEED 变体下边集逐条一致（不引入顺序依赖）。"""
        for n in (400, 401):
            runs = [_r3_edges(_run_derive(_graph_big(n), seed=s)[1]) for s in (0, 1, 12345)]
            self.assertEqual(runs[0], runs[1], "N=%d hashseed 0/1 边集漂移" % n)
            self.assertEqual(runs[1], runs[2], "N=%d hashseed 1/12345 边集漂移" % n)


if __name__ == "__main__":
    unittest.main()
