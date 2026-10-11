# -*- coding: utf-8 -*-
"""守卫 · discovery_report 的 cwd 排除计数必须能区分「护栏生效」与「逃生口打开」
（lingshu issue #338）
============================================================================
缺陷（HEAD 修复前，`lingshu/core/component_resolver.py:discovery_report`）
    `"cwd_excluded_count": _pg["removed_count"] + _cwd_live`
    把两个**语义相反**的量相加：
      ① `path_scrub.removed_count`——已被 `_pathguard` 从 `sys.path` **消除**的条目；
      ② `_cwd_live`——**仍存活**于 `sys.path`、只是被本 finder 忽略的条目
         （逃生口 `LINGSHU_ALLOW_CWD_IMPORTS=1` 打开时）。
    实测（本仓 Python 3.12，`-c` 形态 + 空串条目在场）：
      逃生口**关**：cwd_excluded_count=3（removed=3, live=0）
      逃生口**开**：cwd_excluded_count=3（removed=0, live=3）   ← 读数相同
    即：护栏被放弃（本层保护关闭、非白名单 cwd 模块可执行）与护栏生效，
    在对外计数字段上**不可区分**。既有 `tests/test_issue156_component_discovery.py`
    的 B2d（`:441`）据此断言「已排除 cwd/空串条目」，在两种状态下均通过 ⇒ 弱断言。

修复
    `cwd_excluded_count` 只计**真正被排除**的条目（`path_scrub.removed_count`）；
    仍存活的 cwd 条目由既有字段 `cwd_entries_in_sys_path` 单独给出，二者不可相加。

判据来源
    · 「已排除」的字面语义 = 已被从 `sys.path` 移除（`path_scrub.removed_count`
      的既有口径，见 `lingshu/_pathguard.py:scrub()` / `pathguard_report():384`）；
    · 「逃生口打开 ⇒ 本层保护关闭」见 `_pathguard.py` 模块头（`:37-40`）与
      `CwdImportsAllowed` 告警——两种状态防护强度不同，计数字段必须能区分。
    · 阈值/判据本体未改——本件只让计数与字面语义一致，未放宽任何安全判据。

削掉的判别力（如实）：本件不新增防护；仅纠正一个对外计数。若下游曾把
`cwd_excluded_count` 读作「两类合计」，其读数会变小（逃生口开时由 ≥1 变 0）——
这正是缺陷所在（旧值把「未排除」算成了「已排除」）。

定点变异自证（抽掉修复 ⇒ 必红）
    把 `component_resolver.py:discovery_report` 的 `cwd_excluded_count` 改回
    `_pg["removed_count"] + _cwd_live` ⇒
    test_escape_open_reports_no_exclusion（逃生口开时读数 0→1）红。

运行（仓根）：python -X utf8 -m pytest tests/test_component_discovery_count_semantics.py -q --no-header
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 子进程探针：显式放一个空串（= cwd）条目进 sys.path，再取 discovery 报告。
_PROBE = (
    "import json, sys\n"
    "sys.path.insert(0, '')\n"
    "from lingshu.core.core import SpacetimeMemoryEngine\n"
    "e = SpacetimeMemoryEngine(':memory:')\n"
    "d = e.self_check()['component_assembly']['discovery']\n"
    "ps = d['path_scrub']\n"
    "print('##J##' + json.dumps({\n"
    "    'cwd_excluded_count': d['cwd_excluded_count'],\n"
    "    'cwd_entries_in_sys_path': d['cwd_entries_in_sys_path'],\n"
    "    'removed_count': ps['removed_count'],\n"
    "    'escape_hatch_open': ps['escape_hatch_open'],\n"
    "}))\n"
)


def _run_probe(escape_open: bool) -> dict:
    """在干净临时 cwd 下跑探针，返回 discovery 计数（子进程隔离，不污染本进程）。"""
    env = dict(os.environ)
    env.pop("LINGSHU_ALLOW_CWD_IMPORTS", None)
    if escape_open:
        env["LINGSHU_ALLOW_CWD_IMPORTS"] = "1"
    # 仓根经 PYTHONPATH 声明（cwd 面会被护栏/守卫排除，不能用 cwd 找到 lingshu）
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    work = tempfile.mkdtemp(prefix="c338_")
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", _PROBE],
                       cwd=work, capture_output=True, text=True,
                       encoding="utf-8", env=env)
    assert r.returncode == 0, f"探针子进程失败 rc={r.returncode}\n{r.stderr[-800:]}"
    for line in (r.stdout or "").splitlines():
        if line.startswith("##J##"):
            return json.loads(line[len("##J##"):])
    raise AssertionError(f"探针无输出：stdout={r.stdout!r} stderr={r.stderr[-400:]!r}")


def test_escape_closed_counts_excluded_entries():
    """逃生口关：确有 cwd 条目被消除，且当前 sys.path 中已无 cwd 条目。"""
    j = _run_probe(escape_open=False)
    assert j["escape_hatch_open"] is False, j
    assert j["removed_count"] >= 1, j
    assert j["cwd_entries_in_sys_path"] == 0, j
    assert j["cwd_excluded_count"] >= 1, j
    # 语义钉子：该字段恰等于「真正被排除」的条目数（旧式 +_cwd_live 会在此不等）
    assert j["cwd_excluded_count"] == j["removed_count"], j


def test_escape_open_reports_no_exclusion():
    """★ 核心：逃生口打开（本层保护关闭）时，无任何 cwd 条目被排除 ⇒ 计数必须为 0。

    旧式 `removed_count + _cwd_live` 在此会报 live(≥1)，把「仍存活」谎报成「已排除」。
    """
    j = _run_probe(escape_open=True)
    assert j["escape_hatch_open"] is True, j
    assert j["removed_count"] == 0, j
    assert j["cwd_entries_in_sys_path"] >= 1, j
    assert j["cwd_excluded_count"] == 0, j
    assert j["cwd_excluded_count"] == j["removed_count"], j


def test_two_states_are_distinguishable():
    """★ 核心：两状态下该字段读数必须不同（缺陷即「读数相同、不可区分」）。"""
    closed = _run_probe(escape_open=False)
    opened = _run_probe(escape_open=True)
    assert closed["cwd_excluded_count"] != opened["cwd_excluded_count"], (
        "逃生口开/关两状态下 cwd_excluded_count 读数相同 ⇒ 计数掩盖了防护差异",
        closed, opened)
