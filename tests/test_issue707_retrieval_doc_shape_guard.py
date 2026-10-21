# -*- coding: utf-8 -*-
"""test_issue707_retrieval_doc_shape_guard · `retrieval._doc` 的 tags 形状缺口（#707，P0）

判据来源
--------
issue #707：`_doc` 的 `try/except` 只包住 `json.loads`，`for t in tags` / `" ".join(...)`
落在保护之外 ⇒ **合法 JSON 的非数组形状**（`null` / `5` / `true` / 对象）稳定抛
`TypeError: ... object is not iterable`，而**非法 JSON** 反而被正确降级（`except` 兜住）。
缺口精确落在「非数组」与「非 JSON」之间。

实现侧口径（为什么这样判）
--------------------------
同文件姊妹函数 `provenance._tags`（`lingshu/core/provenance.py`）已按正确口径修过同族
（#342）：① JSON 字符串标量按**单个标签**处理（不按字符拆）；② 其余非 `list/tuple`
形状一律回落空表。本守卫按**同一口径**判 `_doc`，故与实现不重复表述、也不放宽。

影响面为什么是「整库」而非「该行」
--------------------------------
异常穿透 `sync()`（无 try）与 `attach()`（只接 `sqlite3.OperationalError`），而
`candidates()` 每次调用内部先 `sync()` ⇒ 一条坏行让全部检索（含只含良性词的查询）失效；
`attach()` 侧在 `core.py` 构造期被宽 except 降级为 `_fts=None`，FTS 永久失效。

判别力（回退即红）
------------------
把 `_doc` 的 isinstance 形状校验删掉（回退到缺陷形态）⇒
`test_doc_tolerates_legal_json_non_array[...]` 与
`test_poisoned_row_does_not_disable_whole_index` 双双变红并点名 `TypeError`。
"""
import pytest

from lingshu.core import retrieval as rt
from lingshu.core.core import SpacetimeMemoryEngine

# 合法 JSON 的四种非数组形状（缺陷形态：前三种此前抛 TypeError）
NON_ARRAY_JSON = [
    ("null", "NoneType"),
    ("5", "int"),
    ("true", "bool"),
    ('{"a": "dsh"}', "object"),
]

# 良性内容：确保断言针对的是「返回了什么」而不是「空输入恰好不炸」
CONTENT = "认知图是灵枢的记忆结构"


@pytest.fixture
def fts_on(monkeypatch):
    monkeypatch.setenv("LINGSHU_FTS", "1")


def _grams(doc: str) -> set:
    return set(doc.split())


# ---------- 单元面：形状矩阵 ----------

@pytest.mark.parametrize("raw,label", NON_ARRAY_JSON, ids=[x[1] for x in NON_ARRAY_JSON])
def test_doc_tolerates_legal_json_non_array(raw, label):
    """#707 正向：合法 JSON 非数组形状必须**降级**为「无标签」，不得抛异常。"""
    assert raw  # 硬性断言输入非空，防参数化空转成假绿
    doc = rt._doc(CONTENT, raw)          # 缺陷形态下此处抛 TypeError
    assert isinstance(doc, str)
    # 内容侧的二元组仍须在（证明不是「整体回落成空串」掩盖了缺陷）
    assert "认知" in _grams(doc) and "知图" in _grams(doc)


def test_doc_keeps_legal_array_semantics():
    """正对照 1：合法数组照常进索引（量具能测到「有标签」这一极）。"""
    doc = rt._doc(CONTENT, '["dsh", "user"]')
    g = _grams(doc)
    assert "认知" in g and "知图" in g
    assert "ds" in g and "sh" in g and "us" in g and "se" in g


def test_doc_string_scalar_is_single_tag_not_split():
    """正对照 2：JSON 字符串标量按**单个标签**（`provenance._tags` 口径），不按字符拆。
    按字符拆会得到单字二元组 `{'d','s','h'}`，故 `ds`/`sh` 是判别位。"""
    doc = rt._doc(CONTENT, '"dsh"')
    g = _grams(doc)
    assert "ds" in g and "sh" in g, "字符串标量被按字符拆开了（口径应与 provenance._tags 一致）"


