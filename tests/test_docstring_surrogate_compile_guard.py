# -*- coding: utf-8 -*-
"""test_docstring_surrogate_compile_guard · docstring 真代理码点「编译期」静态守卫
================================================================================
为什么需要这一件（本机看不见的缺陷形态）
    `3462ab6`（#378 回灌面修复）在 `lingshu/core/core.py` 的 `SpacetimeMemoryEngine.import_all`
    docstring 里写了一段举例文字，其中是**裸**（单反斜杠）的 ``\\ud83d``。Python 把单反斜杠
    `\\uXXXX` 转义**求值成真代理码点**（U+D83D）存进 docstring 常量；而 **CPython 3.13+ 在
    编译期会对 docstring 常量做 UTF-8 编码**（docstring 要写进 `.pyc`）⇒ `import lingshu.core.core`
    当场抛 `UnicodeEncodeError`，`tests/test_core_export_all.py` 模块级导入即崩。

    CI 实测读数（run `38960041793`，job `gate (py3.13)`，failure）::

        File ".../tests/test_core_export_all.py", line 37, in <module>
          from lingshu.core.core import SpacetimeMemoryEngine
        UnicodeEncodeError: 'utf-8' codec can't encode character '\\ud83d' in position 7358:
          surrogates not allowed

    该处已由 `7eae6ba` 改成双反斜杠修好。但**防复发**缺一格：唯一防线是 CI 的 py3.13 腿
    （推送后才知道），而 py3.12 腿上**永远绿**（3.12 编译 docstring 不做这次 UTF-8 编码）；
    本机只有 py3.12 ⇒ 推送前看不见。本件把该判据搬进**本机 py3.12 也能跑**的静态扫描面：
    `ast` 解析后 docstring 的**值**里是否含 U+D800–U+DFFF。

判据（核心：触发条件精确为「docstring 常量（求值后）含真代理码点」）
    | 形态                                                    | 是否触发 |
    | docstring 含**单**反斜杠转义（源码 ``\\ud83d``）          | **触发**（求值后是真代理） |
    | docstring 含**双**反斜杠（源码 ``\\\\ud83d``）            | 不触发（求值后是字面文本） |
    | 注释里的该转义                                          | 不触发（不进编译产物） |
    | 模块级/函数级**普通字符串常量**含真代理（`LONE = "…"`）    | **不触发**（py3.13 腿实测绿：`test_issue378_tags_surrogate_guard` 全 PASSED） |
    | 函数体内运行时构造的字符串                              | 不触发（不参与 .pyc 写入编码） |

    ⇒ **扫描面只取 docstring**：`ast.Module` / `ast.ClassDef` / `ast.FunctionDef` /
    `ast.AsyncFunctionDef` 的 `ast.get_docstring(node, clean=False)`。**不**扫普通字符串常量、
    **不**扫注释——扫宽了必假阳：本仓 `tests/` 下就有一批**刻意构造**的模块级真代理常量
    （如 `tests/test_issue378_import_all_proxy_codepoint_guard.py` 的 `LONE = "\\ud83d"`、
    `tests/test_issue494_state_attributes_surrogate_guard.py`），它们在 py3.13 腿上是绿的，
    一旦被本守卫扫进来就会把守卫自己变成假阳源。

    偏移口径：报出的 `pos` 是**在 docstring 值内**的字符偏移，与 CPython 报的
    `in position N` 同口径——`3462ab6:lingshu/core/core.py` 实测 `pos=7358`，与上面 CI 报的
    7358 逐字一致（本件断言 D 钉住这一点）。

断言组（A–D）
    A 现状绿：遍历全仓 `*.py`，任一 docstring 含真代理码点 ⇒ 判红，并点名
      `<相对路径>:<符号名> docstring 偏移 <pos>，上下文 <ctx>`。同组另钉两条**不可静默**的
      量具前提：解析失败的文件数为 0（未解析 ⇒ 未检查 ⇒ 不得静默通过）、被扫文件数 ≥ 500。
    B 量具自检：断言**确实读到了文件**（文件数 ≥ 500；`7eae6ba` 实测 626）——「扫描器坏了却
      零命中通过」是本类判据最典型的失效形态，必须用读数钉住。
    C 判别力自证（**内联源码**构造，不依赖历史提交）：对源码字符串跑同一个扫描函数，五种形态
      逐一验：①docstring 单反斜杠 ⇒ 必须命中（符号 `f`、偏移 2）；②同位置双反斜杠 ⇒ 必须不命中；
      ③模块级普通常量 `LONE = "…"` 单反斜杠 ⇒ 必须不命中（防扫宽）；④注释里的单反斜杠 ⇒ 必须
      不命中；⑤模块级 docstring 单反斜杠 ⇒ 必须命中（符号 `<module>`，钉 `ast.Module` 分支）。
      并**自证本件自身不是毒件**：扫本件源码 ⇒ 零命中（内联源码字符串里的反斜杠一律用
      `chr(0x5C)` 拼装，本文件源码文本里不含任何单反斜杠 `u` 转义）。
    D 回退即红（历史提交取证）：`git show 3462ab6:lingshu/core/core.py` 的**字节**喂给扫描函数
      ⇒ 必须命中符号以 `import_all` 结尾、`pos == 7358`。这样判别力不依赖「去改工作区文件」，
      不与并行线撞车。CI 的 `actions/checkout` 未设 `fetch-depth`（浅克隆）⇒ 取不到该对象时
      本组**skip**（不判红：无历史可取证 ≠ 判据失效；判别力由 C 的内联构造独立承担）。

不适用条件
    · 只覆盖 **docstring**：普通字符串常量 / 注释 / 运行时构造串里的真代理码点**不在本守卫射程**
      （那几类在 py3.13 腿实测绿；要收需要另立判据并先证「无假阳」）。
    · 不覆盖「`.pyc` 写入之外」的编码面（如 `str.encode("utf-8")` 在运行期抛错）——那是运行期判据。
    · 本守卫在本机 py3.12 上**不**复现原缺陷的编译期崩溃，只是把「同一毒件形态」搬进可机检面。

运行（仓根）：python -X utf8 -m pytest tests/test_docstring_surrogate_compile_guard.py -q --no-header
"""
from __future__ import annotations

