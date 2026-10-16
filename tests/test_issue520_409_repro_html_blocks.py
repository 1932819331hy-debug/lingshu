# -*- coding: utf-8 -*-
"""test_issue520_409_repro_html_blocks · repro-bot HTML 块与 python3 识别（#520/#409）
====================================================================================
#520（P0 安全）：抽取器只认 `<!-- -->` 注释（#235），不懂 CommonMark HTML 块——
`<div title="` 起始、属性值跨行包住一个 ```python 围栏的正文，网页上被
GitHub 渲染吞进属性值（维护者看不见），抽取器却把它当**第一个 python 块执行**
（改前实测：chosen = `print('HIDDEN')…`——「人审的代码 ≠ runner 跑的代码」）。

修法：补 CommonMark 4.6 HTML 块七型状态机（①script/pre/style/textarea 按同
标签 `</tag>` 收、②`<?…?>`、③注释沿用 #235 状态机、④`<!声明…>`、
⑤`<![CDATA[…]]>`、⑥已知块标签空行收——**起点只需 `<tag` + 空白/>/EOL，属性
可跨行未闭**（#520 攻击形态）、⑦行首完整孤立标签空行收）；**⑥ 型可打断段落**
（#595 纠正：CommonMark 4.6 只规定 ⑦ 型不能打断）、⑦ 型仅在前行空白时启动。
HTML 块内的围栏不是代码围栏。

#409：`_PY_LANGS` 补 `python3`——GFM 合法 info string，旧集合漏收 ⇒ 合法
复现被判「正文无 python 围栏块」不执行（改前实测：choose 返回 None）。

判据（本件）：
  G1 #520 攻击体（申报原样、隐藏块改无害 print）：抽出块恰 1 个（VISIBLE），
     chosen=VISIBLE——隐藏块不进块表；
  G2 防过修·合法围栏仍认：`<div>` 后**空行**隔开的围栏是代码（CommonMark
     ⑥ 空行收）⇒ 抽出；普通正文围栏不受影响；
  G3 ①型：`<pre>` 吞围栏直到 `</pre>`；`<script>` 同；未闭标签吞到正文末；
  G4 ②④⑤型：`<?xml … ?>`、`<!DOCTYPE html>`、CDATA 各自收口（单行/跨行）；
  G5 #409：```python3 块被选中；
  G6 ⑥ 表判定面：`文本行` 紧跟 `<span title="`（span **不在**⑥表）⇒ 不开块，
     后续围栏照抽（GitHub 同渲染）；前行空白时 `<p title="`（在表中）⇒ 开块隐藏。
  G7 定点变异（源码形态锚）：`_html_block_start` 被短路（恒 None）⇒ G1/G3 必
     红——判据不空转。
  G10（#595）⑥ 型可打断段落：`文本行` 紧跟 `<div title="`（或 `<p` / `<details`）
     ⇒ 仍开块、块内围栏被吞（改前 chosen=print('HIDDEN_…')，cmark-gfm 渲染真值
     页面唯一可见块是 VISIBLE）；⑦ 型正对照（文本后不打断 ⇒ 围栏照抽；空行后
     仍吞）钉住「只把 ⑥ 型放出 prev_blank 门」。
  G11（形态锚·定点变异自证·#595）：⑥ 型分支不得再被 `if prev_blank:` 门住，
     ⑦ 型仍须受该门——回退任一即 G10 必红。

运行（仓根）：python -X utf8 -m pytest tests/test_issue520_409_repro_html_blocks.py -q
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 依赖闭包口径（test_gate_dependency_closure）：不裸 import（会被判未声明第三方），
# 按「文件路径 + 中性模块名」加载——与 test_issue363_235_241_repro_bot 同式。
_SCRIPT = REPO / "scripts" / "extract_and_run_issue_repro.py"
_spec = importlib.util.spec_from_file_location("_repro_html_guard", _SCRIPT)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

SRC = _SCRIPT

ATTACK = """复现见下方 python 代码块（维护者只需审这一个）：

<div title="
```python
print('HIDDEN')
```"></div>

