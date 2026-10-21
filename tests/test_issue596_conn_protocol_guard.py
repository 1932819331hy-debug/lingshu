# -*- coding: utf-8 -*-
"""守卫 · issue #596：`_ConnShim` 补齐连接协议 + 消费方**按能力选路**
============================================================================
缺陷（lingshu #596，含 task-7 指出的、issue 正文**没写**的更严重隐患）：
  1. `_ConnShim`（`brain_store.py`）只实现了 `execute` / `commit`，没有
     `executemany` / `rollback`。消费方 `ingest_scene`（#515 引入）先试
     `store.conn.executemany(...)`，脑侧每次都拿 **AttributeError**，被
     `except Exception` 吞掉，再回落逐条 ⇒ 「**用异常做能力探测**」，而且 #515
     那笔"批量优化"在脑侧**是死路径**（每次白抛一个异常）。
  2. ★ **隐患（比 issue 写的更严重）**：一旦只给垫片补上 `executemany` 而消费方
     不改，脑侧就会真走批量路径。垫片背后是**跨进程 RPC**（`record_state` →
     `client.call("cg", {op: state_event})`），**没有真事务**；批量**部分失败**时
     `rollback()` 撤不回已落账的事件（append-only 台账），回落逐条会把**已成功的
     那几条重复落账**——且重试若成功，**连告警都不会发**（完全不可观测）。

修法：
  · `_ConnShim` 补齐协议并**显式声明能力边界**：`executemany`（逐条翻译，
    与逐条路径逐位等价，返回条数）/ `rollback`（**显式无操作**，docstring 写明
    "不是假装回滚成功"）/ 类属性 `atomic_batch = False`；
  · 消费方 `ingest_scene` 的写入按 `getattr(conn, "atomic_batch", True)` 选路：
    真 sqlite ⇒ 批量 + 一次提交、失败 rollback（能撤销整批）后逐条重试；
    脑侧垫片 ⇒ **直接逐条**。
    ★ 逻辑**内联在 `ingest_scene` 里**（不抽模块级 helper）：仓内既有门禁守卫
    `tests/test_issue_ingest_scene_state_batch.py`（`gate.yml` 的 SCRIPT_TESTS）
    用 AST 钉死"`ingest_scene` 内恰有一次 `executemany` / `store.conn.rollback()`
    字面量 / 回落循环字面量"，抽成 helper 会让该门禁红。两者取舍见回报。

断言组（抽掉修复 ⇒ 哪组红）：
  P 协议面：`executemany`/`rollback` 存在、`atomic_batch is False`、
    `executemany` 返回条数且与逐条等价、非状态写 SQL 照旧 `NotImplementedError`；
  R 选路（**抽掉 `atomic_batch` 判据 ⇒ R1 红**）：
    R1 把垫片的 `executemany` 换成"被调用即 raise"⇒ `ingest_scene` 仍成功
       且**不得调用它**；R2 定点变异 `atomic_batch=True` ⇒ R1 那条必须转红
       （executemany 真的被调用了）；R3 变异后批量失败必须**响亮抛出**且不重复
       落账；R4 **真回退演示**：把改动前的实现（宽 `except` + 逐条回落）跑一遍
       ⇒ 实测**重复落账且零告警**；
  E 端到端：每实体状态事件**恰好 1 条**、无警告、**零 AttributeError 探测**、
    状态读回正确；部分失败 ⇒ 成功者各 1 条 + 失败者 1 条告警（文案逐字固定）；
  M 防误杀：真 sqlite（缺 `atomic_batch` ⇒ 取缺省真）仍走批量；批量失败
    `rollback` 撤销整批后逐条重试 ⇒ **不重复落账**；
  S 真 MCP 传输（需 `MDCG_BRAIN_PYTHONPATH` 脑包）缺失时**如实 SKIP**。

判据来源：issue #596 正文（"用异常做能力探测"+"批量优化在脑侧是死路径"）
  ＋ task-7 写明的隐患（补协议必须与按能力选路一起做）；`atomic_batch` 是本件新
  引入的能力声明（仓内无先例，属本件经验标定）；异常收窄依据
  `tools/silent_except_lint.py` 头部登记的口径 (b)「改窄到具体异常类型」。

运行（仓根）：
    python3 -X utf8 -m pytest tests/test_issue596_conn_protocol_guard.py -q
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import warnings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import pytest  # noqa: E402

from lingshu.world.brain_store import (  # noqa: E402
    BrainAgent, BrainError, BrainStore, _ConnShim)
from lingshu.world.scene_model import (  # noqa: E402
    ingest_scene, load_world_from_memory)

SQL = "UPDATE nodes SET state_attributes=? WHERE id=?"

DESC3 = "\n".join(["肥鱼|fatfish|0,0.85,5|shy",
                   "桌子|table|1.5,0.45,6|neutral",
                   "狼|wolf|-2,0.4,7|hungry"])

WARN_FMT = ("场景实体 %s 的状态 %r 未写入脑侧台账（%s: %s）；"
            "记忆节点已创建，重建世界时该实体状态会回落为 neutral")


class StubClient:
    """脑端 RPC 桩：语义面与真脑一致，只把**传输**换成进程内调用。

    `fail_at=k` ⇒ 第 k 次 `cg(op=state_event)` 返回 `{"ok": False}`
    （= 脑端记账未成功，`record_state→False`），用于注入"部分失败"。
    """

    def __init__(self, fail_at=None):
        self.nodes = {}
        self.states = []
        self.calls = []
        self.fail_at = fail_at
        self.n_state_event = 0

    def call(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "mdcg_remember":
            nid = "node-%02d" % (len(self.nodes) + 1)
            self.nodes[nid] = {
                "content": args["content"],
                "tags": list(args.get("tags") or []),
                "coords": (args.get("spatial") or {}).get("coords3d")}
            return {"ok": True, "id": nid}
        if tool == "cg":
            op = args.get("op")
            if op == "state_event":
                self.n_state_event += 1
                if self.fail_at is not None and self.n_state_event == self.fail_at:
                    return {"ok": False}
                self.states.append({"subject": args["subject"], "slot": args["slot"],
                                    "value": args["new"]})
                return {"ok": True}
            if op == "read":
                res = [{"node": {"id": k, "content": v["content"],
                                 "frontmatter": {"tags": v["tags"],
                                                 "spatial": {"coords3d": v["coords"]}}},
                        "state": "BLINDSPOT"} for k, v in self.nodes.items()]
                return {"results": res, "meta": {"pre_cap": len(res)}}
        if tool == "stg" and args.get("op") == "state_chain":
            subj = args.get("subject")
            items = [{"subject": s["subject"], "slot": s["slot"],
                      "state": "active", "value": s["value"]} for s in self.states]
            if subj is not None:
                items = [i for i in items if i["subject"] == str(subj)]
            return {"items": items, "count": len(items), "truncated": False}
        raise AssertionError("桩未实现：%s %r" % (tool, args))

    def n_state_event_calls(self) -> int:
        return sum(1 for t, a in self.calls
                   if t == "cg" and a.get("op") == "state_event")

    def rows_per_entity(self):
        out = {}
        for st in self.states:
            c = self.nodes.get(st["subject"], {}).get("content", "")
            name = (c.split("场景实体 ", 1)[1].split("（", 1)[0]
                    if "场景实体 " in c else st["subject"])
            out[name] = out.get(name, 0) + 1
        return out


class ProbingConn:
    """记录"**用异常做能力探测**"的次数。

    `__getattr__` 只在正常属性查找失败时触发 ⇒ 每次它抛出 AttributeError，
    就是消费方在用异常探测一次能力（正是 #596 要消掉的东西）。
    """

    def __init__(self, inner):
        self.__dict__["_inner"] = inner
        self.__dict__["missing"] = []

    def __getattr__(self, name):
        try:
            return getattr(self.__dict__["_inner"], name)
        except AttributeError:
            self.__dict__["missing"].append(name)
            raise


def _agent(fail_at=None, conn=None):
    """→ (client, probe, agent, store)；`conn` 给了就替掉 store.conn。"""
    cli = StubClient(fail_at=fail_at)
    store = BrainStore(cli, scene_tag="spatial")
    probe = ProbingConn(store.conn)
    store.conn = probe if conn is None else conn
    return cli, probe, BrainAgent(store), store


def _ingest(agent, desc=DESC3):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ids = [str(i) for i in ingest_scene(agent, desc)]
    warns = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    return ids, warns


def test_p1_垫片补齐连接协议并声明能力边界():
    for name in ("execute", "executemany", "commit", "rollback"):
        assert callable(getattr(_ConnShim, name, None)), (
            "P1 `_ConnShim` 缺协议方法 %s（消费方又会去用 AttributeError 探测）" % name)
    assert hasattr(_ConnShim, "atomic_batch"), "P1 缺能力声明 atomic_batch"
    assert _ConnShim.atomic_batch is False, (
        "P1 `atomic_batch` 必须**显式**为 False（跨进程 RPC、无真事务）；取值=%r"
        % (_ConnShim.atomic_batch,))


def test_p2_executemany_逐条等价且返回条数():
    cli = StubClient()
    shim = _ConnShim(BrainStore(cli, scene_tag="spatial"))
    seq = [(json.dumps({"state": "a"}), "n1"),
           (json.dumps({"state": "b"}), "n2"),
           (json.dumps({"state": "c"}), "n3")]
    n = shim.executemany(SQL, seq)
    assert n == 3, "P2 executemany 返回条数应为 3，实得 %r" % (n,)
    assert [s["subject"] for s in cli.states] == ["n1", "n2", "n3"], cli.states
    cli2 = StubClient()
    shim2 = _ConnShim(BrainStore(cli2, scene_tag="spatial"))
    for p in seq:
        shim2.execute(SQL, p)
    assert cli2.states == cli.states, "P2 executemany 与逐条 execute 不等价"


def test_p3_rollback_是显式无操作且不假装成功():
    cli = StubClient()
    shim = _ConnShim(BrainStore(cli, scene_tag="spatial"))
    shim.execute(SQL, (json.dumps({"state": "x"}), "n1"))
    assert shim.rollback() is None, "P3 rollback 应为显式 no-op（返回 None）"
    assert len(cli.states) == 1, (
        "P3 rollback 不得被当成「真撤销」使用：已落账事件必须仍在（物理上撤不回）")


def test_p4_非状态写语句照旧_fail_closed():
    shim = _ConnShim(BrainStore(StubClient(), scene_tag="spatial"))
    for bad in ("SELECT 1", "DELETE FROM nodes", None):
        with pytest.raises(NotImplementedError):
            shim.executemany(bad, [(json.dumps({"state": "x"}), "n1")])


def test_r1_脑侧选路生效_executemany_不得被调用(monkeypatch):
    """把垫片 `executemany` 换成"被调用即 raise" ⇒ ingest 仍成功且不得调用它。

    抽掉 `ingest_scene` 里的 `atomic_batch` 判据（一律走批量）⇒ 本组红。
    """
    called = []

    def _boom(self, sql, seq):
        called.append(True)
        raise AssertionError("脑侧（atomic_batch=False）不得走批量路径")

    monkeypatch.setattr(_ConnShim, "executemany", _boom)
    cli, probe, agent, _store = _agent()
    ids, warns = _ingest(agent)
    assert len(ids) == 3, ids
    assert not called, "R1 垫片 executemany 被调用了 —— 选路没生效（回到「先批量再回落」）"
    assert probe.missing == [], "R1 仍在用异常做能力探测：%r" % (probe.missing,)
    assert not warns, warns
    assert cli.rows_per_entity() == {"肥鱼": 1, "桌子": 1, "狼": 1}, cli.rows_per_entity()


def test_r2_定点变异_atomic_batch_改真则_r1_的判据转红(monkeypatch):
    """★ 定点变异：只把能力声明改成 True，上一条的"不得调用"必须**失效**。

    没有这条，R1 可能是假绿（例如选路判据被写死成"永远逐条"却仍自称按能力选路）。
    """
    called = []

    def _record(self, sql, seq):
        called.append(list(seq))
        return 0

    monkeypatch.setattr(_ConnShim, "executemany", _record)
    monkeypatch.setattr(_ConnShim, "atomic_batch", True)
    _cli, _probe, agent, _store = _agent()
    _ingest(agent)
    assert called, ("R2 变异后仍未调用 executemany —— 说明选路没有真的读 "
                    "`atomic_batch`，R1 是假绿")


def test_r3_变异后批量失败必须响亮暴露且不重复落账(monkeypatch):
    """★ 第二层防线：即使 `atomic_batch` 被误声明为真，也**不得**静默重复落账。

    `atomic_batch=True`（变异）+ 第 2 条失败 ⇒ 垫片的 `executemany` 抛 `BrainError`；
    该异常**故意不在**批量兜底里（只收窄到 `sqlite3.Error` / `NotImplementedError`）
    ⇒ 直接向上抛，不滑进"rollback（no-op）→ 逐条重试"那条会把已落账重复落账的路。
    """
    monkeypatch.setattr(_ConnShim, "atomic_batch", True)
    cli, _probe, agent, _store = _agent(fail_at=2)
    with pytest.raises(BrainError):
        _ingest(agent)
    rows = cli.rows_per_entity()
    assert max(rows.values() or [0]) <= 1, (
        "R3 变异后出现重复落账（应响亮抛出而不是静默重复）：%r" % (rows,))
    assert len(cli.states) <= 1, cli.states


def _legacy_batch_first(conn, pending):
    """**改动前**的实现（宽 `except Exception` + 批量先行 + 逐条回落）。

    仅用于 R4 的"回退即红"实测：它必须复现出**重复落账且零告警**。
    """
    try:
        conn.executemany(SQL, [(json.dumps({"state": s}), i) for _n, s, i in pending])
        conn.commit()
        return len(pending)
    except Exception:
        try:
            conn.rollback()
        except (AttributeError, sqlite3.Error):
            pass
        for _n, s, i in pending:
            try:
                conn.execute(SQL, (json.dumps({"state": s}), i))
                conn.commit()
            except Exception as exc:
                warnings.warn(WARN_FMT % (_n, s, type(exc).__name__, exc),
                              RuntimeWarning, stacklevel=2)
    return 0


def test_r4_真回退演示_旧实现重复落账且零告警():
    """★ 真·回退：把**改动前**的批量先行实现跑一遍，实测隐患。

    这条与 R2/R3 不同：R2/R3 只改能力声明，本组直接执行旧实现 ⇒ 证明
    「R1 的判据不是靠"选路被写死成逐条"才绿的」，也把隐患钉成可复现读数。
    """
    cli = StubClient(fail_at=2)
    shim = _ConnShim(BrainStore(cli, scene_tag="spatial"))
    names = (("肥鱼", "shy"), ("桌子", "neutral"), ("狼", "hungry"))
    for name, state in names:
        cli.nodes["node-%02d" % (len(cli.nodes) + 1)] = {
            "content": "场景实体 %s（x）位于 (0,0,0)，状态 %s" % (name, state),
            "tags": ["spatial"], "coords": None}
    pending = [("肥鱼", "shy", "node-01"), ("桌子", "neutral", "node-02"),
               ("狼", "hungry", "node-03")]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _legacy_batch_first(shim, pending)
    warns = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    rows = cli.rows_per_entity()
    assert max(rows.values()) > 1, (
        "R4 旧实现下未出现重复落账（判据失去判别力）：%r" % (rows,))
    assert len(cli.states) > len(pending) - 1, (len(cli.states), len(pending))
    assert not warns, ("R4 旧实现下重复落账竟然是静默的（实测告警 0 条）：%r"
                       % ([str(w.message) for w in warns],))
    assert cli.n_state_event_calls() == 5, cli.n_state_event_calls()


def test_e1_正常路径_每实体恰好一条_无警告_零异常探测():
    cli, probe, agent, store = _agent()
    ids, warns = _ingest(agent)
    assert len(ids) == 3 and len(set(ids)) == 3, ids
    assert cli.rows_per_entity() == {"肥鱼": 1, "桌子": 1, "狼": 1}, cli.rows_per_entity()
    assert len(cli.states) == 3, cli.states
    assert not warns, [str(w.message) for w in warns]
    assert probe.missing == [], "E1 仍在用异常做能力探测：%r" % (probe.missing,)
    assert cli.n_state_event_calls() == 3, cli.n_state_event_calls()
    wm = load_world_from_memory(store)
    assert {k: v.state for k, v in wm.entities.items()} == {
        "肥鱼": "shy", "桌子": "neutral", "狼": "hungry"}, wm.entities


def test_e2_部分失败_成功者各一条_失败者一条告警且不重复落账():
    cli, probe, agent, store = _agent(fail_at=2)
    ids, warns = _ingest(agent)
    rows = cli.rows_per_entity()
    assert len(ids) == 3, ids
    assert rows == {"肥鱼": 1, "狼": 1}, (
        "E2 已成功的那几条必须**只落账一次**（无重复、无遗漏）：%r" % (rows,))
    assert len(cli.states) == 2, cli.states
    assert max(rows.values()) == 1, "E2 出现重复落账：%r" % (rows,)
    assert cli.n_state_event_calls() == 3, (
        "E2 状态事件调用 %d 次 ⇒ 有重复记账" % cli.n_state_event_calls())
    assert len(warns) == 1, [str(w.message) for w in warns]
    assert str(warns[0].message) == (
        "场景实体 桌子 的状态 'neutral' 未写入脑侧台账（BrainError: "
        "状态事件未入账（record_state→False）：node=node-02 state='neutral'；"
        "节点正文已写入、台账未落，重建世界会与节点内容不一致）；"
        "记忆节点已创建，重建世界时该实体状态会回落为 neutral"), str(warns[0].message)
    assert warns[0].category is RuntimeWarning, warns[0].category
    assert os.path.basename(warns[0].filename) == os.path.basename(__file__), (
        "E2 告警归属漂移了：filename=%r（stacklevel 应与改动前一致，落在 "
        "ingest_scene 的调用方）" % (warns[0].filename,))
    assert probe.missing == [], probe.missing


class AtomicConn:
    """显式声明原子批量的连接（真 sqlite 的替代）：`rollback` 能撤销整批。"""

    atomic_batch = True

    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.txn = []
        self.committed = []
        self.many_calls = 0

    def executemany(self, sql, seq):
        self.many_calls += 1
        for i, p in enumerate(seq, 1):
            if self.fail_at == i:
                raise sqlite3.OperationalError("injected batch failure")
            self.txn.append(p)

    def execute(self, sql, p):
        self.txn.append(p)

    def commit(self):
        self.committed.extend(self.txn)
        self.txn = []

    def rollback(self):
        self.txn = []


def _committed_ids(conn):
    out = {}
    for p in conn.committed:
        out[p[1]] = out.get(p[1], 0) + 1
    return out


def test_m1_真连接走批量一次提交():
    conn = AtomicConn()
    _cli, _probe, agent, _store = _agent(conn=conn)
    ids, warns = _ingest(agent)
    assert len(ids) == 3, ids
    assert conn.many_calls == 1, "M1 应只走一次 executemany，实得 %d" % conn.many_calls
    assert _committed_ids(conn) == {"node-01": 1, "node-02": 1, "node-03": 1}, \
        _committed_ids(conn)
    assert not warns, warns


def test_m2_真连接批量失败_rollback_撤销后逐条重试不重复落账():
    conn = AtomicConn(fail_at=2)
    _cli, _probe, agent, _store = _agent(conn=conn)
    ids, warns = _ingest(agent)
    assert len(ids) == 3, ids
    assert _committed_ids(conn) == {"node-01": 1, "node-02": 1, "node-03": 1}, (
        "M2 真连接批量失败后逐条重试出现重复落账：%r（rollback 应撤销整批）"
        % (_committed_ids(conn),))
    assert not warns, [str(w.message) for w in warns]


def test_m3_真_sqlite_缺_atomic_batch_属性时取缺省真_走批量():
    """真 `sqlite3.Connection` 没有 `atomic_batch` ⇒ `getattr(..., True)` 取真。

    这条钉住"缺省=真"，防止有人把缺省改成 False 而让真 sqlite 静默退化成逐条。
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, state_attributes TEXT)")
        conn.executemany("INSERT INTO nodes (id, state_attributes) VALUES (?, '')",
                         [("node-01",), ("node-02",), ("node-03",)])
        conn.commit()
        assert not hasattr(conn, "atomic_batch"), (
            "M3 真 sqlite 连接竟然有 atomic_batch 属性——缺省取真的前提变了")
        assert getattr(conn, "atomic_batch", True) is True
        _cli, _probe, agent, _store = _agent(conn=conn)
        ids, warns = _ingest(agent)
        assert len(ids) == 3, ids
        got = dict(conn.execute("SELECT id, state_attributes FROM nodes").fetchall())
        assert got == {"node-01": json.dumps({"state": "shy"}),
                       "node-02": json.dumps({"state": "neutral"}),
                       "node-03": json.dumps({"state": "hungry"})}, got
        assert not warns, warns
    finally:
        conn.close()


