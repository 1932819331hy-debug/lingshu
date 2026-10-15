# -*- coding: utf-8 -*-
"""#629 守卫：查看器「只看已锚定」必须 fail-closed——未锚定（含未审计 ncls=''）即隐藏。

缺陷态（修复前）`tools/coggraph/build_viewer.py` 前端过滤判据：
    if(onlyAnch && n.ncls === '隔离区') continue;      # 单值相等
    <input type="checkbox" id="anch" checked>          # 默认勾选
`ncls` 来自 `title2cls.get(title, "")`：naming_report **没有该 row** 的标题
（长度闸挡掉的短标题、未收录标题）落 `''` ⇒ 「未审计」与「已通过审计」在过滤器
眼里同值 ⇒ 默认视图里越短越可见：11 字隔离区标题被正确隐藏，2 字未审计标题留着。
（issue #629，判定线 II 复现成立，origin/main=`6ced89b`。）

修法（本端已裁口径·勿改判＝fail-closed）：单值相等 ⇒ 「未锚定即隐藏」——
只有明确判 `强规范锚`/`已锚定` 的节点可见；`隔离区`/`alias候选`/`''` 一律隐藏，
与「隔离区」语义＋S3 一致；取消勾选仍可看全量。label 同步改口径。
#627 是产出侧（naming_report 不该丢 row）、本件是消费侧（未知态不得当作通过），互不替代。

守卫组：
  G1 数据三态在场：真实跑 naming_report → build_viewer --naming，
     viewer_graph.json 里同时存在 `已锚定`/`隔离区`/`''`（未审计）三态——`''` 态存在
     是本件前提，若 naming 侧将来全部收走标题，本组会提醒口径前提变了；
  G2 前端判据（模板断言，回退即红）：index.html 必含 fail-closed 判据、
     不得残留旧单值相等判据；label 必须与「隐藏未锚定/未审计」语义一致；
  G3 行为模拟（判别力自证）：Python 复算新旧判据在 {强规范锚,已锚定,alias候选,隔离区,''}
     上的可见集——旧判据**放行** `''` 与 `alias候选`（逃逸探针：旧放行），
     新判据**拦截**二者且仍放行 `强规范锚`/`已锚定`（G4 防「一刀砍全隐藏」代偿）；
  G5 #627 交互：修后 `药典`（Q4 词豁免长度闸）拿 `强规范锚` ⇒ 新判据下可见
     ——「fail-closed 不等于误杀规范锚」。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue629_viewer_failclosed_anchor_filter_guard.py -q
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COG = os.path.join(REPO, "tools", "coggraph")
NAMING = os.path.join(COG, "naming_report.py")
VIEWER = os.path.join(COG, "build_viewer.py")

_STUB = "// offline stub\n" + "x" * 100001
_STUB_SHA = hashlib.sha256(_STUB.encode("utf-8")).hexdigest()


def _env():
    return dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                PYTHONIOENCODING="utf-8", LINGSHU_CYTO_SHA256=_STUB_SHA)


def _run(cmd):
    return subprocess.run([sys.executable, "-X", "utf8"] + cmd,
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=REPO, env=_env())


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _node(nid, title, layer="knowledge"):
    return {"id": nid, "title": title, "layer": layer, "bucket": "kb",
            "tags": ["t_" + nid], "importance": 0, "created": 1759930000,
            "path": nid + ".md"}


T_ANCH = "已锚定概念的长标题甲"
T_ISO = "隔离区概念的长标题乙"
T_UNAUD = "记忆方案"      # 4 字、非规范词 ⇒ naming_report 无 row（#627 豁免也不救它）


def _pipeline(tmp):
    """graph → naming_report（真跑）→ build_viewer（离线桩），返回 (out, viewer_graph, html)。"""
    nodes = [_node("a1", T_ANCH), _node("a2", T_ANCH), _node("a3", T_ANCH),   # 3 名组+显式边 ⇒ 已锚定
             _node("i1", T_ISO),                                              # 孤立长标题 ⇒ 隔离区
             _node("u1", T_UNAUD),                                            # 短非规范词 ⇒ ncls=''
             _node("n1", "药典")]                                             # 短规范锚词 ⇒ #627 修后强规范锚
    graph = os.path.join(tmp, "graph.json")
    with io.open(graph, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"meta": {"counts": {"nodes": len(nodes), "edges": 3},
                            "source_sha": "cafe1234"},
                   "nodes": nodes,
                   "edges": [{"s": "a1", "t": "a2", "type": "rel", "derived": False},
                             {"s": "a2", "t": "a3", "type": "rel", "derived": False},
                             {"s": "a1", "t": "a3", "type": "rel", "derived": False}]},
                  f, ensure_ascii=False)
    derived = os.path.join(tmp, "derived.json")
    with io.open(derived, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"meta": {}, "hubs": [], "edges": []}, f, ensure_ascii=False)
    nout = os.path.join(tmp, "nout")
    os.makedirs(nout)
    p = _run([NAMING, "--graph", graph, "--derived", derived, "--out", nout, "--write"])
    assert p.returncode == 0, p.stderr[-300:]
    naming_json = os.path.join(nout, "naming_report.json")
    out = os.path.join(tmp, "out")
    os.makedirs(out)
    _write(os.path.join(out, "cytoscape.min.js"), _STUB)
    p = _run([VIEWER, "--graph", graph, "--derived", derived, "--out", out,
              "--naming", naming_json])
    assert p.returncode == 0, (p.stdout[-300:], p.stderr[-300:])
    with io.open(os.path.join(out, "viewer_graph.json"), encoding="utf-8") as f:
        vg = json.load(f)
    html = io.open(os.path.join(out, "index.html"), encoding="utf-8").read()
    return vg, html


def _ncls_of(vg, nid):
    for n in vg["nodes"]:
        if n["id"] == nid:
            return n["ncls"]
    raise AssertionError("viewer_graph 无节点 %s" % nid)


def test_g1_three_states_present_in_viewer_graph():
    """G1：数据侧三态（已锚定/隔离区/未审计''）同时在场——'' 态存在是本件前提。"""
    with tempfile.TemporaryDirectory() as tmp:
        vg, _ = _pipeline(tmp)
        assert _ncls_of(vg, "a1") == "已锚定", _ncls_of(vg, "a1")
        assert _ncls_of(vg, "i1") == "隔离区", _ncls_of(vg, "i1")
        assert _ncls_of(vg, "u1") == "", \
            "短非规范标题在 viewer 里不再是 '' 态——前提变了，本守卫口径须随裁更新"
        assert _ncls_of(vg, "n1") == "强规范锚", _ncls_of(vg, "n1")  # #627 已修：药典参评


def test_g2_frontend_predicate_is_fail_closed():
    """G2：index.html 的判据必须是 fail-closed 白名单式；旧单值相等判据不得残留。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, html = _pipeline(tmp)
        assert "n.ncls === '隔离区') continue;" not in html, \
            "#629 缺陷形态残留：onlyAnch 仍用单值相等（未审计 '' 逃过滤）"
        assert "if(onlyAnch && n.ncls !== '强规范锚' && n.ncls !== '已锚定') continue;" in html, \
            "fail-closed 判据缺失（未锚定即隐藏）"
        assert "只看已锚定（未锚定/未审计一律隐藏）" in html, \
            "label 口径未与 fail-closed 对齐"
        assert 'id="anch" checked' in html, "默认勾选语义不应变（改的是判据不是默认值）"


