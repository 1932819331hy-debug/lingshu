# -*- coding: utf-8 -*-
"""#628 守卫：synonym_merge 的 importance 非数值（None/字符串档位）不得崩整轮。

缺陷态（修复前 main）`tools/coggraph/synonym_merge.py`：
  · 取数行 `"imp": n.get("importance", 0)` 的默认值只在**键缺席**时生效；
    真源 `importance:` 空值被 export 透传成 **None**（键在、值 None）⇒ 默认值不兜。
  · 归并组代表选取 `sorted(v, key=lambda c: (-c["imp"], ...))` 对 None 取负
    ⇒ `TypeError: unary -: 'NoneType'`，rc=1 整轮崩（--write 不落盘、下游静默无产物）；
    字符串档位（「高」「低」）同理 `unary -: 'str'`。

修法（消费端闸，与 created_to_ts 坏值口径同）：`_imp()` 把非数值/NaN/inf 回落 0，
  正常数值（含 bool 子类）如实放行——坏值不崩、不占排序优势，好值零回归。

守卫组：
  G1 None+字符串混合组不崩（rc=0），canon 由正常数值侧（imp=5）胜出——
     修复前此处 rc=1 TypeError（本夹具有判别力）；
  G2 纯数值零回归：7 对 3 仍 7 侧胜出（防过修：正常数值语义不动）；
  G3 NaN/inf 坏值回落 0 不崩、正常数值胜出；
  G4 bool（int 子类、-True 合法）不被误伤：True=1 参与排序、5 仍胜出。

变异点（双向读数）：
  · 把 `"imp": _imp(n.get("importance"))` 改回 `n.get("importance", 0)`
    ⇒ G1/G3 必红（TypeError 复现），恢复必绿；
  · 把 _imp 改成「凡数值也一律回 0」⇒ G2 必红（canon 由 raw 字典序决定，7 侧不再稳胜），
    钉死「不过修」。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue628_synonym_none_imp_guard.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(REPO, "tools", "coggraph", "synonym_merge.py")


def _node(nid, imp):
    n = {"id": nid, "title": "同题测试", "layer": "knowledge", "bucket": "concept",
         "tags": [], "path": nid + ".md"}
    if imp != "__omit__":
        n["importance"] = imp
    return n


def _run(nodes):
    d = tempfile.mkdtemp(prefix="imp628_")
    g = os.path.join(d, "g.json")
    with open(g, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes, "edges": []}, f, ensure_ascii=False)
    out = os.path.join(d, "o")
    p = subprocess.run([sys.executable, "-X", "utf8", _SCRIPT,
                        "--graph", g, "--out", out, "--write"],
                       capture_output=True, text=True, encoding="utf-8")
    payload = None
    j = os.path.join(out, "synonym_groups.json")
    if os.path.exists(j):
        with open(j, encoding="utf-8") as f:
            payload = json.load(f)
    return p.returncode, p.stderr, payload


def test_g1_none_and_str_importance_do_not_crash():
    """G1：None/字符串档位混合 ⇒ rc=0 且正常数值侧胜出（修复前 rc=1 TypeError）。"""
    rc, err, payload = _run([_node("a", None), _node("b", 5), _node("c", "高")])
    assert rc == 0, "整轮崩复现：rc=%d stderr 尾=%r" % (rc, err[-200:])
    assert payload and payload["groups"], "修复后应产出归并组"
    grp = payload["groups"][0]
    assert grp["canonical_id"] == "b", (
        "canon 应由正常数值 imp=5 的 b 胜出（None/「高」回落 0），实得 %r" % grp["canonical_id"])


def test_g2_numeric_ranking_zero_regression():
    """G2：纯数值语义不动——7 对 3 仍 7 侧（b）胜出。"""
    rc, err, payload = _run([_node("a", 3), _node("b", 7)])
    assert rc == 0, err[-200:]
    assert payload["groups"][0]["canonical_id"] == "b", "正常数值排序被过修"


def test_g3_nan_inf_fall_back_not_crash():
    """G3：NaN/inf 归坏值回落 0，不崩、正常数值胜出。"""
    rc, err, payload = _run([_node("a", float("nan")), _node("b", float("inf")),
                             _node("c", 2)])
    assert rc == 0, err[-200:]
    assert payload["groups"][0]["canonical_id"] == "c", "NaN/inf 未回落 0"


def test_g4_bool_not_over_narrowed():
    """G4：bool 是 int 子类且取负合法（报告矩阵实测正常）⇒ 如实放行，5 仍胜 True(=1)。"""
    rc, err, payload = _run([_node("a", True), _node("b", 5)])
    assert rc == 0, err[-200:]
    assert payload["groups"][0]["canonical_id"] == "b"
