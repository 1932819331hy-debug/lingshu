# -*- coding: utf-8 -*-
"""#778 F3/F4 + #781（保护登记面）守卫 —— 常驻回归
============================================================================
缺陷族（公开 issue #778 的 F3/F4 ＋ #781 的「审计 / 可见性 / 口径」三格）：

  F3（P1 · 数据损坏）`LayeredStore.protect_node` **先写后查**——旧实现先
     `INSERT OR REPLACE INTO protections`，之后才 `get_node(node_id)` ⇒
     拼错 id / 传 `None` / 传空串**一律静默成功**，`protections` 落一条
     **孤儿行**（`nodes` 无此节点），且该行成为**潜伏保护**：之后同 id 建节点
     即被拒删（`delete_node` 返回 False），而 `no_forget` 标签并未回填。
  F4（P2 · 加固不对称）`protections` 表**无外键声明**（对照 `edges` 有两条
     `REFERENCES nodes(id)` ＋ #93 孤儿边预筛）⇒ `PRAGMA foreign_key_check`
     **结构上覆盖不到它**：孤儿保护行可静默落库、完整性面判「通过」，
     且该行随 `export_all` 继续传播。
  #781-审计：`tag_node('no_forget')` / `protect_node` **都不落审计**
     （`action_logs` 增量 0），而 `no_forget` 是遗忘保护的**第二个真源**
     （`_is_forget_protected`：`no_forget` ∈ tags ∪ `protections` 行）⇒
     保护名单可被非属主单向扩张却无任何留痕。
  #781-口径：`archived` 与 `no_forget` 在两条遗忘路径上**不等价**——
     `forget_advisor` 认 `archived`（跳过）、`decay_cycle` 不认（照删）。
     本件**只声明现状、不改语义**（该分叉由 G6 钉住，防日后无声漂移）。

修法（最小 · 与既有先例同口径）：
  ① `protect_node` 把取节点提到写行**之前**，不存在即 `ValueError`
     （异常类型先例：同文件 `promote_to_shared` 的 `raise ValueError("节点不存在")`）；
     该次读同时供 #234 角色闸用 ⇒ 不引入第二次 `get_node`。
  ② F4 走「**独立孤儿检查 ＋ 完整性面上报**」而不补外键：SQLite 无法对既有表
     `ADD CONSTRAINT`（旧库永久无约束），且本仓连接一律 `PRAGMA foreign_keys=ON`
     （#93）⇒ 补外键会让**含孤儿保护行的备份**在 `import_all` 抛
     `FOREIGN KEY constraint failed` 并中断整次导入（与 #93 当年必须先补孤儿边
     预筛同坑，而导入侧预筛落在 `import_all` 内、属另一条线）。判据真源＝
     `LayeredStore.orphan_protection_rows`，上报面＝`verify_integrity` / `export_all`。
  ③ `tag_node` 的 `no_forget` 一支与 `protect_node` 各落一条 `action_logs`
     （与 `add_node` 的 `protected_node_overwritten` 同例）；**不**扩到全部标签面。

断言组（抽掉任一修复即红）：
  G1 F3 硬判据：`protect_node(不存在 id)` ⇒ 抛错（本件定 `ValueError`），
     且 `protections` 行数**不变**、无孤儿行（旧实现 ⇒ 不抛、行 +1 ⇒ 必红）。
  G2 F3 阴性对照：`protect_node(存在 id)` ⇒ 成功、`protections` 行 +1、
     `delete_node` 拒删（bool 契约 `False`）、标签含 `no_forget`。
  G3 #781 审计：`tag_node(ctx, 'no_forget')` ⇒ `action_logs` 出现对应行
     （读回确认 action_type/outcome），且**重复打标不重复留痕**（幂等）。
  G4 #781 审计：`protect_node` 同样落审计。
  G5 阳性对照（PRIMARY 全链读数逐位不变）：`protect_node` / `tag_node` /
     `delete_node` / `decay_cycle` 的保护语义修前修后一致（受保护件拒删、
     免衰减；未受保护件照删）。
  G6 阴性对照（#781 既有行为不被误伤）：SUB 对**自有情境层**节点打
     `no_forget` / `protect_node` 仍**成功**；SUB 对共享层节点仍被
     `PermissionError` 拦下（#234 未放宽）。
  G7 口径分叉（只钉现状，不改语义）：仅带 `archived` 的节点被
     `forget_advisor` **跳过**，却被 `decay_cycle` **删除**；仅带 `no_forget`
     者两条路径皆免。
  G8 F4：`protections` 孤儿行经 `verify_integrity()` 上报
     （`orphan_protections` + 样本）并纳入 `integrity_ok`；`export_all`
     返回值同样上报、且**备份内容逐字不变**（只上报不清理）。

运行（仓根）：
    python -X utf8 -m pytest tests/test_issue778_781_forget_protection_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    ConditionSpace, MemoryLayer, Role, SpacetimeMemoryEngine, STNode,
)

#: 低于情境层删除线的 importance（`min_confidence` 默认 0.1）。
_LOW = 0.05


def _mkctx(e, nid, tags=None, importance=_LOW):
    """直落一个情境层节点（不经 add_context，避免 FIFO 上限干扰读数）。"""
    now = time.time()
    node = STNode(
        id=nid, content="守卫节点 " + nid, modality="text",
        spatial_coordinates={}, temporal_coordinate=now,
        condition_space=ConditionSpace("守卫", "回归", (now, now + 60), "运行中"),
        importance=importance, confidence=0.5, layer=MemoryLayer.CONTEXT,
        access_count=0, last_access=now, created_at=now,
        tags=list(tags or []), semantic_coordinates={}, state_attributes={},
        entity_id=None,
    )
    e.store.add_node(node)
    return node


def _prot_count(e):
    return e.store.conn.execute("SELECT COUNT(*) FROM protections").fetchone()[0]


def _logs(e, action_type=None):
    if action_type is None:
        return e.store.conn.execute("SELECT COUNT(*) FROM action_logs").fetchone()[0]
    return e.store.conn.execute(
        "SELECT COUNT(*) FROM action_logs WHERE action_type=?",
        (action_type,)).fetchone()[0]


def _last_log(e, action_type):
    row = e.store.conn.execute(
        "SELECT summary, node_ids, outcome FROM action_logs"
        " WHERE action_type=? ORDER BY id DESC LIMIT 1", (action_type,)).fetchone()
    return None if row is None else {"summary": row[0],
                                     "node_ids": json.loads(row[1]),
                                     "outcome": json.loads(row[2])}


# --------------------------------------------------------------------- G1

def test_g1_protect_node_missing_id_raises_and_leaves_no_row():
    """G1（F3 硬判据）：目标节点不存在 ⇒ 抛错，且 protections 零新增、零孤儿行。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        before = _prot_count(e)
        with pytest.raises(ValueError):
            e.protect_node("ghost_later", "拼错的 id")
        assert _prot_count(e) == before, (
            "#778-F3：protect_node 对不存在的 id 仍落了 protections 行（旧缺陷行为）")
        assert e.store.orphan_protection_rows() == [], (
            "#778-F3：库内出现孤儿保护行：%r" % (e.store.orphan_protection_rows(),))
        assert e.store.get_node("ghost_later") is None, "前置：该 id 确不存在"

        # 潜伏保护的后果面：旧实现下这一步 `delete_node` 会返回 False
        _mkctx(e, "ghost_later")
        assert e.store.delete_node("ghost_later") is True, (
            "#778-F3：后建的普通节点被幽灵保护行拒删（潜伏保护仍生效）")
    finally:
        e.close()


