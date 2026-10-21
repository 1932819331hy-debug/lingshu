# -*- coding: utf-8 -*-
"""test_issue79_architecture_layers_guard · 架构文档声明的层必须真存在（lingshu issue #79）
============================================================================
缺陷（#79，成立）：
    `docs/architecture.md` §三「分层（轻核 / 重载）」的层树声明了 **`sense` 感知：
    视觉 / 时空（重载依赖：numpy 等）**，紧随其后的「重载声明」句也写
    「`sense`/`gen`/`world` 的依赖…」——但仓里**没有 `lingshu/sense/` 目录**，
    `pyproject.toml` 的 extras 也只有 world/nn/gen/full/dev（无 sense）。实际的
    感知/网络层叫 **`nn`**（`lingshu/nn/` 15 个 `.py`，pyproject 有 `nn` extra）。
    即文档声明的层与代码/打包面**对不上**。

判据（本件，三层）：
  A 量具自检：§三 层树解析器必须真读出至少 4 个层名，且 lingshu/ 下实读非空目录。
  B 主判据（本件机制）：§三 层树声明的层名集合必须**恰等于**实有代码层集合
    {core, nn, gen, world}——每个声明名都是 `lingshu/<名>/` 真目录，且无幻影层
    （`sense` 即幻影层形态）；另每层（重载层 nn/gen/world）必须有同名 extra。
  C 定点夹具（只让本件机制生效）：把 `sense` 层行塞回文本喂给同一检查器 ⇒ 必报
    幻影层；并断言**仓内无第二件**读 architecture.md 的层树（否则两守卫互相代偿）。

判据来源：
  · 「层名 = 实有包目录」——`docs/architecture.md` 层树本身即以包目录为行（
    `├── core` / `├── gen` / `└── world`），`lingshu/` 下同名目录实存
    （`os.listdir('lingshu')` 实读）；`pyproject.toml` 的 extras 命名（world/nn/gen）
    与 §三 的重载声明句同口径。
  · 「sense 是幻影层」——`lingshu/sense` 目录不存在、`pyproject.toml` 无 sense extra
    （2026-10-11 实读），而感知/网络层实为 `nn`（`lingshu/nn/` 15 个 `.py`）。

不适用条件 / 已知边界：
  · 本件只判「层声明面 ↔ 目录/extra 一致」，**不判**各层说明文字是否准确、也不判
    §一「三面结构」表（灵/脑/身，那是聚合来源表，非代码层树）。
  · 若将来真要新增 `sense` 层，本件会判红——届时须**同时**落 `lingshu/sense/` 目录
    与 `sense` extra，本判据才成立（这是有意的：不得只改文档或只落目录）。

定点变异自证（抽掉修复 ⇒ 必红）：
  把 architecture.md §三 的 `├── nn     自研神经网络：…` 行改回
  `├── sense  感知：视觉 / 时空（重载依赖：numpy 等）` ⇒ B/C 两组当场报幻影层 sense。

运行（lingshu 仓根）：python -X utf8 -m pytest tests/test_issue79_architecture_layers_guard.py -q --no-header
退出码：0 = 全过；1 = 有断言失败
"""
from __future__ import annotations

import ast
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "lingshu")
ARCH = os.path.join(REPO, "docs", "architecture.md")
PYPROJECT = os.path.join(REPO, "pyproject.toml")

#: 实有代码层（lingshu/ 下的包目录，排除 __init__/私有）
LAYERS = ("core", "nn", "gen", "world")
#: 需要同名 extra 的重载层（core 是轻核，零依赖）
RELOAD_LAYERS = ("nn", "gen", "world")

#: §三 层树行形态：`├── <name>  <说明>` / `└── <name>  <说明>`
_LAYER_TREE_RE = r"^[├└]──\s+([a-z_][a-z0-9_]*)\s"


def _declared_layers(arch_text):
    """§三 层树里声明的层名（按出现序）。"""
    return re.findall(_LAYER_TREE_RE, arch_text, re.M)


def _real_layer_dirs():
    return sorted(d for d in os.listdir(PKG)
                  if os.path.isdir(os.path.join(PKG, d)) and not d.startswith("__"))


def _declared_extras(pyproject_text):
    import tomllib
    data = tomllib.loads(pyproject_text)
    proj = data.get("project", {})
    return set((proj.get("optional-dependencies") or {}).keys())


def _phantom_layers(arch_text):
    """幻影层：§三 声明了、但 `lingshu/<名>/` 不存在的层名。"""
    return [n for n in _declared_layers(arch_text)
            if not os.path.isdir(os.path.join(PKG, n))]


# ---------------------------------------------------------------- A 量具自检

def test_layer_parser_is_alive():
    """A：层树解析器必须读出 ≥4 个层名，且实读目录非空。"""
    declared = _declared_layers(open(ARCH, encoding="utf-8").read())
    assert len(declared) >= 4, (
        "architecture.md §三 层树解析器只读出 %r（<4）⇒ 它坏了，本判据会静默通过"
        % declared)
    assert _real_layer_dirs(), "lingshu/ 下实读不到任何包目录 ⇒ 扫描器坏了"


# ---------------------------------------------------------------- B 主判据

