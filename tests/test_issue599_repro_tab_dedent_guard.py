# -*- coding: utf-8 -*-
"""test_issue599_repro_tab_dedent_guard · repro-bot 执行核 tab 缩进围栏剥除守卫
============================================================================
被测件：`scripts/extract_and_run_issue_repro.py`（执行核）。
与既有 `tests/test_issue120_239_362_repro_exec.py`（#362①空格缩进围栏）等件
**不共用机制**：那件走 in-process `main()`，本件按 #599 登记形态走**子进程＋
临时 body 文件**（端到端：python -X utf8 scripts/extract_and_run_issue_repro.py
--body-file <文件>），任一件抽掉修复时只本件变红、不靠对方兜底。

#599（成立）——tab 缩进的围栏代码块被静默判死（方向性误判）。
    复现（修复前实跑，8 组形态读数表）：`_FENCE_OPEN = re.compile(r"^\\s*(`{3,}|~{3,})")`
    用 `\\s`（含 tab）**认得出** tab 缩进围栏，但旧实现量缩进只数空格
    （`len(head) - len(head.lstrip(" "))` ⇒ tab 围栏 indent=0）、剥缩进只剥空格字符
    （`_dedent` 逐个比对 `line[k] == " "`）⇒ indent=0 时一个字符都不剥，块内每行
    **带着 tab 落盘** ⇒ 子进程 `IndentationError: unexpected indent`、退出码 1，
    报告写「脚本未正常结束」——外部报告人的**有效**复现被静默判死。
    第二重：报告「- **执行**：」行声明「围栏缩进量与引用块标记已按 CommonMark
    剥除、其余逐字未改」——对 tab 正文这句为假（声明与实现不一致）。
    改前读数（同一 body 只换缩进字符）：无缩进 / 3 空格 / 4 空格 ⇒ rc 0 正常；
    1 tab、1 tab＋嵌套体、2 tab、2 空格＋1 tab、引用块内 tab ⇒ rc 1 全
    IndentationError。修复后 8 组全绿、空格 3 组逐字节保持原样（零回归）。
    判据：CommonMark §2.2「制表符」——块级构造的缩进按**列宽**计（空格 1 列，
    tab 展开到下一制表位，4 列制表位），与 `_FENCE_OPEN` 认围栏用的 `\\s` 同口径；
    修法＝`_indent_width`（列宽度宽）＋`_dedent` 剥至多 N **列**＋开启行
    `indent = _indent_width(head)`。
    断言：G1 tab 5 组形态端到端转绿（rc 0、报告 stdout 段见标记、落盘代码逐字节
    正确）；G2（防过修）空格 3/4、无缩进 3 组行为不变（rc 0、落盘代码逐字节
    正确——缩进敏感结构，多剥/少剥一个字符即 IndentationError 或文件比对失败）；
    G3 报告声明为真：tab 形态执行不再带 IndentationError，声明行在场且不再伴随
    「脚本未正常结束」。
    变异两向：把 `_indent_width`/`_dedent` 文本回退成纯空格版 ⇒ G1 全红；恢复 ⇒ 全绿。

运行（仓根）：python -X utf8 -m pytest tests/test_issue599_repro_tab_dedent_guard.py -q --no-header
退出码：0 = 全过；1 = 有断言失败。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(REPO, "scripts", "extract_and_run_issue_repro.py")

_T = "\t"   # 制表符字面量（构造 body 一律走 python 转义，不经 shell）


def _stdout_section(report):
    """截报告的「原始输出（stdout）」段——被测 print 的干净判据（不含 stderr 回显）。"""
    begin = report.find("#### 原始输出（stdout）")
    end = report.find("#### 原始错误（stderr）")
    assert begin != -1 and end != -1, "报告缺 stdout/stderr 段头：\n%s" % report
    return report[begin:end]


def _run_core(body, out_dir):
    """body 写临时文件，子进程端到端跑执行核 --body-file；返回 (rc, report, 落盘代码)。

    `--out-dir` 钉住落盘目录 ⇒ 抽出的代码文件可直接读回做**逐字节**比对（G1/G2
    共用的最强判据：多剥/少剥任何缩进字符都会在此现形）。
    """
    d = tempfile.mkdtemp(prefix="repro_599_")
    p = os.path.join(d, "body.md")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    r = subprocess.run(
        [sys.executable, "-X", "utf8", _SCRIPT,
         "--body-file", p, "--out-dir", out_dir, "--timeout", "60"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO, timeout=180,
    )
    script_path = os.path.join(out_dir, "repro_issue_local.py")
    with open(script_path, "rb") as f:
        code = f.read().decode("utf-8")
    return r.returncode, (r.stdout or ""), code


# ── G1：tab 5 组形态全部转绿（改前全 IndentationError / rc 1） ───────────────

def test_g1_tab_one():
    """1 tab 缩进围栏：改前 indent=0 不剥 ⇒ '\\tprint' 落盘即 IndentationError（rc 1）。"""
    rc, rep, code = _run_core(
        '前文\n' + _T + '```python\n' + _T + 'print("repro ok tab1")\n' + _T + '```\n后文',
        tempfile.mkdtemp(prefix="repro_599_tab1_"))
    assert rc == 0, "tab 缩进复现仍被误判失败（rc=%d）——#599 回归：\n%s" % (rc, rep)
    assert "repro ok tab1" in _stdout_section(rep), "被测输出未见标记：\n%s" % rep
    assert code == 'print("repro ok tab1")\n', "落盘代码逐字不符：%r" % code


def test_g1_tab_one_nested_body():
    """1 tab＋嵌套体：tab 剥净后嵌套缩进仍须成立（多剥则平掉、少剥则 IndentationError）。"""
    rc, rep, code = _run_core(
        '前文\n' + _T + '```python\n' + _T + 'if True:\n' + _T + '    print("repro ok tab1nest")\n'
        + _T + '```\n后文',
        tempfile.mkdtemp(prefix="repro_599_tab1n_"))
    assert rc == 0, "tab＋嵌套体复现仍被误判失败（rc=%d）：\n%s" % (rc, rep)
    assert "repro ok tab1nest" in _stdout_section(rep), "被测输出未见标记：\n%s" % rep
    assert code == 'if True:\n    print("repro ok tab1nest")\n', "落盘代码逐字不符：%r" % code


def test_g1_tab_two():
    """2 tab 缩进围栏：缩进量按列宽累计（8 列），两枚 tab 须一并剥净。"""
    rc, rep, code = _run_core(
        '前文\n' + _T + _T + '```python\n' + _T + _T + 'print("repro ok tab2")\n'
        + _T + _T + '```\n后文',
        tempfile.mkdtemp(prefix="repro_599_tab2_"))
    assert rc == 0, "2 tab 复现仍被误判失败（rc=%d）：\n%s" % (rc, rep)
    assert "repro ok tab2" in _stdout_section(rep), "被测输出未见标记：\n%s" % rep
    assert code == 'print("repro ok tab2")\n', "落盘代码逐字不符：%r" % code


def test_g1_two_spaces_plus_tab():
    """2 空格＋1 tab（混合缩进，列宽 2+2=4）：旧实现 indent=2 只剥空格 ⇒ 剩 '\\tprint' 炸。"""
    rc, rep, code = _run_core(
        '前文\n  ' + _T + '```python\n  ' + _T + 'print("repro ok sp2tab1")\n  ' + _T + '```\n后文',
        tempfile.mkdtemp(prefix="repro_599_sp2t_"))
    assert rc == 0, "2 空格＋1 tab 复现仍被误判失败（rc=%d）：\n%s" % (rc, rep)
    assert "repro ok sp2tab1" in _stdout_section(rep), "被测输出未见标记：\n%s" % rep
    assert code == 'print("repro ok sp2tab1")\n', "落盘代码逐字不符：%r" % code


def test_g1_blockquote_tab():
    """引用块内 tab 缩进围栏：`> ` 剥掉后按 tab 围栏判（#362②×#599 复合形态）。"""
    rc, rep, code = _run_core(
        '前文\n> ' + _T + '```python\n> ' + _T + 'print("repro ok bq_tab")\n> ' + _T + '```\n后文',
        tempfile.mkdtemp(prefix="repro_599_bqt_"))
    assert rc == 0, "引用块内 tab 复现仍被误判失败（rc=%d）：\n%s" % (rc, rep)
    assert "repro ok bq_tab" in _stdout_section(rep), "被测输出未见标记：\n%s" % rep
    assert code == 'print("repro ok bq_tab")\n', "落盘代码逐字不符：%r" % code


