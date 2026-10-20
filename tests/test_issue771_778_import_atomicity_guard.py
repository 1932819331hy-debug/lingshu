# -*- coding: utf-8 -*-
"""#771 ①-④ ＋ #778 F1/F2 守卫：导入面**原子性**与**保真**（lingshu/core/{core,activation}.py）

缺陷态（修复前 `origin/main`，本守卫作者独立复现，读数见提交正文）：
  · ① `activation.ActivationEngine.import_workset` **先 `DELETE` 目标 workset、再迭代
    members**，无事务、无 `except`/`rollback` ⇒ members 畸形时异常逃逸，**目标已被
    清空且无回滚**（借用连接形态下任一次后续提交即固化）。
  · ② `import_all` 的换层判据（`immutable_rows` / `relayer_rows`）都要求「任一侧属
    IMMUTABLE」⇒ **本地层之间**的换层（`context` → `knowledge`）不进任何闸：无密钥
    可把既有情境层节点改成知识层并替换正文（无审计）；而同一换层经 `add_node` 必被
    `#127` 拦（判据与层集合无关）⇒ 两条入口同语义却不对称。
  · ③ `import_all` 按备份行原样绑 SQL ⇒ 非 str `content` 全部落库（`dict`/`list` 经
    `#721` 归一为 JSON 文本、`int`/`bool` 由 TEXT 列亲缘性静默转文本、`None` 落
    NULL）；对照 `store.add_node` 对同一输入**全部 `TypeError`**（`#588`/`#594`）。
  · ④ `apply_shared_sync` 逐条提交且只捕 `PermissionError` ⇒ 载荷混入非 str content
    时**合法条目已落库**、坏条目 `TypeError` 逃逸（非原子）；`payload=None` 静默返回
    空结果、字符串/整数以 `AttributeError`/`TypeError` 逃逸。
  · #778 F1/F2：`import_all` 走通用逐表 `INSERT OR REPLACE`，缺 `add_node` 的 `#659`
    保真判据 ⇒ 备份行漏带 `no_forget` 时旧行的保护标签被**静默抹掉**（无报错、无需
    密钥、无审计、`integrity_ok` 仍 True），而 `no_forget` ∈ tags 是
    `_is_forget_protected`（`#36` 唯一真源）的两个真源之一 ⇒ 节点随即变可删。

修法（最小 · 复用既有单一真源）：
  · ① 写前物化 members（非可迭代 ⇒ 在任何写之前抛，异常类型不变）＋ `DELETE` 与全部
    `INSERT` 收进同一事务（已在外层事务中则用 SAVEPOINT，不越权提交调用方事务）。
  · ② 新增 `local_relayer_rows` 闸（两侧均不属 IMMUTABLE 而声明层不同 ⇒ `PermissionError`）；
    涉 IMMUTABLE 的换层仍由 `D-007` 双列闸处理（持密钥放行契约逐位不变）。
  · ③ `nodes` 行带 `content` 键而值非 str ⇒ `TypeError`（fail-closed、零落库、不做
    `str()` 强转，与 `#331`/`#588` 同口径）。缺 `content` 键的行仍按 `#652②` 落 NULL
    并计入 `missing_columns`（不抢该面）。
  · ④ 载荷形态前置判（`None`/`str`/非可迭代 ⇒ 可诊断 `TypeError`）＋ content 类型
    前置判（写之前判完 ⇒ 整批零落库）＋ 整批收进一条事务。
  · #778 F1/F2：`import_all` 按 `add_node` 的 `#659` 同款判据——旧行 tags 含
    `no_forget` 而新行不含 ⇒ **并入**并落 `protected_node_overwritten` 审计；
    **不**拒绝覆盖（保住灾备恢复语义）。

断言组：
  G1 `import_workset` 畸形 members 五形态 ⇒ 异常 ＋ 目标 workset 逐位不变。
  G2 **阳性对照**：合法快照 ⇒ 成功且成员/hop/activation/source 逐位保持（防过修）。
  G3 `import_all` 同 id 换层 ⇒ `PermissionError`、旧行逐位不变；
     G3b 同 id **同层**覆盖仍放行（不回归 `#127` 边界）；G3c 涉 IMMUTABLE 换层＋密钥
     仍放行并标注（不回归 `#478` 契约）。
  G4 非 str content 五形态 ⇒ `TypeError` 且库零变更；G4b 缺 `content` 键仍落 NULL 并
     计入 `missing_columns`（不回归 `#652②`）。
  G5 抹 `no_forget` ⇒ 标签保住 ＋ 审计行 ＋ `delete_node` 返回 False；
     G5b `protections` 行保护形态同收口；G5c **阴性对照**：未打标节点经同一路径仍可删。
  G6 `apply_shared_sync` 含坏条目 ⇒ 整批零落库；G6b 载荷形态三形态 ⇒ 可诊断 `TypeError`；
     G6c **阳性对照**：合法载荷照常 applied（防过修）。
  G7 **阳性对照**：`export_all` → `import_all` 干净往返读数逐位不变（防过修）。

判别力（变异自证 · 实测）：`git checkout origin/main -- lingshu/core/core.py
  lingshu/core/activation.py` ⇒ **17 failed / 7 passed**；`git checkout HEAD --` 还原 ⇒
  **24 passed**。17 红＝G1 五形态、G3、G4 五形态、G5、G5b、G6、G6b 两形态（None/str）、
  G7。7 绿＝G2、G3b、G3c、G4b、G5c、G6b[int]、G6c —— 其中 G2/G3b/G3c/G4b/G5c/G6c 是
  **刻意恒绿**的防过修对照（旧码也须绿，红即过修）；`G6b[int]` 旧码亦绿（整数本就不属
  ④ 的判据面，该形态在旧码即已 `TypeError`）；G7 **旧码红**——它断言两个新增返回键
  （`protected_tags_kept` / `protected_rows_overwritten`）存在且为空，故既是防过修
  对照（过修则非空）又要求修复在场（旧码 `KeyError`），非恒绿。

运行（仓根）：python -X utf8 -m pytest tests/test_issue771_778_import_atomicity_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import (  # noqa: E402
    ConditionSpace, MemoryLayer, Role, STNode, SpacetimeMemoryEngine,
)

try:
    from lingshu.core.activation import ActivationEngine
except ModuleNotFoundError as error:  # numpy 缺失 ⇒ 激活面整件跳过（不静默放绿）
    if error.name != "numpy":
        raise
    ActivationEngine = None

_CS = ConditionSpace("守卫", "守卫", (time.time(), time.time() + 3600), "运行中")
_KEY = "guard-771-778"


def _node(nid, content, layer=MemoryLayer.KNOWLEDGE, tags=None, importance=0.5):
    return STNode(id=nid, content=content, modality="text",
                  spatial_coordinates={}, temporal_coordinate=0.0,
                  condition_space=_CS, layer=layer, importance=importance,
                  tags=list(tags or []))


def _engine(tmp_path, name="a.db", role=Role.PRIMARY):
    return SpacetimeMemoryEngine(str(tmp_path / name), role=role)


def _audit_count(eng, action_type, nid=None):
    if nid is None:
        sql = "SELECT COUNT(*) FROM action_logs WHERE action_type=?"
        args = (action_type,)
    else:
        sql = ("SELECT COUNT(*) FROM action_logs WHERE action_type=?"
               " AND node_ids LIKE ?")
        args = (action_type, "%" + nid + "%")
    return eng.store.conn.execute(sql, args).fetchone()[0]


def _backup(tmp_path, name, rows_fn):
    """用一份**真** `export_all` 产物做骨架，把 `nodes` 换成 `rows_fn(骨架行副本)`。

    骨架行来自真备份 ⇒ 列集/取值形态与生产路径一致（不手搓半截行）。
    """
    seed = SpacetimeMemoryEngine(str(tmp_path / ("seed_%s" % name)))
    seed.store.add_node(_node("seed_row", "备份骨架行"))
    path = str(tmp_path / name)
    seed.export_all(path)
    seed.close()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = rows_fn(dict(data["nodes"][0]))
    if rows is not None:
        data["nodes"] = rows
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def _row(base, nid, content, layer="knowledge", tags="[]"):
    row = dict(base)
    row.update({"id": nid, "content": content, "layer": layer, "tags": tags})
    return row


# ══════════════════════════════════════════ G1 import_workset 畸形 ⇒ 零变更

_MALFORMED = [
    ("members=None", {"workset": "w2", "members": None}),
    ("members 字符串", {"workset": "w2", "members": "abc"}),
    ("member 缺 node_id", {"workset": "w2", "members": [{"activation": 0.5}]}),
    ("node_id=None", {"workset": "w2", "members": [{"node_id": None, "activation": 0.5}]}),
    ("activation 为 dict", {"workset": "w2", "members": [{"node_id": "n", "activation": {"a": 1}}]}),
]


@pytest.mark.skipif(ActivationEngine is None, reason="ActivationEngine 需要 numpy")
@pytest.mark.parametrize("label,snapshot", _MALFORMED, ids=[m[0] for m in _MALFORMED])
def test_g1_malformed_members_leave_target_workset_untouched(tmp_path, label, snapshot):
    """G1：畸形 members ⇒ 抛异常且目标 workset **逐位不变**（#771 ①）。"""
    eng = _engine(tmp_path, "g1_%s.db" % abs(hash(label)))
    try:
        eng.store.add_node(_node("c1a", "工作态内容 甲"))
        eng.store.add_node(_node("c1b", "工作态内容 乙"))
        act = ActivationEngine(store=eng.store)
        act.activate("工作态内容 甲", workset="w2")
        act.activate("工作态内容 乙", workset="w2")
        before = act.export_workset("w2")["members"]
        assert before, "前置：w2 须非空"

        # 异常仍逃逸（本件只要求原子性，**不**改错误语义：不改成静默返回错误结构）
        with pytest.raises(Exception):
            act.import_workset(snapshot)

        after = act.export_workset("w2")["members"]
        assert after == before, (
            "#771 ① 畸形 members 抛异常后目标 workset 被破坏（无回滚）："
            "before=%r after=%r（形态 %s）" % (before, after, label))
    finally:
        eng.close()


