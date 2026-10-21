# -*- coding: utf-8 -*-
"""#765 / #774 守卫：`EvidenceLedger` 写面（`record` / `sync`）补共享层角色闸。

缺陷态（修复前 `origin/main`，本端独立复现）：
  · `LayeredStore` 的共享层写保护只在 `add_node` 一处，`EvidenceLedger` 的写面
    **没有任何层/角色闸** ⇒ SUB 实例拿到账本对象后直调
    `ledger.record(anchor_id, source="x", polarity=CONTRADICT)`
    即把 PRIMARY 建的锚点节点 `confidence 1.000000 → 0.333333`
    （`status=refuted`）；单独直调 `ledger.sync(anchor_id)` 则 `→ 0.5`。
    另起 PRIMARY 实例读回一致 ⇒ 跨实例可见的真实损坏（#765）。
  · `enable_evidence_ledger()` 无角色检查且返回**同一可变句柄**
    （两次调用 `is` 同一对象）⇒ #767 那类「只堵引擎入口」的修复收不回账本对象
    本身，SUB 仍可绕过引擎直调（#774）。

修法（落点 `lingshu/core/evidence.py`，不碰 `core.py`）：
  私有助手 `_assert_shared_layer_writable(node_id)`——判据与仓内单一真源同款：
  节点所在层 ∈ `self.store.SHARED_LAYERS` 且 `self.store.role != PRIMARY`
  ⇒ `PermissionError`。在 `record()` 的 **INSERT 之前** 与 `sync()` 的
  **UPDATE 之前** 各调一次。

  ★ 闸必须在 INSERT 之前：`record()` 是先 INSERT 再调 `sync()`，若只在 `sync`
    落闸，SUB 被拒时 `evidence` 表已多出一行**孤儿证据行**（#774 的决定性一格，
    见 G3）。

断言组（抽掉修复即红）：
  G1 SUB 对锚点 `record` ⇒ `PermissionError`，且 `confidence` 逐位不变。
  G2 SUB 对锚点 `sync`   ⇒ `PermissionError`，且 `confidence` 逐位不变。
  G3 孤儿行：G1 之后该节点 `evidence` 行数 == 0（整表行数亦不变）。
  G4 结构层同上（`record` / `sync` 两臂）。
  G5 闸读的是 `store.role`（单一真源）：PRIMARY 引擎上把 `store.role` 改成 SUB
     ⇒ 同样被拒（防「只看 engine.role」的旁路）。
  G6 阴性对照（**防过修**）：SUB 对**自有** context 层 `record` / `sync` 照旧成功。
  G7 阳性对照（**防过修**）：PRIMARY 走 `add_evidence` / `add_perception` /
     账本直调全链，读数与修前**逐位相同**（见各断言里的字面值）。

判别力：把 `lingshu/core/evidence.py` 回退到 `origin/main` 形态 ⇒ G1–G5 必红
（放行 + 置信度被改），G6/G7 仍绿；还原后全绿且 `git diff` 为空。

运行：python -X utf8 -m pytest tests/test_issue765_774_evidence_ledger_role_guard.py -q
"""
from __future__ import annotations

import os
import tempfile

import pytest

from lingshu.core.core import Role, SpacetimeMemoryEngine
from lingshu.core.evidence import CONTRADICT, SUPPORT

pytestmark = pytest.mark.filterwarnings("ignore")


def _sub_of(primary):
    """在同一库文件上再建一个 SUB 实例（跨实例同步面）。"""
    return SpacetimeMemoryEngine(primary.store.db_path, role=Role.SUB)


def _evidence_rows(eng, node_id):
    c = eng.store.conn.cursor()
    c.execute("SELECT COUNT(*) FROM evidence WHERE node_id=?", (node_id,))
    return c.fetchone()[0]


def _evidence_total(eng):
    c = eng.store.conn.cursor()
    c.execute("SELECT COUNT(*) FROM evidence")
    return c.fetchone()[0]


@pytest.fixture
def world():
    """PRIMARY 建锚点/结构层节点 + 同库 SUB 实例（带账本句柄）。"""
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "shared.db")
    p = SpacetimeMemoryEngine(db)
    p.enable_evidence_ledger()
    anchor = p.set_anchor("锚点：共享层不可被 SUB 经证据账本改写")
    struct = p.add_structure_node("结构：共享层不可被 SUB 经证据账本改写", importance=0.9)
    sub = _sub_of(p)
    ledger = sub.enable_evidence_ledger()
    return {"primary": p, "sub": sub, "ledger": ledger,
            "anchor": anchor, "struct": struct}


# ---------------------------------------------------------------- G1 record 锚点

def test_g1_sub_record_on_anchor_denied_and_confidence_unchanged(world):
    p, sub, led = world["primary"], world["sub"], world["ledger"]
    nid = world["anchor"].id
    before = p.store.get_node(nid).confidence
    assert before == 1.0
    with pytest.raises(PermissionError):
        led.record(nid, "attacker", CONTRADICT)
    assert sub.store.get_node(nid).confidence == before, "被拒后置信度被改动"
    assert p.store.get_node(nid).confidence == before, "跨实例读回置信度被改动"


# ---------------------------------------------------------------- G2 sync 锚点

