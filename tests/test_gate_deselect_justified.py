# -*- coding: utf-8 -*-
"""test_gate_deselect_justified · 「deselect 必须是真红」守卫（#341）
============================================================================
背景（#341，部分成立 → 本件收口）：
    `.github/workflows/gate.yml` 的 pytest 腿曾长期 `--deselect` 两条
    `tests/test_hex_text.py` 的用例（README / tests/README 曾标「已知红」）：
        · test_spatial_detect_finds_single_object
        · test_multimodal_check_clean_vs_corrupt
    根因修复已落 main：
        · `9bc4985` —— `image_to_grid` 晶格横向越出画幅 √3 倍（采纳自 PR #116），
          「按晶格三等分/质心取位置」的系统性错位由此消除；
        · `54cb5cb` —— `hex_hier` 形状辅助损失改按 OBJ 布局连续分组。
    本机实测（2026-10-11 · py3.12 · numpy 2.5.3 · Pillow 12.3.0）：
        `python -X utf8 -m pytest tests/test_hex_text.py -q` = **6 passed**。
    两条既已转绿，`--deselect` 就是**静默关掉回归保护**——deselect 一条绿的用例，
    等于让门禁永远看不到它变红。本守卫把这条纪律钉死。

判据（本件）：
    (a) **通用规则**：gate.yml 里每一条 `--deselect <nodeid>`，其 nodeid 单跑必须
        **红**（rc != 0）。跑绿了 ⇒ 该 deselect 无正当性 ⇒ 判红并点名。
        （唯一正当的 deselect 理由＝该用例在当前环境**确实失败**；deselect 一条绿的
          用例即本件要防的缺陷。）
    (b) **#341 定点**：上述两条 hex_text 用例必须**不在** deselect 名单里，且
        **现下确实跑绿**（绿 ⇒ 门禁应真跑它们，而非 deselect）。

量具自检（同族纪律）：解析器必须在**含 deselect 的合成文本**上真能读出 nodeid——
否则「真实 gate.yml 无 deselect ⇒ 空集 ⇒ 恒绿」就成了坏解析器掩盖的假绿。

运行：pytest tests/test_gate_deselect_justified.py
      （或 python -X utf8 tests/test_gate_deselect_justified.py 直跑）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_YML = os.path.join(REPO, ".github", "workflows", "gate.yml")

#: `--deselect <nodeid>`：nodeid 紧跟其后、同一行（本仓 gate.yml 的写法）。
_DESELECT_RE = re.compile(r"--deselect[ \t]+(\S+)")

#: #341 点名的两条——它们**必须**随 tests/ 自动收集真跑，不得被 deselect。
_HEX_TEXT_PAIR = (
    "tests/test_hex_text.py::test_spatial_detect_finds_single_object",
    "tests/test_hex_text.py::test_multimodal_check_clean_vs_corrupt",
)

RUN_TIMEOUT = 600            # 秒；hex_text 的模块级 fixture 训一次网，留足余量


def _deselect_ids(text):
    """从 gate.yml 文本里抽出所有 `--deselect` 的 nodeid（保序、去重）。"""
    seen, out = set(), []
    for m in _DESELECT_RE.finditer(text):
        nid = m.group(1).strip().strip('"').strip("'")
        if nid and nid not in seen:
            seen.add(nid)
            out.append(nid)
    return out


def _gate_text():
    with open(GATE_YML, encoding="utf-8") as fh:
        return fh.read()


def _run_pytest(nodeids):
    """跑给定 nodeid（可多个）；返回 (returncode, tail 输出)。"""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "--no-header",
           *nodeids]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=RUN_TIMEOUT, env=env)
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    tail = "\n".join(out.splitlines()[-12:])
    return proc.returncode, tail


# ---------------------------------------------------------------- 量具自检

def test_parser_reads_deselect_ids():
    """量具自检：解析器必须能在**含 deselect 的合成文本**上读出 nodeid。

    若解析器坏了，真实 gate.yml 的「无 deselect」会被误读成空集、主判据恒绿——
    本自检把这条假绿堵死（对齐仓内 test_gate_dependency_closure 的量具自检先例）。
    """
    sample = (
        "run: |\n"
        "  pytest tests/ \\\n"
        "    --deselect tests/test_hex_text.py::test_multimodal_check_clean_vs_corrupt\n"
        '    --deselect "tests/foo.py::test_bar"\n'
    )
    ids = _deselect_ids(sample)
    assert ids == ["tests/test_hex_text.py::test_multimodal_check_clean_vs_corrupt",
                   "tests/foo.py::test_bar"], (
        "解析器没从含 deselect 的样本里读出预期 nodeid ⇒ 它坏了，"
        "本判据此时**无判别力**（会把任何名单都读成空集）。实读：%r" % (ids,))
    assert _deselect_ids("no deselect here") == [], "无 deselect 时应返回空集"


# ---------------------------------------------------------------- 主判据

def test_every_deselected_node_is_actually_red():
    """主判据 (a)：每条被 deselect 的用例单跑必须**红**——绿即无正当性（#341）。"""
    ids = _deselect_ids(_gate_text())
    greens = []
    for nid in ids:
        rc, tail = _run_pytest([nid])
        if rc == 0:
            greens.append((nid, tail))
        elif rc == 4:                      # usage error：nodeid 不存在
            greens.append((nid, "rc=4（deselect 指向不存在的用例）\n" + tail))
    assert not greens, (
        "以下被 `--deselect` 的用例**并没有红**——deselect 一条跑绿的用例＝"
        "静默关掉回归保护（#341）：\n"
        + "\n".join("  · %s\n%s" % (n, t) for n, t in greens)
        + "\n\n修法：把该 `--deselect` 从 .github/workflows/gate.yml 摘除"
        "（绿 ⇒ 应随 tests/ 自动收集真跑）。"
    )


def test_hex_text_pair_is_green_and_not_deselected():
    """主判据 (b)：#341 点名的两条必须不在 deselect 名单、且现下跑绿。"""
    ids = _deselect_ids(_gate_text())
    still = [n for n in _HEX_TEXT_PAIR if n in ids]
    assert not still, (
        "以下用例已随根因修复（`9bc4985` / `54cb5cb`）转绿，却仍被 gate.yml "
        "`--deselect`——回归保护被静默关掉（#341）：\n"
        + "\n".join("  · %s" % n for n in still))

    rc, tail = _run_pytest(list(_HEX_TEXT_PAIR))
    assert rc == 0, (
        "#341 点名的两条用例现下**没有跑绿**（rc=%d）——若确系环境性真红，"
        "须按「已知红」流程登记（README / tests/README 同步）后再 deselect，"
        "不得静默处理。实读：\n%s" % (rc, tail))


if __name__ == "__main__":
    sys.exit(1 if _deselect_ids(_gate_text()) else 0)
