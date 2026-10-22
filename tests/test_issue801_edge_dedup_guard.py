# -*- coding: utf-8 -*-
"""守卫 · 引擎 add_edge 对同 (source, target, relation_type) 去重（lingshu issue #801）

缺陷（HEAD 修复前，`lingshu/core/core.py`）
    `SpacetimeMemoryEngine.add_edge(a.id, b.id, relation_type=EdgeType.CAUSAL)` 对
    同一 (source_id, target_id, relation_type) 可无限重复调用，每次产出新的独立边
    记录（节点侧有 M5 去重，边侧零去重）。改前实测读数：

        5 次 add_edge 同参数 → 5 个不同 edge_id（`SELECT COUNT(*) FROM edges` = 5）

    后果（issue #801 列举）：find_cycles/shortest_path 的多路计数膨胀、
    infer_causal_paths 的置信度重复计入、存储无界增长（#327 当年即以 k 条平行边
    压测出 105 万条路径/1.3GB——枚举侧已加 max_paths，写入侧一直没堵）。

修复
    写入前 SELECT 同 (source_id, target_id, relation_type) 既有边（ORDER BY
    created_at, id 取最早一条，跨平台确定）——存在则返回已有边（带 `merged=True`
    属性标记，与节点侧 M5/#223 同款），不存在才新建（`merged=False`）。
    `dedup=False`（issue 建议 2）：显式要求平行边时跳过查重照旧插入。

判据来源
    · 题面读数即判据：公开 issue #801 的 5 次 add_edge 复现。
    · 命中口径取「返回已有边」而非 raise：与仓内去重先例 M5（#223：命中返回
      原节点 + merged=True 标记，调用方无需 try/except）一致。
    · 同 (source, target) 不同 relation_type 不算重复（语义不同的关系）；
      `LayeredStore.add_edge(STEdge)` 直写面不去重（同步/导入保真）。

定点变异自证（抽掉修复 ⇒ 必红）
    把查重段整体删除（或恒不命中）⇒ G1 红（5 次调用回不到同一 edge_id）、
    G3 红（库里回到 5 条边）；把 `merged` 标记删掉 ⇒ G2 红（无法区分已存在/新建）。
    恢复 ⇒ 全绿。

运行（仓根）：python -X utf8 -m pytest tests/test_issue801_edge_dedup_guard.py -q --no-header
"""
from pathlib import Path
import os
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from lingshu.core.core import EdgeType, SpacetimeMemoryEngine  # noqa: E402


def _engine():
    db = os.path.join(tempfile.mkdtemp(), "edges.db")
    return SpacetimeMemoryEngine(db)


# ---------- G1：同参数重复添加 → 每次返回同一条边（issue 复现读数 5→1） ----------
def test_g1_repeated_add_returns_same_edge():
    e = _engine()
    a = e.add_perception("A", skip_dedup=True)
    b = e.add_perception("B", skip_dedup=True)
    ids = [e.add_edge(a.id, b.id, relation_type=EdgeType.CAUSAL).id for _ in range(5)]
    assert len(set(ids)) == 1, (
        "#801：5 次同参数 add_edge 产出 %d 个 edge_id（期望 1）" % len(set(ids)))
    assert e.store.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1


# ---------- G2：命中返回已有边（merged=True），新建 merged=False——M5/#223 同款 ----------
def test_g2_merged_marker_distinguishes_hit_and_new():
    e = _engine()
    a = e.add_perception("A", skip_dedup=True)
    b = e.add_perception("B", skip_dedup=True)
    first = e.add_edge(a.id, b.id, relation_type=EdgeType.CAUSAL)
    assert getattr(first, "merged", False) is False, "新建路径应为 merged=False"
    again = e.add_edge(a.id, b.id, relation_type=EdgeType.CAUSAL)
    assert again.id == first.id
    assert getattr(again, "merged", False) is True, (
        "#801：命中路径应带 merged=True（与 M5/#223 标记同款），否则调用方"
        "无法区分「已存在」与「新建」")
    # 命中不增信（#29①「只计复用不增信」同向）：既有边 confidence 不被覆盖
    assert again.confidence == first.confidence


# ---------- G3：不同 relation_type 各自成边；dedup=False 显式逃生口 ----------
def test_g3_relation_types_distinct_and_dedup_false_escape():
    e = _engine()
    a = e.add_perception("A", skip_dedup=True)
    b = e.add_perception("B", skip_dedup=True)
    e.add_edge(a.id, b.id, relation_type=EdgeType.CAUSAL)
    sim = e.add_edge(a.id, b.id, relation_type=EdgeType.SIMILAR)
    assert getattr(sim, "merged", False) is False, "不同 relation_type 不是重复"
    n = e.store.conn.execute(
        "SELECT COUNT(*) FROM edges WHERE source_id=? AND target_id=?",
        (a.id, b.id)).fetchone()[0]
    assert n == 2, "同端点两种关系类型应各有一条边（得到 %d）" % n
    # 显式平行边（issue 建议 2 的逃生口，#327 守卫构图口径）
    p1 = e.add_edge(a.id, b.id, relation_type=EdgeType.CAUSAL, dedup=False)
    p2 = e.add_edge(a.id, b.id, relation_type=EdgeType.CAUSAL, dedup=False)
    assert p1.id != p2.id, "dedup=False 应照旧产出独立边"
    assert e.store.conn.execute(
        "SELECT COUNT(*) FROM edges WHERE source_id=? AND target_id=? "
        "AND relation_type='causal'", (a.id, b.id)).fetchone()[0] == 3


# ---------- G4：反向边不是同一条边（有向图语义不被误伤） ----------
def test_g4_direction_matters():
    e = _engine()
    a = e.add_perception("A", skip_dedup=True)
    b = e.add_perception("B", skip_dedup=True)
    ab = e.add_edge(a.id, b.id, relation_type=EdgeType.CAUSAL)
    ba = e.add_edge(b.id, a.id, relation_type=EdgeType.CAUSAL)
    assert ab.id != ba.id, "a→b 与 b→a 是两条不同的有向边"


if __name__ == "__main__":
    rc = pytest.main([__file__, "-q", "--no-header"])
    sys.exit(rc)