# ══════════════════════════════════════════ G2 阳性对照：合法往返逐位保持

@pytest.mark.skipif(ActivationEngine is None, reason="ActivationEngine 需要 numpy")
def test_g2_legal_snapshot_roundtrip_members_bit_identical(tmp_path):
    """G2（阳性对照 · 防过修）：合法快照 ⇒ 成员/hop/activation/source 逐位保持。"""
    eng = _engine(tmp_path, "g2.db")
    try:
        eng.store.add_node(_node("c1a", "工作态内容 甲"))
        eng.store.add_node(_node("c1b", "工作态内容 乙"))
        act = ActivationEngine(store=eng.store)
        act.activate("工作态内容 甲", workset="w2")
        snap = act.export_workset("w2")
        snap["members"][0]["hop"] = 3            # 显式 hop 须原样恢复

        ret = act.import_workset(snap, workset="w9")
        assert ret == {"status": "ok", "workset": "w9",
                       "restored": len(snap["members"])}, ret

        got = {m["node_id"]: (m["activation"], m["hop"], m["source"])
               for m in act.export_workset("w9")["members"]}
        want = {m["node_id"]: (m["activation"], m["hop"], "restored")
                for m in snap["members"]}
        assert got == want, "G2 合法快照恢复结果与修前口径不符：%r != %r" % (got, want)
    finally:
        eng.close()


