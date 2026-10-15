#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#606 守卫：naming_report 的 r5_in 必须滤掉同名声明 phantom 提及

standalone，无 pytest 依赖（已登记 gate.yml SCRIPT_TESTS）：
    python -X utf8 tests/test_issue606_naming_r5in_phantom_guard.py

背景（#606，P1）：derive_edges 的 R5 把同名节点自己的标题行
（`# 功能名：<自身标题>`）记成互引边（#359 已认定的 phantom）。naming_report
喂 Q1 的 deg 已滤「两端标题相同」的 phantom，而喂 Q2/Q3 的 r5_in 漏了同一道
过滤 ⇒ 同名组被自己的 phantom 提及顶上去：
    2 名组真实外部提及只有 2，加 1 条 phantom 后 r5_in=3 ⇒ q3=True ⇒ class=已锚定，
    逃出「隔离区」；index.html 默认只显示已锚定组，本该隔离的切碎概念被放行，
    q2/q3 还顺流影响 alias 候选/合并建议。

组：
  G1  phantom 不计入：2 名组 + 2 外部提及者 ⇒ q3=False、class=隔离区
      （夹具自证：R5 里确有 1 条 phantom + 2 条真实；按旧口径重算 r5_in=3 ⇒
       旧判定「已锚定」⇒ 本夹具有判别力）
  G2  防过修：2 名组 + 3 外部提及者（真实够数）⇒ 仍「已锚定」
  G3  phantom 不许单独顶上去：2 名组 + 0 外部提及 ⇒ 隔离区
      G3a 真管线：R5 只含 1 条 phantom ⇒ q3=False、隔离区；
      G3b 手造 derived（phantom 提及灌到 3 条）：q3 仍必须 False ⇒ 隔离区。
          真实 derive 按 (无序对, 规则) 去重不会产出重复边，但 naming_report
          是独立一段、读什么算什么——本子题钉死「phantom 单独到不了阈值」
          这条语义本身（夹具自证：旧口径 r5_in=3 ⇒ 判「alias候选」⇒ 有判别力）。
  G4  deg 路径零回归：不同标题的真实互引（R5）与显式边照常计数——
      deg/r5_in 逐 id 断言数值，q1 三路（被提及端 / 提及端 / 显式边）全真。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMING = os.path.join(REPO, "tools", "coggraph", "naming_report.py")
DERIVE = os.path.join(REPO, "tools", "coggraph", "derive_edges.py")

_PASS, _FAIL = [], []


def ok(cond, msg, extra=""):
    (_PASS if cond else _FAIL).append(msg)
    if not cond:
        print("[FAIL] %s%s" % (msg, ("   " + str(extra)) if extra else ""))


TITLE = "重复标题测试用例"        # 8 字，>= r5-min-title-len(8)，也 >= naming --min-title(6)


def node(nid, title):
    return {"id": nid, "title": title, "layer": "knowledge", "bucket": "kb",
            "tags": ["t_" + nid], "importance": 0, "created": 1759930000,
            "path": nid + ".md"}


def write_body(bodies, nid, title, extra=""):
    with io.open(os.path.join(bodies, nid + ".md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\ntags: t\n---\n# 功能名：%s\n\n正文。%s\n" % (title, extra))


def write_graph(path, nodes, edges=None):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"nodes": nodes, "edges": edges or []}, f, ensure_ascii=False)


def run_derive(graph, out, bodies):
    p = subprocess.run([sys.executable, "-X", "utf8", DERIVE, "--graph", graph,
                        "--out", out, "--write", "--bodies-root", bodies],
                       capture_output=True, text=True, encoding="utf-8", cwd=REPO,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    return p


def run_naming(graph, derived, out):
    p = subprocess.run([sys.executable, "-X", "utf8", NAMING, "--graph", graph,
                        "--derived", derived, "--out", out, "--write"],
                       capture_output=True, text=True, encoding="utf-8", cwd=REPO,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                PYTHONIOENCODING="utf-8"))
    rows = {}
    jp = os.path.join(out, "naming_report.json")
    if p.returncode == 0 and os.path.isfile(jp):
        with io.open(jp, encoding="utf-8") as f:
            rows = {r["title"]: r for r in json.load(f)["rows"]}
    return p, rows


