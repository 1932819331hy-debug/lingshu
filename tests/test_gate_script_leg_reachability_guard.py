# -*- coding: utf-8 -*-
"""脚本腿可达性守卫 —— `SCRIPT_TESTS` 件里的 `test_*` 不得落在两条腿之外（#485）
============================================================================
缺陷（#485，实测）：`gate.yml` 的两条腿按**文件**互补划分——
  · 列进 `SCRIPT_TESTS` ⇒ 脚本腿 `python -X utf8 <file>`（执行到 `main()` / `run_checks()`）；
  · 同一件 ⇒ pytest 腿整文件 `--ignore`。
一件若「有 `main()`」且「另含 `main()` 不调用的 `test_*` 函数」，那些 `test_*`
**两条腿都不执行**：脚本腿不调它们，pytest 腿又整文件不收集 ⇒ 绿 / 红无人知。
实例：`tests/test_brain_store.py` 的 #364×2 / #366×2 四个桩式守卫
（已按 #485 拆到 `tests/test_brain_store_stub_guards.py`，新件不列入清单 ⇒ 走 pytest 腿）。

判据（本件）：对 `SCRIPT_TESTS ∩ tests/test_*.py` 的**每一件**——
  设 U = 该件**模块级绑定名**（顶层 def/class、赋值目标、import 别名）；
     L = U 中**本件自有**的绑定（顶层 def/class、赋值；不含 import 别名）；
     reach = 自脚本入口（`main` / `run_checks`）出发、在 U 内按**名字引用**可达的闭包；
  对每个顶层 `test_*` 函数（与 `Test*` 类），若「名字 ∉ reach」且满足其一 ⇒ **判红**：
     · 它引用的**本件自有**名字里有 reach 之外的（L \\ reach）⇒ 它自己带脚本腿不跑的机器；
     · 它一个**本件自有**名字都不引用（只碰 import/内置/形参）⇒ 它自成一体、无从由脚本腿覆盖。
  语义：这两条都是「该 `test_*` 不被脚本腿执行」的证据；反之，只引用脚本腿也会执行的
  本件自有件（薄包装 `assert main() == 0`、`p, f = run_checks(); assert f == 0`、
  参数化包装碰 `main()` 同样碰的 `_PASS` / `_FAIL`）⇒ 豁免。

边界（如实，勿当全能）：
  · 本判据是**静态近似**，不是执行轨迹。它会漏掉「既碰了 reach 内名字、又自带
    reach 外代码」的混合形态（例如 `_PASS.append(1)` 后另调一个 pytest 专属 helper）。
    那种形态仍须靠 `pytest --collect-only` 侧的量具（`tests/test_gate_coverage.py`）
    与执行侧读数（脚本腿 settrace）核。
  · 本件**不改** `tests/test_gate_coverage.py` 的判据（那件是**整件**口径：列进清单
    **或** pytest 可收集；本件是**件内函数**口径）。两者互补。

量具自检（同族纪律：判据不得失明）：
  · 阳性对照①：合成「脚本式件 + `main()` 不调用的 `test_*`」⇒ 必命中；
  · 阳性对照②：把本仓**已拆出**的 4 个守卫拼回原件的形态（还原 #485 缺陷态）⇒
    必命中那 4 条（本判据对**真实历史缺陷**的判别力，不靠合成样本）；
  · 阴性对照：薄包装形 / `run_checks` 形 ⇒ 不得命中；
  · 清单自检：必须真从 `gate.yml` 读到 `SCRIPT_TESTS`，且所列件都在场。

运行（仓根）：python -X utf8 -m pytest tests/test_gate_script_leg_reachability_guard.py -q
"""
from __future__ import annotations

import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_YML = os.path.join(REPO, ".github", "workflows", "gate.yml")
TESTS_DIR = os.path.join(REPO, "tests")

#: 脚本腿的入口函数名（`gate.yml` 的脚本腿就是 `python -X utf8 <file>`，
#: 真正执行体收在 `main()` / `run_checks()` 里——见 tests/README.md「改造注记」）。
ENTRY_NAMES = ("main", "run_checks")

_SPLIT_ANCHOR = "class _CaptureClient"
_SPLIT_FROM = "tests/test_brain_store.py"
_SPLIT_TO = "tests/test_brain_store_stub_guards.py"
_SPLIT_GUARDS = (
    "test_364_add_perception_passes_sensitivity_role_condition_space",
    "test_364_add_perception_rejects_unknown_kwargs",
    "test_366_state_ledger_keyed_by_node_id_not_entity_name",
    "test_366_entity_name_only_ledger_does_not_leak_into_nodes",
)