# ── G2：防过修——空格 3/4、无缩进 3 组行为不变（改前即绿，落盘代码逐字不变） ──

def test_g2_no_indent_sensitive_structure():
    """无缩进围栏＋缩进敏感体：行为不变，落盘代码逐字节一致（零剥离、零多剥）。"""
    body = ('前文\n```python\nif True:\n    print("repro ok sp0")\nx = 1\n```\n后文')
    rc, rep, code = _run_core(body, tempfile.mkdtemp(prefix="repro_599_sp0_"))
    assert rc == 0, "无缩进形态被改坏（rc=%d）：\n%s" % (rc, rep)
    assert "repro ok sp0" in _stdout_section(rep), "被测输出未见标记：\n%s" % rep
    assert code == 'if True:\n    print("repro ok sp0")\nx = 1\n', "落盘代码逐字不符：%r" % code


def test_g2_three_space_indent():
    """3 空格缩进围栏：剥 3 列后嵌套体须完整保留（多剥 1 列即 IndentationError）。"""
    body = ('前文\n   ```python\n   if True:\n       print("repro ok sp3")\n   x = 1\n   ```\n后文')
    rc, rep, code = _run_core(body, tempfile.mkdtemp(prefix="repro_599_sp3_"))
    assert rc == 0, "3 空格形态被改坏（rc=%d）：\n%s" % (rc, rep)
    assert "repro ok sp3" in _stdout_section(rep), "被测输出未见标记：\n%s" % rep
    assert code == 'if True:\n    print("repro ok sp3")\nx = 1\n', "落盘代码逐字不符：%r" % code


