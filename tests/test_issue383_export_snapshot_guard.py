# -*- coding: utf-8 -*-
"""#383 export_all 逐表各发一条自动提交的 SELECT，不在同一读事务里 —— 守卫

缺陷（公开 issue #383 · [core/M13 导出][备份一致性] · 判定「成立」）：
  `SpacetimeMemoryEngine.export_all` 依次 `SELECT * FROM <表>` 九张表，每条语句
  各自自动提交，中间没有 `BEGIN`、也不用 sqlite3 backup API。引擎本身开着并发写入
  方（auto-decay 线程、情境层 FIFO、多进程共享库的 WAL 部署）⇒ `nodes` 与 `edges`
  读到的可能是**两个不同时刻**的库：备份里出现指向「备份中不存在的节点」的悬挂边。
  源库 `verify_integrity` 为 True，备份恢复后 `integrity_ok=False`。

修法（最小 · 按 issue 建议 diff）：
  `export_all` 在本连接不在事务中时显式 `BEGIN`（**延迟读事务**，WAL 下整趟导出
  共用一个快照），读完 `commit`；已在事务中则不插手其边界（`own_tx` 判定）。

判据来源：公开 issue #383「让所有表在同一个读事务里读」（含
  `conn.in_transaction` 判定写法）。仓内无规定导出隔离口径的理论章节，属经验标定
  （本件 #383）。
边界（削掉的判别力）：`BEGIN` 只给**本连接**一个读快照，**不**阻止并发写者继续写
  源库（WAL/MVCC 的正常语义）；也不改 `os.replace` 落盘原子性（#330 面，未动）。

断言组（抽掉修复＝删掉 `BEGIN` ⇒ A/B 必红）：
  A 读完 nodes、开始读 edges 的瞬间另一连接做 delete_node + add_edge ⇒ 备份内
    **零悬挂边**，`import_all` 得 `integrity_ok=True`（旧实现 = 有悬挂边 + False）
  B 导出确实开了读事务：SQL trace 里出现 `BEGIN`（且导出后连接不残留事务）
  C 防误杀：无并发写时，导出内容与逐表结果一致、`tables`/`skipped_tables` 照旧

运行（仓根）：python -X utf8 -m pytest tests/test_issue383_export_snapshot_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _setup_two_engines(td):
    """A（导出者）+ B（并发写者）同库两连接。返回 (A, B, k_id, ctx_id, db)。"""
    db = os.path.join(td, "mem.db")
    A = SpacetimeMemoryEngine(db)
    k = A.add_perception("知识：服务器在机房 3", skip_dedup=True)
    ctx = A.add_context("刚才在聊机房", importance=0.4)
    A.add_edge(k.id, ctx.id)                      # 一条普通关联边
    B = SpacetimeMemoryEngine(db)
    return A, B, k.id, ctx.id, db


def test_a_并发写不产生悬挂边(monkeypatch):
    """A：读 edges 前另一连接删节点 + 加边 ⇒ 备份零悬挂边、导入 integrity_ok=True。"""
    td = tempfile.mkdtemp()
    A, B, k_id, ctx_id, db = _setup_two_engines(td)

    fired = []

    def hook(sql):
        # 在 export_all 读完 nodes、开始读 edges 的那一刻，另一连接执行普通写
        if sql.strip().startswith("SELECT * FROM edges") and not fired:
            fired.append(1)
            B.store.delete_node(ctx_id)
            B.add_edge(k_id, B.add_perception("新知识：机房 3 已迁移",
                                              skip_dedup=True).id)

    A.store.conn.set_trace_callback(hook)
    out = os.path.join(td, "bk.json")
    try:
        A.export_all(out)
    finally:
        A.store.conn.set_trace_callback(None)
    assert fired, "探针未触发（未在读 edges 时并发写）——用例失效"

    bk = json.load(open(out, encoding="utf-8"))
    ids = {n["id"] for n in bk["nodes"]}
    dangling = [(e["source_id"] in ids, e["target_id"] in ids) for e in bk["edges"]
                if not (e["source_id"] in ids and e["target_id"] in ids)]
    assert dangling == [], (
        "#383 备份跨时刻拼接，出现悬挂边 %r（旧实现逐表各自提交）" % (dangling,))

    C = SpacetimeMemoryEngine(os.path.join(td, "restore.db"))
    r = C.import_all(out)
    assert r.get("integrity_ok") is True, (
        "#383 备份恢复后 integrity_ok=False：%r" % (r,))
    assert r.get("dangling_rows") == 0, "#383 备份恢复仍有悬挂行：%r" % (r,)


def test_b_导出开读事务且事后不残留(monkeypatch):
    """B：SQL trace 里出现 `BEGIN`；导出结束后连接**不在事务中**。"""
    td = tempfile.mkdtemp()
    A, B, k_id, ctx_id, db = _setup_two_engines(td)

    seen = []
    A.store.conn.set_trace_callback(lambda sql: seen.append(sql))
    try:
        A.export_all(os.path.join(td, "bk.json"))
    finally:
        A.store.conn.set_trace_callback(None)

    up = [s.strip().upper() for s in seen]
    assert any(s == "BEGIN" for s in up), (
        "#383 导出未开读事务（trace 无 BEGIN）：%r" % (seen[:12],))
    assert not A.store.conn.in_transaction, "B 导出后连接残留未结事务"


def test_c_无并发写时导出内容照旧():
    """C：防误杀——无并发写时导出内容与库内一致，返回值口径不变。"""
    td = tempfile.mkdtemp()
    A, B, k_id, ctx_id, db = _setup_two_engines(td)
    out = os.path.join(td, "bk.json")
    r = A.export_all(out)

    bk = json.load(open(out, encoding="utf-8"))
    assert len(bk["nodes"]) == 2, "C nodes 数不符：%r" % (len(bk["nodes"]),)
    assert len(bk["edges"]) == 1, "C edges 数不符：%r" % (len(bk["edges"]),)
    assert r["exported_nodes"] == 2, "C exported_nodes 口径变了：%r" % (r,)
    assert "skipped_tables" in r and isinstance(r["skipped_tables"], list)
    # 库里真实行数与备份一致
    assert len(A.store.conn.execute("SELECT id FROM nodes").fetchall()) == 2
    assert not A.store.conn.in_transaction, "C 导出后连接残留未结事务"
