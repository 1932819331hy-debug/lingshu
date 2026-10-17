# -*- coding: utf-8 -*-
"""test_issue214_context_promotion_guard · #214「转移」这一步的守卫
============================================================================
缺陷（lingshu issue #214 · P1）：情境层记忆的**转移**（谁在什么时机把够格的
情境层记忆推上知识层/长期层）在仓内**零生产调用方**——
`promote_context_memories` / `LongTermMemoryGate.promote_from_context` 实现
齐备，但 `run_maintenance_cycle`（衰减 → 巩固 → 归纳）**不含**这一步。
后果（本机实测 · 改前读数）：单条**够格**情境记忆（门控评估
`layer=knowledge, importance=0.436`）跑 200 轮维护后 ⇒ **该节点已被删除**
（`decay_cycle` 在同一批周期里把它删了，删除不可逆）——不是首帖描述的
「importance=0 僵尸」，而是「**该提升的被删了**」。

修法（issue #214 裁定 · **路 A**，本守卫钉住的正是这一步）：
`run_maintenance_cycle` 新增**第一步** `promote_context_memories()`，位置取
**衰减之前**（依据：设计声明「短期先进情境层 → 睡眠巩固时再评估提升 →
不够格的自然遗忘」这一三段式，先提升、后遗忘与其顺序一致）。
`start_auto_decay` 的线程体**未动**（仍只调 `decay_cycle()`）——裁定明确
「不接进自动衰减线程」。

守卫断言组（**正向**，钉「转移」这一步）：
  A 组（硬判据 · 结局落层）：单条够格情境记忆跑 N 轮 `run_maintenance_cycle`
     ⇒ 该节点**仍在库中**、`layer` 落在知识层/长期层、tags 带 `promoted`。
     （改前 ⇒ 节点被 decay 删除 ⇒ `get_node` 为 None ⇒ 必红。）
  B 组（硬判据 · 本轮确有提升）：本轮 `run_maintenance_cycle()` 的返回值里
     提升计数 ≥ 1。（改前 ⇒ 无该读数 ⇒ 必红。）
  C 组（对照 · 无净写入）：提升**不新增节点**——`promote_from_context` 是
     `UPDATE` 既有节点（改 `layer`/`importance`/`tags`），节点总数不变。
     这与 `#263` A 组（维护周期无净写入）同口径，二者必须同时成立。
  D 组（对照 · 幂等，不反复提升）：提升后节点已不在情境层，后续轮次
     **不得**再把它当候选（提升计数回 0）、结局（layer/importance/tags）
     逐轮不变——钉住「不会反复提升」这一面。
  E 组（硬判据 · 长程）：同一条够格情境记忆跑 **200 轮**维护 ⇒ 仍不得被
     `decay` 删除（改前实测：200 轮后该节点已不存在 ⇒ 必红）。这一条钉的是
     裁定帖那条**保守推理**所防的形态——「删除不可逆」。

反向面（与 `#263` 共存）：`tests/test_issue263_maintenance_no_net_write_guard.py`
的 A 组（无净写入）与 B 组（`pending_verification` 概念不自学提权）在接上
promote 后**必须仍绿**——由该守卫自身承担；本件**不**改它，也**不**为变绿
放宽它的任何断言。

判别力自证（交付报告读数）：把 `run_maintenance_cycle` 里的
`promote_context_memories()` 调用删掉 ⇒ 本件**必红**（A 组节点被 decay 删掉）；
还原 ⇒ 复绿且 `git diff` 为空。

运行（仓根）：
    python -X utf8 -m pytest tests/test_issue214_context_promotion_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


#: 够格情境记忆的正文——与参照库三条知识**主题不同**，故 novelty 高
CONTEXT_CONTENT = "六边形蜂窝网格等距邻居编码的旋转等变上限定理"

#: 参照库（三条无关知识）：`_novelty` 的参考集是**全库**，库里必须有别的
#: 内容——否则 `known` 为空 ⇒ novelty 回中性 0.5 ⇒ 评分被压到知识层阈值之下
REFERENCE = [
    "水在标准大气压下一百摄氏度沸腾。",
    "地球绕太阳公转一周约一年。",
    "光在真空中的速度约每秒三十万公里。",
]

#: 「N 轮」取 3：够格节点在**第一轮**就被提升（实测 round1 promote=1），
#: 3 轮足以覆盖「提升 + 后续衰减/巩固/归纳都不再动它」这一段；
#: 不用 200 轮是为了让本件保持秒级（裁定帖的长程读数由探针承担）。
ROUNDS = 3

#: E 组的长程轮数——与裁定帖探针同量级（改前：该轮数下节点已被删除）。
#: 成本实测 200 轮 ≈ 0.22s（情境层只有 1 条候选）。
ROUNDS_LONG = 200


def _count(eng):
    return eng.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]


def _engine_with_qualified_context(tmp_path, name):
    """造「一条够格情境记忆 ＋ 参照知识库」，返回 (engine, node, eval_reading)。

    trust 显式拉到 0.9（3 个采样点）：使本条内容的门控评估**稳定**越过
    知识层阈值（0.40），不把守卫的成败押在阈值边界上。
    """
    eng = SpacetimeMemoryEngine(str(tmp_path / name))
    for i in range(3):
        eng.self_model.update_trust_state(0.9, i)
    for s in REFERENCE:
        eng.add_perception(s, importance=0.5, skip_dedup=True)
    node = eng.add_context(CONTEXT_CONTENT, importance=0.4)
    ev = eng._ensure_gate().evaluate(CONTEXT_CONTENT, "promote", node.tags, node.id)
    return eng, node, ev


def test_214_qualified_context_is_promoted_within_maintenance(tmp_path):
    """A 组：够格情境记忆跑 N 轮维护 ⇒ 落在知识层/长期层且带 promoted 标签。"""
    eng, node, ev = _engine_with_qualified_context(tmp_path, "ctx214a.db")
    try:
        assert ev["layer"] in ("knowledge", "long_term"), (
            "A 组前置：本条内容经门控评估本来就够格（实测 layer=%s imp=%s）"
            % (ev["layer"], ev["importance"]))
        for _ in range(ROUNDS):
            eng.run_maintenance_cycle()
        after = eng.store.get_node(node.id)
        assert after is not None, (
            "#214：够格情境记忆在维护周期内必须**先被提升**、不得被 decay 删掉"
            "（改前实测：200 轮后该节点已不存在）")
        assert after.layer.value in ("knowledge", "long_term"), (
            "#214：够格情境记忆的结局必须落在知识层/长期层；实测 layer=%s"
            % after.layer.value)
        assert "promoted" in (after.tags or []), (
            "#214：提升必须留 promoted 标签（区分「被提升」与「本来就是知识层」）；"
            "实测 tags=%s" % (after.tags,))
    finally:
        eng.close()


def test_214_maintenance_cycle_reports_promotion_count(tmp_path):
    """B 组：维护周期必须把「本轮提升几条」带出返回值（改前无此读数）。"""
    eng, node, ev = _engine_with_qualified_context(tmp_path, "ctx214b.db")
    try:
        r = eng.run_maintenance_cycle()
        assert r.get("promote", 0) >= 1, (
            "#214：维护周期必须跑一步 promote 并把提升条数带出返回值；实测 r=%r" % (r,))
    finally:
        eng.close()


def test_214_promotion_writes_no_new_nodes(tmp_path):
    """C 组（对照 · 与 #263 A 组同口径）：提升是 UPDATE，不新增节点。"""
    eng, node, ev = _engine_with_qualified_context(tmp_path, "ctx214c.db")
    try:
        before = _count(eng)
        for _ in range(ROUNDS):
            eng.run_maintenance_cycle()
        after = _count(eng)
        assert after == before, (
            "#214/#263 共存：提升只改既有节点的 layer/importance/tags，不得新增节点"
            "（before=%d after=%d）" % (before, after))
    finally:
        eng.close()


