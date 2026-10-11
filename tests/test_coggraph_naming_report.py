#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coggraph 命名审计守卫：Q2/Q3 的「被正文提及」只能数**被提及**方向

standalone，无 pytest 依赖：
    python -X utf8 tests/test_coggraph_naming_report.py

背景：`derive_edges.py` 的 R5 是 `add(me, owner, "body_crossref", ...)` ——
`s` = 提及者（正文里出现别人标题的那个节点），`t` = 被提及的规范概念节点。
`naming_report.py` 里 Q2/Q3 的判据是「**被**正文提及 / 出现在 >=3 个节点正文」，
因此只能累计 `e["t"]`；把 `e["s"]` 也算进来，会让「这条正文提到了 3 个概念」
被当成「被 3 个节点正文提到」，从而把纯引用型节点判成「已锚定」、逃出隔离区。

A 组  纯提及者（正文提到 3 个概念、自己从未被提及）必须是「隔离区」
B 组  正向对照：被 4 个节点提及 → 已锚定；只被 1 个提及 → 隔离区（阈值语义不变）
C 组  同名别名组（3 个同名、无边）仍是「alias候选」
D 组  structural 层不受影响；Q4 规范标识词仍是「强规范锚」
E 组  夹具自证：按旧口径（同时累计 s）重算会得到「已锚定」⇒ 与修复后判定不同
F 组  报告产物：隔离区段落点名该节点、别名组表格含同名标题
G 组（#315）Q4 正则：`\b` 在汉字间不构成边界、`GB[/T]` 是单字符类 ⇒ 旧式两处漏判。
      断言：嵌在汉字串中的规范词（依据国家标准制定 / 遵守INN规则）、`GB/T 12345`、
      `GB 12345`、数字紧邻写法（ISO9001 / 药典2020）都必须命中；英文粘连
      （ISOLATION / INNOVATION）不得命中。
H 组（#359）「alias候选」必须在**真实管线**（derive_edges → naming_report）可达：
      旧口径把 R1 桶枢纽边等结构性派生边计入 Q1 ⇒ 有 bucket 的节点 q1 恒真；
      且 R5 在正文里搜到同名组自身的 `# 功能名：<标题>` 行 ⇒ 同名组内互引递归。
      断言：跑真管线后同名组 q1=False、判「alias候选」；被外部真引用的节点 q1=True、
      仍判「已锚定」（防「把 Q1 一刀砍成恒假」的代偿）。
