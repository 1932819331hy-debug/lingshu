"""ConnectionLibrary's saved knowledge survives a failed publication."""
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid

import pytest
from lingshu.nn.hex_query import ConnectionLibrary
import lingshu.nn.hex_query as query


def library(name='original'):
    lib = ConnectionLibrary()
    lib.add_record(name, [{'type': 'circle', 'color': 'red', 'zone': '*'}])
    return lib


def existing(tmp_path):
    target = tmp_path / 'library.json'
    lib = library()
    lib.save(str(target))
    return target, target.read_bytes()


def test_serialization_failure_preserves_existing_library(tmp_path):
    target, old = existing(tmp_path)
    lib = library('replacement')
    lib.add_record('bad', [], {'value': object()})
    with pytest.raises(TypeError):
        lib.save(str(target))
    assert target.read_bytes() == old, 'failed serialization destroyed the previous connection library'
    assert ConnectionLibrary.load(str(target)).records[0]['name'] == 'original'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['library.json']


def test_failed_new_save_leaves_no_target(tmp_path):
    target = tmp_path / 'new.json'
    lib = library()
    lib.records[0]['tags']['invalid'] = object()
    with pytest.raises(TypeError):
        lib.save(str(target))
    assert not target.exists(), 'failed first save published a partial library'
    assert not list(tmp_path.iterdir())


def test_write_failure_preserves_existing_bytes(tmp_path, monkeypatch):
    target, old = existing(tmp_path)
    def broken_dump(payload, stream, **kwargs):
        stream.write('[{"unfinished":')
        raise OSError('controlled interrupted write')
    with monkeypatch.context() as patch:
        patch.setattr(query.json, 'dump', broken_dump)
        with pytest.raises(OSError, match='controlled interrupted write'):
            library('replacement').save(str(target))
    assert target.read_bytes() == old, 'interrupted write changed the published library'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['library.json']


def test_replace_failure_keeps_old_file_and_cleans_owned_temp(tmp_path, monkeypatch):
    target, old = existing(tmp_path)
    def denied(source, dest):
        assert Path(source).parent == target.parent
        assert json.loads(Path(source).read_text(encoding='utf-8'))[0]['name'] == 'replacement'
        raise PermissionError('controlled publication denial')
    with monkeypatch.context() as patch:
        patch.setattr(query.os, 'replace', denied)
        with pytest.raises(PermissionError, match='controlled publication denial'):
            library('replacement').save(str(target))
    assert target.read_bytes() == old, 'failed replace changed the published library'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['library.json']


def test_exclusive_name_collision_never_consumes_another_file(tmp_path, monkeypatch):
    target, old = existing(tmp_path)
    candidate = tmp_path / f'.lingshu-query.tmp-{os.getpid()}-collision'
    candidate.write_bytes(b'another save owns this')
    with monkeypatch.context() as patch:
        patch.setattr(uuid, 'uuid4', lambda: SimpleNamespace(hex='collision'))
        with pytest.raises(FileExistsError):
            library('replacement').save(str(target))
    assert candidate.read_bytes() == b'another save owns this', 'save consumed a temporary file it did not create'
    assert target.read_bytes() == old


def test_successful_relative_save_preserves_return_and_contents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lib = library('连接知识')
    assert lib.save('local.json') is None
    assert ConnectionLibrary.load('local.json').records == lib.records
    assert sorted(p.name for p in tmp_path.iterdir()) == ['local.json']


def test_replacing_a_restricted_file_preserves_mode(tmp_path):
    target, _ = existing(tmp_path)
    os.chmod(target, 0o600)
    mode = target.stat().st_mode & 0o7777
    library('replacement').save(str(target))
    assert target.stat().st_mode & 0o7777 == mode
    assert ConnectionLibrary.load(str(target)).records[0]['name'] == 'replacement'


def test_concurrent_saves_publish_only_complete_owned_files(tmp_path, monkeypatch):
    target, old = existing(tmp_path)
    ready, release = threading.Event(), threading.Event()
    real_replace = os.replace
    outcomes = []
    def ordered_replace(source, dest):
        if threading.current_thread().name == 'first-library-save':
            ready.set()
            assert release.wait(8), 'concurrent save was not released'
        return real_replace(source, dest)
    def first():
        try:
            library('first').save(str(target))
        except BaseException as exc:
            outcomes.append(exc)
    with monkeypatch.context() as patch:
        patch.setattr(query.os, 'replace', ordered_replace)
        worker = threading.Thread(target=first, name='first-library-save', daemon=True)
        worker.start()
        try:
            assert ready.wait(3), 'save never reached atomic publication'
            assert target.read_bytes() == old, 'unpublished save changed the old library'
            library('second').save(str(target))
            assert ConnectionLibrary.load(str(target)).records[0]['name'] == 'second'
        finally:
            release.set()
            worker.join(8)
    assert not worker.is_alive() and not outcomes, 'concurrent save failed or remained blocked'
    assert ConnectionLibrary.load(str(target)).records[0]['name'] == 'first'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['library.json']