class DeadConn:
    """任何写都失败、且声明非原子的连接（钉住"不阻断导入 + 文案逐位一致"）。"""

    atomic_batch = False

    def execute(self, sql, p):
        raise sqlite3.OperationalError("disk I/O error")

    def commit(self):
        pass

    def rollback(self):
        pass


def test_m4_写失败仍不阻断导入且文案逐位一致():
    _cli, _probe, agent, _store = _agent(conn=DeadConn())
    ids, warns = _ingest(agent)
    assert len(ids) == 3, ids
    assert len(warns) == 3, [str(w.message) for w in warns]
    assert str(warns[0].message) == (
        "场景实体 肥鱼 的状态 'shy' 未写入脑侧台账（OperationalError: disk I/O error）；"
        "记忆节点已创建，重建世界时该实体状态会回落为 neutral"), str(warns[0].message)


def test_s1_真脑包端到端():
    """真 MCP 子进程通路（`connect` + 真脑包）。缺前置 ⇒ SKIP（**不是通过**）。"""
    pythonpath = os.environ.get("MDCG_BRAIN_PYTHONPATH")
    if not pythonpath:
        pytest.skip("SKIP：未设 MDCG_BRAIN_PYTHONPATH（脑包目录，须含 md_cg/）"
                    "——本机**未跑**真 MCP 传输端到端；语义面由 E/R/M 组在进程内"
                    "桩上覆盖（同一批函数，只替换 JSON-RPC 传输）")
    import shutil
    import tempfile
    from lingshu.world.brain_store import connect
    root = tempfile.mkdtemp(prefix="lingshu_596_")
    assert root.startswith(tempfile.gettempdir()), "隔离根必须是临时目录（fail-closed）"
    agent = connect(root=root, pythonpath=pythonpath,
                    extra_env={"MDCG_LEGACY_ENV_AUTH": "1", "MDCG_CAN_ADMIN": "1",
                               "MDCG_LEGACY_ENV_ADMIN": "1"})
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ids = [str(i) for i in ingest_scene(agent, DESC3)]
        ledger = os.path.join(root, "_state_events.jsonl")
        rows = 0
        if os.path.isfile(ledger):
            with open(ledger, encoding="utf-8") as fh:
                rows = sum(1 for ln in fh if ln.strip())
        wm = load_world_from_memory(agent.store)
        states = {k: v.state for k, v in wm.entities.items()}
        assert len(ids) == 3, ids
        assert rows == 3, ("真脑端台账行数应为 3（每实体恰好 1 条），实得 %d" % rows)
        assert not [w for w in caught if issubclass(w.category, RuntimeWarning)], caught
        assert states == {"肥鱼": "shy", "桌子": "neutral", "狼": "hungry"}, states
    finally:
        try:
            agent.store.client.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
