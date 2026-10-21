# -*- coding: utf-8 -*-
"""守卫 · discovery_report 的 cwd 排除计数必须能区分「护栏生效」与「逃生口打开」
（lingshu issue #338）
============================================================================
缺陷（#338 修复前，`lingshu/core/component_resolver.py:discovery_report`）
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
    的 B2d 据此断言「已排除 cwd/空串条目」，在两种状态下均通过 ⇒ 弱断言。

#338 修复
    `cwd_excluded_count` 只计**真正被排除**的条目（当时的实现口径
    `path_scrub.removed_count`）；仍存活的 cwd 条目由既有字段
    `cwd_entries_in_sys_path` 单独给出，二者不可相加。

#273 裁定（路 B · 解析期拒绝）后的**口径迁移**（本件同步，判据对位非放宽）
    #273 路 B 落地后 `_pathguard` **不再从 `sys.path` 删除任何条目** ⇒ 旧口径
    `removed_count` **恒 0**，两状态读数皆为 0 ⇒ #338 的核心判据
    （两状态必须可区分）**必红**。故 `cwd_excluded_count` 改取新语义下
    「已排除」的字面对应物：**被解析期拒绝 finder 拒绝解析的 cwd/空串条目数**
    （`path_scrub.refused_cwd_entry_count`）——
      逃生口**关**（finder 在位）⇒ ≥1；逃生口**开**（不装 finder）⇒ 0
    ⇒ 两状态仍可区分，#338 的判据**保住**。
    ★本件的判别力**不降反增**：除计数面外，另加**行为断言**——「cwd 条目**仍在**
    `sys.path`（路 B 不删条目）**但**其上的解析被拒（cwd 下的毒文件未被执行）」。

判据来源
    · 「已排除」的字面语义（路 B 下）= 该条目上的解析被**拒绝**
      （`lingshu/_pathguard.py::refused_cwd_entry_count()` / `_CwdRouteFinder`），
      而**不是**「条目已被从 `sys.path` 移除」；
    · 「逃生口打开 ⇒ 本层保护关闭」见 `_pathguard.py` 模块头与
      `CwdImportsAllowed` 告警——两种状态防护强度不同，计数字段必须能区分。
    · 阈值/判据本体未改——本件只让计数与（新）字面语义一致，未放宽任何安全判据。

削掉的判别力（如实）：本件不新增防护；仅纠正一个对外计数。若下游曾把
`cwd_excluded_count` 读作「两类合计」，其读数会变小（逃生口开时由 ≥1 变 0）——
这正是缺陷所在（旧值把「未排除」算成了「已排除」）。

定点变异自证（抽掉修复 ⇒ 必红）
    ① 把 `component_resolver.py:discovery_report` 的 `cwd_excluded_count` 改回
       `_pg["removed_count"]`（路 B 下恒 0）⇒
       test_escape_closed_counts_excluded_entries / test_two_states_are_distinguishable 红。
    ② 把 `cwd_excluded_count` 改回旧式 `_pg["removed_count"] + _cwd_live` ⇒
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

# 子进程探针：显式放一个空串（= cwd）条目进 sys.path，再取 discovery 报告；
# 并**另加一条行为腿**——cwd 下放一个只存在于 cwd 的裸名毒文件，尝试导入它：
#   · 逃生口关（finder 在位）⇒ 解析被拒（ModuleNotFoundError）、毒文件**未被执行**；
#   · 逃生口开（不装 finder）⇒ 毒文件被执行（MARKER 出现）＝如实记录「开＝放弃保护」。
_PROBE = (
    "import json, os, sys\n"
    "sys.path.insert(0, '')\n"
    "from lingshu.core.core import SpacetimeMemoryEngine\n"
    "e = SpacetimeMemoryEngine(':memory:')\n"
    "d = e.self_check()['component_assembly']['discovery']\n"
    "ps = d['path_scrub']\n"
    "out = {\n"
    "    'cwd_excluded_count': d['cwd_excluded_count'],\n"
    "    'cwd_entries_in_sys_path': d['cwd_entries_in_sys_path'],\n"
    "    'removed_count': ps['removed_count'],\n"
    "    'escape_hatch_open': ps['escape_hatch_open'],\n"
    "    'refused_cwd_entry_count': ps['refused_cwd_entry_count'],\n"
    "    'route_finder_installed': ps['route_finder_installed'],\n"
    "    'cwd': os.getcwd(),\n"
    "}\n"
    "try:\n"
    "    import c338_cwd_probe\n"
    "    out['poison_import'] = 'ok'\n"
    "except ModuleNotFoundError:\n"
    "    out['poison_import'] = 'ModuleNotFoundError'\n"
    "out['poison_marker'] = os.path.exists(os.path.join(os.getcwd(), 'MARKER.txt'))\n"
    "print('##J##' + json.dumps(out))\n"
)

# 只存在于 cwd 的裸名毒文件（非标准库、非组件白名单；仅由 cwd 条目可解析）
_POISON_SRC = (
    "import os as _os\n"
    "with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"
    " 'MARKER.txt'), 'w', encoding='utf-8') as _f:\n"
    "    _f.write('EXECUTED cwd=' + _os.getcwd())\n"
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
    with open(os.path.join(work, "c338_cwd_probe.py"), "w", encoding="utf-8") as f:
        f.write(_POISON_SRC)
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", _PROBE],
                       cwd=work, capture_output=True, text=True,
                       encoding="utf-8", env=env)
    assert r.returncode == 0, f"探针子进程失败 rc={r.returncode}\n{r.stderr[-800:]}"
    for line in (r.stdout or "").splitlines():
        if line.startswith("##J##"):
            return json.loads(line[len("##J##"):])
    raise AssertionError(f"探针无输出：stdout={r.stdout!r} stderr={r.stderr[-400:]!r}")


def test_escape_closed_counts_excluded_entries():
    """逃生口关（护栏生效）：cwd 条目**仍在** `sys.path`（路 B 不删条目），
    但其上的解析**被拒**（finder 在位）；cwd 毒文件未被执行。

    ★#273 路 B 口径迁移：旧断言 `removed_count >= 1` / `cwd_entries_in_sys_path == 0`
    锁的是「条目被删」这一**实现**判据，路 B 下必红（条目不再被删）⇒ 按裁定帖
    「守卫」节第 1 条改为**行为断言**（毒文件未被执行）＋新口径读数（被拒条目数）。
    """
    j = _run_probe(escape_open=False)
    assert j["escape_hatch_open"] is False, j
    # 路 B：不删条目（宿主进程 sys.path 零改写）
    assert j["removed_count"] == 0, j
    assert j["cwd_entries_in_sys_path"] >= 1, j
    # 新口径：解析期拒绝面在位、被拒条目数 ≥1
    assert j["route_finder_installed"] is True, j
    assert j["refused_cwd_entry_count"] >= 1, j
    assert j["cwd_excluded_count"] >= 1, j
    # 语义钉子：该字段恰等于「被拒解析」的条目数（旧口径 removed_count 在路 B 下恒 0）
    assert j["cwd_excluded_count"] == j["refused_cwd_entry_count"], j
    # 行为腿：cwd 裸名毒文件未被执行（解析被拒，非「解析到别处」）
    assert j["poison_import"] == "ModuleNotFoundError", j
    assert j["poison_marker"] is False, j


def test_escape_open_reports_no_exclusion():
    """★ 核心：逃生口打开（本层保护关闭）时，无任何 cwd 条目被排除 ⇒ 计数必须为 0。

    旧式 `removed_count + _cwd_live` 在此会报 live(≥1)，把「仍存活」谎报成「已排除」；
    #273 路 B 下旧口径 `removed_count` 恒 0（两状态读数皆 0、不可区分）⇒ 本字段
    改取「被拒解析」口径，逃生口开时**不装 finder** ⇒ 0。
    """
    j = _run_probe(escape_open=True)
    assert j["escape_hatch_open"] is True, j
    assert j["route_finder_installed"] is False, j
    assert j["refused_cwd_entry_count"] == 0, j
    assert j["cwd_entries_in_sys_path"] >= 1, j
    assert j["cwd_excluded_count"] == 0, j
    assert j["cwd_excluded_count"] == j["refused_cwd_entry_count"], j
    # 行为腿（如实记录：开＝放弃本层保护 ⇒ cwd 裸名毒文件被执行）
    assert j["poison_import"] == "ok", j
    assert j["poison_marker"] is True, j


def test_two_states_are_distinguishable():
    """★ 核心：两状态下该字段读数必须不同（缺陷即「读数相同、不可区分」）。"""
    closed = _run_probe(escape_open=False)
    opened = _run_probe(escape_open=True)
    assert closed["cwd_excluded_count"] != opened["cwd_excluded_count"], (
        "逃生口开/关两状态下 cwd_excluded_count 读数相同 ⇒ 计数掩盖了防护差异",
        closed, opened)