def load_derived(out):
    with io.open(os.path.join(out, "derived_edges.json"), encoding="utf-8") as f:
        return json.load(f)


def r5_pairs(der):
    return sorted((e["s"], e["t"]) for e in der["edges"] if e.get("rule") == "R5_body_crossref")


def build_same_title_fixture(tmp, n_extern):
    """g1/g2 同名组（自身标题行 ⇒ phantom g2->g1）+ n_extern 个外部提及者 x*。"""
    bodies = os.path.join(tmp, "bodies")
    os.makedirs(bodies)
    nodes = [node("g1", TITLE), node("g2", TITLE)]
    for j in range(1, n_extern + 1):
        xt = "外部提及者文号%d" % j
        nodes.append(node("x%d" % j, xt))
    write_body(bodies, "g1", TITLE)
    write_body(bodies, "g2", TITLE)
    for j in range(1, n_extern + 1):
        write_body(bodies, "x%d" % j, "外部提及者文号%d" % j, "参见 %s 的实现。" % TITLE)
    graph = os.path.join(tmp, "g.json")
    write_graph(graph, nodes)
    return graph, bodies


def group_g1_phantom_not_counted():
    """G1：2 名组 + 2 外部提及者 ⇒ q3=False、class=隔离区（phantom 不计入 r5_in）。"""
    with tempfile.TemporaryDirectory() as tmp:
        graph, bodies = build_same_title_fixture(tmp, 2)
        dout = os.path.join(tmp, "do")
        pd = run_derive(graph, dout, bodies)
        ok(pd.returncode == 0, "G1 前置：derive_edges 退出码 0", pd.stderr[-300:])
        if pd.returncode != 0:
            return
        der = load_derived(dout)
        pairs = r5_pairs(der)
        ok(pairs == [("g2", "g1"), ("x1", "g1"), ("x2", "g1")],
           "G1 夹具自证：R5 = 1 条同名 phantom（g2->g1）+ 2 条真实外部提及",
           pairs)
        pn, rows = run_naming(graph, os.path.join(dout, "derived_edges.json"),
                              os.path.join(tmp, "no"))
        ok(pn.returncode == 0, "G1 前置：naming_report 退出码 0", pn.stderr[-300:])
        r = rows.get(TITLE, {})
        ok(r.get("q3") is False,
           "G1 Q3：真实外部提及 2 < 3，phantom 不得把 r5_in 顶到 3", r)
        ok(r.get("class") == "隔离区",
           "G1 判定：2 名组 + 2 真实提及 ⇒ 隔离区（修复前被 phantom 顶成「已锚定」）",
           r.get("class"))
        # 判别力自证（旧口径重算）：r5_in 全收 ⇒ 3 ⇒ 旧判定「已锚定」≠ 现判定
        old_sum = sum(1 for e in der["edges"]
                      if e.get("rule") == "R5_body_crossref" and e["t"] in ("g1", "g2"))
        ok(old_sum == 3, "G1 自证：旧口径 r5_in=%d（>=3 ⇒ 旧判「已锚定」）" % old_sum, old_sum)
        ok(old_sum >= 3 and r.get("class") == "隔离区",
           "G1 自证：新旧口径判定确实不同（本夹具有判别力）")


