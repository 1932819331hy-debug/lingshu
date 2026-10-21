# -*- coding: utf-8 -*-
"""#769 ＋ #780 守卫：**治理列写面**补角色闸（TMS 传播 / 数值接口 / 自我快照回收 /
构造期坐标迁移 / 结构重要性重算链）—— 守卫（lingshu/core/core.py、core/tms.py）

两件同族：都是「治理列（`nodes.confidence` / `nodes.state_attributes` /
`nodes.importance` / `protections`）的写面缺角色闸」，且共用已登记缺口
`docs/plans/待裁清单_v0.1.md` **D-33**（`#127` 数值接口是否同批 → 「本批不动，
另笔」）⇒ 由本笔一并收口（**本笔即 D-33 的「另笔」**）。

缺陷态（修复前 `origin/main`，本端独立探针复现；报告人账号在公开面已不存在、
原 issue 正文不可见，判定沿用本端核验帖 `<!-- VERIFY-769 -->` / `<!-- VERIFY-780 -->`）：

  · ① `TruthMaintenance.propagate` 的 `UPDATE nodes SET confidence=? WHERE id=?`
    零角色判据 ⇒ SUB 调 `add_justification(锚点, [前提])` 即把**锚点行**的
    confidence `1.0 → 0.5` 并沿下游闭包传播，重开 PRIMARY 读回同值。
    （同族第二腿 `EvidenceLedger.record/sync` 已由 `#765`/`#774` 收口，本件只做
    **回归钉**，证明该闸未被放松。）
  · ② `LayeredStore.prune_self_snapshots` 的 `DELETE FROM nodes WHERE id=?`
    零角色判据 ⇒ SUB 硬删 SELF 层快照（`3 → 0`），而同层 `delete_node` 仍返回
    `False`（两层判据不一致）。
  · ③ `LayeredStore.migrate_v17_coordinates` 由 `__init__` **无条件**调用 ⇒
    **仅构造 SUB 实例**（不调任何写 API）即改写共享层行的
    `spatial_coordinates` / `semantic_coordinates`。
  · ④ `update_node_confidence` / `update_node_importance` 无层/角色判据 ⇒
    SUB 可改写锚点行（confidence / importance 各 `1.0 → 0.5`）。
  · ⑤（#780）`recalc_structural_importance` 整链零角色闸 ⇒ SUB 可把锚点
    `state_attributes` 由 `{}` 改成 `{'concept_influence': N}`；直调
    `_write_influence(锚点, x)` 亦放行；且共享层节点触发 `_protect_upstream` 的
    `#234` 闸时 `_write_influence` **已提交**而整次调用抛错 ⇒ **部分落库**。

修法（最小 · 复用既有单一真源，不另造第二套判据）：
  · 新增 `LayeredStore.node_layer_writable(layer)` / `assert_node_layer_writable(node_id)`
    ——**唯一判据单点**，判据与 `add_node` 的共享层闸逐字同款同源（层 ∈
    `SHARED_LAYERS` 且 `role != PRIMARY` ⇒ `PermissionError`），并按 `#127` 的
    D-07/D-08 形状**写前查旧行、按行判权限**。
  · 各旁路写面调用该单点：`tms.TruthMaintenance`（`add_justification` 写前判 +
    `propagate` 写回前**整体预判**＝先判后写、零落库）、`update_node_confidence`、
    `update_node_importance`、`_write_influence`、`_protect_upstream`。
  · `recalc_structural_importance` 在**每个节点**进入写入路径前按「目标行层 × 角色」
    判一次，非 PRIMARY 时**跳过共享层节点**（逐节点隔离 ⇒ 不再有「先写后抛」的
    部分落库），并把跳过数经返回键 `skipped_shared` 上报。
  · `migrate_v17_coordinates`：非 PRIMARY 时**只跳过共享层行**、本地层照迁
    （取向 (b)——既不越权改共享层，又不让 SUB 的本地数据永久停在旧格式）；
    跳过数经既有 `warnings.warn` 告警面上报，返回值形状不变。
  · `prune_self_snapshots`：非 PRIMARY **一行都不删**、返回 0 并经
    `warnings.warn` 上报（取向「返回 0 并上报」而非抛错——`update_self` 在 SUB 上
    合法（#191：SELF 属本地层），若在此抛错会把 SUB 的 `update_self` 一并打掉，
    属**回归 #191**；该取舍与代价在 docstring 与回帖中如实标注）。
  · `EvidenceLedger._assert_shared_layer_writable` 改为**委托**上述单点
    （判据不再有第二份实现）。

断言组（抽掉修复即红）：
  G1 TMS：SUB `add_justification(锚点, [前提])` ⇒ `PermissionError`、锚点 confidence
     逐位不变、`justifications` 表**零新增行**（闸在 INSERT 之前）；PRIMARY 读回同值。
  G2 TMS 闭包：SUB 的本地层结论（前提含锚点）⇒ 闭包含共享层行 ⇒ 整体拒、零落库。
  G3 自我快照：SUB `prune_self_snapshots(0)` ⇒ 不抛、返回 0、SELF 行数不变、
     且**有告警上报**（不得静默）；同层 `delete_node` 仍 `False`。
  G4 坐标迁移：**仅构造 SUB 实例** ⇒ 共享层两行坐标逐字不变、本地层行照迁
     （取向 (b)）；PRIMARY 构造 ⇒ 共享层照迁（正对照）。
  G5 数值接口：SUB 打锚点行（confidence / importance 两臂）⇒ `PermissionError`
     且值不变；SUB 打**本地层**节点仍放行（阴性对照，防过修）。
  G6 recalc 链：SUB `recalc(dry_run=False)` ⇒ 不抛、共享层节点 `state_attributes` /
     `importance` 逐字不变、`protections` 零新增、`action_logs` 零新增、
     返回 `skipped_shared` > 0。
  G7 直调助手：SUB 直调 `_write_influence(锚点, x)` / `_protect_upstream(锚点, …)`
     ⇒ `PermissionError` 且**零落库**（先判后写）。
  G8 阳性对照（防过修）：PRIMARY 侧 recalc / prune / migrate / 数值接口 / TMS
     读数与修前**逐位相同**（字面值见各断言）。
  G9 阴性对照（既有闸不得松动）：`add_node`（新 id 共享层 / 同 id 覆盖共享层）、
     `delete_node`、`protect_node`、`tag_node`、`add_edge`（共享层端点）、
     `EvidenceLedger.record`/`sync` 全部仍被拦。
  G10 判据单一真源：把 `store.role` 改成 SUB（engine 侧仍 PRIMARY）⇒ 同样被拒
     （防「只看 engine.role」的旁路）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue769_780_governance_write_role_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import warnings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import pytest  # noqa: E402

from lingshu.core.core import (  # noqa: E402
    ConditionSpace, EdgeType, MemoryLayer, Role, SpacetimeMemoryEngine, STNode,
)
from lingshu.core.evidence import CONTRADICT  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


# ------------------------------------------------------------------ 夹具助手

def _cs():
    return ConditionSpace("守卫", "守卫", (0.0, 1e12), "运行中")


def _node(nid, layer, importance=0.9, confidence=1.0, spatial=None, content=None):
    return STNode(id=nid, content=content or ("节点 " + nid), modality="text",
                  spatial_coordinates=spatial or {}, temporal_coordinate=1.0,
                  condition_space=_cs(), importance=importance,
                  confidence=confidence, layer=layer, tags=[])


def _db(tmp):
    return os.path.join(tmp, "gov.db")


def _spatial(e, nid):
    row = e.store.conn.execute(
        "SELECT spatial_coordinates, semantic_coordinates FROM nodes WHERE id=?",
        (nid,)).fetchone()
    return json.loads(row[0] or "{}"), json.loads(row[1] or "{}")


def _sa(e, nid):
    return json.loads(e.store.conn.execute(
        "SELECT state_attributes FROM nodes WHERE id=?", (nid,)).fetchone()[0] or "{}")


def _count(e, sql, params=()):
    return e.store.conn.execute(sql, params).fetchone()[0]


class _World:
    """PRIMARY 建库（锚点 / 结构 / 知识 + 一条已验证 causal 出边），并开一个 SUB 实例。"""

    def __init__(self, tmp, name="gov.db"):
        self.path = os.path.join(tmp, name)
        p = SpacetimeMemoryEngine(self.path)
        p.store.add_node(_node("g_anchor", MemoryLayer.ANCHOR, importance=1.0))
        p.store.add_node(_node("g_struct", MemoryLayer.STRUCTURE, importance=1.0))
        p.store.add_node(_node("g_know", MemoryLayer.KNOWLEDGE, importance=0.5))
        e = p.add_edge("g_anchor", "g_know", EdgeType.CAUSAL, confidence=1.0)
        p.verify_edge(e.id, 1.0)
        p.store.conn.close()
        self.primary = p
        self.sub = SpacetimeMemoryEngine(self.path, role=Role.SUB)

    def close(self):
        self.sub.store.conn.close()
        self.primary.store.conn.close()


# ==================================================================== G1 TMS

def test_g1_sub_add_justification_on_anchor_denied_and_zero_rows():
    """G1：SUB 的 TMS 依据登记不得写共享层行，且不得留孤儿依据行。"""
    with tempfile.TemporaryDirectory() as tmp:
        w = _World(tmp)
        try:
            sub = w.sub
            sub.enable_tms()
            before = sub.store.get_node("g_anchor").confidence
            assert before == 1.0
            with pytest.raises(PermissionError):
                sub.add_justification("g_anchor", ["g_know"])
            assert sub.store.get_node("g_anchor").confidence == before, "锚点 confidence 被改写"
            assert _count(sub, "SELECT COUNT(*) FROM justifications") == 0, (
                "G1 被拒的 add_justification 留下孤儿依据行（闸未落在 INSERT 之前）")
            assert _count(sub, "SELECT COUNT(*) FROM justification_premises") == 0
        finally:
            w.close()
        p2 = SpacetimeMemoryEngine(w.path, role=Role.PRIMARY)
        try:
            assert p2.store.get_node("g_anchor").confidence == 1.0, "重开 PRIMARY 读回被改写"
        finally:
            p2.store.conn.close()


def test_g2_sub_tms_propagation_closure_with_shared_layer_denied():
    """G2：SUB 触发传播时，**下游闭包含共享层行** ⇒ 整体拒、零落库。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "g2.db")
        p = SpacetimeMemoryEngine(path)
        p.store.add_node(_node("c_anchor", MemoryLayer.ANCHOR, importance=1.0))
        p.store.add_node(_node("c_local", MemoryLayer.KNOWLEDGE, importance=0.5))
        p.enable_tms()
        p.add_justification("c_anchor", ["c_local"])   # 锚点 ← 本地层（锚点在下游闭包内）
        p.store.conn.close()

        sub = SpacetimeMemoryEngine(path, role=Role.SUB)
        try:
            tms = sub.enable_tms()
            before = sub.store.get_node("c_anchor").confidence
            just_before = _count(sub, "SELECT COUNT(*) FROM justifications")
            with pytest.raises(PermissionError):
                tms.propagate(["c_local"])      # 闭包 = [c_local, c_anchor]
            assert sub.store.get_node("c_anchor").confidence == before, (
                "G2 闭包写回改写了共享层锚点（%s -> %s）"
                % (before, sub.store.get_node("c_anchor").confidence))
            assert _count(sub, "SELECT COUNT(*) FROM justifications") == just_before
        finally:
            sub.store.conn.close()