import ast
import collections
import os
import subprocess
import warnings

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF_REL = "tests/test_docstring_surrogate_compile_guard.py"

SURROGATE_LO = 0xD800
SURROGATE_HI = 0xDFFF
# docstring 宿主：只有这四类节点有 `__doc__`（`ast.get_docstring` 也只接受这四类）。
DOCSTRING_HOLDERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
# 不参与扫描的目录：VCS / 依赖 / 缓存 / 隔离 worktree 落点（`_prowatch` 是值守侧 scratch 约定）。
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "build", "dist", ".eggs", "_prowatch",
})
MIN_PY_FILES = 500          # 量具自检下界（`7eae6ba` 实测 626）
CONTEXT_SPAN = 24           # 命中点两侧各取多少字符作上下文
BACKSLASH = chr(0x5C)       # ★ 拼装用：本文件源码文本里不出现单反斜杠 u 转义

# 判别力自证用的内联源码（★ 全部由 BACKSLASH 拼装 ⇒ 本件自身不含真代理码点）
_TXT_SINGLE_ESC = BACKSLASH + "ud83d"        # 源码文本形态：单反斜杠 + ud83d
_TXT_DOUBLE_ESC = BACKSLASH * 2 + "ud83d"    # 源码文本形态：双反斜杠 + ud83d
SRC_FUNC_SINGLE = 'def f():\n    """x ' + _TXT_SINGLE_ESC + ' y"""\n'
SRC_FUNC_DOUBLE = 'def f():\n    """x ' + _TXT_DOUBLE_ESC + ' y"""\n'
SRC_MODULE_SINGLE = '"""x ' + _TXT_SINGLE_ESC + ' y"""\n'
SRC_COMMENT_SINGLE = "# " + _TXT_SINGLE_ESC + "\ndef f():\n    pass\n"
SRC_LONE_CONST = 'LONE = "' + _TXT_SINGLE_ESC + '"\n'

Finding = collections.namedtuple("Finding", "origin symbol pos codepoint context")


def _is_surrogate(ch):
    return SURROGATE_LO <= ord(ch) <= SURROGATE_HI


def _escape_surrogates(text):
    """把真代理码点渲染成 `\\uXXXX` 文本（供报错信息用；本函数自身不引入真代理）。"""
    return "".join(
        (BACKSLASH + "u%04x" % ord(ch)) if _is_surrogate(ch) else ch for ch in text
    )