"""
from __future__ import annotations

import collections
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMING = os.path.join(REPO, "tools", "coggraph", "naming_report.py")
DERIVE = os.path.join(REPO, "tools", "coggraph", "derive_edges.py")


def load_naming_module():
    spec = importlib.util.spec_from_file_location("naming_report_under_test", NAMING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_PASS, _FAIL = [], []


def ok(cond, msg, extra=""):
    (_PASS if cond else _FAIL).append(msg)
    if not cond:
        print("[FAIL] %s%s" % (msg, ("   " + str(extra)) if extra else ""))


def node(nid, title, layer="knowledge", bucket="kb"):
    return {"id": nid, "title": title, "layer": layer, "bucket": bucket,
            "tags": [], "importance": 0, "created": 1759930000, "path": nid + ".md"}


CITER = "甲方案说明文本"        # a1：正文提到 3 个概念，自己从未被提及
MENTIONED = "乙方案说明文本"    # b1：被 a1/e1/e2/e3 共 4 个节点正文提及
ONCE = "丙方案说明文本"         # c1：只被 a1 提及一次
ALIAS = "同一别名候选标题"      # 3 个同名节点、无边
STRUCT = "结构层骨架说明"       # structural 层
NORM = "ISO 9001 规范说明"      # Q4 规范标识词


def build_fixtures(tmp):
    nodes = [
        node("a1", CITER), node("b1", MENTIONED), node("c1", ONCE),
        node("d1", "丁方案说明文本"),
        node("e1", "戊方案说明文本"), node("e2", "己方案说明文本"), node("e3", "庚方案说明文本"),
        node("g1", ALIAS), node("g2", ALIAS), node("g3", ALIAS),
        node("s1", STRUCT, layer="structural"),
        node("n1", NORM),
    ]
    graph = os.path.join(tmp, "graph.json")
    with io.open(graph, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"meta": {"counts": {"nodes": len(nodes)}}, "nodes": nodes, "edges": []}, f, ensure_ascii=False)
    r5 = [("a1", "b1"), ("a1", "c1"), ("a1", "d1"),      # a1 提到 3 个概念
          ("e1", "b1"), ("e2", "b1"), ("e3", "b1")]      # b1 被 4 个节点正文提及
    derived = {
        "meta": {"rules": {"R5_body_crossref": len(r5)}},
        "hubs": [],
        "edges": [{"s": s, "t": t, "type": "body_crossref", "derived": True,
                   "rule": "R5_body_crossref", "weight": 0.8} for s, t in r5],
    }
    dpath = os.path.join(tmp, "derived_edges.json")
    with io.open(dpath, "w", encoding="utf-8", newline="\n") as f:
        json.dump(derived, f, ensure_ascii=False)
    return graph, dpath, derived


def run_naming(tmp, graph, derived):
    out = os.path.join(tmp, "out")
    cmd = [sys.executable, "-X", "utf8", NAMING, "--graph", graph, "--derived", derived, "--out", out, "--write"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=REPO,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"))
    rows = {}
    jp = os.path.join(out, "naming_report.json")
    if os.path.isfile(jp):
        with io.open(jp, encoding="utf-8") as f:
            rows = {r["title"]: r for r in json.load(f)["rows"]}
    md = ""
    mp = os.path.join(out, "naming_report.md")
    if os.path.isfile(mp):
        with io.open(mp, encoding="utf-8") as f:
            md = f.read()
    return p, rows, md


def main():
    with tempfile.TemporaryDirectory() as tmp:
        graph, derived_path, derived = build_fixtures(tmp)
        p, rows, md = run_naming(tmp, graph, derived_path)
        ok(p.returncode == 0, "命名审计退出码为 0", (p.stdout[-200:], p.stderr[-300:]))

        def cls(title):
            return (rows.get(title) or {}).get("class")

        # ---- A 组：纯提及者不是「被提及」 ----
        cited = [e for e in derived["edges"] if e["t"] == "a1"]
        ok(len(cited) == 0, "A 夹具自证：a1 在 R5 里从未作为被提及者（t）出现", len(cited))
        ok(len([e for e in derived["edges"] if e["s"] == "a1"]) == 3,
           "A 夹具自证：a1 的正文提到 3 个概念", len([e for e in derived["edges"] if e["s"] == "a1"]))
        ok(rows.get(CITER, {}).get("q3") is False,
           "A Q3：提及者不因「自己提到了 3 个概念」而算统计稳定", rows.get(CITER, {}).get("q3"))
        ok(rows.get(CITER, {}).get("q2") is False,
           "A Q2：提及者不因「自己提到了别人」而算被正文提及", rows.get(CITER, {}).get("q2"))
        ok(cls(CITER) == "隔离区", "A 判定：纯提及者进隔离区（修复前是「已锚定」）", cls(CITER))

        # ---- B 组：正向对照 ----
        ok(rows.get(MENTIONED, {}).get("q3") is True,
           "B Q3：被 4 个节点正文提及 ⇒ 可复现/统计稳定", rows.get(MENTIONED, {}).get("q3"))
        ok(cls(MENTIONED) == "已锚定", "B 判定：被多次提及的节点仍是「已锚定」", cls(MENTIONED))
        ok(cls(ONCE) == "隔离区", "B 判定：只被提及一次仍进隔离区（阈值未变）", cls(ONCE))

        # ---- C 组：同名别名组 ----
        ok(rows.get(ALIAS, {}).get("n") == 3, "C 夹具：3 个同名节点成组", rows.get(ALIAS, {}).get("n"))
        ok(cls(ALIAS) == "alias候选", "C 判定：同名 >=3 且无显式边 ⇒ alias候选", cls(ALIAS))

        # ---- D 组：structural 层与 Q4 ----
        ok(cls(STRUCT) == "已锚定", "D 判定：structural 层不受「必须被引用」约束", cls(STRUCT))
        ok(cls(NORM) == "强规范锚", "D 判定：Q4 规范标识词仍是「强规范锚」", cls(NORM))

        # ---- E 组：夹具自证（按旧口径重算，判定必须不同） ----
        buggy_in = len([e for e in derived["edges"] if e["rule"] == "R5_body_crossref" and e["s"] == "a1"])
        buggy_q2 = buggy_in > 0
        buggy_q3 = buggy_in >= 3
        buggy_cls = "已锚定" if (buggy_q2 and buggy_q3) else "隔离区"
        ok(buggy_cls == "已锚定", "E 自证：旧口径（同时累计 s）会把 a1 判成「已锚定」", buggy_cls)
        ok(buggy_cls != cls(CITER), "E 自证：旧口径判定与修复后判定确实不同", (buggy_cls, cls(CITER)))

        # ---- F 组：报告产物 ----
        ok(CITER in md, "F 报告：隔离区/分组段落里出现该标题")
        ok(ALIAS in md, "F 报告：重复标题别名组表格含同名标题")

    group_g_q4_regex()
    group_h_real_pipeline_alias()

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


def group_g_q4_regex():
    """#315：Q4 规范标识词正则必须能命中汉字间 / GB 写法；英文粘连不得命中。"""
    mod = load_naming_module()
    must_hit = [
        "依据国家标准制定",       # 汉字紧邻 ⇒ 旧 \b 两侧皆非边界，漏判
        "符合行业标准要求",       # 同上
        "采用注册商标标识",       # 同上
        "遵守INN规则",          # 同上
        "GB/T 12345",          # 旧 GB[/T] 单字符类漏判
        "GB 12345",            # 旧 GB[/T] 亦漏判
        "GB/T12345",           # 数字紧邻
        "ISO9001 标准",         # 数字紧邻
        "ISO 9001 规范",        # 正向对照（旧口径本就能命中，防「一刀砍空」）
        "RFC 2616",            # 正向对照
    ]
    for s in must_hit:
        ok(bool(mod.NORM_MARKERS.search(s)), "G Q4 命中：%r" % s,
           mod.NORM_MARKERS.pattern)
    must_miss = [
        "ISOLATION 说明",       # 英文粘连，不得误命中 ISO
        "INNOVATION 方案",      # 英文粘连，不得误命中 INN
        "无标识词的普通标题",     # 无标识词
    ]
    for s in must_miss:
        ok(not mod.NORM_MARKERS.search(s), "G Q4 不命中：%r" % s,
           mod.NORM_MARKERS.pattern)


