# -*- coding: utf-8 -*-
"""#588 + #594 类型闸只加在 add_perception —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #588 · [core/写入][数据损坏][P0边界]，与 #594 同族）：
  #331 的 fail-closed 类型闸**只加在 `add_perception` 一个入口**
  （core.py:4953 起「拒绝非字符串 content」）。其余直接构造 `STNode(content=…)`
  入库的入口**全部绕闸**，bytes 以 **BLOB** 静默落进 `nodes.content`
  （SQLite TEXT 列不做类型转换）：
    - `add_context(content=bytes)`        （#588 点名）
    - `ingest_frame({"caption": bytes})`  （#588 点名）
    - `set_anchor(content=bytes)`         （#594 点名 · 不可变层）
    - `add_structure_node(content=bytes)` （#594 点名 · 不可变层）
  一行 BLOB 毒化全局：`search_content` 抛
  `TypeError: sequence item 0: expected str instance, bytes found`；
  `export_all` 抛 `Object of type bytes is not JSON serializable`
  ⇒ **M13 备份做不出来**。

修法（维护者在 #588 值守帖里点名的方向）：把类型闸**下沉到公共写入咽喉
`LayeredStore.add_node`**（一处覆盖全部入口），而不是逐个入口补。闸 fail-closed、
**先判后写**：非 `str` 一律 `TypeError`，判在任何 SQL / 不可变层写入之前；
空串 `""` 照旧放行（既有语义）；**不**做 `str()` 静默强转。

判据来源：经验标定（#331 已定口径「非 str ⇒ fail-closed 拒绝」）+
#588/#594 值守帖「闸下沉 add_node、一处覆盖」的修复面锚。

断言组（抽掉修复＝把 add_node 的闸去掉 ⇒ A/B/C 红；只留 add_perception 的闸 ⇒ A 红）：
  A 四类旁路入口逐入口 pass bytes ⇒ `TypeError`（点名 #588 / #594）
  B 拒绝后**零落库**：四入口各拒绝一次后库内仍无 blob 行
  C 下游不再被毒化：`search_content` 与 `export_all` 均可用
  D 防误杀：str 照旧经四入口入库（含空串 —— 既有语义不得被本闸收掉）

运行（仓根）：
  python -X utf8 -m pytest tests/test_issue588_594_content_type_gate_sink.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine():
    eng = SpacetimeMemoryEngine(":memory:")
    return eng


def _blob_rows(store) -> int:
    """库内 content 为 BLOB（非 TEXT）的行数——0 才是干净的。"""
    return store.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE typeof(content)='blob'"
    ).fetchone()[0]


def test_a_四类旁路入口逐入口拒绝():
    """A：#588 两入口 + #594 两入口，bytes 一律 TypeError（点名件号）。"""
    eng = _engine()
    cases = [
        ("#588 add_context", lambda: eng.add_context(b"z")),
        ("#588 ingest_frame", lambda: eng.ingest_frame({"caption": b"w"})),
        ("#594 set_anchor", lambda: eng.set_anchor(b"a")),
        ("#594 add_structure_node", lambda: eng.add_structure_node(b"s")),
    ]
    for name, call in cases:
        try:
            call()
        except TypeError as e:
            issue = name.split()[0]
            assert issue in str(e), (
                "A %s 拒绝理由未点名 %s：%r" % (name, issue, str(e))
            )
        else:
            raise AssertionError("A %s 未被拒（旧缺陷行为：绕闸落 BLOB）" % name)


def test_a2_正对照_331闸仍在():
    """A2：量具自证——#331 的 add_perception 闸必须仍在场。

    保证本守卫不是「因为整个写入面都炸了」而假绿。
    """
    eng = _engine()
    try:
        eng.add_perception(b"x", skip_dedup=True)
        raise AssertionError("A2 #331 闸消失（正对照失败，守卫读数不可信）")
    except TypeError as e:
        assert "#331" in str(e), "A2 #331 拒绝理由未点名：#r" % (str(e),)


def test_b_拒绝后零落库():
    """B：拒绝必须**先判后写**——四入口各拒一次后仍无 blob 行。"""
    eng = _engine()
    eng.add_perception("正常基线记忆", skip_dedup=True)
    before = _blob_rows(eng.store)
    assert before == 0, "B 前置条件失败：基线就不干净（%d blob 行）" % before

    for call in (
        lambda: eng.add_context(b"z"),
        lambda: eng.ingest_frame({"caption": b"w"}),
        lambda: eng.set_anchor(b"a"),
        lambda: eng.add_structure_node(b"s"),
    ):
        try:
            call()
        except TypeError:
            pass

    after = _blob_rows(eng.store)
    assert after == 0, "B 拒绝后仍落了 %d 行 BLOB（闸在写之后）" % after


def test_c_下游不再被毒化():
    """C：库干净 ⇒ 检索与备份都可用（旧缺陷：一行 BLOB 毒化全局）。"""
    import tempfile

    eng = _engine()
    eng.add_perception("干净记忆内容", skip_dedup=True)

    # 四个入口全部尝试毒化（均被拒）
    for call in (
        lambda: eng.add_context(b"z"),
        lambda: eng.ingest_frame({"caption": b"w"}),
        lambda: eng.set_anchor(b"a"),
        lambda: eng.add_structure_node(b"s"),
    ):
        try:
            call()
        except TypeError:
            pass

    # 检索面可用
    hits = eng.store.search_content("干净")
    assert isinstance(hits, list), "C search_content 未返回列表"

    # 备份面可用（M13）
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "dump.json")
        res = eng.export_all(out)
        assert isinstance(res, dict), "C export_all 未返回 dict"


def test_d_防误杀_str照旧入库():
    """D：str（含空串）经四入口照旧放行——既有语义不得被本闸收掉。"""
    eng = _engine()
    eng.add_context("上下文正文")
    eng.ingest_frame({"caption": "帧说明"})
    eng.set_anchor("锚点正文")
    eng.add_structure_node("结构正文")

    n = eng.store.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE typeof(content)='text'"
    ).fetchone()[0]
    assert n >= 4, "D str 正文被误杀：仅 %d 行 TEXT" % n

    # 空串单独确认（#331 既有语义："" 放行，不被本闸收掉）
    eng.add_context("")
    n_empty = eng.store.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE typeof(content)='text' AND content=''"
    ).fetchone()[0]
    assert n_empty >= 1, "D 空串被误杀（#331 既有语义要求放行）"
