# -*- coding: utf-8 -*-
"""#164 importance 全路径无值域校验 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #164 · 判定成立，实读复现见下）：
  `add_perception` 原样透传 `importance`、`ingest_frame` 取
  `frame_data.get("importance", 0.6)` 原样透传，二者汇入 `LayeredStore.add_node`
  时**无任何值域校验** ⇒ 一条 `importance=1e6` 的无关记忆在 `recall` 的线性加权
  （`0.5*sim + 0.3*importance + 0.2*recency`）下压过一切结构层规定；负数同理。
  实测（HEAD，修前）：`add_perception("x", importance=1e6)` 落库 importance=1000000.0。

修法（最小 · 唯一写入汇聚点归一）：在 `LayeredStore.add_node`（所有引擎写入路径
的汇聚点）把 `node.importance` 钳到 [0,1]，口径与同文件 `update_node_importance`
的 `MIN(1.0, MAX(0.0, ...))` 一致。

判据来源：`STNode.importance` 字段声明「重要性 [0,1]」＋同文件
`update_node_importance` 钳制先例＋对外工具表
`docs/mdcg/tool_table_v0.3.0.md`「importance 重要性[0,1]」；仓内无更早理论章节，
追不到更早出处。
边界（削掉的判别力）：钳制是**归一**而非拒绝（调用方传错值不报错）；`import_all`
绕过本方法，不在本闸范围内。

断言组（抽掉 add_node 里的钳制即 A/B/C/E 变红）：
  A `add_perception(importance=1e6)` ⇒ 落库 ≤ 1.0（修前 1000000.0）。
  B `add_perception(importance=-5)` ⇒ 落库 ≥ 0.0（修前 -5.0）。
  C `ingest_frame` 的 `frame_data["importance"]=300000` ⇒ 落库 ≤ 1.0。
  D 防误杀：区间内值 0.6 原样保留（不得被钳制改值）。
  E 检索面：越界写入不得再以超大分压过既有高分节点（钳后两者同为 1.0 上限）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue164_importance_range_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _db(name):
    return os.path.join(tempfile.mkdtemp(prefix="lingshu164_"), name + ".db")


def test_a_positive_out_of_range_clamped():
    e = SpacetimeMemoryEngine(_db("hi"))
    n = e.add_perception("无关记忆以超大 importance 入库", importance=1e6)
    assert n.importance <= 1.0, "越界 importance 未钳制：%r" % (n.importance,)
    assert e.store.get_node(n.id).importance <= 1.0


def test_b_negative_clamped():
    e = SpacetimeMemoryEngine(_db("lo"))
    n = e.add_perception("负 importance 入库", importance=-5)
    assert n.importance >= 0.0, "负 importance 未钳制：%r" % (n.importance,)
    assert e.store.get_node(n.id).importance >= 0.0


def test_c_ingest_frame_clamped():
    e = SpacetimeMemoryEngine(_db("frame"))
    n = e.ingest_frame({"caption": "帧", "importance": 300000})
    assert n.importance <= 1.0, "ingest_frame 越界未钳制：%r" % (n.importance,)
    assert e.store.get_node(n.id).importance <= 1.0


def test_d_in_range_preserved():
    """防误杀：区间内值原样保留（钳制不得改正常值）。"""
    e = SpacetimeMemoryEngine(_db("ok"))
    n = e.add_perception("正常 importance", importance=0.6)
    assert abs(e.store.get_node(n.id).importance - 0.6) < 1e-9
    n2 = e.add_context("正常情境 importance", importance=0.4)
    assert abs(e.store.get_node(n2.id).importance - 0.4) < 1e-9


def test_e_recall_not_dominated_by_oversized_importance():
    """越界写入经钳制后不得以超大分压过满分结构节点（上界一致）。"""
    e = SpacetimeMemoryEngine(_db("rank"))
    struct = e.add_structure_node("结构层规定：优先级最高", importance=1.0)
    wild = e.add_perception("结构层规定：优先级最高", importance=1e6)
    assert e.store.get_node(wild.id).importance <= e.store.get_node(struct.id).importance
