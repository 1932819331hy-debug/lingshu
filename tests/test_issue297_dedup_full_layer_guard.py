# -*- coding: utf-8 -*-
"""#297 M5 去重视野只有「importance/last_access 前 200 条」知识节点 —— 守卫
（lingshu/core/core.py）

缺陷（公开 issue #297，判定「成立」）：
  `add_perception` 的去重候选 = `query_nodes(layer=KNOWLEDGE, limit=200)`，而该查询
  的排序是 `ORDER BY importance DESC, last_access DESC`（为检索服务）。知识层超过
  200 条后，**较早写入、未被访问**的内容对去重不可见 ⇒ 逐字相同的感知被重复入库
  （同一事实拆成多份节点，增信/实体标签/访问计数分散在副本上，recall 被重复项占位）。

修法（最小 · 按 issue「修复建议 2」）：
  候选改为**全层同模态**（`limit=-1`，SQLite LIMIT -1 = 不限条数）——去重视野覆盖
  整个知识层，不再随库规模静默退化。

判据来源：公开 issue #297（标题/复现）＋ 该 issue「修复建议 2：全层扫描」（经验标定；
  仓内理论章节未规定 M5 候选视野口径，追不到更早出处）。
边界（削掉的判别力/代价，如实标注）：写入代价随知识层规模线性增长；issue 建议 1 的
  `content LIKE` 预筛在本仓无收益（`nodes.content` 无索引，LIKE 亦为全表扫描）；
  issue 建议 3（维持 200 并声明）不采纳——那只是把漏去重写进文档。

断言组（抽掉修复——`limit=-1` 改回 `limit=200`——Q1 必红）：
  Q1 去重穿透 top-200：写入目标句 → 210 条无关节点（skip_dedup）→ 重写目标句 ⇒
     命中**原节点**、库内副本数 == 1。
  Q2 正对照：同样条件下，被重复写入的原节点**不在** importance 前 200 名内
     （证明 Q1 确实考的是「视野穿透」而非巧合）。
  Q3 防误杀：无关内容不因全层视野被误合并（知识层节点数 = 目标 + 填充 + 重写命中）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue297_dedup_full_layer_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import MemoryLayer, SpacetimeMemoryEngine  # noqa: E402

_TEXT = "用户对青霉素过敏，处方时须避开"
_FILLERS = 210
#: 目标节点 importance 取低值（0.05 < 填充节点 0.5），使其在
#: `ORDER BY importance DESC, last_access DESC` 下**确定性**地排到 200 名之外
#: （仅靠 last_access 排序在 Windows 时钟粒度下并列、名次不确定，会削弱守卫判别力）。
_LOW = 0.05


def _fill(e, n=_FILLERS):
    for i in range(n):
        e.add_perception("第%d条日常观测记录：编号%d，主题%d" % (i, i * 7919, i % 13),
                         skip_dedup=True)


def _copies(e):
    return sum(1 for n in e.store.query_nodes(layer=MemoryLayer.KNOWLEDGE, limit=100000)
               if n.content == _TEXT)


def test_q1_dedup_sees_beyond_top200():
    """Q1：210 条无关节点之后，重写目标句仍命中原节点、副本数 == 1。"""
    e = SpacetimeMemoryEngine(":memory:")
    target = e.add_perception(_TEXT, importance=_LOW)
    _fill(e)
    again = e.add_perception(_TEXT, importance=0.5)
    assert again.id == target.id, (
        "#297：重写未命中原节点（去重视野被 200 截断）——target=%s got=%s"
        % (target.id, again.id))
    assert _copies(e) == 1, (
        "#297：库内同内容副本数 = %d（应为 1）" % _copies(e))


def test_q2_target_outside_importance_top200():
    """Q2 正对照：原节点确不在 importance 前 200 名内（Q1 考的是视野穿透）。"""
    e = SpacetimeMemoryEngine(":memory:")
    target = e.add_perception(_TEXT, importance=_LOW)
    _fill(e)
    top200 = e.store.query_nodes(layer=MemoryLayer.KNOWLEDGE, limit=200)
    assert target.id not in {n.id for n in top200}, (
        "#297 前置不成立：原节点仍在 importance 前 200 名内，本守卫失去判别力")


def test_q3_unrelated_content_not_merged():
    """Q3：防误杀——无关内容不因全层视野被误合并。"""
    e = SpacetimeMemoryEngine(":memory:")
    target = e.add_perception(_TEXT, importance=_LOW)
    _fill(e, 20)
    other = e.add_perception("完全无关的观测：今日气温偏低，注意保暖")
    assert other.id != target.id, "#297：无关内容被误合并进目标节点"
    assert e.store.count_layer(MemoryLayer.KNOWLEDGE) == 22, (
        "知识层节点数 = %d（应为 1 目标 + 20 填充 + 1 无关）"
        % e.store.count_layer(MemoryLayer.KNOWLEDGE))