# ══════════════════════════════════════════ G3 import_all 同 id 换层闸

def test_g3_local_layer_relayer_rejected_and_row_untouched(tmp_path):
    """G3：本地层之间同 id 换层（context → knowledge）⇒ 拒、旧行逐位不变（#771 ②）。"""
    eng = _engine(tmp_path, "g3.db")
    try:
        eng.store.add_node(_node("sw1", "本地情境原文", layer=MemoryLayer.CONTEXT))
        path = _backup(tmp_path, "g3.json", lambda b: [
            _row(b, "sw1", "被换成知识层并替换正文", layer="knowledge")])

        with pytest.raises(PermissionError):
            eng.import_all(path)

        got = eng.store.get_node("sw1")
        assert got is not None
        assert got.layer is MemoryLayer.CONTEXT, (
            "#771 ② 同 id 换层被放行（绕过 #127）：layer=%s" % got.layer.value)
        assert got.content == "本地情境原文", "G3 被拒后正文仍被改写：%r" % (got.content,)
    finally:
        eng.close()


def test_g3b_same_layer_overwrite_still_allowed(tmp_path):
    """G3b（防过修）：同 id **同层**覆盖仍放行（#127 的既有边界不动）。"""
    eng = _engine(tmp_path, "g3b.db")
    try:
        eng.store.add_node(_node("sw2", "同层原文", layer=MemoryLayer.KNOWLEDGE))
        path = _backup(tmp_path, "g3b.json", lambda b: [
            _row(b, "sw2", "同层新正文", layer="knowledge")])

        eng.import_all(path)                       # 不抛
        got = eng.store.get_node("sw2")
        assert got.content == "同层新正文", "G3b 同层覆盖被误拒：%r" % (got.content,)
    finally:
        eng.close()