def group_g2_real_mentions_still_anchor():
    """G2（防过修）：2 名组 + 3 外部提及者（真实够数）⇒ 仍「已锚定」。"""
    with tempfile.TemporaryDirectory() as tmp:
        graph, bodies = build_same_title_fixture(tmp, 3)
        dout = os.path.join(tmp, "do")
        pd = run_derive(graph, dout, bodies)
        ok(pd.returncode == 0, "G2 前置：derive_edges 退出码 0", pd.stderr[-300:])
        if pd.returncode != 0:
            return
        der = load_derived(dout)
        pairs = r5_pairs(der)
        ok(pairs == [("g2", "g1"), ("x1", "g1"), ("x2", "g1"), ("x3", "g1")],
           "G2 夹具自证：R5 = 1 phantom + 3 真实外部提及", pairs)
        pn, rows = run_naming(graph, os.path.join(dout, "derived_edges.json"),
                              os.path.join(tmp, "no"))
        ok(pn.returncode == 0, "G2 前置：naming_report 退出码 0", pn.stderr[-300:])
        r = rows.get(TITLE, {})
        ok(r.get("q3") is True, "G2 Q3：真实外部提及 3 >= 3 ⇒ 统计稳定", r)
        ok(r.get("class") == "已锚定", "G2 判定：真实够数仍「已锚定」（不得过修）", r.get("class"))


def group_g3_phantom_alone_never_lifts():
    """G3：0 外部提及 ⇒ 隔离区（phantom 不许单独顶上去）。"""
    # G3a：真管线——R5 只含 1 条同名 phantom
    with tempfile.TemporaryDirectory() as tmp:
        graph, bodies = build_same_title_fixture(tmp, 0)
        dout = os.path.join(tmp, "do")
        pd = run_derive(graph, dout, bodies)
        ok(pd.returncode == 0, "G3a 前置：derive_edges 退出码 0", pd.stderr[-300:])
        if pd.returncode != 0:
            return
        der = load_derived(dout)
        pairs = r5_pairs(der)
        ok(pairs == [("g2", "g1")], "G3a 夹具自证：R5 只含同名 phantom（g2->g1）", pairs)
        pn, rows = run_naming(graph, os.path.join(dout, "derived_edges.json"),
                              os.path.join(tmp, "no"))
        ok(pn.returncode == 0, "G3a 前置：naming_report 退出码 0", pn.stderr[-300:])
        r = rows.get(TITLE, {})
        ok(r.get("q3") is False, "G3a Q3：0 外部提及 ⇒ 不得统计稳定", r)
        ok(r.get("class") == "隔离区", "G3a 判定：真管线下 0 外部提及 ⇒ 隔离区", r.get("class"))

    # G3b：手造 derived——phantom 提及灌到 3 条，q3 仍必须 False
    #（真实 derive 按 (无序对, 规则) 去重不会产出重复边；此处钉 naming_report
    #  自身「phantom 单独到不了阈值」的语义，naming 读什么算什么，须自守门。）
    with tempfile.TemporaryDirectory() as tmp:
        nodes = [node("g1", TITLE), node("g2", TITLE)]
        graph = os.path.join(tmp, "g.json")
        write_graph(graph, nodes)
        phantom = [{"s": "g2", "t": "g1", "type": "body_crossref", "derived": True,
                    "rule": "R5_body_crossref", "weight": 0.8}] * 3
        dpath = os.path.join(tmp, "derived_edges.json")
        with io.open(dpath, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"meta": {"rules": {"R5_body_crossref": 3}}, "hubs": [],
                       "edges": phantom}, f, ensure_ascii=False)
        pn, rows = run_naming(graph, dpath, os.path.join(tmp, "no"))
        ok(pn.returncode == 0, "G3b 前置：naming_report 退出码 0", pn.stderr[-300:])
        r = rows.get(TITLE, {})
        ok(r.get("q3") is False,
           "G3b Q3：3 条同名 phantom 单独在场也不得顶到阈值", r)
        ok(r.get("class") == "隔离区",
           "G3b 判定：phantom 不许单独顶上去 ⇒ 隔离区（旧口径判「alias候选」）",
           r.get("class"))
        old_cls = "alias候选"   # 旧口径：r5_in=3 ⇒ q3=True；deg 滤 phantom ⇒ q1=False
        ok(old_cls != r.get("class"),
           "G3b 自证：新旧口径判定确实不同（本夹具有判别力）", (old_cls, r.get("class")))


