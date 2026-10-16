# -*- coding: utf-8 -*-
"""test_issue612_repro_nested_container_guard · repro-bot 嵌套容器内 HTML 块漏判（#612）
====================================================================================
#612（P2 安全面，#520/#555/#568 同族残留）：`_container_strip` 每行只剥**一层**
容器前缀（`_bq_strip` 一遍 ＋ `_RE_LIST_MARKER.sub(count=1)` 一次）⇒ HTML 块起点
行处于**嵌套容器**时判不出「HTML 块开始」：
  · 形态 A：行首列表标记后还跟块引用标记（`- > <div title="`、`- > > <div title="`）
    ——剥一层后行首仍是容器标记，⑥ 型起点正则不认；
  · 形态 B：嵌套列表项（`  - <div title="`，外层列表项内容缩进内）——剥一层后
    虽露出 `<div`，但前行是外层列表项的段落文字（prev_blank=False），⑥ 型起点
    按「不打断段落」被拒。
后果：块内缩进围栏被当真围栏抽出执行，而 CommonMark 渲染下整段被吞进
`<div title="…">` 属性值——**页面上看不见的代码被执行**（人审的代码 ≠ runner
跑的代码）。改前实测（--dry-run 落盘读回）：G1 全部 7 例 chosen 均为
`print('HIDDEN…')`；markdown-it-py（commonmark 预设）渲染真值：7 例页面可见
`<pre><code>` 均只有 `print('VISIBLE…')`——改前读数与真值相反。

修法（判据口径）：
  1. `_container_strip` 循环剥净：反复「剥引用标记 → 剥列表标记」直到行首不再是
     容器标记（每层前导空格 ≤3，与既有 _BQ_MARKER/_RE_LIST_MARKER 口径一致；
     上限 _MAX_CONTAINER_LAYERS=8 防死循环）；#555 的单层形态是循环一层的特例。
  2. ⑥⑦ 起点的 prev_blank：起点行自带**可打断段落**的列表标记（圆弹/`1.`，
     CommonMark 5.2——`2.` 起头不得打断，markdown-it 对表验证）⇒ 标记开新列表
     项、打断前行段落 ⇒ 视同前行空白（形态 B 判出依赖它；**#595 后仅 ⑦ 型起点
     依赖它**——⑥ 型恒可打断段落，见下）。块引用标记不给此豁免（同引用延续/
     lazy continuation 无法与「引用新开」区分）。**#595 补正**：该豁免只对 ⑦ 型
     成立（⑦ 不打断段落 ⇒ `> 文本` 后 `> <custom-tag>` 的围栏是真代码，
     cmark-gfm 实测页面可见）；⑥ 型可打断段落，`> 文本` 后 `> <div title="`
     在 cmark-gfm 下整段被吞进属性值（实测页面只可见其后顶层围栏）⇒ ⑥ 型自
     #595 起不受 prev_blank 前置。
  3. 代码缩进的剥除（_dedent）不经 _container_strip（围栏路径按原始行算缩进），
     互不影响——G3 钉住这一点。

判据（本件，子进程＋临时 body 文件＋--out-dir 落盘读回）：
  G1 嵌套容器 7 例（题面最小复现 ＋ 形态 A 两例 ＋ 形态 A 带前文 ＋ 形态 B
     三变体）：全部不再选中隐藏块——正文块表只剩可见块（`python`×1）、chosen
     落到 print('VISIBLE…')、落盘脚本不含 HIDDEN。各例渲染语义：正文末尾的
     顶层围栏是页面唯一可见代码块（markdown-it commonmark 预设实测），故期望
     chosen=print('VISIBLE…')（而非整篇无块）。
  G2（防过修）单层容器既有形态零回归：复刻 test_issue520_409 G8/G9 的代表性
     正文两例（`> <div title="` / `- <div title="` 单层），断言与该件一致
     （chosen=VISIBLE，隐藏块不进块表）；另附引用块内合法围栏正例照抽。
  G3（防过修）普通缩进围栏（无容器）抽取行为不变：纯缩进围栏正常抽出且
     dedent 语义不变（3 空格缩进块的块内代码剥缩进后逐字相等；4 空格同抽）。
  G4（形态锚·定点变异自证）：循环剥除与打断豁免的接线不得被回退——
     `_MAX_CONTAINER_LAYERS` 上限、循环体、`list_marker_seen` 置位、以及
     `_html_block_start(content, prev_blank)` 调用形态（与 #520 G7 同锚）。
     变异验证（人工两向读数已录）：循环剥除回退单层 ⇒ G1 全红（7/7）＋G4 红；
     恢复 ⇒ 全绿。

运行（仓根）：python -X utf8 -m pytest tests/test_issue612_repro_nested_container_guard.py -q --no-header
退出码：0 = 全过；1 = 有断言失败。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "extract_and_run_issue_repro.py")

# ── G1 嵌套容器 7 例：改前 chosen 全为 HIDDEN 变体（见模块 docstring 读数）──
# (标签, 正文, 期望 chosen（= 页面唯一可见代码块）)
G1_CASES = [
    # 题面最小复现（形态 B：嵌套列表项，prev_blank=False 致 ⑥ 起点被拒）
    ("B_nested_item_minimal",
     "- 场景说明\n"
     "  - <div title=\"\n"
     "    ```python\n"
     "    print('HIDDEN')\n"
     "    ```\n"
     "    \"></div>\n"
     "\n"
     "```python\n"
     "print('VISIBLE')\n"
     "```\n",
     "print('VISIBLE')"),
    # 形态 A：列表标记后跟块引用标记（首行；改前剥一层后行首仍 `>`，⑥ 正则不认）
    ("A_list_then_bq",
     "- > <div title=\"\n"
     "  > ```python\n"
     "  > print('HIDDEN_A1')\n"
     "  > ```\n"
     "  > \"></div>\n"
     "\n"
     "```python\n"
     "print('VISIBLE_A1')\n"
     "```\n",
     "print('VISIBLE_A1')"),
    # 形态 A 双层引用：`- > > <div`（改前同样剥不净）
    ("A_list_then_bq_bq",
     "- > > <div title=\"\n"
     "  > > ```python\n"
     "  > > print('HIDDEN_A2')\n"
     "  > > ```\n"
     "  > > \"></div>\n"
     "\n"
     "```python\n"
     "print('VISIBLE_A2')\n"
     "```\n",
     "print('VISIBLE_A2')"),
    # 形态 A ＋ 前文列表项段落（循环剥净 ＋ 打断豁免同时需要）
    ("A_after_sibling_item",
     "- 场景说明\n"
     "- > <div title=\"\n"
     "  > ```python\n"
     "  > print('HIDDEN_A3')\n"
     "  > ```\n"
     "  > \"></div>\n"
     "\n"
     "```python\n"
     "print('VISIBLE_A3')\n"
     "```\n",
     "print('VISIBLE_A3')"),
    # 形态 B 变体：`*` 圆弹标记
    ("B_star_marker",
     "* 场景说明\n"
     "  * <div title=\"\n"
     "    ```python\n"
     "    print('HIDDEN_B3')\n"
     "    ```\n"
     "    \"></div>\n"
     "\n"
     "```python\n"
     "print('VISIBLE_B3')\n"
     "```\n",
     "print('VISIBLE_B3')"),
    # 形态 B 变体：有序外层 `1.` ＋ 3 空格内层（`1.` 可打断段落，豁免适用）
    ("B_ordered_outer",
     "1. 场景说明\n"
     "   - <div title=\"\n"
     "     ```python\n"
     "     print('HIDDEN_B4')\n"
     "     ```\n"
     "     \"></div>\n"
     "\n"
     "```python\n"
     "print('VISIBLE_B4')\n"
     "```\n",
     "print('VISIBLE_B4')"),
    # 形态 B 变体：块引用包裹的列表嵌套（`> - ` ／ `>   - ` 交错前缀）
    ("B_bq_wrapped_list",
     "> - 场景说明\n"
     ">   - <div title=\"\n"
     ">     ```python\n"
     ">     print('HIDDEN_B5')\n"
     ">     ```\n"
     ">     \"></div>\n"
     "\n"
     "```python\n"
     "print('VISIBLE_B5')\n"
     "```\n",
     "print('VISIBLE_B5')"),
]


def _run_dry(body):
    """写临时 body 文件、子进程跑执行核 --dry-run --out-dir；读回落盘脚本。

    返回 (returncode, stdout, 落盘脚本内容或 None)。--dry-run 仍会落盘抽出代码
    （repro_issue_local.py）但不执行——「落盘读回」即 runner 将要执行的内容。
    """
    with tempfile.TemporaryDirectory(prefix="repro612_guard_") as td:
        body_file = os.path.join(td, "body.md")
        with open(body_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        out_dir = os.path.join(td, "out")
        r = subprocess.run(
            [sys.executable, "-X", "utf8", SCRIPT,
             "--body-file", body_file, "--out-dir", out_dir, "--dry-run"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=REPO, timeout=120)
        script_path = os.path.join(out_dir, "repro_issue_local.py")
        script_txt = None
        if os.path.exists(script_path):
            with open(script_path, encoding="utf-8") as f:
                script_txt = f.read()
        return r.returncode, r.stdout or "", script_txt


def _extract_line(stdout):
    for ln in stdout.splitlines():
        if "个围栏块" in ln:
            return ln.strip()
    return ""


def test_g1_nested_container_hidden_fences_not_extracted():
    """G1：嵌套容器 7 例——隐藏块不再被选中/落盘，chosen 落到页面唯一可见块。"""
    for tag, body, want_visible in G1_CASES:
        rc, stdout, script = _run_dry(body)
        assert rc == 0, "[%s] --dry-run 退出码 %d\n%s" % (tag, rc, stdout)
        # 选中的是 python 块且正文块表只剩可见块（隐藏块不得进块表）
        line = _extract_line(stdout)
        assert "第 1 个围栏块" in line, "[%s] 未选中 python 块：%r" % (tag, line)
        assert "正文围栏块 1 个" in line, (
            "[%s] #612：隐藏围栏不得进块表（改前 2 个：python×2）：%r" % (tag, line))
        # 落盘读回：runner 将执行的内容恰为页面可见块，不含 HIDDEN
        assert script is not None, "[%s] 未落盘脚本" % tag
        assert "HIDDEN" not in script, (
            "[%s] #612：页面上看不见的代码被落盘执行（改前 chosen=HIDDEN）：%r"
            % (tag, script))
        assert script.strip() == want_visible, (
            "[%s] chosen 应为页面唯一可见块 %r，实得 %r"
            % (tag, want_visible, script))


def test_g2_single_container_forms_no_regression():
    """G2（防过修）：单层容器既有形态零回归——复刻 test_issue520_409 G8/G9 正文。

    两例的渲染语义（该件已对表 GitHub）：引用块/列表项内的 `<div title="` 是
    raw HTML，其内围栏被吞；正文顶层围栏是页面唯一可见块 ⇒ chosen=VISIBLE。
    """
    # G8 复刻：引用块内 HTML 块（改前 #555 时代的绕过体，修复后不得回潮）
    bq_body = ('> <div title="\n'
               '> ```python\n'
               '> print(HIDDEN_BQ)\n'
               '> ```\n'
               '> "></div>\n'
               '\n'
               '```python\n'
               'print(VISIBLE_BQ)\n'
               '```\n')
    rc, stdout, script = _run_dry(bq_body)
    assert rc == 0 and script is not None
    assert script.strip() == "print(VISIBLE_BQ)", (
        "G8 复刻：引用块内隐藏块不得被选中（实得 %r）" % script)
    # G9 复刻：列表项内 HTML 块
    li_body = ('- <div title="\n'
               '  ```python\n'
               '  print(HIDDEN_LI)\n'
               '  ```\n'
               '  "></div>\n'
               '\n'
               '```python\n'
               'print(VISIBLE_LI)\n'
               '```\n')
    rc, stdout, script = _run_dry(li_body)
    assert rc == 0 and script is not None
    assert script.strip() == "print(VISIBLE_LI)", (
        "G9 复刻：列表项内隐藏块不得被选中（实得 %r）" % script)
    # 引用块内合法围栏正例（#362②）：容器剥除不得波及围栏路径
    bq_fence = '> 前言\n> ```python\n> print(OK_BQ)\n> ```\n'
    rc, stdout, script = _run_dry(bq_fence)
    assert rc == 0 and script is not None
    assert script.strip() == "print(OK_BQ)", (
        "引用块内合法围栏须照抽（实得 %r）" % script)


def test_g3_plain_indented_fence_unchanged():
    """G3（防过修）：无容器的普通缩进围栏抽取行为不变（dedent 语义不动）。"""
    # 3 空格缩进围栏：块内代码剥掉开启缩进量后逐字相等（#362① 口径）
    body3 = "   ```python\n   if True:\n       y = 1\n   ```\n"
    rc, stdout, script = _run_dry(body3)
    assert rc == 0 and script is not None, stdout
    assert script == "if True:\n    y = 1\n", (
        "缩进围栏 dedent 语义被 #612 改动（实得 %r）" % script)
    # 纯 4 空格缩进围栏：仍正常抽出（题面指定复刻例）
    body4 = "    ```python\n    print('IND4')\n    ```\n"
    rc, stdout, script = _run_dry(body4)
    assert rc == 0 and script is not None, stdout
    assert script.strip() == "print('IND4')", (
        "4 空格缩进围栏仍须抽出（实得 %r）" % script)


def test_g4_mutation_loop_strip_and_interrupt_wiring():
    """G4（形态锚·定点变异自证）：循环剥净与打断豁免的接线不得被回退。

    回退任一（循环回单层 / 豁免置位删除 / 起点判定换原始行）⇒ G1 对应用例
    必红——判据不空转。`_html_block_start(content, prev_blank)` 调用形态与
    test_issue520_409 G7 同锚：#612 的豁免通过起点行前置 prev_blank 置位实现，
    调用形态保持不变。
    """
    with open(SCRIPT, encoding="utf-8") as f:
        src = f.read()
    assert "_MAX_CONTAINER_LAYERS" in src and \
        "for _ in range(_MAX_CONTAINER_LAYERS)" in src, (
            "#612：_container_strip 必须循环剥净（防死循环上限不得删）——"
            "回退单层即 G1 全红")
    assert "_RE_LIST_MARKER_INTERRUPT" in src, (
        "#612：可打断段落的列表标记口径（圆弹/`1.`）必须存在——`2.` 起头"
        "不得打断段落（markdown-it 对表），删掉即过修")
    assert "list_marker_seen" in src, (
        "#612：⑥⑦ 起点对带可打断列表标记的行视同前行空白的接线必须存在"
        "（形态 B 判出依赖它）")
    assert "_html_block_start(content, prev_blank)" in src, (
        "#520/#555/#612：抽取循环必须经 _html_block_start 判定 HTML 块起点"
        "（在剥容器后的内容上）——短路即回潮")


if __name__ == "__main__":
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
