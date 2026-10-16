# -*- coding: utf-8 -*-
"""test_issue712_approve_reviewer_set_guard · `approve()` 互斥判据的基数（#712，P1）

判据来源
--------
issue #712：`approve()` 用
`SELECT * FROM structure_reviews WHERE proposal_id=? ORDER BY rowid DESC LIMIT 1`
把「全部历史复核人中最后一个」当成「复核人」，判据基数从**集合**退化为**单元素**。
于是做过 `needs_info`（明确的反对票＝要求补充信息）复核的主体，只要不再出现在最后
一条 review 上，就能**自己终裁通过**，写入 `structure_records`（`approver` 落该主体）、
推进 `structure_heads` revision，并经 `_record_structure_event(record_class="normative")`
生成**不可遗忘**的规范性节点——而该模块**无撤销 API**。

实现侧口径（为什么这样判）
--------------------------
同一函数内该行自己的错误文案是 `proposer, reviewer and approver must be distinct`，
即契约要求三者互斥。**复核人是一个集合**，不是「最新一条」。故本守卫按「actor 的 subject
∈ {proposer} ∪ {全部历史复核人}」判。这也自然覆盖 issue 建议 ③（needs_info 提出者不得
终裁）——`review()` 是 `structure_reviews` 的唯一写入方，提出 `needs_info` 必然已入表。

范围（本守卫不判什么）
----------------------
不判 issue 建议 ①（把 `reviewed` 也当封闭态）。那是**对外语义变更**：现语义下
`reviewed` 之后仍可重开复核（`needs_info` → 他人 `approve` 使 state 回到 `reviewed` 的
复核循环就依赖它），本件不改。见 `test_reviewed_state_still_reopenable`（该用例锁住
「不越界」这一事实，防止后续误把 ① 混进本件）。

判别力（回退即红）
------------------
把互斥判据回退成 `ORDER BY rowid DESC LIMIT 1`（缺陷形态）⇒
`test_needs_info_reviewer_cannot_self_approve` 与
`test_earlier_reviewer_cannot_self_approve` 双双变红（实测放行而非 PermissionDenied）。
"""
import pytest

from lingshu.core.core import LayeredStore, Role, SpacetimeMemoryEngine
from lingshu.core.structure_writer import (ActorContext, Conflict, PermissionDenied,
                                           ProposalInput, StructureWriter)


def host(path):
    engine = SpacetimeMemoryEngine.__new__(SpacetimeMemoryEngine)
    engine.role = Role.PRIMARY
    engine.store = LayeredStore(str(path), role=Role.PRIMARY)
    return engine


def actor(subject, *capabilities):
    return ActorContext(subject, "lab", subject, frozenset(capabilities))


REVIEW = ("structure.review", "structure.read")
APPROVE = ("structure.approve", "structure.read")
PROPOSE = ("events.write", "structure.propose", "structure.read")


@pytest.fixture
def gov(tmp_path):
    engine = host(tmp_path / "gov.sqlite3")
    writer = StructureWriter(engine)
    try:
        yield engine, writer
    finally:
        engine.store.close()


def _propose(writer, request_id, aggregate_key):
    return writer.propose(actor("proposer", *PROPOSE),
                          ProposalInput(request_id, aggregate_key, "anchor",
                                        {"weight": 0.8}, 0))


def _reviews_of(engine, proposal_id):
    return [(r["reviewer"], r["decision"]) for r in engine.store.conn.execute(
        "SELECT reviewer, decision FROM structure_reviews WHERE proposal_id=? ORDER BY rowid",
        (proposal_id,)).fetchall()]


def _records_of(engine, proposal_id):
    return engine.store.conn.execute(
        "SELECT approver FROM structure_records WHERE proposal_id=?", (proposal_id,)).fetchall()


# ---------- 正向：判据基数必须是集合 ----------

def test_needs_info_reviewer_cannot_self_approve(gov):
    """#712 正向（issue 复现路径）：做过 `needs_info` 的复核人不得成为终裁人。"""
    engine, writer = gov
    p = _propose(writer, "p-712", "agg-712")
    pid = p["proposal_id"]
    assert writer.review(actor("alice", *REVIEW), pid, "approve")["state"] == "reviewed"
    assert writer.review(actor("bob", *REVIEW), pid, "needs_info")["state"] == "pending"
    assert writer.review(actor("carol", *REVIEW), pid, "approve")["state"] == "reviewed"
    # 审计表里 bob 确在案（硬性断言非空，防「表为空所以恰好通过」的假绿）
    assert ("bob", "needs_info") in _reviews_of(engine, pid)
    assert len(_reviews_of(engine, pid)) == 3

    with pytest.raises(PermissionDenied):
        writer.approve(actor("bob", *APPROVE), pid)
    # 不得留下任何终裁痕迹（规范性节点不可撤销，故「没写进去」本身是判据）
    assert _records_of(engine, pid) == []
    assert writer.get_proposal(actor("proposer", *PROPOSE), pid)["state"] == "reviewed"
    assert writer.target_status(actor("proposer", *PROPOSE), "agg-712")["revision"] == 0