```python
print('VISIBLE')
```
"""


def _langs(text):
    return [b[0] for b in R._extract_fenced_blocks(text)]


def test_g1_attack_hidden_block_not_extracted():
    """G1：攻击体只抽出 VISIBLE 块（改前：抽出 1 块=隐藏块且 chosen=HIDDEN）。"""
    blocks = R._extract_fenced_blocks(ATTACK)
    assert len(blocks) == 1 and blocks[0][0] == "python", [b[:2] for b in blocks]
    assert blocks[0][3].strip() == "print('VISIBLE')", blocks[0][3]
    chosen = R._choose_python_block(blocks)
    assert chosen and chosen[3].strip() == "print('VISIBLE')", (
        "#520：隐藏块不得被选中执行（改前 chosen=%r）"
        % (chosen[3][:40] if chosen else None))


def test_g2_legit_fences_still_extracted():
    """G2（防过修）：div 空行隔开的围栏、普通围栏照抽。"""
    body_div = "<div>\n\n```python\nprint(1)\n```\n\n</div>\n"
    assert _langs(body_div) == ["python"], R._extract_fenced_blocks(body_div)
    body_plain = "正文\n\n```python\nprint(2)\n```\n"
    assert _langs(body_plain) == ["python"]


def test_g3_raw_html_type1():
    """G3：script/pre 吞围栏直到闭标签；未闭吞到末尾。"""
    pre = "<pre>\n```python\nprint('IN_PRE')\n```\n</pre>\n\n```python\nprint('OK')\n```\n"
    assert _langs(pre) == ["python"], R._extract_fenced_blocks(pre)
    script = "<script>\n```python\nhidden\n```\n</script>\n"
    assert _langs(script) == [], R._extract_fenced_blocks(script)
    unclosed = "<textarea>\n```python\nhidden\n```\n"
    assert _langs(unclosed) == []


def test_g4_marker_types():
    """G4：<?…?> / <!DOCTYPE…> / CDATA 各自收口（单行与跨行）。"""
    pi = "<?xml version=\"1.0\"?>\n```python\nprint(1)\n```\n"
    assert _langs(pi) == ["python"]
    pi2 = "<?xml\n```python\nhidden\n```\n?>\n\n```python\nprint(2)\n```\n"
    assert _langs(pi2) == ["python"], R._extract_fenced_blocks(pi2)
    doctype = "<!DOCTYPE html>\n```python\nprint(3)\n```\n"
    assert _langs(doctype) == ["python"]
    cdata = "<![CDATA[\n```python\nhidden\n```\n]]>\n\n```python\nprint(4)\n```\n"
    assert _langs(cdata) == ["python"]


def test_g5_python3_recognized():
    """G5（#409）：```python3 块被选中（改前 choose=None）。"""
    body = "X\n```python3\nprint('PY3')\n```\n"
    c = R._choose_python_block(R._extract_fenced_blocks(body))
    assert c and c[3].strip() == "print('PY3')", c


def test_g6_type6_paragraph_interrupt_rule():
    """G6：⑥ 型表与 ⑦ 型段落打断口径的**既有断言面**（改前口径，见 G10 纠正）。

    两口径都与 cmark 对表：⑥ 型标签表是**块级容器**表（CommonMark 4.6），
    `span` 不在其中——`<span title="` 在 GitHub 上同样按段落渲染、其内围栏
    是代码（抽取＝正确）；`<p title="` 在表中，前行空白时开块、其内围栏被
    吞（攻击可达面）。**#595 补正**：表中标签（6 型）前行**非**空白时同样
    开块（可打断段落）——该格由 G10 覆盖，本函数的三条断言不涉该格、不变。
    """
    inline_span = "某段落文本\n<span title=\"\n```python\nprint('RENDERED_INLINE')\n```\n\"\n\n后文\n"
    assert _langs(inline_span) == ["python"], R._extract_fenced_blocks(inline_span)
    after_blank = "\n<p title=\"\n```python\nprint('HIDDEN_P')\n```\n\"\n"
    assert _langs(after_blank) == []
    # 行内 span 前是空行也一样（span 不在⑥表 ⇒ 永不藏代码）
    blank_span = "\n<span title=\"\n```python\nprint('RENDERED_SPAN')\n```\n\"\n"
    assert _langs(blank_span) == ["python"]


def test_g7_mutation_html_state_machine_required():
    """G7（形态锚·定点变异自证）：HTML 块状态机在抽取循环里不得被短路。"""
    src = SRC.read_text(encoding="utf-8")
    assert '_html_block_start(content, prev_blank)' in src, (
        "#520/#555 残留：抽取循环必须经 _html_block_start 判定 HTML 块起点"
        "（在剥容器后的内容上）——短路即回潮（G1/G3/G8/G9 必红）")
    assert '_container_strip' in src, (
        "#555 残留：容器前缀剥离函数必须存在")
    assert '"python3" in _PY_LANGS' in src or '"python", "python3"' in src, (
        "#409：_PY_LANGS 必须含 python3")


# --------------------------------------- G8/G9 · #555 残留（容器内 HTML 块绕过）
def test_g8_blockquote_html_block_bypass():
    """G8：引用块 `> <div title="` 内的围栏不得被抽取执行（改前 chosen=HIDDEN）。"""
    body = ('> <div title="\n'
            '> ```python\n'
            '> print(HIDDEN_BQ)\n'
            '> ```\n'
            '> "></div>\n'
            '\n'
            '```python\n'
            'print(VISIBLE_BQ)\n'
            '```\n')
    blocks = R._extract_fenced_blocks(body)
    chosen = R._choose_python_block(blocks)
    assert chosen and chosen[3].strip() == 'print(VISIBLE_BQ)', (
        "#555 残留：引用块内 HTML 块的围栏不得被选中（改前 chosen=%r）"
        % (chosen[3][:40] if chosen else None))
    # 防过修：正常引用块内的围栏不受影响
    bq_fence = '> 前言\n> ```python\n> print(OK_BQ)\n> ```\n'
    blocks2 = R._extract_fenced_blocks(bq_fence)
    assert len(blocks2) == 1 and 'OK_BQ' in blocks2[0][3]