# S2 **真传输**端到端：真 `MCPClient` 子进程 + JSON-RPC（脑包用**假脑**替代）
# ---------------------------------------------------------------------------
# 与 E/R/M 组的差别：那几组只替换 `client.call`（进程内桩）；本组起**真子进程**，
# 走 `subprocess.Popen` + 逐行 JSON-RPC + `PYTHONPATH` 注入 + `MDCG_ROOT` 隔离根，
# 台账真的落到磁盘 `_state_events.jsonl`。
# ★ 如实标注：脑端是**本测试现写的假脑**（实现 `mdcg_remember` / `cg(op=read|
#   state_event)` / `stg(op=state_chain)` 四个面，写进 tmp_path）——它验证的是
#   **本件改动在真传输边界上不变**，**不**代表真脑包已验；真脑包版见 S1（本机 SKIP）。
# ===========================================================================

FAKE_BRAIN_SRC = r'''
import json, os, sys

ROOT = os.environ.get("MDCG_ROOT") or os.path.dirname(os.path.abspath(__file__))
NODES = os.path.join(ROOT, "_fake_nodes.jsonl")
LEDGER = os.path.join(ROOT, "_state_events.jsonl")
#: 部分失败注入点（本测试按需改写这一行）：第 k 次 state_event 返回 ok=False
FAIL_AT = None
_STATE_N = 0


def _rows(path):
    out = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    out.append(json.loads(ln))
    return out


def _reply(mid, result=None, error=None):
    obj = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        obj["error"] = error
    else:
        obj["result"] = result
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _dispatch(name, args):
    if name == "mdcg_remember":
        nodes = _rows(NODES)
        nid = "node-%03d" % (len(nodes) + 1)
        rec = {"id": nid, "content": args.get("content", ""),
               "tags": list(args.get("tags") or []),
               "coords": (args.get("spatial") or {}).get("coords3d"),
               "created_at": 1.0}
        with open(NODES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "id": nid}
    if name == "cg":
        op = args.get("op")
        if op == "state_event":
            global _STATE_N
            _STATE_N += 1
            if FAIL_AT is not None and _STATE_N == FAIL_AT:
                return {"ok": False}
            with open(LEDGER, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"subject": str(args.get("subject")),
                                     "slot": args.get("slot"),
                                     "value": args.get("new"),
                                     "kind": args.get("kind")},
                                    ensure_ascii=False) + "\n")
            return {"ok": True}
        if op == "read":
            res = []
            for n in _rows(NODES):
                res.append({"node": {"id": n["id"], "content": n["content"],
                                     "frontmatter": {"tags": n["tags"],
                                                     "created_at": n["created_at"],
                                                     "spatial": {"coords3d": n["coords"]}}},
                            "state": "BLINDSPOT"})
            return {"results": res, "meta": {"pre_cap": len(res)}}
    if name == "stg" and args.get("op") == "state_chain":
        latest = {}
        for e in _rows(LEDGER):
            latest[e["subject"]] = e
        subj = args.get("subject")
        items = [{"subject": k, "slot": v["slot"], "state": "active",
                  "value": v["value"]} for k, v in sorted(latest.items())
                 if subj is None or str(k) == str(subj)]
        return {"items": items, "count": len(items), "truncated": False}
    raise ValueError("fake brain: 未实现的工具/操作 %r %r" % (name, args))


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            _reply(mid, {"protocolVersion": "2024-11-05",
                         "serverInfo": {"name": "fake-md-cg", "version": "0.0.1"},
                         "capabilities": {}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                payload = _dispatch(params.get("name"), params.get("arguments") or {})
            except Exception as exc:
                _reply(mid, None, {"code": -32000, "message": str(exc)})
                continue
            _reply(mid, {"content": [{"text": json.dumps(payload, ensure_ascii=False)}],
                         "isError": False})
        elif mid is not None:
            _reply(mid, None, {"code": -32601, "message": "method not found"})


if __name__ == "__main__":
    main()
'''


