# -*- coding: utf-8 -*-
"""治理写面「同列 / 同键两套口径」三处收口 —— 守卫（lingshu/core/core.py）

病灶（本族共性）：**同一列 / 同一个键存在两套口径**——一处走单点判据、另一处裸写。
本件把下列三处收口到**既有单点**（不另造第二份判据）：

  · P1 `SpacetimeMemoryEngine.annotate_semantics`
      `UPDATE nodes SET semantic_coordinates=? WHERE id=?` 此前**零角色闸** ⇒ SUB
      实测把 PRIMARY 建的锚点/结构层行语义坐标由 `{}` 改成
      `{"protocol":{"concept":{"object":0.75}}}`，PRIMARY 跨实例读回同值；而同实例
      单点 `assert_node_writable_by_role` 对同一行必拒 ⇒ 同列两套口径。
      收口：写前调 `self.store.assert_node_writable_by_role(...)`。
  · P2 `SpacetimeMemoryEngine.adjudicate_verifier_standard`
      自开一句 `INSERT OR REPLACE INTO engine_meta(key,value)` 写治理键
      `verifier_config`（∈ `ENGINE_META_GUARDED_KEYS`）此前**无角色闸** ⇒ SUB 走完
      A-2 四步实测把 `dedup_static` `0.85 → 0.5` 写穿库、PRIMARY 重启回读生效；
      而**同一键**经 `store.set_meta` 必被 `#768` 单点拒 ⇒ 同键两套口径。
      收口：`approved` 分支内、`_apply_verifier_standard` 之前调
      `self.store._assert_meta_key_writable("verifier_config")`。
  · P3 `LayeredStore.delete_node` 的 **OPPOSITE 邻居标签清理腿**
      主闸只判**被删目标行**（`node.layer in IMMUTABLE_LAYERS`），随后
      `for peer in peers:` 里 `UPDATE nodes SET tags=?` **无任何层/角色判据** ⇒ SUB
      删本地行时实测把锚点 peer 的 `tags` 由 `["conflict","no_forget"]` 改成
      `["no_forget"]`，PRIMARY 跨实例读回同值；而同列经 `tag_node` 打共享层目标必被
      `#234` 拦 ⇒ 同列两套口径。
      收口：peer 循环内、`UPDATE` 之前判该 peer 写权限；不可写 ⇒ **跳过该 peer 的
      标签清理 + 计入 `last_skipped_rows`**（**不抛错**，与同段 `#720` 坏 tags 容错
      同款口径）。
      ★后果面（如实标注）：跳过会在共享层 peer 上留下**悬挂的 `conflict` 标签**
      （OPPOSITE 边已随删除消失、标签未被移除）。这是「宁可留脏标签，也不让非
      PRIMARY 改写共享层」的取舍；清理由 PRIMARY 侧承担。取向不取抛错的理由：
      `delete_node` 的语义是删**本地层**节点，因一个**邻居**是共享层就让整条删除
      失败，会把 `decay_cycle` / `enforce_context_cap` 等既有删除路径打成红。

既有单点（本件只调用，不复制）：
  · `LayeredStore.layer_writable_by_role` / `assert_node_writable_by_role`
    （G 线本笔 `d0c2be3` 落地；判据＝层 ∈ `SHARED_LAYERS` 且 role != PRIMARY ⇒
    `PermissionError`；形状＝`#127` D-07「写入前查旧行」）；
  · `LayeredStore._assert_meta_key_writable`（`#768` 落地；判据＝
    `key ∈ ENGINE_META_GUARDED_KEYS` 且 role != PRIMARY 且非 `:memory:` 库 ⇒
    `PermissionError`）。

断言组（逐项回退 ⇒ 对应组必红；全撤 ⇒ A/B/C 同时红）：
  A（P1）SUB 对 ANCHOR / STRUCTURE 行调 `annotate_semantics` ⇒ `PermissionError`，
    且该行 `semantic_coordinates` **逐字未变**（前后快照对比）；PRIMARY 对同一行调
    ⇒ `True` 且真写入。
  B（P2）SUB 走完整 A-2 四步（propose→review→cs_review→adjudicate）⇒
    `PermissionError`，且 `engine_meta` 的 `verifier_config` **逐字未变**、进程内
    `_dedup_static` **未变**（内存回退）、`verifier_standards` 行状态未变（零落库）；
    PRIMARY 同流程 ⇒ `True` 且写入生效（**跨实例重启回读**核一次）。
  C（P3）PRIMARY 建「本地行 + 锚点行（带 `conflict` 标签）+ OPPOSITE 边」⇒ SUB
    `delete_node(本地行)`：① 返回 `True`；② 锚点行 tags **逐字未变**（`conflict`
    仍在）；③ 该 peer id 出现在 `store.last_skipped_rows`。对照腿：peer 是**本地层**
    行时标签照旧被清（防过修）。
  D（防过修 / 回归钉）SUB 对自己的本地层行调 `annotate_semantics` 仍 `True`；SUB 删
    自己的本地行、peer 也是本地行时 `conflict` 标签照旧被清（既有 `#115` 行为不回退）。
  E（量具自检）断言闸**确实被调到过**——拒态下确认快照确实读到了行（
    `semantic_coordinates` 非 None），且对单点直接调一次确认它在该角色/层组合下会拒。

运行（仓根）：python -X utf8 -m pytest tests/test_governance_write_surface_closure_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import pytest  # noqa: E402

from lingshu.core.core import (  # noqa: E402
    ConditionSpace, MemoryLayer, Role, SpacetimeMemoryEngine, STNode,
)

pytestmark = pytest.mark.filterwarnings("ignore")

#: 设计者密钥从**环境变量**取（照仓内既有守卫件做法），不把密钥字面量写进测试件。
KEY = "k-guard-write-surface-closure"


def _cs():
    return ConditionSpace("守卫", "守卫", (0.0, 1e12), "运行中")


def _node(nid, layer, tags=None, content=None):
    return STNode(id=nid, content=content or ("节点 " + nid), modality="text",
                  spatial_coordinates={}, temporal_coordinate=1.0,
                  condition_space=_cs(), importance=0.9, confidence=1.0,
                  layer=layer, tags=list(tags or []))


class _Provider:
    """duck-typed 语义提供者：只实现本件用到的 `to_coordinates`。"""

    def __init__(self, out):
        self._out = dict(out)

    def to_coordinates(self, text):
        return dict(self._out)


def _semantic_raw(store, nid):
    """读回 `semantic_coordinates` **原始文本**（逐字比对用；缺行 ⇒ None）。"""
    row = store.conn.execute(
        "SELECT semantic_coordinates FROM nodes WHERE id=?", (nid,)).fetchone()
    return None if row is None else row[0]


def _tags_raw(store, nid):
    row = store.conn.execute(
        "SELECT tags FROM nodes WHERE id=?", (nid,)).fetchone()
    return None if row is None else row[0]


def _tags(store, nid):
    return json.loads(_tags_raw(store, nid) or "[]")


def _meta_verifier_config(store):
    return store.get_meta("verifier_config").get("verifier_config")


def _seed_primary(path):
    """PRIMARY 建库：锚点 / 结构 / 知识三行 + 一条本地行。"""
    p = SpacetimeMemoryEngine(path)
    p.store.add_node(_node("w_anchor", MemoryLayer.ANCHOR))
    p.store.add_node(_node("w_struct", MemoryLayer.STRUCTURE))
    p.store.add_node(_node("w_know", MemoryLayer.KNOWLEDGE))
    return p


# ================================================================== A（P1）

def test_a_p1_sub_annotate_semantics_on_shared_layer_denied_and_unchanged(tmp_path):
    """A（P1）：SUB 不得经 `annotate_semantics` 改写共享层行语义坐标。"""
    path = str(tmp_path / "p1.db")
    p = _seed_primary(path)
    p.store.conn.close()

    sub = SpacetimeMemoryEngine(path, role=Role.SUB)
    try:
        sub.set_semantic_provider(_Provider({"protocol_object": 0.75}))
        for nid in ("w_anchor", "w_struct"):
            before = _semantic_raw(sub.store, nid)
            # E（量具自检）：拒态下快照**确实读到了行**（防「闸没生效但测试因别的
            # 原因绿」——行读不到时快照恒 None，比对会退化成 None == None）。
            assert before is not None, "E：快照未读到行 %s（量具失效）" % nid
            assert json.loads(before or "{}") == {}, "前置：%s 语义坐标非空" % nid
            with pytest.raises(PermissionError):
                sub.annotate_semantics(nid)
            after = _semantic_raw(sub.store, nid)
            assert after == before, (
                "A/P1：SUB 改写了共享层行 %s 的 semantic_coordinates：%r -> %r"
                % (nid, before, after))
    finally:
        sub.store.conn.close()

    # 跨实例读回：PRIMARY 重开同一库，共享层行语义坐标仍为空
    p2 = SpacetimeMemoryEngine(path, role=Role.PRIMARY)
    try:
        for nid in ("w_anchor", "w_struct"):
            assert json.loads(_semantic_raw(p2.store, nid) or "{}") == {}, (
                "A/P1：SUB 的改写写穿库（PRIMARY 跨实例读回非空）：%s" % nid)
    finally:
        p2.store.conn.close()


def test_a_p1_primary_annotate_semantics_on_shared_layer_succeeds(tmp_path):
    """A（P1 正对照）：PRIMARY 对同一行调 ⇒ `True` 且真写入（不误伤合法路径）。"""
    path = str(tmp_path / "p1ok.db")
    p = _seed_primary(path)
    try:
        p.set_semantic_provider(_Provider({"protocol_object": 0.75}))
        for nid in ("w_anchor", "w_struct", "w_know"):
            before = _semantic_raw(p.store, nid)
            assert p.annotate_semantics(nid) is True, "A/P1 正对照：%s 被误拒" % nid
            after = json.loads(_semantic_raw(p.store, nid) or "{}")
            assert after.get("protocol", {}).get("concept", {}).get("object") == 0.75, (
                "A/P1 正对照：%s 未真写入（before=%r after=%r）" % (nid, before, after))
    finally:
        p.store.conn.close()


def test_e_p1_single_point_denies_that_role_layer_combo(tmp_path):
    """E（量具自检）：单点在该「角色 × 层」组合下**确实会拒**。"""
    path = str(tmp_path / "p1e.db")
    p = _seed_primary(path)
    p.store.conn.close()
    sub = SpacetimeMemoryEngine(path, role=Role.SUB)
    try:
        assert sub.store.layer_writable_by_role(MemoryLayer.ANCHOR) is False, (
            "E：单点对 ANCHOR × SUB 判为可写（量具失效）")
        assert sub.store.layer_writable_by_role(MemoryLayer.KNOWLEDGE) is True, (
            "E：单点对 KNOWLEDGE × SUB 判为不可写（判据过宽）")
        with pytest.raises(PermissionError):
            sub.store.assert_node_writable_by_role("w_anchor")
        sub.store.assert_node_writable_by_role("w_know")   # 本地层 ⇒ 不抛
    finally:
        sub.store.conn.close()


# ================================================================== B（P2）

def _a2_four_steps(engine, vid_proposer="proposer", reviewer="reviewer",
                   cs_reviewer="cs_reviewer", adjudicator="adjudicator",
                   value=0.5):
    """走完整 A-2 四步（propose→review→cs_review→adjudicate），返回 vid 与终裁结果。"""
    vid = engine.store.add_verifier_standard(
        "dedup_static 标定", "dedup_static", value, "守卫构造", vid_proposer)
    assert engine.store.review_verifier_standard(vid, reviewer, True) is True
    assert engine.store.cs_review_verifier_standard(vid, cs_reviewer, True) is True
    return vid, engine.adjudicate_verifier_standard(
        vid, adjudicator, True, designer_key=os.environ.get("AEIS_DESIGNER_KEY"))


def test_b_p2_sub_adjudicate_denied_meta_and_memory_unchanged(tmp_path, monkeypatch):
    """B（P2）：SUB 走完 A-2 四步 ⇒ 拒，库内 `verifier_config` 与内存配置都不动。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    path = str(tmp_path / "p2.db")
    p = _seed_primary(path)
    # PRIMARY 写入基线 `verifier_config`（治理键基线值）
    p.store.set_meta("verifier_config", json.dumps(
        {"dedup_static": 0.85, "deviation_threshold": 0.3}))
    p.store.conn.close()

    sub = SpacetimeMemoryEngine(path, role=Role.SUB)
    try:
        meta_before = _meta_verifier_config(sub.store)
        assert meta_before is not None, "B 前置：基线 verifier_config 未落库"
        assert sub._dedup_static == 0.85, "B 前置：SUB 初始 dedup_static=%r" % sub._dedup_static
        vid = sub.store.add_verifier_standard(
            "dedup_static 标定", "dedup_static", 0.5, "守卫构造", "proposer")
        assert sub.store.review_verifier_standard(vid, "reviewer", True) is True
        assert sub.store.cs_review_verifier_standard(vid, "cs_reviewer", True) is True
        with pytest.raises(PermissionError):
            sub.adjudicate_verifier_standard(
                vid, "adjudicator", True,
                designer_key=os.environ.get("AEIS_DESIGNER_KEY"))
        assert sub._dedup_static == 0.85, (
            "B/P2：被拒后内存 `_dedup_static` 已改（%r，静默分叉）" % sub._dedup_static)
        assert sub._verifier_config.get("dedup_static") == 0.85, (
            "B/P2：被拒后内存 `_verifier_config` 已改：%r" % sub._verifier_config)
        row = sub.store.conn.execute(
            "SELECT status FROM verifier_standards WHERE id=?", (vid,)).fetchone()
        assert row is not None and row[0] != "approved", (
            "B/P2：被拒的终裁仍落库（verifier_standards.status=%r）" % (row,))
    finally:
        sub.store.conn.close()

    p2 = SpacetimeMemoryEngine(path, role=Role.PRIMARY)
    try:
        assert _meta_verifier_config(p2.store) == meta_before, (
            "B/P2：SUB 的终裁写穿 `engine_meta.verifier_config`：%r -> %r"
            % (meta_before, _meta_verifier_config(p2.store)))
        assert p2._dedup_static == 0.85, (
            "B/P2：PRIMARY 跨实例重启回读被篡改的 dedup_static：%r" % p2._dedup_static)
    finally:
        p2.store.conn.close()


