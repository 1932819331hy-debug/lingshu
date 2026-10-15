# -*- coding: utf-8 -*-
"""#660 守卫：`close()` 清理 `_local.slot`，close 后不再复用已关闭连接／不再建假连接。

缺陷态（修复前 main，issue #660 复现实测）：
  `LayeredStore.close()` 只遍历 `_conns` 关槽 ＋ `_anchor = None`，**不清 `self._local.slot`**；
  而 `conn` property 的新鲜度判据是 `slot is None or slot.conn is None`——被 close 的槽
  `_closed=True` 但 `slot.conn` 仍指向已关闭的连接对象（非 None），判为「仍新鲜」⇒ 返回**死
  连接**，五个公开面全抛 `ProgrammingError: Cannot operate on a closed database.`。新线程
  走 `_connect(self._memory_dsn)` 建到**已随锚连接销毁**的 `:memory:` 库上 ⇒
  `OperationalError: no such table: nodes`（假连接）。同一缺陷第二面：`close_current_thread()`
  读的是 `getattr(self._local, "conn", None)`，而 `_connect` 实际设的是 `self._local.slot`
  ⇒ **该方法恒为 no-op**（槽仍在、连接仍可用）。

修法（落点 `lingshu/core/core.py`）：
  ① `conn` property 的新鲜度判据纳入 `slot._closed`，并在 `self._closed`（close 置位的终止态
     标志）时 **fail-closed 抛 `ProgrammingError`**，文案含「已关闭」与 `#660`；
  ② `close()` 置 `self._closed`、清 `self._local.slot`；
  ③ `close_current_thread()` 改读/清 `self._local.slot`（不再是 no-op；它**不**置终止态）。
  边界（如实标注 · 留设计者裁）：本件取「close 为生命周期终点、拒绝重用」的最小口径；
  **是否**提供显式 reopen（尤其 `:memory:` 后端重连）属产品口径，本件不定，留门。

组（回退即红）：
  G1 `close()` 后五个公开面各自抛「已关闭」类错误，且**错误文案可判**（含「已关闭」；
     不只断言异常类型）。
  G2 `close()` 后新线程不再拿到可用连接（抛「已关闭」/`no such table`，不得返回可用连接）。
  G3 `close_current_thread()` 后**本线程**连接确被释放（槽清空、旧连接不可用）。
  G4 正常生命周期（未 close）行为逐位不变（防误杀正对照）：读写照常、`close_current_thread()`
     后仍可继续用（按需重连）。

判别力（变异自证）：把 `close()` 的 `self._local.slot = None` 与 `self._closed = True` 摘掉、
并把 `conn` 的 `self._closed` / `slot._closed` 判据还原 ⇒ G1/G2/G3 必红；还原 ⇒ 全绿、
`git diff` 为空。

运行：python -X utf8 -m pytest tests/test_issue660_close_slot_guard.py -q
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from lingshu.core.core import LayeredStore, SpacetimeMemoryEngine

_CLOSED_WORDS = ("已关闭",)


def _assert_closed_semantics(exc):
    """判据按**文案**：必须是本件新增的可判「已关闭」文案。

    为什么不接受 `Cannot operate on a closed database.`——那是**缺陷态**下 sqlite 自己抛的
    英文文案（死连接被复用时的表象），把它算作通过会让守卫丧失判别力（本仓「同类型不同
    文案假绿」教训）。本件要求错误形态由「复用死连接」转为「显式『已关闭』拒绝」。
    """
    text = str(exc)
    assert any(w in text for w in _CLOSED_WORDS), (
        "#660：错误文案不可判（应为本件新增的『已关闭』语义，而非 sqlite 的 closed database）："
        "%s: %s" % (type(exc).__name__, text))


# --------------------------------------------------------- G1 五个公开面

def test_g1_public_faces_after_close_have_judgeable_closed_error():
    eng = SpacetimeMemoryEngine(":memory:")
    eng.add_context("hello world")
    eng.close()
    faces = [
        ("add_context", lambda: eng.add_context("after close")),
        ("recall", lambda: eng.recall("hello")),
        ("search_content", lambda: eng.search_content("hello")),
        ("get_stats", lambda: eng.get_stats()),
        ("self_check", lambda: eng.self_check()),
    ]
    for name, fn in faces:
        with pytest.raises((sqlite3.ProgrammingError, sqlite3.OperationalError)) as ei:
            fn()
        _assert_closed_semantics(ei.value)


# --------------------------------------------------------- G2 新线程

def test_g2_new_thread_after_close_gets_no_usable_connection():
    eng = SpacetimeMemoryEngine(":memory:")
    eng.add_context("hello world")
    eng.close()
    box = {}

    def worker():
        try:
            hits = eng.recall("hello")
            box["result"] = ("OK", hits)
        except Exception as exc:      # noqa: BLE001 —— 断言下面按文案判
            box["result"] = (type(exc).__name__, str(exc))

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=30)
    kind, detail = box["result"]
    assert kind != "OK", "#660：close() 后新线程仍拿到可用连接（假连接/死连接复用）"
    assert "已关闭" in detail, (
        "#660：新线程错误形态不可判（应为『已关闭』，而非 sqlite 的 no such table/closed "
        "database）：%s: %s" % (kind, detail))


# --------------------------------------------------------- G3 close_current_thread

def test_g3_close_current_thread_actually_releases_connection():
    store = LayeredStore(":memory:")
    first = store.conn                    # 建立本线程连接
    store.close_current_thread()
    assert getattr(store._local, "slot", None) is None, (
        "#660：close_current_thread() 未清 _local.slot（旧实现恒为 no-op）")
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")          # 旧连接确已被关闭
    # 且仍可继续使用（按需重连；本方法不是终止态）
    store.set_meta("k", "v")
    assert store.get_meta("k").get("k") == "v"
    store.close()


# --------------------------------------------------------- G4 防误杀正对照

def test_g4_normal_lifecycle_unchanged():
    store = LayeredStore(":memory:")
    store.set_meta("k", "v")
    assert store.get_meta("k").get("k") == "v"
    # close_current_thread 后本线程仍可继续读写（按需重连）
    store.close_current_thread()
    store.set_meta("k2", "v2")
    assert store.get_meta("k2").get("k2") == "v2"
    # 未 close 前不得抛「已关闭」
    assert store._closed is False
    store.close()

    eng = SpacetimeMemoryEngine(":memory:")
    n = eng.add_perception("正常写入")
    assert eng.store.get_node(n.id) is not None
    assert eng.recall("正常写入") is not None
    eng.close()