def test_g3c_immutable_relayer_with_key_still_allowed(tmp_path, monkeypatch):
    """G3c（防回归 #478）：涉 IMMUTABLE 的换层＋密钥 ⇒ 仍放行且显式标注。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)
    eng = _engine(tmp_path, "g3c.db")
    try:
        anchor = eng.set_anchor("持密钥换层须标注")
        path = _backup(tmp_path, "g3c.json", lambda b: [
            _row(b, anchor.id, "迁移内容", layer="knowledge")])

        ret = eng.import_all(path, designer_key=_KEY)
        assert ret["requires_review"] is True, ret
        assert anchor.id in ret["immutable_rows"], ret
        got = eng.store.get_node(anchor.id)
        assert got.layer is MemoryLayer.KNOWLEDGE, "G3c 密钥换层被误拒：%s" % got.layer.value
        assert _audit_count(eng, "import_immutable_override") >= 1, "G3c 换层放行缺审计标注"
    finally:
        eng.close()


# ══════════════════════════════════════════ G4 非 str content 闸

_BAD_CONTENT = [("int", 12345), ("dict", {"a": 1}), ("list", ["x", "y"]),
                ("bool", True), ("null", None)]


@pytest.mark.parametrize("label,value", _BAD_CONTENT, ids=[b[0] for b in _BAD_CONTENT])
def test_g4_non_str_content_rejected_with_zero_change(tmp_path, label, value):
    """G4：非 str content 五形态 ⇒ `TypeError`、目标行不被改写、无新行（#771 ③）。"""
    eng = _engine(tmp_path, "g4_%s.db" % label)
    try:
        eng.store.add_node(_node("ph1", "占位原文"))
        before_rows = eng.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        path = _backup(tmp_path, "g4_%s.json" % label, lambda b: [
            _row(b, "bad_%s" % label, value)])

        with pytest.raises(TypeError):
            eng.import_all(path)

        assert eng.store.get_node("bad_%s" % label) is None, (
            "#771 ③ 非 str content（%s）仍落库" % label)
        after_rows = eng.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert after_rows == before_rows, (
            "#771 ③ 被拒后仍有落库：%d -> %d" % (before_rows, after_rows))
        assert eng.store.get_node("ph1").content == "占位原文", "G4 旧行被改写"
    finally:
        eng.close()


def test_g4b_missing_content_key_still_lands_null_and_is_reported(tmp_path):
    """G4b（防过修）：缺 `content` 键的行仍落 NULL 并计入 `missing_columns`（#652②）。"""
    eng = _engine(tmp_path, "g4b.db")
    try:
        def _rows(base):
            # 两行：后行带 `content`（使列并集含该列 ⇒ 首行「缺列」可被 #652② 观测）
            row = _row(base, "nocontent", "x")
            row.pop("content", None)
            return [row, _row(base, "hascontent", "有内容")]

        ret = eng.import_all(_backup(tmp_path, "g4b.json", _rows))
        got = eng.store.get_node("nocontent")
        assert got is not None and got.content is None, (
            "G4b 缺 content 键的行不再落 NULL：%r" % (got.content if got else got,))
        assert ret["missing_columns"].get("nodes", {}).get("content") == 1, (
            "G4b 缺列未按表按列上报：%r" % (ret.get("missing_columns"),))
    finally:
        eng.close()


# ══════════════════════════════════════════ G5 #778 F1/F2 保护标记保真

