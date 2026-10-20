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

    # =====================================================================
    # #165 裁定路 B · §4（来源标识 ＋ 落库审计）/ §5a（上行待晋升副本分组导出）
    #
    # 本组只追加，P1–P6 一字未动。判据来源：本端 2026-10-16 对 issue #165 的
    # 裁定帖（<!-- RULING-165 -->）第 4 条（载荷带来源实例标识并落审计，使「这条是
    # 谁推上来的」可查）与第 5 条上半（SUB 侧 pending_sync / external_anchor 待晋升
    # 副本应**单列分组**导出）。
    #
    # 未落地（如实标注，与裁定帖有出入）：路 B 第 1/2/3 条（新增 id 走
    # propose→verify→adjudicate、overwrite 无密钥即拒、载荷带内容摘要哈希且缺哈希
    # 即拒）**全部暂缓** —— 与在-main 的既有守卫正面冲突，编排侧 2026-10-17 裁定
    # 暂缓；故本组**不含**这三条的断言。
    # =====================================================================

    #: 审计行类型（与 `core._audit_shared_sync_apply` 的 action_type 同字面量）
    AUDIT_TYPE = "shared_sync_applied"

    def _audit_rows(self, engine):
        """取某引擎的同步落库审计行 `[(summary, node_ids_json, outcome_json)]`。"""
        c = engine.store.conn.cursor()
        c.execute("SELECT summary, node_ids, outcome FROM action_logs"
                  " WHERE action_type=? ORDER BY id", (self.AUDIT_TYPE,))
        return c.fetchall()

    # ---- A1 载荷带 source_instance 键且 == 导出实例 identity ----
    def test_a1_payload_carries_source_instance_key(self):
        """A1：顶层键**必须在场**，且取值 == 导出实例的 identity。"""
        p = self.engine.prepare_shared_sync()
        self.assertIn("source_instance", p,
                      "载荷缺 source_instance 键（#165 路 B §4）")
        self.assertEqual(p["source_instance"], self.engine.self_model.identity)
        # 无值时必须写**显式 None**（键缺席是歧义源，None 才是可机器判读的
        # 「我们知道这里没填」）——造一个 identity 为空的实例作正对照。
        e3 = SpacetimeMemoryEngine(":memory:", identity=None)
        self.addCleanup(e3.close)
        p3 = e3.prepare_shared_sync()
        self.assertIn("source_instance", p3, "identity 为空时键仍必须在场")
        self.assertIsNone(p3["source_instance"])

    # ---- A2 落库后 action_logs 有该行、含来源标识与条数 ----
    def test_a2_apply_records_audit_row_with_source_and_count(self):
        """A2：发生落库 ⇒ 落一条审计，含来源实例标识与落库条数。

        现状读数（本线探针，改动前）：`action_logs` 中 `shared_sync*` 行数 **0**
        ⇒ 本断言在缺陷形态下必红（回退即红，见留痕）。
        """
        import json as _json
        p = self.engine.prepare_shared_sync()
        e2 = SpacetimeMemoryEngine(":memory:", identity="guard-165b")
        self.addCleanup(e2.close)
        self.assertEqual(self._audit_rows(e2), [],
                         "前置：接收侧库应零审计行")
        res = e2.apply_shared_sync(p)
        self.assertTrue(res["applied"], "前置：本用例需要真的发生落库")
        rows = self._audit_rows(e2)
        self.assertEqual(len(rows), 1, "落库未留痕（#165 路 B §4）")
        summary, node_ids_json, outcome_json = rows[0]
        self.assertIn(str(len(res["applied"])), summary)
        for nid in res["applied"]:
            self.assertIn(nid, node_ids_json)
        outcome = _json.loads(outcome_json)
        self.assertEqual(outcome.get("source_instance"),
                         self.engine.self_model.identity,
                         "审计未记来源实例标识")
        self.assertEqual(outcome.get("applied_count"), len(res["applied"]))
        self.assertIs(outcome.get("overwrite"), False)

    # ---- A3 幂等重放（零落库）⇒ 不新增审计行 ----
    def test_a3_idempotent_replay_adds_no_audit_row(self):
        """A3：全 `identical`、零落库的重放**不**新增审计行（防噪声）。"""
        p = self.engine.prepare_shared_sync()
        e2 = SpacetimeMemoryEngine(":memory:", identity="guard-165d")
        self.addCleanup(e2.close)
        e2.apply_shared_sync(p)
        before = len(self._audit_rows(e2))
        self.assertEqual(before, 1, "前置：首次落库应恰一行")
        res = e2.apply_shared_sync(p)
        self.assertEqual(res["applied"], [])
        self.assertEqual(set(res["identical"]),
                         {self.anchor.id, self.struct.id})
        self.assertEqual(len(self._audit_rows(e2)), before,
                         "幂等重放（零落库）新增了审计行 ⇒ 噪声")

    # ---- B1 PRIMARY 导出 ⇒ pending_promotions 键在场且为空 ----
    def test_b1_primary_export_has_empty_pending_promotions(self):
        """B1：PRIMARY 侧无待晋升副本 ⇒ 键**在场**、值为空表。"""
        p = self.engine.prepare_shared_sync()
        self.assertIn("pending_promotions", p,
                      "载荷缺 pending_promotions 键（#165 路 B §5a）")
        self.assertEqual(p["pending_promotions"], [])

    def _sub_engine(self, identity):
        """造一个 SUB 实例（构造本身不需密钥）。"""
        from lingshu.core.core import Role
        sub = SpacetimeMemoryEngine(":memory:", identity=identity,
                                    role=Role.SUB)
        self.addCleanup(sub.close)
        return sub

    def _designer_key_env(self, key="guard-165-key"):
        """临时置入设计者密钥（`register_external_anchor` 的 D-007 闸需要它），
        经 `addCleanup` **复原**环境（本守卫自管，不污染同进程的其它件）。"""
        import os as _os
        saved = _os.environ.get("AEIS_DESIGNER_KEY")
        _os.environ["AEIS_DESIGNER_KEY"] = key

        def _restore():
            if saved is None:
                _os.environ.pop("AEIS_DESIGNER_KEY", None)
            else:
                _os.environ["AEIS_DESIGNER_KEY"] = saved

        self.addCleanup(_restore)
        return key

    # ---- B2 SUB 导出 ⇒ 含待晋升副本，且 sync_payload 仍只含共享层 ----
    def test_b2_sub_pending_promotions_grouped_without_polluting_payload(self):
        """B2：SUB 的 `pending_sync` / `external_anchor` 副本单列分组导出，
        **不**混进 `sync_payload`（后者语义一字不动：仍只含共享层）。

        现状读数（本线探针，改动前）：`SUB sync_payload 条数: 0`、
        `pending_sync=1 external_anchor=1`、**无** `pending_promotions` 键。
        """
        sub = self._sub_engine("guard-165-sub")
        key = self._designer_key_env()
        node = sub.register_external_anchor(
            "external_calibration", "外部校准：输出须附来源", designer_key=key)
        p = sub.prepare_shared_sync()
        # ① sync_payload 仍只含共享层（SUB 侧共享层为空 ⇒ 空表）
        self.assertEqual(p["sync_payload"], [], "SUB 侧无共享层条目")
        self.assertEqual(set(p["layers"]), {"anchor", "structure"})
        # ② 待晋升副本单列分组导出：该条同时带两标签 ⇒ 两组都出现
        groups = p["pending_promotions"]
        self.assertEqual({g["pending_group"] for g in groups},
                         {"pending_sync", "external_anchor"},
                         "待晋升副本未按触发标签单列分组")
        self.assertEqual({g["id"] for g in groups}, {node.id})
        # ③ 来源标识随载荷带出（SUB 实例自述）
        self.assertEqual(p["source_instance"], "guard-165-sub")

    # ---- B3 无标签 / 仅前缀相似的条目 ⇒ 不进 pending_promotions ----
    def test_b3_untagged_or_prefix_similar_tags_not_exported(self):
        """B3（判别力）：无标签条目、以及仅**前缀相同**的标签
        （`pending_sync_extra`）都不得进 `pending_promotions`；正对照证明
        上一条不是「恒空」的假绿。"""
        sub = self._sub_engine("guard-165-b3")
        sub.add_perception("普通本地知识一条", tags=["k"])
        sub.add_perception("前缀相似标签一条", tags=["pending_sync_extra"])
        self.assertEqual(sub.prepare_shared_sync()["pending_promotions"], [],
                         "无标签 / 仅前缀相似的条目不得进 pending_promotions")
        # 正对照：真带 `pending_sync` 的副本必须进（否则本组是恒真的假绿）
        sub._write_structure_record("一条结构事件", "history")
        got = sub.prepare_shared_sync()["pending_promotions"]
        self.assertEqual([g["pending_group"] for g in got], ["pending_sync"])
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