def _qualified_symbol(node, parents):
    """`Outer.Inner.f` 形态的符号名；模块级 docstring 记作 `<module>`。"""
    parts = []
    cur = node
    while cur is not None:
        if isinstance(cur, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(cur.name)
        cur = parents.get(cur)
    return ".".join(reversed(parts)) if parts else "<module>"


def _context(doc, pos, span=CONTEXT_SPAN):
    frag = doc[max(0, pos - span):pos + span].replace("\n", BACKSLASH + "n")
    return _escape_surrogates(frag)


def find_docstring_surrogates(source, origin="<source>"):
    """扫**一份源码**（str 或 bytes）的 docstring，返回命中列表（`Finding`）。

    只走 `ast.get_docstring(node, clean=False)`（＝编译进 `.pyc` 的那个常量原文），
    不扫普通字符串常量、不扫注释——理由见模块 docstring 的判据表。
    """
    with warnings.catch_warnings():
        # 仓内存在历史遗留的非法转义（实测：全树扫描时 `ast.parse` 会就两处
        # 非法转义 `\\s` / `\\d` 发 SyntaxWarning）。若调用方开了 `-W error`，
        # 那会把**与判据无关**的警告升级成异常、令扫描中断 ⇒ 这里就地忽略。
        warnings.simplefilter("ignore")
        tree = ast.parse(source, filename=origin)
    parents = {child: parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, DOCSTRING_HOLDERS):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        for pos, ch in enumerate(doc):
            if _is_surrogate(ch):
                findings.append(Finding(origin=origin,
                                        symbol=_qualified_symbol(node, parents),
                                        pos=pos,
                                        codepoint="U+%04X" % ord(ch),
                                        context=_context(doc, pos)))
    return findings


def _rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def scan_tree(root):
    """遍历 `root` 下全部 `*.py`，返回 `(findings, unparsed, file_count)`。"""
    findings, unparsed, count = [], [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            count += 1
            path = os.path.join(dirpath, name)
            rel = _rel(path, root)
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
            except OSError as exc:                       # pragma: no cover - 环境异常
                unparsed.append((rel, "read: %s" % exc))
                continue
            try:
                findings.extend(find_docstring_surrogates(raw, rel))
            except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
                unparsed.append((rel, "parse: %s" % exc))
    return findings, unparsed, count


def _format(findings):
    return "\n".join(
        "  %s:%s docstring 偏移 %d（%s），上下文 %s"
        % (f.origin, f.symbol, f.pos, f.codepoint, f.context) for f in findings
    )


def _git_show(rev_path):
    """`git show <rev:path>` 的**字节**产物；取不到（浅克隆 / 无 git / 无该对象）返回 None。

    走 `subprocess` 的 bytes 面（不设 text=True）——不经 Windows shell、不做本地解码。
    """
    try:
        proc = subprocess.run(["git", "-C", REPO, "show", rev_path],
                              capture_output=True)
    except OSError:                                      # pragma: no cover - 无 git 环境
        return None
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


# ---------------------------------------------------------------- A 现状绿
def test_a_全仓docstring无真代理码点():
    """A：全仓 `*.py` 的 docstring 里不得有真代理码点；并钉住「不可静默」的两条量具前提。"""
    findings, unparsed, count = scan_tree(REPO)
    assert not unparsed, (
        "有 %d 个 .py 未能解析 ⇒ 它们**未被检查**（不得静默通过）：\n%s"
        % (len(unparsed), "\n".join("  %s %s" % kv for kv in unparsed))
    )
    assert count >= MIN_PY_FILES, (
        "扫描面只读到 %d 个 .py（下界 %d）⇒ 扫描器/仓根推错了" % (count, MIN_PY_FILES)
    )
    assert not findings, (
        "docstring 含真代理码点 ⇒ py3.13+ 编译期 UnicodeEncodeError（`.pyc` 写 docstring 即崩）。"
        "修法＝把该处转义的反斜杠写成**双**反斜杠（源码形态 `" + BACKSLASH * 2 + "ud83d`）。\n"
        "命中 %d 处：\n%s" % (len(findings), _format(findings))
    )


# ---------------------------------------------------------------- B 量具自检
def test_b_量具自检确实读到了文件():
    """B：扫描器必须真的读到文件（「零命中通过」不得来自「零文件」）。

    本组只钉**量具读数**，不复述 A 的判据——这样红/绿能区分「量具坏了」与「仓里有毒件」。
    """
    _, unparsed, count = scan_tree(REPO)
    assert count >= MIN_PY_FILES, (
        "量具失效：只读到 %d 个 .py（下界 %d；7eae6ba 实测 626）" % (count, MIN_PY_FILES)
    )
    assert not unparsed, "量具失效：%d 个 .py 未解析" % len(unparsed)
    assert os.path.isfile(os.path.join(REPO, "pyproject.toml")), "仓根推错（无 pyproject.toml）"


# ---------------------------------------------------------------- C 判别力自证
def test_c_判别力自证内联源码五形态():
    """C：内联源码五形态逐一对表（不依赖历史提交、不改工作区文件）。"""
    # ①docstring 单反斜杠 ⇒ 命中（符号 f、偏移 2）
    hits = find_docstring_surrogates(SRC_FUNC_SINGLE, "src_func_single")
    assert len(hits) == 1, hits
    assert hits[0].symbol == "f", hits
    assert hits[0].pos == 2, hits
    assert hits[0].codepoint == "U+D83D", hits
    # 先钉住「内联源码确实造出了真代理」（量具的量具：否则 ① 可能只是字符串写错）
    inner = ast.get_docstring(ast.parse(SRC_FUNC_SINGLE).body[0], clean=False)
    assert [_is_surrogate(ch) for ch in inner] == [False, False, True, False, False], repr(inner)

    # ②同位置双反斜杠 ⇒ 不命中
    assert find_docstring_surrogates(SRC_FUNC_DOUBLE, "src_func_double") == []

    # ③模块级普通字符串常量 ⇒ 不命中（防扫宽：本仓 tests/ 下有一批这类刻意构造）
    assert find_docstring_surrogates(SRC_LONE_CONST, "src_lone_const") == []
    lone = ast.parse(SRC_LONE_CONST).body[0].value.value
    assert any(_is_surrogate(ch) for ch in lone), "夹具失效：普通常量里应当是真代理"

    # ④注释 ⇒ 不命中（不进编译产物）
    assert find_docstring_surrogates(SRC_COMMENT_SINGLE, "src_comment_single") == []

    # ⑤模块级 docstring ⇒ 命中（钉 ast.Module 分支；符号 <module>）
    hits = find_docstring_surrogates(SRC_MODULE_SINGLE, "src_module_single")
    assert len(hits) == 1 and hits[0].symbol == "<module>" and hits[0].pos == 2, hits

    # ★ 无毒件自证：本件自身源码扫下来必须零命中（内联反斜杠全由 chr(0x5C) 拼装）。
    #   （真代理码点根本无法以 UTF-8 编码落盘 ⇒ 字节面不必查；查的是「解析后 docstring 的值」。）
    with open(os.path.join(REPO, SELF_REL), "rb") as fh:
        self_raw = fh.read()
    self_hits = find_docstring_surrogates(self_raw, SELF_REL)
    assert self_hits == [], "本件自己成了 py3.13 毒件：\n" + _format(self_hits)


# ---------------------------------------------------------------- D 回退即红
def test_d_历史提交回退即红():
    """D：`3462ab6:lingshu/core/core.py` 的字节 ⇒ 必须命中 `import_all` / 偏移 7358。

    （「还原即绿」那一侧由 A 在同一棵树上扫 `HEAD` 承担，本组只钉判别力。）
    """
    rev, path = "3462ab6", "lingshu/core/core.py"
    raw = _git_show(rev + ":" + path)
    if raw is None:
        pytest.skip("取不到 %s:%s（浅克隆 / 无 git）—— 判别力由 C 的内联构造独立承担" % (rev, path))
    hits = find_docstring_surrogates(raw, rev + ":" + path)
    assert hits, "判别力失效：修复前形态未命中（%s:%s）" % (rev, path)
    named = [f for f in hits if f.symbol.endswith("import_all") and f.pos == 7358]
    assert named, "判别力失效：期望 import_all / 偏移 7358，实得：\n%s" % _format(hits)
    assert named[0].codepoint == "U+D83D", named
