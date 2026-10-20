# -*- coding: utf-8 -*-
"""守卫 · `#10` 未收口子项（core.py 两处脱敏死占位符 ⇒ 环境变量驱动）
====================================================================================
缺陷（`#10` ② 在 `lingshu/core/core.py` 的未收口子项）：
  导出脱敏把**路径操作数**改写成了中性占位字面量，`pattern_separation_scan`（H3）
  与 `reconstruct_scene`（H4）里的 `_kb` 即该残留——`os.path.isabs(<占位字面量>)`
  在 Windows/Linux **均恒 False** ⇒ 这条 `sys.path` 注入在公开仓**恒不发生**
  （死代码），而 `#10` 母主张要求的**功能等价修复（本机字面量 → 环境变量驱动）**
  一直未落地（`#381` 只做了「相对条目不插」的安全禁用）。

判据来源：
  · `#10` §三.③ 的导出精修先例：「本机字面量 → 环境变量（缺省 …）」；
  · 既有 env 约定真源 `lingshu/core/component_resolver.py::configured_roots`
    （`LINGSHU_COMPONENT_ROOT`，`os.pathsep` 多根；同 `activation.py` 的
    `LINGSHU_DATA_ROOT` 一族）——本件**复用**该单一真源，不另造第二份解析；
  · `tests/test_issue381_relative_sys_path_insert_guard.py`（同面既有守卫：相对
    条目不得进 `sys.path`）——本件不得放松它。

断言（每条钉「缺陷不再存在」，不是「代码能跑」）：
  A **fail-safe 保持**：`LINGSHU_COMPONENT_ROOT` 未声明 ⇒ 两个方法调用后
     `sys.path` **零新增**（未声明即不注入）。
  B **功能等价**：`LINGSHU_COMPONENT_ROOT` 声明为**绝对**根 ⇒ 该根被插入
     `sys.path`（H3/H4 两处都要）；同一声明里的**相对**条目仍**不**插入
     （`#381` 判据未松）。
  C **形态防回归**：`lingshu/core/core.py` 的**代码**里不得再出现脱敏占位符
     `<local-path>`（按 AST/词法取字面量，避免误命中注释与 docstring 里的说明字样）。

运行（lingshu 仓根）：
  python -X utf8 -m pytest tests/test_issue10_kb_env_driven_guard.py -q --no-header
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(REPO, "lingshu", "core", "core.py")

#: 脱敏占位符（`#10` ② 的形态锚；与 `tests/test_issue10_intake_registration_guard.py`
#: 的 `PLACEHOLDER_RE` 同字面，勿各自漂移）。
PLACEHOLDER = "<local-path>"

_PROBE = r'''
import json, os, sys
from lingshu.core.core import SpacetimeMemoryEngine

which = sys.argv[1]
e = SpacetimeMemoryEngine(":memory:")
before = list(sys.path)
if which == "h3":
    ret = e.pattern_separation_scan(limit=2)
else:
    ret = e.reconstruct_scene("probe", depth=1)
after = list(sys.path)
print("##J##" + json.dumps({
    "added": [p for p in after if p not in before],
    "err": (ret or {}).get("error"),
}))
'''


def _run_probe(work: str, which: str, root_env=None) -> dict:
    """干净子进程（cwd=work，仓根经 PYTHONPATH 声明）跑单点探针。

    `which` ∈ {"h3","h4"}——**一次只调一个方法**：两处插入面各有独立落点，
    合并调用时 H3 已插入的条目会让 H4 的 `_kb not in sys.path` 为假（去重），
    那样测不到 H4 自身的落点。
    `root_env` 为 `None` ⇒ 显式清掉 `LINGSHU_COMPONENT_ROOT`（判 A 的 fail-safe）。
    """
    env = dict(os.environ)
    env.pop("LINGSHU_ALLOW_CWD_IMPORTS", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    if root_env is None:
        env.pop("LINGSHU_COMPONENT_ROOT", None)
    else:
        env["LINGSHU_COMPONENT_ROOT"] = root_env
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", _PROBE, which],
                       cwd=work, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0, (
        "探针子进程失败 rc=%s\nstdout=%s\nstderr=%s"
        % (r.returncode, r.stdout[-800:], r.stderr[-1200:]))
    for line in (r.stdout or "").splitlines():
        if line.startswith("##J##"):
            return json.loads(line[len("##J##"):])
    raise AssertionError("探针无输出：stdout=%r stderr=%r"
                         % (r.stdout[-800:], r.stderr[-800:]))


# ---------------------------------------------------------------- A
def test_a_env_unset_no_injection(tmp_path):
    """A：`LINGSHU_COMPONENT_ROOT` 未声明 ⇒ 两处方法都**不**向 `sys.path` 注入。"""
    for which in ("h3", "h4"):
        j = _run_probe(str(tmp_path), which, root_env=None)
        assert j["added"] == [], (
            "env 未声明却发生注入（%s）：%r" % (which, j["added"]))


# ---------------------------------------------------------------- B
def test_b_env_absolute_root_injected_relative_not(tmp_path):
    """B：env 声明**绝对**根 ⇒ H3/H4 两处都插入该根；同声明的**相对**条目不插。"""
    abs_root = os.path.join(str(tmp_path), "kbroot")
    os.makedirs(abs_root, exist_ok=True)
    rel_entry = "rel_kb_10"
    declared = abs_root + os.pathsep + rel_entry
    for which in ("h3", "h4"):
        j = _run_probe(str(tmp_path), which, root_env=declared)
        assert abs_root in j["added"], (
            "env 声明的绝对根未被 %s 插入（功能等价修复缺失）：added=%r err=%r"
            % (which, j["added"], j["err"]))
        assert rel_entry not in j["added"], (
            "同一声明里的**相对**条目被 %s 插进 `sys.path`（放松了 #381）：%r"
            % (which, j["added"]))


# ---------------------------------------------------------------- C
def _code_string_literals(src: str):
    """源码里**代码**位置的字符串字面量（docstring 不计入）。"""
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_c_no_placeholder_literal_in_core_code():
    """C：`core.py` 的**代码字面量**里不得再有 `<local-path>`（死占位符防回归）。"""
    # 量具自检：先证明扫描器**确实**能认出代码字面量里的占位符（否则本判据恒绿）。
    planted = 'X = r"%s Files\\2_ai\\kb"\n' % PLACEHOLDER
    assert _code_string_literals(planted), (
        "量具自检失败：扫描器认不出植入的占位符字面量 ⇒ 本判据会静默通过")
    # 同自检的反面：**注释 / docstring** 里的说明字样不得被判红（否则只会误报）。
    comment_only = '"""doc: %s 占位符"""\n# 注释里的 %s\n' % (PLACEHOLDER, PLACEHOLDER)
    assert [v for _, v in _code_string_literals(comment_only)
            if PLACEHOLDER in v] == [], (
        "量具自检失败：注释/docstring 里的说明字样被判红 ⇒ 本判据会误报")

    src = open(CORE, encoding="utf-8").read()
    offenders = [(ln, v) for ln, v in _code_string_literals(src)
                 if PLACEHOLDER in v]
    assert offenders == [], (
        "core.py 代码字面量里仍有脱敏占位符（#10 ② 未收口）：%r" % (offenders,))
