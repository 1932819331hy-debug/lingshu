# -*- coding: utf-8 -*-
"""issue #614 · coverage 白名单四向守卫（stale / 漂移 / 单调不增 / 不过修）

背景（issue #614，P1）
----------------------
`tools/coverage_map_lint.py` 的零覆盖白名单判定原只按「路径字符串」命中：
  1. stale（已失效条目）被算出来却不影响 exit（门只看 new）；
  2. 登记行数从不参与比较——「文件删除后同名重建（全新未测代码）」路径相同即豁免
     （幽灵豁免），行数漂移（登记 1 行/实际 999 行）也检不出；
  3. `--update-baseline` 把当前零引用清单整份写回、头部读数是硬编码模板常量，
     白名单规模**增长**（含空基线 0→N）无任何阻拦——门被一次性关掉；
  4. #23 §3.1 明文「白名单是冻结的债务清单，不是通行证——断言**白名单规模单调不增**」
     未落地，旧守卫只有 baseline_entries>=1 下界，没有「加条目」方向的守卫。

本件断言组（与 #614 修法一一对应）
----------------------------------
G1 stale 入判定：基线条目已不再是零引用 ⇒ exit 1，读数行提示清理后 --update-baseline。
G2 行数参与（同名重建）：登记行数与实测一致 ⇒ 按现状豁免（防过修）；同名重建/行数
   变化 ⇒ 不再豁免、exit 1（漂移入判定）。
G3 行数漂移：登记 1 行 / 实测 999 行 ⇒ exit 1 并给出两个行数。
G4 --update-baseline 单调不增（#23 §3.1 落地）：白名单规模增长（含题面 0→5）⇒ 拒绝
   写入、exit 3、点名 --force；显式 --force 才放行（exit 0＋警告）。
G5 防过修：正常 shrinking 更新（清 stale＋重算漂移行）仍允许 exit 0；「删条目 ⇒ 红」
   方向保持（基线删掉一个模块 ⇒ exit 1）。
G6 仓内实跑锁定清理后基线：exit 0、无 stale、无漂移、无新增；**头部读数与当次实测
   自洽**（不再是硬编码模板常量）。

判据来源：issue #614 题面＋#23 §3.1（白名单规模单调不增）；先例
tests/test_coverage_map_lint.py（本件是其「加条目/失效/漂移」方向的补全，不替代）。

本文件只读仓库源码、只写 tmp_path；不改 lingshu/ 下任何文件。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "coverage_map_lint.py"
BASELINE = REPO_ROOT / "tools" / "coverage_map_lint_baseline.txt"


def run_lint(*args):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), *args],
        cwd=str(REPO_ROOT), capture_output=True, timeout=300, env=env)


def payload(res):
    assert res.stdout, "工具没有输出：stderr=%r" % res.stderr[:2000]
    return json.loads(res.stdout.decode("utf-8"))


def make_repo(root: Path, modules, tests_text="def test_a():\n    pass\n"):
    """合成仓：modules = {文件名: 文件体}；tests_text 里提到谁谁就不是零引用。"""
    pkg = root / "lingshu" / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    for name, body in modules.items():
        (pkg / name).write_text(body, encoding="utf-8")
    tdir = root / "tests"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "test_a.py").write_text(tests_text, encoding="utf-8")
    return root


def seed_baseline(root: Path, entries):
    bl = root / "tools" / "coverage_map_lint_baseline.txt"
    bl.parent.mkdir(parents=True, exist_ok=True)
    bl.write_text("".join("%s\t%d\n" % (p, n) for p, n in entries), encoding="utf-8")
    return bl


# --------------------------------------------------- G1 stale 入判定
def test_g1_stale_entry_turns_red(tmp_path):
    """★G1：登记条目已不再是零引用（模块被测试提到）⇒ exit 1（原 defect：被算出来却 ignored）。"""
    root = make_repo(tmp_path / "repo", {"m.py": "x = 1\n"},
                     tests_text="def test_a():\n    assert m\n")
    seed_baseline(root, [("lingshu/pkg/m.py", 1)])
    res = run_lint("--repo-root", str(root), "--json")
    data = payload(res)
    assert res.returncode == 1, "stale 条目竟然不红：%r" % data
    assert data["stale_baseline_entries"] == ["lingshu/pkg/m.py"]
    assert data["summary"]["stale"] == 1

    # 文面输出必须提示清理动作（题面读数行措辞）
    res_text = run_lint("--repo-root", str(root))
    out = res_text.stdout.decode("utf-8")
    assert "已失效" in out and "清理后 --update-baseline" in out, out


def test_g1_stale_cannot_revive_as_ghost_exemption(tmp_path):
    """★G1 延伸（幽灵豁免堵死链）：stale 红逼清理 ⇒ 清理后删测试 ⇒ 走「新增」红。"""
    root = make_repo(tmp_path / "repo", {"m.py": "x = 1\n"},
                     tests_text="def test_a():\n    assert m\n")
    bl = seed_baseline(root, [("lingshu/pkg/m.py", 1)])
    assert run_lint("--repo-root", str(root), "--json").returncode == 1  # stale ⇒ 红
    bl.write_text("", encoding="utf-8")                                  # 清理（缩白名单）
    assert run_lint("--repo-root", str(root), "--json").returncode == 0  # 清理后绿
    (root / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    data = payload(run_lint("--repo-root", str(root), "--json"))
    assert data["exit_code"] == 1                                        # 无登记可复活 ⇒ 新增红
    assert [z["file"] for z in data["new_zero_ref"]] == ["lingshu/pkg/m.py"]


# --------------------------------------------------- G2 同名重建：行数参与
def test_g2_same_lines_still_exempted(tmp_path):
    """★G2 防过修：路径＋行数都一致 ⇒ 按现状豁免（行数入判定不得误伤存量豁免）。"""
    root = make_repo(tmp_path / "repo", {"m.py": "x = 1\n"})
    seed_baseline(root, [("lingshu/pkg/m.py", 1)])
    data = payload(run_lint("--repo-root", str(root), "--json"))
    assert data["exit_code"] == 0
    assert data["drifted_baseline_entries"] == []


def test_g2_same_name_rebuild_no_longer_exempted(tmp_path):
    """★G2 题面场景 2：文件同名重建（1 行 → 2 行，全新未测代码）⇒ 不再豁免、exit 1。"""
    root = make_repo(tmp_path / "repo", {"m.py": "x = 1\n"})
    seed_baseline(root, [("lingshu/pkg/m.py", 1)])
    (root / "lingshu" / "pkg" / "m.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    res = run_lint("--repo-root", str(root), "--json")
    data = payload(res)
    assert res.returncode == 1, "同名重建（行数已变）竟然仍豁免：%r" % data
    assert [z["file"] for z in data["drifted_baseline_entries"]] == ["lingshu/pkg/m.py"]
    assert data["drifted_baseline_entries"][0]["registered_lines"] == 1
    assert data["drifted_baseline_entries"][0]["lines"] == 2
    out = run_lint("--repo-root", str(root)).stdout.decode("utf-8")
    assert "漂移" in out and "登记 1 行 / 实测 2 行" in out, out


# --------------------------------------------------- G3 行数漂移
def test_g3_line_count_drift_turns_red(tmp_path):
    """★G3 题面场景 3：登记 1 行 / 实际 999 行 ⇒ exit 1（原 defect：路径相同即豁免）。"""
    root = make_repo(tmp_path / "repo",
                     {"m.py": "\n".join("l%d" % i for i in range(999)) + "\n"})
    seed_baseline(root, [("lingshu/pkg/m.py", 1)])
    res = run_lint("--repo-root", str(root), "--json")
    data = payload(res)
    assert res.returncode == 1, "行数漂移（1→999）竟然不红：%r" % data
    entry = data["drifted_baseline_entries"][0]
    assert (entry["file"], entry["registered_lines"], entry["lines"]) == \
        ("lingshu/pkg/m.py", 1, 999)


# --------------------------------------------------- G4 --update-baseline 单调不增
def test_g4_update_baseline_growth_refused_0_to_5(tmp_path):
    """★G4 题面场景 4：空基线 + 5 零引用模块 --update-baseline ⇒ 拒绝写入、exit 3。"""
    root = make_repo(tmp_path / "repo",
                     {"m%d.py" % i: "x = %d\n" % i for i in range(5)})
    res = run_lint("--repo-root", str(root), "--update-baseline")
    out = res.stdout.decode("utf-8")
    assert res.returncode == 3, "0→5 增长竟然放行（exit=%d）：%s" % (res.returncode, out)
    assert not (root / "tools" / "coverage_map_lint_baseline.txt").exists(), "拒绝后不得写入文件"
    assert "单调不增" in out and "--force" in out, out


def test_g4_update_baseline_growth_refused_1_to_2(tmp_path):
    """★G4：非空基线增长（1→2）同样拒绝。"""
    root = make_repo(tmp_path / "repo", {"m.py": "x = 1\n", "n.py": "y = 2\n"})
    seed_baseline(root, [("lingshu/pkg/m.py", 1)])
    res = run_lint("--repo-root", str(root), "--update-baseline")
    assert res.returncode == 3
    assert "旧 1 条 → 新 2 条" in res.stdout.decode("utf-8")


def test_g4_update_baseline_force_allows_growth_with_warning(tmp_path):
    """★G4：显式 --force 放行增长（exit 0）＋必须打警告。"""
    root = make_repo(tmp_path / "repo",
                     {"m%d.py" % i: "x = %d\n" % i for i in range(5)})
    res = run_lint("--repo-root", str(root), "--update-baseline", "--force")
    out = res.stdout.decode("utf-8")
    assert res.returncode == 0, out
    assert "警告" in out and "单调不增" in out and "--force" in out, out
    text = (root / "tools" / "coverage_map_lint_baseline.txt").read_text(encoding="utf-8")
    for i in range(5):
        assert "lingshu/pkg/m%d.py\t1" % i in text, text  # 各文件实测 1 行
    # 放行写回后，门恢复关态：再跑 exit 0（新增 0）
    assert payload(run_lint("--repo-root", str(root), "--json"))["exit_code"] == 0


def test_g4_update_baseline_writes_measured_header(tmp_path):
    """★G4 配套：--update-baseline 头部读数＝当次实测真值，不是模板常量（#614 场景 3）。

    注意：旧模板里的真实仓模块 basename 在本文件里必须**拆开拼接**——本件自身也是
    零引用判定的扫描语料（tests/*.py），写字面量会把那些模块「假引用」掉（工具 L1）。
    """
    root = make_repo(tmp_path / "repo", {"m.py": "x = 1\n"})
    # tmp 里初始化一个最小 git 仓，让头部能取到生成时 HEAD 短号
    for cmd in (["git", "init", "-q", str(root)],
                ["git", "-C", str(root), "add", "-A"],
                ["git", "-C", str(root), "-c", "user.name=t",
                 "-c", "user.email=t@t", "commit", "-qm", "seed"]):
        subprocess.run(cmd, capture_output=True, timeout=60)
    run_lint("--repo-root", str(root), "--update-baseline", "--force")
    text = (root / "tools" / "coverage_map_lint_baseline.txt").read_text(encoding="utf-8")
    assert "零引用 1 模块 / 1 行" in text, text            # 真值：1 模块 / 1 行
    assert "b846086" not in text and "289 行" not in text, text  # 硬编码模板读数必须消失
    _old_mods = ("hex" + "_recon", "hex" + "_unit")        # 拆开写，避免自指假引用
    assert _old_mods[0] not in text and _old_mods[1] not in text, text
    assert re.search(r"HEAD [0-9a-f]{7,}", text), text      # 溯源指向生成时真实 HEAD


# --------------------------------------------------- G5 防过修：shrink 合法、删条目仍红
def test_g5_shrinking_update_still_allowed(tmp_path):
    """★G5：清 stale＋重算漂移行（2 条 → 1 条，缩白名单）⇒ 允许、exit 0。"""
    root = make_repo(tmp_path / "repo", {"m.py": "x = 1\n"})
    # 旧基线：m.py 登记行数已漂移（99）＋一条 stale（gone.py）——更新＝清 stale＋重算行
    seed_baseline(root, [("lingshu/pkg/m.py", 99), ("lingshu/pkg/gone.py", 5)])
    res = run_lint("--repo-root", str(root), "--update-baseline")
    assert res.returncode == 0, res.stdout.decode("utf-8")
    text = (root / "tools" / "coverage_map_lint_baseline.txt").read_text(encoding="utf-8")
    assert "lingshu/pkg/m.py\t1" in text, text              # 漂移行已重算为实测 1
    assert "gone.py" not in text, text                      # stale 已清理
    assert payload(run_lint("--repo-root", str(root), "--json"))["exit_code"] == 0


def test_g5_dropping_entry_still_turns_red(tmp_path):
    """★G5：既有「删条目 ⇒ 红」方向保持（G 组补的是加条目方向，不得挤掉旧门）。"""
    root = make_repo(tmp_path / "repo", {"m.py": "x = 1\n", "n.py": "y = 2\n"})
    seed_baseline(root, [("lingshu/pkg/m.py", 1)])          # 只登记 m，n 未登记
    data = payload(run_lint("--repo-root", str(root), "--json"))
    assert data["exit_code"] == 1
    assert "lingshu/pkg/n.py" in {z["file"] for z in data["new_zero_ref"]}


# --------------------------------------------------- G6 仓内实跑锁定清理后基线
def test_g6_real_repo_clean_baseline_exit_zero():
    """★G6：清理后的出厂基线在仓内实跑 exit 0——无 stale、无漂移、无新增，条目全对齐。"""
    res = run_lint("--json")
    data = payload(res)
    assert res.returncode == 0, data
    assert data["stale_baseline_entries"] == []
    assert data["drifted_baseline_entries"] == []
    assert data["new_zero_ref"] == []
    assert data["baseline_entries"] == len(data["zero_ref_modules"]), \
        "白名单条目数应与实测零引用模块数完全对齐（无多无少）"


def test_g6_shipped_header_matches_measured_reality():
    """★G6：出厂头部读数与当次实测**自洽**（#614：头部不得再是硬编码模板常量）。"""
    data = payload(run_lint("--json"))
    text = BASELINE.read_text(encoding="utf-8")
    # 只检**工具自动生成的头部**（登记面之前）——登记段引用历史读数属合法留痕
    head_region = text.split("# ── 登记面（", 1)[0]
    m = re.search(r"零引用 (\d+) 模块 / (\d+) 行", head_region)
    assert m, "头部缺少实测读数行：%r" % head_region[:400]
    assert int(m.group(1)) == data["summary"]["zero_ref_modules"], (m.group(0), data["summary"])
    assert int(m.group(2)) == data["summary"]["zero_ref_lines"], (m.group(0), data["summary"])
    assert "b846086" not in head_region and "289 行" not in head_region, "旧硬编码读数残留于头部"
    assert re.search(r"HEAD [0-9a-f]{7,}", head_region), "缺少生成时 HEAD 溯源"
    assert "单调不增" in head_region, "头部应登记 #23 §3.1 单调不增机制"