def _install_fake_brain(rootdir):
    pkg = os.path.join(rootdir, "md_cg")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("")
    with open(os.path.join(pkg, "mcp_server.py"), "w", encoding="utf-8") as fh:
        fh.write(FAKE_BRAIN_SRC)
    return rootdir


def test_s2_真_MCP_传输端到端_假脑包(tmp_path):
    """真子进程 + 逐行 JSON-RPC：正常路径每实体恰好 1 条落盘台账；部分失败不重复。"""
    from lingshu.world.brain_store import connect
    import shutil
    brain_dir = _install_fake_brain(str(tmp_path / "brain"))
    root = str(tmp_path / "root")
    os.makedirs(root, exist_ok=True)

    def _run(fail_at=None):
        # ★ 部分失败注入点：假脑在第 k 次 state_event 返回 ok=False
        src = os.path.join(brain_dir, "md_cg", "mcp_server.py")
        with open(src, encoding="utf-8") as fh:
            body = fh.read()
        # 幂等注入：每次先把注入行还原，再按 fail_at 决定是否注入
        body = body.replace("FAIL_AT = 2\n", "FAIL_AT = None\n")
        if fail_at is not None:
            body = body.replace("FAIL_AT = None", "FAIL_AT = %d" % fail_at)
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(body)
        agent = connect(root=root, pythonpath=brain_dir, request_timeout=30)
        try:
            ledger = os.path.join(root, "_state_events.jsonl")
            if os.path.isfile(ledger):
                os.remove(ledger)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ids = [str(i) for i in ingest_scene(agent, DESC3)]
            warns = [w for w in caught if issubclass(w.category, RuntimeWarning)]
            rows = 0
            if os.path.isfile(ledger):
                with open(ledger, encoding="utf-8") as fh:
                    rows = sum(1 for ln in fh if ln.strip())
            wm = load_world_from_memory(agent.store)
            states = {k: v.state for k, v in wm.entities.items()}
            return ids, warns, rows, states
        finally:
            agent.store.client.close()

    ids, warns, rows, states = _run()
    assert len(ids) == 3, ids
    assert rows == 3, ("S2 真传输：磁盘台账应为 3 行（每实体恰好 1 条），实得 %d" % rows)
    assert not warns, [str(w.message) for w in warns]
    assert states == {"肥鱼": "shy", "桌子": "neutral", "狼": "hungry"}, states

    ids2, warns2, rows2, states2 = _run(fail_at=2)
    assert len(ids2) == 3, ids2
    assert rows2 == 2, ("S2 真传输·部分失败：台账应为 2 行（无重复落账），实得 %d" % rows2)
    assert len(warns2) == 1, [str(w.message) for w in warns2]
    assert "未写入脑侧台账" in str(warns2[0].message), str(warns2[0].message)
    shutil.rmtree(brain_dir, ignore_errors=True)


