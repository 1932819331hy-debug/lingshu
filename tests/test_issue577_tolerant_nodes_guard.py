# -*- coding: utf-8 -*-
"""test_issue577_tolerant_nodes_guard · get_nodes_in_range 容错守卫（#577 / PR #578 返工）
================================================================================================
返工原因（ybyz2 复核）：「零新增守卫——把修复回退后 19 条既有守卫全绿」。
守卫判据：往库里注入一条坏行（tags 非法 JSON），get_nodes_in_range /
get_nodes_by_tag 必须跳过坏行并返回其余好行，而非抛异常或返回空。
"""
from __future__ import annotations

import tempfile
import os

import pytest

from lingshu.core.core import LayeredStore, MemoryLayer, Role, STNode, ConditionSpace


def _store_with_bad_row():
    """建库、写入一个好节点 + 直接注入一条 tags 非法的坏行。"""
    s = LayeredStore(":memory:", role=Role.PRIMARY)
    nid = "good_001"
    s.add_node(STNode(id=nid, content="好节点", modality="text",
                      spatial_coordinates={}, temporal_coordinate=1000.0,
                      condition_space=ConditionSpace("T", "T", (0, 1e12), "R"),
                      importance=0.5, confidence=0.5, layer=MemoryLayer.KNOWLEDGE,
                      tags=["clean"]))
    # 直接注入坏行（tags 列写非法 JSON——STNode.from_row 会抛）
    c = s.conn.cursor()
    c.execute("INSERT INTO nodes (id, content, layer, tags, importance, confidence, modality,"
              " spatial_coordinates, temporal_coordinate, condition_space, created_at, last_access)"
              " VALUES ('bad_001', '坏行', 'knowledge', 'not-json', 0.5, 0.5, 'text',"
              " '{}', 2000.0, '{}', 1, 1)")
    s.conn.commit()
    return s, nid


def test_g1_get_nodes_in_range_skips_bad_row():
    """G1：时间范围查询含坏行时跳过之、返回好行，不抛异常。"""
    s, good_id = _store_with_bad_row()
    try:
        nodes = s.get_nodes_in_range(0.0, 5000.0)
        ids = [n.id for n in nodes]
        assert good_id in ids, ("好节点被丢弃", ids)
        assert "bad_001" not in ids, ("坏行未被跳过", ids)
    finally:
        s.close()


def test_g2_get_nodes_by_tag_skips_bad_row():
    """G2：标签查询含坏行时跳过之、返回好行。"""
    s, good_id = _store_with_bad_row()
    try:
        nodes = s.get_nodes_by_tag("clean")
        ids = [n.id for n in nodes]
        assert good_id in ids, ("好节点被丢弃", ids)
    finally:
        s.close()


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
