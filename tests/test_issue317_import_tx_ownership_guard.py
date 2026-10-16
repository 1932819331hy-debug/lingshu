# -*- coding: utf-8 -*-
"""守卫 · 事务提交权归属（#317 根因面）· lingshu/core/core.py
============================================================================
缺陷（取证 = 修复前 main 的 `LayeredStore._tx()` / `_commit_locked()`）：

    `_tx()` 在 `nested=True` 分支（调用方已开事务）建 SAVEPOINT，却仍把
    `_tx_depth` 置 1；而 `_commit_locked()` 的判据是 `_tx_depth > 1` ⇒
    depth==1 时执行 `conn.commit()`。

    ⇒ 当调用方**在 `_tx` 之外已开事务**时，这次 commit 提交的是**整条事务**
      并销毁 `_tx` 自己的 SAVEPOINT，随后 `_tx` 的 `RELEASE SAVEPOINT` 必抛
      `OperationalError: no such savepoint`；更糟的是调用方**已失去回滚权**
      （其事务被越权提交）。

根因：`_tx_depth` 一个变量承担了两种语义——「SAVEPOINT 栈深度」与
「本帧是否有权提交」；二者在「调用方先开事务」这一路径上分叉。

修法：新增 `_tx_owns_txn` 归属位——最外层帧（`_tx_depth` 归 1 时）记录
「本帧是否自己开了真事务」（`nested=False` 为 True）；`_commit_locked()`
的提交条件由 `_tx_depth > 1` 改为 `_tx_depth > 1 or not _tx_owns_txn`。

与 #665 的关系：本件**不削弱** #665 的嵌套语义（depth>1 仍推迟提交），
只补「最外层帧非事务所有者」这一未覆盖面；#665 既有守卫
`tests/test_issue665_nested_tx_savepoint.py` 须同时全绿。

断言组（把修复抽掉 ⇒ 哪组红）：
  A [调用方 BEGIN]  调用方 raw `BEGIN IMMEDIATE` + 写 ⇒ 调 `import_all`：
                    · 不得抛 `no such savepoint`（修复前必抛）；
                    · `in_transaction` 必须**仍为 True**（修复前被越权提交成 False）；
                    · 调用方 `rollback()` 后，导入的行与调用方自己的行**都必须消失**。
  B [调用方 SAVEPOINT] 调用方 `SAVEPOINT` + 写 ⇒ 调 `import_all`：
                    · 不得抛 `no such savepoint`；
                    · 调用方 `ROLLBACK TO` 后库内**一行不剩**（修复前 savepoint 已被
                      销毁 ⇒ ROLLBACK TO 必抛）。
  C [单层不变]      无外部事务时行为与修复前**完全一致**：正常提交、异常回滚。
  D [归属位复位]    一笔事务跑完/抛异常后 `_tx_owns_txn` 必须归还默认 True，
                    否则后续独立事务会静默退化成「永不提交」。
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


@pytest.fixture
def database(tmp_path):
    with closing(SpacetimeMemoryEngine(str(tmp_path / "target.db"))) as engine:
        with closing(sqlite3.connect(tmp_path / "target.db", timeout=0.1)) as observer:
            assert not engine.store.conn.in_transaction
            yield engine, observer


def _node(nid, content="synthetic import"):
    return {"id": nid, "content": content, "modality": "text", "layer": "context",
            "spatial_coordinates": "{}", "temporal_coordinate": 0.0,
            "condition_space": "{}", "importance": 0.4, "confidence": 0.5,
            "created_at": 0.0, "tags": "[]"}


def _backup(tmp_path, **tables):
    path = tmp_path / "backup.json"
    path.write_text(json.dumps(tables), encoding="utf-8")
    return str(path)


def _contents(conn):
    return dict(conn.execute("SELECT id, content FROM nodes").fetchall())


def _pending_node(conn, nid, content="caller work"):
    conn.execute("INSERT INTO nodes (id, content) VALUES (?, ?)", (nid, content))


# --------------------------------------------------- A 调用方已有真事务（BEGIN）

def test_a_caller_begin_is_not_committed_by_import(database, tmp_path):
    """调用方的 `BEGIN IMMEDIATE` 事务不得被 `import_all` 越权提交。

    修复前：`_commit_locked()` 见 `_tx_depth == 1` 即 `conn.commit()`
    ⇒ 提交整条事务、销毁自己的 SAVEPOINT ⇒ `RELEASE` 抛
    `OperationalError: no such savepoint`，且 `in_transaction` 变 False。
    """
    engine, observer = database
    conn = engine.store.conn
    conn.execute("BEGIN IMMEDIATE")
    _pending_node(conn, "caller")

    engine.import_all(_backup(tmp_path, nodes=[_node("imported")]))

    assert conn.in_transaction, (
        "调用方事务被 import_all 越权提交（修复前症状：in_transaction 变 False）")
    assert _contents(observer) == {}, "导入的行不该在调用方提交前对独立连接可见"


def test_a_caller_rollback_still_discards_the_import(database, tmp_path):
    """调用方仍持有回滚权：`rollback()` 后导入的行与调用方自己的行都必须消失。"""
    engine, observer = database
    conn = engine.store.conn
    conn.execute("BEGIN IMMEDIATE")
    _pending_node(conn, "caller")

    engine.import_all(_backup(tmp_path, nodes=[_node("imported")]))

    conn.rollback()
    assert _contents(conn) == {}, "调用方回滚后仍有残留（回滚权已被越权提交吞掉）"
    assert _contents(observer) == {}


def test_a_caller_commit_publishes_both(database, tmp_path):
    """调用方提交 ⇒ 自己的行与导入的行一起落库。"""
    engine, observer = database
    conn = engine.store.conn
    conn.execute("BEGIN IMMEDIATE")
    _pending_node(conn, "caller")

    engine.import_all(_backup(tmp_path, nodes=[_node("imported")]))

    conn.commit()
    assert _contents(observer) == {"caller": "caller work",
                                  "imported": "synthetic import"}


# ------------------------------------------------ B 调用方已有 SAVEPOINT（非 BEGIN）

def test_b_caller_savepoint_survives_the_import(database, tmp_path):
    """调用方自己开的 SAVEPOINT 不得被 `import_all` 销毁。

    修复前：`_tx()` 判 `conn.in_transaction` 为真 ⇒ 走 nested 分支建自己的
    SAVEPOINT，但 `_commit_locked()` 仍 commit ⇒ 调用方的 SAVEPOINT 一并被
    销毁 ⇒ 调用方 `ROLLBACK TO SAVEPOINT` 抛 `no such savepoint`。
    """
    engine, observer = database
    conn = engine.store.conn
    conn.execute("SAVEPOINT caller_sp")
    _pending_node(conn, "caller")

    engine.import_all(_backup(tmp_path, nodes=[_node("imported")]))

    conn.execute("ROLLBACK TO SAVEPOINT caller_sp")
    conn.execute("RELEASE SAVEPOINT caller_sp")
    assert _contents(conn) == _contents(observer) == {}, (
        "调用方 SAVEPOINT 回滚后仍有残留（savepoint 已被销毁）")
    assert not conn.in_transaction


# ------------------------------------------------------- C 单层行为与修复前一致

def test_c_single_level_still_commits(database, tmp_path):
    """无外部事务：`import_all` 正常提交，事务结束。"""
    engine, observer = database
    conn = engine.store.conn

    result = engine.import_all(_backup(tmp_path, nodes=[_node("imported")]))

    assert not conn.in_transaction
    assert result["imported"]["nodes"] == 1
    assert _contents(observer) == {"imported": "synthetic import"}


def test_c_single_level_failure_rolls_back(database, tmp_path):
    """无外部事务：失败时整批回滚、释放写锁（与修复前一致）。

    失败注入用 `blindspots=[None]`：`M13_TABLES` 中 `blindspots` 排在 `nodes`
    **之后** ⇒ 异常发生时 `nodes` 已写过，回滚必须把它们撤掉（非「还没写就失败」）。
    `TypeError` 来自 `for k in r`（`None` 不可迭代）。
    """
    engine, observer = database
    path = _backup(tmp_path, nodes=[_node("imported")], blindspots=[None])

    with pytest.raises(TypeError):
        engine.import_all(path)

    assert not engine.store.conn.in_transaction
    assert _contents(observer) == {}
    observer.execute("BEGIN IMMEDIATE")   # 独立连接必须拿得到写锁
    observer.rollback()


# ------------------------------------------------------------- D 归属位必须复位

def test_d_ownership_flag_returns_to_default_after_success(database, tmp_path):
    """正常路径跑完后 `_tx_owns_txn` 必须归还默认 True。"""
    engine, _ = database
    conn = engine.store.conn
    conn.execute("BEGIN IMMEDIATE")
    engine.import_all(_backup(tmp_path, nodes=[_node("imported")]))
    conn.rollback()

    assert getattr(engine.store, "_tx_owns_txn", True) is True, (
        "归属位未复位：后续独立事务会静默退化成「永不提交」")

    # 归零判据：下一笔独立事务必须仍能真提交。
    node = engine.add_context("after D")
    assert dict(conn.execute("SELECT id, content FROM nodes").fetchall()) == {
        node.id: "after D"}


def test_d_ownership_flag_returns_to_default_after_exception(database, tmp_path):
    """异常路径也必须复位归属位（否则该连接后续写全部静默不提交）。"""
    engine, _ = database
    conn = engine.store.conn
    conn.execute("BEGIN IMMEDIATE")
    path = _backup(tmp_path, nodes=[_node("imported")], blindspots=[None])
    with pytest.raises(TypeError):
        engine.import_all(path)
    conn.rollback()

    assert getattr(engine.store, "_tx_owns_txn", True) is True

    node = engine.add_context("after D2")
    assert dict(conn.execute("SELECT id, content FROM nodes").fetchall()) == {
        node.id: "after D2"}
