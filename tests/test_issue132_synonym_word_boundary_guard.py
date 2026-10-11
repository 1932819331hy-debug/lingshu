# -*- coding: utf-8 -*-
"""#132 同义词扩展用「纯子串」触发 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #132 · 判定成立）：
  `LayeredStore.expand_query_terms` 的触发判据是 `if w in query`（**纯子串**）⇒
  短 ASCII 泛词在无关查询里被误触发，把整组同义词灌进 LIKE 预筛
  （`docs/plans/待裁清单_v0.1.md` D-44 点名的病根：**EMAIL → AI**）。
  实测缺陷形态：`expand_query_terms("EMAIL 地址")` 展开出
  `['EMAIL 地址', 'AI', '智能体', '灵枢', '智能']`。

修法（最小 · 词边界）：ASCII/数字词要求**词边界**命中（前后不得紧邻 ASCII 字母/
数字/下划线）；中文词仍按子串（本件不引入分词器）。

判据来源：`docs/plans/待裁清单_v0.1.md` **B-16**（§三「按推荐通过」批：触发口径 =
**词边界**）＋ **D-44**（同上，附-F 详表）。

边界（本件**未**做，属未裁策略项，见 `待裁清单` D-45/D-46）：
  · 零分条目过滤（D-45）——**未实施**：会改掉既有守卫
    `tests/test_core_search_discrimination.py::test_document_without_query_scores_zero`
    锁定的「无关文档得 0 并可见」这一**诚实评分**行为（非缺陷行为）；
  · 预筛池「原查询优先」（D-46）——未实施。
  故本守卫**只**钉「ASCII 词内误触发」这一 D-44 点名的病根。

断言组（抽掉修复即红）：
  A 缺陷复现（核心）：`EMAIL`/`EMAIL 地址` 不得展开出 `AI` 组任何词。
  B 防误杀：独立 ASCII 词 `AI` 仍触发其组；`AI 与灵枢` 仍触发。
  C 中文口径未回归：`记忆库` 仍展开出 `记忆`/`记录`/`库`（本件不改中文触发）。
  D 端到端：库里只有含 `AI` 的节点，查询 `EMAIL 地址` 不得把它召回
     （缺陷形态：扩展词含 `AI` ⇒ `LIKE %AI%` 命中）。
  E 静态有牙：AST 扫 `expand_query_terms`，其触发判据必须走词边界助手
     （注入回 `if w in query` 的纯子串形态 ⇒ 判据必红，自证有牙）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue132_synonym_word_boundary_guard.py -q --no-header
"""
from __future__ import annotations

import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import LayeredStore, SpacetimeMemoryEngine  # noqa: E402

_AI_GROUP = {"智能", "智能体", "灵枢", "AI"}


# ---------- A 缺陷复现（核心） ----------

def test_a_email_does_not_trigger_ai_group():
    for q in ("EMAIL", "EMAIL 地址", "email 地址", "MAILBOX"):
        terms = set(LayeredStore.expand_query_terms(q))
        leaked = terms & _AI_GROUP
        assert not leaked, (
            "A 子串误触发（D-44 病根 EMAIL→AI）：查询 %r 展开出 %r（缺陷形态：纯子串 `w in query`）"
            % (q, sorted(leaked)))


def test_a_other_ascii_substring_collisions():
    """其它 ASCII 词内子串碰撞：`AI` 不得由含 ai 的无关长词触发。"""
    for q in ("SAIL", "PAIR", "CHAIR"):
        assert "AI" not in LayeredStore.expand_query_terms(q), (
            "A %r 触发了 AI（子串误命中）" % (q,))


# ---------- B 防误杀 ----------

def test_b_standalone_ascii_word_still_triggers():
    assert _AI_GROUP <= set(LayeredStore.expand_query_terms("AI")), \
        "B 独立词 AI 不再触发其同义组（过度收窄）"
    assert _AI_GROUP <= set(LayeredStore.expand_query_terms("AI 与灵枢")), \
        "B 中文环境里的独立 AI 词不再触发"


# ---------- C 中文口径未回归 ----------

def test_c_chinese_substring_trigger_unchanged():
    terms = set(LayeredStore.expand_query_terms("记忆库"))
    assert {"记忆", "记录", "库"} <= terms, (
        "C 中文触发口径被改动（本件不应引入中文分词）：%r" % (sorted(terms),))


# ---------- D 端到端 ----------

def test_d_email_query_does_not_recall_ai_only_node():
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.add_perception("AI 芯片选型记录", skip_dedup=True)
        e.add_perception("EMAIL 地址簿维护", skip_dedup=True)
        hits = [n.content for n, _ in e.search_content("EMAIL 地址")]
        assert "AI 芯片选型记录" not in hits, (
            "D 含 AI 的无关节点被同义扩展召回（缺陷形态：扩展词含 AI ⇒ LIKE %%AI%% 命中）：%r"
            % (hits,))
        # 防误杀：真正含 EMAIL 的节点仍在召回面
        assert any("EMAIL" in h for h in hits), "D 真命中项被误杀：%r" % (hits,)
    finally:
        e.close()


# ---------- E 静态有牙 ----------

def _expand_fn(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "expand_query_terms":
            return node
    return None


def _uses_substring_trigger(fn_node) -> bool:
    """判据：函数体内是否出现 `if <Name> in <Name>:` 形态（旧纯子串触发）。"""
    for node in ast.walk(fn_node):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            c = node.test
            if (len(c.ops) == 1 and isinstance(c.ops[0], ast.In)
                    and isinstance(c.left, ast.Name)
                    and isinstance(c.comparators[0], ast.Name)):
                return True
    return False


def test_e_expand_query_terms_no_bare_substring_trigger():
    src = open(os.path.join(REPO, "lingshu", "core", "core.py"), encoding="utf-8").read()
    fn = _expand_fn(ast.parse(src))
    assert fn is not None, "E 未定位到 expand_query_terms（AST 扫描面为空）"
    assert not _uses_substring_trigger(fn), (
        "E expand_query_terms 仍是 `if w in query` 纯子串触发（缺陷形态）")
    # 有牙自证：注入旧形态 ⇒ 判据必须判为「有纯子串触发」
    probe = ast.parse(
        "def expand_query_terms(query):\n"
        "    for w in ('AI',):\n"
        "        if w in query:\n"
        "            pass\n")
    assert _uses_substring_trigger(probe.body[0]), \
        "E 有牙自证失败：旧纯子串形态未被检出"
