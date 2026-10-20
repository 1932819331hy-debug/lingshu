# -*- coding: utf-8 -*-
"""#775 并发守卫：访问计数失败**不再静默** ＋ `_tx_depth` 线程本地化

两条缺陷（lingshu issue #775，独立复核判定成立）：
  ① `increment_access_many` 自开 `sqlite3.connect(self.db_path, timeout=0)` 绕过
     `_connect` 的锁等待，并把 `OperationalError: database is locked` 用
     `except sqlite3.OperationalError: pass` **整个吞掉** ⇒ 外部写者持锁期间该批
     访问计数**静默丢失**（该计数参与提权/降权、遗忘信号与检索排序次键），
     调用方与运维都看不到。
  ② `_tx_depth` 记在**实例**上而连接是**线程级**：线程 A 把深度抬到 2 并持真写锁
     后，线程 B 读到**别人的**深度 ⇒ `_structure_event_transaction` 只发 `SAVEPOINT`
     、**不发 `BEGIN IMMEDIATE`** ⇒ 结构事件单元全程不持写锁，#36/#517 的
     「单元期间持写锁以保护快照」保证在跨线程下失效。

本件修法（本笔）：
  ① **只**把「被吞掉的失败」改为「被上报的失败」（`access_count_write_failures`
     计数 ＋ `last_access_count_error` 原文 ＋ `warnings.warn`），**不改锁等待取向**
     ——`timeout=0` 是刻意的非阻塞设计（`ac6c0ca` 的 `increment_access` docstring
     逐字：「尽力而为：外部写锁持有时立即放弃，不阻塞检索主流程」），改成
     `_connect` 的 `busy_timeout=30000` 会把 `recall` 的 touch 路径拖住最多 30 秒，
     属语义变更。「要不要为访问计数等锁」列为取向题（见报告），不在本件自定。
  ② `_tx_depth` / `_tx_owns_txn` 改由 `property` 转发到 `self._local`
     （`threading.local()`，与线程级连接同域）⇒ 跨线程各看各的深度。

断言组与判别力（★回退 `lingshu/core/core.py` ⇒ G1/G2/G3/G7 必红；G4/G5 是**正对照**，
修前修后逐位相同、故意不随回退变红；G6 是**回归钉**，防后续把非阻塞改成阻塞）：
  · G1 缺陷①：外部持写锁时失败**可观测**（计数 + 原文 + 告警），不是静默；
  · G2 缺陷①阴性：无竞争下增量逐位不变（+3）、且不产生任何 #775 告警/计数；
  · G3 缺陷①边界：持锁释放后计数照常记上（旁路连接未被破坏）；
  · G4 缺陷②跨线程**判别读数**：B 线程在单元内**确实持写锁**且发出 `BEGIN IMMEDIATE`；
  · G5 缺陷②跨线程**深度读数**：A 线程 `_tx` 嵌套中，B 线程读到**本线程**深度 0；
  · G6 缺陷②正对照：单线程嵌套的事务序列修前修后**逐位相同**；
  · G7 阳性对照：`update_node_confidence` / `tag_node` / `add_edge` / `delete_node`
     四条公开写路径读数逐位不变；
  · G8 静态回归钉：旁路连接仍是 `timeout=0`（非阻塞语义未被改成 30s 等锁）。

★并发类断言一律用「**锁是否被持有**」「深度读数」这类**状态**观测，**不**写墙钟上界
断言——`pytest-xdist` 并行下计时类断言会因资源争用假红。测试内的 1.5s 只是让 A 线程
释放写锁的**夹具窗口**（不是断言）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue775_concurrency_guard.py -q
"""
from __future__ import annotations

import ast
import os
import re
import sqlite3
import sys
import threading
import warnings

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import (LayeredStore, STNode, STEdge,  # noqa: E402
                               MemoryLayer, EdgeType, ConditionSpace, Role)

CORE_PY = os.path.join(REPO, "lingshu", "core", "core.py")
CS = ConditionSpace("lab", "fixture", (0.0, 1e12), "test")

#: 保存点名含 uuid 十六进制 ⇒ 归一后再比对事务序列。
_SP_RE = re.compile(r"(SAVEPOINT|RELEASE SAVEPOINT|ROLLBACK TO SAVEPOINT) \S+")

#: A 线程持写锁的**夹具窗口**（秒）。只为保证「B 进单元时 A 仍在事务内」，
#: 不作断言——预修版 B 不阻塞（SAVEPOINT），修后 B 在 BEGIN IMMEDIATE 上等到
#: A 释放为止（有界，busy_timeout 之下）。
_A_HOLD_WINDOW = 1.5


def _norm(sql: str) -> str:
    return _SP_RE.sub(lambda m: m.group(1) + " _sp_", sql).strip()


