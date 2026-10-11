# -*- coding: utf-8 -*-
"""批次 F / issue #23 · `tools/coverage_map_lint.py` 的验收测试（守卫）

本件机制：**零引用（零覆盖）模块只准减、不准增**（issue #23「新模块未测即红」）。
  · 基线白名单之外的零引用模块出现 ⇒ 退出 1（红）；
  · 已列入基线的零引用模块允许存在（本批**不**补测试，只上尺子）。

断言组
------
A 量具自证（lint 必须真在数）：
   · 合成「测试正文从未提到」的模块 ⇒ 进零引用清单；
   · 合成「测试正文提到过」的模块 ⇒ 不进清单。
B 真实仓库（出货基线）⇒ 退出码 0、无新增零引用模块。
C 定点变异（从基线清单删掉一个模块 ⇒ 守卫必红）：临时副本基线，不动出货基线。
D 基线外新零引用模块 ⇒ 退出 1 并点名。

判据来源：公开 issue #23「§一 口径」＋「建议的最小覆盖门（新模块未测即红）」＋
本仓 tools/time_core_lint.py 先例。

本文件只读仓库源码、只写 tmp_path；不改 lingshu/ 下任何文件。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "coverage_map_lint.py"
BASELINE = REPO_ROOT / "tools" / "coverage_map_lint_baseline.txt"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import coverage_map_lint as C  # noqa: E402


def run_lint(*args, cwd=None, timeout=300):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), *args],
        cwd=str(cwd or REPO_ROOT), capture_output=True, timeout=timeout, env=env)


def payload(res):
    assert res.stdout, "工具没有输出：stderr=%r" % res.stderr[:2000]
    return json.loads(res.stdout.decode("utf-8"))


def write_module(directory: Path, name: str, body: str = "x = 1\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def make_repo(root: Path, modules, tests_text):
    # 不建 __init__.py：避免它自身成为零引用模块，干扰本件的判定自证。
    for name in modules:
        write_module(root / "lingshu" / "pkg", name)
    write_module(root / "tests", "test_sample.py", tests_text)
    return root


# --------------------------------------------------- A 量具自证
def test_unreferenced_module_is_listed(tmp_path):
    root = make_repo(tmp_path / "repo", ["alpha.py", "orphan.py"],
                     "from lingshu.pkg import alpha\n\ndef test_a():\n    assert alpha\n")
    zero, errors = C.scan(root, root / "lingshu", root / "tests")
    assert errors == []
    names = {z["file"] for z in zero}
    assert "lingshu/pkg/orphan.py" in names
    assert "lingshu/pkg/alpha.py" not in names


def test_referenced_module_not_listed(tmp_path):
    root = make_repo(tmp_path / "repo", ["alpha.py"],
                     "def test_a():\n    assert alpha is not None\n")
    zero, errors = C.scan(root, root / "lingshu", root / "tests")
    assert errors == []
    assert zero == []


def test_word_boundary_match(tmp_path):
    """`alpha` 的匹配必须是词边界：`alphabet` 不算提到 `alpha`。"""
    root = make_repo(tmp_path / "repo", ["alpha.py"],
                     "def test_a():\n    assert alphabet\n")
    zero, _ = C.scan(root, root / "lingshu", root / "tests")
    assert {z["file"] for z in zero} == {"lingshu/pkg/alpha.py"}


# --------------------------------------------------- B 真实仓库：出货基线绿
def test_real_repo_shipped_baseline_exit_zero():
    res = run_lint("--json")
    data = payload(res)
    assert res.returncode == 0, data
    assert data["new_zero_ref"] == []
    assert data["summary"]["new"] == 0
    # 基线里确实登记了真实零引用模块（否则规则可能失效）
    assert data["baseline_entries"] >= 1


def test_shipped_baseline_file_has_provenance():
    text = BASELINE.read_text(encoding="utf-8")
    assert "基线来源" in text and "HEAD" in text
    entries, errors = C.parse_baseline(text)
    assert errors == []
    assert entries, "基线清单为空"


# --------------------------------------------------- C 定点变异：删一个模块 ⇒ 红
def test_mutation_drop_one_baseline_entry_turns_red(tmp_path):
    """★ 定点变异：从基线清单删掉一个模块 ⇒ 守卫必红并点名该模块。"""
    data0 = payload(run_lint("--json"))
    assert data0["exit_code"] == 0
    assert data0["zero_ref_modules"], "真实仓库没有零引用模块，无法做变异"
    victim = data0["zero_ref_modules"][0]["file"]

    lines = [z["file"] + "\t" + str(z["lines"]) for z in data0["zero_ref_modules"]
             if z["file"] != victim]
    mutated = tmp_path / "dropped_baseline.txt"
    mutated.write_text("\n".join(lines) + "\n", encoding="utf-8")

    res = run_lint("--baseline", str(mutated), "--json")
    data = payload(res)
    assert res.returncode == 1, "基线删掉一个模块后守卫竟然没红：%r" % data
    assert victim in {z["file"] for z in data["new_zero_ref"]}, (victim, data["new_zero_ref"])


def test_mutation_empty_baseline_turns_red(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    res = run_lint("--baseline", str(empty))
    assert res.returncode == 1
    assert "新零引用模块" in res.stdout.decode("utf-8")


# --------------------------------------------------- D 基线外新零引用模块 ⇒ 红
def test_new_unreferenced_module_turns_red(tmp_path):
    root = make_repo(tmp_path / "repo", ["alpha.py"],
                     "def test_a():\n    assert alpha\n")
    baseline = root / "tools" / "coverage_map_lint_baseline.txt"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("", encoding="utf-8")

    # 初始：alpha 被提到 ⇒ 零引用为空 ⇒ 绿
    res0 = run_lint("--repo-root", str(root), "--json")
    assert payload(res0)["exit_code"] == 0

    # 新增一个没被测试提到的模块 ⇒ 红
    write_module(root / "lingshu" / "pkg", "beta.py")
    res1 = run_lint("--repo-root", str(root), "--json")
    data1 = payload(res1)
    assert res1.returncode == 1, data1
    assert "lingshu/pkg/beta.py" in {z["file"] for z in data1["new_zero_ref"]}


def test_new_module_with_test_stays_green(tmp_path):
    """新增模块但测试提到了它 ⇒ 不红（机制只在零引用时触发）。"""
    root = make_repo(tmp_path / "repo", ["alpha.py"],
                     "def test_a():\n    assert alpha\n")
    baseline = root / "tools" / "coverage_map_lint_baseline.txt"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("", encoding="utf-8")
    write_module(root / "lingshu" / "pkg", "gamma.py")
    write_module(root / "tests", "test_gamma.py", "def test_g():\n    assert gamma\n")
    res = run_lint("--repo-root", str(root), "--json")
    assert payload(res)["exit_code"] == 0


# --------------------------------------------------- baseline 解析 / 更新往返
def test_baseline_parse_and_render_roundtrip():
    zero = [{"file": "lingshu/a.py", "name": "a", "lines": 3},
            {"file": "lingshu/b.py", "name": "b", "lines": 0}]
    text = C.render_baseline(zero)
    parsed, errors = C.parse_baseline(text)
    assert errors == []
    assert parsed == {"lingshu/a.py": 3, "lingshu/b.py": 0}


def test_update_baseline_roundtrip(tmp_path):
    root = make_repo(tmp_path / "repo", ["orphan.py"], "x = 1\n")
    res = run_lint("--repo-root", str(root), "--update-baseline")
    assert res.returncode == 0
    baseline = root / "tools" / "coverage_map_lint_baseline.txt"
    assert baseline.exists()
    assert "lingshu/pkg/orphan.py" in baseline.read_text(encoding="utf-8")
    assert run_lint("--repo-root", str(root)).returncode == 0


def test_usage_errors_return_two(tmp_path):
    assert run_lint("--repo-root", str(tmp_path / "no-root")).returncode == 2
    assert run_lint("--baseline", str(tmp_path / "nope.txt")).returncode == 2
    bad = tmp_path / "bad.txt"
    bad.write_text("这不是一条合法条目\n", encoding="utf-8")
    assert run_lint("--baseline", str(bad)).returncode == 2
    assert run_lint("--update-baseline", "--json").returncode == 2


def test_json_byte_identical_across_runs(tmp_path):
    first = run_lint("--json")
    second = run_lint("--json")
    assert first.stdout == second.stdout
    assert first.returncode == second.returncode == 0
    third = run_lint("--json", cwd=tmp_path)
    assert third.stdout == first.stdout


# ------------------------------------------- 登记面（documented-exemption surface）
def test_registration_surface_is_preserved_by_update_baseline(tmp_path):
    """★「登记面」用户登记段须被 --update-baseline 逐字保留（仅作文档，无强制力）。"""
    root = make_repo(tmp_path / "repo", ["orphan.py"], "x = 1\n")
    baseline = root / "tools" / "coverage_map_lint_baseline.txt"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(
        "%s\n%s\n%s\n" % (C.REG_BEGIN,
                          "# 2026-10-11 deadbee lingshu/pkg/orphan.py  待补测试（issue #23 登记）",
                          C.REG_END),
        encoding="utf-8")
    res = run_lint("--repo-root", str(root), "--update-baseline")
    assert res.returncode == 0
    text = baseline.read_text(encoding="utf-8")
    assert "# 2026-10-11 deadbee lingshu/pkg/orphan.py  待补测试（issue #23 登记）" in text, text
    assert "lingshu/pkg/orphan.py\t1" in text, text


def test_registration_surface_has_no_force():
    text = (C.REG_BEGIN + "\n"
            + "# 2026-10-11 deadbee lingshu/x.py  允许零覆盖\n"
            + C.REG_END + "\n")
    entries, errors = C.parse_baseline(text)
    assert errors == []
    assert entries == {}, "登记面注释不应被 parse_baseline 当作白名单条目"
