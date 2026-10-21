# -*- coding: utf-8 -*-
"""test_issue264_spatiotemporal_full_scan_guard · #264 时空检索取数截断守卫
============================================================================
缺陷（lingshu issue #264 · P1）：`LayeredStore.spatiotemporal_query` 用
`self.query_nodes(limit=200)` 取候选，而 `query_nodes` 的排序是
`ORDER BY importance DESC, last_access DESC`。库内节点超过 200 条后，
importance 排名 200 之外的低权重记忆**永远不被时空检索看到**——即使它在
时间与空间上都紧邻查询中心，也一律返回空列表（静默）。

修法：候选改全量（`query_nodes(limit=-1)`，SQLite `LIMIT -1` = 不限条数），
与同文件 `get_layer_nodes` / #258 / #297 同款口径。
判据来源：公开 issue #264 ＋ 分诊表 `triage_lingshu_ALL.md` 行 264。

守卫断言组：
  A 组（硬判据 · 界外紧邻目标必须命中）：总节点 205（中心 + 目标 + 203 噪声），
     目标 importance 最低（0.02）但在时空上紧邻中心；放宽半径后
     `spatiotemporal_query` 必须返回目标。旧实现（limit=200）⇒ 返回空 ⇒ 必红。
     同时钉住「旧候选集确实不含目标」以证明是取数截断（非过滤条件）。
  B 组（对照 · 界内照旧命中）：总节点 ≤ 200 时目标照旧命中。
  C 组（对照 · 距离过滤仍生效）：空间上很远的节点不得因全量候选而混入结果。

运行（仓根）：
    python -X utf8 -m pytest tests/test_issue264_spatiotemporal_full_scan_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _build(noise):
    """中心（0.5，原点）+ 目标（0.02，紧邻）+ noise 条高重要度远处噪声。

    坐标用 dict（`space_metric` 过滤按轴名取值，见 `spatial_coordinates` 字段类型
    `Dict[str, float]`）。
    """
    e = SpacetimeMemoryEngine(":memory:")
    center = e.add_perception("中心节点：基准事件", skip_dedup=True, importance=0.5,
                              spatial_coordinates={"x": 0.0, "y": 0.0, "z": 0.0})
    target = e.add_perception("目标节点：紧邻中心的事件", skip_dedup=True,
                              importance=0.02,
                              spatial_coordinates={"x": 0.001, "y": 0.001, "z": 0.0})
    for i in range(noise):
        e.add_perception(f"噪声节点{i}：远处事件{i}", skip_dedup=True,
                         importance=0.6,
                         spatial_coordinates={"x": 100.0 + i, "y": 100.0, "z": 0.0})
    return e, center, target


def test_neighbor_beyond_rank_200_is_found():
    """A 组：总节点 205（> 200）时，界外低权但时空紧邻的目标必须被查到。"""
    e, center, target = _build(203)
    try:
        total = e.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert total == 205, "前置：库规模应为 205，实际 %d" % total
        # 前置：目标确实落在 query_nodes(limit=200) 的界外（缺陷触发条件）
        old_visible = {n.id for n in e.store.query_nodes(limit=200)}
        assert target.id not in old_visible, (
            "前置：目标应落在 importance 降序前 200 之外")

        res = e.spatiotemporal_query(center.id, time_radius=1e9,
                                     space_metric="x", space_radius=1e6,
                                     max_results=500)
        found = any(n.id == target.id for n, _ in res)
        assert found, (
            "#264：时空紧邻的界外低权目标必须被返回（旧实现 limit=200 ⇒ 返回空）；"
            "实际命中 %d 条，目标命中=%s" % (len(res), found))
    finally:
        e.close()


def test_neighbor_in_range_still_found():
    """B 组（对照）：总节点 ≤ 200 时目标照旧命中。"""
    e, center, target = _build(100)
    try:
        res = e.spatiotemporal_query(center.id, time_radius=1e9,
                                     space_metric="x", space_radius=1e6,
                                     max_results=500)
        assert any(n.id == target.id for n, _ in res), (
            "B 组对照：界内时空紧邻目标应命中")
    finally:
        e.close()


def test_spatial_radius_still_filters_far_nodes():
    """C 组（对照）：空间距离过滤仍生效，远处噪声不得混入结果。"""
    e, center, target = _build(50)
    try:
        res = e.spatiotemporal_query(center.id, time_radius=1e9,
                                     space_metric="x", space_radius=0.5,
                                     max_results=500)
        ids = {n.id for n, _ in res}
        assert target.id in ids, "C 组对照：紧邻目标应命中"
        far = [n for n in e.store.query_nodes(limit=-1)
               if n.content.startswith("噪声节点")]
        assert not any(n.id in ids for n in far), (
            "C 组对照：空间上很远的噪声节点不得混入结果（%d 条混入）"
            % len([n for n in far if n.id in ids]))
    finally:
        e.close()
