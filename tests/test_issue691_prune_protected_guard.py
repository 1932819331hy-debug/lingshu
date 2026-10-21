# -*- coding: utf-8 -*-
"""#691 P1-a `prune_self_snapshots` 不查保护 —— 守卫（lingshu/core/core.py）

缺陷（issue #691 · [core/遗忘]）：
  `prune_self_snapshots` 的 `DELETE FROM nodes ... WHERE tags LIKE '%self_snapshot%'`
  **不查** `_is_forget_protected`（唯一保护谓词）⇒ 实测对 `self_snapshot` 节点
  `protect_node` 后再 `prune_self_snapshots(keep=0)`，受保护节点**仍被删除**
  （保护被清理面静默绕过）。

修法：删除前逐候选过 `_is_forget_protected`（保护谓词单一真源），受保护件跳过；
**选面口径**（`tags LIKE '%self_snapshot%'`）不动，不扩大改动面。

断言组（回退「删除前不查保护」⇒ A/B 红）：
  A 受保护快照（`protect_node`）在 keep=0 下必须存活
  B `no_forget` 标签快照在 keep=0 下必须存活（谓词的第二条真源）
  C 正对照：未保护的快照仍被正常清理（保护未把清理面收死）
  D keep 语义：受保护件不占 keep 名额，未受保护件仍按 rowid 降序保留最近 keep 条

运行（仓根）：python -X utf8 -m pytest tests/test_issue691_prune_protected_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _insert_snapshot(eng, nid, tags):
    eng.store.conn.execute(
        "INSERT INTO nodes (id, content, modality, spatial_coordinates,"
        " temporal_coordinate, condition_space, importance, confidence, layer,"
        " access_count, last_access, created_at, tags, semantic_coordinates,"
        " state_attributes, entity_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (nid, "快照内容 %s" % nid, "text", "{}", 1.0, "{}", 0.9, 1.0,
         "self", 0, 1.0, 1.0, json.dumps(tags), "{}", "{}", None))
    eng.store.conn.commit()


def _ids(eng):
    return {r[0] for r in eng.store.conn.execute("SELECT id FROM nodes")}


def test_a_protected_snapshot_survives():
    """A：`protect_node` 的快照在 keep=0 下必须存活（旧：仍被删）。"""
    e = SpacetimeMemoryEngine(":memory:")
    _insert_snapshot(e, "snap_a0", ["self_snapshot"])
    _insert_snapshot(e, "snap_a1", ["self_snapshot"])
    e.protect_node("snap_a1", "设计者显式保护")
    e.prune_self_snapshots(keep=0)
    assert "snap_a1" in _ids(e), "#691：受保护 self_snapshot 被 prune 删除"


def test_b_no_forget_tag_snapshot_survives():
    """B：`no_forget` 标签快照在 keep=0 下必须存活（谓词第二真源）。"""
    e = SpacetimeMemoryEngine(":memory:")
    _insert_snapshot(e, "snap_b0", ["self_snapshot"])
    _insert_snapshot(e, "snap_b1", ["self_snapshot", "no_forget"])
    e.prune_self_snapshots(keep=0)
    assert "snap_b1" in _ids(e), "#691：no_forget 标签的 self_snapshot 被 prune 删除"


def test_c_unprotected_snapshot_still_pruned():
    """C：防误杀——未保护的快照仍被正常清理。"""
    e = SpacetimeMemoryEngine(":memory:")
    _insert_snapshot(e, "snap_c0", ["self_snapshot"])
    _insert_snapshot(e, "snap_c1", ["self_snapshot"])
    n = e.prune_self_snapshots(keep=0)
    assert n == 2, "#691：未保护快照清理面被收死：deleted=%r" % (n,)
    assert _ids(e) == set(), "#691：未保护快照未清理：%r" % (_ids(e),)


def test_d_keep_semantics_with_protected():
    """D：受保护件不占 keep 名额；未受保护件仍按 rowid 降序保留最近 keep 条。"""
    e = SpacetimeMemoryEngine(":memory:")
    # 插入序：c0 最旧 … c3 最新；保护最旧的 c0
    for i in range(4):
        _insert_snapshot(e, "snap_d%d" % i, ["self_snapshot"])
    e.protect_node("snap_d0", "保护最旧")
    e.prune_self_snapshots(keep=2)
    ids = _ids(e)
    assert "snap_d0" in ids, "#691：受保护的最旧快照被删"
    assert {"snap_d2", "snap_d3"} <= ids, "#691：keep=2 未保留最近两条：%r" % (ids,)
    assert "snap_d1" not in ids, "#691：超 keep 的未保护快照未清理：%r" % (ids,)
