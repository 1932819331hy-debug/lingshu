# -*- coding: utf-8 -*-
"""#494 JSON 列静默吞下孤立 UTF-16 代理 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #494 · [core/JSON 列编码][持久化毒行]）：
  #378 只把「代理码点拒写」落在 `nodes.tags` 一列（`_dumps_tags`，递归深度一层）。
  而 `nodes` 表另有三列是 JSON 文本：`state_attributes` / `semantic_coordinates`
  / `spatial_coordinates`。它们的写点当时仍是**裸** `json.dumps`（默认
  `ensure_ascii=True`）⇒ 孤立代理（unpaired surrogate，如 `"\\ud83d"`）被**静默
  转义**成字面量 `\\ud83d` 文本入库：SQLite 不报错、`json.loads` 往返也不报错，
  于是毒行潜伏；直到读回后再以 `ensure_ascii=False` 序列化写回（`_write_influence`
  / `insight_verify`）或写 UTF-8 文件/日志时才抛
  `UnicodeEncodeError: surrogates not allowed` ⇒ 单条毒行炸掉整条写回/导出链。
  （复现读数：`ingest_frame({"caption":"帧"}, state_hint={"color": LONE})` 落库
   `{"color": "\\ud83d"}`；随后 `recalc_structural_importance(dry_run=False)`
  → `UnicodeEncodeError: ... surrogates not allowed`。）

修法（fail-closed，单一真源 `_dumps_json` / `_reject_surrogates`）：含
U+D800–U+DFFF 任一码点（递归 dict 键值 / list / tuple / set / str）一律
`ValueError`（点名 #494），**不做静默替换**（替换会把污染源洗白、掩盖上游解码
错误）。上述三列的全部写点改经该函数；`ensure_ascii` 缺省保持 `True`（不改存储
编码口径）。

判据来源：经验标定（本件 #494）＋ #378 的既有判据（「含代理码点 ⇒ fail-closed
拒绝，不洗白」）。仓内无规定 JSON 列编码面的理论章节，追不到更早出处。

断言组（抽掉修复＝写点改回裸 `json.dumps` ⇒ A/B/C/D/E/F 中相应者红）：
  A `ingest_frame(state_hint)` 孤立代理 ⇒ `ValueError` 点名 #494，零落库
  B `add_node(state_attributes)` 孤立代理 ⇒ `ValueError`，零落库
  C `add_node(semantic_coordinates)` 孤立代理 ⇒ `ValueError`，零落库
  D `insight_record(source=LONE)` ⇒ `ValueError`（毒值走 `state_attributes`）
  E 存量毒行（直连 SQL 绕过闸）⇒ `insight_verify` 抛点名 #494 的 `ValueError`
    （不是 `UnicodeEncodeError`）
  F 同上存量毒行 ⇒ `_write_influence` 抛点名 #494 的 `ValueError`
  G 防误杀：已配对代理经 `json.loads` 解成单一码点 U+1F600 ⇒ 放行且逐字往返
  H 对照：正常中文值照旧可写可读、`_write_influence` 照旧正常返回

运行（仓根）：python -X utf8 -m pytest tests/test_issue494_state_attributes_surrogate_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import warnings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

warnings.simplefilter("ignore")

from lingshu.core.core import (  # noqa: E402
    STNode, ConditionSpace, MemoryLayer, LayeredStore, SpacetimeMemoryEngine,
)

LONE = "\ud83d"                    # 孤立高位代理（无低位配对）
PAIR = "\ud83d\ude00"              # 两个代理码点（Python str 层面仍是代理）
PAIRED_CHAR = json.loads(json.dumps(PAIR))   # json.loads 合并代理对 ⇒ 单一码点 U+1F600
# 存量毒行的列文本：JSON 转义 `\ud83d`（ASCII 反斜杠-u，非真代理码点）
POISON_JSON = '{"k": "\\ud83d"}'


def _node(nid: str, **kw) -> STNode:
    base = dict(
        id=nid, content="探针节点", modality="text",
        spatial_coordinates={}, temporal_coordinate=0.0,
        condition_space=ConditionSpace("测试", "pytest", (0.0, 0.0), "（未声明）"),
        layer=MemoryLayer.KNOWLEDGE, tags=[],
    )
    base.update(kw)
    return STNode(**base)


def _count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]


# ---------------------------------------------------------------------------
# A：ingest_frame 的 state_hint 孤立代理 ⇒ ValueError 点名 #494，零落库
# ---------------------------------------------------------------------------
def test_a_ingest_frame_state_hint_拒绝孤立代理且零落库():
    e = SpacetimeMemoryEngine(":memory:")
    e.add_perception("基线节点", skip_dedup=True)
    before = _count(e.store.conn)

    try:
        e.ingest_frame({"caption": "帧"}, state_hint={"color": LONE})
        raise AssertionError("A state_hint 孤立代理未被拒（旧缺陷静默入库）")
    except ValueError as ex:
        assert "#494" in str(ex), "A 拒绝理由未点名 #494：%r" % (str(ex),)

    after = _count(e.store.conn)
    assert after == before, "A 被拒帧仍落库：%d -> %d" % (before, after)
    poisoned = e.store.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE state_attributes LIKE '%\\\\ud83d%' ESCAPE '\\'"
    ).fetchone()[0]
    assert poisoned == 0, "A 库内出现转义代理文本（旧缺陷静默入库）：%d" % poisoned


# ---------------------------------------------------------------------------
# B：add_node 的 state_attributes 孤立代理 ⇒ ValueError，零落库
# ---------------------------------------------------------------------------
def test_b_add_node_state_attributes_拒绝孤立代理且零落库():
    store = LayeredStore(":memory:")
    store.add_node(_node("baseline"))
    before = _count(store.conn)

    try:
        store.add_node(_node("poison", state_attributes={"k": LONE}))
        raise AssertionError("B state_attributes 孤立代理未被拒（旧缺陷静默入库）")
    except ValueError as ex:
        assert "#494" in str(ex), "B 拒绝理由未点名 #494：%r" % (str(ex),)

    assert _count(store.conn) == before, "B 被拒节点仍落库"
    assert store.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE state_attributes LIKE '%\\\\ud83d%' ESCAPE '\\'"
    ).fetchone()[0] == 0, "B 库内出现转义代理文本"


# ---------------------------------------------------------------------------
# C：add_node 的 semantic_coordinates 孤立代理 ⇒ ValueError，零落库
# ---------------------------------------------------------------------------
def test_c_add_node_semantic_coordinates_拒绝孤立代理且零落库():
    store = LayeredStore(":memory:")
    store.add_node(_node("baseline"))
    before = _count(store.conn)

    try:
        store.add_node(_node("poison", semantic_coordinates={"x": LONE}))
        raise AssertionError("C semantic_coordinates 孤立代理未被拒（旧缺陷静默入库）")
    except ValueError as ex:
        assert "#494" in str(ex), "C 拒绝理由未点名 #494：%r" % (str(ex),)

    assert _count(store.conn) == before, "C 被拒节点仍落库"
    assert store.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE semantic_coordinates LIKE '%\\\\ud83d%' ESCAPE '\\'"
    ).fetchone()[0] == 0, "C 库内出现转义代理文本"


# ---------------------------------------------------------------------------
# D：insight_record 的 source 孤立代理（经 state_attributes 走 to_row）⇒ ValueError
# ---------------------------------------------------------------------------
def test_d_insight_record_source_拒绝孤立代理():
    store = LayeredStore(":memory:")
    try:
        store.insight_record("探针洞见", source=LONE)
        raise AssertionError("D source 孤立代理未被拒（旧缺陷静默入库）")
    except ValueError as ex:
        assert "#494" in str(ex), "D 拒绝理由未点名 #494：%r" % (str(ex),)


# ---------------------------------------------------------------------------
# E：存量毒行 ⇒ insight_verify 抛点名 #494 的 ValueError（非 UnicodeEncodeError）
# ---------------------------------------------------------------------------
def test_e_insight_verify_存量毒行抛494():
    store = LayeredStore(":memory:")
    nid = store.insight_record("探针洞见", source="clean")["node_id"]
    # 直连 SQL 绕过闸写入毒行（模拟 #494 修复前落库的历史数据）
    store.conn.execute(
        "UPDATE nodes SET state_attributes=? WHERE id=?", (POISON_JSON, nid))
    store.conn.commit()

    try:
        store.insight_verify(nid, "V2", "ev1")
        raise AssertionError("E 存量毒行未被 insight_verify 拒绝")
    except ValueError as ex:
        assert "#494" in str(ex), "E 拒绝理由未点名 #494：%r" % (str(ex),)
    except UnicodeEncodeError as ex:  # pragma: no cover - 旧缺陷行为
        raise AssertionError("E 仍是旧缺陷的 UnicodeEncodeError：%r" % (str(ex),))


# ---------------------------------------------------------------------------
# F：存量毒行 ⇒ _write_influence 抛点名 #494 的 ValueError
# ---------------------------------------------------------------------------
def test_f_write_influence_存量毒行抛494():
    store = LayeredStore(":memory:")
    store.add_node(_node("poison_target"))
    store.conn.execute(
        "UPDATE nodes SET state_attributes=? WHERE id=?", (POISON_JSON, "poison_target"))
    store.conn.commit()

    try:
        store._write_influence("poison_target", 0.5)
        raise AssertionError("F 存量毒行未被 _write_influence 拒绝")
    except ValueError as ex:
        assert "#494" in str(ex), "F 拒绝理由未点名 #494：%r" % (str(ex),)
    except UnicodeEncodeError as ex:  # pragma: no cover - 旧缺陷行为
        raise AssertionError("F 仍是旧缺陷的 UnicodeEncodeError：%r" % (str(ex),))


# ---------------------------------------------------------------------------
# G：防误杀——已配对代理经 json.loads 解成单一码点 U+1F600 ⇒ 放行且逐字往返
# ---------------------------------------------------------------------------
def test_g_已配对代理解成单一码点放行且逐字往返():
    assert PAIRED_CHAR == "\U0001F600", "G 前置：json.loads 未把代理对合并成单一码点"
    store = LayeredStore(":memory:")
    sa = {"k": PAIRED_CHAR, "中文": "红"}
    store.add_node(_node("paired", state_attributes=sa))
    col = store.conn.execute(
        "SELECT state_attributes FROM nodes WHERE id='paired'").fetchone()[0]
    assert json.loads(col) == sa, "G 单一码点值往返不一致：%r" % (col,)
    col.encode("utf-8")  # 单一码点必须可 UTF-8 编码


# ---------------------------------------------------------------------------
# H：对照——正常中文值照旧可写可读、_write_influence 照旧正常返回
# ---------------------------------------------------------------------------
def test_h_正常值照旧可写可读():
    e = SpacetimeMemoryEngine(":memory:")
    n = e.ingest_frame({"caption": "帧"}, state_hint={"color": "红"})
    col = e.store.conn.execute(
        "SELECT state_attributes FROM nodes WHERE id=?", (n.id,)).fetchone()[0]
    assert json.loads(col) == {"color": "红"}, "H 正常中文值往返不一致：%r" % (col,)

    e.store._write_influence(n.id, 0.5)  # 照旧正常返回（不抛）
    col2 = e.store.conn.execute(
        "SELECT state_attributes FROM nodes WHERE id=?", (n.id,)).fetchone()[0]
    assert json.loads(col2)["concept_influence"] == 0.5, "H 结构量未写入：%r" % (col2,)
