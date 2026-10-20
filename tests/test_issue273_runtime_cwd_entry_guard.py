# -*- coding: utf-8 -*-
"""守卫 · #273 路 B 的拒绝面必须**等价于**基线删除面（issue #273 裁定帖理由 2）
============================================================================
缺陷（`origin/main` = `47f364b`，即 146A 落地后的形态；CI run 38982722018 三腿全红）
    `lingshu/_pathguard.py::_CwdRouteFinder.find_spec` 的拒绝面是**动态**的：
    每次解析都重判**当前** `sys.path`，把「运行期新增的 cwd / 空串条目」也一并拒掉。

    而基线（`3f1e944`，删除面）是「`import lingshu` 时把**当时** `sys.path` 里的
    cwd / 空串条目**物理删除**（一次）」——scrub 跑完之后宿主新增的条目**不受影响**。

    ⇒ 集合关系：finder 的拒绝集合 S(t) 是删除集合 S₀ 的**真超集**（S(t) ⊋ S₀），
      超出的正是「运行期新增的 cwd 面条目」这一族。

    实测后果（本机 `wt150A @ 47f364b`）：`pytest tests/ --collect-only -q`
    ⇒ **3294 tests collected, 10 errors**，逐件同形：

        tests/test_silent_except_lint.py:42: in <module>
            from tools import silent_except_lint as S  # noqa: E402
        lingshu/_pathguard.py:364: in find_spec
            raise err
        E   ModuleNotFoundError: No module named 'tools'

    机理：`tools` 是仓根**顶层包**、只存在于仓根；`tests/` 下多件用
    `sys.path.insert(0, REPO_ROOT)` ＋ `from tools import ...`。件序上只要**先有件
    `import lingshu`**（finder 装上），后续件的顶层包导入即被拒 ⇒ **跨件顺序依赖**
    （单跑该件时它自己不 `import lingshu` ⇒ 侥幸绿，这也是 146A 定向测试没抓到它的
    原因）。

修复（本件 150A）
    拒绝面**冻结为「装 finder 那一刻」的 `sys.path` 快照**：只有**快照内**的
    cwd / 空串条目才被拒；快照之后新增的条目（按**多重集差**判定）全部保留
    ⇒ 拒绝面与删除面**逐条等价**（裁定帖理由 2）。安全面**只许等价、不许放宽**：

    ★**不是**「净化路径找不到就回退返回原始 spec」——那等于取消拒绝面，
      F1/F2 的「毒 cwd 文件未被执行」当场红。

本件守卫（覆盖 143B 与 146A 的共同盲区：**运行期新增的 cwd 条目 ⇒ 仓内顶层包仍可导入**）
    G-A（承重 · 回归钉）：子进程内先 `import lingshu`（finder 在位），**再**
        `sys.path.insert(0, <仓根绝对路径>)`，然后 `import tools` ⇒ **必须成功**。
        回退到 `47f364b` 形态 ⇒ 必红（`ModuleNotFoundError: No module named 'tools'`）。
    G-A2（真实形态 · 跨件顺序依赖）：同一 pytest 进程内按文件序跑「先 `import lingshu`
        的件」＋「`sys.path.insert(REPO_ROOT)` ＋ `from tools import ...` 的件」，
        收集必须 0 errors。
    G-B（**不得放宽** · F1/F2 同款判据）：子进程内 `import lingshu` 后，**快照内**的
        cwd / 空串条目**仍被拒**——`-c` 形态（F1）与 `-m` 形态（F2）下 cwd 毒
        `json.py` 均**未被执行**（MARKER 不存在）且只存在于 cwd 的裸名导入仍抛
        `ModuleNotFoundError`。
    G-C（计数如实）：`pathguard_report()` 的拒绝面计数**只计快照内**的 cwd 面条目
        ——快照外新增的 cwd 绝对路径条目**不计入**（同时 `cwd_entries_now` 会随之
        增加，两字段语义相反、不可相加）。

判据来源
    · `#273` 裁定帖理由 2：「拒绝集合与删除集合**逐条等价**」（本件的承重判据）；
    · 定因读数：`.tmp/prowatch/diag_ci_47f364b.md`（编排侧已定因，含决定性最小复现）；
    · 本件探针实测（`wt150A @ 47f364b`）：`import lingshu` 那一刻快照含仓根绝对路径
      3 次；`import tools` 时当前计数 6～40 次 ⇒ 多重集差恒非空。

不适用条件（如实）
    · 本件**不**主张「cwd 自有模块必须可导入」：`#273` 主诉的「只存在于 cwd 的裸名」
      在净化路径找不到时**仍抛** `ModuleNotFoundError`（与 143B 臂 B 同形态）。
    · 本件**不**覆盖「`import lingshu` 之后新增的 cwd 面条目上的毒模块」——那是与
      基线删除面等价的**刻意取舍**（安全面只许等价，不许放宽也不许扩成超集）。
    · ★**已知残余（如实记录，本件不修）**：仓内多件用
      `if str(REPO_ROOT) not in sys.path: sys.path.insert(0, str(REPO_ROOT))` 这类
      **守卫式插入**。路 B（#273 裁定）**不删条目** ⇒ 仓根「已在」⇒ 该守卫**不插**，
      于是快照内那几次仓根出现仍被剔 ⇒ 若同一进程里**没有任何**新增仓根条目
      （实测：`pytest tests/test_activation_empty_graph.py tests/test_silent_except_lint.py`
      这种「先 import lingshu 的件 ＋ 后 insert 的件」的**极小选择**），`from tools import`
      仍会红。基线（删除面）下该形态是绿的——差别**不在拒绝面**（拒绝面已与删除面
      逐条等价），而在**宿主自己的守卫**（基线物理删条目 ⇒ 守卫看到「不在」⇒ 重插；
      路 B 不删 ⇒ 守卫看到「在」⇒ 不插）。这是路 B「不改写宿主 `sys.path`」的固有代价，
      非本件可消除（消除它须改宿主 `sys.path` ＝回到被裁定否决的路线）。
      **CI 的实际调用是 `pytest tests/`（全量）**：pytest 会反复前插 rootdir
      （实测同一值 3 → 6 → 40 次）⇒ 新增仓根条目恒非空 ⇒ 该残余不触发。

运行（仓根）：python -X utf8 -m pytest tests/test_issue273_runtime_cwd_entry_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 快照内 cwd 面条目（F1/F2 同款）的毒件：只存在于 cwd、非标准库名。
_CWD_ONLY_POISON = "c273a_cwd_only_probe"


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("LINGSHU_ALLOW_CWD_IMPORTS", None)
    env.pop("LINGSHU_COMPONENT_ROOT", None)
    # 仓根经 PYTHONPATH 声明（模拟「包已可导入」的常规部署面）
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run(code, cwd, args=None, extra=None):
    env = _env()
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, "-B", "-X", "utf8"] + (args or ["-c", code]),
        cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)


def _make_poison_dir(base, tag):
    """建一个「cwd 毒目录」：毒 `json.py` ＋ 只存在于 cwd 的裸名毒件。"""
    d = os.path.join(base, "poison_%s" % tag)
    os.makedirs(d, exist_ok=True)
    poison_body = (
        "import os as _os\n"
        "with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"
        " 'MARKER.txt'), 'w', encoding='utf-8') as _f:\n"
        "    _f.write('EXECUTED ' + _os.path.basename(__file__) + '\\n')\n"
    )
    with open(os.path.join(d, "json.py"), "w", encoding="utf-8") as f:
        f.write(poison_body)
    with open(os.path.join(d, _CWD_ONLY_POISON + ".py"), "w", encoding="utf-8") as f:
        f.write(poison_body)
    return d


# ------------------------------------------------------------------ G-A（承重 · 回归钉）
def test_ga_runtime_inserted_repo_root_entry_still_imports_tools():
    """★承重回归钉：`import lingshu`（finder 在位）**之后**新增的仓根绝对路径条目
    不得被拒 —— 仓内**顶层包** `tools` 必须仍可导入。

    形态逐字取自派工书 §3 G-A：子进程内**先** `import lingshu`，**再**
    `sys.path.insert(0, <仓根绝对路径>)`，然后 `import tools` ⇒ 必须成功。

    回退到 `47f364b` 形态（finder 按值剔除当前 `sys.path` 的全部 cwd 面条目）⇒
    必红：`ModuleNotFoundError: No module named 'tools'`。
    """
    code = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"          # 模拟「先有件 import lingshu」的前置面
        "import lingshu\n"                  # finder 装上（快照在此刻冻结）
        "sys.path.insert(0, %r)\n"          # 运行期新增的 cwd 面条目（快照外）
        "from tools import silent_except_lint as _S\n"
        "print('##J##' + json.dumps({'ok': True, 'file': getattr(_S, '__file__', None)}))\n"
    ) % (REPO, REPO)
    r = _run(code, cwd=REPO)
    assert r.returncode == 0, (
        "G-A 红：`import lingshu` 之后新增的仓根绝对路径条目被 finder 误拒 —— "
        "仓内顶层包 tools 导不进来（这正是 47f364b 上 10 件收集 ERROR 的形态）\n"
        "rc=%s\nstdout=%r\nstderr=%r" % (r.returncode, r.stdout[-800:], r.stderr[-800:]))
    assert "##J##" in (r.stdout or ""), (
        "G-A：子进程未打印读数\nstdout=%r\nstderr=%r" % (r.stdout[-400:], r.stderr[-400:]))
    payload = json.loads((r.stdout or "").split("##J##", 1)[1].splitlines()[0])
    assert payload.get("ok") is True, payload


# ------------------------------------------------- G-A2（真实形态 · 跨件顺序依赖）
def test_ga2_cross_file_order_dependency_with_unconditional_insert():
    """真实形态（**跨件顺序依赖**）：同一 pytest 进程内，先收集「顶层 `import lingshu`
    的件」，再收集「`sys.path.insert(0, <仓根绝对路径>)` ＋ `from tools import ...`
    的件」⇒ 收集必须 0 errors。

    两件都写在 tmp 目录里，pytest 的 cwd 仍是**仓根**（`python -m pytest` ⇒
    `sys.path[0]` = cwd = 仓根 ⇒ 仓根条目落在**快照内**，正是被拒面）；配置用 tmp 里的
    空 `-c pytest.ini`（rootdir 不落仓根）——这只影响 rootdir 发现，不改 sys.path 形态。
    第二件**无条件** `sys.path.insert`：刻意不用仓内多件采用的
    `if REPO_ROOT not in sys.path` 守卫——路 B 不删条目 ⇒ 该守卫会看到仓根「已在」
    而**不插**，此时红读数反映的是**宿主守卫的行为差异**，不是拒绝面口径
    （该残余代价见模块头「不适用条件」节）。

    回退到 `47f364b` 形态（finder 按值剔除**当前** `sys.path` 的全部 cwd 面条目）⇒
    第二件新插的仓根条目**也被剔** ⇒ 必红（`ModuleNotFoundError: No module named
    'tools'`，即 CI 上 10 件收集 ERROR 的最小真实复现）。
    """
    work = tempfile.mkdtemp(prefix="i273ga2_")
    first = os.path.join(work, "test_a_first_imports_lingshu.py")
    second = os.path.join(work, "test_b_tools_import.py")
    ini = os.path.join(work, "pytest.ini")
    with open(ini, "w", encoding="utf-8") as f:
        f.write("[pytest]\n")
    with open(first, "w", encoding="utf-8") as f:
        f.write("import lingshu  # noqa: F401  （finder 装上 ⇒ 快照在此刻冻结）\n"
                "\n\ndef test_placeholder():\n    assert True\n")
    with open(second, "w", encoding="utf-8") as f:
        f.write("import sys\n"
                "sys.path.insert(0, %r)   # 运行期新增的 cwd 面条目（无条件）\n"
                "from tools import silent_except_lint as _S  # noqa: F401,E402\n"
                "\n\ndef test_placeholder():\n    assert True\n" % REPO)
    # cwd = 仓根（真实 CI 形态：`python -m pytest` ⇒ sys.path[0] = cwd = 仓根）
    r = _run("", cwd=REPO, args=["-m", "pytest", first, second,
                                 "--collect-only", "-q", "--no-header",
                                 "-p", "no:cacheprovider", "-c", ini])
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0 and "Interrupted" not in out, (
        "G-A2 红：跨件顺序依赖仍在 —— 第二件收集失败（新插的仓根条目被 finder 误拒，"
        "`tools` 导不进来）\nrc=%s\n%s" % (r.returncode, out[-1200:]))
    assert "2 tests collected" in out, (
        "G-A2：两件应各收 1 件\nrc=%s\n%s" % (r.returncode, out[-800:]))


# ------------------------------------------------- G-B（不得放宽 · F1/F2 同款判据）
def test_gb1_snapshot_cwd_entry_still_refused_c_form():
    """F1 同款（`-c` 形态）：快照内的 `''`（= cwd）条目**仍被拒**。

    · cwd 毒 `json.py` **未被执行**（MARKER 不存在）且引擎照常装配 rc=0；
    · 只存在于 cwd 的裸名毒件 `c273a_cwd_only_probe` 仍抛 `ModuleNotFoundError`
      且其 MARKER 不存在 ⇒ 拒绝面**未被放宽**。
    """
    work = tempfile.mkdtemp(prefix="i273gb1_")
    d = _make_poison_dir(work, "gb1")
    # ★探针顺序：`import lingshu` 必须在 `import json` **之前**——否则 cwd 毒 json.py
    #   会在 finder 装上之前被解析并执行（本层明确不覆盖「import lingshu 之前已发生
    #   的 cwd 面解析」）。
    code = (
        "import sys\n"
        "import lingshu\n"                      # finder 装上（快照含 -c 形态的 ''）
        "from lingshu.core.core import SpacetimeMemoryEngine\n"
        "import os, json\n"
        "SpacetimeMemoryEngine(':memory:')\n"
        "out = {'constructed': True}\n"
        "try:\n"
        "    import %s as _m\n"
        "    out['cwd_only'] = 'imported'\n"
        "except ModuleNotFoundError:\n"
        "    out['cwd_only'] = 'ModuleNotFoundError'\n"
        "out['marker_json'] = os.path.exists(os.path.join(os.getcwd(), 'MARKER.txt'))\n"
        "print('##J##' + json.dumps(out))\n" % _CWD_ONLY_POISON
    )
    r = _run(code, cwd=d)
    assert r.returncode == 0, (
        "G-B1：引擎在 cwd 毒目录下应照常装配 rc=0\nrc=%s\nstdout=%r\nstderr=%r"
        % (r.returncode, r.stdout[-400:], r.stderr[-800:]))
    assert "##J##" in (r.stdout or ""), (
        "G-B1：探针无输出\nstdout=%r\nstderr=%r" % (r.stdout[-400:], r.stderr[-800:]))
    payload = json.loads((r.stdout or "").split("##J##", 1)[1].splitlines()[0])
    assert payload["constructed"] is True, payload
    assert payload["marker_json"] is False, (
        "G-B1 红：cwd 毒 json.py 被**执行**了（快照内 cwd 条目的拒绝面被放宽）", payload)
    assert payload["cwd_only"] == "ModuleNotFoundError", (
        "G-B1 红：只存在于 cwd 的裸名模块不再被拒（拒绝面被放宽）", payload)
    assert not os.path.exists(os.path.join(d, "MARKER.txt")), (
        "G-B1 红：cwd 毒目录出现 MARKER.txt（毒文件被执行）")


def test_gb2_snapshot_cwd_entry_still_refused_m_form():
    """F2 同款（`-m` 形态，`sys.path[0]` = 启动 cwd 的**绝对路径**）：快照内的该条目
    **仍被拒** —— cwd 毒 `json.py` 未被执行、引擎照常装配。
    """
    work = tempfile.mkdtemp(prefix="i273gb2_")
    runner_root = os.path.join(work, "runner_mod")
    pkg = os.path.join(runner_root, "f273mod")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(pkg, "__main__.py"), "w", encoding="utf-8") as f:
        f.write("from lingshu.core.core import SpacetimeMemoryEngine\n"
                "SpacetimeMemoryEngine(':memory:')\n"
                "print('CONSTRUCTED')\n")
    d = _make_poison_dir(work, "gb2")
    r = _run("", cwd=d, args=["-m", "f273mod"],
             extra={"PYTHONPATH": REPO + os.pathsep + runner_root})
    assert r.returncode == 0 and "CONSTRUCTED" in (r.stdout or ""), (
        "G-B2：-m 形态下引擎应照常装配 rc=0\nrc=%s\nstdout=%r\nstderr=%r"
        % (r.returncode, r.stdout[-400:], r.stderr[-800:]))
    assert not os.path.exists(os.path.join(d, "MARKER.txt")), (
        "G-B2 红：-m 形态下 cwd 毒 json.py 被**执行**了（快照内 cwd 条目的拒绝面被放宽）")


# ------------------------------------------------------------------ G-C（计数如实）
def test_gc_refusal_count_only_counts_snapshot_entries():
    """`pathguard_report()` 的拒绝面计数**只计快照内**的 cwd / 空串条目：
    快照外新增的 cwd 绝对路径条目**不计入**（同时 `cwd_entries_now` 随之 +1）。

    两字段语义相反、不可相加：`refused_cwd_entry_count` = 「被拒解析的条目数」
    （启动期快照口径，冻结）；`cwd_entries_now` = 「条目**仍在** `sys.path` 里」
    （判定时口径）。
    """
    work = tempfile.mkdtemp(prefix="i273gc_")
    d = _make_poison_dir(work, "gc")
    code = (
        "import sys\n"
        "import lingshu\n"        # finder 装上；快照含 -c 形态的 ''（cwd 面）
        "from lingshu import _pathguard as pg\n"
        "import os, json\n"
        "before = pg.pathguard_report()\n"
        "sys.path.insert(0, os.getcwd())\n"   # 运行期新增的 cwd 绝对路径条目（快照外）
        "after = pg.pathguard_report()\n"
        "print('##J##' + json.dumps({\n"
        "    'before': {k: before[k] for k in (\n"
        "        'refused_cwd_entry_count', 'refused_cwd_kinds', 'cwd_entries_now',\n"
        "        'route_finder_installed')},\n"
        "    'after': {k: after[k] for k in (\n"
        "        'refused_cwd_entry_count', 'refused_cwd_kinds', 'cwd_entries_now',\n"
        "        'route_finder_installed')},\n"
        "}, ensure_ascii=False))\n"
    )
    r = _run(code, cwd=d)
    assert r.returncode == 0, (
        "G-C：子进程失败 rc=%s\nstdout=%r\nstderr=%r"
        % (r.returncode, r.stdout[-400:], r.stderr[-800:]))
    payload = json.loads((r.stdout or "").split("##J##", 1)[1].splitlines()[0])
    before, after = payload["before"], payload["after"]
    # ① 快照内的 cwd / 空串条目计入（`''` 至少一条）
    assert before["route_finder_installed"] is True, payload
    assert before["refused_cwd_entry_count"] >= 1, payload
    assert before["cwd_entries_now"] >= 1, payload
    # ② 快照外新增的 cwd 绝对路径条目**不计入**拒绝面
    assert after["refused_cwd_entry_count"] == before["refused_cwd_entry_count"], (
        "G-C 红：快照外新增的 cwd 绝对路径条目被计入拒绝面（拒绝面 ⊋ 删除面）", payload)
    # ③ 但它确实**仍在** sys.path 里（`cwd_entries_now` 如实 +1）⇒ 两字段可区分
    assert after["cwd_entries_now"] == before["cwd_entries_now"] + 1, (
        "G-C 红：新增条目未被 `cwd_entries_now` 如实观测（计数面失真）", payload)
    # ④ 只报「种类」、不含本机绝对路径
    for st in (before, after):
        assert all(":" not in k for k in st["refused_cwd_kinds"]), payload