# ===========================================================================
# F 三态语义（复核 F2 的回填项：**不改行为**，把"为什么这样是对的"钉住）
# ---------------------------------------------------------------------------
# 复核意见（`_evidence/verify/REVIEW-615.md` §F2）：falsy 状态（None / 空串）
# 被"静默丢弃（零告警）"。**裁定：这是对的，不改行为** —— 仓内既有口径
# `tests/test_state_write_observable.py` T3 写着「空状态属『无状态可写』，
# 不报警、重建 neutral」。但原代码/文档没把这条区分写下来 ⇒ 下一个人会再报一次。
# 本组把三态钉死 + 定点变异证明判据能红（不是恒真）。
# ===========================================================================

#: 定点变异的锚点：`execute` 里唯一的抛错条件。
#: 变异 = 把它改成 `if not ok:`（把 falsy 也当失败）⇒「falsy 合法静默」必须转红。
FALSY_ANCHOR = "if state and not ok:"
FALSY_MUTATED = "if not ok:"


def test_f1_空状态属无状态可写_不抛不告警不落账():
    """falsy 状态 = 「无状态可写」⇒ 合法静默：不抛、不告警、不落账、**不发 RPC**。"""
    cases = [("state=None", json.dumps({"state": None})),
             ("state=''", json.dumps({"state": ""})),
             ("payload 无 state 键", json.dumps({})),
             ("params[0] 为 None", None)]
    for tag, payload in cases:
        cli = StubClient()
        shim = _ConnShim(BrainStore(cli, scene_tag="spatial"))
        n_before = len(cli.calls)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                shim.execute(SQL, (payload, "n1"))
                raised = None
            except BrainError as exc:
                raised = "BrainError: %s" % exc
        warns = [w for w in caught if issubclass(w.category, RuntimeWarning)]
        assert raised is None, (
            "F1 falsy 状态（%s）被当成写失败抛错：%s —— 它属「无状态可写」"
            "（T3 口径），不是失败" % (tag, raised))
        assert not warns, "F1 falsy 状态（%s）竟然告警：%r" % (
            tag, [str(w.message) for w in warns])
        assert cli.states == [], "F1 falsy 状态（%s）竟然落账：%r" % (tag, cli.states)
        assert len(cli.calls) == n_before, (
            "F1 falsy 状态（%s）竟然发起了 RPC —— record_state 应对 falsy 直接短路"
            % tag)


