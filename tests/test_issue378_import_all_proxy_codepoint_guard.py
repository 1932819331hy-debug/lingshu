# -*- coding: utf-8 -*-
"""#378 回灌面 —— `import_all` 的 `tags` 列绕过代理码点拒写闸（守卫 · lingshu/core/core.py）

缺陷（lingshu issue #378 · 外部报告人 @hawkongz 2026-10-12 追加评论）：
  写侧 `add_perception` / `add_node` / `tag_node` 的 `tags` 列已过 `_dumps_tags` 的
  代理码点判据（含 U+D800–U+DFFF ⇒ `ValueError`，fail-closed）⇒ 公开写侧不再产出毒行。
  但 `import_all` 的**回灌面**绕过该判据：备份里 `tags` 列是**转义后的 ASCII 文本**
  （存量行形态即字面量 `\\ud83d`），经 `json.loads` 回来仍是**含真代理码点的 str**，
  再经 `_dumps_tags`（或 #721 归一）写回 ⇒ 毒行**重新造出**。实测（本端复跑，改前）：
  `import_all` 无异常、`integrity_ok=true`、回灌后 `tags` 列含转义代理、库仍可继续写入
  （**零信号**）。
  该毒行随后让 `longterm_gate.write_snapshot` 的「已存在分支」UPDATE 抛
  `UnicodeEncodeError` ⇒ 该行三列全未变（写入失效）。
  ⇒ `export_all → import_all` 是仓内**自带的灾备/迁移路径**，回灌面把「存量行只存在于
  本库、不可经备份传播」这一隐含前提推翻。

修法（取向＝**拒收**，与写侧同口径、单一真源）：`import_all` 落库前**整批**判 `nodes`
行的 `tags` 列——经 `_json_tags` 归一为 list（JSON 文本 / 真 list / None 三形态）后过
`_dumps_tags` 的**同一判据**（只借其拒绝判据、丢弃返回值，**不**手写第二份代理扫描）；
带代理码点 ⇒ `ValueError`（点名行 id）、fail-closed、零落库。
为什么取「拒收」而非「清洗后收」：①`_dumps_tags` 判据明写「**不**做静默替换（替换会
把污染源洗白、掩盖上游解码错误）」；②与本方法既有整批判闸（#586 共享层行 / #478 双列层闸
/ #771 ②③ 换层与 content 类型闸）同形；③合法备份零影响（合法往返逐位不变）。

断言组（抽掉修复＝`bad_tags` 闸整段移除 ⇒ B/C 红；A/D/E/F 为对照与既有面回归钉）：
  A 写侧对照：`add_perception(tags=[孤立代理])` 仍 `ValueError` 点名 #378，且零落库。
  B 回灌面（JSON 文本转义形态，报告人夹具）：备份 `tags` = `'["正常标签", "\\ud83d"]'`
    ⇒ `import_all` 抛 `ValueError`（点名 #378 与行 id），且**零落库**（目标库逐字不变）。
  C 回灌面（真 list 容器形态）：备份 `tags` = `["正常标签", 孤立代理]`（#721 会归一）
    ⇒ 同样拒（判据在归一之前，两种形态同闸）。
  D 防过修：合法 `export_all → import_all` 往返逐位不变（id/content/tags/importance/
    confidence/layer 全等）。
  E 不抢既有面：缺 `tags` 键的行（#652② `missing_columns` 面）与 `tags=None` 的行
    （#718「视为无该标记」面）照旧落库，不被本闸误杀。
  F 因果链回归钉（**不随本件回退转红**）：存量毒行（直改库造出，写侧已拒）下
    `write_snapshot` 的失败在**返回值上可观测**（`write_failed is True` 或
    `write_error` 非 None）——该半已由 #292（`286b428`）收口，本件只钉住它不回退。

运行（仓根）：python -X utf8 -m pytest tests/test_issue378_import_all_proxy_codepoint_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import (  # noqa: E402
    ConditionSpace, SpacetimeMemoryEngine,
)
from lingshu.core.longterm_gate import LongTermMemoryGate  # noqa: E402

LONE = "\ud83d"          # 孤立高位代理（无低位配对）
_CS = ConditionSpace("守卫", "守卫", (time.time(), time.time() + 3600), "运行中").to_json()


def _node_row(nid, tags_value, content="回灌面探针节点内容 ABCDEF", layer="knowledge"):
    """构造一条备份 `nodes` 行（列形状与 `export_all` 产物同）。"""
    return {"id": nid, "content": content, "modality": "text", "tags": tags_value,
            "spatial_coordinates": "{}", "temporal_coordinate": 0.0,
            "condition_space": _CS, "importance": 0.5, "confidence": 0.5,
            "layer": layer, "access_count": 0, "last_access": None,
            "created_at": 0.0, "semantic_coordinates": "{}",
            "state_attributes": "{}", "entity_id": None}


def _write_backup(nodes):
    # `ensure_ascii=True`：这是**备份里可达的**形态——代理码点不可能以原文写进
    # UTF-8 文件（写盘即 `UnicodeEncodeError`），故备份 JSON 里一律是转义文本。
    path = os.path.join(tempfile.mkdtemp(prefix="p378g_"), "backup.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"meta": {"version": "v1.6"}, "nodes": nodes}, f, ensure_ascii=True)
    return path


def _snapshot(e):
    return [tuple(r) for r in e.store.conn.execute(
        "SELECT id, content, tags, importance, confidence, layer FROM nodes ORDER BY id")]


def test_a_写侧对照仍拒绝孤立代理标签():
    """A：写侧 fail-closed 未被本件改动（对照）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.add_perception("写侧对照内容", tags=[LONE])
    except ValueError as exc:
        assert "#378" in str(exc), str(exc)
    else:
        raise AssertionError("写侧 `add_perception(tags=[孤立代理])` 应 fail-closed")
    assert e.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0


