# -*- coding: utf-8 -*-
"""#627 守卫：naming_report 长度闸必须豁免「标题本身即规范标识词」的短标题。

缺陷态（修复前）`tools/coggraph/naming_report.py` 组收集段：
    if len(t) >= args.min_title:      # 默认 6
「药典/专利/国家标准/INN/ISO/IEC/IEEE/W3C/RFC××」等规范标识词字数常 < 6 ⇒
根本进不了 groups ⇒ Q4 永不参评 ⇒ 默认参数下这类节点零「强规范锚」，报告
对这一盲区零披露；同一份图 `--min-title 2` 与默认跑 counts 差全由该闸造成
（issue #627，判定线 II 复现成立，origin/main=`6ced89b`）。

修法（本端已裁·勿改判）：一行豁免 `or NORM_MARKERS.search(t)` —— 命中 Q4
词表的标题不受长度闸限制，照常进 groups 由四问裁决。

守卫组（双向判别力）：
  G1 短规范锚词（药典/INN/ISO，单节点、无任何边、layer=knowledge）：
     默认参数下必须出现在 rows 且 q4=True、class=强规范锚
     ——旧口径（len>=6）这三个标题全被挡 ⇒ rows 无此行 ⇒ 本组必红（回退即红）；
  G2 防过修：短的**非**规范词标题（「记忆写入」4 字、无 Q4 词）默认仍被
     min-title 闸挡住（不进 rows）——豁免只开给规范锚词，不是取消长度闸；
  G3 counts 一致性：同一份混合图，默认参数与 `--min-title 2` 的
     counts['强规范锚'] 必须相等（修复前默认 0 vs 2-min 2 ⇒ 逃逸探针：旧放行、新拦截）；
  G4 既有面零回归：8 字普通标题的组照常收集（不误伤长标题）。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue627_naming_norm_anchor_minlen_guard.py -q
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

EMPTY_DERIVED = {"meta": {}, "hubs": [], "edges": []}


def _node(nid, title):
    return {"id": nid, "title": title, "layer": "knowledge", "bucket": "kb",
            "tags": ["t_" + nid], "importance": 0, "created": 1759930000,
            "path": nid + ".md"}


def _run(tmp, nodes, min_title=None):
    graph = os.path.join(tmp, "g.json")
    with io.open(graph, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"nodes": nodes, "edges": []}, f, ensure_ascii=False)
    derived = os.path.join(tmp, "d.json")
    with io.open(derived, "w", encoding="utf-8", newline="\n") as f:
        json.dump(EMPTY_DERIVED, f, ensure_ascii=False)
    out = os.path.join(tmp, "out%s" % (min_title if min_title is not None else "d"))
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, "-X", "utf8", NAMING, "--graph", graph,
           "--derived", derived, "--out", out, "--write"]
    if min_title is not None:
        cmd += ["--min-title", str(min_title)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       cwd=REPO, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                          PYTHONIOENCODING="utf-8"))
    assert p.returncode == 0, p.stderr[-400:]
    with io.open(os.path.join(out, "naming_report.json"), encoding="utf-8") as f:
        rep = json.load(f)
    return {r["title"]: r for r in rep["rows"]}, rep["counts"]


def test_g1_short_norm_anchor_classified_despite_min_title():
    """G1：短规范锚词默认参数下必须参评且判「强规范锚」（旧口径回退即红）。"""
    with tempfile.TemporaryDirectory() as tmp:
        rows, counts = _run(tmp, [_node("n1", "药典"), _node("n2", "INN"),
                                  _node("n3", "ISO")])
        for t in ("药典", "INN", "ISO"):
            assert t in rows, "短规范锚词 %r 被长度闸挡在 Q4 之外（#627 缺陷态）" % t
            assert rows[t]["q4"] is True, (t, rows[t])
            assert rows[t]["class"] == "强规范锚", (t, rows[t]["class"])
        assert counts.get("强规范锚", 0) == 3, counts
        # 判别力自证：三词 len 均 < 默认 min-title=6，修复前收集段全挡
        assert all(len(t) < 6 for t in ("药典", "INN", "ISO"))


def test_g2_short_non_norm_title_still_gated():
    """G2：短非规范词标题仍受 min-title 闸——豁免只开给 Q4 词，不是取消闸。"""
    with tempfile.TemporaryDirectory() as tmp:
        rows, _ = _run(tmp, [_node("n1", "记忆写入")])
        assert "记忆写入" not in rows, "非规范短标题漏过长度闸（豁免面被放宽）"


def test_g3_counts_identical_default_vs_min_title_2():
    """G3：混合图 counts['强规范锚'] 默认与 --min-title 2 必须一致（旧逃逸探针）。"""
    nodes = [_node("n1", "药典"), _node("n2", "INN"), _node("n3", "国家标准"),
             _node("n4", "记忆写入"), _node("n5", "重复标题测试用例")]
    with tempfile.TemporaryDirectory() as tmp:
        _d, cd = _run(tmp, nodes)
        _m, cm = _run(tmp, nodes, min_title=2)
        assert cd.get("强规范锚", 0) == cm.get("强规范锚", 0), \
            "默认 counts 与 --min-title 2 差仍由长度闸造成（#627）：%s vs %s" % (cd, cm)
        assert cd.get("强规范锚", 0) == 3, cd


def test_g4_long_title_grouping_zero_regression():
    """G4：>=min_title 的普通标题照常收集，修复不误伤既有路径。"""
    with tempfile.TemporaryDirectory() as tmp:
        rows, _ = _run(tmp, [_node("n1", "重复标题测试用例"),
                             _node("n2", "重复标题测试用例")])
        r = rows.get("重复标题测试用例")
        assert r is not None and r["n"] == 2, rows
        # 同名 2 ⇒ q2=True；q1 False（无边）、q3 False（2<3 且无外部提及）
        # ⇒ 四问唯 Q2 立 ⇒ 隔离区。钉死此判定不随本豁免变化（长标题路径与闸无关）。
        assert r["q2"] is True and r["q1"] is False and r["q3"] is False, r
        assert r["class"] == "隔离区", r["class"]
