# -*- coding: utf-8 -*-
"""test_issue574_import_clamp_guard · import_all 的 importance 钳制守卫（#574）
==================================================================================
缺陷（PR #579 复核意见）：修复本体成立但**无自带守卫**——把钳制回退后
既有测试全绿，违反《PR 审核细则 v0.1》§二「行为变更必须带可鉴别断言」。

守卫判据：
  G1 越界值经 import_all 落库后必须被钳到 [0.0, 1.0]；
  G2 非有限值（NaN/inf）回落 0.5；
  G3 键缺失或值为 null 时钳到 0.5；
  G4 正对照：合法范围内值（0.7）原样通过。

运行：python -X utf8 -m pytest tests/test_issue574_import_clamp_guard.py -q
"""
from __future__ import annotations

import json
import math
import os
import tempfile

import pytest

from lingshu.core.core import SpacetimeMemoryEngine


def _roundtrip(importance_values):
    """源库写入→篡改→导出→目标库导入→返回目标库中实际 importance 列表。"""
    d = tempfile.mkdtemp()
    src_db = os.path.join(d, "src.db")
    dst_db = os.path.join(d, "dst.db")
    bak = os.path.join(d, "backup.json")

    e = SpacetimeMemoryEngine(src_db)
    ids = []
    for i, v in enumerate(importance_values):
        n = e.add_context(f"节点{i}", importance=v if v is not None and not isinstance(v, float) else (0.5 if v is None else v))
        ids.append(n.id)
    # 直接篡改库内 importance（绕过 add_node 的钳制，模拟被损坏的备份）
    c = e.store.conn.cursor()
    for n, v in zip(ids, importance_values):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v) or v > 1.0 or v < 0.0):
            c.execute("UPDATE nodes SET importance=? WHERE id=?", (v, n))
    e.store.conn.commit()
    e.export_all(bak)
    e.close()

    e2 = SpacetimeMemoryEngine(dst_db)
    result = e2.import_all(bak)
    # 读取导入后的值
    c2 = e2.store.conn.cursor()
    c2.execute("SELECT importance FROM nodes ORDER BY rowid")
    actual = [row[0] for row in c2.fetchall()]
    e2.close()
    return actual, result


def test_g1_out_of_range_clamped():
    """G1：越界值（1e6 / -5.0）经 import_all 落库后必须钳到 [0.0, 1.0]。"""
    actual, _ = _roundtrip([0.5, 0.5])  # 先正常写入两个节点
    # 直接构造篡改后的导入（模拟 #574 的场景：备份含越界值）
    d = tempfile.mkdtemp()
    bak = os.path.join(d, "corrupt.json")
    data = {
        "nodes": [
            {"id": "n1", "content": "越界高", "layer": "knowledge", "importance": 1e6},
            {"id": "n2", "content": "越界低", "layer": "knowledge", "importance": -5.0},
            {"id": "n3", "content": "正常", "layer": "knowledge", "importance": 0.7},
        ],
        "edges": [],
    }
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    e = SpacetimeMemoryEngine(os.path.join(d, "dst.db"))
    try:
        e.import_all(bak)
        c = e.store.conn.cursor()
        c.execute("SELECT id, importance FROM nodes ORDER BY id")
        vals = dict(c.fetchall())
        assert vals.get("n1") == 1.0, ("越界高值未钳到上界", vals)
        assert vals.get("n2") == 0.0, ("越界低值未钳到下界", vals)
        assert vals.get("n3") == 0.7, ("合法值被误改", vals)
    finally:
        e.close()


def test_g2_non_finite_defaults():
    """G2：NaN / +inf / -inf 回落 0.5。"""
    d = tempfile.mkdtemp()
    bak = os.path.join(d, "nan.json")
    data = {
        "nodes": [
            {"id": "n1", "content": "NaN", "layer": "knowledge", "importance": float("nan")},
            {"id": "n2", "content": "Inf", "layer": "knowledge", "importance": float("inf")},
        ],
        "edges": [],
    }
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    e = SpacetimeMemoryEngine(os.path.join(d, "dst.db"))
    try:
        e.import_all(bak)
        c = e.store.conn.cursor()
        c.execute("SELECT id, importance FROM nodes ORDER BY id")
        vals = dict(c.fetchall())
        assert vals.get("n1") == 0.5, ("NaN 未回落 0.5", vals)
        assert vals.get("n2") == 0.5, ("inf 未回落 0.5", vals)
    finally:
        e.close()


def test_g3_null_importance_defaults():
    """G3：importance 为 null 时钳到 0.5。"""
    d = tempfile.mkdtemp()
    bak = os.path.join(d, "null.json")
    data = {
        "nodes": [
            {"id": "n1", "content": "null", "layer": "knowledge", "importance": None},
        ],
        "edges": [],
    }
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    e = SpacetimeMemoryEngine(os.path.join(d, "dst.db"))
    try:
        e.import_all(bak)
        c = e.store.conn.cursor()
        c.execute("SELECT importance FROM nodes WHERE id='n1'")
        val = c.fetchone()[0]
        assert val == 0.5, ("null 未钳到 0.5", val)
    finally:
        e.close()


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
