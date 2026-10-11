# -*- coding: utf-8 -*-
"""#268 M5 去重判同只比 content、完全无视条件空间 —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #268，判定「成立」）：
  `add_perception` 的 M5 去重只算 `char_bigram_jaccard(content, n.content)`，候选节点
  的 `condition_space` 完全不参与比较。于是同一句话在不同「存在约束 / 时间窗 / 观测
  位置 / 观测工具」下被判为同一条知识：新传入的 `condition_space` 被整体丢弃、旧节点
  被 `update_node_confidence(+0.02)` 增信、并打 `duplicate` 标签。知识层对同一句话
  **结构上只能保存一个条件版本**（且是最早写入的那个），违背理论锚点 `K=K(C)`。

修法（最小 · 按 issue「建议 1」的判同半句）：
  去重候选须过条件空间判据 `_conditions_mergeable(new_cs, n.condition_space)`——
  `observation_position` / `observation_tool` / `existence_constraint` 三维度**字面
  相等** 且 `time_window` **有交集**；任一不满足即「不同条件」⇒ 跳过该候选、另立节点。

判据来源（**理论章节**，非经验标定）：
  `docs/theory/世界模型与语义时空图_完整理论整理与实现路线.md` L35
  「知识 = 条件空间 C 中的有效关系 `K=K(C)`」＋ L154「任何知识/识别须绑定条件
  （观测位置/工具/时间/存在约束）」＋ 公开 issue #268「建议 1」。
边界（削掉的判别力，如实标注）：①只做相等/相交判据，不推理条件包含关系；
  ②时间窗相邻不重叠仍判不同（「取并集扩展旧时间窗」属另一改动，本件不做）；
  ③条件不同的同正文写入**不**自动建 SIMILAR/APPLIES_TO 边、不落冲突。

断言组（抽掉修复——删掉 `_conditions_mergeable` 闸——Q1/Q2 必红）：
  Q1 异条件不合并：同正文、三条不同 `existence_constraint` 的写入 ⇒ 三个独立节点，
     各自保留自己的条件；成人版置信度**不因**另外两条上升（旧：返回同一节点、0.54）。
  Q2 时间窗不同亦不合并：仅 `time_window` 不重叠（存在约束相同）⇒ 另立节点。
  Q3 防误杀（M5 原有功能不回退）：条件空间**完全相同**的逐字重复仍正常去重。

运行（仓根）：python -X utf8 -m pytest tests/test_issue268_dedup_condition_space_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import (  # noqa: E402
    ConditionSpace, MemoryLayer, SpacetimeMemoryEngine,
)

_TEXT = "布洛芬混悬液推荐剂量为每次10毫升，每日不超过4次"


def _cs(constraint, t0=None, pos="药品说明书", tool="人工录入"):
    t0 = time.time() if t0 is None else t0
    return ConditionSpace(pos, tool, (t0, t0 + 3600), constraint)


def _rows(e):
    c = e.store.conn.cursor()
    c.execute("SELECT condition_space, confidence FROM nodes "
              "WHERE layer='knowledge' AND content=?", (_TEXT,))
    return [(ConditionSpace.from_json(r[0]), r[1]) for r in c.fetchall()]


def test_q1_distinct_conditions_not_merged():
    """Q1：同正文、不同存在约束 ⇒ 各自独立节点，旧节点不增信。"""
    e = SpacetimeMemoryEngine(":memory:")
    now = time.time()
    a = e.add_perception(_TEXT, condition_space=_cs("适用人群：12岁以上及成人", now))
    b = e.add_perception(_TEXT, condition_space=_cs("适用人群：6岁以下儿童禁用此剂量", now))
    c = e.add_perception(_TEXT, condition_space=_cs("该剂量已于2026-09-01撤销", now + 86400))
    assert not (a.id == b.id == c.id), (
        "#268：异条件的同正文写入被并成同一节点（条件空间未参与判同）")
    rows = _rows(e)
    assert len(rows) == 3, (
        "#268：知识层同内容节点数 = %d（应为 3，各留一个条件版本）" % len(rows))
    constraints = {cs.existence_constraint for cs, _ in rows}
    assert constraints == {"适用人群：12岁以上及成人",
                           "适用人群：6岁以下儿童禁用此剂量",
                           "该剂量已于2026-09-01撤销"}, (
        "#268：条件声明未各自落库——实测 %r" % constraints)
    for _cs_obj, conf in rows:
        assert conf == 0.5, (
            "#268：异条件观测给旧节点增信（置信度应为 0.5）——实测 %r" % conf)


def test_q2_disjoint_time_windows_not_merged():
    """Q2：存在约束相同、仅时间窗不重叠 ⇒ 亦另立节点。"""
    e = SpacetimeMemoryEngine(":memory:")
    now = time.time()
    a = e.add_perception(_TEXT, condition_space=_cs("协议实例运行中", now))
    b = e.add_perception(_TEXT, condition_space=_cs("协议实例运行中", now + 100000))
    assert a.id != b.id, "#268：时间窗不重叠的同正文写入被合并"
    assert e.store.count_layer(MemoryLayer.KNOWLEDGE) == 2, (
        "#268：知识层节点数 = %d（应为 2）"
        % e.store.count_layer(MemoryLayer.KNOWLEDGE))


def test_q3_identical_conditions_still_dedup():
    """Q3：防误杀——条件空间完全相同的逐字重复仍正常去重（M5 不回退）。"""
    e = SpacetimeMemoryEngine(":memory:")
    now = time.time()
    same = _cs("协议实例运行中", now)
    a = e.add_perception(_TEXT, condition_space=same)
    b = e.add_perception(_TEXT, condition_space=same)
    assert a.id == b.id, "同条件的逐字重复未被去重（条件闸误伤正常去重）"
    assert e.store.count_layer(MemoryLayer.KNOWLEDGE) == 1
    assert "duplicate" in e.store.get_node(a.id).tags, "重复命中未留痕"