def test_g5_no_forget_tag_kept_and_audited(tmp_path):
    """G5：导入覆写抹 `no_forget` ⇒ 标签保住 ＋ 审计行 ＋ 仍拒删（#778 F1）。"""
    eng = _engine(tmp_path, "g5.db")
    try:
        eng.store.add_node(_node("keepA", "受标签保护的不可遗忘事实", tags=["no_forget"]))
        eng.store.add_node(_node("plainA", "普通节点", tags=["ordinary"]))
        path = _backup(tmp_path, "g5.json", lambda b: [
            _row(b, "keepA", "被覆盖的新正文", tags='["ordinary"]'),
            _row(b, "plainA", "普通节点", tags='["ordinary"]')])

        eng.import_all(path)

        got = eng.store.get_node("keepA")
        assert got is not None and "no_forget" in (got.tags or []), (
            "#778 F1 导入覆写把 no_forget 标签静默抹掉了：tags=%r" % (got.tags if got else None,))
        assert got.content == "被覆盖的新正文", "G5 内容仍按备份落库（本件不改内容口径）"
        assert _audit_count(eng, "protected_node_overwritten", "keepA") >= 1, (
            "#778 F1 受保护节点被导入覆写却无审计行")
        assert eng.store.delete_node("keepA") is False, (
            "#778 F1 标签被抹后节点变可删（delete_node 返回 True）")

        # 阴性对照：未打标节点经同一路径后仍可删（排除「导入坏了删除路径」）
        assert eng.store.delete_node("plainA") is True, (
            "G5 阴性对照：未打标节点经同一路径后不再可删（过修）")
    finally:
        eng.close()


def test_g5b_protections_row_node_kept_and_audited(tmp_path):
    """G5b（#778 F2）：行保护节点被导入覆写 ⇒ protections 行仍在、标签并回、落审计。"""
    eng = _engine(tmp_path, "g5b.db")
    try:
        eng.store.add_node(_node("pk1", "行保护关键证据"))
        eng.store.protect_node("pk1", "P0")
        path = _backup(tmp_path, "g5b.json", lambda b: [
            _row(b, "pk1", "被导入覆写的正文", tags='["context"]')])

        eng.import_all(path)

        assert "pk1" in eng.store.get_protected_nodes(), "#778 F2 protections 行被覆盖抹掉"
        assert eng.store.delete_node("pk1") is False, "#778 F2 行保护节点被删"
        assert _audit_count(eng, "protected_node_overwritten", "pk1") >= 1, (
            "#778 F2 受保护节点被导入覆写却无审计行")
    finally:
        eng.close()


def test_g5c_clean_roundtrip_keeps_protection(tmp_path, monkeypatch):
    """G5c（阳性对照 · 防过修）：**干净**备份往返后保护清单与标签逐位不变。

    **#479 契约迁移（2026-10-16 编排侧裁定，同 `test_issue270` 先例）**：本件往返
    的备份现在携带 `action_logs` 行（`#781` 起 `protect_node` / `tag_node('no_forget')`
    落审计）⇒ 按 `#479` 既有契约（备份含 `action_logs` 带 id 行 / `engine_meta`
    治理快照 ⇒ 恢复须持设计者密钥，fail-closed、零落库）须持设计者密钥；
    **原断言未动，仅调用点补密钥**。不持密钥则整次导入 `PermissionError`（`D-007`），
    本格测不到——这与「保护保真」无关，属既有治理面的适用范围被常见路径触达。
    禁止为变绿而删 `protect_node`、清 `action_logs` 或放宽 `#479` 闸（削弱安全闸）。
    """
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)
    eng = _engine(tmp_path, "g5c.db")
    try:
        eng.store.add_node(_node("k1", "标签保护", tags=["no_forget"]))
        eng.store.add_node(_node("k2", "行保护"))
        eng.store.protect_node("k2", "P0")
        assert _audit_count(eng, "protected_node_registered", "k2") >= 1, (
            "#781 契约：protect_node 应落 protected_node_registered 审计")
        path = str(tmp_path / "g5c_clean.json")
        eng.export_all(path)

        dst = _engine(tmp_path, "g5c_dst.db")
        try:
            dst.import_all(path, designer_key=_KEY)
            assert "no_forget" in (dst.store.get_node("k1").tags or []), "干净往返丢了标签"
            assert "k2" in dst.store.get_protected_nodes(), "干净往返丢了 protections 行"
            assert dst.store.delete_node("k1") is False
            assert dst.store.delete_node("k2") is False
            assert _audit_count(dst, "protected_node_overwritten") == 0, (
                "干净往返误落「受保护节点被覆写」审计（过修）")
            # #781×#479 契约存活断言（编排侧 2026-10-16 要求）：打标审计行须随备份
            # 存活到目标库——审计不丢。该契约再漂移（如 `protect_node` 停落审计、
            # `import_all` 丢 `action_logs` 行）时本格当场红。
            assert _audit_count(dst, "protected_node_registered", "k2") >= 1, (
                "往返丢了 protected_node_registered 审计行（#781 打标审计未随备份存活）")
        finally:
            dst.close()
    finally:
        eng.close()


