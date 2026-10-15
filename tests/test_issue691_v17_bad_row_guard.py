# -*- coding: utf-8 -*-
"""#691 P1-c 坏 JSON 行崩构造期 —— 守卫（lingshu/core/core.py）

缺陷（issue #691 · [core/启动·迁移]）：
  `migrate_v17_coordinates` 的前置筛只挡「不含候选子串」的坏行；只要存在 ≥1 条
  合法候选，就会走全表 `SELECT` + 逐行 `json.loads` ⇒ **任一共存的坏 JSON 行**
  都会让构造期抛 `JSONDecodeError`、引擎起不来（前置筛自身对「含候选子串但 JSON
  非法」的行同样会崩）。

修法：两处 `json.loads` 逐行 try/except 具体异常（`ValueError`/`TypeError`），
坏行跳过并计入读数 `skipped_bad_rows`（显式上报，不静默吞）。未新增宽
`except Exception`；迁移判据与阈值未动。

断言组（回退逐行容错 ⇒ A/C 红）：
  A 坏行共存时**重开引擎不崩**（旧：JSONDecodeError）
  B 合法候选行照旧迁移（容错未把迁移面收死）
  C 读数：坏行被计数上报（`skipped_bad_rows >= 1`），非静默吞
  D 坏行原文未被改写（跳过＝不动它）

运行（仓根）：python -X utf8 -m pytest tests/test_issue691_v17_bad_row_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _insert_raw(eng, nid, spatial, semantic="{}"):
    eng.store.conn.execute(
        "INSERT INTO nodes (id, content, layer, spatial_coordinates, semantic_coordinates) "
        "VALUES (?,?,?,?,?)",
        (nid, "x", "knowledge", spatial, semantic))
    eng.store.conn.commit()


def _db(tmp_path):
    return os.path.join(str(tmp_path), "691c.db")


def test_a_bad_row_coexists_engine_starts(tmp_path):
    """A：1 条合法候选 + 1 条坏 JSON 行 ⇒ 重开引擎不得崩。"""
    path = _db(tmp_path)
    eng = SpacetimeMemoryEngine(path)
    _insert_raw(eng, "good1", json.dumps({"protocol_x": 1}))
    _insert_raw(eng, "bad1", "{not valid json")
    eng.store.close()
    fresh = SpacetimeMemoryEngine(path)      # 旧实现：此处 JSONDecodeError
    assert fresh is not None


def test_b_valid_candidate_still_migrated(tmp_path):
    """B：合法候选行照旧迁移（容错未把迁移面收死）。"""
    path = _db(tmp_path)
    eng = SpacetimeMemoryEngine(path)
    _insert_raw(eng, "good2", json.dumps({"protocol_delta": 9, "visual": [1]}))
    _insert_raw(eng, "bad2", "{oops")
    eng.store.close()
    fresh = SpacetimeMemoryEngine(path)
    sp = json.loads(fresh.store.conn.execute(
        "SELECT spatial_coordinates FROM nodes WHERE id='good2'").fetchone()[0])
    se = json.loads(fresh.store.conn.execute(
        "SELECT semantic_coordinates FROM nodes WHERE id='good2'").fetchone()[0])
    assert sp == {"visual": [1]}, "B 合法候选语义键未摘除：%r" % (sp,)
    assert se["protocol"]["concept"] == {"delta": 9}, "B 合法候选未迁移：%r" % (se,)


def test_c_bad_row_counted_not_silent(tmp_path):
    """C：坏行被上报（经既有 `warnings.warn` 告警面），非静默吞。"""
    path = _db(tmp_path)
    eng = SpacetimeMemoryEngine(path)
    _insert_raw(eng, "good3", json.dumps({"radical_eps": 2}))
    _insert_raw(eng, "bad3", "{broken")
    with pytest.warns(RuntimeWarning, match=r"#691"):
        ret = eng.migrate_v17_coordinates()
    assert ret["migrated_nodes"] >= 1, "C 前置：合法候选未迁移：%r" % (ret,)


def test_d_bad_row_text_untouched(tmp_path):
    """D：坏行原文未被改写（跳过＝不动它）。"""
    path = _db(tmp_path)
    eng = SpacetimeMemoryEngine(path)
    _insert_raw(eng, "good4", json.dumps({"neural_zeta": 3}))
    _insert_raw(eng, "bad4", "{still bad")
    eng.store.close()
    fresh = SpacetimeMemoryEngine(path)
    got = fresh.store.conn.execute(
        "SELECT spatial_coordinates FROM nodes WHERE id='bad4'").fetchone()[0]
    assert got == "{still bad", "D 坏行被改写：%r" % (got,)
