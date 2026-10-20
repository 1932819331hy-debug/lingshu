# -*- coding: utf-8 -*-
"""issue #521 守卫：ActivationEngine 的 OPPOSITE 抑制不得单向满额。

病象（修前）：抑制幅度恒为 |EDGE_BASE_DECAY["opposite"]|×cond_match=1.0，且只按
存储方向（register_conflict 新→旧）作用——「后写入」的一方把「先写入」的真命题
满额压出工作态；交换写入顺序，结论随之交换。未复核边（confidence 0.3、
verified=0）与已复核边同权。

修复语义：抑制幅度 × 边 confidence（未复核 0.3 不得满额否决）；抑制按对称关系
双向计算（supp = max(adj_inh·act, adj_inhᵀ·act)），结果与写入顺序无关；edge-list
降级路径同口径。

运行：python -X utf8 tests/test_issue521_activation_conflict_veto.py
自证：python -X utf8 tests/test_issue521_activation_conflict_veto.py --mutate
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from lingshu.core import activation as A  # noqa: E402
from lingshu.core.activation import ActivationEngine  # noqa: E402
from lingshu.core.core import ConditionSpace, SpacetimeMemoryEngine  # noqa: E402

TRUE = ("用户在二零二六年的年度体检报告中确认对青霉素类抗生素过敏，以后开药前"
        "必须提醒医生注意这一点，并在病历首页显著标注，避免出现严重的过敏反应事件")
FALSE = ("用户在二零二六年的年度体检报告中确认对青霉素类抗生素不过敏，以后开药前"
         "必须提醒医生注意这一点，并在病历首页显著标注，避免出现严重的过敏反应事件")
QUERY = "青霉素过敏吗"
# 同一条件空间 ⇒ cond_match=1.0，抑制幅值只由 base×confidence 决定（可复算）。
COND = ConditionSpace("fixture", "fixture", (100.0, 101.0), "synthetic")

_CHECKS = []
FAILS = []


def _check(name, cond, detail=""):
    if name not in _CHECKS:
        _CHECKS.append(name)
    if cond:
        print("  PASS " + name)
    else:
        print("  FAIL " + name + "  " + str(detail)[:240])
        FAILS.append(name)


def _engine(order, conf=None):
    e = SpacetimeMemoryEngine(":memory:")
    for s in order:
        e.add_perception(s, condition_space=COND)
    if conf is not None:
        e.store.conn.execute(
            "UPDATE edges SET confidence=? WHERE relation_type='opposite'", (conf,))
        e.store.conn.commit()
    ids = {n.content: n.id for n in e.store.query_nodes(limit=10)}
    eng = ActivationEngine(store=e.store)
    r = eng.activate(QUERY)
    ws = eng.workset_state("default")
    acts = {}
    for m in ws["members"]:
        if m["node_id"] == ids.get(TRUE):
            acts["TRUE"] = m["activation"]
        elif m["node_id"] == ids.get(FALSE):
            acts["FALSE"] = m["activation"]
    return e, r, acts


def _assertions():
    print("== A 组：写序与置信 ==")
    e, r, acts = _engine([TRUE, FALSE])
    edges = e.store.conn.execute(
        "SELECT relation_type, confidence, verified FROM edges").fetchall()
    _check("A01 前提：M5 自动建 OPPOSITE 边（confidence 0.3、verified 0）",
           len(edges) == 1 and edges[0][0] == "opposite"
           and abs(float(edges[0][1]) - 0.3) < 1e-9 and int(edges[0][2]) == 0,
           [tuple(x) for x in edges])

    e, r, acts = _engine([TRUE, FALSE])
    _check("A02 ★行：TRUE 先写、FALSE 后写——TRUE 仍在工作态",
           "TRUE" in acts and acts["TRUE"] > 0,
           acts)

    e, r, acts = _engine([FALSE, TRUE])
    _check("A03 ★行：FALSE 先写、TRUE 后写——TRUE 仍在工作态",
           "TRUE" in acts and acts["TRUE"] > 0,
           acts)

    base = _engine([TRUE, FALSE])[2]
    e, r, acts = _engine([TRUE, FALSE], conf=1.0)
    # 满额边下 TRUE 必须**显著低于** 0.3 边的读数（证明按置信缩放承重）。
    # 不断言「必被压出」：满额时 hop1 后恰为 0.1667（≥ act_floor 0.15），
    # 是否在 hop2 被压出受浮点/hop 边界影响（Windows 与 Linux CI 实测不同）。
    _check("A04 ★行：满额 1.0 时 TRUE 激活显著低于 0.3 边（证明缩放承重）",
           acts.get("TRUE", 0) < base.get("TRUE", 0) - 0.1,
           (acts, base))

    e, r, acts = _engine([TRUE])
    _check("A05 对照：只有 TRUE 时正常激活（抑制通路不误伤）",
           acts.get("TRUE", 0) > 0.6,
           acts)

    print("== B 组：edge-list 降级 ==")
    saved = A.csr_matrix
    A.csr_matrix = None
    try:
        e1, r1, a1 = _engine([TRUE, FALSE])
        e2, r2, a2 = _engine([FALSE, TRUE])
    finally:
        A.csr_matrix = saved
    _check("B01 ★行：edge-list 降级下两种写序 TRUE 都在（同口径）",
           a1.get("TRUE", 0) > 0 and a2.get("TRUE", 0) > 0,
           (a1, a2))

    print("== E 组：源码钉子 ==")

    def _src(rel):
        try:
            return (_REPO / rel).read_text(encoding="utf-8")
        except OSError:
            return ""

    src = _src("lingshu/core/activation.py")
    _check("E01 ★钉子：抑制幅度乘边置信",
           "inh_mags.append(-d * _c)" in src)
    _check("E02 ★钉子：低置信边稀疏/边表两路径都双向",
           "self._max_plus(self._adj_inh_unv.T, act)" in src
           and "unv_rows, unv_cols, unv_mags = self._inh_unv_meta" in src
           and "supp[si] = max(supp[si], act[di] * m)" in src)


def _run_assertions():
    global FAILS
    FAILS = []
    del _CHECKS[:]
    _assertions()
    print("通过 %d / 失败 %d" % (len(_CHECKS) - len(FAILS), len(FAILS)))
    return len(FAILS)


_ARMS = {
    # 撤置信缩放：T-first 满额否决回来 ⇒ A02 红（E01 钉子按设计同红）。
    "M1": (
        "lingshu/core/activation.py",
        [("                    inh_rows.append(si); inh_cols.append(di); inh_mags.append(-d * _c)",
          "                    inh_rows.append(si); inh_cols.append(di); inh_mags.append(-d)")],
        {"A02", "A04", "B01", "E01"},
    ),
    # 撤稀疏双向：单条 opposite 边场景下「TRUE 是否保留」无可观测差异
    # （置信缩放单独已兜住），由 E02 钉子钉住双向形式；双向的价值在多边对称。
    "M2": (
        "lingshu/core/activation.py",
        [("                    supp = np.maximum(supp, self._max_plus(self._adj_inh_unv.T, act))",
          "                    supp = supp")],
        {"E02"},
    ),
    # 不读边置信（恒 1.0）⇒ 未复核边满额 ⇒ A02 红。
    "M3": (
        "lingshu/core/activation.py",
        [("                if _c < 0.5:",
          "                if False:")],
        {"A02", "A04", "B01"},
    ),
}


def _subprocess_fail_names():
    for cache in _REPO.rglob("__pycache__"):
        for pyc in cache.glob("activation.*.pyc"):
            pyc.unlink(missing_ok=True)
    r = subprocess.run([sys.executable, "-X", "utf8", __file__, "--assert-only"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(_REPO), timeout=900)
    names = set(re.findall(r"(?m)^\s*FAIL\s+([A-Z]\d+)", r.stdout or ""))
    return names, (r.stdout or "") + (r.stderr or "")


def _mutate(argv):
    want = ""
    for a in argv:
        if a == "--mutate":
            want = want or "ALL"
        elif a.startswith("--mutate="):
            want = a.split("=", 1)[1]
    if not want:
        return 0
    ids = sorted(_ARMS) if want == "ALL" else [want]
    red = 0
    for mid in ids:
        if mid not in _ARMS:
            print("[ANCHOR-MISS] %s：未登记该变异臂" % mid)
            return 2
        rel, edits, expect = _ARMS[mid]
        target = _REPO / rel
        orig = target.read_bytes()
        try:
            text = orig.decode("utf-8")
            nl = "\r\n" if "\r\n" in text else "\n"
            norm = text.replace("\r\n", "\n")
            for old, new in edits:
                cnt = norm.count(old)
                if cnt != 1:
                    print("[ANCHOR-MISS] %s：锚点命中 %d 次（期望 1）" % (mid, cnt))
                    return 2
                norm = norm.replace(old, new)
            target.write_bytes(norm.replace("\n", nl).encode("utf-8"))
            actual, out = _subprocess_fail_names()
            if actual == expect:
                print("[MUTATE-OK]   %s：指定断言转红 %s" % (mid, sorted(actual)))
            else:
                print("[MUTATE-FAIL] %s：期望红 %s，实际红 %s"
                      % (mid, sorted(expect), sorted(actual)))
                for line in out.splitlines():
                    if line.strip().startswith("FAIL"):
                        print("    " + line)
                red += 1
        finally:
            target.write_bytes(orig)
    print("反证结果：%d 条变异，%s" % (len(ids), "全部恰好命中" if red == 0 else "%d 条未命中" % red))
    return 1 if red else 0


if __name__ == "__main__":
    _f = _run_assertions()
    if "--assert-only" in sys.argv:
        sys.exit(1 if _f else 0)
    _m = _mutate(sys.argv[1:])
    sys.exit(1 if (_f or _m) else 0)
