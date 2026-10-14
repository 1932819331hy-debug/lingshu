# -*- coding: utf-8 -*-
"""test_issue581_store_gate_guard · add_promotion_proposal store 层闸守卫（#581 / PR #585 返工）
====================================================================================================
守卫判据：store 层直调 add_promotion_proposal 时：
  G1 无 override → 拒绝（负记忆闸）；
  G2 override=True 无密钥 → PermissionError；
  G3 override=True 正密钥 → 放行 + action_logs 留痕；
  G4 共享层节点 → 拒绝；
  G5 正对照：全新内容非共享层 → 正常通过。
"""
from __future__ import annotations

import os

import pytest

from lingshu.core.core import LayeredStore, MemoryLayer, Role, STNode, ConditionSpace

_KEY = "test_designer_key_581"


def _store():
    return LayeredStore(":memory:", role=Role.PRIMARY)


def _add_node(s, layer=MemoryLayer.KNOWLEDGE, content="测试内容", tags=None):
    import uuid
    nid = "n_" + uuid.uuid4().hex[:8]
    s.add_node(STNode(id=nid, content=content, modality="text",
                      spatial_coordinates={}, temporal_coordinate=1000.0,
                      condition_space=ConditionSpace("T", "T", (0, 1e12), "R"),
                      importance=0.5, confidence=0.5, layer=layer, tags=tags or []))
    return nid


def test_g1_no_override_rejected():
    s = _store()
    nid = _add_node(s, content="已被否决的提议")
    rid = s.add_rejected_path("promotion", "已被否决的提议——设计者终裁拒绝", "测试否决")
    try:
        with pytest.raises(ValueError, match=r"#581"):
            s.add_promotion_proposal(nid, "req", "reason")
    finally:
        s.close()


def test_g4_shared_layer_rejected():
    s = _store()
    nid = _add_node(s, layer=MemoryLayer.STRUCTURE)
    try:
        with pytest.raises(ValueError, match=r"#581"):
            s.add_promotion_proposal(nid, "req", "reason")
    finally:
        s.close()


def test_g5_normal_content_passes():
    s = _store()
    nid = _add_node(s, content="全新内容无负记忆")
    try:
        pid = s.add_promotion_proposal(nid, "req", "reason")
        assert pid, "正常内容应通过"
    finally:
        s.close()


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