def test_earlier_reviewer_cannot_self_approve(gov):
    """#712 的更一般形态：**任意早期复核人**（不限于 needs_info 提出者）都不得自己终裁。
    issue 只标注了 needs_info 一条可达路径；本条证明缺口面更宽——
    `alice(approve)` → `carol(approve)` → `alice(approve)` 在缺陷形态下同样放行。"""
    engine, writer = gov
    p = _propose(writer, "p-early", "agg-early")
    pid = p["proposal_id"]
    writer.review(actor("alice", *REVIEW), pid, "approve")
    writer.review(actor("carol", *REVIEW), pid, "approve")
    assert [r[0] for r in _reviews_of(engine, pid)] == ["alice", "carol"]

    with pytest.raises(PermissionDenied):
        writer.approve(actor("alice", *APPROVE), pid)
    assert _records_of(engine, pid) == []


# ---------- 正对照：量具不是恒红 ----------

def test_unrelated_third_party_is_allowed(gov):
    """正对照 1：无关第三方仍可正常终裁（证明判据不是「一律拒绝」）。"""
    engine, writer = gov
    p = _propose(writer, "p-ok", "agg-ok")
    pid = p["proposal_id"]
    writer.review(actor("reviewer", *REVIEW), pid, "approve")
    out = writer.approve(actor("approver", *APPROVE), pid)
    assert out["approver"] == "approver" and out["revision"] == 1
    assert _records_of(engine, pid)[0][0] == "approver"


def test_proposer_and_sole_reviewer_still_denied(gov):
    """正对照 2：既有拦截面不被本次改动破坏 —— proposer 拦、唯一复核人自己拦。"""
    engine, writer = gov
    p = _propose(writer, "p-old", "agg-old")
    pid = p["proposal_id"]
    writer.review(actor("reviewer", *REVIEW), pid, "approve")
    with pytest.raises(PermissionDenied):
        writer.approve(actor("proposer", *APPROVE), pid)
    with pytest.raises(PermissionDenied):
        writer.approve(actor("reviewer", *APPROVE), pid)
    assert _records_of(engine, pid) == []


def test_repeated_approve_keeps_replay_shape(gov):
    """正对照 3：已 committed 的重复 `approve()` 仍按原语义返回同一条 record
    （返回体形状不变；互斥判据的加严不得波及重放分支）。"""
    engine, writer = gov
    p = _propose(writer, "p-replay", "agg-replay")
    pid = p["proposal_id"]
    writer.review(actor("reviewer", *REVIEW), pid, "approve")
    first = writer.approve(actor("approver", *APPROVE), pid)
    again = writer.approve(actor("approver", *APPROVE), pid)
    assert again["id"] == first["id"]
    assert set(again) == set(first)
    assert len(_records_of(engine, pid)) == 1


def test_reviewer_of_another_proposal_is_not_excluded(gov):
    """正对照 4（过修判别位）：互斥判据只作用于**本提案**的复核历史。
    在别的提案上做过复核的主体，对本提案仍是无关第三方 ⇒ 必须放行。
    （把集合查询的 `WHERE proposal_id=?` 抽掉即「过修」：本用例必红。）"""
    engine, writer = gov
    other = _propose(writer, "p-other", "agg-other")
    writer.review(actor("r-other", *REVIEW), other["proposal_id"], "approve")
    writer.approve(actor("a-other", *APPROVE), other["proposal_id"])

    p = _propose(writer, "p-self", "agg-self")
    pid = p["proposal_id"]
    writer.review(actor("reviewer", *REVIEW), pid, "approve")
    # r-other 在**别的提案**上是复核人，在本提案上不是 ⇒ 应放行
    out = writer.approve(actor("r-other", *APPROVE), pid)
    assert out["approver"] == "r-other" and out["revision"] == 1


def test_reviewed_state_still_reopenable(gov):
    """边界（本件**不**改的那条）：`reviewed` 之后仍可重开复核——`needs_info` 把 state
    退回 `pending`、他人 `approve` 再推回 `reviewed` 的复核循环依赖此语义。
    本件只改 `approve()` 的判据基数，不动 `review()` 的封闭态集合（issue 建议 ① 需
    设计者裁：把它当封闭态会改变这条既有对外语义）。"""
    engine, writer = gov
    p = _propose(writer, "p-reopen", "agg-reopen")
    pid = p["proposal_id"]
    assert writer.review(actor("r1", *REVIEW), pid, "approve")["state"] == "reviewed"
    assert writer.review(actor("r2", *REVIEW), pid, "needs_info")["state"] == "pending"
    assert writer.review(actor("r3", *REVIEW), pid, "approve")["state"] == "reviewed"
    # 已封闭态仍按原语义拒绝
    q = _propose(writer, "p-rejected", "agg-rejected")
    writer.review(actor("r4", *REVIEW), q["proposal_id"], "reject")
    with pytest.raises(Conflict):
        writer.review(actor("r5", *REVIEW), q["proposal_id"], "approve")
