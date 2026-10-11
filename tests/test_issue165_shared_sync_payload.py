# -*- coding: utf-8 -*-
"""test_issue165_shared_sync_payload · 蜂群分层同步载荷须为共享层且可无损回放（lingshu #165）
============================================================================
缺陷（#165 · 成立）：唯一的「蜂群分层同步」`prepare_shared_sync` 导出的是**本地
知识层**、结构层/锚点层 0 条进入载荷；载荷**有损**（content 截 100 字符、缺
layer/confidence/condition_space/tags）；全仓**零消费方**（只有生产者）。

修法：
  ① `prepare_shared_sync` 按分层章程（`LayeredStore` 类头：共享层=锚点+结构、
     跨实例同步；本地层=知识/情境/自我、实例独立）改导出**共享层**，字段按
     `SHARED_SYNC_FIELDS` 全量导出（可无损回放）。
  ② 新增接收端 `apply_shared_sync`（尽力而为）：按 layer 重建；本地层条目拒绝；
     同 id 冲突**显式报告**、默认不覆盖（`overwrite=True` 才落库且仍入 conflicts）；
     坏条目不毒化整批。

断言组（回退即红）：
  P1 载荷层 = 共享层（anchor/structure），知识层条目**不在**载荷里。
  P2 载荷字段齐全（含 layer/confidence/condition_space/tags/state_attributes），
     且 content **不截断**（无损）。
  P3 回放无损：apply 到新引擎后，锚点/结构节点的 content/tags/confidence/
     condition_space/importance 与原一致。
  P4 冲突显式：同 id 内容不同 ⇒ 入 `conflicts`、默认不落库；`overwrite=True`
     才落库且仍在 `conflicts` 里（绝不静默覆盖）。
  P5 越界拒绝：载荷含本地层（knowledge）条目 ⇒ 入 `rejected`、零落库。
  P6 幂等重放：同一载荷二次 apply ⇒ 全部入 `identical`、零重复落库。

定点变异自证（抽掉关键一行 ⇒ 必红并点名）：
  · 把 `prepare_shared_sync` 的 `WHERE layer IN (...)` 改回 `WHERE layer='knowledge'`
    ⇒ P1/P2/P3 红。
  · 把 `apply_shared_sync` 的 `if not overwrite: continue` 删掉 ⇒ P4 红。

运行（仓根）：python -X utf8 -m pytest tests/test_issue165_shared_sync_payload.py -q
"""
from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    SpacetimeMemoryEngine, MemoryLayer,
)

#: 载荷必须覆盖的重建字段（无损回放的最小集）
REQUIRED_FIELDS = {"id", "content", "modality", "temporal_coordinate",
                   "condition_space", "importance", "confidence", "layer",
                   "tags", "semantic_coordinates", "state_attributes",
                   "entity_id"}