def group_g4_deg_path_zero_regression():
    """G4：不同标题的真实互引/显式边计数不受影响（deg/r5_in 逐 id 数值断言）。"""
    U, V, W, Z = "甲概念基准标题名", "乙概念引用者标题", "丙概念第二引用者", "丁概念孤立邻接者"
    with tempfile.TemporaryDirectory() as tmp:
        bodies = os.path.join(tmp, "bodies")
        os.makedirs(bodies)
        nodes = [node("u1", U), node("v1", V), node("w1", W), node("z1", Z)]
        write_body(bodies, "u1", U)
        write_body(bodies, "v1", V, "参见 %s 的实现。" % U)
        write_body(bodies, "w1", W, "参见 %s 的实现。" % U)
        write_body(bodies, "z1", Z)
        graph = os.path.join(tmp, "g.json")
        write_graph(graph, nodes, edges=[{"s": "z1", "t": "w1", "type": "rel",
                                          "weight": 1.0}])
        dout = os.path.join(tmp, "do")
        pd = run_derive(graph, dout, bodies)
        ok(pd.returncode == 0, "G4 前置：derive_edges 退出码 0", pd.stderr[-300:])
        if pd.returncode != 0:
            return
        der = load_derived(dout)
        pairs = r5_pairs(der)
        ok(pairs == [("v1", "u1"), ("w1", "u1")],
           "G4 夹具自证：不同标题 ⇒ R5 全是真实互引（无 phantom 可滤）", pairs)

        # 按 naming（修复后）口径重算 deg / r5_in，逐 id 断言数值
        title_of = {n["id"]: (n.get("title") or "").strip() for n in nodes}
        deg, r5_in = {}, {}
        for e in der["edges"]:
            if e.get("rule") != "R5_body_crossref":
                continue
            if title_of.get(e["s"], "") == title_of.get(e["t"], ""):
                continue
            deg[e["s"]] = deg.get(e["s"], 0) + 1
            deg[e["t"]] = deg.get(e["t"], 0) + 1
            r5_in[e["t"]] = r5_in.get(e["t"], 0) + 1
        deg["z1"] = deg.get("z1", 0) + 1          # 显式边 z1-w1
        deg["w1"] = deg.get("w1", 0) + 1
        ok(deg == {"u1": 2, "v1": 1, "w1": 2, "z1": 1},
           "G4 deg 数值：真实 R5 两端 + 显式边照常计数（w1=1 条 R5 + 1 条显式）", deg)
        ok(r5_in == {"u1": 2}, "G4 r5_in 数值：只数被提及方向（u1=2，其余 0）", r5_in)

        pn, rows = run_naming(graph, os.path.join(dout, "derived_edges.json"),
                              os.path.join(tmp, "no"))
        ok(pn.returncode == 0, "G4 前置：naming_report 退出码 0", pn.stderr[-300:])
        ok(rows.get(U, {}).get("q1") is True, "G4 q1：被提及端（u1）仍连到稳定节点", rows.get(U))
        ok(rows.get(V, {}).get("q1") is True, "G4 q1：提及端（v1）R5 真实边仍计 deg", rows.get(V))
        ok(rows.get(Z, {}).get("q1") is True, "G4 q1：显式边（z1）仍计 deg", rows.get(Z))
        ok(rows.get(U, {}).get("q2") is True, "G4 q2：u1 被 2 个节点正文提及 ⇒ 被提及>0", rows.get(U))
        ok(rows.get(V, {}).get("q2") is False, "G4 q2：提及者 v1 不算被提及", rows.get(V))
        ok(rows.get(U, {}).get("q3") is False, "G4 q3：2 条真实提及 < 3 ⇒ 阈值语义不变", rows.get(U))


def main():
    group_g1_phantom_not_counted()
    group_g2_real_mentions_still_anchor()
    group_g3_phantom_alone_never_lifts()
    group_g4_deg_path_zero_regression()

    total = len(_PASS) + len(_FAIL)
    print("")
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), total))
    if _FAIL:
        print("失败项：")
        for m in _FAIL:
            print("  - " + m)
        return 1
    print("VERDICT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