def test_g9_list_item_html_block_bypass():
    """G9：列表项 `- <div title="` 内的围栏不得被抽取执行（改前 chosen=HIDDEN）。"""
    body = ('- <div title="\n'
            '  ```python\n'
            '  print(HIDDEN_LI)\n'
            '  ```\n'
            '  "></div>\n'
            '\n'
            '```python\n'
            'print(VISIBLE_LI)\n'
            '```\n')
    blocks = R._extract_fenced_blocks(body)
    chosen = R._choose_python_block(blocks)
    assert chosen and chosen[3].strip() == 'print(VISIBLE_LI)', (
        "#555 残留：列表项内 HTML 块的围栏不得被选中（改前 chosen=%r）"
        % (chosen[3][:40] if chosen else None))



# ------------------------------------- G10/G11 · #595（⑥ 型可打断段落）
def test_g10_type6_interrupts_paragraph():
    """G10（#595）：⑥ 型**可以**打断段落——文本行紧跟 6 型标签仍开块。

    CommonMark 4.6：1–6 型 HTML 块都可打断段落，**只有 ⑦ 型不能**。改前实现把
    ⑥⑦ 一起放在 `if prev_blank:` 门下 ⇒ 正文「一行文字」紧跟 `<div title="` 时
    判它**不是**块，属性值里的围栏被当第一个 python 块执行（改前实测
    chosen=print('HIDDEN_…')），而 GitHub（cmark-gfm）把整段吞进属性值、页面
    唯一可见代码块是 VISIBLE ⇒ 人审的代码 ≠ runner 跑的代码。

    三型标签（div / p / details，均在 ⑥ 表）同判；⑦ 型两向正对照证明「只放 ⑥ 型
    出 prev_blank 门」而非整体拆门。
    """
    for tag in ("div", "p", "details"):
        body = ("复现步骤如下：\n"
                "<%s title=\"\n" % tag
                + "```python\nprint('HIDDEN_%s')\n```\n" % tag.upper()
                + "\"></%s>\n\n" % tag
                + "```python\nprint('VISIBLE')\n```\n")
        blocks = R._extract_fenced_blocks(body)
        chosen = R._choose_python_block(blocks)
        assert chosen and chosen[3].strip() == "print('VISIBLE')", (
            "#595：文本后紧跟 ⑥ 型 <%s> 起点必须开块（改前 chosen=%r）"
            % (tag, chosen[3][:40] if chosen else None))
        assert len(blocks) == 1, [b[:3] for b in blocks]
    # ⑥ 型前有空行时行为不变（既有正确面不得回退）
    after_blank = ("\n<div title=\"\n```python\nprint('HIDDEN_B')\n```\n\"></div>\n")
    assert _langs(after_blank) == [], R._extract_fenced_blocks(after_blank)
    # ⑦ 型正对照①：文本后紧跟 7 型标签**不**开块（⑦ 不打断段落）⇒ 围栏照抽
    seven_text = ("文本\n<custom-tag attr=\"x\">\n"
                  "```python\nprint('SEVEN')\n```\n")
    assert _langs(seven_text) == ["python"], R._extract_fenced_blocks(seven_text)
    # ⑦ 型正对照②：空行后 7 型标签**仍**开块吞围栏 ⇒ ⑦ 的 prev_blank 门没被拆
    seven_blank = ("\n<custom-tag attr=\"x\">\n"
                   "```python\nprint('SEVEN_B')\n```\n")
    assert _langs(seven_blank) == [], R._extract_fenced_blocks(seven_blank)


def test_g11_type6_not_gated_by_prev_blank_source_anchor():
    """G11（形态锚·定点变异自证·#595）：⑥ 型分支不得再被 prev_blank 门住。

    判据是**源码形态**（与 G7 同式）：回退到 `if prev_blank:` 包住 ⑥ ⇒ G10 必红；
    把 ⑦ 的门一并拆掉（过修）⇒ 本件断言也红。
    """
    src = SRC.read_text(encoding="utf-8")
    assert "if prev_blank:\n        if _RE_HTML6_START.match(line):" not in src, (
        "#595：⑥ 型起点分支不得再置于 `if prev_blank:` 门下（回退即 G10 必红）")
    assert "if _RE_HTML6_START.match(line):" in src, (
        "#595：⑥ 型起点判定必须存在")
    assert "if prev_blank and _RE_HTML7_START.match(line)" in src, (
        "#595：⑦ 型仍须受 prev_blank 门（CommonMark 4.6：⑦ 不打断段落）"
        "——拆掉即过修")


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
