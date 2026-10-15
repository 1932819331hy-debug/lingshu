# -*- coding: utf-8 -*-
"""test_issue648_session_chains_truncation_guard · session_chains 产物口径统一守卫（#648）
======================================================================================
缺陷（#648，成立）：``tools/coggraph/session_chains.py`` 的产物三字段两套口径——
``meta.sessions`` 写全量（``len(chains)``）、``hubs``/``edges`` 不截断，而
``chains`` 只落 ``chains[:40]``、stdout 明细再截 ``chains[:8]``。45 会话夹具
实测（本机 Windows / py3.12，main @ 43ece08，min_chain=3，每会话 3 节点）：

  · 改前读数：meta.sessions=45、len(hubs)=45、len(edges)=225、len(chains)=40；
    排第 45 的 target 会话**不在** chains（chains 末位是 s39），但其枢纽在
    hubs、5 条边（2 sequential + 3 in_session）仍在 edges——同一份产物对同一
    条链给出两个答案。
  · 危害在守卫：test_coggraph_issue300_413_423_guard.py:189-191 按 chains 取
    断言（``assert "s1" in chains``），会话数 ≥ 41 时必红而与被测改动无关；
    该守卫对「chains 是否被截断」零判别力（变异实测）。

修法（方案 B：去截断，chains 全量落盘；实测依据）：
  · 体积考量不成立——45 会话产物 35180 B 中 chains 段 4680 B（13%），被截的
    5 条仅 ~585 B（1.7%）；大头 edges（26185 B，74%）本就不截断，且边随会话
    数线性增长（每会话 5 边 vs 1 条 chain），单截 chains 从未约束住产物上界。
  · chains 无任何下游代码读（build_viewer 只取 --derived 的 hubs/edges，
    view_memory 只转发文件）⇒ 截断什么都没保护。
  · 全量后单一口径 ``meta.sessions == len(chains)`` 天然成立，无需再加
    chains_total/chains_truncated 两个互证字段（互证本身也可能漂移）。
  · stdout 明细 [:8] 保留为纯展示 top-N，超样时加显式披露尾行（数据全量在
    产物里，不再隐含截断）。
  · 改后读数（同夹具）：meta.sessions=45、len(chains)=45、target 在 chains
    末位、产物 35778 B（+598 B / +1.7%）。

守卫判据（端到端跑真实 CLI，CLI 子进程＋临时件）：
  G1  45 会话夹具口径一致：meta.sessions == len(chains) == 45（≥41 跨门槛），
      stdout 汇总数与明细披露行均如实报 45；
  G2  排第 45 的 target 会话的**边**仍在 edges（题面已证的事实，钉住不回归）；
  G3（防过修）小规模（3 会话）行为与产物结构零回归；
  G4  跨门槛守卫自证：排最后的目标会话在产物中可追溯（chains 里直接有），
      且本夹具对旧口径 [ :40] 有判别力（按旧口径重放，G1/G4 必红）。

判据来源：issue #648 取证（复现命令族与本件同构）＋ #616/#639 同族守卫先例。
运行：pytest tests/test_issue648_session_chains_truncation_guard.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "tools", "coggraph", "session_chains.py")

#: 旧口径的 chains 落盘上限（session_chains.py 的 ``chains[:40]``）
OLD_CAP = 40
#: 45 会话夹具：44 个填充会话 + 1 个 target（记录放最后 ⇒ 稳定排序下排第 45）
N_FILLER = 44
TARGET = "sess_target_45th"


def _fixture_45(tmp):
    """45 个会话 × 各 3 记录；target 的记录放文件末尾。

    chains.sort(key=lambda c: -c["nodes"]) 是稳定排序，节点数全为 3 的平局按
    首次出现顺序排列 ⇒ target（最后出现）恰好排第 45，正是被旧 [:40] 截掉的
    那一条。
    """
    audit = os.path.join(tmp, "_audit.jsonl")
    graph = os.path.join(tmp, "graph.json")
    lines, nodes = [], []
    for si in range(N_FILLER):
        s = "s%02d" % si
        for i in range(3):
            nid = "%s_n%d" % (s, i)
            lines.append(json.dumps({"op": "add", "id": nid,
                                     "t": 1760000000 + si * 100 + i, "session": s}))
            nodes.append({"id": nid, "title": nid})
    for i in range(3):
        nid = "%s_n%d" % (TARGET, i)
        lines.append(json.dumps({"op": "add", "id": nid,
                                 "t": 1770000000 + i, "session": TARGET}))
        nodes.append({"id": nid, "title": nid})
    with open(audit, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    with open(graph, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"nodes": nodes, "edges": []}, f, ensure_ascii=False)
    return audit, graph


def _fixture_3(tmp):
    """3 个会话：sA=4 节点、sB/sC=3 节点 ⇒ chains 序 [sA, sB, sC]。"""
    audit = os.path.join(tmp, "_audit.jsonl")
    graph = os.path.join(tmp, "graph.json")
    lines, nodes = [], []
    spec = [("sA", 4, 1760000000), ("sB", 3, 1760010000), ("sC", 3, 1760020000)]
    for s, n, base in spec:
        for i in range(n):
            nid = "%s_n%d" % (s, i)
            lines.append(json.dumps({"op": "add", "id": nid,
                                     "t": base + i, "session": s}))
            nodes.append({"id": nid, "title": nid})
    with open(audit, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    with open(graph, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"nodes": nodes, "edges": []}, f, ensure_ascii=False)
    return audit, graph


def _run(audit, graph):
    """跑真实 CLI（--min-chain 3 --write），返回 (rc, stdout, stderr, 产物 dict|None)。"""
    tmp = os.path.dirname(audit)
    out = os.path.join(tmp, "out")
    p = subprocess.run(
        [sys.executable, "-X", "utf8", SCRIPT, "--audit", audit, "--graph", graph,
         "--out", out, "--min-chain", "3", "--write"],
        capture_output=True, text=True, encoding="utf-8", cwd=REPO, timeout=180,
        env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"))
    doc = None
    pj = os.path.join(out, "session_chains.json")
    if os.path.exists(pj):
        with open(pj, encoding="utf-8") as f:
            doc = json.load(f)
    return p.returncode, p.stdout, p.stderr, doc


def _target_nodes():
    return ["%s_n%d" % (TARGET, i) for i in range(3)]


# ============================================================ G1 · 口径一致

def test_g1_45_sessions_single_caliber():
    """G1：45 会话（≥41 跨门槛）口径一致——meta.sessions == len(chains) == 45。"""
    with tempfile.TemporaryDirectory(prefix="issue648_g1_") as tmp:
        audit, graph = _fixture_45(tmp)
        rc, stdout, stderr, doc = _run(audit, graph)
        assert rc == 0, "rc=%d stderr=%r" % (rc, stderr[-300:])
        assert doc is not None, "产物必须在场"
        n = N_FILLER + 1
        assert doc["meta"]["sessions"] == n, \
            "#648 缺陷形态：meta.sessions 应为全量 %d，实得 %r" % (n, doc["meta"]["sessions"])
        assert len(doc["chains"]) == n, \
            ("#648 缺陷形态：chains 被截为 %d 条（全量 %d）——同一份产物两套口径"
             % (len(doc["chains"]), n))
        assert len(doc["hubs"]) == n and len(doc["edges"]) == 5 * n, \
            "hubs/edges 本就不截断（45/225），实得 %d/%d" % (len(doc["hubs"]), len(doc["edges"]))
        # stdout 不得谎报：汇总数如实，超 8 条明细时显式披露全量数
        assert ("会话 %d" % n) in stdout, "stdout 汇总应如实报 %d 个会话：%r" % (n, stdout)
        assert ("共 %d 条" % n) in stdout, \
            "#648 修复形态：明细 top-N 超样时须披露全量数：%r" % stdout


# ============================================================ G2 · target 边仍在

def test_g2_target_session_edges_still_in_edges():
    """G2：排第 45 的 target 会话的边仍在 edges（题面已证事实，钉住不回归）。"""
    with tempfile.TemporaryDirectory(prefix="issue648_g2_") as tmp:
        audit, graph = _fixture_45(tmp)
        rc, _, _, doc = _run(audit, graph)
        assert rc == 0 and doc is not None
        tn = set(_target_nodes())
        seq = [e for e in doc["edges"]
               if e["type"] == "sequential" and e["s"] in tn and e["t"] in tn]
        hub = [e for e in doc["edges"]
               if e["type"] == "in_session" and e["t"] == "session:" + TARGET]
        assert len(seq) == 2, "target 会话应有 2 条顺序边，实得 %r" % seq
        assert len(hub) == 3, "target 会话应有 3 条枢纽边，实得 %r" % hub
        # 边的规则标记不回归
        assert all(e["rule"] == "R6_session_chain" and e["derived"] and e["weight"] == 0.6
                   for e in seq), seq
        assert all(e["rule"] == "R6_session_hub" and e["derived"] and e["weight"] == 0.3
                   for e in hub), hub


# ============================================================ G3 · 防过修

def test_g3_small_scale_zero_regression():
    """G3（防过修）：3 会话行为与产物结构零回归。"""
    with tempfile.TemporaryDirectory(prefix="issue648_g3_") as tmp:
        audit, graph = _fixture_3(tmp)
        rc, stdout, stderr, doc = _run(audit, graph)
        assert rc == 0, stderr[-300:]
        assert doc is not None and doc["chains"], "产物必须在场且非空"
        # 计数面：3 会话 / 7 顺序边(3+2+2) / 10 枢纽边(4+3+3) / 3 枢纽
        c = doc["meta"]["counts"]
        assert doc["meta"]["sessions"] == 3 and len(doc["chains"]) == 3, doc["meta"]
        assert c == {"sequential_edges": 7, "hub_edges": 10, "hub_nodes": 3}, c
        # 链结构：4 个字段原样，按节点数降序（平局按首现序）
        assert [x["session"] for x in doc["chains"]] == ["sA", "sB", "sC"], doc["chains"]
        assert all(set(x) == {"session", "nodes", "span", "range"} for x in doc["chains"]), \
            "chain 字段集不得漂移：%r" % doc["chains"][0]
        assert doc["chains"][0]["nodes"] == 4 and doc["chains"][1]["nodes"] == 3
        # 枢纽结构：id/kind/label/count 原样
        h = doc["hubs"][0]
        assert set(h) == {"id", "kind", "label", "count"} and h["kind"] == "session", h
        # 小规模（≤8 条）不得出现明细披露尾行
        assert "明细仅列前 8 条" not in stdout, \
            "未超样的明细不得带截断披露行（防过修）：%r" % stdout


# ============================================================ G4 · 自证判别力

def test_g4_last_ranked_target_traceable_and_fixture_distinguishes_old_caliber():
    """G4：排最后的目标会话在产物中可追溯；且本夹具对旧口径 [:40] 有判别力。"""
    with tempfile.TemporaryDirectory(prefix="issue648_g4_") as tmp:
        audit, graph = _fixture_45(tmp)
        rc, _, _, doc = _run(audit, graph)
        assert rc == 0 and doc is not None
        chains = doc["chains"]
        n = N_FILLER + 1

        # 前提自证：target 在全量序里恰好排第 45（平局稳定排序 ⇒ 夹具编排成立）
        assert chains[-1]["session"] == TARGET, \
            "夹具前提：target 应排最后，实得 %r" % chains[-1]["session"]

        # 修复形态：单一口径 + target 直接可追溯（hub / 边 / 链三处一致在场）
        assert doc["meta"]["sessions"] == n == len(chains), \
            "#648 单一口径：meta.sessions 必须等于 len(chains)"
        assert any(h["id"] == "session:" + TARGET for h in doc["hubs"])
        assert any(c["session"] == TARGET for c in chains), \
            "#648 缺陷形态：排最后的目标会话不在 chains（被 [:40] 截掉）"

        # 自证判别力：按旧口径（chains[:40]）重放本夹具产物 ⇒ G1/G4 的断言必红
        old_slice = chains[:OLD_CAP]
        assert len(old_slice) == OLD_CAP and n - OLD_CAP == 5, \
            "旧口径下 45 条被截为 40 条（丢 5 条）"
        assert TARGET not in [c["session"] for c in old_slice], \
            "自证：旧口径下 target 被截掉（否则本夹具无判别力）"
        assert doc["meta"]["sessions"] != len(old_slice), \
            "自证：旧口径下 meta.sessions(45) != len(chains[:40])(40)（两套口径之实）"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