def test_g2_four_space_indent():
    """4 空格缩进围栏（本执行核历来按前导空白认围栏的既有口径）：剥 4 列、嵌套保留。"""
    body = ('前文\n    ```python\n    if True:\n        print("repro ok sp4")\n    x = 1\n    ```\n后文')
    rc, rep, code = _run_core(body, tempfile.mkdtemp(prefix="repro_599_sp4_"))
    assert rc == 0, "4 空格形态被改坏（rc=%d）：\n%s" % (rc, rep)
    assert "repro ok sp4" in _stdout_section(rep), "被测输出未见标记：\n%s" % rep
    assert code == 'if True:\n    print("repro ok sp4")\nx = 1\n', "落盘代码逐字不符：%r" % code


# ── G3：报告声明为真——「已按 CommonMark 剥除」对 tab 正文不再是假话 ──────────

def test_g3_report_claim_true_for_tab():
    """tab 形态：执行声明行在场，且全报告无 IndentationError、无「脚本未正常结束」。

    声明行（`cmd_line`）写明「围栏缩进量与引用块标记已按 CommonMark 剥除、其余
    逐字未改」——#599 修复前对 tab 正文这句为假（执行必然 IndentationError）。
    """
    rc, rep, code = _run_core(
        '前文\n' + _T + '```python\n' + _T + 'print("repro ok claim")\n' + _T + '```\n后文',
        tempfile.mkdtemp(prefix="repro_599_claim_"))
    assert rc == 0, "rc=%d：声明无从谈真——\n%s" % (rc, rep)
    assert "围栏缩进量与引用块标记已按 CommonMark 剥除" in rep, \
        "报告缺「已按 CommonMark 剥除」声明行：\n%s" % rep
    assert "IndentationError" not in rep, \
        "声明已剥除但执行仍带 IndentationError——声明为假：\n%s" % rep
    assert "脚本未正常结束" not in rep, \
        "tab 复现仍被判死（脚本未正常结束）——#599 回归：\n%s" % rep
    assert "repro ok claim" in _stdout_section(rep)


# ── 量具自检 ────────────────────────────────────────────────────────────────

def test_gauge_is_alive():
    """量具自检：执行核在场、tab 在 Python 里确属缩进字符（IndentationError 前提）。"""
    assert os.path.isfile(_SCRIPT), "被测执行核不在场：%s" % _SCRIPT
    # 缺陷前提：tab 开头的模块级语句确实触发 IndentationError（否则 #599 无从复现）
    probe = tempfile.mkdtemp(prefix="repro_599_gauge_")
    p = os.path.join(probe, "t.py")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(_T + 'print("x")\n')
    r = subprocess.run([sys.executable, "-X", "utf8", p],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode != 0 and "IndentationError" in (r.stderr or ""), \
        "tab 开头落盘不炸 IndentationError ⇒ #599 前提不成立，本件判据需重审"