def _store(path) -> LayeredStore:
    return LayeredStore(str(path), role=Role.PRIMARY)


def _seed(store: LayeredStore, nids) -> None:
    """直接插行：只关心 `access_count` 一列，不走公开写 API（避免夹具自身有语义）。"""
    c = store.conn.cursor()
    for nid in nids:
        c.execute(
            "INSERT OR REPLACE INTO nodes (id, content, modality,"
            " spatial_coordinates, temporal_coordinate, condition_space, importance,"
            " confidence, layer, access_count, last_access, created_at, tags,"
            " semantic_coordinates, state_attributes, entity_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, "c", "text", "{}", 0.0, "{}", 0.5, 0.5, "knowledge",
             0, None, 0.0, "[]", "{}", "{}", None))
    store.conn.commit()


def _acc(store: LayeredStore, nid: str) -> int:
    return store.conn.execute(
        "SELECT access_count FROM nodes WHERE id=?", (nid,)).fetchone()[0]


def _lock_free(db: str) -> bool:
    """独立连接能否取得写锁 ⇒ True = 当下**无人**持写锁（状态观测，非计时）。"""
    c = sqlite3.connect(db, timeout=0, isolation_level=None)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


def _capture(store: LayeredStore) -> list:
    """给**本线程**连接挂 SQL trace，返回归一化语句列表。"""
    buf: list = []
    store.conn.set_trace_callback(lambda s: buf.append(_norm(s)))
    return buf


# =============================================================================
# 缺陷① 静默丢计数
# =============================================================================

def test_g1_write_contention_failure_is_reported_not_swallowed(tmp_path):
    """G1：外部写者持锁 ⇒ 该批计数失败必须**可观测**（旧实现：`pass`，零读数）。

    预修版读数：`access_count_write_failures` 属性根本不存在（`AttributeError`）、
    也没有任何告警 ⇒ 本用例红。
    """
    db = str(tmp_path / "g1.db")
    st = _store(db)
    _seed(st, ["n0"])
    ext = sqlite3.connect(db, timeout=0, isolation_level=None)
    ext.execute("BEGIN IMMEDIATE")
    try:
        assert not _lock_free(db), "夹具自检：外部连接应已持有写锁"
        with pytest.warns(RuntimeWarning, match="#775"):
            st.increment_access_many(["n0"] * 5)
    finally:
        ext.execute("ROLLBACK")
        ext.close()

    assert getattr(st, "access_count_write_failures", 0) == 1, (
        "#775：写竞争下访问计数失败必须计入可读读数（旧实现静默 `pass`）")
    assert "database is locked" in (st.last_access_count_error or ""), (
        "#775：末次失败原文须留存可诊断（实测 %r）" % (st.last_access_count_error,))
    st.close()


def test_g2_no_contention_increment_is_bit_identical(tmp_path):
    """G2（阴性对照）：无竞争下增量逐位不变（+3），且**不**产生失败读数/告警。"""
    db = str(tmp_path / "g2.db")
    st = _store(db)
    _seed(st, ["n0"])
    before = _acc(st, "n0")
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        st.increment_access_many(["n0"] * 3)
    after = _acc(st, "n0")
    assert after - before == 3, "无竞争增量必须逐位不变（实测 %+d）" % (after - before)
    assert getattr(st, "access_count_write_failures", 0) == 0, (
        "无竞争不得记失败")
    assert not [w for w in rec if "#775" in str(w.message)], (
        "无竞争不得产生 #775 告警：%r" % ([str(w.message) for w in rec],))
    st.close()


def test_g3_count_is_recorded_after_lock_released(tmp_path):
    """G3（边界）：释放写锁后同一入口照常记上（旁路连接未被上报改动破坏）。"""
    db = str(tmp_path / "g3.db")
    st = _store(db)
    _seed(st, ["n0"])
    ext = sqlite3.connect(db, timeout=0, isolation_level=None)
    ext.execute("BEGIN IMMEDIATE")
    try:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            st.increment_access_many(["n0"] * 2)          # 竞争期：放弃该批
    finally:
        ext.execute("ROLLBACK")
        ext.close()
    before = _acc(st, "n0")
    st.increment_access_many(["n0"] * 5)
    assert _acc(st, "n0") - before == 5, "释放写锁后必须照常计数"
    st.close()


# =============================================================================
# 缺陷② 实例级 _tx_depth ＋ 线程级连接
# =============================================================================

