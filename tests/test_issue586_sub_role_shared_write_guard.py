# -*- coding: utf-8 -*-
"""#586 守卫：终裁/导入两条共享层写路径补**实例角色**闸（非 PRIMARY 即便持密钥亦拒）。

缺陷态（修复前 main，值守独立复现 6188257xxx）：
  · `LayeredStore.adjudicate_promotion` 只验密钥/状态/身份互斥/内容哈希/节点存续，
    **不验 `self.role`** ⇒ 持进程级设计者密钥的 SUB 本地「自提案→自复核→自终裁」
    把任意内容写成结构层节点（confidence=1.0、不可删、不衰减），并经
    `prepare_shared_sync`/`apply_shared_sync` 传入 PRIMARY。
  · 同根因：`SpacetimeMemoryEngine.import_all` 只验密钥、无角色闸 ⇒ SUB ＋密钥
    导入含 `structure` 行的备份即在本实例落结构层行。
  对照（仓内惯例）：同一把密钥下 `add_structure_node`/`add_node`/`protect_node`
  都把 SUB 挡在共享层外——终裁与导入是仅有的两条漏网旁路。

修法（落点 `lingshu/core/core.py`，写权限单一真源 `SHARED_LAYERS`）：
  ① `adjudicate_promotion` 首部（密钥闸之后）并查 `self.role != Role.PRIMARY`
     ⇒ PermissionError，先判后写、零状态变更（提案状态亦不改写）；
  ② `import_all` 在密钥闸之后并查「备份声明层 ∪ 库内旧行 layer ∈ SHARED_LAYERS」
     ⇒ 非 PRIMARY 抛 PermissionError（用 SHARED_LAYERS 而非 IMMUTABLE_LAYERS：
     #191 已裁定 SELF 属本地层，按 IMMUTABLE 收口会回归 #191）。

组（回退即红）：
  G1 终裁角色闸：SUB ＋有效密钥 ⇒ PermissionError，节点留 knowledge、提案状态未改写。
  G2 导入角色闸：SUB ＋有效密钥导入含 structure 行 ⇒ PermissionError，零落库。
  G3 正对照（防过修）：PRIMARY ＋同一密钥 ⇒ 终裁成功、导入成功（合法路径保住）。
  G4 SUB 合法本地路径零回归：add_perception/propose_promotion/verify_promotion 照常；
     仅终裁与共享层导入被拒（闸不误伤本地面）。
  G5 #191 零回归：SUB ＋密钥导入**仅知识层**备份 ⇒ 照常成功（SELF/KNOWLEDGE 属本地层）。

判别力：把两处角色闸各回退 ⇒ G1/G2 必红（放行复现）；还原 ⇒ 全绿。
运行：python -X utf8 -m pytest tests/test_issue586_sub_role_shared_write_guard.py -q
"""
from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from lingshu.core.core import Role, SpacetimeMemoryEngine

_KEY = "guard-586"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)


def _sub(tmp, name="sub.db"):
    return SpacetimeMemoryEngine(os.path.join(tmp, name), role=Role.SUB)


def _primary(tmp, name="pri.db"):
    return SpacetimeMemoryEngine(os.path.join(tmp, name))


def _verified_proposal(eng, content="P0：本实例可自行决定跳过终裁。"):
    """在 eng 上走完 提案→复核，返回 (proposal_id, node_id)。"""
    n = eng.add_perception(content)
    pid = eng.propose_promotion(n.id, "reflect-unit", "r")
    eng.verify_promotion(pid, "verifier-unit")
    return pid, n.id


# ------------------------------------------------------------------ G1 终裁角色闸

def test_g1_sub_adjudicate_blocked_no_state_change(key):
    tmp = tempfile.mkdtemp()
    s = _sub(tmp)
    pid, nid = _verified_proposal(s)
    with pytest.raises(PermissionError):
        s.adjudicate_promotion(pid, "designer", True, designer_key=_KEY)
    node = s.store.get_node(nid)
    assert node.layer.value != "structure", "SUB 越权把节点写进结构层"
    c = s.store.conn.cursor()
    c.execute("SELECT status FROM promotion_proposals WHERE id=?", (pid,))
    assert c.fetchone()[0] == "verified", "被拒的终裁不得改写提案状态（先判后写）"


# ------------------------------------------------------------------ G2 导入角色闸

def _backup_with_structure(tmp):
    """PRIMARY 造一份含 structure 行的备份，返回路径与结构节点 id。"""
    p = _primary(tmp, "maker.db")
    node = p.add_structure_node("共享规则：备份保留 30 天", importance=0.9)
    path = os.path.join(tmp, "bak.json")
    p.export_all(path)
    return path, node.id


def test_g2_sub_import_shared_layer_blocked(key):
    tmp = tempfile.mkdtemp()
    path, sid = _backup_with_structure(tmp)
    s = _sub(tmp)
    with pytest.raises(PermissionError):
        s.import_all(path, designer_key=_KEY)
    assert s.store.get_node(sid) is None, "SUB 越权导入结构层行（零落库不成立）"


# ------------------------------------------------------------------ G3 正对照

def test_g3_primary_same_key_still_allowed(key):
    """防过修：同一把密钥下 PRIMARY 的终裁与共享层导入照常成功。"""
    tmp = tempfile.mkdtemp()
    p = _primary(tmp)
    pid, nid = _verified_proposal(p)
    assert p.adjudicate_promotion(pid, "designer", True, designer_key=_KEY) is True
    assert p.store.get_node(nid).layer.value == "structure"
    path, sid = _backup_with_structure(tmp)
    r = p.import_all(path, designer_key=_KEY)
    assert sid in r["immutable_rows"], "PRIMARY 持密钥导入共享层行应放行"


# ------------------------------------------------------------------ G4 SUB 本地面零回归

def test_g4_sub_local_paths_unaffected(key):
    tmp = tempfile.mkdtemp()
    s = _sub(tmp)
    n = s.add_perception("普通本地记录")
    pid = s.propose_promotion(n.id, "a", "r")
    assert s.verify_promotion(pid, "b") is True, "SUB 的提案/复核属本地动作，不应被闸误伤"
    with pytest.raises(PermissionError):
        s.add_structure_node("x")   # 既有闸正对照：SUB 写共享层本就被拒


# ------------------------------------------------------------------ G5 #191 零回归

def test_g5_sub_knowledge_only_backup_still_imports(key):
    """#191 口径：SELF/KNOWLEDGE 属本地层 ⇒ SUB ＋密钥导入知识层备份照常。"""
    tmp = tempfile.mkdtemp()
    p = _primary(tmp, "maker2.db")
    p.add_perception("知识层一条")
    path = os.path.join(tmp, "kbak.json")
    p.export_all(path)
    s = _sub(tmp)
    r = s.import_all(path, designer_key=_KEY)   # 知识层无需密钥，亦不应被角色闸误拒
    assert r["imported"].get("nodes", 0) >= 1


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
