# -*- coding: utf-8 -*-
"""test_issue361_pr_lint_title_bypass · PR 标题门不得被「开关形标题」绕过（#361）
============================================================================
缺陷（#361，成立）：`.github/workflows/pr-lint.yml` 的标题步把 PR 标题经
`PR_TITLE` 环境变量 + 位置参数喂给 `scripts/check_pr_title.py`。而该脚本 `main`
先把**位置参数**当开关派发——标题恰为 `--help` / `-h` / `--list-types` /
`--selftest` 时命中短路分支，`print(__doc__); return 0` ⇒ **退出码 0**，
CI 标题门被静默绕过（标题是攻击者可控输入）。

判据（本件）：把 workflow 里**真实那条**标题校验命令原样跑起来（同样的
`PR_TITLE` 注入 + 同样的位置参数），喂「开关形标题」必须**非 0**，喂合规标题
必须 0——即「开关形标题 = 不合规标题」而非「开关调用」。

判据来源：经验标定（#361 取证列点名的四个字串 + `pr-lint.yml` 标题步实况）；
无理论章节规定该门禁的标题通道口径。

量具自检（同族纪律）：抽取器必须能从 workflow 文本里读出那条标题校验命令——
否则「没抽到 ⇒ 跳过 ⇒ 恒绿」会成为假绿。

运行：pytest tests/test_issue361_pr_lint_title_bypass.py
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(REPO, ".github", "workflows", "pr-lint.yml")

#: 开关形标题——#361 取证列点名者，外加空串（摘要为空的另一极端）。
FLAG_TITLES = ("--help", "-h", "--list-types", "--selftest")
VALID_TITLE = "fix(world): 某处修正——示例"


def _run_commands():
    """从 pr-lint.yml 抽出所有 `run:` 里的 check_pr_title 调用（保序）。"""
    with open(WF, encoding="utf-8") as fh:
        text = fh.read()
    return [m.group(1).strip()
            for m in re.finditer(r"^\s*run:\s*(.+)$", text, re.M)
            if "check_pr_title.py" in m.group(1)]


def _title_step_command():
    """标题校验步的命令（含 `PR_TITLE`，不含 `--selftest`）。"""
    for cmd in _run_commands():
        if "PR_TITLE" in cmd and "--selftest" not in cmd:
            return cmd
    return None


def _selftest_step_command():
    """自检步的命令（含 `--selftest`）。"""
    for cmd in _run_commands():
        if "--selftest" in cmd:
            return cmd
    return None


def _as_argv(cmd, title):
    """把 workflow 命令转成可执行 argv：`$PR_TITLE` 替成实参、`python` 替成解释器。"""
    argv = shlex.split(cmd, posix=True)
    argv = [sys.executable if i == 0 and os.path.basename(a) in ("python", "python3")
            else a for i, a in enumerate(argv)]
    return [title if a in ("$PR_TITLE", '"$PR_TITLE"', "${PR_TITLE}") else a
            for a in argv]


def _run(argv, pr_title):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    if pr_title is None:
        env.pop("PR_TITLE", None)
    else:
        env["PR_TITLE"] = pr_title
    return subprocess.run(argv, cwd=REPO, env=env,
                          capture_output=True, text=True, encoding="utf-8")


# ---------------------------------------------------------------- 量具自检

def test_extractor_reads_title_step_command():
    """量具自检：抽取器必须从 workflow 读出那条标题校验命令。"""
    cmd = _title_step_command()
    assert cmd, (
        "抽取器没从 pr-lint.yml 读出「含 PR_TITLE 的 check_pr_title 调用」⇒ "
        "本守卫会静默通过。实读命令集：%r" % (_run_commands(),))
    assert "check_pr_title.py" in cmd, cmd
    # 自检步也须能被读出（用于证明自检入口仍可用）。
    assert _selftest_step_command(), _run_commands()


# ---------------------------------------------------------------- 主判据

def test_flag_shaped_title_is_rejected_by_the_real_gate_command():
    """G1（核心）：用 workflow 真实标题命令跑开关形标题 ⇒ 必须非 0。"""
    cmd = _title_step_command()
    bad = []
    for t in FLAG_TITLES:
        r = _run(_as_argv(cmd, t), t)
        if r.returncode == 0:
            bad.append("%r → rc=0（被当开关短路放行）stdout=%r"
                       % (t, r.stdout.strip()[:120]))
    assert not bad, (
        "PR 标题门可被开关形标题绕过（#361）：标题命中短路分支即退出码 0。\n"
        + "\n".join("  · %s" % b for b in bad)
        + "\n\n修法：门禁通道（PR_TITLE 在场）下位置参数一律按标题校验，"
          "开关形标题 ⇒ 判负（退出码 1），不得短路成 0。"
    )


def test_valid_title_still_passes_through_the_real_gate_command():
    """G2（防过修）：合规标题经同一条命令仍须退出码 0。"""
    cmd = _title_step_command()
    r = _run(_as_argv(cmd, VALID_TITLE), VALID_TITLE)
    assert r.returncode == 0, (
        "合规标题被误判负（修 #361 修过头）：rc=%d stdout=%r stderr=%r"
        % (r.returncode, r.stdout.strip()[:200], r.stderr.strip()[:200]))


def test_selftest_entry_still_works():
    """G3（防连带）：workflow 的自检步（无 PR_TITLE）仍须退出码 0。"""
    cmd = _selftest_step_command()
    r = _run(_as_argv(cmd, ""), None)
    assert r.returncode == 0, (
        "自检入口被 #361 修复连带打断：rc=%d stdout=%r"
        % (r.returncode, r.stdout.strip()[:200]))


if __name__ == "__main__":
    sys.exit(0)