def test_g4_cross_thread_unit_holds_write_lock(tmp_path):
    """G4（判别读数）：A 线程嵌套 `_tx` 持锁时，B 线程进结构事件单元**须持写锁**。

    预修版：B 读到**实例** `_tx_depth=2` ⇒ 只发 `SAVEPOINT`（不开事务、不取写锁）
    ⇒ 单元内第三方探测**可取得写锁** ⇒ 本用例红。
    """
    db = str(tmp_path / "g4.db")
    st = _store(db)
    _seed(st, ["n0"])

    a_inside = threading.Event()
    release_a = threading.Event()
    out: dict = {}
    errors: list = []

    def thread_a():
        try:
            with st._tx():
                with st._tx():
                    out["a_depth"] = st._tx_depth
                    a_inside.set()
                    release_a.wait(_A_HOLD_WINDOW)
            out["a_depth_after"] = st._tx_depth
        except BaseException as exc:                      # noqa: BLE001
            errors.append("A: %r" % (exc,))
            a_inside.set()

    def thread_b():
        try:
            assert a_inside.wait(10.0), "夹具：A 未在窗口内进入事务"
            out["b_sees_depth"] = st._tx_depth
            buf = _capture(st)
            with st._structure_event_transaction():
                out["b_sql_inside"] = list(buf)
                out["b_in_transaction"] = st.conn.in_transaction
                out["b_lock_free"] = _lock_free(db)       # False ⇒ B 持写锁
                release_a.set()
            st.conn.set_trace_callback(None)
            out["b_sql"] = list(buf)
        except BaseException as exc:                      # noqa: BLE001
            errors.append("B: %r" % (exc,))
            release_a.set()

    ta = threading.Thread(target=thread_a, name="A")
    tb = threading.Thread(target=thread_b, name="B")
    ta.start()
    tb.start()
    ta.join(30)
    tb.join(30)

    assert not errors, "线程内异常：%r" % (errors,)
    assert out.get("a_depth") == 2, "夹具：A 应处于嵌套两层（实测 %r）" % (out.get("a_depth"),)
    assert out.get("b_lock_free") is False, (
        "#775②：B 的结构事件单元必须**持有写锁**（单元内第三方探测可取得写锁 ⇒ "
        "单元全程无锁，B 的 SQL=%r）" % (out.get("b_sql_inside"),))
    assert "BEGIN IMMEDIATE" in (out.get("b_sql_inside") or []), (
        "#775②：B 须发出 BEGIN IMMEDIATE（实测 %r）" % (out.get("b_sql_inside"),))
    assert out.get("b_sql") == ["BEGIN IMMEDIATE", "COMMIT"], (
        "#775②：B 的单元事务序列应为 BEGIN IMMEDIATE → COMMIT（实测 %r）"
        % (out.get("b_sql"),))
    st.close()


def test_g5_tx_depth_is_thread_local(tmp_path):
    """G5（深度读数）：A 线程嵌套 `_tx` 中，B 线程读到的是**本线程**深度 0。

    预修版：`_tx_depth` 是实例属性 ⇒ B 读到 2（别人的深度）⇒ 本用例红。
    本用例只读属性、不碰 DB，故 A 用「B 已读毕」握手后立即退出（无写锁互等）。
    """
    db = str(tmp_path / "g5.db")
    st = _store(db)
    _seed(st, ["n0"])

    a_inside = threading.Event()
    b_read = threading.Event()
    out: dict = {}

    def thread_a():
        with st._tx():
            with st._tx():
                out["a_depth"] = st._tx_depth
                out["a_owns"] = getattr(st, "_tx_owns_txn", None)
                a_inside.set()
                b_read.wait(10.0)
        out["a_depth_after"] = st._tx_depth
        out["a_owns_after"] = getattr(st, "_tx_owns_txn", None)

    def thread_b():
        assert a_inside.wait(10.0), "夹具：A 未在窗口内进入事务"
        out["b_depth"] = getattr(st, "_tx_depth", 0)
        out["b_owns"] = getattr(st, "_tx_owns_txn", True)
        b_read.set()

    ta = threading.Thread(target=thread_a, name="A")
    tb = threading.Thread(target=thread_b, name="B")
    ta.start()
    tb.start()
    ta.join(30)
    tb.join(30)

    assert out.get("a_depth") == 2, "夹具：A 应为嵌套两层（实测 %r）" % (out.get("a_depth"),)
    assert out.get("b_depth") == 0, (
        "#775②：`_tx_depth` 必须线程本地——B 读到的是**别人的**深度 %r（跨线程事务"
        "会退化为 SAVEPOINT）" % (out.get("b_depth"),))
    assert out.get("b_owns") is True, (
        "#775②：`_tx_owns_txn` 同样须线程本地（B 读到 %r）" % (out.get("b_owns"),))
    assert out.get("a_depth_after") == 0 and out.get("a_owns_after") is True, (
        "A 退出后本线程深度须归零、归属位须归还默认 True（实测 %r / %r）"
        % (out.get("a_depth_after"), out.get("a_owns_after")))
    st.close()