def test_f2_非空状态未落账必须抛_BrainError():
    """非空状态 + `record_state→False` ⇒ **必须可见**（抛 `BrainError`）。"""
    cli = StubClient(fail_at=1)
    shim = _ConnShim(BrainStore(cli, scene_tag="spatial"))
    with pytest.raises(BrainError) as e:
        shim.execute(SQL, (json.dumps({"state": "shy"}), "n1"))
    assert "状态事件未入账" in str(e.value), str(e.value)
    assert cli.states == [], cli.states


def test_f3_非空状态落账成功正常返回():
    cli = StubClient()
    shim = _ConnShim(BrainStore(cli, scene_tag="spatial"))
    assert shim.execute(SQL, (json.dumps({"state": "shy"}), "n1")) is shim
    assert [s["subject"] for s in cli.states] == ["n1"], cli.states


def test_f4_端到端_空状态行不告警且只落非空那条的账():
    """T3 口径的端到端版：混有空状态行的场景 ⇒ 0 告警、只落非空那条、空者回落 neutral。"""
    desc = "甲|box|0,0,0|\n乙|box|1,0,0|neutral\n丙|box|2,0,0|"
    cli, probe, agent, store = _agent()
    ids, warns = _ingest(agent, desc)
    assert len(ids) == 3, ids
    assert warns == [], [str(w.message) for w in warns]
    assert cli.rows_per_entity() == {"乙": 1}, cli.rows_per_entity()
    assert cli.n_state_event_calls() == 1, cli.n_state_event_calls()
    assert probe.missing == [], probe.missing
    wm = load_world_from_memory(store)
    assert {k: v.state for k, v in wm.entities.items()} == {
        "甲": "neutral", "乙": "neutral", "丙": "neutral"}, wm.entities


