"""同一个激活实例跨 chdir 后仍须读写构造时选中的文件。"""
from pathlib import Path

import pytest

from lingshu.core.activation import ActivationEngine
from lingshu.core.core import SpacetimeMemoryEngine


pytestmark = pytest.mark.filterwarnings("ignore")


def _snapshot(nid, activation):
    return {"workset": "session", "members": [{"node_id": nid, "activation": activation}]}


def _members(engine):
    return {row[0]: row[1] for row in engine.store.conn.execute(
        "SELECT node_id, activation FROM activation_nodes WHERE workset='session'")}


@pytest.fixture
def pair(tmp_path, monkeypatch):
    previous = Path.cwd()
    engines, ids = {}, {}
    try:
        for name, score in (("a", 0.8), ("b", 0.25)):
            directory = tmp_path / name
            directory.mkdir()
            engine = SpacetimeMemoryEngine(str(directory / "graph.db"))
            engines[name] = engine
            ids[name] = engine.add_context("shared token " + name, importance=0.5).id
            initial = ActivationEngine(engine.store.db_path, audit_path=str(tmp_path / (name + ".jsonl")))
            initial.import_workset(_snapshot(ids[name], score))
        monkeypatch.chdir(tmp_path / "a")
        active = ActivationEngine("graph.db", audit_path=str(tmp_path / "live.jsonl"))
        yield active, engines, ids, tmp_path, monkeypatch
    finally:
        monkeypatch.chdir(previous)
        for engine in engines.values():
            engine.close()


def test_import_after_chdir_does_not_overwrite_the_other_database(pair):
    active, engines, ids, root, monkeypatch = pair
    monkeypatch.chdir(root / "b")
    active.import_workset(_snapshot(ids["a"], 0.9))
    assert _members(engines["a"]) == {ids["a"]: 0.9}, "activation write moved to another cwd database"
    assert _members(engines["b"]) == {ids["b"]: 0.25}, "activation overwrote another database's workset"


def test_all_workset_readers_stay_on_the_original_database(pair):
    active, _, ids, root, monkeypatch = pair
    monkeypatch.chdir(root / "b")
    assert active.carry_vector("session") == {ids["a"]: 0.8}, "activation read moved to another cwd database"
    assert active.export_workset("session")["members"][0]["node_id"] == ids["a"]
    state = active.workset_state("session")
    assert state["members"][0]["node_id"] == ids["a"]
    assert state["members"][0]["content"] == "shared token a"


def test_activate_reads_and_replaces_only_its_original_workset(pair):
    active, engines, ids, root, monkeypatch = pair
    monkeypatch.chdir(root / "b")
    result = active.activate("shared token", hops=0, workset="session")
    assert [nid for nid, _ in result["top"]] == [ids["a"]], "activate seeded from another cwd database"
    assert _members(engines["a"]) == {ids["a"]: 1.0}
    assert _members(engines["b"]) == {ids["b"]: 0.25}


def test_borrowed_store_keeps_its_connection_and_caller_rollback(pair):
    _, engines, ids, root, monkeypatch = pair
    monkeypatch.chdir(root / "b")
    borrowed = ActivationEngine(store=engines["a"].store, audit_path=str(root / "borrowed.jsonl"))
    conn = engines["a"].store.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        borrowed.import_workset(_snapshot(ids["a"], 0.6))
        assert conn.in_transaction
        assert borrowed.carry_vector("session") == {ids["a"]: 0.6}
    finally:
        conn.rollback()
    assert _members(engines["a"]) == {ids["a"]: 0.8}


def test_initial_relative_path_still_selects_the_construction_directory(pair):
    active, engines, ids, root, _ = pair
    assert Path(active.db_path).resolve() == (root / "a/graph.db").resolve()
    assert active.carry_vector("session") == {ids["a"]: 0.8}
    assert _members(engines["b"]) == {ids["b"]: 0.25}
