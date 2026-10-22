"""A detached derived index must not hide persisted memories from live clients."""
import sqlite3

import pytest

from lingshu.core.core import SpacetimeMemoryEngine
from lingshu.core.retrieval import FTSIndex


pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("LINGSHU_FTS", "1")
    monkeypatch.delenv("LINGSHU_TMS", raising=False)
    monkeypatch.delenv("LINGSHU_EVIDENCE_LEDGER", raising=False)
    value = SpacetimeMemoryEngine(str(tmp_path / "fts.db"))
    assert value.store._fts is not None, "validation requires real SQLite FTS5"
    try:
        yield value
    finally:
        value.store.conn.set_trace_callback(None)
        value.close()


@pytest.mark.parametrize("primed", [False, True], ids=["queued", "indexed"])
def test_default_client_does_not_hide_old_or_new_memories(engine, monkeypatch, primed):
    old = engine.add_perception("archived telescope calibration", skip_dedup=True)
    if primed:
        engine.store._fts.sync()
    monkeypatch.delenv("LINGSHU_FTS")
    other = SpacetimeMemoryEngine(engine.store.db_path)
    try:
        assert other.store._fts is None
        new = other.add_perception("fresh telescope signal", skip_dedup=True)
        hits = engine.store.search_content(new.content)
        assert new.id in {n.id for n, _ in hits}, "detached FTS hid a persisted new memory"
        assert old.id in {n.id for n, _ in engine.store.search_content(old.content)}, "detached FTS hid an old memory"
    finally:
        other.close()


def test_partial_tracking_loss_falls_back_for_updated_content(engine):
    node = engine.add_perception("初始频谱记录", skip_dedup=True)
    engine.store._fts.sync()
    engine.store.conn.execute("DROP TRIGGER nodes_fts_au")
    with engine.store._tx():
        engine.store.conn.execute("UPDATE nodes SET content=? WHERE id=?", ("修正星图坐标", node.id))
    hits = engine.store.search_content("修正星图坐标")
    assert node.id in {n.id for n, _ in hits}, "partial FTS tracking loss hid corrected content"
    assert next(n.content for n, _ in hits if n.id == node.id) == "修正星图坐标"


def test_lifecycle_change_during_candidate_read_is_rejected(engine, monkeypatch):
    engine.add_perception("registered telescope baseline", skip_dedup=True)
    index = engine.store._fts
    index.sync()
    fired, errors = [], []

    def trace(sql):
        if sql.startswith("SELECT n.*, f.b AS _b") and not fired:
            fired.append(True)
            try:
                monkeypatch.delenv("LINGSHU_FTS")
                other = SpacetimeMemoryEngine(engine.store.db_path)
                other.close()
            except BaseException as exc:
                errors.append(repr(exc))

    engine.store.conn.set_trace_callback(trace)
    rejected = False
    try:
        index.candidates(["registered telescope baseline"])
    except sqlite3.Error:
        rejected = True
    finally:
        engine.store.conn.set_trace_callback(None)
    assert fired and not errors, errors
    assert rejected, "FTS lifecycle change was accepted as a valid index read"


def test_explicit_reattach_restores_real_index_queries(engine, monkeypatch):
    monkeypatch.delenv("LINGSHU_FTS")
    other = SpacetimeMemoryEngine(engine.store.db_path)
    other.close()
    engine.store._fts = FTSIndex.attach(engine.store)
    assert engine.store._fts is not None
    node = engine.add_perception("reattached telescope observation", skip_dedup=True)
    rows = engine.store._fts.candidates([node.content])
    assert node.id in {row[0] for row, _ in rows}, "explicit FTS reattach did not recover indexed reads"


def test_index_read_preserves_caller_transaction(engine):
    engine.store.conn.execute("BEGIN IMMEDIATE")
    try:
        node = engine.add_perception("pending telescope observation", skip_dedup=True)
        assert node.id in {n.id for n, _ in engine.store.search_content(node.content)}
        assert engine.store.conn.in_transaction, "FTS read committed its caller"
    finally:
        engine.store.conn.rollback()
    assert engine.store.get_node(node.id) is None