def test_declared_layers_match_real_packages():
    """B：§三 层树声明的层集合恰等于实有代码层集合，且无幻影层。"""
    arch = open(ARCH, encoding="utf-8").read()
    declared = _declared_layers(arch)
    phantom = _phantom_layers(arch)
    assert not phantom, (
        "architecture.md §三 声明了**不存在的层**（#79）：%s\n实有声明的层 = %s\n"
        "lingshu/ 下的真目录 = %s\n修法：把该行改为真实层名（`sense` → `nn`），"
        "或先落 `lingshu/<层>/` 目录 + 同名 extra 再声明。"
        % (phantom, declared, _real_layer_dirs()))
    assert set(declared) == set(LAYERS), (
        "§三 声明的层集合 %s ≠ 实有代码层 %s ⇒ 声明面与代码面漂移"
        % (sorted(set(declared)), sorted(LAYERS)))


def test_no_sense_layer_anywhere():
    """B′（#79 正身）：`sense` 层不得出现在目录 / 层树 / extra / 散文声明里。"""
    arch = open(ARCH, encoding="utf-8").read()
    assert not os.path.isdir(os.path.join(PKG, "sense")), (
        "lingshu/sense/ 目录出现了——若确要新增 sense 层，须同时补 pyproject extra 与"
        "§三 层树（本判据须重写），不得只落目录。")
    assert "sense" not in _declared_layers(arch), (
        "architecture.md §三 仍声明 `sense` 层（#79）。实声明 = %s" % _declared_layers(arch))
    assert "sense" not in _declared_extras(open(PYPROJECT, encoding="utf-8").read()), (
        "pyproject 出现了 sense extra——须同时落 lingshu/sense/ 目录并更新 §三 层树。")
    # 散文残留：反引号形态的层名引用（`` `sense` ``）在 architecture.md 里必须清零
    m = re.search(r"`sense`", arch)
    assert not m, (
        "architecture.md 仍在散文里以层名形态引用 `sense`（#79）。上下文：%r"
        % arch[max(0, m.start() - 60):m.end() + 40])


def test_reload_layers_have_same_named_extra():
    """B″：§三 声明的每个重载层都必须有同名 extra（幻影层 sense 的连带面）。"""
    extras = _declared_extras(open(PYPROJECT, encoding="utf-8").read())
    missing = [l for l in RELOAD_LAYERS if l not in extras]
    assert not missing, (
        "§三 声明了重载层 %s 但 pyproject 无同名 extra（#79 同族缺陷）。实有 extras = %s"
        % (missing, sorted(extras)))


# ---------------------------------------------------------------- C 定点夹具

def test_mutation_reintroducing_sense_is_red():
    """C-1：把 `sense` 层行塞回 §三 ⇒ 同一检查器必报幻影层 sense。

    **只让本件机制生效**：本检查器是仓内唯一解析 architecture.md §三 层树的地方
    （见 C-2），故不存在与其它守卫互相代偿。
    """
    arch = open(ARCH, encoding="utf-8").read()
    assert not _phantom_layers(arch), "基线应无幻影层（前置条件不满足）"
    mutated = re.sub(
        r"^├── nn\s+.*$",
        "├── sense  感知：视觉 / 时空（重载依赖：numpy 等）",
        arch, count=1, flags=re.M)
    assert mutated != arch, "architecture.md 里没有可替换的 `├── nn` 行 ⇒ 本夹具须重写"
    phantom = _phantom_layers(mutated)
    assert "sense" in phantom, (
        "变异自证失败：塞回 `sense` 层行后检查器**没**报幻影层 ⇒ 本件判据无判别力。"
        "实报 = %r" % phantom)


def test_no_other_guard_parses_layer_tree():
    """C-2：仓内无第二件解析 architecture.md 的 **§三 层树** ⇒ 不可能代偿。

    口径（AST，非全文搜词）：另一测试件必须**两样都占**才算重叠——
      ① 出现裸文件名常量 `'architecture.md'`（真读该文档）；**且**
      ② 出现**层树解析形态**（非 docstring 的字符串常量里含 box-drawing 字符
      U+251C/U+2514 + U+2500，即 §三 层树行的形状）。
    **docstring 里提一句不算**。只读 architecture.md 的**模块计数行**（件 #78 的
    机制，其解析器片段是 `\\d` + `件` / `个 `\\.py``）不命中 ②，故两件不重叠。
    """
    targets = {"architecture.md"}
    me = os.path.basename(__file__)

    def _docstring_values(tree):
        """docstring 的**原始**字符串值（`ast.get_docstring` 会 dedent，与 Constant
        原值不等，故直接取 body[0].value.value）。"""
        vals = set()
        for n in ast.walk(tree):
            body = getattr(n, "body", None)
            if (isinstance(body, list) and body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                vals.add(body[0].value.value)
        return vals

    def _tree_marker(src):
        tree = ast.parse(src)
        docs = _docstring_values(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and n.value not in docs:
                v = n.value
                if ("\u251c\u2500\u2500" in v or "\u2514\u2500\u2500" in v
                        or "[\u251c\u2514]\u2500\u2500" in v):
                    return True
        return False

    hits = []
    for dp, dn, fn in os.walk(os.path.join(REPO, "tests")):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in sorted(fn):
            if not f.endswith(".py") or f == me:
                continue
            p = os.path.join(dp, f)
            src = open(p, encoding="utf-8").read()
            if not _tree_marker(src):
                continue
            tree = ast.parse(src)
            docstrings = _docstring_values(tree)
            for n in ast.walk(tree):
                if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and n.value in targets and n.value not in docstrings):
                    hits.append("%s:%d" % (os.path.relpath(p, REPO).replace("\\", "/"),
                                           n.lineno))
    assert not hits, (
        "以下测试件也真读 architecture.md 并解析**层树**（%r）⇒ 与本件机制重叠/代偿；"
        "须重新设计只让本件生效的夹具（或合并判据）。" % hits)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