def _script_tests_from_gate_yml():
    """读 `gate.yml` 的 `SCRIPT_TESTS`（YAML 折叠标量 `>-` ⇒ 换行折为空格，按空白分词）。

    与 `tests/test_gate_coverage.py` 同款极窄解析（不引 YAML，避免守卫自身新增依赖）。
    """
    with open(GATE_YML, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    entries = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if re.match(r"\s*SCRIPT_TESTS\s*:", line):
            tail = line.split(":", 1)[1].strip()
            if tail and tail not in (">", ">-", "|", "|-", ">+", "|+"):
                entries.extend(tail.split())
            i += 1
            while i < n:
                nxt = lines[i]
                if nxt.strip() == "" or not nxt[:1].isspace():
                    break
                entries.extend(nxt.split())
                i += 1
            break
        i += 1
    return [e for e in entries if e.startswith("tests/")]


def _assign_names(node):
    """赋值目标里的绑定名（支持 `a, b = ...` / `a: int = ...` / `a += ...`）。"""
    out = set()
    targets = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    for t in targets:
        for sub in ast.walk(t):
            if isinstance(sub, ast.Name):
                out.add(sub.id)
    return out


def _bound_names(tree):
    """模块级绑定名集合 U（顶层 def/class、赋值、import 别名）。"""
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            out |= _assign_names(node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.add(alias.asname or alias.name)
    return out


def _local_bound_names(tree):
    """本件**自有**的模块级绑定 L（顶层 def/class、赋值；**不含** import 别名）。

    import 别名几乎人人引用（`BrainStore` / `os`），拿它当「被脚本腿覆盖」的证据会
    把真正的盲区放过去（#485 的守卫就与 `main()` 共用同一个导入类）。
    """
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            out |= _assign_names(node)
    return out


def _refs(node):
    """节点体内出现的**裸名字**（`ast.Name`；属性名不计——`.get` / `.append` 会污染）。"""
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def _reachable(tree, universe):
    """自脚本入口出发的可达闭包（限定在 U 内；顶层 def/class 递归展开）。"""
    tops = {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    reach = set()
    frontier = []
    for name in ENTRY_NAMES:
        if name in tops:
            reach.add(name)
            frontier.append(tops[name])
    while frontier:
        node = frontier.pop()
        for name in _refs(node) & universe:
            if name not in reach:
                reach.add(name)
                if name in tops:
                    frontier.append(tops[name])
    return reach


def _blind_tests(source, label="<mem>"):
    """返回该源文件里「落在两条腿之外」的顶层 `test_*` / `Test*` 行清单。

    判红条件：名字 ∉ reach 且（引用的本件自有名里有 reach 之外的，或一个本件自有名都不引用）。
    """
    tree = ast.parse(source, filename=label)
    universe = _bound_names(tree)
    local = _local_bound_names(tree)
    reach = _reachable(tree, universe)
    out = []
    for node in tree.body:
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")):
            kind = "def"
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            kind = "class"
        else:
            continue
        if node.name in reach:
            continue                       # 脚本腿会执行到它本人
        touched = _refs(node) & local
        if touched and not (touched - reach):
            continue                       # 只碰脚本腿同样执行的本件自有件 ⇒ 覆盖重复
        out.append("%s:%d: %s %s" % (label, node.lineno, kind, node.name))
    return out


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- 量具自检

def test_gate_yml_script_tests_parsed():
    """量具自检：必须真从 gate.yml 读到 SCRIPT_TESTS，且所列件都在场。"""
    script_tests = _script_tests_from_gate_yml()
    assert script_tests, (
        "没从 %s 读到任何 SCRIPT_TESTS 条目 ⇒ 解析器坏了，本判据此时**无判别力**"
        "（会静默通过）。" % os.path.relpath(GATE_YML, REPO))
    missing = [p for p in script_tests if not os.path.isfile(os.path.join(REPO, p))]
    assert not missing, "SCRIPT_TESTS 里列了不存在的文件：%r" % missing


def test_synthetic_blind_guard_is_caught():
    """阳性对照①（合成）：脚本式件 + `main()` 不调用的 `test_*` ⇒ 必命中。"""
    src = (
        "import os\n"
        "\n"
        "\n"
        "def helper(x):\n"
        "    return x\n"
        "\n"
        "\n"
        "def main():\n"
        "    return 0\n"
        "\n"
        "\n"
        "def test_blind_guard():\n"
        "    assert helper(1) == 1\n"
    )
    hits = _blind_tests(src, "syn.py")
    assert len(hits) == 1 and "test_blind_guard" in hits[0], (
        "合成缺陷态（main() 不调用的 test_*）没被判红 ⇒ 本判据失明：%r" % (hits,))
    # 自证②：同一个 test_* 一旦被 main() 调用，就不得再命中（判据不是「凡 test_* 皆红」）。
    src2 = src.replace("def main():\n    return 0\n",
                       "def main():\n    test_blind_guard()\n    return 0\n")
    assert _blind_tests(src2, "syn2.py") == [], "入口调用后仍判红 ⇒ 判据方向错了"


def test_no_entry_file_is_flagged():
    """阳性对照③（反向复发面）：**没有** `main()` / `run_checks()` 的件若被列进
    `SCRIPT_TESTS`，脚本腿 `python -X utf8 <file>` 会以 rc=0 空跑（**静默绿**）——
    它的每个 `test_*` 都必须被判红。
    """
    src = ("import os\n"
           "\n"
           "\n"
           "def test_a():\n"
           "    assert os.sep\n"
           "\n"
           "\n"
           "def test_b():\n"
           "    assert 1\n")
    hits = _blind_tests(src, "noentry.py")
    assert len(hits) == 2, (
        "无入口件（`python <file>` 静默 rc=0）的 test_* 没被判红 ⇒ 本判据对"
        "「pytest 式件误入 SCRIPT_TESTS」失明：%r" % (hits,))


def test_wrapper_shapes_are_not_flagged():
    """阴性对照：薄包装 / `run_checks` 形（引用脚本入口）⇒ 不得命中。"""
    cases = {
        "thin_main.py": "def main():\n    return 0\n\n\ndef test_wrap():\n    assert main() == 0\n",
        "thin_rc.py": "def run_checks():\n    return 1, 0\n\n\n"
                      "def test_wrap():\n    p, f = run_checks()\n    assert f == 0\n",
        "param_state.py": "_PASS = []\n\n\ndef main():\n    _PASS.append(1)\n    return 0\n"
                          "\n\ndef test_state():\n    _PASS.clear()\n    assert not _PASS\n",
    }
    for label, src in cases.items():
        assert _blind_tests(src, label) == [], (
            "合法形被误判为盲区（假阳性）：%s" % label)


def test_real_split_is_detected_when_rejoined():
    """阳性对照②（真件、定点）：把已拆出的 4 个守卫拼回原件的形态 ⇒ 必命中那 4 条。

    还原的正是 #485 的缺陷态（`main()` 不调用、又列在 `SCRIPT_TESTS` 里），
    故本判据对**真实历史缺陷**有判别力——不是只对合成样本有效。
    """
    src_from = _read(_SPLIT_FROM)
    src_to = _read(_SPLIT_TO)
    assert _SPLIT_ANCHOR in src_to, (
        "%s 里找不到搬运锚点 %r ⇒ 本对照失效（不得静默通过）" % (_SPLIT_TO, _SPLIT_ANCHOR))
    block = src_to[src_to.index(_SPLIT_ANCHOR):]
    hits = _blind_tests(src_from + "\n\n" + block, "rejoined.py")
    flagged = {h.rsplit(" ", 1)[-1] for h in hits}
    missing = sorted(set(_SPLIT_GUARDS) - flagged)
    assert not missing, (
        "拼回缺陷态后，这 %d 条守卫没被判红 ⇒ 本判据对 #485 真实缺陷失明：%r\n命中=%r"
        % (len(missing), missing, hits))


# ---------------------------------------------------------------- 主判据

def test_no_blind_test_functions_in_script_leg_files():
    """主判据：`SCRIPT_TESTS` 件里的每个顶层 `test_*` 都必须被脚本腿触达（或引用入口）。"""
    offenders = []
    for rel in _script_tests_from_gate_yml():
        path = os.path.join(REPO, rel)
        if not os.path.isfile(path):
            continue                       # 缺件由量具自检与 test_gate_coverage 判红
        with open(path, encoding="utf-8") as fh:
            offenders.extend(_blind_tests(fh.read(), rel))
    assert offenders == [], (
        "以下 `test_*` 落在两条腿之外（#485）：文件列在 `SCRIPT_TESTS` 里 ⇒ pytest 腿"
        "整文件 `--ignore`，而 `main()` / `run_checks()` 又调不到它们 ⇒ CI 里绿 / 红"
        "无人知：\n  " + "\n  ".join(offenders)
        + "\n\n修法（二选一，勿放宽断言）：① 把它们拆到一个**不列入** `SCRIPT_TESTS`"
        " 的 pytest 式新件（体例见 tests/test_brain_store_stub_guards.py）；"
        "② 让 `main()` / `run_checks()` 真的调用它们（脚本腿即覆盖，"
        "但 pytest 腿仍不收集 ⇒ 若还要 pytest 侧可见，仍须选①）。")


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", os.path.abspath(__file__),
         "-q", "--no-header"]))