def test_g1b_protect_node_none_and_empty_id_also_rejected():
    """G1b：`None` / 空串 id 同样被拒（旧实现可无上界往 protections 灌 NULL 行）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        for bad in (None, ""):
            with pytest.raises(ValueError):
                e.protect_node(bad, "坏 id")
        assert _prot_count(e) == 0, "坏 id 仍落了 protections 行"
        assert e.store.get_protected_nodes() == [], (
            "#778-F3：不可遗忘名单可被写入 None/空串条目：%r"
            % (e.store.get_protected_nodes(),))
    finally:
        e.close()


# --------------------------------------------------------------------- G2

def test_g2_existing_id_protected_normally():
    """G2（阴性对照）：目标存在 ⇒ 保护照常生效（行 +1、拒删、标签落）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        n = _mkctx(e, "real_node")
        before = _prot_count(e)
        e.protect_node(n.id, "守卫：不可遗忘")
        assert _prot_count(e) == before + 1, "protections 行未 +1"
        assert n.id in e.store.get_protected_nodes()
        got = e.store.get_node(n.id)
        assert "no_forget" in (got.tags or []), (
            "protect_node 未回填 no_forget 标签：%r" % (got.tags,))
        assert e.store.delete_node(n.id) is False, "受保护节点未被拒删（bool 契约）"
        assert e.store.get_node(n.id) is not None
    finally:
        e.close()


# --------------------------------------------------------------------- G3/G4