def test_b_p2_primary_adjudicate_succeeds_and_persists(tmp_path, monkeypatch):
    """B（P2 正对照）：PRIMARY 同流程 ⇒ `True`、写入生效、跨实例重启回读生效。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    path = str(tmp_path / "p2ok.db")
    p = _seed_primary(path)
    try:
        vid, r = _a2_four_steps(p)
        assert r is True, "B/P2 正对照：PRIMARY 终裁被误拒（返回值 %r）" % (r,)
        assert p._dedup_static == pytest.approx(0.5, abs=1e-9), (
            "B/P2 正对照：内存 dedup_static=%r" % p._dedup_static)
        assert json.loads(_meta_verifier_config(p.store))["dedup_static"] == pytest.approx(
            0.5, abs=1e-9), "B/P2 正对照：盘面 verifier_config 未写入"
        assert p.store.conn.execute(
            "SELECT status FROM verifier_standards WHERE id=?", (vid,)).fetchone()[0] \
            == "approved", "B/P2 正对照：裁决未落库"
    finally:
        p.store.conn.close()

    p2 = SpacetimeMemoryEngine(path, role=Role.PRIMARY)
    try:
        assert p2._dedup_static == pytest.approx(0.5, abs=1e-9), (
            "B/P2 正对照：跨实例重启回读 dedup_static=%r（应为 0.5）" % p2._dedup_static)
    finally:
        p2.store.conn.close()


def test_e_p2_single_point_denies_verifier_config_for_sub(tmp_path):
    """E（量具自检）：`_assert_meta_key_writable("verifier_config")` 在 SUB + 文件库下必拒。"""
    path = str(tmp_path / "p2e.db")
    p = _seed_primary(path)
    p.store.conn.close()
    sub = SpacetimeMemoryEngine(path, role=Role.SUB)
    try:
        with pytest.raises(PermissionError):
            sub.store._assert_meta_key_writable("verifier_config")
        sub.store._assert_meta_key_writable("_last_decay_at")   # 非治理键 ⇒ 不抛
    finally:
        sub.store.conn.close()


# ================================================================== C（P3）

def _conflict_pair(path, a_id, a_layer, b_id, b_layer, a_tags=None, b_tags=None):
    """PRIMARY 建 a/b 两行并登记 OPPOSITE 冲突（两端各带 `conflict` 标签）。"""
    p = SpacetimeMemoryEngine(path)
    p.store.add_node(_node(a_id, a_layer, tags=a_tags))
    p.store.add_node(_node(b_id, b_layer, tags=b_tags))
    edge = p.store.register_conflict(a_id, b_id)
    assert edge is not None, "前置：register_conflict 未建边"
    assert "conflict" in _tags(p.store, a_id) and "conflict" in _tags(p.store, b_id), (
        "前置：register_conflict 未打 conflict 标签")
    p.store.conn.close()
    return edge


def test_c_p3_sub_delete_local_does_not_touch_shared_peer_tags(tmp_path):
    """C（P3）：SUB 删本地行时不得改写共享层 peer 的 `tags`；跳过须记账。"""
    path = str(tmp_path / "p3.db")
    # 锚点 peer 额外带 `no_forget`（复刻线 N 实测的 `["conflict","no_forget"]` 形态）
    _conflict_pair(path, "c_local", MemoryLayer.KNOWLEDGE, "c_anchor",
                   MemoryLayer.ANCHOR, b_tags=["no_forget"])
    p0 = SpacetimeMemoryEngine(path, role=Role.PRIMARY)
    try:
        anchor_before = _tags_raw(p0.store, "c_anchor")
        assert sorted(json.loads(anchor_before)) == ["conflict", "no_forget"], (
            "C 前置：锚点 peer 的 tags 形态不符：%r" % anchor_before)
    finally:
        p0.store.conn.close()

    sub = SpacetimeMemoryEngine(path, role=Role.SUB)
    try:
        assert sub.store.delete_node("c_local") is True, (
            "C/P3：SUB 删自己的本地行被拒（本件取向＝不阻断删除主流程）")
        assert _tags_raw(sub.store, "c_anchor") == anchor_before, (
            "C/P3：锚点 peer 的 tags 被 SUB 改写：%r -> %r"
            % (anchor_before, _tags_raw(sub.store, "c_anchor")))
        assert "conflict" in _tags(sub.store, "c_anchor"), (
            "C/P3：锚点 peer 的 conflict 标签被移除（缺陷态）")
        assert "c_anchor" in (sub.store.last_skipped_rows or []), (
            "C/P3：被跳过的 peer 未计入 `last_skipped_rows`：%r"
            % (sub.store.last_skipped_rows,))
        assert sub.store.get_node("c_local") is None, "C/P3：本地行未被删除"
    finally:
        sub.store.conn.close()

    p2 = SpacetimeMemoryEngine(path, role=Role.PRIMARY)
    try:
        assert _tags_raw(p2.store, "c_anchor") == anchor_before, (
            "C/P3：跨实例读回锚点 peer 的 tags 已变：%r" % _tags_raw(p2.store, "c_anchor"))
    finally:
        p2.store.conn.close()


def test_c_p3_contrast_local_peer_tags_still_cleared(tmp_path):
    """C（P3 对照腿 / 防过修）：peer 是**本地层**行时，`conflict` 标签照旧被清。"""
    path = str(tmp_path / "p3local.db")
    _conflict_pair(path, "d_local", MemoryLayer.KNOWLEDGE, "d_peer",
                   MemoryLayer.KNOWLEDGE)
    sub = SpacetimeMemoryEngine(path, role=Role.SUB)
    try:
        assert sub.store.delete_node("d_local") is True
        assert "conflict" not in _tags(sub.store, "d_peer"), (
            "C/P3 对照腿：本地层 peer 的 conflict 标签未被清（过修）：%r"
            % (_tags(sub.store, "d_peer"),))
        assert "d_peer" not in (sub.store.last_skipped_rows or []), (
            "C/P3 对照腿：本地层 peer 被误计入 `last_skipped_rows`：%r"
            % (sub.store.last_skipped_rows,))
    finally:
        sub.store.conn.close()


# ================================================================== D（防过修）

def test_d_sub_local_writes_still_allowed(tmp_path):
    """D：SUB 对自己的本地层行照旧可写（P1 / P3 均不得过修）。"""
    path = str(tmp_path / "d.db")
    p = _seed_primary(path)
    p.store.add_node(_node("d_local", MemoryLayer.KNOWLEDGE))
    p.store.conn.close()

    sub = SpacetimeMemoryEngine(path, role=Role.SUB)
    try:
        sub.set_semantic_provider(_Provider({"protocol_object": 0.75}))
        assert sub.annotate_semantics("d_local") is True, (
            "D：SUB 对自己的本地层行调 annotate_semantics 被误拒（过修）")
        assert json.loads(_semantic_raw(sub.store, "d_local") or "{}").get(
            "protocol", {}).get("concept", {}).get("object") == 0.75, (
            "D：SUB 的本地层行语义坐标未真写入")
    finally:
        sub.store.conn.close()


def test_d_sub_delete_own_local_pair_clears_conflict(tmp_path):
    """D（回归钉）：SUB 删自己的本地行、peer 亦本地层 ⇒ `conflict` 照旧被清（#115）。"""
    path = str(tmp_path / "d2.db")
    _conflict_pair(path, "e_local", MemoryLayer.CONTEXT, "e_peer", MemoryLayer.KNOWLEDGE)
    sub = SpacetimeMemoryEngine(path, role=Role.SUB)
    try:
        assert sub.store.delete_node("e_local") is True
        assert "conflict" not in _tags(sub.store, "e_peer"), (
            "D 回归钉：SUB 删本地行后本地层 peer 的 conflict 标签未被清：%r"
            % (_tags(sub.store, "e_peer"),))
    finally:
        sub.store.conn.close()


def test_d_primary_delete_local_peer_local_unchanged_behavior(tmp_path):
    """D（正对照）：PRIMARY 删本地行、peer 为本地层 ⇒ 标签照旧被清（既有行为不动）。"""
    path = str(tmp_path / "d3.db")
    _conflict_pair(path, "f_local", MemoryLayer.KNOWLEDGE, "f_peer", MemoryLayer.KNOWLEDGE)
    p = SpacetimeMemoryEngine(path, role=Role.PRIMARY)
    try:
        assert p.store.delete_node("f_local") is True
        assert "conflict" not in _tags(p.store, "f_peer"), (
            "D 正对照：PRIMARY 删本地行后 peer 标签未被清")
        assert not (p.store.last_skipped_rows or []), (
            "D 正对照：PRIMARY 路径误记账：%r" % (p.store.last_skipped_rows,))
    finally:
        p.store.conn.close()
