# -*- coding: utf-8 -*-
"""#272 `search_skills` 预筛必须先定序再截断（不得按插入序截前 100 行） —— 守卫

缺陷（lingshu issue #272，判定成立）：`search_skills` 的预筛
`SELECT * FROM skills WHERE name LIKE ? OR description LIKE ? LIMIT 100` **无
`ORDER BY`** ⇒ 按扫描（rowid/插入）序取前 100 行；相关度 `sort`（按
`char_bigram_jaccard`）只在这 100 行里做。于是命中超过 100 张后，**名字与查询完全
相同、置信度最高**的技能卡召不回来（issue 实测：先写 100 张「项目NNN日志备份」、
confidence=0.5，再写第 101 张名为「备份」、confidence=0.9 的卡，
`recall_skill("备份")` 返回的前三张全是旧卡且不含它）。

修法（issue 补充帖「更稳的修法」第 1 条）：预筛加
`ORDER BY confidence DESC, updated_at DESC` —— 截断时保留**高置信、较近更新**的候选。
不改 `LIMIT 100` 的有界候选池口径，不把 `procedure` 纳入匹配（那是另一条独立面）。

断言组（把 `ORDER BY confidence DESC, updated_at DESC` 抽掉即红）：
  J1 第 101 张高置信同名卡可召回：`recall_skill("备份")` 必须含它。
  J2 截断保留的是高置信者：命中 >100 时，返回集合的置信度不低于被丢弃者
     （旧缺陷：按插入序截断 ⇒ 后写入的高置信卡整批不可达）。
  J3 返回仍按相关度排（不得把 ORDER BY 直接当最终序）：同名卡里名称完全匹配的
     排在「项目NNN日志备份」之前。

运行（仓根）：python -X utf8 -m pytest tests/test_issue272_recall_skill_order_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _fill(engine, n=100):
    for i in range(n):
        engine.store_skill("项目%03d日志备份" % i,
                           "把项目%03d的日志打包备份" % i,
                           "tar > scp", confidence=0.5)


def test_j1_high_confidence_card_beyond_first_100_is_recalled():
    """J1：第 101 张（名字与查询完全相同、置信度最高）必须能召回。"""
    e = SpacetimeMemoryEngine(":memory:")
    _fill(e)
    good = e.store_skill("备份", "数据库全量备份（已验证）",
                         "pg_dump > gzip > 校验", confidence=0.9)
    for q in ("备份", "数据库全量备份"):
        r = e.recall_skill(q, limit=3)
        assert any(s["id"] == good for s in r), (
            "recall_skill(%r) 召不回第 101 张高置信同名卡（#272）：%r"
            % (q, [s["name"] for s in r]))
    # 直接按 id 查得到（证明它确实在库里，只是被插入序截断排除了）
    row = e.store.conn.execute(
        "SELECT name, confidence FROM skills WHERE id=?", (good,)).fetchone()
    assert row[0] == "备份" and row[1] == 0.9, row


def test_j2_truncation_keeps_high_confidence_candidates():
    """J2：命中 >100 时，被保留的候选置信度不得低于被丢弃者。

    构造 120 张同词命中、置信度递增的卡；`search_skills` 的候选池上限 100 ⇒
    按插入序截断会丢掉最后写入的 20 张（最高置信），按 confidence DESC 截断则保留它们。
    """
    e = SpacetimeMemoryEngine(":memory:")
    ids = {}
    for i in range(120):
        ids[i] = e.store_skill("同词命中%03d" % i, "同词命中描述", "p", confidence=0.1 + i * 0.005)
    got = {s["id"] for s in e.store.search_skills("同词命中", limit=1000)}
    # 最高置信的三张必须在内
    for i in (119, 118, 117):
        assert ids[i] in got, (
            "置信度最高的卡（i=%d, conf=%.3f）未进入候选池（#272：按插入序截断）"
            % (i, 0.1 + i * 0.005))
    confs = [s["confidence"] for s in e.store.search_skills("同词命中", limit=1000)]
    assert min(confs) >= 0.1 + 20 * 0.005, (
        "候选池里出现了低置信卡（min=%.3f）——截断未按置信度定序" % min(confs))


def test_j3_final_order_still_by_relevance():
    """J3：返回仍按相关度排（ORDER BY 只作用于预筛，不是最终序）。"""
    e = SpacetimeMemoryEngine(":memory:")
    _fill(e)
    e.store_skill("备份", "数据库全量备份（已验证）", "pg_dump", confidence=0.9)
    names = [s["name"] for s in e.recall_skill("备份", limit=3)]
    assert names[0] == "备份", "相关度最高的卡未排首位：%r" % (names,)
