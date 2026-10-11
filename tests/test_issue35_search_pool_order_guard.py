# -*- coding: utf-8 -*-
"""test_issue35_search_pool_order_guard · #35 检索候选池定序守卫
============================================================================
缺陷（lingshu issue #35 · P1）：`LayeredStore.search_content` 的两处候选池
`LIMIT` **均无 `ORDER BY`**：

  · `c.execute(f"SELECT * FROM nodes WHERE {where} LIMIT 300", ...)`
  · 回退腿 `SELECT * FROM nodes LIMIT 500`（含 conds 分支）

无 `ORDER BY` 时 SQLite 实际按 **rowid（插入序）** 返回 ⇒ 池满之后**后写入的
命中项整批不可达**：实测 351 条同词记忆中，第 351 条高重要度目标落在前 300 行
之外、检索不可见（「600 条记忆只返回前 3」的同型病）。

修法：两处补 `ORDER BY importance DESC, last_access DESC`（沿用同文件
`query_nodes` 既有定序手法），把「按插入序静默丢新项」改成「按重要度有序截断」。
判据来源：经验标定（本件 #35；仓内无规定检索候选池排序的理论章节，追不到更早
出处）＋复用 `query_nodes` 既有定序手法。

守卫断言组：
  A 组（行为 · 有牙）：候选池超限（>300 行命中）时，后写入的**高重要度**命中项
     必须可达——修复前不可达（按 rowid 截断），修复后可达。
  B 组（行为 · 回退腿）：LIKE 预筛落空（查询为重组短语）时，回退腿 `LIMIT 500`
     同样必须定序——超 500 行时后写入的高重要度项可达。
  C 组（对照 · 防误杀）：池未满（<300 行）时命中集合不变（定序不改变召回面）；
     且同分/同重要度下既有相关度排序口径（Jaccard + importance/confidence 终排）
     未被破坏。
  D 组（静态 · 有牙）：AST 扫描 `search_content` 内**每个** `LIMIT` 语句必须带
     `ORDER BY`（含回退腿两条分支）；注入一条无 ORDER BY 的 LIMIT ⇒ 判据必红
    （自证有牙）。

运行（lingshu 仓根）：
    python -X utf8 tests/test_issue35_search_pool_order_guard.py     # 脚本式，退出码 0/1
    python -X utf8 -m pytest tests/test_issue35_search_pool_order_guard.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_PASS, _FAIL = [], []


def ok(cond, name, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  <- " + str(detail)) if detail else ""))


_Q = "超时"          # 单查询词；无同义词组含它 ⇒ 展开词 = 原查询整句


def _engine():
    return SpacetimeMemoryEngine(
        os.path.join(tempfile.mkdtemp(prefix="lingshu_issue35_"), "s.db"))


# ---------- A 组：候选池超限时后写入的高重要度命中项必须可达 ----------

def group_a_pool_overflow_reachable():
    print("== A 组：候选池 >300 行 ⇒ 后写入高重要度命中项可达 ==")
    e = _engine()
    n_fill = 350
    for i in range(n_fill):
        e.add_perception("%s 填充%d" % (_Q, i), importance=0.1, skip_dedup=True)
    target = "%s 目标条目" % _Q
    e.add_perception(target, importance=0.95, skip_dedup=True)
    hits = [n.content for n, _ in e.search_content(_Q, limit=50)]
    print("     命中数=%d / 池内候选目标可达=%s" % (len(hits), target in hits))
    ok(target in hits,
       "A1 池超限（%d 行命中 > LIMIT 300）时，后写入的高重要度目标仍可达"
       "（旧实现按 rowid 截断 ⇒ 不可达）" % (n_fill + 1),
       "hits[:3]=%r" % (hits[:3],))
    ok(len(hits) > 0, "A2 检索仍返回结果（未因定序改坏）", len(hits))


# ---------- B 组：回退腿（LIKE 预筛落空）同样定序 ----------

def group_b_fallback_leg_ordered():
    print("== B 组：LIKE 预筛落空 ⇒ 回退腿 LIMIT 500 同样定序 ==")
    e = _engine()
    # 填 520 条**不含**查询字面量的记忆（查询为重组短语：原文无连续子串命中）
    for i in range(520):
        e.add_perception("网络超 时间 指标%d" % i, importance=0.1, skip_dedup=True)
    target = "网络超 时间 指标 目标"          # 与查询共享 bigram（折叠空白后）
    e.add_perception(target, importance=0.95, skip_dedup=True)
    q = "超时指标"                              # 字面 LIKE 落空 ⇒ 走全表回退
    hits = [n.content for n, _ in e.search_content(q, limit=50)]
    print("     回退腿命中数=%d 目标可达=%s" % (len(hits), target in hits))
    ok(target in hits,
       "B1 回退腿（LIMIT 500）超限时，后写入的高重要度项仍可达"
       "（旧实现按 rowid 截断 ⇒ 不可达）", "hits[:3]=%r" % (hits[:3],))


# ---------- C 组：对照（防误杀） ----------

def group_c_no_regression():
    print("== C 组：对照——池未满时召回面不变、相关度排序口径不破坏 ==")
    e = _engine()
    pure, diluted = _Q, _Q + "".join(chr(0x4E00 + i) for i in range(1, 400))
    e.add_perception(pure, importance=0.5, skip_dedup=True)
    e.add_perception(diluted, importance=0.5, skip_dedup=True)
    s = {n.content: sc for n, sc in e.search_content(_Q, limit=50)}
    ok(pure in s and diluted in s, "C1 池未满：两条命中都在结果里（召回面不变）", sorted(s))
    ok(s[diluted] < s[pure] and next(iter(s)) == pure,
       "C2 相关度排序口径未被破坏（长文稀释、纯查询词列首）", s)


# ---------- D 组：AST 静态（有牙） ----------

def _limit_without_order_by(fn_node):
    """返回 fn_node 内**无 ORDER BY** 的 LIMIT 字符串字面量列表（SQL 形态判据）。"""
    bad = []
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.upper()
            if "LIMIT" in s and "ORDER BY" not in s:
                bad.append(node.value)
    return bad


def _search_content_node(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "search_content":
            # LayeredStore.search_content 是唯一在 core.py 内含 LIMIT 的那个
            if any(isinstance(c.value, str) and "LIMIT" in c.value
                   for c in ast.walk(node) if isinstance(c, ast.Constant)):
                return node
    return None


def group_d_static_teeth():
    print("== D 组：AST 静态——search_content 内每个 LIMIT 必须带 ORDER BY ==")
    src = open(os.path.join(REPO, "lingshu", "core", "core.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    node = _search_content_node(tree)
    ok(node is not None, "D1 定位到含 LIMIT 的 search_content（AST 扫描面非空）")
    if node is None:
        return
    bad = _limit_without_order_by(node)
    ok(not bad, "D2 search_content 内无「无 ORDER BY 的 LIMIT」", bad)
    # D3：有牙自证——注入一条无 ORDER BY 的 LIMIT 常量 ⇒ 判据必红
    probe = ast.parse(
        "def search_content(self):\n"
        "    c.execute('SELECT * FROM nodes LIMIT 300')\n")
    bad2 = _limit_without_order_by(probe.body[0])
    ok(len(bad2) == 1,
       "D3 有牙自证：注入无 ORDER BY 的 LIMIT 常量 ⇒ 判据检为违规（必红）", bad2)
    # D4：正对照——带 ORDER BY 的 LIMIT 不被误报
    good = ast.parse(
        "def search_content(self):\n"
        "    c.execute('SELECT * FROM nodes ORDER BY importance DESC LIMIT 300')\n")
    ok(not _limit_without_order_by(good.body[0]),
       "D4 正对照：带 ORDER BY 的 LIMIT 不误报")


def run_checks():
    _PASS.clear(); _FAIL.clear()
    group_a_pool_overflow_reachable()
    group_b_fallback_leg_ordered()
    group_c_no_regression()
    group_d_static_teeth()
    print()
    print("===== SUMMARY %d/%d 通过 =====" % (len(_PASS), len(_PASS) + len(_FAIL)))
    if _FAIL:
        print("FAILS:", _FAIL)
        return 1
    print("VERDICT=PASS（search_content 两处候选池均显式定序；超限时后写入的高重要度"
          "命中项可达；池未满与相关度排序口径未被破坏）")
    return 0


def test_issue35_search_pool_order_guard():
    """pytest 入口（脚本/pytest 双模式）。"""
    run_checks()
    assert not _FAIL, "%d checks failed: %s" % (len(_FAIL), _FAIL)


if __name__ == "__main__":
    sys.exit(run_checks())
