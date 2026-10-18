# -*- coding: utf-8 -*-
"""#804 守卫：`search_content` 查询含 `%` / `_` 时的字面匹配契约（引擎面）。

现象（issue #804 报称）
    `search_content('%20')` / `search_content('%s')` 抛
    `ValueError: unsupported format character ...`——查询串里的 `%` 被当作
    Python `%` 格式化占位符；含 `_` 的查询被当作 SQL LIKE 单字符通配符
    （`file_name` 误匹配 `fileXname`）。

改前读数（基线 40fceb6 · Windows 11 · Python 3.12 · 本 worktree 实测）
    issue 原文复现脚本逐字重跑（`SpacetimeMemoryEngine` + tempfile 库）：
      search_content('example.com') -> 1 命中（URL 节点）        ——正常
      search_content('%20')        -> 1 命中（URL 节点），无异常 ——**不崩**
      search_content('%s')         -> 1 命中（格式串节点），无异常——**不崩**
      search_content('file_name')  -> 2 命中（均为兜底相似腿，非通配误配）
    全检索链静态排查（engine `search_content` → store `search_content` →
    `expand_query_terms`/`_like_fragments`/`_escape_like` → FTS `candidates`）
    无一处对查询做 Python `%` 格式化；LIKE 模式经 `_escape_like` 转义 +
    `ESCAPE '\\'` + `?` 参数绑定（#183 落地，commit 1cfe147/f80b579）。
    ⇒ **issue 报称的 ValueError 在基线上已不可复现**（属先行修复 #183 覆盖面）。

本件据此钉契约（不改行为）：A/B/D 组复刻 issue 复现脚本的验收口径；
C 组钉 `_` 的字面语义（回退 #183 转义即红：不转义时 `LIKE '%file_name%'`
会命中 `fileXname`）；E 组钉转义助手本身的转义表。

断言组（抽掉 `_escape_like` 转义 ⇒ C/D/E 红）：
  A 含 `%` 查询不抛异常且命中字面含该查询的节点（URL 百分号编码场景）
  B 含 `%s` 格式串查询不抛异常且命中字面 `%s` 内容（代码片段场景）
  C `_` 按字面匹配：搜 `file_name` 命中字面节点、不命中 `fileXname`
  D 纯 `%` 查询只回字面含 `%` 者（引擎面；#183 守卫只钉了 store 面）
  E `_escape_like` 转义表完整（`\\` → `\\\\`、`%` → `\\%`、`_` → `\\_`）

运行（仓根）：python -X utf8 -m pytest tests/test_issue804_search_like_wildcard_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _add(e, content):
    e.add_perception(content, skip_dedup=True)


def test_a_percent_query_no_crash_literal_hit():
    """A：`%20` 检索 URL 百分号编码——不抛异常、只命中字面含 `%20` 的节点。"""
    e = _engine()
    _add(e, "URL: https://example.com/file%20name")
    _add(e, "plain content without any percent sign")
    hits = e.search_content("%20")  # 旧缺陷面：ValueError / 整库通配命中
    got = [n.content for n, _ in hits]
    assert "URL: https://example.com/file%20name" in got, (
        "含 % 查询未命中字面含该字符的节点：%r" % (got,))
    assert "plain content without any percent sign" not in got, (
        "含 % 查询命中了不含该字符的节点（通配语义未封死）：%r" % (got,))


def test_b_format_string_query_no_crash_literal_hit():
    """B：`%s` 检索格式串——不抛异常、只命中字面含 `%s` 的节点。"""
    e = _engine()
    _add(e, "Format string: %s and %d")
    _add(e, "nothing format like here")
    hits = e.search_content("%s")
    got = [n.content for n, _ in hits]
    assert "Format string: %s and %d" in got, "含 %s 查询未命中：%r" % (got,)
    assert "nothing format like here" not in got, (
        "含 %s 查询误命中无关节点：%r" % (got,))


def test_c_underscore_literal_not_single_char_wildcard():
    """C：`_` 按字面匹配——搜 `file_name` 命中字面节点、不命中 `fileXname`。

    前置：库内有字面 `file_name` 节点 ⇒ LIKE 预筛命中、不走全表兜底，
    `fileXname` 若出现在结果里只可能是 `_` 被当单字符通配符（回退即红）。
    """
    e = _engine()
    _add(e, "config file_name here")
    _add(e, "config fileXname here")
    hits = e.search_content("file_name")
    got = [n.content for n, _ in hits]
    assert "config file_name here" in got, "字面 file_name 未命中：%r" % (got,)
    assert "config fileXname here" not in got, (
        "`_` 被当单字符通配符（file_name 命中 fileXname）：%r" % (got,))


def test_d_percent_only_query_engine_level_whole_store_sealed():
    """D：引擎面 `search_content("%")` 只回字面含 `%` 者（不再返回整库）。"""
    e = _engine()
    _add(e, "discount 50% off")
    _add(e, "no wildcard chars at all")
    got = sorted(n.content for n, _ in e.search_content("%"))
    assert got == ["discount 50% off"], (
        "引擎面纯 %% 查询未按字面收敛：%r" % (got,))


def test_escape_like_translation_table():
    """E：`_escape_like` 转义表——`\\` 先行（顺序敏感），`%`/`_` 全转义。"""
    from lingshu.core.core import LayeredStore
    esc = LayeredStore._escape_like
    assert esc("%") == "\\%"
    assert esc("_") == "\\_"
    assert esc("\\") == "\\\\"
    assert esc("a%b_c\\d") == "a\\%b\\_c\\\\d"
