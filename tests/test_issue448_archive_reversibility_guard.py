# -*- coding: utf-8 -*-
"""#448 守卫：归档必须真可逆——还原清标签、恢复原值、可再归档，硬删不留登记行。

缺陷面（公开 issue #448，2026-10-10 独立复核成立）：
`forget_advisor` 的 note 承诺「可逆」，但仓内没有反向入口——`archived` 标签只加
不清，`forget_advisor` 又对带该标签的节点 `continue` ⇒ 节点被永久锁在归档态；
`LayeredStore` 也没有 `untag_node`，残留无法用公开 API 清除。本件补
`untag_node` ＋ `restore_archived` ＋ `archive_state` 归档区登记表，本文件把
「可逆」锁成判据：

  ① 归档→还原往返后 tags 与 importance 回到初态（issue 回归建议 3）；
  ② 还原后再调 `forget_advisor` 会再次归档（状态机闭合，不是「还原后免疫」）；
  ③ `archive_state` 行随归档建立、随还原/硬删消失（有行 ⇔ 当前归档态）；
  ④ `untag_node` 与 `tag_node` 对称：幂等、撤销 `no_forget` 落审计。
"""
import math
import os
import tempfile

import pytest

from lingshu.core.core import SpacetimeMemoryEngine


def _engine(tmp_path=None):
    d = tmp_path or tempfile.mkdtemp()
    return SpacetimeMemoryEngine(os.path.join(str(d), "probe.db"))


def _registry(e):
    c = e.store.conn.cursor()
    c.execute("SELECT node_id, pre_archive_importance, archived_at FROM archive_state")
    return {r[0]: (r[1], r[2]) for r in c.fetchall()}


def _actions(e, action_type):
    c = e.store.conn.cursor()
    c.execute("SELECT action_type FROM action_logs WHERE action_type=?", (action_type,))
    return [r[0] for r in c.fetchall()]


# ---------------------------------------------------------------- ① 往返闭合

def test_archive_restore_roundtrip_returns_to_initial_state(tmp_path):
    e = _engine(tmp_path)
    n = e.add_context("归档候选", importance=0.15)
    before = e.store.get_node(n.id)
    assert "archived" not in before.tags

    out = e.forget_advisor(stale_days=0, low_value=0.2)
    assert out["archived"] == 1
    mid = e.store.get_node(n.id)
    assert "archived" in mid.tags, "归档必须打 archived 标签"
    assert mid.importance == pytest.approx(0.1), (
        "归档应把 importance 降到 archived_imp（min 口径，只降不升）")
    assert n.id in _registry(e), "归档必须在 archive_state 落登记行（本件新增的归档区）"

    assert e.store.restore_archived(n.id) is True
    after = e.store.get_node(n.id)
    assert "archived" not in after.tags, "还原必须清除 archived 标签"
    assert after.importance == pytest.approx(before.importance), (
        "还原必须恢复归档前 importance（pre_archive_importance），实读 %r" % (after.importance,))
    assert e.store.conn.execute(
        "SELECT COUNT(*) FROM archive_state WHERE node_id=?", (n.id,)).fetchone()[0] == 0


def test_restore_is_idempotent_and_only_for_archived_nodes(tmp_path):
    e = _engine(tmp_path)
    n = e.add_context("未归档", importance=0.4)
    assert e.store.restore_archived(n.id) is False, "未归档节点还原应返回 False"
    assert e.store.restore_archived("no_such_node") is False

    e.forget_advisor(stale_days=0, low_value=0.9)
    assert e.store.restore_archived(n.id) is True
    assert e.store.restore_archived(n.id) is False, "二次还原应返回 False（幂等）"


def test_restore_uses_explicit_importance_when_given(tmp_path):
    e = _engine(tmp_path)
    n = e.add_context("显式还原值", importance=0.15)
    e.forget_advisor(stale_days=0, low_value=0.2)
    assert e.store.restore_archived(n.id, importance=0.7) is True
    assert e.store.get_node(n.id).importance == pytest.approx(0.7)

    with pytest.raises(ValueError):
        e.store.restore_archived(n.id, importance=float("nan"))


# ---------------------------------------------------------------- ② 状态机闭合

def test_restored_node_can_be_archived_again(tmp_path):
    e = _engine(tmp_path)
    n = e.add_context("可循环", importance=0.15)
    assert e.forget_advisor(stale_days=0, low_value=0.2)["archived"] == 1
    e.store.restore_archived(n.id)
    out = e.forget_advisor(stale_days=0, low_value=0.2)
    assert out["archived"] == 1, (
        "还原后的节点若仍满足信号，下一次 forget_advisor 必须能再归档它——"
        "修前 archived 标签只加不清，它会永远被 continue 跳过")
    assert "archived" in e.store.get_node(n.id).tags


# ---------------------------------------------------------------- ③ 登记行卫生

def test_hard_delete_clears_archive_registry(tmp_path):
    e = _engine(tmp_path)
    n = e.add_context("归档后硬删", importance=0.15)
    e.forget_advisor(stale_days=0, low_value=0.2)
    assert n.id in _registry(e)
    assert e.store.delete_node(n.id) is True
    assert n.id not in _registry(e), "硬删必须清 archive_state，避免孤儿登记误导「仍可还原」"


def test_readd_without_archived_tag_clears_stale_registry(tmp_path):
    e = _engine(tmp_path)
    n = e.add_context("同 id 覆盖", importance=0.15)
    e.forget_advisor(stale_days=0, low_value=0.2)
    node = e.store.get_node(n.id)
    node.tags = [t for t in node.tags if t != "archived"]  # 新版本内容，不带归档态
    e.store.add_node(node)
    assert n.id not in _registry(e), (
        "同 id 覆盖写的是新内容版本——不带 archived 标签时必须清登记行"
        "（维持「有登记行 ⇔ 当前归档态」不变量）")


# ---------------------------------------------------------------- ④ untag 对称

def test_untag_node_symmetric_and_idempotent(tmp_path):
    e = _engine(tmp_path)
    nid = e.add_context("标签对称", importance=0.4).id
    e.store.tag_node(nid, "archived")
    assert "archived" in e.store.get_node(nid).tags
    assert e.store.untag_node(nid, "archived") is True
    assert "archived" not in e.store.get_node(nid).tags
    assert e.store.untag_node(nid, "archived") is False, "标签不存在时应返回 False（幂等）"
    assert e.store.untag_node("no_such_node", "x") is False


def test_untag_no_forget_is_audited(tmp_path):
    e = _engine(tmp_path)
    nid = e.add_context("保护撤销", importance=0.4).id
    e.store.tag_node(nid, "no_forget")
    assert e.store.untag_node(nid, "no_forget") is True
    assert _actions(e, "no_forget_untagged"), (
        "撤销 no_forget 必须落审计——修前该标签只加不清且打标零审计")


def test_forget_note_points_to_real_reverse_entry(tmp_path):
    e = _engine(tmp_path)
    n = e.add_context("note 措辞", importance=0.15)
    note = e.forget_advisor(stale_days=0, low_value=0.2)["note"]
    assert "restore_archived" in note, (
        "note 承诺可逆时必须点名真实的还原入口（#448）；实读 %r" % (note,))
    # 点名入口必须真的可用（不是只改措辞）：按 note 所指方法还原成功。
    assert e.store.restore_archived(n.id) is True
    assert math.isfinite(e.store.get_node(n.id).importance)