def test_f5_定点变异_falsy也当失败则_f1_f4_转红(monkeypatch):
    """★ 定点变异（**真源码单点改写**）：`if state and not ok:` → `if not ok:`。

    变异后 falsy 状态会被当失败 ⇒ f1（falsy 不抛不告警）与 f4（空状态行不告警）
    的判据必须**失效**。这条同时钉住"锚点还在"：若有人重写了抛错条件而没更新
    三态文档，本测试会在锚点断言处**响亮失败**，而不是静默失去判别力。
    """
    import inspect
    import textwrap

    src = inspect.getsource(_ConnShim.execute)
    assert src.count(FALSY_ANCHOR) == 1, (
        "F5 变异锚点 %r 在 `_ConnShim.execute` 里出现 %d 次（应恰 1 次）——"
        "抛错条件被改写过了；请同步复核三态 docstring 与本组守卫"
        % (FALSY_ANCHOR, src.count(FALSY_ANCHOR)))
    mutated_src = textwrap.dedent(src).replace(FALSY_ANCHOR, FALSY_MUTATED)
    assert FALSY_MUTATED in mutated_src, mutated_src
    ns = {"json": json, "BrainError": BrainError}
    exec(mutated_src, ns)                                  # noqa: S102 —— 受控单点变异
    monkeypatch.setattr(_ConnShim, "execute", ns["execute"])

    # ① 单点：falsy 现在会抛 ⇒ f1 的断言（raised is None）失效
    cli = StubClient()
    shim = _ConnShim(BrainStore(cli, scene_tag="spatial"))
    with pytest.raises(BrainError):
        shim.execute(SQL, (json.dumps({"state": ""}), "n1"))

    # ② 端到端：空状态行现在会告警 ⇒ f4 的断言（warns == []）失效
    _cli2, _probe2, agent2, _store2 = _agent()
    _ids, warns = _ingest(agent2, "甲|box|0,0,0|\n乙|box|1,0,0|neutral\n丙|box|2,0,0|")
    assert warns, ("F5 变异后空状态行仍未告警 —— 说明 f1/f4 的判据是假绿"
                   "（变异没生效或被别处吞掉）")
    assert len(warns) == 2, [str(w.message) for w in warns]
    assert all("未写入脑侧台账" in str(w.message) for w in warns), \
        [str(w.message) for w in warns]