class SharedSyncPayloadTests(unittest.TestCase):

    def setUp(self):
        self.engine = SpacetimeMemoryEngine(":memory:", identity="guard-165")
        self.addCleanup(self.engine.close)
        self.engine.add_perception("本地知识一条", tags=["k"])   # 本地层，不应入载荷
        self.anchor = self.engine.set_anchor("锚点一条")
        self.struct = self.engine.add_structure_node("结构一条")

    # ---- P1 载荷层 = 共享层 ----
    def test_p1_payload_is_shared_layer_only(self):
        p = self.engine.prepare_shared_sync()
        layers = {it["layer"] for it in p["sync_payload"]}
        self.assertEqual(layers, {"anchor", "structure"})
        ids = {it["id"] for it in p["sync_payload"]}
        self.assertIn(self.anchor.id, ids)
        self.assertIn(self.struct.id, ids)
        # 知识层条目不得进载荷
        self.assertFalse(any(it["layer"] == "knowledge"
                             for it in p["sync_payload"]))

    # ---- P2 字段齐全 + content 不截断 ----
    def test_p2_payload_fields_complete_and_lossless(self):
        long_content = "长内容" * 100    # 300 字，旧实现截到 100
        self.engine.add_structure_node(long_content)
        p = self.engine.prepare_shared_sync()
        for it in p["sync_payload"]:
            self.assertTrue(REQUIRED_FIELDS <= set(it.keys()),
                            "缺字段：%r" % (REQUIRED_FIELDS - set(it.keys())))
        kept = [it for it in p["sync_payload"] if it["content"] == long_content]
        self.assertEqual(len(kept), 1, "content 被截断 ⇒ 无损回放不成立")

    # ---- P3 回放无损 ----
    def test_p3_apply_rebuilds_losslessly(self):
        p = self.engine.prepare_shared_sync()
        e2 = SpacetimeMemoryEngine(":memory:", identity="guard-165b")
        self.addCleanup(e2.close)
        res = e2.apply_shared_sync(p)
        self.assertEqual(set(res["applied"]), {self.anchor.id, self.struct.id})
        got_a = e2.store.get_node(self.anchor.id)
        got_s = e2.store.get_node(self.struct.id)
        self.assertEqual(got_a.content, self.anchor.content)
        self.assertEqual(got_a.layer, MemoryLayer.ANCHOR)
        self.assertEqual(got_a.confidence, self.anchor.confidence)
        self.assertEqual(got_a.tags, self.anchor.tags)
        self.assertEqual(got_a.condition_space.to_json(),
                         self.anchor.condition_space.to_json())
        self.assertEqual(got_s.content, self.struct.content)
        self.assertEqual(got_s.layer, MemoryLayer.STRUCTURE)
        self.assertEqual(got_s.importance, self.struct.importance)

    # ---- P4 冲突显式、默认不覆盖 ----
    def test_p4_conflict_reported_not_silently_overwritten(self):
        p = self.engine.prepare_shared_sync()
        e2 = SpacetimeMemoryEngine(":memory:", identity="guard-165c")
        self.addCleanup(e2.close)
        e2.apply_shared_sync(p)
        # 造一个同 id 但内容不同的既有节点
        from lingshu.core.core import STNode, ConditionSpace
        e2.store.add_node(STNode(
            id=self.anchor.id, content="接收侧不同内容", modality="protocol",
            spatial_coordinates={}, temporal_coordinate=1.0,
            condition_space=ConditionSpace("p", "t", (0, 1), "e"),
            layer=MemoryLayer.ANCHOR, tags=["anchor"]))
        res = e2.apply_shared_sync(p)
        self.assertIn(self.anchor.id, [c["id"] for c in res["conflicts"]])
        self.assertNotIn(self.anchor.id, res["applied"])
        self.assertEqual(e2.store.get_node(self.anchor.id).content, "接收侧不同内容")
        # overwrite=True 才覆盖，且仍留在 conflicts 里（不静默）
        res2 = e2.apply_shared_sync(p, overwrite=True)
        self.assertIn(self.anchor.id, res2["applied"])
        self.assertIn(self.anchor.id, [c["id"] for c in res2["conflicts"]])
        self.assertEqual(e2.store.get_node(self.anchor.id).content,
                         self.anchor.content)

    # ---- P5 越界拒绝 ----
    def test_p5_local_layer_rejected(self):
        p = self.engine.prepare_shared_sync()
        forged = dict(p["sync_payload"][0])
        forged["id"] = "forged_knowledge"
        forged["layer"] = "knowledge"
        res = self.engine.apply_shared_sync(
            {"sync_payload": p["sync_payload"] + [forged]})
        self.assertIn("forged_knowledge", [r["id"] for r in res["rejected"]])
        self.assertIsNone(self.engine.store.get_node("forged_knowledge"))

    # ---- P6 幂等重放 ----
    def test_p6_replay_is_idempotent(self):
        p = self.engine.prepare_shared_sync()
        e2 = SpacetimeMemoryEngine(":memory:", identity="guard-165d")
        self.addCleanup(e2.close)
        e2.apply_shared_sync(p)
        res = e2.apply_shared_sync(p)
        self.assertEqual(res["applied"], [])
        self.assertEqual(set(res["identical"]),
                         {self.anchor.id, self.struct.id})
        self.assertEqual(res["conflicts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