def test_g3_old_leaks_new_blocks_simulation():
    """G3：Python 复算新旧判据——旧放行 ''/alias候选（逃逸探针），新拦截且不误杀锚定态。"""
    def old_onlyanch(ncls):        # 旧：隐藏条件 ncls === '隔离区'
        return ncls != "隔离区"

    def new_onlyanch(ncls):        # 新：隐藏条件 ncls !== '强规范锚' && ncls !== '已锚定'
        return ncls in ("强规范锚", "已锚定")

    # 缺陷自证：旧判据下未审计态通过过滤（= 默认可见），本夹具 u1 因此逃过隐藏
    assert old_onlyanch("") is True, "旧判据必须放行 ''（否则本件判别力为恒真假守卫）"
    assert old_onlyanch("alias候选") is True, "旧判据放行 alias候选（同为未锚定态）"
    # 修复语义：未锚定即隐藏
    assert new_onlyanch("") is False, "fail-closed：未审计必须视作未锚定 ⇒ 隐藏"
    assert new_onlyanch("alias候选") is False, "fail-closed：alias候选未锚定 ⇒ 隐藏"
    assert new_onlyanch("隔离区") is False
    # G4（防一刀砍全隐藏代偿）：锚定态仍可见
    assert new_onlyanch("强规范锚") is True
    assert new_onlyanch("已锚定") is True
    # 可见集逐字钉住
    states = ["强规范锚", "已锚定", "alias候选", "隔离区", ""]
    assert {s for s in states if old_onlyanch(s)} == {"强规范锚", "已锚定", "alias候选", ""}
    assert {s for s in states if new_onlyanch(s)} == {"强规范锚", "已锚定"}


def test_g5_norm_anchor_still_visible_after_627_interaction():
    """G5：#627 修后「药典」=强规范锚，在 fail-closed 判据下仍可见（不误杀规范锚）。"""
    with tempfile.TemporaryDirectory() as tmp:
        vg, html = _pipeline(tmp)
        assert _ncls_of(vg, "n1") == "强规范锚"
        # 模板里的白名单必须同时枚举两个锚定态（只留一个=过修，回滚到旧=过松，皆红）
        line = [l.strip() for l in html.split("\n")
                if l.strip().startswith("if(onlyAnch && n.ncls !==")]
        assert len(line) == 1 and "强规范锚" in line[0] and "已锚定" in line[0], line
