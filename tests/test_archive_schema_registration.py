"""归档登记表必须同时进入旧库迁移、完整备份与角色检查。"""
from contextlib import closing
import json
import sqlite3

import pytest

from lingshu.core.core import LayeredStore, Role, SpacetimeMemoryEngine


pytestmark = pytest.mark.filterwarnings("ignore")


def _drop_archive_table(path, version):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE archive_state")
        conn.execute("UPDATE engine_meta SET value=? WHERE key='_schema_version'", (str(version),))
        conn.commit()


def _has_archive_table(engine):
    return engine.store.conn.execute(
        "SELECT name FROM sqlite_master WHERE name='archive_state'").fetchone() is not None


def _seed(path):
    engine = SpacetimeMemoryEngine(str(path))
    try:
        return engine.add_context("retained old node", importance=0.15).id
    finally:
        engine.close()


def test_v3_database_is_migrated_before_normal_delete(tmp_path):
    path = tmp_path / "v3.db"
    nid = _seed(path)
    _drop_archive_table(path, 3)
    engine = SpacetimeMemoryEngine(str(path))
    try:
        assert _has_archive_table(engine), "archive_state missing after reopening a v3 database"
        assert engine.store.delete_node(nid)
    finally:
        engine.close()


def test_current_version_with_missing_table_is_repaired(tmp_path):
    path = tmp_path / "incomplete.db"
    _seed(path)
    _drop_archive_table(path, LayeredStore.SCHEMA_VERSION)
    engine = SpacetimeMemoryEngine(str(path))
    try:
        assert _has_archive_table(engine), "archive_state missing after structural migration check"
    finally:
        engine.close()


def test_backup_roundtrip_preserves_pre_archive_importance(tmp_path):
    source = SpacetimeMemoryEngine(str(tmp_path / "source.db"))
    target = SpacetimeMemoryEngine(str(tmp_path / "target.db"))
    try:
        nid = source.add_context("archive and restore", importance=0.15).id
        assert source.forget_advisor(stale_days=0, low_value=0.2)["archived"] == 1
        path = tmp_path / "backup.json"
        source.export_all(str(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("archive_state"), "backup omitted the pre-archive restoration state"
        target.import_all(str(path))
        assert target.store.restore_archived(nid)
        assert target.store.get_node(nid).importance == pytest.approx(0.15)
    finally:
        target.close()
        source.close()


def test_shared_archive_state_cannot_be_imported_by_sub(tmp_path):
    path = tmp_path / "shared.db"
    primary = SpacetimeMemoryEngine(str(path))
    sub = None
    try:
        anchor = primary.set_anchor("shared archive target")
        primary.store.tag_node(anchor.id, "archived")
        sub = SpacetimeMemoryEngine(str(path), role=Role.SUB)
        payload = tmp_path / "shared-state.json"
        payload.write_text(json.dumps({"archive_state": [{"node_id": anchor.id,
            "pre_archive_importance": 0.9, "archived_at": 1.0}]}), encoding="utf-8")
        try:
            sub.import_all(str(payload))
        except PermissionError:
            pass
        row = primary.store.conn.execute(
            "SELECT * FROM archive_state WHERE node_id=?", (anchor.id,)).fetchone()
        assert row is None, "SUB import wrote shared archive state"
    finally:
        if sub is not None:
            sub.close()
        primary.close()


def test_legacy_backup_without_archive_state_keeps_restore_fallback(tmp_path):
    source = SpacetimeMemoryEngine(str(tmp_path / "source.db"))
    target = SpacetimeMemoryEngine(str(tmp_path / "target.db"))
    try:
        nid = source.add_context("legacy archive", importance=0.15).id
        source.forget_advisor(stale_days=0, low_value=0.2)
        path = tmp_path / "old-backup.json"
        source.export_all(str(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("archive_state", None)
        path.write_text(json.dumps(data), encoding="utf-8")
        target.import_all(str(path))
        assert target.store.restore_archived(nid)
        assert target.store.get_node(nid).importance == pytest.approx(0.1)
    finally:
        target.close()
        source.close()


def test_reopening_a_complete_database_keeps_archive_rows(tmp_path):
    path = tmp_path / "current.db"
    first = SpacetimeMemoryEngine(str(path))
    nid = first.add_context("current archive", importance=0.15).id
    first.forget_advisor(stale_days=0, low_value=0.2)
    before = tuple(first.store.conn.execute("SELECT * FROM archive_state WHERE node_id=?", (nid,)).fetchone())
    first.close()
    second = SpacetimeMemoryEngine(str(path))
    try:
        after = tuple(second.store.conn.execute("SELECT * FROM archive_state WHERE node_id=?", (nid,)).fetchone())
        assert after == before
        assert second.store.restore_archived(nid)
        assert second.store.get_node(nid).importance == pytest.approx(0.15)
    finally:
        second.close()
