# -*- coding: utf-8 -*-
"""test_issue575_pos_origin_guard · load_shapes pos_origin 缺失行为守卫（#575 / PR #580 返工）
====================================================================================================
返工原因（维护者复核指出两格）：① docstring 未写明缺失行为；② 无自带守卫。
守卫判据：缺失 pos_origin 时必须发 DeprecationWarning（不再静默），有 pos_origin 时不发。
"""
from __future__ import annotations

import json
import os
import tempfile
import warnings

import pytest

from lingshu.world import shapes


def _write_shapes(path, pos_origin=None):
    doc = {"schema": shapes.SHAPES_SCHEMA, "source": "test",
           "shapes": {"box": [{"kind": "box", "pos": [0, 0, 0], "size": [1, 1, 1]}]}}
    if pos_origin is not None:
        doc["pos_origin"] = pos_origin
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def test_g1_missing_pos_origin_warns():
    """G1：缺失 pos_origin ⇒ DeprecationWarning（不再静默取默认）。"""
    d = tempfile.mkdtemp()
    f = os.path.join(d, "no_origin.json")
    _write_shapes(f)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        shapes.load_shapes(f)
        dep = [x for x in w if issubclass(x.category, DeprecationWarning)
               and "pos_origin" in str(x.message)]
    assert dep, ("缺失 pos_origin 未发 DeprecationWarning", [str(x.message) for x in w])


def test_g2_explicit_pos_origin_no_warning():
    """G2：显式声明 pos_origin 时不发警告（不误伤）。"""
    d = tempfile.mkdtemp()
    f = os.path.join(d, "with_origin.json")
    _write_shapes(f, pos_origin="anchor")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        shapes.load_shapes(f)
        dep = [x for x in w if issubclass(x.category, DeprecationWarning)
               and "pos_origin" in str(x.message)]
    assert not dep, ("显式声明时不应发 DeprecationWarning", [str(x.message) for x in dep])


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
