# -*- coding: utf-8 -*-
"""#415 annotate_semantics 写错列、覆盖既有真实位置 —— 守卫

缺陷（公开 issue #415 · [core/语义坐标] · 判定「成立」）：
  `SpacetimeMemoryEngine.annotate_semantics(node_id)` 执行
      UPDATE nodes SET spatial_coordinates=? WHERE id=?
  把 provider 的**语义**坐标写进**物理位置**列：补注语义却覆盖了节点的真实
  空间坐标，而 `semantic_coordinates` 仍为空。
  issue 实测（本仓复现同款读数）：
      spatial_coordinates  {"x":1.0,"y":2.0,"z":3.0} → {"protocol_object":0.75}
      semantic_coordinates {}                          → {}
      返回值 True（提交成功）

修法（最小 · 按 issue「修复方向」）：
  ① 新增模块级 `_flat_semantic_to_nested`——把 provider 的扁平语义键按
     `migrate_v17_coordinates` 的既有 D-003 映射转成嵌套语义结构
     （`protocol_X`→`protocol.concept.X` / `radical_X`→`radical.X` /
     `neural_X`→`neural.X`；非前缀键原样保留）；
  ② `annotate_semantics` 改为**合并**写入 `semantic_coordinates`，
     `spatial_coordinates` 一字不动。

判据来源：公开 issue #415「保留真实位置，将 provider 输出转换为当前语义坐标结构
  后写入 `semantic_coordinates`……不能仅机械替换列名」＋ 本文件
  `migrate_v17_coordinates` 的 D-003 映射先例（`protocol_`/`radical_`/`neural_`
  前缀分桶）。仓内无规定该落点的理论章节，属经验标定（本件 #415）。
边界（削掉的判别力）：只做前缀分桶，**不**校验语义坐标的值类型/范围；写入前也
  **不**校验 provider 输出形态（非 dict 归一为 `{}`，不报错）。

断言组（抽掉修复＝把 UPDATE 列名改回 `spatial_coordinates` ⇒ A/C 必红）：
  A 真实位置保留：调用后 `spatial_coordinates` 逐键等于调用前
  B 语义补注生效：`semantic_coordinates` 按 D-003 结构含 provider 输出
  C 幂等/合并：二次补注不丢既有语义桶、仍不碰 `spatial_coordinates`
  D 防误杀：无 provider / 无该节点 ⇒ 仍返回 False，且不写任何列

运行（仓根）：python -X utf8 -m pytest tests/test_issue415_annotate_semantics_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


class _Provider:
    """duck-typed 语义提供者：只实现本件用到的 `to_coordinates`。"""

    def __init__(self, out):
        self._out = out

    def to_coordinates(self, text):
        return dict(self._out)


def _spatial(e, nid):
    row = e.store.conn.execute(
        "SELECT spatial_coordinates FROM nodes WHERE id=?", (nid,)).fetchone()
    import json
    return json.loads(row[0]) if row and row[0] else {}


def _semantic(e, nid):
    row = e.store.conn.execute(
        "SELECT semantic_coordinates FROM nodes WHERE id=?", (nid,)).fetchone()
    import json
    return json.loads(row[0]) if row and row[0] else {}


def test_a_real_position_preserved():
    """A：补注后真实空间坐标逐键不变（旧缺陷：被 provider 输出整体覆盖）。"""
    e = SpacetimeMemoryEngine(":memory:")
    node = e.add_perception("红色方块位于工作台",
                            spatial_coordinates={"x": 1.0, "y": 2.0, "z": 3.0},
                            skip_dedup=True)
    before = _spatial(e, node.id)
    assert before == {"x": 1.0, "y": 2.0, "z": 3.0}, "前置：真实位置未落库 %r" % (before,)

    e.set_semantic_provider(_Provider({"protocol_object": 0.75}))
    assert e.annotate_semantics(node.id) is True
    after = _spatial(e, node.id)
    assert after == before, (
        "#415 补注语义覆盖了真实位置：%r -> %r（旧实现写 spatial_coordinates）"
        % (before, after))


def test_b_semantic_column_annotated():
    """B：语义补注落在 `semantic_coordinates`（按 D-003 嵌套结构）。"""
    e = SpacetimeMemoryEngine(":memory:")
    node = e.add_perception("红色方块位于工作台",
                            spatial_coordinates={"x": 1.0, "y": 2.0, "z": 3.0},
                            skip_dedup=True)
    e.set_semantic_provider(_Provider({"protocol_object": 0.75, "radical_红": 0.9}))
    assert e.annotate_semantics(node.id) is True
    se = _semantic(e, node.id)
    assert se.get("protocol", {}).get("concept", {}).get("object") == 0.75, (
        "#415 语义坐标未按 D-003 结构补注：%r" % (se,))
    assert se.get("radical", {}).get("红") == 0.9, "#415 radical 桶未补注：%r" % (se,)


def test_c_merge_keeps_existing_semantic_buckets():
    """C：二次补注**合并**（不丢既有语义桶），且始终不碰 `spatial_coordinates`。"""
    e = SpacetimeMemoryEngine(":memory:")
    node = e.add_perception("红色方块位于工作台",
                            spatial_coordinates={"x": 1.0, "y": 2.0, "z": 3.0},
                            skip_dedup=True)
    # 先塞一个既有语义桶（模拟 v1.7 写入的 neural 桶）
    e.store.conn.execute(
        "UPDATE nodes SET semantic_coordinates=? WHERE id=?",
        ('{"neural": {"v1": 0.1}}', node.id))
    e.store.conn.commit()

    e.set_semantic_provider(_Provider({"protocol_object": 0.75}))
    e.annotate_semantics(node.id)
    se = _semantic(e, node.id)
    assert se.get("neural", {}).get("v1") == 0.1, "#415 既有语义桶被补注冲掉：%r" % (se,)
    assert se.get("protocol", {}).get("concept", {}).get("object") == 0.75, (
        "#415 新桶未合入：%r" % (se,))
    assert _spatial(e, node.id) == {"x": 1.0, "y": 2.0, "z": 3.0}, (
        "#415 二次补注改动了真实位置：%r" % (_spatial(e, node.id),))


def test_d_no_provider_or_node_returns_false():
    """D：防误杀——无 provider / 无该节点 ⇒ False，且不写任何列。"""
    e = SpacetimeMemoryEngine(":memory:")
    node = e.add_perception("红色方块位于工作台",
                            spatial_coordinates={"x": 1.0, "y": 2.0, "z": 3.0},
                            skip_dedup=True)
    # 无 provider
    assert e.annotate_semantics(node.id) is False
    assert _spatial(e, node.id) == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert _semantic(e, node.id) == {}
    # 有 provider 但节点不存在
    e.set_semantic_provider(_Provider({"protocol_object": 0.75}))
    assert e.annotate_semantics("no_such_node") is False
