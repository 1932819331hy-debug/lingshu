# -*- coding: utf-8 -*-
"""#344 `tag_node` / `protect_node` 对 `nodes.tags` 的读改写必须原子 —— 守卫

缺陷（lingshu issue #344，判定成立）：`LayeredStore.tag_node` / `protect_node`
对 `nodes.tags` 做「SELECT 旧值 → Python 里改 → 整列 UPDATE 覆盖写」，读与写**不在
同一事务**、也没有乐观锁。两个连接（等价于两个进程，官方部署形态＝3×MCP server＋
循环＋心跳共享同一文件库）各自读到同一份旧 tags，后写者用旧 tags 覆盖掉先写者的
改动 —— **0 异常，标签静默丢失**。`protect_node` 更甚：`protections` 表里已登记该
节点，`no_forget` 标签却被并发的 `tag_node` 抹掉 ⇒ `forget_advisor` 只看标签，
把受保护节点照样归档降权（标签与 protections 表不同源）。

修法（issue 修法建议「把标签改动收进单条 SQL（同一事务内读改写，或
`BEGIN IMMEDIATE`）」取后者）：读 tags 之前先 `BEGIN IMMEDIATE` 取 RESERVED 写锁，
把「读 → 改 → 整列覆盖写」收进同一个 DB 级事务（`_lock` 是进程内锁，跨进程无效）。
不改 `nodes.tags` 的整列覆盖语义，不改 `protections` 表结构。

断言组（抽掉 `BEGIN IMMEDIATE` 即红）：
  J1 确定性复现（issue [A]）：A 读到旧 tags 后 B 完成 `protect_node`，A 再
     `tag_node` 写回 —— `no_forget` 必须仍在 tags 里（旧缺陷：被抹掉），且
     `duplicate` 与 `protections` 行都不得丢。
  J2 并发留存（issue [B] 同形）：2 连接 × N 次 `tag_node`，全部标签必须留存
     （旧缺陷：只剩一半，异常 0 次）。
  J3 幂等与事务收尾不回归：重复 `tag_node` 同一标签不重复写；调用后连接不在
     事务中（不把写锁泄漏给后续语句）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue344_tags_read_modify_write_atomic_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import LayeredStore, SpacetimeMemoryEngine  # noqa: E402


def _tags_of(db: str, node_id: str):
    st = LayeredStore(db)
    row = st.conn.execute("SELECT tags FROM nodes WHERE id=?", (node_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else []


def test_j1_protect_then_tag_does_not_lose_no_forget(tmp_path):
    """J1：A 读到旧 tags 后 B 保护、A 写回 —— `no_forget` 不得被覆盖丢失。

    这是 issue [A] 的确定性复现，但把「另一进程恰好在此刻提交」放在**另一线程**
    （单线程里让 A 等 B 完成会与 DB 级写锁互等，属人为死锁，不构成本缺陷的判据）：
    A 线程读到旧 tags 后暂停一个窗口；B 线程在窗口内对同一节点 `protect_node`。
    旧实现（无事务）B 在窗口内完成、A 用旧 tags 覆盖写回 ⇒ `no_forget` 消失；
    修复后 B 被 A 的 `BEGIN IMMEDIATE` 写锁挡到 A 提交之后，读到的是新 tags。
    """
    db = str(tmp_path / "j1.db")
    eng = SpacetimeMemoryEngine(db)
    nid = eng.add_context("需要长期保护的情境记忆：设计者口述的第一条约束",
                          importance=0.15).id
    eng.close()

    a = LayeredStore(db)   # 进程 A 的连接（如 M5 去重给节点打 duplicate）
    b = LayeredStore(db)   # 进程 B 的连接（如 LongTermMemoryGate 给同一节点 protect）

    orig_get = a.get_node
    read_done = threading.Event()
    errs = []

    def get_node_with_window(node_id):
        node = orig_get(node_id)      # A 已读到旧 tags
        read_done.set()
        time.sleep(0.4)               # 「读 → 写」之间的窗口
        return node

    a.get_node = get_node_with_window

    def other_connection():
        read_done.wait(5.0)
        try:
            b.protect_node(nid, "LongTermMemoryGate:snapshot")
        except Exception as ex:  # pragma: no cover
            errs.append("%s: %s" % (type(ex).__name__, ex))

    t = threading.Thread(target=other_connection)
    t.start()
    a.tag_node(nid, "duplicate")      # A 用旧 tags 覆盖写回
    t.join(30.0)

    assert not errs, "protect_node 抛异常：%r" % (errs,)
    tags = _tags_of(db, nid)
    st = LayeredStore(db)
    prot = [r[0] for r in st.conn.execute("SELECT node_id FROM protections")]
    assert "no_forget" in tags, (
        "protect_node 写下的 no_forget 被并发 tag_node 的整列覆盖抹掉（#344）：tags=%r"
        % (tags,))
    assert "duplicate" in tags, "A 自己的标签反而丢了：tags=%r" % (tags,)
    assert nid in prot, "protections 表登记丢失：%r" % (prot,)


def test_j2_concurrent_tag_node_keeps_every_tag(tmp_path):
    """J2：2 连接 × N 次 `tag_node` 全部留存（旧缺陷：只剩一半，0 异常）。"""
    n = 60
    db = str(tmp_path / "j2.db")
    eng = SpacetimeMemoryEngine(db)
    nid = eng.add_perception("并发打标签目标节点", skip_dedup=True).id
    eng.close()

    stores = [LayeredStore(db), LayeredStore(db)]
    for st in stores:
        orig = st.get_node

        def slow(node_id, _orig=orig):
            node = _orig(node_id)
            time.sleep(0.002)     # 拉宽「读到旧值 → 写回」的窗口
            return node

        st.get_node = slow

    errs = []

    def worker(st, prefix):
        try:
            for i in range(n):
                st.tag_node(nid, "%s%03d" % (prefix, i))
        except Exception as ex:  # pragma: no cover
            errs.append("%s: %s" % (type(ex).__name__, ex))

    ts = [threading.Thread(target=worker, args=(stores[0], "a")),
          threading.Thread(target=worker, args=(stores[1], "b"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert not errs, "并发 tag_node 抛异常：%r" % (errs,)
    tags = _tags_of(db, nid)
    mine = [t for t in tags if t[:1] in ("a", "b") and t[1:].isdigit()]
    assert len(mine) == 2 * n, (
        "2 连接 × %d 次 tag_node 期望留存 %d 个标签，实际 %d 个（读改写非原子，#344）"
        % (n, 2 * n, len(mine)))


def test_j3_idempotent_and_no_dangling_transaction(tmp_path):
    """J3：幂等不变；调用收尾后连接不留在事务中（写锁不泄漏）。"""
    db = str(tmp_path / "j3.db")
    st = LayeredStore(db)
    nid = SpacetimeMemoryEngine(db).add_perception("幂等探针", skip_dedup=True).id
    st.tag_node(nid, "dup")
    st.tag_node(nid, "dup")
    assert _tags_of(db, nid).count("dup") == 1, "tag_node 不再幂等"
    assert not st.conn.in_transaction, (
        "tag_node 收尾后连接仍留在事务中（BEGIN IMMEDIATE 未收尾，写锁泄漏）")
