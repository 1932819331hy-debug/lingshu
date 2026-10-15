# -*- coding: utf-8 -*-
"""#691 P0-b `flywheel_reuse` 不进备份 —— 守卫（lingshu/core/core.py）

缺陷（issue #691 · [core/M13]）：
  `flywheel_reuse`（知识飞轮复用观测表）此前只在 `_note_reuse` 里运行时
  `CREATE TABLE IF NOT EXISTS` 建出 ⇒ 它既不在 `_SCHEMA_TABLES` 也不在
  `M13_TABLES`（导出/导入自称「全库备份」的表清单）⇒ **不参与备份、重启即丢**
  跨进程复用观测（心跳进程与对话进程互相看不到复用读数）。

修法：把建表收进 `_init_tables`（构造期即建，与其它自建表同源）并补进
`_SCHEMA_TABLES` / `M13_TABLES` 两处清单。

断言组（把 `flywheel_reuse` 移出两处清单 ⇒ A/B/C 红）：
  A 清单不变量：`flywheel_reuse ∈ M13_TABLES` 且 `∈ _SCHEMA_TABLES`
  B 实证：经真实写入面 `_note_reuse` 落一行 → `export_all` 产物**含该表键且行在场**
  C 往返：export → import 后该表数据存活（不是只进清单）
  D 防误杀：其它既有表仍在清单内、`entities` 仍按 #16 降级

运行（仓根）：python -X utf8 -m pytest tests/test_issue691_flywheel_reuse_backup_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import LayeredStore, SpacetimeMemoryEngine  # noqa: E402


def test_a_清单覆盖该表():
    """A：`flywheel_reuse` 必须在两处备份真源清单里。"""
    assert "flywheel_reuse" in SpacetimeMemoryEngine.M13_TABLES, \
        "#691：flywheel_reuse 未进 M13_TABLES（不参与备份）"
    assert "flywheel_reuse" in LayeredStore._SCHEMA_TABLES, \
        "#691：flywheel_reuse 未进 _SCHEMA_TABLES（引擎自建表全集漏项）"


def test_b_写入后导出产物含该表():
    """B：经真实写入面落一行 ⇒ 导出产物含该表键且行在场。"""
    eng = SpacetimeMemoryEngine(":memory:")
    eng._note_reuse(["node_alpha"])
    rows = eng.store.conn.execute("SELECT COUNT(*) FROM flywheel_reuse").fetchone()[0]
    assert rows >= 1, "B 前置：_note_reuse 未落库"

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "backup.json")
        res = eng.export_all(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    assert "flywheel_reuse" in data, \
        "#691：导出产物缺少 flywheel_reuse 键（不参与备份）"
    assert len(data["flywheel_reuse"]) >= 1, \
        "#691：flywheel_reuse 导出为空：%r" % (data.get("flywheel_reuse"),)
    assert "flywheel_reuse" not in res["skipped_tables"], \
        "#691：flywheel_reuse 被当成缺失表跳过：%r" % (res["skipped_tables"],)


def test_c_往返数据存活(monkeypatch):
    """C：export → import 后 flywheel_reuse 数据存活。"""
    src = SpacetimeMemoryEngine(":memory:")
    src._note_reuse(["node_beta"])
    n = src.store.conn.execute("SELECT COUNT(*) FROM flywheel_reuse").fetchone()[0]
    assert n >= 1, "C 前置：源库应有 flywheel_reuse 行"

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "backup.json")
        src.export_all(path)
        dst = SpacetimeMemoryEngine(":memory:")
        imp = dst.import_all(path)
    assert "flywheel_reuse" in imp["imported"], \
        "#691：回灌未覆盖 flywheel_reuse：%r" % (imp["imported"],)
    got = dst.store.conn.execute("SELECT COUNT(*) FROM flywheel_reuse").fetchone()[0]
    assert got >= 1, "#691：flywheel_reuse 未随备份恢复（重启即丢）"


def test_d_其它表未受扰():
    """D：防误杀——既有表仍在清单内，entities 仍按 #16 降级。"""
    eng = SpacetimeMemoryEngine(":memory:")
    eng.add_perception("探针", skip_dedup=True)
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "backup.json")
        res = eng.export_all(path)
    assert res["skipped_tables"] == ["entities"], \
        "D #16 降级口径退化：%r" % (res["skipped_tables"],)
    for t in ("nodes", "edges", "action_logs", "engine_meta", "gap_history"):
        assert t in SpacetimeMemoryEngine.M13_TABLES, "D 既有表被移出清单：%s" % t