# ============================================================ G3 自我快照回收

def test_g3_sub_prune_self_snapshots_denied_not_silent():
    """G3：SUB 不得经 prune 硬删 SELF 层快照；取向＝返回 0 并**上报**（不静默）。"""
    with tempfile.TemporaryDirectory() as tmp:
        w = _World(tmp)
        try:
            sub = w.sub
            for i in range(3):
                sub.update_self({"state_description": "snap%d" % i})
            n_before = _count(sub, "SELECT COUNT(*) FROM nodes WHERE layer='self'")
            assert n_before >= 3
            with warnings.catch_warnings(record=True) as rec:
                warnings.simplefilter("always")
                n = sub.store.prune_self_snapshots(0)
            assert n == 0, "SUB 仍删掉了 %d 条 SELF 快照（越权面未收口）" % n
            assert _count(sub, "SELECT COUNT(*) FROM nodes WHERE layer='self'") == n_before
            assert rec, "G3 取向为「返回 0 并上报」——不得静默"
            sid = sub.store.conn.execute(
                "SELECT id FROM nodes WHERE layer='self' LIMIT 1").fetchone()[0]
            assert sub.store.delete_node(sid) is False, "SELF 层被 delete_node 放行"
        finally:
            w.close()


# ============================================================ G4 构造期坐标迁移