def _run_derive(graph, out, bodies):
    cmd = [sys.executable, "-X", "utf8", DERIVE, "--graph", graph, "--out", out,
           "--write", "--bodies-root", bodies]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=REPO,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


def group_h_real_pipeline_alias():
    """#359：真管线（derive_edges → naming_report）下「alias候选」必须可达。"""
    ALIAS_T = "同一别名候选标题"          # 8 字，>= r5-min-title-len
    EXTERN = "被外部引用的概念名"
    with tempfile.TemporaryDirectory() as tmp:
        bodies = os.path.join(tmp, "bodies")
        os.makedirs(bodies)
        nodes = []
        for i in (1, 2, 3):
            nodes.append({"id": "g%d" % i, "title": ALIAS_T, "layer": "knowledge",
                          "bucket": "kb", "tags": ["tg%d" % i], "created": 1759930000,
                          "path": "g%d.md" % i})
        nodes.append({"id": "p1", "title": EXTERN, "layer": "knowledge",
                      "bucket": "kb", "tags": ["tp"], "created": 1759930000, "path": "p1.md"})
        for i in (1, 2, 3):
            nodes.append({"id": "x%d" % i, "title": "外部提及者文号%d" % i, "layer": "knowledge",
                          "bucket": "kb", "tags": ["tx%d" % i], "created": 1759930000,
                          "path": "x%d.md" % i})

        def body(nid, title, extra=""):
            with io.open(os.path.join(bodies, nid + ".md"), "w", encoding="utf-8", newline="\n") as f:
                f.write("---\ntags: t\n---\n# 功能名：%s\n\n正文。%s\n" % (title, extra))

        for i in (1, 2, 3):
            body("g%d" % i, ALIAS_T)             # 自身标题行 ⇒ R5 同名组内递归
        body("p1", EXTERN)
        for i in (1, 2, 3):
            body("x%d" % i, "外部提及者文号%d" % i, "参见 %s 的实现。" % EXTERN)

        graph = os.path.join(tmp, "g.json")
        with io.open(graph, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"nodes": nodes, "edges": []}, f, ensure_ascii=False)
        dout = os.path.join(tmp, "do")
        pd = _run_derive(graph, dout, bodies)
        ok(pd.returncode == 0, "H 前置：derive_edges 退出码 0", pd.stderr[-300:])
        dpath = os.path.join(dout, "derived_edges.json")
        if not os.path.isfile(dpath):
            ok(False, "H 前置：derive_edges 未产出 derived_edges.json", pd.stderr[-300:])
            return
        with io.open(dpath, encoding="utf-8") as f:
            der = json.load(f)
        r5 = [e for e in der["edges"] if e["rule"] == "R5_body_crossref"]
        r1 = [e for e in der["edges"] if e["rule"] == "R1_same_bucket"]
        ok(len(r1) >= 3, "H 夹具自证：R1 桶枢纽边存在（旧口径下即令 q1 恒真）", len(r1))
        ok(any(e["s"] in ("g2", "g3") and e["t"] == "g1" for e in r5),
           "H 夹具自证：R5 同名组内递归边存在（g2/g3 -> g1）",
           [(e["s"], e["t"]) for e in r5])

        nout = os.path.join(tmp, "no")
        pn = subprocess.run([sys.executable, "-X", "utf8", NAMING, "--graph", graph,
                             "--derived", dpath, "--out", nout, "--write"],
                            capture_output=True, text=True, encoding="utf-8", cwd=REPO,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        ok(pn.returncode == 0, "H 前置：naming_report 退出码 0", pn.stderr[-300:])
        rows = {}
        jp = os.path.join(nout, "naming_report.json")
        if os.path.isfile(jp):
            with io.open(jp, encoding="utf-8") as f:
                rows = {r["title"]: r for r in json.load(f)["rows"]}
        a = rows.get(ALIAS_T, {})
        ok(a.get("q1") is False,
           "H Q1：同名组不因 R1 桶枢纽边 / R5 自身标题行递归而算「连到稳定节点」", a)
        ok(a.get("class") == "alias候选",
           "H 判定：真管线下同名组落入「alias候选」（修复前不可达，恒为「已锚定」）",
           a.get("class"))
        e = rows.get(EXTERN, {})
        ok(e.get("q1") is True,
           "H 反向对照：被外部真引用的节点 q1 仍为真（防把 Q1 一刀砍成恒假）", e)
        ok(e.get("class") == "已锚定",
           "H 反向对照：被 3 个外部节点正文引用 ⇒ 仍判「已锚定」", e.get("class"))


if __name__ == "__main__":
    sys.exit(main())