def test_g3_tag_no_forget_writes_audit_row():
    """G3（#781 审计）：打 `no_forget` 落 `action_logs`，且重复打标不重复留痕。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        n = _mkctx(e, "tag_audit")
        before = _logs(e, "no_forget_tagged")
        e.store.tag_node(n.id, "no_forget")
        assert _logs(e, "no_forget_tagged") == before + 1, (
            "#781：tag_node('no_forget') 未落审计（旧实现 action_logs 增量 0）")
        rec = _last_log(e, "no_forget_tagged")
        assert n.id in rec["node_ids"], "审计行的 node_ids 未指向被打标节点：%r" % (rec,)
        assert rec["outcome"].get("tag") == "no_forget", (
            "审计行 outcome 未记标签名：%r" % (rec,))

        # 幂等：重复打同一标签不重复留痕（避免同一事实刷满审计面）
        e.store.tag_node(n.id, "no_forget")
        assert _logs(e, "no_forget_tagged") == before + 1, (
            "#781：重复打标重复留痕（应幂等）")
        # 其它标签不落审计（本件只对 no_forget 落，避免大面积行为变更）
        plain = _logs(e)
        e.store.tag_node(n.id, "ordinary_tag")
        assert _logs(e) == plain, "#781：非 no_forget 标签也被加了审计（超出本件范围）"
    finally:
        e.close()


def test_g4_protect_node_writes_audit_row():
    """G4（#781 审计）：`protect_node` 落 `action_logs`（治理动作可查）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        n = _mkctx(e, "prot_audit")
        before = _logs(e, "protected_node_registered")
        e.protect_node(n.id, "守卫：不可遗忘")
        assert _logs(e, "protected_node_registered") == before + 1, (
            "#781：protect_node 未落审计（旧实现 action_logs 增量 0）")
        rec = _last_log(e, "protected_node_registered")
        assert n.id in rec["node_ids"] and rec["outcome"].get("reason") == "守卫：不可遗忘", (
            "审计行内容不符：%r" % (rec,))
    finally:
        e.close()


# --------------------------------------------------------------------- G5

def test_g5_primary_full_chain_readings_unchanged():
    """G5（阳性对照）：PRIMARY 上保护全链读数逐位不变（修法未削保护语义）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        protected = _mkctx(e, "p_keep")
        plain = _mkctx(e, "p_plain")
        e.protect_node(protected.id, "守卫：不可遗忘")
        e.store.tag_node(plain.id, "ordinary")

        assert e.store.delete_node(protected.id) is False, "受保护件未被拒删"
        assert e.store.delete_node(plain.id) is True, "未受保护件未被删（防误杀对照）"

        tag_only = _mkctx(e, "p_tag")
        e.store.tag_node(tag_only.id, "no_forget")
        for _ in range(200):
            e.store.decay_cycle()
        assert e.store.get_node(protected.id) is not None, (
            "protections 行登记的节点被 decay_cycle 删除（保护语义被削）")
        assert e.store.get_node(tag_only.id) is not None, (
            "no_forget 标签腿的节点被 decay_cycle 删除（保护语义被削）")
    finally:
        e.close()


# --------------------------------------------------------------------- G6

def test_g6_sub_local_layer_tagging_still_allowed():
    """G6（阴性对照）：SUB 对自有本地层打标/登记保护**仍成功**（#781 既有行为不误伤）。"""
    e = SpacetimeMemoryEngine(":memory:", role=Role.SUB)
    try:
        own = e.add_context("SUB 自有的情境节点")
        e.store.tag_node(own.id, "no_forget")           # 本地层：放行
        assert "no_forget" in (e.store.get_node(own.id).tags or []), (
            "#781：SUB 对自有情境层打 no_forget 被误拦（本件未收窄该格）")
        own2 = e.add_context("SUB 自有的第二个情境节点")
        e.store.protect_node(own2.id, "SUB 自保")        # 本地层：放行
        assert own2.id in e.store.get_protected_nodes(), (
            "#781：SUB 对自有情境层登记保护被误拦")
    finally:
        e.close()


def test_g6b_sub_still_blocked_on_shared_layer():
    """G6b：SUB 对**共享层**节点打标/保护仍被 `PermissionError` 拦下（#234 未放宽）。"""
    import tempfile
    db = os.path.join(tempfile.mkdtemp(prefix="guard778sub_"), "shared.db")
    prim = SpacetimeMemoryEngine(db, role=Role.PRIMARY)
    try:
        a = prim.set_anchor("共享层锚点（守卫）")
        aid = getattr(a, "id", None) or a
    finally:
        prim.close()
    sub = SpacetimeMemoryEngine(db, role=Role.SUB)
    try:
        with pytest.raises(PermissionError):
            sub.store.tag_node(aid, "no_forget")
        with pytest.raises(PermissionError):
            sub.store.protect_node(aid, "SUB 越权登记")
    finally:
        sub.close()


# --------------------------------------------------------------------- G7