def test_doc_still_degrades_invalid_json_and_empty():
    """正对照 3：非法 JSON / 空串仍回落空标签（原 `except` 面不得被本次改动碰坏）。"""
    for raw in ("not-json", "", "   "):
        doc = rt._doc(CONTENT, raw)
        assert "认知" in _grams(doc)


# ---------- 端到端：一行坏行不得毒化整库 ----------

def _engine(tmp_path, name):
    return SpacetimeMemoryEngine(str(tmp_path / name))


def test_poisoned_row_does_not_disable_whole_index(tmp_path, fts_on):
    """#707 影响面：一条 tags 为合法 JSON 非数组的行 ⇒ `sync()` / `candidates()` 仍可用，
    重启后 `attach()` 仍成功（缺陷形态下 attach 内的 sync 抛 TypeError，被 core 的宽
    except 降级成 `_fts=None`，FTS 永久失效）。

    范围说明：本件只判 `_doc` 所辖面（`attach` / `sync` / `candidates`）。
    `search_content()` 的端到端还受 core.py 侧另一处同族点制约（`node.tags` 直接迭代），
    那条不属本件改动面，已在报告里单列、不在本守卫内断言。"""
    e = _engine(tmp_path, "poison.sqlite3")
    bad = e.add_perception(CONTENT, skip_dedup=True)
    e.add_perception("灵枢认知图把记忆组织成六边形晶格", skip_dedup=True)
    assert e.store._fts is not None, "正对照失败：FTS 未挂载，后续断言无意义"
    assert e.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 2
    # 正对照：未污染时 candidates() 可用（量具能测到「好」这一极）
    assert e.store._fts.candidates(["认知图"], k=5), "正对照失败：未污染时 candidates() 已空"

    e.store.conn.execute("UPDATE nodes SET tags='null' WHERE id=?", (bad.id,))
    e.store.conn.commit()
    assert e.store.conn.execute("SELECT COUNT(*) FROM nodes_fts_dirty").fetchone()[0] >= 1

    # 良性词查询（与坏行内容无关）也必须可用 —— 缺陷形态下此处抛 TypeError
    cands = e.store._fts.candidates(["认知图"], k=5)
    assert cands, "candidates() 被坏行拖死（返回空/抛异常）"
    e.store.close()

    # 重启：attach() 内 sync() 不得抛（否则 core 静默降级为无 FTS）
    e2 = _engine(tmp_path, "poison.sqlite3")
    assert e2.store._fts is not None, "重启后 FTS 被降级为 None（坏行毒化了 attach）"
    assert e2.store.conn.execute(
        "SELECT COUNT(*) FROM nodes_fts_map").fetchone()[0] == \
        e2.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert e2.store._fts.candidates(["灵枢"], k=5)
    e2.store.close()


def test_legal_array_row_is_positive_control(tmp_path, fts_on):
    """正对照：同一库同一行换成合法数组 ⇒ 索引行数与节点数一致、candidates() 可用。
    （与上一件构成同库正/反对照：差别只在 tags 的形状。）"""
    e = _engine(tmp_path, "control.sqlite3")
    bad = e.add_perception(CONTENT, skip_dedup=True)
    e.add_perception("灵枢认知图把记忆组织成六边形晶格", skip_dedup=True)
    e.store.conn.execute("UPDATE nodes SET tags='[\"dsh\"]' WHERE id=?", (bad.id,))
    e.store.conn.commit()
    assert e.store._fts is not None
    assert e.store._fts.candidates(["认知图"], k=5)
    assert e.store.conn.execute(
        "SELECT COUNT(*) FROM nodes_fts_map").fetchone()[0] == 2
    e.store.close()