def test_g4_sub_construct_does_not_migrate_shared_layer_rows():
    """G4：仅构造 SUB 实例不得改写共享层行坐标；本地层照迁（取向 (b)）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mig.db")
        p = SpacetimeMemoryEngine(path)
        for nid, layer in (("m_anchor", "anchor"), ("m_struct", "structure"),
                           ("m_know", "knowledge")):
            p.store.conn.execute(
                "INSERT INTO nodes (id, content, layer, spatial_coordinates, semantic_coordinates) "
                "VALUES (?,?,?,?,?)",
                (nid, "x", layer,
                 json.dumps({"protocol_purpose": 0.7, "image2d_x": 1.0}), "{}"))
        p.store.conn.commit()
        before = {nid: tuple(p.store.conn.execute(
            "SELECT spatial_coordinates, semantic_coordinates FROM nodes WHERE id=?",
            (nid,)).fetchone())
            for nid in ("m_anchor", "m_struct", "m_know")}
        p.store.conn.close()

        sub = SpacetimeMemoryEngine(path, role=Role.SUB)      # ← 仅构造，不调任何写 API
        try:
            for nid in ("m_anchor", "m_struct"):
                row = tuple(sub.store.conn.execute(
                    "SELECT spatial_coordinates, semantic_coordinates FROM nodes WHERE id=?",
                    (nid,)).fetchone())
                assert row == before[nid], (
                    "G4 仅构造 SUB 即改写共享层行 %s：%r -> %r"
                    % (nid, before[nid], row))
            # 本地层行照迁（防过修：不能整体跳过迁移）
            sp_k, se_k = _spatial(sub, "m_know")
            assert "protocol_purpose" not in sp_k, "G4 本地层行未迁移（过修）"
            assert se_k.get("protocol", {}).get("concept", {}).get("purpose") == 0.7
        finally:
            sub.store.conn.close()

        # 正对照：PRIMARY 构造照旧迁移共享层行
        p2 = SpacetimeMemoryEngine(path, role=Role.PRIMARY)
        try:
            sp_a, se_a = _spatial(p2, "m_anchor")
            assert "protocol_purpose" not in sp_a, "G4 PRIMARY 不再迁移共享层行（过修）"
            assert se_a.get("protocol", {}).get("concept", {}).get("purpose") == 0.7
        finally:
            p2.store.conn.close()


# ============================================================ G5 数值接口

def test_g5_sub_numeric_interfaces_on_shared_layer_denied():
    """G5：SUB 不得经数值接口改写共享层行（confidence / importance 两臂）。"""
    with tempfile.TemporaryDirectory() as tmp:
        w = _World(tmp)
        try:
            sub = w.sub
            for nid, getter in (("g_anchor", "confidence"), ("g_struct", "confidence"),
                                ("g_anchor", "importance")):
                node = sub.store.get_node(nid)
                before = getattr(node, getter)
                with pytest.raises(PermissionError):
                    if getter == "confidence":
                        sub.store.update_node_confidence(nid, -0.5)
                    else:
                        sub.store.update_node_importance(nid, 0.5)
                assert getattr(sub.store.get_node(nid), getter) == before, (
                    "G5 %s 的 %s 被 SUB 改写" % (nid, getter))
            # 阴性对照（防过修）：SUB 写**本地层**节点照旧放行
            kn_before = sub.store.get_node("g_know")
            sub.store.update_node_confidence("g_know", -0.2)
            assert sub.store.get_node("g_know").confidence == pytest.approx(
                kn_before.confidence - 0.2, abs=1e-9)
            sub.store.update_node_importance("g_know", 0.2)
            assert sub.store.get_node("g_know").importance == pytest.approx(
                kn_before.importance + 0.2, abs=1e-9)
        finally:
            w.close()


# ============================================================ G6 recalc 链

def test_g6_sub_recalc_skips_shared_layer_and_does_not_raise():
    """G6：SUB 的 recalc 不得改写共享层治理列，也不得「先写后抛」部分落库。"""
    with tempfile.TemporaryDirectory() as tmp:
        w = _World(tmp)
        try:
            sub = w.sub
            logs_before = _count(sub, "SELECT COUNT(*) FROM action_logs")
            prot_before = list(sub.store.get_protected_nodes())
            r = sub.store.recalc_structural_importance(dry_run=False)   # 不得抛
            assert _sa(sub, "g_anchor") == {}, "G6 共享层 state_attributes 被改写"
            assert _sa(sub, "g_struct") == {}, "G6 共享层 state_attributes 被改写"
            assert sub.store.get_node("g_anchor").importance == 1.0
            assert sub.store.get_node("g_struct").importance == 1.0
            assert list(sub.store.get_protected_nodes()) == prot_before, "共享层被登记保护"
            assert _count(sub, "SELECT COUNT(*) FROM action_logs") == logs_before
            assert r.get("skipped_shared", 0) > 0, "G6 未上报被跳过的共享层节点：%r" % (r,)
        finally:
            w.close()


def test_g7_sub_direct_helpers_denied_with_zero_write():
    """G7：直调 `_write_influence` / `_protect_upstream` 打共享层 ⇒ 拒且零落库。"""
    with tempfile.TemporaryDirectory() as tmp:
        w = _World(tmp)
        try:
            sub = w.sub
            with pytest.raises(PermissionError):
                sub.store._write_influence("g_anchor", 7.77)
            assert _sa(sub, "g_anchor") == {}, "G7 被拒的 _write_influence 仍落库"
            with pytest.raises(PermissionError):
                sub.store._protect_upstream("g_anchor", 9.9, 0.9)
            assert _sa(sub, "g_anchor") == {}, "G7 被拒的 _protect_upstream 仍落库"
            assert sub.store.get_node("g_anchor").importance == 1.0
            assert list(sub.store.get_protected_nodes()) == []
        finally:
            w.close()


# ============================================================ G8 阳性对照

def test_g8_primary_golden_readings_unchanged():
    """G8（防过修·正对照）：PRIMARY 侧全链读数与修前逐位相同。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "gold.db")
        p = SpacetimeMemoryEngine(path)
        try:
            p.store.add_node(_node("h_anchor", MemoryLayer.ANCHOR, importance=0.9))
            p.store.add_node(_node("h_know", MemoryLayer.KNOWLEDGE, importance=1.0))
            e = p.add_edge("h_anchor", "h_know", EdgeType.CAUSAL, confidence=1.0)
            p.verify_edge(e.id, 1.0)
            r = p.store.recalc_structural_importance(dry_run=False)
            assert (r["applied"], r["protected"], r["candidates"]) == (0, 1, 0), (
                "G8 PRIMARY recalc 读数漂移：%r" % (r,))
            assert _sa(p, "h_anchor") == {"concept_influence": 1.0}
            assert p.store.get_node("h_anchor").importance == 0.9
            assert p.store.get_protected_nodes() == ["h_anchor"]

            # 结构层走扇出分支的读数（修前 golden：imp 0.45 / sa structural_boost 0.15）
            p.store.add_node(_node("h_struct", MemoryLayer.STRUCTURE, importance=0.3))
            for i in range(5):
                p.store.add_node(_node("h_deg%d" % i, MemoryLayer.KNOWLEDGE, importance=0.3))
                p.add_edge("h_struct", "h_deg%d" % i, EdgeType.CAUSAL, confidence=0.5)
            r2 = p.store.recalc_structural_importance(dry_run=False)
            assert r2["applied"] == 1, "G8 PRIMARY 扇出分支读数漂移：%r" % (r2,)
            assert p.store.get_node("h_struct").importance == pytest.approx(0.45, abs=1e-9)
            assert _sa(p, "h_struct") == {"structural_boost": 0.15}
            assert p.store.recalc_structural_importance(dry_run=True)["mode"] == "dry_run"

            # 数值接口 / 自快照回收 / TMS 在 PRIMARY 上照旧
            p.store.update_node_confidence("h_know", -0.2)
            p.store.update_node_importance("h_know", 0.2)
            assert p.store.get_node("h_know").confidence == pytest.approx(0.8, abs=1e-9)
            for i in range(3):
                p.update_self({"state_description": "g%d" % i})
            assert p.store.prune_self_snapshots(0) == 3, "G8 PRIMARY prune 读数漂移"
            assert _count(p, "SELECT COUNT(*) FROM nodes WHERE layer='self'") == 0
        finally:
            p.store.conn.close()