def test_g7_archived_vs_no_forget_divergence_is_pinned():
    """G7（口径钉现状）：`archived` 被 forget_advisor 跳过、却被 decay_cycle 删除。"""
    # —— decay_cycle 腿：只认 no_forget ∪ protections，不认 archived
    e = SpacetimeMemoryEngine(":memory:")
    try:
        keep = _mkctx(e, "d_nf", tags=["no_forget"])
        arch = _mkctx(e, "d_arch", tags=["archived"])
        plain = _mkctx(e, "d_plain")
        for _ in range(200):
            e.store.decay_cycle()
        assert e.store.get_node(keep.id) is not None, "no_forget 节点被衰减删除"
        assert e.store.get_node(arch.id) is None, (
            "#781 口径：仅带 archived 的节点本应被 decay_cycle 删除（当前口径），"
            "若此处变绿说明 decay_cycle 已改认 archived——须同步更新本守卫与注释")
        assert e.store.get_node(plain.id) is None, "普通节点未被遗忘（衰减面失效）"
    finally:
        e.close()

    # —— forget_advisor 腿：认 archived（跳过），不认「已归档者再归档」
    f = SpacetimeMemoryEngine(":memory:")
    try:
        arch2 = _mkctx(f, "f_arch", tags=["archived"])
        nf2 = _mkctx(f, "f_nf", tags=["no_forget"])
        plain2 = _mkctx(f, "f_plain")
        stats = f.forget_advisor(stale_days=0.0, low_value=1.0)
        got = f.store.get_node(arch2.id)
        assert got is not None and "archived" in (got.tags or []), (
            "#781 口径：archived 节点被 forget_advisor 二次改写（应被跳过）：%r"
            % (None if got is None else got.tags,))
        assert f.store.get_node(nf2.id) is not None, "no_forget 节点被 forget_advisor 归档"
        plain_tags = f.store.get_node(plain2.id).tags
        assert "archived" in plain_tags, (
            "#781 口径：普通情境节点未被 forget_advisor 归档（信号面失效）：%r"
            % (plain_tags,))
        assert stats["archived"] >= 1, "forget_advisor 未归档任何节点：%r" % (stats,)
    finally:
        f.close()


# --------------------------------------------------------------------- G8

def test_g8_orphan_protection_rows_are_reported():
    """G8（F4）：孤儿保护行经完整性面上报并纳入 `integrity_ok`；导出只上报不清理。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        _mkctx(e, "alive_node")
        # 现场构造：绕过 protect_node 的 F3 前置校验，直落一条孤儿保护行
        # （对应「含孤儿保护行的陈旧/篡改备份」这一真实损坏形态）。
        e.store.conn.execute(
            "INSERT OR REPLACE INTO protections VALUES (?,?,?)",
            ("nobody_home", "陈旧备份里的孤儿保护行", time.time()))
        e.store.conn.commit()

        vi = e.verify_integrity()
        assert vi.get("orphan_protections") == 1, (
            "#778-F4：protections 孤儿行未被完整性面上报：%r" % (vi,))
        assert vi.get("orphan_protections_sample") == ["nobody_home"], (
            "样本不符：%r" % (vi.get("orphan_protections_sample"),))
        assert vi["integrity_ok"] is False, (
            "#778-F4：有孤儿保护行却判 integrity_ok=True（加固不对称未收口）")

        # 对照：清掉孤儿行后判「通过」（不误报）
        e.store.conn.execute("DELETE FROM protections WHERE node_id='nobody_home'")
        e.store.conn.commit()
        vi2 = e.verify_integrity()
        assert vi2["orphan_protections"] == 0 and vi2["integrity_ok"] is True, (
            "无孤儿行时被误判损坏：%r" % (vi2,))

        # 导出面：只上报、备份内容逐字不变
        e.store.conn.execute(
            "INSERT OR REPLACE INTO protections VALUES (?,?,?)",
            ("nobody_home", "陈旧备份里的孤儿保护行", time.time()))
        e.store.conn.commit()
        import tempfile
        out = os.path.join(tempfile.mkdtemp(prefix="guard778exp_"), "bk.json")
        res = e.export_all(out)
        assert res.get("orphan_protections") == 1, (
            "#778-F4：export_all 未上报孤儿保护行：%r" % (res,))
        assert res.get("orphan_protections_sample") == ["nobody_home"], (
            "样本不符：%r" % (res.get("orphan_protections_sample"),))
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        kept = [r for r in data.get("protections", []) if r.get("node_id") == "nobody_home"]
        assert len(kept) == 1, (
            "#778-F4：孤儿保护行被静默从备份中丢弃（本件只上报、不清理）：%r"
            % (data.get("protections"),))
        assert kept[0].get("reason") == "陈旧备份里的孤儿保护行", (
            "备份里的保护行内容被改写：%r" % (kept[0],))
    finally:
        e.close()