def test_g6_single_thread_nested_sequence_is_bit_identical(tmp_path):
    """G6（正对照）：单线程嵌套的事务序列修前修后**逐位相同**。

    期望序列（与 issue 报告人对照读数同形）：`BEGIN IMMEDIATE → SAVEPOINT _tx_*
    → SAVEPOINT structure_event_* → RELEASE → RELEASE → COMMIT`（`_sp_` 为 uuid
    归一后的占位；本仓实测该嵌套深度为**两个** SAVEPOINT）。本用例**故意不随回退
    变红**——它是正对照。
    """
    db = str(tmp_path / "g6.db")
    st = _store(db)
    _seed(st, ["n0"])
    buf = _capture(st)
    with st._tx():
        with st._tx():
            with st._structure_event_transaction():
                pass
    st.conn.set_trace_callback(None)
    assert buf == [
        "BEGIN IMMEDIATE",
        "SAVEPOINT _sp_", "SAVEPOINT _sp_",
        "RELEASE SAVEPOINT _sp_", "RELEASE SAVEPOINT _sp_",
        "COMMIT",
    ], "单线程嵌套事务序列必须逐位不变（实测 %r）" % (buf,)
    assert st._tx_depth == 0 and getattr(st, "_tx_owns_txn", True) is True, (
        "收尾后本线程深度须归零、归属位归还默认 True")
    st.close()


# =============================================================================
# 阳性对照：四条公开写路径读数逐位不变
# =============================================================================

def test_g7_public_write_paths_readings_unchanged(tmp_path):
    """G7（阳性对照）：`update_node_confidence` / `tag_node` / `add_edge` /
    `delete_node` 四条公开写路径读数修前修后逐位不变（报告人已测四条未丢写）。
    """
    db = str(tmp_path / "g7.db")
    st = _store(db)
    st.add_node(STNode("f1", "a", "text", {}, 0.0, CS, layer=MemoryLayer.KNOWLEDGE))
    st.add_node(STNode("f2", "b", "text", {}, 0.0, CS, layer=MemoryLayer.KNOWLEDGE))
    st.update_node_confidence("f1", 0.2)
    st.tag_node("f1", "f1tag")
    st.add_edge(STEdge("e1", "f1", "f2", EdgeType.SIMILAR, CS, confidence=0.6))

    assert st.get_node("f1").confidence == pytest.approx(0.7)
    assert st.get_node("f1").tags == ["f1tag"]
    assert st.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1
    assert st.delete_node("f2") is True
    assert st.get_node("f2") is None
    st.close()


# =============================================================================
# 静态回归钉：非阻塞取向不得被改成等锁
# =============================================================================

def test_g8_bypass_connection_still_non_blocking(tmp_path):
    """G8（回归钉）：访问计数旁路连接仍是 `timeout=0`（**刻意非阻塞**）。

    判据来源：`ac6c0ca` 的 `increment_access` docstring 逐字「尽力而为：外部写锁
    持有时立即放弃，不阻塞检索主流程」。若后续把该连接换成 `_connect` 的
    `busy_timeout=30000`，`recall` 的 touch 路径会在写竞争下最多阻塞 30 秒——
    那是语义变更，须先过取向题（见 issue #775 处置帖）。本用例**故意不随回退
    变红**（回退后该行仍在）——它是回归钉，不是判别力。
    """
    src = open(CORE_PY, encoding="utf-8").read()
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "increment_access_many"), None)
    assert fn is not None, "夹具：未定位 increment_access_many"

    # 只看**可执行**部分（跳过 docstring）：找 `sqlite3.connect(..., timeout=0)`
    connects = [n for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "connect"]
    assert len(connects) == 1, "夹具：旁路连接应恰有一处 connect（实测 %d）" % len(connects)
    kw = {k.arg: k.value for k in connects[0].keywords}
    assert isinstance(kw.get("timeout"), ast.Constant) and kw["timeout"].value == 0, (
        "#775①：访问计数旁路连接必须保持 timeout=0 的非阻塞取向（实测 timeout=%r）"
        % (ast.unparse(kw["timeout"]) if "timeout" in kw else None,))
    assert kw.get("factory") is None, (
        "#775①：旁路连接不得引入重试工厂（那属等锁取向变更）")

    # 可执行语句里不得出现 `PRAGMA busy_timeout` / 等锁连接
    pragmas = [n for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "execute" and n.args
               and isinstance(n.args[0], ast.Constant)
               and isinstance(n.args[0].value, str)
               and "busy_timeout" in n.args[0].value]
    assert not pragmas, (
        "#775①：旁路连接不得改成继承 busy_timeout=30000 的等锁连接（语义变更）")
