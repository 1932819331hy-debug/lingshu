# -*- coding: utf-8 -*-
"""#674 守卫：`import_all` 的 **edges 分支**补实例角色闸（与 `add_edge` 同一真源）。

缺陷态（修复前 main，issue #674 复现实测）：
  `#586` 给 `import_all` 补的角色闸判据是「备份 `nodes` 行的**声明层** ∪ 库内旧行
  `layer` ∈ `SHARED_LAYERS`」——`edges` 表无 `layer` 列，**天然不在该判据内**。于是
  edges 分支只有 `#93` 的孤儿预筛，随后裸 `INSERT OR REPLACE`：
    · **新增面**：SUB 用 edges-only 备份（端点指向库内既有 structure 节点）**无密钥**
      即可落一条共享层端点边；同一引擎上 `add_edge` 对同一条边抛 `PermissionError`。
    · **覆盖面**：SUB 用同 id 覆盖 PRIMARY 已存在的共享层已验证边（改端点、
      `confidence` 0.9→0.01、`verified` 1→0），全程无密钥、无审计。
  两条后果即 `#234`/`#244` 所述：共享层边 `decay_cycle` 显式跳过 ⇒ 永不衰减、不可删，
  且立即参与 PRIMARY 的因果推理。

修法（落点 `lingshu/core/core.py`，写权限单一真源 `SHARED_LAYERS`）：
  把边角色判据抽为 `LayeredStore._assert_edge_endpoints_role(source_id, target_id)`
  （**唯一真源**），`_assert_edge_role(edge)` 改为委托它；`import_all` 的 edges 分支在
  写入前对每行调用同一方法，并对**同 id 旧边**的端点再判一次（覆盖面）——非 PRIMARY
  即抛 `PermissionError`，`@_transactional` 整次回滚 ⇒ **零落库**（与 `#586`/`#478`
  的「先判后写」同口径）。

组（回退即红）：
  G1 新增面：SUB ＋ edges-only 备份（端点含 structure）⇒ `PermissionError` 且零落边。
  G2 覆盖面：SUB ＋ 同 id 覆盖 PRIMARY 既有共享层边（新行端点为本地层）⇒
     `PermissionError`，且原边 `source_id/target_id/confidence/verified` **逐字段不变**。
  G3 正对照（防过修）：PRIMARY ＋ 同一份备份 ⇒ 两条均放行、真落库。
  G4 防误杀：SUB ＋ **本地层端点**边备份 ⇒ 照常导入（闸不误伤本地面）。
  G5 文案可判：拒绝信息含 `role=sub` 与「共享层」字样（不只断言异常类型）。

判别力（变异自证）：把 edges 分支新增的两处 `_assert_edge_endpoints_role` 调用摘掉
  ⇒ G1/G2 必红（放行复现）；还原 ⇒ 全绿、`git diff` 为空。

运行：python -X utf8 -m pytest tests/test_issue674_import_edges_role_guard.py -q
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from lingshu.core.core import EdgeType, Role, SpacetimeMemoryEngine


def _mk(tmp, name, role=None):
    return SpacetimeMemoryEngine(os.path.join(tmp, name), role=role or Role.PRIMARY)


def _edge_row(eng, edge_id):
    c = eng.store.conn.cursor()
    c.execute("SELECT * FROM edges WHERE id=?", (edge_id,))
    return dict(c.fetchone())


def _write_backup(rows):
    path = os.path.join(tempfile.mkdtemp(), "edges-only.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"edges": rows}, f, ensure_ascii=False)
    return path


@pytest.fixture
def scene():
    """PRIMARY 库：两个 structure 节点 + 一条共享层已验证边 + 两个本地层节点。

    返回 (db, primary, sub, a, b, e0, k1, k2)（sub 指向**同一个库文件**）。
    """
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "s674.db")
    p = SpacetimeMemoryEngine(db)
    a = p.add_structure_node("P0: 共享规则甲")
    b = p.add_structure_node("P0: 共享规则乙")
    e0 = p.add_edge(a.id, b.id, EdgeType.CAUSAL, confidence=0.9)
    p.verify_edge(e0.id, 0.9)
    k1 = p.add_perception("本地知识甲")
    k2 = p.add_perception("本地知识乙")
    s = SpacetimeMemoryEngine(db, role=Role.SUB)
    try:
        yield db, p, s, a, b, e0, k1, k2
    finally:
        p.close()
        s.close()


# --------------------------------------------------------------- G1 新增面

def test_g1_sub_new_shared_edge_backup_rejected_zero_write(scene):
    db, p, s, a, b, e0, k1, k2 = scene
    row = _edge_row(p, e0.id)
    p.store.conn.execute("DELETE FROM edges WHERE id=?", (e0.id,))
    p.store.conn.commit()
    new_row = dict(row, id="e674_new")
    path = _write_backup([new_row])
    with pytest.raises(PermissionError) as ei:
        s.import_all(path)                     # 故意不传密钥（#674 的触发形态）
    assert "role=sub" in str(ei.value) and "共享层" in str(ei.value), (
        "#674 拒绝文案应指明角色与共享层面：%s" % ei.value)
    c = p.store.conn.cursor()
    c.execute("SELECT COUNT(*) FROM edges WHERE id=?", ("e674_new",))
    assert c.fetchone()[0] == 0, "#674：SUB 经 edges-only 备份越权落了共享层端点边"


# --------------------------------------------------------------- G2 覆盖面

def test_g2_sub_overwrite_existing_shared_edge_rejected_fields_intact(scene):
    db, p, s, a, b, e0, k1, k2 = scene
    before = _edge_row(p, e0.id)
    # 新行端点全为**本地层**（新增面闸放行）⇒ 只有「同 id 旧边端点」这一腿能拦
    forged = dict(before, id=e0.id, source_id=k1.id, target_id=k2.id,
                  confidence=0.01, verified=0)
    path = _write_backup([forged])
    with pytest.raises(PermissionError) as ei:
        s.import_all(path)
    assert "role=sub" in str(ei.value) and "共享层" in str(ei.value), (
        "#674 覆盖面拒绝文案应指明角色与共享层面：%s" % ei.value)
    after = _edge_row(p, e0.id)
    assert after["source_id"] == before["source_id"], "#674：共享层边源端点被 SUB 改写"
    assert after["target_id"] == before["target_id"], "#674：共享层边目标端点被 SUB 改写"
    assert after["confidence"] == before["confidence"], "#674：共享层边 confidence 被 SUB 改写"
    assert after["verified"] == before["verified"], "#674：共享层边 verified 被 SUB 抹掉"


# --------------------------------------------------------------- G3 正对照

def test_g3_primary_same_backup_still_allowed(scene):
    db, p, s, a, b, e0, k1, k2 = scene
    row = _edge_row(p, e0.id)
    new_row = dict(row, id="e674_pri")
    path = _write_backup([new_row])
    r = p.import_all(path)
    assert r["imported"].get("edges", 0) == 1, "PRIMARY 导入共享层端点边应放行（防过修）"
    assert p.store.get_edge("e674_pri") is not None


# --------------------------------------------------------------- G4 防误杀

def test_g4_sub_local_endpoint_edges_still_import(scene):
    db, p, s, a, b, e0, k1, k2 = scene
    local = s.add_edge(k1.id, k2.id, EdgeType.CAUSAL, confidence=0.4)
    row = _edge_row(p, local.id)
    p.store.conn.execute("DELETE FROM edges WHERE id=?", (local.id,))
    p.store.conn.commit()
    r = s.import_all(_write_backup([row]))
    assert r["imported"].get("edges", 0) == 1, "SUB 导入本地层端点边不应被角色闸误伤"
    assert p.store.get_edge(local.id) is not None
