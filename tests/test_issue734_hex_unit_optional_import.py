# -*- coding: utf-8 -*-
"""test_issue734_hex_unit_optional_import · `hex_unit` 注解里用到的 typing 名必须**全部已导入**（#734）
====================================================================================================
缺陷（#734，改前实测复现）：``lingshu/nn/hex_unit.py`` 头部是
``from typing import Dict, List, Tuple``，而两处注解用了 ``Optional``
（``UnitHeadNet.describe(self, card, feat_names: Optional[List[str]] = None)`` 与
``finetune_units(..., feat_cache: Optional[np.ndarray] = None)``）⇒

    typing.get_type_hints(UnitHeadNet.describe) -> NameError: name 'Optional' is not defined
    typing.get_type_hints(finetune_units)       -> NameError: name 'Optional' is not defined
    （正对照：无注解缺陷的 UnitHeadNet.get_vec -> {'return': numpy.ndarray}，正常）

``from __future__ import annotations`` 让普通调用不炸（注解不求值），但任何
``typing.get_type_hints()`` / pydantic / dataclasses 内省 / 静态工具都会炸。

判据（本件，两条）：
  G1 **运行时判据**：``typing.get_type_hints`` 对本模块**全部**带注解的函数/方法求值
      不抛异常——这是缺陷的直接可观测面，不是肉眼看 import 行；
  G2 **静态判据**：AST 扫全模块，注解里出现的 typing 名（``Dict/List/Optional/...``）
      必须都在 ``from typing import ...`` 里 —— 覆盖「同族其它漏导入」（本条只修点名的
      ``Optional``；若将来又添别的，本断言照样红）。

正/负对照（证明量具本身有效，不是恒绿也不是恒红）：
  C1 正对照：另建一个**导入齐全**的模块，同一 ``get_type_hints`` 调用正常返回；
  C2 负对照：另建一个**故意漏导入**的模块，同一调用必须抛 ``NameError``
      —— 证明本量具确实能测到这一类缺陷。

运行（仓根）：python -X utf8 -m pytest tests/test_issue734_hex_unit_optional_import.py -q
"""
from __future__ import annotations

import ast
import inspect
import io
import typing

import pytest

import lingshu.nn.hex_unit as U

#   注解里可能用到的 typing 名（判据面：出现即必须已导入）
TY_NAMES = frozenset((
    "Optional", "Dict", "List", "Tuple", "Set", "FrozenSet", "Any", "Callable",
    "Sequence", "Mapping", "MutableMapping", "Iterable", "Iterator", "Union",
    "Type", "Generic", "TypeVar", "NamedTuple", "Protocol", "Literal", "Final",
))


def _annotation_names(src):
    """AST 取该模块**注解**里出现的裸名（``ast.Name``）集合。"""
    tree = ast.parse(src)
    out = set()

    def add(ann):
        for sub in ast.walk(ann):
            if isinstance(sub, ast.Name):
                out.add(sub.id)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                add(node.returns)
            args = list(node.args.args) + list(node.args.kwonlyargs) + \
                list(node.args.posonlyargs)
            for a in args:
                if a.annotation is not None:
                    add(a.annotation)
            for extra in (node.args.vararg, node.args.kwarg):
                if extra is not None and extra.annotation is not None:
                    add(extra.annotation)
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            add(node.annotation)
    return out


def _typing_imported(src):
    """AST 取 ``from typing import ...`` 里导入的**名字**（含 asname）。"""
    tree = ast.parse(src)
    got = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            for a in node.names:
                got.add(a.asname or a.name)
    return got


def _annotated_members():
    """本模块**全部**带注解的模块级函数 + 类方法（名字 → 可调用对象）。"""
    out = {}
    for name, obj in vars(U).items():
        if name.startswith("__") or getattr(obj, "__module__", None) != U.__name__:
            continue
        if inspect.isfunction(obj) and getattr(obj, "__annotations__", None):
            out[name] = obj
        elif inspect.isclass(obj):
            for mname, m in vars(obj).items():
                if inspect.isfunction(m) and getattr(m, "__annotations__", None):
                    out["%s.%s" % (name, mname)] = m
    return out


def test_g1_get_type_hints_evaluates_every_annotated_member():
    """G1：``get_type_hints`` 对本模块全部带注解成员求值都不抛异常（改前必抛 NameError）。"""
    members = _annotated_members()
    #   硬性非空断言：没有成员可测 ⇒ 本用例恒绿，等于没测
    assert len(members) >= 3, "构造异常：本模块带注解成员只有 %r 个" % len(members)
    assert "UnitHeadNet.describe" in members, "构造异常：未取到 describe"
    assert "finetune_units" in members, "构造异常：未取到 finetune_units"
    bad = []
    for name, fn in sorted(members.items()):
        try:
            typing.get_type_hints(fn)
        except Exception as e:                                  # noqa: BLE001
            bad.append("%s -> %s: %s" % (name, type(e).__name__, e))
    assert not bad, "#734 注解求值失败（缺导入的 typing 名）：\n  " + "\n  ".join(bad)


def test_g2_every_typing_name_in_annotations_is_imported():
    """G2：AST 静态判据 —— 注解里出现的 typing 名必须都在 `from typing import ...` 里。"""
    src = io.open(inspect.getfile(U), encoding="utf-8").read()
    used = _annotation_names(src) & TY_NAMES
    imported = _typing_imported(src)
    assert used, "构造异常：本模块注解里没扫到任何 typing 名"
    missing = sorted(used - imported)
    assert not missing, (
        "#734 注解用到但未导入的 typing 名：%r（已导入 %r）" % (missing, sorted(imported)))


def _exec_module(src):
    """在**独立命名空间**里执行一段模块源码，返回该命名空间（供对照用）。"""
    ns = {}
    exec(compile(src, "<probe>", "exec"), ns)                   # noqa: S102
    return ns


def test_c1_positive_control_well_imported_module_is_fine():
    """C1 正对照：导入齐全的同形模块，同一 `get_type_hints` 调用正常返回。"""
    ns = _exec_module(
        "from __future__ import annotations\n"
        "from typing import List, Optional\n"
        "\n"
        "def f(a: Optional[List[str]] = None) -> List[str]:\n"
        "    return a or []\n")
    got = typing.get_type_hints(ns["f"])
    assert got["a"] == typing.Optional[typing.List[str]], "正对照失败：%r" % (got,)


def test_c2_negative_control_missing_import_raises_name_error():
    """C2 负对照：故意漏导入的模块，同一调用必须抛 `NameError`（证明量具能测到本类缺陷）。"""
    ns = _exec_module(
        "from __future__ import annotations\n"
        "from typing import List\n"
        "\n"
        "def f(a: Optional[List[str]] = None) -> List[str]:\n"
        "    return a or []\n")
    with pytest.raises(NameError):
        typing.get_type_hints(ns["f"])