def test_b_回灌转义形态毒tags行被拒且零落库():
    """B：报告人夹具（备份 tags 列 = ensure_ascii=True 存量转义形态）⇒ 拒且零落库。"""
    e = SpacetimeMemoryEngine(":memory:")
    e.add_perception("既有行内容 XYZ", tags=["正常"])
    before = _snapshot(e)
    path = _write_backup([_node_row("poison_json", json.dumps(["正常标签", LONE]))])
    try:
        e.import_all(path)
    except ValueError as exc:
        assert "#378" in str(exc), str(exc)
        assert "poison_json" in str(exc), str(exc)
    else:
        raise AssertionError("回灌带毒 tags 的备份应 fail-closed（旧缺陷＝无异常落库）")
    assert _snapshot(e) == before, "被拒后必须零落库"


def test_c_回灌真list形态毒tags同样被拒():
    """C：备份 tags 为真 list 容器（#721 会归一）⇒ 同一闸拦下。"""
    e = SpacetimeMemoryEngine(":memory:")
    path = _write_backup([_node_row("poison_list", ["正常标签", LONE])])
    try:
        e.import_all(path)
    except ValueError as exc:
        assert "#378" in str(exc), str(exc)
        assert "poison_list" in str(exc), str(exc)
    else:
        raise AssertionError("真 list 形态的毒 tags 应同闸拒绝")
    assert e.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0


def test_d_合法往返逐位不变():
    """D：防过修 —— 合法 `export_all → import_all` 往返逐位不变。"""
    src = SpacetimeMemoryEngine(":memory:")
    src.add_perception("往返探针内容 UVWXYZ", tags=["中文标签", "ascii"])
    path = os.path.join(tempfile.mkdtemp(prefix="p378rt_"), "clean.json")
    src.export_all(path)
    dst = SpacetimeMemoryEngine(":memory:")
    res = dst.import_all(path)
    assert res["integrity_ok"] is True, res
    assert _snapshot(dst) == _snapshot(src)


def test_e_缺tags键与None不被误杀():
    """E：不抢 #652②（缺键 ⇒ missing_columns）与 #718（None ⇒ 无该标记）两面。"""
    e = SpacetimeMemoryEngine(":memory:")
    row_no_key = _node_row("no_tags_key", "[]")
    del row_no_key["tags"]
    res1 = e.import_all(_write_backup([row_no_key]))
    assert res1["imported"]["nodes"] == 1, res1
    assert "no_tags_key" in {r[0] for r in e.store.conn.execute("SELECT id FROM nodes")}
    res2 = e.import_all(_write_backup([_node_row("tags_none", None)]))
    assert res2["imported"]["nodes"] == 1, res2
    got = e.store.conn.execute(
        "SELECT tags FROM nodes WHERE id='tags_none'").fetchone()[0]
    assert got is None, repr(got)


def test_f_存量毒行下write_snapshot失败可观测():
    """F：因果链回归钉（#292 已收口的那半）——毒行下失败不得静默。"""
    e = SpacetimeMemoryEngine(":memory:")
    e.add_perception("存量毒行探针内容 ABCDEF", tags=["正常标签"], importance=0.5)
    nid = e.store.conn.execute("SELECT id FROM nodes LIMIT 1").fetchone()[0]
    # 直改库造出存量毒行（写侧已 fail-closed ⇒ 只能这样模拟修复前落库的行）。
    e.store.conn.execute("UPDATE nodes SET tags=? WHERE id=?",
                         (json.dumps(["正常标签", LONE]), nid))
    e.store.conn.commit()
    gate = LongTermMemoryGate(e)
    res = gate.write_snapshot("存量毒行探针内容 ABCDEF", source="guard",
                              importance_hint=0.5)
    assert res.get("write_failed") is True or res.get("write_error") is not None, res
