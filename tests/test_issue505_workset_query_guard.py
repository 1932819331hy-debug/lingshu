# -*- coding: utf-8 -*-
"""#505 守卫：工作集落库必须能回溯到产生它的查询（`activation_nodes.query`）。

缺陷面（公开 issue #505，intake）：`_write_workset(workset, members, query, ...)` 收了
`query` 却零引用；`activation_nodes` 表里也**没有这一列**——同一签名里的 `sources`/`hops`
都有列、都落库，只有 `query` 被静默丢弃。工作集落库后无法回答「它是哪次查询的产物」。

本件锁住的判据：
  ① 新库：`activate(query)` 后 `workset_state()["query"]` 与 `export_workset()["query"]`
     都等于该查询；
  ② 持久化：关闭后重开同一文件库，查询仍可回读；
  ③ 覆盖语义：同一 workset 再次 activate 新查询 ⇒ 旧查询被替换（DELETE+INSERT）；
  ④ 旧库迁移：旧 7 列结构（无 query 列）重开自动补列，旧行 query 为 None（不猜）；
  ⑤ 快照往返：`export_workset` → `import_workset` 后查询保留；
  ⑥ 空工作集：query 为 None、size 为 0，不抛。
定点变异：把 `_write_workset` 的 INSERT 里 `query` 改回不写（或删 ALTER 迁移）⇒
④/⑤ 与 ① 相应变红。
"""
import gc
import sqlite3

import pytest

from lingshu.core.activation import ActivationEngine
from lingshu.core.core import SpacetimeMemoryEngine


@pytest.fixture
def engine_factory(tmp_path):
    """The activation engine borrows its connection; retain and close its owner."""
    owners = []

    def create(name="act.db"):
        path = str(tmp_path / name)
        mem = SpacetimeMemoryEngine(path)
        owners.append(mem)
        mem.add_perception("量子纠缠实验在液氦温区观测到超导涡旋", skip_dedup=True)
        return ActivationEngine(store=mem.store), path

    try:
        yield create
    finally:
        for mem in reversed(owners):
            mem.close()


def _seeded(eng, query, workset="w1"):
    out = eng.activate(query, workset=workset)
    assert out["status"] == "ok"
    assert out["size"] > 0, "夹具应至少激活一个节点（否则本守卫测不到落库）"
    return out


# ---------------------------------------------------------------- ① 写入与回读

def test_activate_persists_and_exposes_query(tmp_path, engine_factory):
    eng, _ = engine_factory()
    _seeded(eng, "量子纠缠 超导")
    st = eng.workset_state("w1")
    assert st["query"] == "量子纠缠 超导", "workset_state 未回读查询：%r" % (st,)
    snap = eng.export_workset("w1")
    assert snap["query"] == "量子纠缠 超导", "export_workset 未带出查询：%r" % (snap,)
    assert snap["members"], "快照应有成员"


# ---------------------------------------------------------------- ② 持久化

def test_query_survives_reopen(tmp_path, engine_factory):
    eng, path = engine_factory()
    _seeded(eng, "超导 液氦")
    eng2 = ActivationEngine(db_path=path)
    assert eng2.workset_state("w1")["query"] == "超导 液氦"


# ---------------------------------------------------------------- ③ 覆盖语义

def test_reactivation_replaces_query(tmp_path, engine_factory):
    eng, path = engine_factory()
    _seeded(eng, "量子纠缠")
    _seeded(eng, "超导 温区")
    assert eng.workset_state("w1")["query"] == "超导 温区"
    con = sqlite3.connect(path)
    rows = con.execute(
        "SELECT DISTINCT query FROM activation_nodes WHERE workset='w1'").fetchall()
    con.close()
    assert rows == [("超导 温区",)], "同一 workset 的旧查询应被整批替换：%r" % (rows,)


# ---------------------------------------------------------------- ④ 旧库迁移

def test_legacy_schema_gets_query_column(tmp_path):
    path = str(tmp_path / "legacy.db")
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE activation_nodes (
        id TEXT PRIMARY KEY, workset TEXT NOT NULL, node_id TEXT NOT NULL,
        activation REAL NOT NULL, source TEXT NOT NULL, hop INTEGER NOT NULL, ts REAL NOT NULL)""")
    con.execute("INSERT INTO activation_nodes VALUES ('old1','w1','n1',0.5,'seed',0,1.0)")
    con.commit()
    con.close()

    eng = ActivationEngine(db_path=path)
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(activation_nodes)")}
    con.close()
    assert "query" in cols, "旧 7 列库重开未补 query 列：%r" % (cols,)
    st = eng.workset_state("w1")
    assert st["query"] is None, "旧行没有查询信息，不得猜测：%r" % (st,)
    # 补列后新写入应可落查询
    eng._write_workset("w2", [("n2", 0.7)], "迁移后的查询")
    assert eng.workset_state("w2")["query"] == "迁移后的查询"


# ---------------------------------------------------------------- ⑤ 快照往返

def test_export_import_roundtrip_keeps_query(tmp_path, engine_factory):
    eng, _ = engine_factory()
    _seeded(eng, "量子 涡旋")
    snap = eng.export_workset("w1")
    eng.import_workset(snap, workset="w1-restored")
    assert eng.workset_state("w1-restored")["query"] == "量子 涡旋"
    # 旧版快照（无 query 键）仍可导入，query 为 None
    legacy = dict(snap)
    legacy.pop("query")
    eng.import_workset(legacy, workset="w1-legacy")
    assert eng.workset_state("w1-legacy")["query"] is None


# ---------------------------------------------------------------- ⑥ 空工作集

def test_empty_workset_query_is_none(tmp_path, engine_factory):
    eng, _ = engine_factory()
    st = eng.workset_state("no-such-workset")
    assert st["size"] == 0 and st["query"] is None
    snap = eng.export_workset("no-such-workset")
    assert snap["members"] == [] and snap["query"] is None


def test_borrowed_fixture_owner_survives_collection(engine_factory):
    """Deterministic negative control for the former CI-only owner collection."""
    eng, _ = engine_factory()
    gc.collect()
    try:
        _seeded(eng, "量子纠缠 超导")
    except sqlite3.ProgrammingError as exc:
        pytest.fail("borrowed query fixture lost its database owner: " + str(exc))
    assert eng.workset_state("w1")["query"] == "量子纠缠 超导"
