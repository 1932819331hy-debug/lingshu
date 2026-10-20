# -*- coding: utf-8 -*-
"""守卫 · `_vprim_context_for_claim` 的**扫描入口**必须收负号坐标（lingshu issue #718 面）

缺陷来源（第三方复核，非本仓自述）
    复核方 ybyz2 于 2026-10-16T04:55:48Z 在 PR #742 线程指出：#718「视觉锚点可写
    不可读」在 `lingshu/core/core.py::SpacetimeMemoryEngine._vprim_context_for_claim`
    里**原样保留** —— 该函数**自带第二份坐标正则副本**、不走 `parse_anchor`：

        for m in _re.finditer(r"[\\w\\-]+@\\(\\d+,\\d+,\\d+,\\d+\\)", claim):

    坐标组写死 `\\d+`（无 `-?`），而 `lingshu/world/vprim.py::parse_anchor` 早在
    #730 就收了 `-?\\d+`。⇒ 负坐标锚点连 `finditer` 都过不了（`regex_hits=0`），
    根本没机会进 `parse_anchor`；**全负坐标 claim 的空间关系表整体失效**（返回
    None，静默、不报错）。根因＝**单点真源被破坏**（同一格式在两侧各有一份定义，
    读回侧单方面收窄）。

修法（单一真源）
    `lingshu/world/vprim.py` 增模块级 `ANCHOR_COORD_PATTERN`（坐标组，含 `-?`）
    与 `ANCHOR_SCAN_PATTERN`（扫描形态，由前者拼出）；`parse_anchor` 与 core 的
    **既有惰性导入处**都从该常量取用 ⇒ 不再存在可各自收窄格式的第二份副本。
    **不**动 `MAX_VPRIM_ANCHORS`、截断上报语义、关系计算与返回体形状。

红→绿读数（隔离 worktree，基线 `origin/main` = 5c530ff；实跑原始输出）
    修前（生产实现）：全负 ⇒ None（`regex_hits=0`）｜全正 ⇒ dict/anchors=2/rel=1
    ｜混合 ⇒ None（`regex_hits=1`）；同夹具 `parse_anchor("cat@(-10,-20,-30,-40)")`
    ⇒ `VPrim(bbox=(-10,-20,-30,-40))`（**单点解析没坏** —— 隔离变量，坏的是扫描入口）。
    修后：三组全 dict/anchors=2/relations=1，`regex_hits=2/2/2`；三遍重跑一致。

断言组
    A 全负（与混合符号）claim ⇒ 非 None、`anchors` 长度 2、关系表非空（**修前必红**）
    B 全正 claim ⇒ 与修前**逐字相同**（`PRE_FIX_POSITIVE_CONTEXT`，防过修）
    C 单锚点 ⇒ 仍返回 None（`len(anchors) < 2` 的既有语义不得变）
    D 超限面（> `MAX_VPRIM_ANCHORS` 个负锚点）⇒ `truncated=True` 且 `total_matches`
      如实计数（放宽坐标**不得**改变截断语义）
    E 单一真源：core 该函数体内**不得**再有内联坐标组副本（符号锚 + AST 取字符串
      常量，**禁手写行号锚**），且 `lingshu/**` 全域不再出现旧式无符号坐标链

`--mutate` 变异自证（经**生产调用路径**，测试件内不复制正则实现）
    把 core.py 的扫描形态还原成修前那份**内联无符号副本**（＝「把 `-?` 拿掉」且
    「只改 core.py 那份」）⇒ 依赖负坐标入扫描的断言点红、正坐标面（B/C）不动。
    红集由 `EXPECTED_RED` 声明并与**实跑结果做相等断言**（不匹配即本 harness 自身红）。

运行（仓根）
    python -X utf8 -m pytest tests/test_issue718_vprim_scan_negative_anchor_guard.py -q --no-header
    变异自证：python -X utf8 tests/test_issue718_vprim_scan_negative_anchor_guard.py --mutate
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402
from lingshu.world import vprim as vprim_mod  # noqa: E402

#: 全负坐标 claim（#742 线程点名的形态）
NEG_CLAIM = "cat@(-10,-20,-30,-40)；dog@(-5,-7,-25,-37)"
#: 全正坐标 claim（正对照）
POS_CLAIM = "cat@(10,20,30,40)；dog@(5,7,25,37)"
#: 混合符号 claim（一负一正）
MIX_CLAIM = "cat@(-10,-20,-30,-40)；dog@(5,7,25,37)"

#: 修前（`origin/main` = 5c530ff）全正 claim 的**逐字**读数（实跑取得）——
#: B 用它防过修：放宽负号不得让非负域的任何一位发生变化。
PRE_FIX_POSITIVE_CONTEXT = {
    "anchors": ["cat@(10,20,30,40)", "dog@(5,7,25,37)"],
    "relations": [{"a": "cat", "b": "dog", "relation": "overlap", "distance": 9.4}],
    "truncated": False,
    "total_matches": 2,
    "anchor_limit": 32,
    "note": "确定性空间原语（VPRIM-REV1）：坐标精确性替代语言近似",
}

#: 旧式（无符号）坐标链 —— 扫描/解析两侧都**不得**再出现
LEGACY_COORD_CHAIN = "\\d+,\\d+,\\d+,\\d+"
#: 旧式扫描形态全文（#718 缺陷原文）
LEGACY_SCAN_LITERAL = "[\\w\\-]+@\\(\\d+,\\d+,\\d+,\\d+\\)"


def _engine() -> SpacetimeMemoryEngine:
    return SpacetimeMemoryEngine(":memory:")


def _neg_claim(k: int) -> str:
    """k 个**全负**坐标锚点组成的 claim（每个锚点四坐标皆负）。"""
    return "；".join("obj%d@(-%d,-%d,-%d,-%d)"
                     % (i, i + 1, i + 2, i + 3, i + 4) for i in range(k))


def _non_docstring_string_constants(source: str):
    """AST 取源码里的字符串常量，**排除 docstring**（注释不入 AST，天然免疫）。"""
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_nodes.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in doc_nodes:
            out.append(node.value)
    return out


# ==================== A 核心：负坐标必须进扫描入口 ====================

def test_a_全负坐标claim必须产出空间关系():
    """★ 核心：全负坐标 claim 必须产出非 None 的空间上下文（修前返回 None）。

    修前形态：扫描正则写死 `\\d+` ⇒ 负坐标锚点 `regex_hits=0` ⇒ `anchors` 为空
    ⇒ `len(anchors) < 2` ⇒ None（静默失效）。
    """
    eng = _engine()
    ctx = eng._vprim_context_for_claim(NEG_CLAIM)
    assert ctx is not None, (
        "A 全负坐标 claim 未产出上下文（扫描入口把负号锚点滤掉了）")
    assert ctx["anchors"] == ["cat@(-10,-20,-30,-40)", "dog@(-5,-7,-25,-37)"], (
        "A 负坐标锚点未如实入列：%r" % (ctx["anchors"],))
    assert len(ctx["anchors"]) == 2, "A 锚点数不符：%r" % (len(ctx["anchors"]),)
    assert ctx["relations"], "A 关系表为空（空间上下文整体失效）：%r" % (ctx,)
    assert len(ctx["relations"]) == 1, "A 关系数不符：%r" % (len(ctx["relations"]),)
    assert ctx["total_matches"] == 2, "A 匹配计数不符：%r" % (ctx["total_matches"],)
    assert ctx["truncated"] is False, "A 未超限却报截断：%r" % (ctx["truncated"],)


def test_a2_混合符号claim必须产出空间关系():
    """★ 核心（同一缺陷面）：一负一正时，负锚点被滤掉 ⇒ `anchors` 只剩 1 个 ⇒ None。"""
    eng = _engine()
    ctx = eng._vprim_context_for_claim(MIX_CLAIM)
    assert ctx is not None, (
        "A2 混合符号 claim 未产出上下文（负锚点被扫描入口滤掉）")
    assert len(ctx["anchors"]) == 2, "A2 锚点数不符：%r" % (ctx["anchors"],)
    assert ctx["anchors"] == ["cat@(-10,-20,-30,-40)", "dog@(5,7,25,37)"], (
        "A2 锚点文本不符：%r" % (ctx["anchors"],))
    assert len(ctx["relations"]) == 1, "A2 关系数不符：%r" % (ctx["relations"],)
    assert ctx["total_matches"] == 2, "A2 匹配计数不符：%r" % (ctx["total_matches"],)


# ==================== B 防过修：非负域逐字不变 ====================

def test_b_全正坐标输出与修前逐字相同():
    """防过修：全正 claim 的返回体必须与修前**逐字相同**（含 relations/note/上限）。"""
    eng = _engine()
    ctx = eng._vprim_context_for_claim(POS_CLAIM)
    assert ctx == PRE_FIX_POSITIVE_CONTEXT, (
        "B 非负域输出被本次改动波及：\n实得 %r\n期望 %r"
        % (ctx, PRE_FIX_POSITIVE_CONTEXT))
    assert eng._vprim_context_for_claim(POS_CLAIM) == ctx, "B 同输入两次读数不一致"


# ==================== C 既有语义：单锚点仍 None ====================

def test_c_单锚点仍返回None():
    """C：`len(anchors) < 2` 的既有语义不得变——单锚点（含负坐标单锚点）仍返回 None。"""
    eng = _engine()
    assert eng._vprim_context_for_claim("cat@(-1,-2,-3,-4)") is None, (
        "C 负坐标单锚点语义被改动")
    assert eng._vprim_context_for_claim("cat@(1,2,3,4)") is None, (
        "C 正坐标单锚点语义被改动")
    assert eng._vprim_context_for_claim("无锚点的一句话") is None, (
        "C 零锚点语义被改动")


# ==================== D 超限面：截断语义不得变 ====================

def test_d_超限负锚点截断语义不变():
    """D：> `MAX_VPRIM_ANCHORS` 个**负**锚点 ⇒ `truncated=True` 且总数如实上报。

    放宽坐标收负号**不得**顺带改变截断语义（#426 面）：`anchors` 封顶在上限、
    `relations` 封顶在 C(上限,2)、`total_matches` 报**实际匹配数**。
    """
    eng = _engine()
    cap = eng.MAX_VPRIM_ANCHORS
    k = cap * 4
    ctx = eng._vprim_context_for_claim(_neg_claim(k))
    assert ctx is not None, "D 超限负锚点 claim 未产出上下文"
    assert len(ctx["anchors"]) == cap, (
        "D 锚点数未封顶：%r != %r" % (len(ctx["anchors"]), cap))
    assert len(ctx["relations"]) == cap * (cap - 1) // 2, (
        "D 关系数未封顶：%r" % (len(ctx["relations"]),))
    assert ctx["truncated"] is True, "D 截断未上报：%r" % (ctx["truncated"],)
    assert ctx["total_matches"] == k, (
        "D 实际匹配数未如实上报：%r != %r" % (ctx["total_matches"], k))
    assert ctx["anchor_limit"] == cap, "D 上限值未随结果回报：%r" % (ctx["anchor_limit"],)
    assert ctx["anchors"][0] == "obj0@(-1,-2,-3,-4)", (
        "D 首个负锚点文本不符：%r" % (ctx["anchors"][0],))


# ==================== E 单一真源（防第二份副本复发） ====================

def test_e_扫描形态单一真源不得再有第二份副本():
    """E：坐标组只有一个真源；core 该函数体内不得再内联坐标组副本。

    判据（**符号锚**，禁手写行号）：
      ① `vprim.ANCHOR_COORD_PATTERN` 收负号，且 `ANCHOR_SCAN_PATTERN` 由它拼出
         （出现次数 = 4）；
      ② core 的 `_vprim_context_for_claim` 体内**引用**该常量，且其字符串常量里
         **不出现**旧式无符号坐标链（AST 取常量、排除 docstring）；
      ③ `lingshu/**` 全域的非 docstring 字符串常量里不再出现旧式无符号坐标链。
    """
    # ① 真源本身收负号，且扫描形态与解析形态同源
    assert "-?" in vprim_mod.ANCHOR_COORD_PATTERN, (
        "E 真源坐标组未收负号：%r" % (vprim_mod.ANCHOR_COORD_PATTERN,))
    assert vprim_mod.ANCHOR_SCAN_PATTERN.count(vprim_mod.ANCHOR_COORD_PATTERN) == 4, (
        "E 扫描形态未由真源坐标组拼出（副本复发）：%r"
        % (vprim_mod.ANCHOR_SCAN_PATTERN,))
    assert "-?" in vprim_mod.ANCHOR_SCAN_PATTERN, (
        "E 扫描形态未收负号：%r" % (vprim_mod.ANCHOR_SCAN_PATTERN,))

    # ② core 取用真源，且不再内联坐标组
    src = inspect.getsource(SpacetimeMemoryEngine._vprim_context_for_claim)
    assert "ANCHOR_SCAN_PATTERN" in src, (
        "E core 未取用扫描形态真源（仍自带副本？）")
    inlined = [s for s in _non_docstring_string_constants(src)
               if LEGACY_COORD_CHAIN in s or LEGACY_SCAN_LITERAL in s]
    assert not inlined, "E core 仍内联坐标组副本：%r" % (inlined,)

    # ③ 全域：旧式无符号坐标链已无第二处
    offenders = []
    for path in sorted((REPO / "lingshu").rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
            consts = _non_docstring_string_constants(text)
        except SyntaxError as exc:  # 解析失败如实上报，不静默跳过
            offenders.append((str(path), "AST 解析失败: %s" % (exc,)))
            continue
        for s in consts:
            if LEGACY_COORD_CHAIN in s or LEGACY_SCAN_LITERAL in s:
                offenders.append((str(path), s[:120]))
    assert not offenders, "E 旧式无符号坐标副本仍存在：%r" % (offenders,)

    # ④ 形态自证：真源形态**确实**匹配负坐标锚点、且不匹配畸形串
    pat = re.compile(vprim_mod.ANCHOR_SCAN_PATTERN)
    assert [m.group(0) for m in pat.finditer(NEG_CLAIM)] == [
        "cat@(-10,-20,-30,-40)", "dog@(-5,-7,-25,-37)"], "E 真源形态读不回负坐标锚点"
    for junk in ("obj@(1.5,2.5,3.5,4.5)", "obj@(-,2,3,4)", "obj@(1,2,3)"):
        assert not pat.search(junk), "E 真源形态过宽，吃进了畸形串：%r" % (junk,)


# ==================== --mutate 变异自证 ====================

#: 变异体（＝修前 core.py 的扫描形态）下**应当**点红的用例。
#: A/A2 与 D 都依赖「负坐标锚点能进扫描入口」；E 钉住「不得再有第二份副本」——
#: 本变异正是把第二份副本放回去，故 E 亦红。B/C 覆盖非负域与既有语义，不得被波及。
EXPECTED_RED = frozenset({
    "test_a_全负坐标claim必须产出空间关系",
    "test_a2_混合符号claim必须产出空间关系",
    "test_d_超限负锚点截断语义不变",
    "test_e_扫描形态单一真源不得再有第二份副本",
})
EXPECTED_GREEN = frozenset({
    "test_b_全正坐标输出与修前逐字相同",
    "test_c_单锚点仍返回None",
})


def _mutate_and_run() -> int:
    """在**临时副本**里把 core.py 的扫描形态还原成修前内联无符号副本，跑本守卫。

    只在临时副本里改源码（隔离 worktree 本体零改动）；用例仍经生产调用路径触发。
    """
    import shutil
    import subprocess
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="r15_vprim_mutate_"))
    try:
        dst = tmp / "repo"
        dst.mkdir()
        for name in ("lingshu", "tests", "conftest.py", "pyproject.toml"):
            src = REPO / name
            if src.is_dir():
                shutil.copytree(src, dst / name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(src, dst / name)
        core = dst / "lingshu" / "core" / "core.py"
        text = core.read_text(encoding="utf-8")
        old = "for m in _re.finditer(ANCHOR_SCAN_PATTERN, claim):"
        new = ('for m in _re.finditer(r"[\\w\\-]+@\\(\\d+,\\d+,\\d+,\\d+\\)",'
               ' claim):')
        if text.count(old) != 1:
            print("[mutate] 变异锚点未唯一命中（%d 次）：%r" % (text.count(old), old))
            return 2
        core.write_text(text.replace(old, new), encoding="utf-8")
        print("[mutate] 已把 core.py 的扫描形态还原成修前内联无符号副本")

        guard = "tests/test_issue718_vprim_scan_negative_anchor_guard.py"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest", guard,
             "-q", "--no-header", "--tb=no", "-rf", "-p", "no:cacheprovider"],
            cwd=str(dst), capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        tail = (proc.stdout or "").strip().splitlines()
        print("[mutate] pytest rc=%d" % proc.returncode)
        print("[mutate] 输出尾行：%s" % (tail[-1] if tail else "(空)"))
        red = set()
        for line in tail:
            m = re.match(r"FAILED\s+\S*::(\S+?)(?:\s|$)", line.strip())
            if m:
                red.add(m.group(1))
        green = set(EXPECTED_RED | EXPECTED_GREEN) - red
        print("[mutate] 实跑红集：%s" % sorted(red))
        print("[mutate] 实跑绿集：%s" % sorted(green))
        ok = red == set(EXPECTED_RED) and green == set(EXPECTED_GREEN)
        print("[mutate] 与声明集相等：%s" % ok)
        if not ok:
            print("[mutate] 期望红集：%s" % sorted(EXPECTED_RED))
            print("[mutate] 期望绿集：%s" % sorted(EXPECTED_GREEN))
            print("---- pytest 全文 ----")
            print(proc.stdout)
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--mutate" in sys.argv:
        sys.exit(_mutate_and_run())
    print(__doc__)
