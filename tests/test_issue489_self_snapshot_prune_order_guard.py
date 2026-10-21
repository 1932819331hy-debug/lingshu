# -*- coding: utf-8 -*-
"""#489 自我快照保留序按**插入序**（rowid），不再按墙钟 `created_at` —— 守卫

缺陷（lingshu issue #489，判定成立，两轮复核）：`LayeredStore.prune_self_snapshots`
旧实现 `ORDER BY created_at DESC` 后 `doomed = ids[keep:]`，而快照的 `created_at`
取 `time.time()`（**不单调**）。只要库里存在任何 `created_at` **大于当前时间**的
SELF 快照（NTP 步进校正 / 虚拟机快照回滚 / 无 RTC 开机 / `import_all` 导入时钟偏快
机器的备份），`update_self` **本次刚插入**的快照就是全层最小 ⇒ 紧随其后的本次淘汰
把它**当场删除**：

  · 带 `link_to_node_id`：删完后 `update_self` 用该 id `add_edge` ⇒
    `IntegrityError: FOREIGN KEY constraint failed`（**半截副作用**：`add_node` 与
    `prune` 各自已 commit，内存 `self_model` 已更新而库内快照已删——调用方看到异常，
    重启后回到旧值）；
  · 不带 link：静默——`update_self` 正常返回，库里却没有这次快照。
SUB 侧另有同根路径：伪造 `created_at` 在未来的 SELF 行恒排最前，真实历史被整批挤出。

修法（贴同库既有先例）：排序键由 `created_at DESC` 改 **`rowid DESC`**——与情境层
`enforce_context_cap` / `get_recent_context`（#259）**同根同法**；`_load_self_snapshot`
的 SELF 层回退腿同步改 `rowid DESC`（与 prune 同键，否则「保留的」与「读回的」不是
同一批）。`update_self` 的 `add_node → prune → add_edge` 顺序不动。

断言组（把排序键改回 `created_at DESC` 即红）：
  G1 时钟回拨 + `link_to_node_id`：不抛 `IntegrityError`，本次快照落库，且边建成。
  G2 时钟回拨 + 不带 link：本次快照**不再静默自删**（缺陷的静默腿）。
  G3 SUB 伪造未来时间戳行：本次真实快照仍幸存（报告臂 2）。
  G4 正常路径：仍按插入序 FIFO 保留最新 `keep` 条（防修法改变正常语义）。
  G5 定序判据本身：`created_at` 与插入序**相反**时，淘汰的是**最早插入**的
     （若仍按 `created_at` 淘汰，淘汰集正好反过来 ⇒ 本腿红）。
  G6 `_load_self_snapshot` 回退腿与 prune 同键：回拨后读回的是**刚写入**那份。
  G7 防误杀：`keep` 边界与返回删除条数口径不变（`len(ids) <= keep` ⇒ 0）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue489_self_snapshot_prune_order_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    ConditionSpace, MemoryLayer, Role, SpacetimeMemoryEngine, STNode,
)

_KEEP = 5
#: 模拟「时钟快一天时写入」的行（与报告同法：既有行 created_at 推后 86400 秒）。
_ROLLBACK = 86400.0
#: 伪造的「未来」时间戳（2100-01-01），报告臂 2 用值。
_FUTURE = 4102444800.0


class _Engine:
    """文件库引擎的上下文管理器（退出时 `close()`——Windows 下不关连接会让
    `TemporaryDirectory` 清理抛 `PermissionError`）。"""

    def __init__(self, tmp, name="m.db", role=None):
        self.path = os.path.join(tmp, name)
        self.role = role

    def __enter__(self):
        e = SpacetimeMemoryEngine(
            db_path=self.path, **({"role": self.role} if self.role else {}))
        e.SELF_SNAPSHOT_MAX = _KEEP
        self._e = e
        return e

    def __exit__(self, *exc):
        self._e.close()
        return False


def _snapshot_states(e):
    """库内 SELF 层快照的 `state_description`（按 rowid 升序＝插入序）。"""
    c = e.store.conn.cursor()
    c.execute("SELECT content FROM nodes WHERE layer='self' "
              "AND tags LIKE '%self_snapshot%' ORDER BY rowid ASC")
    return [json.loads(r[0]).get("state_description") for r in c.fetchall()]


def _snapshot_ids(e):
    c = e.store.conn.cursor()
    c.execute("SELECT id FROM nodes WHERE layer='self' ORDER BY rowid ASC")
    return [r[0] for r in c.fetchall()]


def _rollback_clock(e):
    """把既有 SELF 快照的 created_at 推后一天（等价「时钟快一天时写入」）。"""
    e.store.conn.execute(
        "UPDATE nodes SET created_at = created_at + ? WHERE layer='self'",
        (_ROLLBACK,))
    e.store.conn.commit()


def _forge_future_rows(e, n):
    cs = ConditionSpace("x", "y", (0, 1), "z")
    for i in range(n):
        e.store.add_node(STNode(
            id="self_forged_%d" % i,
            content='{"state_description": "forged"}',
            modality="self_state", spatial_coordinates={},
            temporal_coordinate=0, condition_space=cs,
            layer=MemoryLayer.SELF, tags=["self_snapshot"],
            created_at=_FUTURE))


# ---------------------------------------------------------------- G1 / G2

def test_g1_clock_rollback_with_link_no_fk_error():
    """G1：时钟回拨后 `update_self(..., link_to_node_id=...)` 不得抛外键错误。

    旧缺陷：新快照被自己那次 prune 删掉 ⇒ `add_edge` 抛
    `IntegrityError: FOREIGN KEY constraint failed`（报告臂 1 逐字复现）。
    """
    with tempfile.TemporaryDirectory() as tmp, _Engine(tmp) as e:
        exp = e.add_perception("今天完成了一次实验", importance=0.6)
        for i in range(_KEEP):
            e.update_self({"state_description": "fast-clock state %d" % i})
        _rollback_clock(e)

        e.update_self({"state_description": "REAL state"}, link_to_node_id=exp.id)

        states = _snapshot_states(e)
        assert "REAL state" in states, (
            "G1 本次快照未落库（旧缺陷：被同一次 prune 自删）—— SELF 层：%r" % (states,))
        c = e.store.conn.cursor()
        c.execute("SELECT COUNT(*) FROM edges WHERE target_id=? AND source_id LIKE 'self_%'",
                  (exp.id,))
        assert c.fetchone()[0] == 1, "G1 快照→记忆节点的边未建成"
        assert e.self_model.state_description == "REAL state", "G1 内存自我模型不符"


def test_g2_clock_rollback_without_link_not_silently_dropped():
    """G2：时钟回拨后不带 link 的 `update_self` 不得静默丢弃本次快照。

    旧缺陷：`update_self` 正常返回（无异常可观测），库里却已无本次快照——
    「写一条丢一条，且无任何信号」。
    """
    with tempfile.TemporaryDirectory() as tmp, _Engine(tmp) as e:
        for i in range(_KEEP):
            e.update_self({"state_description": "fast-clock state %d" % i})
        _rollback_clock(e)

        assert e.update_self({"state_description": "REAL silent"}) is None

        states = _snapshot_states(e)
        assert "REAL silent" in states, (
            "G2 本次快照被静默自删（旧缺陷）—— SELF 层：%r" % (states,))
        assert len(states) == _KEEP, "G2 保留条数应恰为 keep，实得 %d" % len(states)


# ---------------------------------------------------------------- G3

def test_g3_sub_forged_future_rows_do_not_evict_new_snapshot():
    """G3：SUB 侧伪造未来时间戳行后，本次真实快照仍须幸存（报告臂 2）。

    旧缺陷：伪造行 `created_at=_FUTURE` 恒排最前，真实历史与**本次新行**一并被挤出。
    说明（如实标注）：被淘汰的**旧**真实快照是插入序最旧者，属正确 FIFO 语义；
    本腿只主张「本次新写入的快照不得被自删」。
    """
    with tempfile.TemporaryDirectory() as tmp, _Engine(tmp, "m2.db", role=Role.SUB) as e:
        for i in range(3):
            e.update_self({"state_description": "genuine %d" % i})
        _forge_future_rows(e, _KEEP)

        e.update_self({"state_description": "genuine 3"})

        states = _snapshot_states(e)
        assert "genuine 3" in states, (
            "G3 本次真实快照被伪造未来行挤出（旧缺陷）—— SELF 层：%r" % (states,))


# ---------------------------------------------------------------- G4 / G7

def test_g4_normal_path_still_keeps_newest_by_insertion_order():
    """G4（防误杀·正常路径）：无时钟偏差时仍保留**最新** `keep` 条（FIFO 语义不变）。"""
    with tempfile.TemporaryDirectory() as tmp, _Engine(tmp, "m3.db") as e:
        for i in range(8):
            e.update_self({"state_description": "n %d" % i})
        states = _snapshot_states(e)
        assert states == ["n %d" % i for i in range(3, 8)], (
            "G4 正常路径保留集不符（应留最新 5 条）—— 实得 %r" % (states,))


def test_g7_prune_boundary_and_return_count_unchanged():
    """G7（防误杀·边界）：`len(ids) <= keep` ⇒ 返回 0 且一条不删；超限返回删除条数。"""
    with tempfile.TemporaryDirectory() as tmp, _Engine(tmp, "m4.db") as e:
        for i in range(_KEEP):
            e.update_self({"state_description": "n %d" % i})
        assert e.store.prune_self_snapshots(_KEEP) == 0, "G7 未超限时不应删"
        assert len(_snapshot_ids(e)) == _KEEP

        for i in range(_KEEP, _KEEP + 3):
            e.update_self({"state_description": "n %d" % i})
        assert len(_snapshot_ids(e)) == _KEEP, "G7 update_self 内联 prune 后条数不符"
        # 绕过 update_self 直插两条（模拟既有库超限 / 不调内联 prune 的调用方），
        # 再手动 prune：恰删 2 条，且删的是**插入序最旧**的两条。
        cs = ConditionSpace("x", "y", (0, 1), "z")
        for k in range(2):
            e.store.add_node(STNode(
                id="self_extra_%d" % k,
                content=json.dumps({"state_description": "extra %d" % k}),
                modality="self_state", spatial_coordinates={},
                temporal_coordinate=0, condition_space=cs,
                layer=MemoryLayer.SELF, tags=["self_snapshot"]))
        before = _snapshot_ids(e)
        assert len(before) == _KEEP + 2
        n = e.store.prune_self_snapshots(_KEEP)
        assert n == 2, "G7 返回删除条数应为 2，实得 %d" % n
        assert _snapshot_ids(e) == before[2:], "G7 删除的应是最早插入的两条"


# ---------------------------------------------------------------- G5

def test_g5_created_at_order_inverted_still_evicts_earliest_inserted():
    """G5（定序判据本身）：`created_at` 与插入序**相反**时，淘汰的是**最早插入**的。

    构造：第 1 条给「未来」时间戳、第 2..6 条给递减时间戳（插入序与 created_at
    降序正好相反）。旧实现按 `created_at DESC` ⇒ 保留集＝**最早插入的 5 条**、
    淘汰**最后插入**的那条；修后按 `rowid DESC` ⇒ 淘汰**第 1 条**（插入序最旧）。
    """
    with tempfile.TemporaryDirectory() as tmp, _Engine(tmp, "m5.db") as e:
        cs = ConditionSpace("x", "y", (0, 1), "z")
        # 插入序 0..5；created_at 严格递减（插入序 0 最大）。
        for i in range(_KEEP + 1):
            e.store.add_node(STNode(
                id="self_ord_%d" % i,
                content=json.dumps({"state_description": "ord %d" % i}),
                modality="self_state", spatial_coordinates={},
                temporal_coordinate=0, condition_space=cs,
                layer=MemoryLayer.SELF, tags=["self_snapshot"],
                created_at=_FUTURE - i))

        assert e.store.prune_self_snapshots(_KEEP) == 1
        ids = _snapshot_ids(e)
        assert "self_ord_0" not in ids, (
            "G5 插入序最旧的那条未被淘汰（仍在按 created_at 定序）—— 实得 %r" % (ids,))
        assert ids == ["self_ord_%d" % i for i in range(1, _KEEP + 1)], (
            "G5 保留集不符（应为插入序最新的 keep 条）—— 实得 %r" % (ids,))


# ---------------------------------------------------------------- G6

def test_g6_load_fallback_reads_newest_by_insertion_order():
    """G6：`_load_self_snapshot` 的 SELF 层回退腿与 prune **同键**（rowid）。

    旧缺陷：回退腿按 `created_at DESC` ⇒ 回拨后「最新」指向**旧**快照，与
    prune 保留的那批不是同一批（读回的自我状态滞后于库内实际保留集）。
    """
    with tempfile.TemporaryDirectory() as tmp, _Engine(tmp, "m6.db") as e:
        for i in range(_KEEP):
            e.update_self({"state_description": "fast-clock state %d" % i})
        _rollback_clock(e)
        e.update_self({"state_description": "REAL newest"})

        # 清掉权威键（engine_meta），强制走 SELF 层回退腿。
        e.store.conn.execute("DELETE FROM engine_meta WHERE key=?",
                             (e.SELF_MODEL_META_KEY,))
        e.store.conn.commit()

        data, source = e._load_self_snapshot()
        assert source == "self_snapshot_node", "G6 未走回退腿：%r" % (source,)
        assert data.get("state_description") == "REAL newest", (
            "G6 回退腿读到的不是最新插入的快照（旧缺陷：按 created_at 读到旧值）"
            "—— 实得 %r" % (data.get("state_description"),))
