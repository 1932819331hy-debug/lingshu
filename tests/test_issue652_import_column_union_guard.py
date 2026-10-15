# -*- coding: utf-8 -*-
"""#652② `import_all` 列名白名单只取**首行** —— 守卫（lingshu/core/core.py）

缺陷（公开 issue #652 第 ② 条，报告人自陈「代码事实已核实、安全影响未坐实」）：
  旧实现 `keys = list(rows[0].keys())` 只按**首行**的键集合算白名单，
  `cols = [k for k in keys if k in allowed]`，随后 `tuple(r.get(col) for col in cols)`
  逐行取值。两种后果都**静默**发生（值守本机复跑实测）：

  · **首行缺某列** ⇒ 该列整体不进 `cols` ⇒ **后行的真值一并被丢**。
    回灌（`import_all`）是灾备/迁移路径 ⇒ 这是**静默数据丢失**，
    且返回值 `discarded_keys` 为空（调用方读不到任何异常读数）。
  · **后行缺某列** ⇒ 该列在 `cols` 内、`r.get(col)` 落 `None` ⇒ 该行该列
    静默写成 NULL，返回值里**一个字都没有**。

  与 docstring 自陈的「非白名单键丢弃并计数」不完全一致（那条只覆盖「键不在
  白名单」这一种，不覆盖「行缺列」）。

修法（最小 · 不动闸/不动返回体既有键）：
  ① 键集合改按**全部行的并集**算，顺序＝跨行首次出现序（确定性——`_table_columns`
     回的是 set，直接迭代会引入非确定列序）；
  ② 「行缺列」按表按列计数上报新键 `missing_columns`（键为列名、值为缺该列的
     行数），使原先静默的 NULL 落库可观测。

判据来源：公开 issue #652（含报告人给出的修法形状：`keys = set()` 逐行
`update`）；报告人**未构造**可利用后果，本守卫亦**不声称**安全影响——只钉住
「列集合按行并集」与「缺列可观测」两条代码事实。

断言组（抽掉修法即红）：
  G1 后行缺列：合法列照旧导入，且该行该列落 NULL —— 但 `missing_columns`
     必须点名（旧实现在此**一个字都没有**）。
  G2 首行缺列（**静默数据丢失**面）：后行该列真值必须照旧落库；
     旧实现在此把后行真值一并丢掉（`content` 整列为 NULL）且无任何上报。
  G3 对照（防误杀）：键集合齐全的合法备份 ⇒ `missing_columns == {}`、
     `discarded_keys == {}`、往返计数不变。
  G4 键并集口径的确定性：同一备份重复导入 ⇒ `missing_columns` 读数逐字节一致
     （列序不得随 set 迭代漂移）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue652_import_column_union_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_CS = ("{}")


def _node_row(nid, content, importance=0.5, layer="knowledge"):
    return {"id": nid, "content": content, "modality": "text", "tags": "[]",
            "spatial_coordinates": "{}", "temporal_coordinate": time.time(),
            "condition_space": _CS, "importance": importance, "confidence": 0.5,
            "layer": layer, "access_count": 0, "last_access": None,
            "created_at": time.time(), "semantic_coordinates": "{}",
            "state_attributes": "{}", "entity_id": None}


def _write_backup(rows, extra=None):
    data = {"nodes": rows}
    data.update(extra or {})
    path = os.path.join(tempfile.mkdtemp(), "backup.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def _import(rows, extra=None):
    e = SpacetimeMemoryEngine(":memory:")
    return e, e.import_all(_write_backup(rows, extra))


def _row(e, nid):
    c = e.store.conn.cursor()
    c.execute("SELECT id, content, importance FROM nodes WHERE id = ?", (nid,))
    r = c.fetchone()
    return None if r is None else {"id": r[0], "content": r[1], "importance": r[2]}


# --------------------------------------------------------------------------- G1
def test_g1_later_row_missing_column_is_reported():
    """G1：后行缺列 ⇒ 落 NULL 但**必须**在 `missing_columns` 里点名。"""
    rows = [_node_row("k1", "第一行"), _node_row("k2", "第二行")]
    rows[1].pop("importance", None)          # 后行缺列（首行有）
    e, ret = _import(rows)

    got = _row(e, "k2")
    assert got is not None and got["content"] == "第二行", \
        "G1 后行其它列应照旧导入：%r" % (got,)
    assert got["importance"] is None, "G1 前提：缺列确实落 NULL：%r" % (got,)
    miss = ret.get("missing_columns", {}).get("nodes", {})
    assert miss.get("importance") == 1, \
        "G1 缺列未按表按列上报（旧实现在此一个字都没有）：%r" \
        % (ret.get("missing_columns"),)


# --------------------------------------------------------------------------- G2
def test_g2_first_row_missing_column_keeps_later_rows():
    """G2：首行缺列 ⇒ 后行该列**真值不得被吞**（旧实现静默丢整列）。"""
    rows = [_node_row("k1", "第一行"), _node_row("k2", "第二行")]
    rows[0].pop("content", None)             # 首行缺列，后行有真值
    e, ret = _import(rows)

    a, b = _row(e, "k1"), _row(e, "k2")
    assert b is not None and b["content"] == "第二行", \
        "G2 后行 content 真值被首行形状吞掉（静默数据丢失）：%r" % (b,)
    assert a is not None and a["content"] is None, \
        "G2 缺列行自身照旧落 NULL：%r" % (a,)
    miss = ret.get("missing_columns", {}).get("nodes", {})
    assert miss.get("content") == 1, \
        "G2 首行缺列未上报：%r" % (ret.get("missing_columns"),)
    assert ret["imported"]["nodes"] == 2, "G2 行数不应受影响：%r" % (ret["imported"],)


# --------------------------------------------------------------------------- G3
def test_g3_legal_backup_unaffected():
    """G3：防误杀——键集合齐全的合法备份：无缺列、无丢弃、计数不变。"""
    src = SpacetimeMemoryEngine(":memory:")
    src.add_perception("往返测试记忆", skip_dedup=True)
    src.add_perception("第二条记忆", skip_dedup=True)
    path = os.path.join(tempfile.mkdtemp(), "kb.json")
    src.export_all(path)

    dst = SpacetimeMemoryEngine(":memory:")
    ret = dst.import_all(path)
    assert ret["imported"]["nodes"] == 2, "G3 往返节点数不符：%r" % (ret["imported"],)
    assert ret.get("missing_columns") == {}, \
        "G3 合法备份被误报缺列：%r" % (ret.get("missing_columns"),)
    assert ret.get("discarded_keys") == {}, \
        "G3 合法备份被误报丢弃键：%r" % (ret.get("discarded_keys"),)


# --------------------------------------------------------------------------- G4
def test_g4_column_union_order_is_deterministic():
    """G4：键并集口径必须确定性——同备份重复导入，读数逐字节一致。"""
    rows = [_node_row("k1", "第一行"), _node_row("k2", "第二行")]
    rows[0].pop("entity_id", None)
    rows[1].pop("semantic_coordinates", None)
    reads = []
    for _ in range(3):
        _, ret = _import([dict(r) for r in rows])
        reads.append(json.dumps(ret.get("missing_columns"), ensure_ascii=False,
                                sort_keys=True))
    assert len(set(reads)) == 1, "G4 缺列读数不稳定：%r" % (reads,)
    miss = json.loads(reads[0]).get("nodes", {})
    assert miss == {"entity_id": 1, "semantic_coordinates": 1}, \
        "G4 两处缺列都应点名：%r" % (miss,)