def test_g2_sub_sync_on_anchor_denied_and_confidence_unchanged(world):
    p, sub, led = world["primary"], world["sub"], world["ledger"]
    nid = world["anchor"].id
    before = p.store.get_node(nid).confidence
    with pytest.raises(PermissionError):
        led.sync(nid)
    assert sub.store.get_node(nid).confidence == before, "sync 被拒后置信度被改动"
    assert p.store.get_node(nid).confidence == before


# ---------------------------------------------------------------- G3 孤儿行

def test_g3_no_orphan_evidence_row_after_denied_record(world):
    """#774 决定性一格：闸落 INSERT 之前 ⇒ 被拒的 record 不留孤儿证据行。"""
    sub, led = world["sub"], world["ledger"]
    nid = world["anchor"].id
    total_before = _evidence_total(sub)
    with pytest.raises(PermissionError):
        led.record(nid, "attacker", CONTRADICT)
    assert _evidence_rows(sub, nid) == 0, "被拒的 record 留下了孤儿证据行"
    assert _evidence_total(sub) == total_before, "evidence 表行数被改动"
    assert led.entries(nid) == [], "只读 entries 仍看到被拒证据"


# ---------------------------------------------------------------- G4 结构层

def test_g4_sub_record_and_sync_on_structure_denied(world):
    p, sub, led = world["primary"], world["sub"], world["ledger"]
    nid = world["struct"].id
    before = p.store.get_node(nid).confidence
    assert before == 1.0
    with pytest.raises(PermissionError):
        led.record(nid, "attacker", CONTRADICT)
    with pytest.raises(PermissionError):
        led.sync(nid)
    assert sub.store.get_node(nid).confidence == before, "结构层置信度被改动"
    assert _evidence_rows(sub, nid) == 0
    assert p.store.get_node(nid).layer.value == "structure"


# ---------------------------------------------------------------- G5 判据单一真源

def test_g5_gate_reads_store_role_not_engine_role(world):
    """闸的判据取 `self.store.role`（与 add_node 同源），不是 engine 侧副本。"""
    p, led = world["primary"], world["ledger"]
    nid = world["anchor"].id
    p.store.role = Role.SUB          # 只改 store（engine.role 仍是 PRIMARY）
    with pytest.raises(PermissionError):
        led.record(nid, "attacker", CONTRADICT)
    assert p.store.get_node(nid).confidence == 1.0


# ---------------------------------------------------------------- G6 阴性（本地面）

def test_g6_sub_local_context_layer_still_allowed(world):
    """阴性对照：SUB 对自己的**本地层**（context）记证据是合法用法，照旧放行。"""
    sub, led = world["sub"], world["ledger"]
    ctx = sub.add_context("SUB 自有情境节点")
    eid = led.record(ctx.id, "self-obs", SUPPORT)
    assert isinstance(eid, str) and eid
    assert led.sync(ctx.id) == pytest.approx(2.0 / 3.0, abs=1e-6)
    assert sub.store.get_node(ctx.id).confidence == pytest.approx(2.0 / 3.0, abs=1e-6)
    assert len(led.entries(ctx.id)) == 1
    # SUB 自有 knowledge 层（本地层）同样放行
    kn = sub.add_perception("SUB 自有知识节点", source="self-obs")
    assert sub.store.get_node(kn.id).layer.value == "knowledge"


# ---------------------------------------------------------------- G7 阳性（防过修）

def test_g7_primary_full_chain_readings_bitwise_unchanged(world):
    """阳性对照：PRIMARY 全链读数与修前逐位相同（含账本直调）。"""
    p = world["primary"]
    led = p.enable_evidence_ledger()          # PRIMARY 自己的账本句柄
    nid = world["anchor"].id
    # PRIMARY 直调账本：修前修后都应放行
    led.record(nid, "src1", CONTRADICT)
    assert p.store.get_node(nid).confidence == pytest.approx(0.333333, abs=1e-9)
    led.record(nid, "src2", SUPPORT)
    assert led.sync(nid) == pytest.approx(0.5, abs=1e-9)
    led.record(nid, "src3", SUPPORT)
    assert led.sync(nid) == pytest.approx(0.6, abs=1e-9)
    # 引擎面 add_evidence / add_perception
    a2 = p.set_anchor("锚点：PRIMARY 正对照")
    r = p.add_evidence(a2.id, "srcA", supports=False)
    assert (r["confidence"], r["status"], r["entries"]) == (0.333333, "refuted", 1)
    r2 = p.add_evidence(a2.id, "srcB", supports=True)
    assert (r2["confidence"], r2["status"], r2["entries"]) == (0.5, "contested", 2)
    n = p.add_perception("PRIMARY 感知", source="obs")
    assert p.store.get_node(n.id).confidence == pytest.approx(0.666667, abs=1e-9)
    assert len(led.entries(n.id)) == 1


# ---------------------------------------------------------------- G8 默认关闭面

def test_g8_default_off_face_untouched(monkeypatch):
    """不设开关时引擎侧不建账本 ⇒ 该面逐位不变（闸不参与）。"""
    for k in ("LINGSHU_EVIDENCE_LEDGER", "LINGSHU_TMS"):
        monkeypatch.delenv(k, raising=False)
    e = SpacetimeMemoryEngine(":memory:")
    a = e.set_anchor("锚点：默认关闭面")
    assert e._evidence is None
    assert e.store.get_node(a.id).confidence == 1.0
    assert e.add_evidence(a.id, "src", supports=False)["confidence"] == pytest.approx(
        0.333333, abs=1e-9)          # 引擎路径自行建账本，PRIMARY 放行
