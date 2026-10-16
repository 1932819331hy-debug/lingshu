# -*- coding: utf-8 -*-
"""守卫 · #381（运行期把**相对**路径写进 `sys.path`）
====================================================================================
缺陷（issue #381，@kudowyv 2026-10-09，P0，基线 `83cce59`）：
  `lingshu/core/core.py` 的 `pattern_separation_scan` / `reconstruct_scene` 在
  **调用期**执行 `_s.path.insert(0, _kb)`，而 `_kb` 是**相对**字面量
  （`r'<local-path> Files\\2_ai\\knowledge-base'`，`os.path.isabs` 为 False）。
  相对 `sys.path` 条目的解析目标由**判定时的 cwd** 决定 ⇒ 这是把本包 import 期
  刚由 `_pathguard.scrub()` 消除的 cwd/空串解析面（issue #156 v2）又插回来。

  受影响面已由 #156 v2 / #288·D-2 收窄到**非白名单裸名**：
  · **白名单组件名**（`component_resolver.COMPONENT_NAMES` 内，含
    `pattern_separation` / `scene_reconstruction`）由 meta_path finder 拦截，
    只走受控面 ⇒ 插入的相对条目对它们**无效**（`e2b87a9` 已关闭这一半）；
  · **非白名单裸名**（任意其它名，含标准库名）走**普通** `sys.path` 解析
    ⇒ 插入的相对条目**有效**，cwd 下同名文件可被抢先导入并在本进程执行。

  本守卫钉的就是第二半——「相对条目不得进 `sys.path`」。

判据来源：
  · `lingshu/_pathguard.py::_is_relative_entry`（相对条目目标由 cwd 决定 ⇒ 不可信）；
  · `lingshu/_pathguard.py::scrub()`（消除 cwd/空串条目，本包 import 前已执行）；
  · `tests/test_issue108_import_path_guard.py`（同族先例：`lingshu/gen` 三模块
    的 import 期相对条目，同款缺陷、同款判据）；
  · `lingshu/nn/rust_bridge.py::resolve_hex_exe`（#488 先例：可执行面一律要求
    `is_absolute()`，相对值 fail-closed）。

断言（每条都钉「缺陷不再存在」，不是「代码能跑」）：
  A **运行期**：干净子进程里 import `lingshu`（触发 scrub）→ 调两个方法 →
     `sys.path` **新增条目中不得有任何相对条目**。缺陷形态下新增恰为
     `['<local-path> Files\\2_ai\\knowledge-base']`（相对）⇒ 首条即红。
  B **因果臂**：cwd 下放一个**非白名单裸名**的毒模块（执行即落 MARKER 文件），
     调两个方法后再导入该名 ⇒ 必须 `ImportError`、且 MARKER 不得存在。
     A 判「路径没变」，B 判「路径没变**所以**劫持不成立」，二者互补。
  C **源码形态**：`core.py` 里每一处 `sys.path.insert` 都必须处在
     `os.path.isabs(...)` 为真的分支内（AST 判定，抽掉守卫即命中）。
  D **正对照**：绝对路径仍按原样插入（能力未被误删）——否则 C 可被「整段删掉」代偿。
     A/B 判缺陷消失，C/D 判修法未过头。

运行（lingshu 仓根）：
  python -X utf8 -m pytest tests/test_issue381_relative_sys_path_insert_guard.py -q --no-header
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(REPO, "lingshu", "core", "core.py")

# 非白名单裸名（不在 component_resolver.COMPONENT_NAMES 内、且非标准库）
POISON_NAME = "hexcolor_probe_381"

_POISON_TMPL = (
    "import os\n"
    "open(os.path.join(os.path.dirname(__file__), 'MARKER_381.txt'), 'w').close()\n"
    "VALUE = 'poison'\n"
)

_PROBE = r'''
import json, os, sys
from lingshu.core.core import SpacetimeMemoryEngine

e = SpacetimeMemoryEngine(":memory:")
before = list(sys.path)
r1 = e.pattern_separation_scan(limit=3)
r2 = e.reconstruct_scene("probe", depth=1)
after = list(sys.path)
added = [p for p in after if p not in before]
rel = [p for p in added if not os.path.isabs(p)]

try:
    import hexcolor_probe_381 as m
    poisoned = {"file": getattr(m, "__file__", None)}
except ImportError as exc:
    poisoned = {"error": str(exc)}

print("##J##" + json.dumps({
    "added": added,
    "rel": rel,
    "marker": os.path.isfile("MARKER_381.txt"),
    "poisoned": poisoned,
    "scan_err": (r1 or {}).get("error"),
    "recon_err": (r2 or {}).get("error"),
}))
'''


def _run_probe(work: str) -> dict:
    """干净子进程（cwd=work，仓根经 PYTHONPATH 声明）跑探针。"""
    env = dict(os.environ)
    env.pop("LINGSHU_ALLOW_CWD_IMPORTS", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", _PROBE],
                       cwd=work, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0, (
        "探针子进程失败 rc=%s\nstdout=%s\nstderr=%s"
        % (r.returncode, r.stdout[-800:], r.stderr[-1200:]))
    for line in (r.stdout or "").splitlines():
        if line.startswith("##J##"):
            return __import__("json").loads(line[len("##J##"):])
    raise AssertionError("探针无输出：stdout=%r stderr=%r"
                         % (r.stdout[-800:], r.stderr[-800:]))


def _poison_dir(tmp_path):
    with open(os.path.join(str(tmp_path), POISON_NAME + ".py"), "w",
              encoding="utf-8") as f:
        f.write(_POISON_TMPL)
    return str(tmp_path)


# ---------------------------------------------------------------- A / B
def test_a_b_no_relative_entry_inserted_and_no_cwd_hijack(tmp_path):
    """A＋B：两个方法调用后，`sys.path` 无相对新增；cwd 毒模块未被导入/执行。"""
    work = _poison_dir(tmp_path)
    j = _run_probe(work)
    assert j["rel"] == [], (
        "调用期往 sys.path 插入了**相对**条目（#381）：%r" % (j["rel"],))
    assert j["marker"] is False, (
        "cwd 下同名非白名单裸模块被执行（MARKER 落地）⇒ 劫持成立：#381")
    assert "error" in j["poisoned"], (
        "cwd 毒模块被成功导入：%r" % (j["poisoned"],))


def test_b2_cwd_hijack_would_be_real_if_entry_inserted(tmp_path):
    """B2（因果正对照）：**手动**插入相对条目 ⇒ 毒模块确实被导入并执行。

    证明 A/B 的「没劫持」来自「没插条目」，而不是「cwd 毒模块本来就导不进来」
    （消融实验：若不插也导得进来，则 A/B 不构成判据）。
    """
    work = _poison_dir(tmp_path)
    code = (
        "import os, sys\n"
        "from lingshu.core.core import SpacetimeMemoryEngine\n"
        "SpacetimeMemoryEngine(':memory:')\n"
        "sys.path.insert(0, '.')\n"
        "import %s as m\n"
        "print('##J##' + m.__file__)\n" % POISON_NAME
    )
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       cwd=work, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0, (r.stdout[-500:], r.stderr[-1200:])
    assert os.path.isfile(os.path.join(work, "MARKER_381.txt")), (
        "手动插入相对条目后毒模块仍未执行 ⇒ 本守卫的因果不成立（须复核判据）")
    assert "##J##" in (r.stdout or ""), r.stdout[-500:]


# ---------------------------------------------------------------- C / D
def _insert_calls_guarded(src: str):
    """返回 [(行号, 是否处在 `os.path.isabs(...)` 为真的分支内)]。"""
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def is_path_insert(call: ast.Call) -> bool:
        f = call.func
        return (isinstance(f, ast.Attribute) and f.attr == "insert"
                and isinstance(f.value, ast.Attribute) and f.value.attr == "path")

    def guard_text(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, ast.If):
                return ast.dump(cur.test)
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return ""
            cur = parents.get(cur)
        return ""

    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and is_path_insert(node):
            out.append((node.lineno, "isabs" in guard_text(node)))
    return out


def test_c_every_sys_path_insert_is_isabs_guarded():
    """C：`core.py` 每一处 `sys.path.insert` 都在 `isabs` 守卫分支内。"""
    src = open(CORE, encoding="utf-8").read()
    calls = _insert_calls_guarded(src)
    assert calls, "core.py 里未找到 `sys.path.insert` ⇒ 本守卫的形态锚失效，须更新"
    bad = [ln for ln, ok in calls if not ok]
    assert bad == [], (
        "core.py:%r 的 `sys.path.insert` 未被 `os.path.isabs` 守卫（#381）" % (bad,))


def test_d_absolute_entry_still_inserted(tmp_path):
    """D（正对照）：`_kb` 为**绝对**路径时仍按原样插入——修法未把能力一并删掉。

    把 `os.path.isabs` 临时钉为 True（模拟本机绝对形态），调两个方法后该条目
    必须出现在 `sys.path` 里。若有人把整段插入删掉（而非加守卫），本断言判红。
    """
    code = (
        "import json, os, sys\n"
        "from lingshu.core.core import SpacetimeMemoryEngine\n"
        "KB = r'<local-path> Files\\2_ai\\knowledge-base'\n"
        "e = SpacetimeMemoryEngine(':memory:')\n"
        "real = os.path.isabs\n"
        "os.path.isabs = lambda p: True if p == KB else real(p)\n"
        "try:\n"
        "    e.pattern_separation_scan(limit=1)\n"
        "finally:\n"
        "    os.path.isabs = real\n"
        "print('##J##' + json.dumps({'in_path': KB in sys.path}))\n"
    )
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       cwd=str(tmp_path), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0, (r.stdout[-800:], r.stderr[-1200:])
    assert '"in_path": true' in (r.stdout or "").lower(), (
        "绝对 `_kb` 未被插入 ⇒ 修法把能力一并删掉了（#381 正对照）：%s"
        % (r.stdout[-400:],))