# ══════════════════════════════════════════ G6 apply_shared_sync 整批原子

def _shared_payload(tmp_path, name="src.db"):
    src = _engine(tmp_path, name)
    src.set_anchor("锚点甲")
    src.add_structure_node("结构甲")
    payload = src.prepare_shared_sync()
    return src, payload


def test_g6_mixed_payload_leaves_zero_rows(tmp_path):
    """G6：载荷含非 str content ⇒ 整批零落库（#771 ④）。"""
    src, payload = _shared_payload(tmp_path, "g6_src.db")
    try:
        items = list(payload["sync_payload"])
        bad = dict(items[0])
        bad["id"] = "shared_bad_content"
        bad["content"] = 12345
        rcv = _engine(tmp_path, "g6_rcv.db")
        try:
            before = rcv.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            with pytest.raises(TypeError):
                rcv.apply_shared_sync({"sync_payload": [items[0], bad]})
            after = rcv.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            assert after == before == 0, (
                "#771 ④ 混合载荷非原子：合法条目已落库（before=%d after=%d）" % (before, after))
            assert rcv.store.get_node(items[0]["id"]) is None, "#771 ④ 合法条目仍落库"
        finally:
            rcv.close()
    finally:
        src.close()


@pytest.mark.parametrize("label,payload", [("None", None), ("str", "oops"), ("int", 3)],
                         ids=["None", "str", "int"])
def test_g6b_malformed_payload_shapes_raise_typeerror(tmp_path, label, payload):
    """G6b：`payload` 三形态 ⇒ 可诊断 `TypeError`（不再静默返回空结果 / 裸 AttributeError）。"""
    eng = _engine(tmp_path, "g6b_%s.db" % label)
    try:
        with pytest.raises(TypeError):
            eng.apply_shared_sync(payload)
    finally:
        eng.close()


def test_g6c_legal_payload_still_applies(tmp_path):
    """G6c（阳性对照 · 防过修）：合法载荷照常 applied，冲突/幂等口径不变。"""
    src, payload = _shared_payload(tmp_path, "g6c_src.db")
    try:
        rcv = _engine(tmp_path, "g6c_rcv.db")
        try:
            res = rcv.apply_shared_sync(payload)
            ids = {it["id"] for it in payload["sync_payload"]}
            assert set(res["applied"]) == ids, res
            assert res["failed"] == [] and res["rejected"] == [], res
            again = rcv.apply_shared_sync(payload)
            assert again["applied"] == [] and set(again["identical"]) == ids, again
        finally:
            rcv.close()
    finally:
        src.close()


# ══════════════════════════════════════════ G7 干净往返读数逐位不变

def test_g7_clean_export_import_roundtrip_bit_identical(tmp_path):
    """G7（阳性对照 · 防过修）：无篡改备份的往返读数逐位不变。"""
    src = _engine(tmp_path, "g7_src.db")
    try:
        src.store.add_node(_node("r1", "往返记忆一", layer=MemoryLayer.CONTEXT))
        src.store.add_node(_node("r2", "往返记忆二", tags=["no_forget"]))
        path = str(tmp_path / "g7.json")
        exp = src.export_all(path)
        src_tags = {nid: list(src.store.get_node(nid).tags or [])
                    for nid in ("r1", "r2")}

        dst = _engine(tmp_path, "g7_dst.db")
        try:
            ret = dst.import_all(path)
            assert ret["imported"]["nodes"] == exp["exported_nodes"] == 2, ret
            assert ret["integrity_ok"] is True, ret
            assert ret["normalized_values"] == {} and ret["skipped_rows"] == {}, ret
            assert ret["requires_review"] is False, ret
            assert ret["protected_tags_kept"] == [], ret
            assert ret["protected_rows_overwritten"] == [], ret
            dst_tags = {nid: list(dst.store.get_node(nid).tags or [])
                        for nid in ("r1", "r2")}
            assert dst_tags == src_tags, (
                "G7 干净往返 tags 变了：%r != %r" % (dst_tags, src_tags))
        finally:
            dst.close()
    finally:
        src.close()
