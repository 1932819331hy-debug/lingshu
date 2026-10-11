# -*- coding: utf-8 -*-
"""test_ci_landed_and_script_refs · CI 面「已落仓」声明与脚本引用可解析（#238）
============================================================================
背景（#238，部分成立）：
    已随仓落仓并在运行的 4 个 workflow（gate / pr-lint / repro-bot / upstream-tracker）
    与 `docs/repro-bot.md` 曾自称「工作区草稿（尚未落仓）」——与事实相反。
    落仓后该自称已改（gate.yml:5 / repro-bot.md 等均写「已随仓落仓」），但**残留**两处：
      · `scripts/check_pr_title.py` docstring 指向不随仓发布的本地报告
        （「见报告『落仓前须知』」）；
      · `scripts/extract_and_run_issue_repro.py` docstring 引
        `scripts/link_check.py::_FENCE_OPEN`——而本仓 `scripts/` 下**没有**该文件
        （悬空引用）。

判据（本件，两条）：
    G1 「已落仓」声明：4 个 workflow 均不得含「草稿 / 尚未落仓 / 未落仓 / 工作区草稿」
        字样（与事实相反的自称），且必须显式声明已落仓。
    G2 脚本引用可解析：workflow 与 `scripts/*.py` 里出现的 `scripts/<name>.py`
        引用，其目标必须在 `scripts/` 下真实存在——悬空引用判红。

判据来源：经验标定（#238 取证列点名的两处残留 + 本仓 `scripts/` 实况）。

量具自检（同族纪律）：引用扫描器必须能从**含引用的合成文本**读出路径——
否则「没读到 ⇒ 空集 ⇒ 恒绿」会成为坏扫描器掩盖的假绿。

运行：pytest tests/test_ci_landed_and_script_refs.py
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(REPO, ".github", "workflows")
SCRIPTS_DIR = os.path.join(REPO, "scripts")

#: 4 个已落仓 workflow（#238 点名）。
WORKFLOWS = ("gate.yml", "pr-lint.yml", "repro-bot.yml", "upstream-tracker.yml")

#: 与事实相反的自称（草稿态）。
_DRAFT_MARKERS = ("尚未落仓", "未落仓", "工作区草稿", "工作区草案", "草稿")

#: 已落仓声明。
_LANDED_MARKER = "已随仓落仓"

#: 「指向不随仓发布的本地报告」的残留标记（#238 取证列点名 `check_pr_title.py:37`）。
_DANGLING_REPORT_MARKERS = ("落仓前须知", "见报告「")

#: G1/G3 的扫描面：4 个 workflow + `docs/repro-bot.md` + `scripts/*.py`。
_DOC_REPRO = os.path.join(REPO, "docs", "repro-bot.md")

#: `scripts/<name>.py` 形式的仓内脚本引用。
_SCRIPT_REF_RE = re.compile(r"scripts/([A-Za-z0-9_]+\.py)")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _script_refs(text):
    """从文本里抽出所有 `scripts/<name>.py` 引用（保序、去重）。"""
    seen, out = set(), []
    for m in _SCRIPT_REF_RE.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


# ---------------------------------------------------------------- 量具自检

def test_ref_scanner_reads_synthetic_refs():
    """量具自检：扫描器必须能从合成文本读出 `scripts/*.py` 引用。"""
    ids = _script_refs("见 scripts/link_check.py::_FENCE_OPEN 与 scripts/foo.py 的对齐")
    assert ids == ["link_check.py", "foo.py"], (
        "扫描器没从含引用的样本里读出预期路径 ⇒ 它坏了，G2 会静默通过。实读：%r" % (ids,))
    assert _script_refs("无引用") == []


# ---------------------------------------------------------------- 主判据

def test_g1_workflows_declare_landed_not_draft():
    """G1：4 个 workflow + `docs/repro-bot.md` 不得自称草稿，且必须声明已落仓。"""
    offenders = []
    targets = [(".github/workflows/" + n, os.path.join(WF_DIR, n))
               for n in WORKFLOWS] + [("docs/repro-bot.md", _DOC_REPRO)]
    for label, path in targets:
        text = _read(path)
        hits = [mk for mk in _DRAFT_MARKERS if mk in text]
        if hits:
            offenders.append("%s 仍含草稿态自称：%s" % (label, hits))
        if _LANDED_MARKER not in text:
            offenders.append("%s 缺「%s」声明" % (label, _LANDED_MARKER))
    assert not offenders, (
        "CI 面「落仓状态」自称与事实不符（#238）：\n"
        + "\n".join("  · %s" % o for o in offenders)
        + "\n\n修法：改成与事实一致的「已随仓落仓（引入提交 <sha>）」。"
    )


def test_g3_no_reference_to_unpublished_local_report():
    """G3：scripts/*.py 不得指向「不随仓发布的本地报告」（#238 取证点名的残留）。"""
    offenders = []
    for f in sorted(os.listdir(SCRIPTS_DIR)):
        if not f.endswith(".py"):
            continue
        text = _read(os.path.join(SCRIPTS_DIR, f))
        for mk in _DANGLING_REPORT_MARKERS:
            if mk in text:
                offenders.append("scripts/%s 含「%s」——指向不随仓发布的本地报告" % (f, mk))
    assert not offenders, (
        "以下脚本仍指向**不随仓发布的本地报告**（读者无从查证，#238）：\n"
        + "\n".join("  · %s" % o for o in offenders)
        + "\n\n修法：删掉该指引，或改指随仓件（如落仓提交 sha / 本仓文档）。"
    )


def test_g2_script_refs_resolve_to_existing_files():
    """G2：workflow 与 scripts/*.py 里的 `scripts/<name>.py` 引用必须真实存在。"""
    # 引用面：4 个 workflow + scripts/ 下所有 .py 的正文
    texts = {}
    for name in WORKFLOWS:
        texts[".github/workflows/" + name] = _read(os.path.join(WF_DIR, name))
    for f in sorted(os.listdir(SCRIPTS_DIR)):
        if f.endswith(".py"):
            texts["scripts/" + f] = _read(os.path.join(SCRIPTS_DIR, f))

    dangling = []
    for where, text in texts.items():
        for ref in _script_refs(text):
            if not os.path.isfile(os.path.join(SCRIPTS_DIR, ref)):
                dangling.append("%s → scripts/%s（不存在）" % (where, ref))
    assert not dangling, (
        "以下 `scripts/*.py` 引用指向**本仓不存在的文件**（悬空引用，误导读者，#238）：\n"
        + "\n".join("  · %s" % d for d in dangling)
        + "\n\n修法：删掉该引用，或改指真实存在的件（跨仓参考须写明属主仓，勿写成仓内路径）。"
    )


if __name__ == "__main__":
    sys.exit(0)
