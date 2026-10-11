# -*- coding: utf-8 -*-
"""#167 待终裁的提案节点被衰减/FIFO 静默删除，终裁仍返「已入结构层」—— 守卫

缺陷（lingshu issue #167 · 判定成立，实读复现见下）：
  `propose_promotion` 只 `tag_node(node_id, "promotion_pending")`，**未** protect；
  被提案节点若为情境层，会在 `decay_cycle`（情境层自然遗忘）或
  `enforce_context_cap`（FIFO）里被静默删除。此后终裁通过时，旧实现
  `adjudicate_promotion` 无条件 `UPDATE nodes SET layer='structure' WHERE id=?`
  （不查 rowcount）＋ 无条件标 `approved` ⇒ 结构层 **0 行写入**，审计却记
  「已入结构层」，指向一个不存在的节点。老提案 `content_hash` 为空时 #140 的
  哈希闸不生效，此路必现。
  实测（HEAD，修前）：节点被删 + `content_hash=''` ⇒
  `adjudicate_promotion(...) → True`、`proposal.status = 'approved'`，而 nodes 表
  无该行。

修法（最小 · 节点存续门，fail-closed）：终裁前节点已不存在 ⇒ 提案标 `rejected`、
  落一条 `action_logs`（把「静默删除」变为可审计的显式拒绝）、返回 `None`。

判据来源：分诊表 `.tmp/triage_lingshu_ALL.md` 行 167；仓内无规定「待终裁节点
存续」的理论章节，追不到更早出处（经验标定，本件 #167）。
边界（削掉的判别力）：**未**引入「pending 期间 protect 该节点」——protect 是永久、
无 unprotect 生命周期（B-3/D-15），属设计取舍，本件不擅动；本闸只保证不谎报 + 留痕。

断言组（抽掉节点存续门即 A/B 变红）：
  A 情境层自然遗忘路径：节点被 decay 删除 ⇒ 终裁返回 False、提案 `rejected`、
    结构层无该 id。
  B FIFO 路径：`set_context_cap` 淘汰后同上。
  C 留痕：A 场景下 `action_logs` 出现 `promotion_node_missing` 一行。
  D 防误杀：节点存续时终裁通过照常返 True、写结构层、标 `approved`。

运行（仓根）：python -X utf8 -m pytest tests/test_issue167_promotion_node_liveness_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

KEY = "GUARD-167-KEY"


def _db(name):
    return os.path.join(tempfile.mkdtemp(prefix="lingshu167_"), name + ".db")


def _propose(e, importance, text="待终裁的提案节点"):
    n = e.add_context(text, importance=importance)
    pid = e.propose_promotion(n.id, "提议者", "理由")
    assert e.verify_promotion(pid, "复核者") is True
    # 老提案（#140 之前创建）：content_hash 为空 ⇒ 哈希闸不生效，暴露存续门缺口
    e.store.conn.execute("UPDATE promotion_proposals SET content_hash='' WHERE id=?", (pid,))
    e.store.conn.commit()
    return n.id, pid


def _proposal_status(e, pid):
    c = e.store.conn.cursor()
    c.execute("SELECT status FROM promotion_proposals WHERE id=?", (pid,))
    return c.fetchone()["status"]


def test_a_decayed_node_does_not_report_promoted(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(_db("decay"))
    nid, pid = _propose(e, importance=0.15)
    e.decay_cycle(factor=0.9, min_confidence=0.1)
    assert e.store.get_node(nid) is None, "前置：节点应已被情境层遗忘删除"
    assert e.adjudicate_promotion(pid, "终裁者", True, designer_key=KEY) is False
    assert _proposal_status(e, pid) == "rejected"
    assert e.store.get_node(nid) is None


def test_b_fifo_evicted_node_does_not_report_promoted(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(_db("fifo"))
    nid, pid = _propose(e, importance=0.5, text="FIFO 待终裁")
    e.set_context_cap(1)
    e.add_context("新情境挤掉旧的", importance=0.5)
    assert e.store.get_node(nid) is None, "前置：节点应已被 FIFO 淘汰"
    assert e.adjudicate_promotion(pid, "终裁者", True, designer_key=KEY) is False
    assert _proposal_status(e, pid) == "rejected"


def test_c_rejection_is_audited(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(_db("audit"))
    nid, pid = _propose(e, importance=0.15)
    e.decay_cycle(factor=0.9, min_confidence=0.1)
    e.adjudicate_promotion(pid, "终裁者", True, designer_key=KEY)
    c = e.store.conn.cursor()
    c.execute("SELECT COUNT(*) FROM action_logs WHERE action_type='promotion_node_missing'")
    assert c.fetchone()[0] >= 1, "节点缺失被静默吞掉：无 promotion_node_missing 审计"


def test_d_existing_node_still_promotes(monkeypatch):
    """防误杀：节点存续时终裁照常通过（存续门不得拦正常路径）。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    e = SpacetimeMemoryEngine(_db("ok"))
    nid, pid = _propose(e, importance=0.5, text="存续的提案节点")
    assert e.adjudicate_promotion(pid, "终裁者", True, designer_key=KEY) is True
    assert _proposal_status(e, pid) == "approved"
    n = e.store.get_node(nid)
    assert n is not None and n.layer.value == "structure"
