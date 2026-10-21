# -*- coding: utf-8 -*-
"""#611 守卫：synonym_merge 不得剥整段括号注释；kind=duplicate 仅限真·同标题。

缺陷态（修复前 HEAD）`tools/coggraph/synonym_merge.py`：
  · `ANNO`（`[（(][^）)]*[）)]`）在 deep_norm 里被 `ANNO.sub("", t)` **整段删除**
    ⇒「记忆写入（长期）」与「记忆写入（短期）」规范化后同为「记忆写入」⇒ L1 撞车
    被 union 并组——括号里是**互斥的维度值/版本/单位**，恰恰是最不该合并的内容。
  · kind 判定用 `norms`（规范化后）兜底：原文 2 个不同标题但 norm 撞车 ⇒ 误判
    `kind="duplicate"`，与注释/报告措辞「完全重复组（同一标题多节点）」自相矛盾。
  · 下游：build_viewer --merge-synonyms 不分 kind/rule 建别名映射 ⇒ 2 节点被真并成 1
    （消失的是「（短期）」侧）。本件只修 synonym_merge 判定面；#410 的 viewer 侧
    `rule != "L1"` 过滤不在本件范围——但本件修后 L1 假 duplicate 源头已断。

守卫（G1-G4 + 既有边界）：
  G1 互斥维度 4 对（长期/短期、旧版/新版、毫秒/微秒、旧/待复核）不再归并成组
     （无 L1 撞车；落 L3 待复核是「只进待复核不自动归并」的既定保守通道，允许）；
  G2 同值异空白对（（长期）vs（ 长期 ））仍归并（norm 一致）且 kind 不是 duplicate
     （原文标题不同）；
  G3 真·同标题对照组仍 kind=duplicate；
  G4 端到端 --merge-synonyms：互斥维度 2 节点在 viewer 里**不再被并掉**（节点数保持
     2）；真同标题/同值对按修复后语义如实归并（1 节点）；#410 的 L2 语序变体 case
     （教师评价学生/学生评价教师 ⇒ kind=synonym rule=L1+L2）判定零回归；
  既有边界（c）：含 ASCII 的标题（解析器v1/v2、退火温度（0.7）/（0.9））整条跳过，
     零改动。

变异点（两向读数）：
  · 把 deep_norm 的 ANNO 行改回 `t = ANNO.sub("", t)` ⇒ G1（test_g1_*）与
    G4（test_g4_viewer_keeps_exclusive_pair_as_two_nodes）必红，恢复必绿；
  · 把 kind 判定 else 分支改回 `kind = "synonym" if len(norms) > 1 else "duplicate"`
    ⇒ G2 必红（同值异空白对退回 duplicate 误标），恢复必绿。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue611_synonym_anno_guard.py -q
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(REPO, "tools", "coggraph", "synonym_merge.py")
_BUILD_VIEWER = os.path.join(REPO, "tools", "coggraph", "build_viewer.py")

# 互斥维度值/版本/单位：括号内容不同 ⇒ 语义互斥 ⇒ 绝不允许并组
EXCLUSIVE_PAIRS = [
    ("记忆写入（长期）", "记忆写入（短期）"),
    ("推理引擎（旧版）", "推理引擎（新版）"),
    ("采样率（毫秒）", "采样率（微秒）"),
    ("记忆写入（旧）", "记忆写入（待复核）"),
]


def _load_module():
    spec = importlib.util.spec_from_file_location("_synonym_merge_611", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MOD = _load_module()


def _write_graph(path, titles):
    """graph.json 夹具：synonym_merge 与 build_viewer 都能消费的同构格式。"""
    nodes = [{"id": "n%d" % i, "title": t, "layer": "knowledge", "bucket": "concept",
              "tags": [], "importance": 0.5, "path": "n%d.md" % i}
             for i, t in enumerate(titles)]
    doc = {"meta": {"counts": {"nodes": len(nodes), "edges": 0}, "source_sha": "test611"},
           "nodes": nodes, "edges": []}
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False)


def _run_merge(titles, out_dir):
    """跑 synonym_merge.main(--write) → (stdout JSON 摘要, synonym_groups.json)。"""
    d = tempfile.mkdtemp(prefix="syn611_")
    gp = os.path.join(d, "g.json")
    _write_graph(gp, titles)
    out = os.path.join(d, out_dir)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _MOD.main(["--graph", gp, "--out", out, "--write"])
    assert rc == 0
    summary = None
    for ln in buf.getvalue().splitlines():
        if ln.startswith("{"):
            summary = json.loads(ln)
    assert summary is not None, "没拿到 JSON 摘要：\n" + buf.getvalue()
    with io.open(os.path.join(out, "synonym_groups.json"), encoding="utf-8") as f:
        detail = json.load(f)
    return gp, summary, detail


def _run_viewer(graph, syn_path):
    """build_viewer --merge-synonyms 端到端（离线：cytoscape 桩 + 声明指纹）。"""
    d = tempfile.mkdtemp(prefix="view611_")
    out = os.path.join(d, "out")
    os.makedirs(out, exist_ok=True)
    stub = "// offline stub\n" + "x" * 100001
    with io.open(os.path.join(out, "cytoscape.min.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(stub)
    sha = hashlib.sha256(stub.encode("utf-8")).hexdigest()
    # #620：桩为非官方副本 ⇒ 自备轨须经显式开关（夹具语义不变）
    cmd = [sys.executable, "-X", "utf8", _BUILD_VIEWER,
           "--graph", graph, "--out", out, "--synonyms", syn_path, "--merge-synonyms",
           "--allow-unofficial-cyto"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LINGSHU_CYTO_SHA256=sha)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       cwd=REPO, env=env)
    assert p.returncode == 0, (p.stdout[-300:], p.stderr[-300:])
    with io.open(os.path.join(out, "viewer_graph.json"), encoding="utf-8") as f:
        return json.load(f)


# ── G1：互斥维度值不再因括号被剥而撞车 ───────────────────────────────────────

def test_g1_deep_norm_keeps_bracket_discriminative():
    """括号注释必须保留判别力：互斥值规范化后**不同**（缺陷态同为「记忆写入」）。"""
    for a, b in EXCLUSIVE_PAIRS:
        na, nb = _MOD.deep_norm(a), _MOD.deep_norm(b)
        assert na != nb, (
            "#611：括号被整段剥掉 ⇒ %r 与 %r 规范化撞车（%r）⇒ L1 假重复" % (a, b, na))


def test_g1_exclusive_dimension_pairs_not_merged():
    """互斥维度 4 对：不归并成组、不计 L1（落 L3 待复核属保守通道，允许）。"""
    for a, b in EXCLUSIVE_PAIRS:
        _, s, det = _run_merge([a, b], "out")
        assert not det["groups"], (
            "#611：互斥维度对 %r/%r 仍被并组（读数 %r，组 %r）" % (a, b, s, det["groups"]))
        assert s["stats"].get("L1_规范化后相同", 0) == 0, (
            "#611：%r/%r 仍 L1 撞车（读数 %r）" % (a, b, s))


# ── G2：同值异空白仍等价，且不再误标 duplicate ───────────────────────────────

def test_g2_deep_norm_normalizes_whitespace_inside_brackets():
    """同值异空白等价：「记忆写入（ 长期 ）」≡「记忆写入（长期）」（norm 一致）。"""
    assert _MOD.deep_norm("记忆写入（ 长期 ）") == _MOD.deep_norm("记忆写入（长期）"), (
        "#611 过修：括号内空白归一失效 ⇒ 同值异空白对不再等价")


def test_g2_same_value_whitespace_pair_merged_as_synonym():
    """同值异空白对仍归并（L1），但原文标题不同 ⇒ kind 必须不是 duplicate。"""
    _, s, det = _run_merge(["记忆写入（长期）", "记忆写入（ 长期 ）"], "out")
    assert len(det["groups"]) == 1, "同值异空白对应仍归并成 1 组（读数 %r）" % (s,)
    g0 = det["groups"][0]
    assert g0["kind"] != "duplicate", (
        "#611：原文标题不同（%r）却被标 duplicate——「同一标题多节点」措辞失真"
        % [m["title"] for m in g0["members"]])
    assert g0["rule"] == "L1", "norm 一致应走 L1（实得 %r）" % g0["rule"]


# ── G3：真·同标题对照 ───────────────────────────────────────────────────────

def test_g3_true_same_title_control_still_duplicate():
    """对照组：两节点标题完全相同 ⇒ kind=duplicate（该语义只属于真·同标题）。"""
    _, s, det = _run_merge(["记忆写入", "记忆写入"], "out")
    assert len(det["groups"]) == 1, "真同标题对照组应成 1 组（读数 %r）" % (s,)
    g0 = det["groups"][0]
    assert g0["kind"] == "duplicate", (
        "真·同标题对照组必须仍是 duplicate（实得 %r，读数 %r）" % (g0["kind"], s))
    assert s["分组"].get("duplicate") == 1


# ── 既有边界（c）：含 ASCII 整条跳过，零改动 ────────────────────────────────

def test_ascii_boundary_titles_still_skipped():
    """「解析器v1/v2」「退火温度（0.7）/（0.9）」含 ASCII ⇒ 整条跳过、不归并。"""
    for a, b in (("解析器v1", "解析器v2"), ("退火温度（0.7）", "退火温度（0.9）")):
        _, s, det = _run_merge([a, b], "out")
        assert not det["groups"] and s["候选"] == 0, (
            "ASCII 既有边界被改动：%r/%r（读数 %r）" % (a, b, s))


# ── G4：--merge-synonyms 端到端（防过修/防回归）─────────────────────────────

def test_g4_viewer_keeps_exclusive_pair_as_two_nodes():
    """互斥维度 2 节点在 viewer 里不再被并掉：节点数保持 2、无别名重映射。"""
    titles = ["记忆写入（长期）", "记忆写入（短期）"]
    gp, s, det = _run_merge(titles, "out")
    assert not det["groups"], "夹具前置：互斥对应无归并组（读数 %r）" % (s,)
    vg = _run_viewer(gp, os.path.join(os.path.dirname(gp), "out", "synonym_groups.json"))
    titles_kept = sorted(n["title"] for n in vg["nodes"])
    assert len(vg["nodes"]) == 2 and titles_kept == sorted(titles) and not vg.get("merged_aliases"), (
        "#611 端到端：互斥维度对仍被 build_viewer 并掉（节点 %r，merged_aliases=%r）"
        % (titles_kept, vg.get("merged_aliases")))


def test_g4_viewer_fixed_semantics_same_value_and_true_same_title():
    """修复后语义如实记录：同值异空白对、真同标题对在 --merge-synonyms 下并成 1 节点。"""
    # 同值异空白：同值该并 ⇒ 1 节点
    gp, s, det = _run_merge(["记忆写入（长期）", "记忆写入（ 长期 ）"], "out")
    assert len(det["groups"]) == 1 and det["groups"][0]["kind"] != "duplicate"
    vg = _run_viewer(gp, os.path.join(os.path.dirname(gp), "out", "synonym_groups.json"))
    assert len(vg["nodes"]) == 1 and vg.get("merged_aliases") == 1, (
        "同值异空白对应按修复后语义归并为 1 节点（实得 %r / %r）"
        % (len(vg["nodes"]), vg.get("merged_aliases")))
    # 真同标题：duplicate ⇒ 1 节点（既有行为不变）
    gp, s, det = _run_merge(["记忆写入", "记忆写入"], "out")
    assert det["groups"] and det["groups"][0]["kind"] == "duplicate"
    vg = _run_viewer(gp, os.path.join(os.path.dirname(gp), "out", "synonym_groups.json"))
    assert len(vg["nodes"]) == 1 and vg.get("merged_aliases") == 1


def test_g4_l2_word_order_case_zero_regression():
    """#410 的 L2 语序变体 case 判定零回归：kind=synonym、rule=L1+L2、viewer 仍并。"""
    _, s, det = _run_merge(["教师评价学生", "学生评价教师"], "out")
    assert len(det["groups"]) == 1, "L2 语序变体应归并成 1 组（读数 %r）" % (s,)
    g0 = det["groups"][0]
    assert g0["kind"] == "synonym" and g0["rule"] == "L1+L2", (
        "#410 L2 case 判定回归：kind=%r rule=%r（读数 %r）" % (g0["kind"], g0["rule"], s))
    assert s["stats"].get("L2_语序变体", 0) >= 1


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))