# ============================================================ G9 阴性对照

def test_g9_existing_gates_not_loosened():
    """G9：既有闸（add_node / delete_node / protect_node / tag_node / add_edge /
    证据账本）逐项仍被拦——本笔不得放松任何一处。"""
    with tempfile.TemporaryDirectory() as tmp:
        w = _World(tmp)
        try:
            sub = w.sub
            with pytest.raises(PermissionError):
                sub.store.add_node(_node("new_anchor_x", MemoryLayer.ANCHOR))
            with pytest.raises(PermissionError):
                sub.store.add_node(_node("g_anchor", MemoryLayer.KNOWLEDGE))
            with pytest.raises(PermissionError):
                sub.store.protect_node("g_anchor", "x")
            with pytest.raises(PermissionError):
                sub.store.tag_node("g_anchor", "t")
            with pytest.raises(PermissionError):
                sub.add_edge("g_anchor", "g_know", EdgeType.CAUSAL, confidence=1.0)
            assert sub.store.delete_node("g_anchor") is False
            led = sub.enable_evidence_ledger()
            with pytest.raises(PermissionError):
                led.record("g_anchor", "attacker", CONTRADICT)
            with pytest.raises(PermissionError):
                led.sync("g_anchor")
            assert sub.store.get_node("g_anchor").confidence == 1.0
        finally:
            w.close()


# ============================================================ G10 单一真源

def test_g10_gate_reads_store_role_not_engine_role():
    """G10：闸取 `store.role`（与 `add_node` 同源），不是 engine 侧副本。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = SpacetimeMemoryEngine(os.path.join(tmp, "src.db"))
        try:
            p.store.add_node(_node("s_anchor", MemoryLayer.ANCHOR, importance=1.0))
            p.store.role = Role.SUB      # 只改 store（engine.role 仍是 PRIMARY）
            with pytest.raises(PermissionError):
                p.store.update_node_confidence("s_anchor", -0.5)
            with pytest.raises(PermissionError):
                p.store._write_influence("s_anchor", 1.0)
            assert p.store.get_node("s_anchor").confidence == 1.0
            assert _sa(p, "s_anchor") == {}
        finally:
            p.store.conn.close()