def test_214_long_run_qualified_context_not_deleted(tmp_path):
    """E 组（硬判据 · 长程）：够格情境记忆跑 200 轮维护不得被 decay 删除。

    改前实测（promote 未接线）：同一条记忆 200 轮后 `get_node` 为 None——
    `decay_cycle` 在同一批周期里把它删了，**删除不可逆**。本组钉住这一形态。
    """
    eng, node, ev = _engine_with_qualified_context(tmp_path, "ctx214e.db")
    try:
        for _ in range(ROUNDS_LONG):
            eng.run_maintenance_cycle()
        after = eng.store.get_node(node.id)
        assert after is not None, (
            "#214：够格情境记忆不得在长程维护里被 decay 删除（改前实测：%d 轮后"
            "该节点已不存在，删除不可逆）" % ROUNDS_LONG)
        assert after.layer.value in ("knowledge", "long_term"), (
            "#214：长程维护后结局必须落在知识层/长期层；实测 layer=%s"
            % after.layer.value)
        assert "promoted" in (after.tags or []), (
            "#214：长程维护后仍须带 promoted 标签；实测 tags=%s" % (after.tags,))
    finally:
        eng.close()


def test_214_promotion_is_idempotent_no_repeated_promotion(tmp_path):
    """D 组（对照 · 幂等）：提升后不再进候选，后续轮次不再提升、结局不变。"""
    eng, node, ev = _engine_with_qualified_context(tmp_path, "ctx214d.db")
    try:
        eng.run_maintenance_cycle()
        first = eng.store.get_node(node.id)
        assert first is not None and "promoted" in (first.tags or []), (
            "D 组前置：首轮须已完成提升；实测=%s"
            % (None if first is None else (first.layer.value, first.tags),))
        for _ in range(ROUNDS):
            r = eng.run_maintenance_cycle()
            assert r.get("promote", 0) == 0, (
                "#214：已提升节点不在情境层 ⇒ 后续轮次不得再提升它"
                "（不得反复提升）；实测 r=%r" % (r,))
        later = eng.store.get_node(node.id)
        assert later is not None, "#214：提升后的节点不得被 decay 删除"
        assert (later.layer.value, round(float(later.importance), 6),
                sorted(later.tags or [])) == (
                    first.layer.value, round(float(first.importance), 6),
                    sorted(first.tags or [])), (
            "#214：提升后结局必须逐轮稳定；first=%s later=%s"
            % ((first.layer.value, first.importance, first.tags),
               (later.layer.value, later.importance, later.tags)))
    finally:
        eng.close()



