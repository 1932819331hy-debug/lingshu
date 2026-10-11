# -*- coding: utf-8 -*-
"""#118 D-003 双签 / A-2 四步制衡缺「状态机 + 角色互斥 + 值域钳制」 —— 守卫

缺陷（lingshu issue #118 · 判定成立）：
  治理流水线**只验密钥、不验角色与状态**：
  · `LayeredStore.verify_promotion` 是**无条件** `UPDATE ... SET status='verified'`
    ⇒ 已 `rejected`/`approved` 的**终态**提案可被拉回复核态（终态可重开）；
  · 身份不互斥：`requester`/`verified_by`/`adjudicated_by`、`proposer`/
    `independent_reviewer`/`cs_reviewer`/`adjudicator` 均为自由文本、无相等性判断
    ⇒ 同一身份可走完四步「制衡」；
  · `SpacetimeMemoryEngine.adjudicate_verifier_standard` 原样写入终裁值
    （`self._dedup_static = result["value"]`）——而公开入口 `set_dedup_config`
    有 `min(0.95, max(0.5, ...))` 钳制 ⇒ **最严格路径反而无钳制**。

修法（最小）：
  ① `verify_promotion` 加状态门（仅 `pending`）+ 身份互斥（`verified_by != requester`）；
  ② A-2 三条（`review`/`cs_review`/`adjudicate_verifier_standard`）加身份两两互斥；
  ③ 引擎终裁应用复用 `set_dedup_config` 的值域钳制。

判据来源：
  · `docs/plans/待裁清单_v0.1.md` **C-3 / D-01**（终态不可重开＋重开走新提案）、
    **C-4 / D-02**（三条流水线身份两两互斥）、**D-32**（钳制落点）；
  · 设计稿 `docs/plans/治理与写入边界_修复设计_v0.1.md` §4「修法形状 1/3/4」
    与 §4 守卫判据（终态不可重开 / 身份互斥 / 值域钳制）。

断言组（抽掉修复即红）：
  A 终态不可重开：`propose→verify→adjudicate(False)` 后再 `verify(同 id)` ⇒ 返回
     False、状态**仍 `rejected`**；`approved` 后再 `verify` 同样被拒。
  B 身份互斥（D-003）：`verify_promotion(verified_by == requester)` ⇒ False、状态
     仍 `pending`。
  C 值域钳制：A-2 终裁 `dedup_static=0.1` ⇒ 钳到 0.5；`=2.0` ⇒ 钳到 0.95
     （`_dedup_static` 与 `_verifier_config` 同口径）。
  D 身份互斥（A-2）：提议人自兼独立复核 / 终裁 ⇒ 被拒（返回 False/None、状态不变）。
  E 防误杀：合法三步（互异身份）照旧走通：复核 True、终裁 True、节点升结构层。

运行（仓根）：python -X utf8 -m pytest tests/test_issue118_governance_state_identity_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import MemoryLayer, SpacetimeMemoryEngine  # noqa: E402

KEY = "GUARD-118-KEY"


def _status(e, pid):
    return e.store.conn.execute(
        "SELECT status FROM promotion_proposals WHERE id=?", (pid,)).fetchone()[0]


def _vs_status(e, vid):
    return e.store.conn.execute(
        "SELECT status FROM verifier_standards WHERE id=?", (vid,)).fetchone()[0]


# ---------- A 终态不可重开 ----------

def test_a_rejected_proposal_cannot_be_reverified(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(":memory:")
    try:
        n = e.add_perception("待晋升内容", skip_dedup=True)
        pid = e.propose_promotion(n.id, "提议者", "理由")
        assert e.verify_promotion(pid, "复核者") is True, "A 正常复核被误拒"
        assert e.adjudicate_promotion(pid, "终裁者", False, designer_key=KEY) is True
        assert _status(e, pid) == "rejected"
        # 缺陷形态：无条件 UPDATE 会把 rejected 拉回 verified
        assert e.verify_promotion(pid, "复核者") is False, (
            "A 终态提案被重新复核（旧缺陷：无条件 UPDATE 复活 rejected）")
        assert _status(e, pid) == "rejected", "A 终态被重开：%r" % (_status(e, pid),)
    finally:
        e.close()


def test_a_approved_proposal_cannot_be_reverified(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(":memory:")
    try:
        n = e.add_perception("待晋升内容2", skip_dedup=True)
        pid = e.propose_promotion(n.id, "提议者", "理由")
        e.verify_promotion(pid, "复核者")
        assert e.adjudicate_promotion(pid, "终裁者", True, designer_key=KEY) is True
        assert _status(e, pid) == "approved"
        assert e.verify_promotion(pid, "复核者") is False, "A approved 提案被重新复核"
        assert _status(e, pid) == "approved"
    finally:
        e.close()


# ---------- B 身份互斥（D-003） ----------

def test_b_requester_cannot_self_verify():
    e = SpacetimeMemoryEngine(":memory:")
    try:
        n = e.add_perception("自提自审探针", skip_dedup=True)
        pid = e.propose_promotion(n.id, "同一人", "理由")
        assert e.verify_promotion(pid, "同一人") is False, (
            "B 提议人自兼复核未被拒（旧缺陷：身份不互斥）")
        assert _status(e, pid) == "pending", "B 被拒后状态被改写：%r" % (_status(e, pid),)
        # 防误杀：换人即可复核
        assert e.verify_promotion(pid, "另一个人") is True
    finally:
        e.close()


# ---------- C 值域钳制 ----------

def _flow(e, value, proposer="提议人", reviewer="复核者", cs="空间复核", adj="终裁者"):
    vid = e.store.add_verifier_standard("去重基准", "dedup_static", value, "理由", proposer)
    assert e.store.review_verifier_standard(vid, reviewer, True) is True
    assert e.store.cs_review_verifier_standard(vid, cs, True) is True
    assert e.adjudicate_verifier_standard(vid, adj, True, designer_key=KEY) is True
    return vid


def test_c_low_out_of_range_value_is_clamped(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(":memory:")
    try:
        _flow(e, 0.1)
        assert e._dedup_static == 0.5, (
            "C 越界终裁值未钳到 0.5（旧缺陷：原样写入绕过 set_dedup_config 钳制）：%r"
            % (e._dedup_static,))
        assert e.get_verifier_config()["dedup_static"] == 0.5, (
            "C 配置快照未同步钳制值：%r" % (e.get_verifier_config(),))
    finally:
        e.close()


def test_c_high_out_of_range_value_is_clamped(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(":memory:")
    try:
        _flow(e, 2.0)
        assert e._dedup_static == 0.95, "C 越界终裁值未钳到 0.95：%r" % (e._dedup_static,)
    finally:
        e.close()


# ---------- D 身份互斥（A-2） ----------

def test_d_a2_identity_mutual_exclusion(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(":memory:")
    try:
        vid = e.store.add_verifier_standard("阈值", "dedup_static", 0.9, "理由", "甲")
        # 提议人自兼独立复核 ⇒ 拒
        assert e.store.review_verifier_standard(vid, "甲", True) is False, (
            "D A-2 提议人自兼独立复核未被拒")
        assert _vs_status(e, vid) == "pending"
        # 换人复核通过
        assert e.store.review_verifier_standard(vid, "乙", True) is True
        # 独立复核人自兼条件空间复核 ⇒ 拒
        assert e.store.cs_review_verifier_standard(vid, "乙", True) is False, (
            "D A-2 独立复核人自兼条件空间复核未被拒")
        assert e.store.cs_review_verifier_standard(vid, "丙", True) is True
        # 终裁人与前三步同人 ⇒ 拒（返回 None ⇒ 引擎 False）
        assert e.adjudicate_verifier_standard(vid, "甲", True, designer_key=KEY) is False, (
            "D A-2 终裁人与提议人同人未被拒")
        assert _vs_status(e, vid) == "cs_approved"
        # 换人终裁通过
        assert e.adjudicate_verifier_standard(vid, "丁", True, designer_key=KEY) is True
    finally:
        e.close()


# ---------- E 防误杀 ----------

def test_e_legit_flow_still_passes(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(":memory:")
    try:
        n = e.add_perception("合法晋升内容", skip_dedup=True)
        pid = e.propose_promotion(n.id, "提议者", "理由")
        assert e.verify_promotion(pid, "复核者") is True
        assert e.adjudicate_promotion(pid, "终裁者", True, designer_key=KEY) is True
        got = e.store.get_node(n.id)
        assert got.layer is MemoryLayer.STRUCTURE and got.confidence == 1.0, (
            "E 合法晋升被误杀：%s/%s" % (got.layer.value, got.confidence))
        assert e.get_verifier_config()["dedup_static"] == 0.85, (
            "E 未动过终裁时配置基线被改动：%r" % (e.get_verifier_config(),))
    finally:
        e.close()
