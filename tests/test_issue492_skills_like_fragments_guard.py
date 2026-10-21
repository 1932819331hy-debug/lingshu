# -*- coding: utf-8 -*-
"""#492 `search_skills` 超长查询不得让 LIKE 预筛自己抛异常 —— 守卫

缺陷（lingshu issue #492，判定成立）：`search_skills` 的 LIKE 预筛把**整段查询原文**
（`query.strip()`）拼成**一条** `%{q}%` 模式，同时绑定给 `name` / `description` 两列。
SQLite 上游常量 `SQLITE_MAX_LIKE_PATTERN_LENGTH` 默认 50000 字节 ⇒ 模式一超限，
这条 `execute` 自身抛 `OperationalError: LIKE or GLOB pattern too complex`。
`recall_skill` 走 `store.search_skills` 同链同报错。

触发需**两条件同时成立**（本件复核实测）：
  ① skills 表**非空**（空表时 LIKE 不参与求值，不抛）；
  ② 去空白后的查询非空且过长（实测 49998 字节不抛、49999 字节抛；20000 汉字
     = 60000 字节抛）。
同库对照：`search_content` 早已由 #266 经 `_like_fragments` 切分保护，超长查询正常
返回；**只有技能面漏了**（同链不同面 ⇒ 修复面就是技能面）。

修法：把 `_like_fragments`（#266 既有件，同文件同语义）移植到技能面预筛——短词原样
返回 `[term]`（短查询 SQL 语义逐字不变），超长词切成多条短模式，每片段各占一组
`name/description LIKE ? ESCAPE '\\'` 子句；`ORDER BY confidence DESC, updated_at DESC`
（#272）与 `_escape_like`（#200）口径不变。

断言组（把技能面的 `_like_fragments` 切分抽掉即红）：
  K1 超长查询不抛：49999 字节（reporter 报的边界）与 60000 字节（20000 汉字）在
     `search_skills` / `recall_skill` 上都必须正常返回（旧缺陷：同抛 OperationalError）。
  K2 已合法长度**结果不变**：对修前已不抛的长度（100 / 3000 / 4000 / 20000 / 49998
     字节），修后结果须与「整句单模式」的修前等价行为**逐条相同**（防切分顺手改了
     合法输入的召回语义）。
  K3 短查询行为逐字不变：短查询仍只产出一条模式（`_escape_like` 调用 1 次）、
     `%` / `_` 仍按字面匹配（#200）、定序仍按 confidence DESC（#272）。
  K4 切分确实发生：超长查询下 `_escape_like` 被调用 >1 次（模式确实分了片）。
  K5 前置边界：空 skills 表 + 超长查询不抛、返回空列表（缺陷前提①的对照）。
  K6 **转义放大型**触发：查询里含大量 `\\` 时，`_escape_like` 会把每个 `\\` 变成两个
     字符 ⇒ **模式字节数约翻倍**，分界点前移（25000 个 `\\`：原文 25000 字节，
     转义后模式 50002 字节 > 上限 ⇒ 旧实现抛；原文远未到 49999 的分界）。
     本腿正是 reporter「更正 1」指出的那一面——修复后（切分发生在**转义之前**）
     每片段转义后仍 ≤ `2 × _LIKE_PATTERN_MAX_BYTES + 2`，远离上限。

**修复后的语义边界（如实标注，不主张超长查询能召回技能）**：技能面预筛是
`name/description LIKE %片段%`，短列**不可能包含**数十字节以上的片段 ⇒ 任何超长查询
的预筛本就落空；且技能面**没有** `search_content` 那条「LIKE 落空 → 全表二元组兜底」
腿（#272 明确不采纳，见其 docstring 边界①）。故本件修的是**「抛异常」→「与已合法
长度同结果」**，**不**是「超长查询也能召回技能」——后者属 #272 的召回语义面，不在
本件范围。缺陷的实质是**分界处行为断裂**（49998 字节静默返回空、49999 字节直接抛），
修复把它抹平为连续。

运行（仓根）：python -X utf8 -m pytest tests/test_issue492_skills_like_fragments_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import LayeredStore, SpacetimeMemoryEngine  # noqa: E402

_BASE = "数据库全量备份"


def _long_query(total_bytes: int, base: str = _BASE) -> str:
    """构造编码后恰为 `total_bytes` 字节的查询（base + ASCII 填充）。"""
    pad = total_bytes - len(base.encode("utf-8"))
    assert pad >= 0, total_bytes
    return base + "a" * pad


def _engine_with_skills():
    e = SpacetimeMemoryEngine(":memory:")
    e.store_skill("数据库全量备份", "把数据库整库导出并校验", "pg_dump > gzip", confidence=0.9)
    e.store_skill("日志清理", "按天清理旧日志", "find -mtime +30 -delete", confidence=0.5)
    return e


def test_k1_long_query_does_not_raise_on_skills_face():
    """K1：模式上限分界处（49998/49999/50000/60000 字节）都不得抛。

    旧缺陷：49998 静默返回空、49999 起抛 `OperationalError` —— 分界处行为断裂。
    """
    e = _engine_with_skills()
    for total in (49998, 49999, 50000, 60000):
        q = _long_query(total)
        assert len(q.encode("utf-8")) == total
        r1 = e.store.search_skills(q)      # 旧缺陷：≥49999 时此处抛 OperationalError
        r2 = e.recall_skill(q)             # 旧缺陷：经 store.search_skills 同抛
        assert isinstance(r1, list) and isinstance(r2, list), (total, r1, r2)
        assert [s["id"] for s in r1] == [s["id"] for s in r2], total


def test_k2_legal_lengths_results_unchanged(monkeypatch):
    """K2：修前已不抛的长度上，修后结果须与「整句单模式」逐条相同。

    参考行为＝把 `_like_fragments` 钉成恒等（`[term]`），即修前的整句单模式语义。
    对 100 / 3000 / 4000 / 20000 / 49998 字节（均 ≤ SQLite 模式上限，修前不抛），
    两条路径的结果必须一致——防「顺手把合法输入的召回语义也改了」。
    """
    e = _engine_with_skills()
    lens = (100, 3000, 4000, 20000, 49998)
    new = {n: e.store.search_skills(_long_query(n)) for n in lens}
    monkeypatch.setattr(LayeredStore, "_like_fragments",
                        staticmethod(lambda t: [t]))   # 修前：整句单模式
    old = {n: e.store.search_skills(_long_query(n)) for n in lens}
    monkeypatch.undo()
    for n in lens:
        assert [s["id"] for s in new[n]] == [s["id"] for s in old[n]], (
            "#492：合法长度 %d 字节的召回结果被切分改动：new=%r old=%r"
            % (n, [s["name"] for s in new[n]], [s["name"] for s in old[n]]))


def test_k3_short_query_behaviour_unchanged(monkeypatch):
    """K3：短查询仍只产一条模式；`%`/`_` 字面匹配；定序仍按置信度。"""
    e = _engine_with_skills()
    calls = []
    orig = LayeredStore._escape_like

    def _spy(q):
        calls.append(q)
        return orig(q)

    monkeypatch.setattr(LayeredStore, "_escape_like", staticmethod(_spy))
    got = e.store.search_skills("数据库")
    assert len(calls) == 1, "短查询不得被切碎（模式数应恰为 1）：%r" % (calls,)
    assert [s["name"] for s in got][:1] == ["数据库全量备份"]
    monkeypatch.undo()

    # #200：`%` / `_` 仍按字面匹配（不得因切分改回通配）
    e2 = SpacetimeMemoryEngine(":memory:")
    e2.store_skill("普通卡", "描述里没有特殊字符", "p", confidence=0.5)
    assert e2.store.search_skills("%") == [], "#200：`%` 被当通配符"
    assert e2.store.search_skills("_") == [], "#200：`_` 被当通配符"

    # #272：定序仍按 confidence DESC 截断（构造 >100 张命中，最高置信者须在内）
    e3 = SpacetimeMemoryEngine(":memory:")
    ids = {}
    for i in range(120):
        ids[i] = e3.store_skill("同词命中%03d" % i, "同词命中描述", "p",
                                confidence=0.1 + i * 0.005)
    hit = {s["id"] for s in e3.store.search_skills("同词命中", limit=1000)}
    assert ids[119] in hit, "#272：最高置信卡未进候选池"


def test_k4_long_query_is_actually_fragmented(monkeypatch):
    """K4：超长查询下模式确实分片（`_escape_like` 调用次数 >1）。"""
    e = _engine_with_skills()
    calls = []
    orig = LayeredStore._escape_like

    def _spy(q):
        calls.append(q)
        return orig(q)

    monkeypatch.setattr(LayeredStore, "_escape_like", staticmethod(_spy))
    e.store.search_skills(_long_query(60000))
    assert len(calls) > 1, (
        "#492：超长查询未分片（模式仍是一条整句 ⇒ 会撞 SQLITE_MAX_LIKE_PATTERN_LENGTH）：%r"
        % (calls,))


def test_k5_empty_skills_table_long_query_ok():
    """K5：缺陷前提①对照——空 skills 表 + 超长查询不抛、返回空。"""
    e = SpacetimeMemoryEngine(":memory:")
    assert e.store.search_skills(_long_query(60000)) == []
    assert e.recall_skill(_long_query(60000)) == []


def test_k6_escape_amplification_leg():
    """K6：转义放大型触发——`\\` 经 `_escape_like` 加倍 ⇒ 模式长度翻倍、分界前移。

    reporter 的「更正 1」指出：查询里每个 `\\` 在 `_escape_like` 后变两个字符 ⇒ 模式
    字节数约为查询的两倍，分界点随之前移到约 24999/25000（本机核算：`"\\" * 25000`
    的旧单模式 = `25000×2 + 2 = 50002` 字节 > 上游 50000 ⇒ 抛；`"\\" * 24999` 恰
    50000 ⇒ 不抛）。修复把切分放在**转义之前**，故每片段转义后仍 ≤
    `2 × _LIKE_PATTERN_MAX_BYTES + 2`，与上限差一个数量级。
    本腿钉死这一点，消「转义放大量随 SQLite 构建漂移」的盲区。
    """
    e = _engine_with_skills()
    for n in (20000, 24000, 24999, 25000, 26000, 30000, 60000):
        q = "\\" * n
        assert len(q.encode("utf-8")) == n
        r1 = e.store.search_skills(q)     # 旧缺陷：≥ 分界点即抛 OperationalError
        r2 = e.recall_skill(q)
        assert isinstance(r1, list) and isinstance(r2, list), (n, r1, r2)

    # 转义放大上界：分片在转义前 ⇒ 最坏情形（满片数 × 单片放大后字节）仍远离上限
    from lingshu.core.core import _LIKE_FRAGMENT_CAP, _LIKE_PATTERN_MAX_BYTES
    worst = 2 * _LIKE_PATTERN_MAX_BYTES + 2
    assert worst * _LIKE_FRAGMENT_CAP < 50000, (worst, _LIKE_FRAGMENT_CAP)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "--no-header"]))