def test_214_promotion_runs_before_decay_so_below_threshold_qualified_context_is_rescued(tmp_path):
    """F 组（硬判据 · 位置）：`promote` 必须在 `decay_cycle` **之前**跑。

    场景（#214 裁定帖给「取衰减之前」的那条理由所防的形态）：情境节点**进周期时**
    importance 已在删除线（`decay_cycle` 的 `min_confidence`，默认 0.1）**之下**
    （历史库遗留 / 异常输入），但内容经门控评估**够格**。

      · promote 在 decay **之前** ⇒ 先被救走（提升为知识层 ⇒ 同轮 decay 不再删它）；
      · promote 在 decay **之后** ⇒ 同轮 decay 先删掉它（**删除不可逆**）⇒ promote 永远看不到它。

    判据来源：issue #214 裁定（路 A）「接进 `run_maintenance_cycle`，**位置取衰减之前**」。
    编排侧实测（两条腿）：位置在**之前** ⇒ 节点仍在库、`layer=knowledge`、
    `tags` 含 `promoted`；把 promote 挪到**之后** ⇒ 该节点**已被删除**。
    """
    eng = SpacetimeMemoryEngine(str(tmp_path / "ctx214f.db"))
    try:
        for i in range(3):
            eng.self_model.update_trust_state(0.9, i)
        for s in REFERENCE:
            eng.add_perception(s, importance=0.5, skip_dedup=True)
        # ★importance 刻意取 0.05：已在删除线（0.1）之下 ⇒ 只有「先提升」才救得回来
        node = eng.add_context(CONTEXT_CONTENT, importance=0.05)
        ev = eng._ensure_gate().evaluate(CONTEXT_CONTENT, "promote", node.tags, node.id)
        assert ev["layer"] in ("knowledge", "long_term"), (
            "F 组前置：本条内容经门控评估本来就够格（实测 layer=%s imp=%s）"
            % (ev["layer"], ev["importance"]))
        eng.run_maintenance_cycle()
        after = eng.store.get_node(node.id)
        assert after is not None, (
            "#214：够格情境记忆即便进周期时已在删除线之下，也必须**先被提升**救走——"
            "promote 若排在 decay 之后，同轮 decay 会先把它删掉（删除不可逆）")
        assert after.layer.value in ("knowledge", "long_term"), (
            "#214：结局须落在知识层/长期层；实测 layer=%s" % after.layer.value)
        assert "promoted" in (after.tags or []), (
            "#214：提升须留 promoted 标签；实测 tags=%s" % (after.tags,))
    finally:
        eng.close()
