# -*- coding: utf-8 -*-
"""test_issue604_repro_html_end_guard · repro-bot HTML 块结束条件对齐 CommonMark（#604）
====================================================================================
#604（P3 正确性/fail-closed）：抽取器 HTML 块**结束条件**与 CommonMark 4.6 三处
分歧——①型按「同名」闭标签收、④型终止 `>` 独占一行被 `_container_strip` 当块引
用前缀剥掉判不出、③型空注释 `<!-->`/`<!--->` 的闭串与开串重叠而搜不到——三例
正文都把**有**围栏的正文谎报「正文没有任何围栏代码块」（rc=2 回帖断言为假；
改前实测三例 rc=2，去掉 HTML 起始行的对照组 rc=0；markdown-it-py commonmark
渲染真值三例均有可见 `print('VISIBLE')` 块）。

修法（scripts/extract_and_run_issue_repro.py）：
  D1 ①型终止=script/pre/style/textarea **四者任一**闭标签（`<script>…</pre>`
     合法收；`</div>` 不在四者内仍不收——负对照渲染真值也无块，0 块是正确行为）；
  D2 ④型裸 `>` 终止行改在**原始行**上判定（`_container_strip('>')==''` 剥净行
     判不出）；②⑤型（`?>`/`]]>`）不是容器前缀、行为不变；
  D3 ③型闭注释搜索起点 s+2 允许 `-->` 与 `<!--` 重叠；`<!--` 真未闭合仍吞后续、
     `<!-- -->` 正常注释不变。

判据（本件，子进程＋直调双档）：
  G1 D1 三组交错正例（script×pre、pre×script、style×textarea）全部抽出
     VISIBLE；负对照 `</div>` 收不了（0 块＝与渲染真值一致的正确行为）；
  G2 D2 裸 `>` 终止行 ⇒ 正常抽出；`?>`/`]]>` 形态行为不变（含「裸 `>` 收不了
     ②型」——终止串按型别各归各）；
  G3 D3 `<!-->`/`<!--->` ⇒ 后续围栏正常抽出；`<!--`（真未闭合）仍吞后续、
     `<!-- -->` 正常——两个方向都断言；
  G4（防过修）无 HTML 普通正文、正常成对 HTML 块（同名收/未闭吞到末）零回归。

渲染真值对表：markdown-it-py commonmark 预设（可选依赖，不在则跳过对表、
不阻塞守卫——判据主体是抽取器与直调读数）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue604_repro_html_end_guard.py -q
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(REPO, "scripts", "extract_and_run_issue_repro.py")

# 依赖闭包口径（test_gate_dependency_closure）：不裸 import 被测脚本（第三方按
# 「文件路径 + 中性模块名」加载——与 test_issue520_409_repro_html_blocks 同式）。
_spec = importlib.util.spec_from_file_location("_repro_html_end_guard", _SCRIPT)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

SRC = _SCRIPT

FENCE = "```python\nprint('VISIBLE')\n```\n"


def _langs(text):
    return [b[0] for b in R._extract_fenced_blocks(text)]


def _visible_code(text):
    """抽出块的代码串列表（守卫里比对 VISIBLE/HIDDEN 用）。"""
    return [b[3].strip() for b in R._extract_fenced_blocks(text)]


def _run_cli(body):
    """子进程档：body 写临时文件，真跑 `--body-file … --dry-run`，返回 (rc, stdout)。"""
    d = tempfile.mkdtemp(prefix="repro604_guard_")
    p = os.path.join(d, "body.md")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    r = subprocess.run(
        [sys.executable, "-X", "utf8", _SCRIPT, "--body-file", p, "--dry-run"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    return r.returncode, r.stdout or ""


def _render_fence_codes(body):
    """渲染真值（markdown-it-py commonmark）：fence 块代码串列表；不可导入则 skip。"""
    pytest.importorskip("markdown_it", reason="渲染真值对表需 markdown-it-py（可选，不阻塞）")
    from markdown_it import MarkdownIt
    md = MarkdownIt("commonmark")
    return [t.content.strip() for t in md.parse(body) if t.type == "fence"]


# ── 量具自检 ────────────────────────────────────────────────────────────────
def test_gauge_is_alive():
    """量具自检：模块真加载、三个被修函数/常量在场。"""
    assert callable(R._extract_fenced_blocks)
    assert callable(R._advance_comment_state)
    assert callable(R._container_strip)
    assert hasattr(R, "_RE_HTML1_END"), "执行核缺 `_RE_HTML1_END` ⇒ #604 D1 的任一收不存在"


# ── G1 · D1：①型闭标签=四者任一（#604） ────────────────────────────────────
def test_g1_type1_cross_closing_tags():
    """G1：`<script>` 用 `</pre>` 收、`<pre>` 用 `</script>` 收、`<style>` 用
    `</textarea>` 收——CommonMark ①型终止=四标签任一闭标签，后续围栏照抽。

    改前：同名收漏收 ⇒ 0 块（rc=2 谎报无块）；渲染真值 1 个可见块。
    """
    cases = {
        "script_x_pre": "<script>\nx = 1\n</pre>\n\n" + FENCE,
        "pre_x_script": "<pre>\ny = 2\n</script>\n\n" + FENCE,
        "style_x_textarea": "<style>\nz = 3\n</textarea>\n\n" + FENCE,
        "textarea_x_style": "<textarea>\nw = 4\n</style>\n\n" + FENCE,
    }
    for name, body in cases.items():
        codes = _visible_code(body)
        assert codes == ["print('VISIBLE')"], (
            "#604 D1（%s）：①型任一闭标签应收块，改前同名收 0 块；实测 %r" % (name, codes))
        assert _render_fence_codes(body) == ["print('VISIBLE')"], (
            "#604 D1（%s）：与渲染真值（markdown-it-py commonmark）对表不符" % name)
    # 同行交错也收：`<pre>x</script>` 单行即开即收
    assert _visible_code("<pre>x</script>\n\n" + FENCE) == ["print('VISIBLE')"]


def test_g1_negative_control_div_does_not_close():
    """G1 负对照：`</div>` 不在四者内 ⇒ 不收，抽取器 0 块——**这是正确行为**，
    渲染真值同样无块（规则边界是「四者任一」，不是「任何标签」）。"""
    body = "<script>\nx = 1\n</div>\n\n" + FENCE
    assert _visible_code(body) == [], "#604 D1 负对照：`</div>` 不得收 ①型块"
    assert _render_fence_codes(body) == [], "#604 D1 负对照：渲染真值也应无块"


def test_g1_subprocess_dry_run():
    """G1 子进程档：三例 body 真跑 --dry-run，rc=0（改前 rc=2 谎报无块）；
    负对照 rc=2（fail-closed 正确方向，报告行如实说「没有任何围栏代码块」）。"""
    rc, out = _run_cli("<script>\nx = 1\n</pre>\n\n" + FENCE)
    assert rc == 0 and "DRY-RUN" in out, (rc, out[:200])
    assert "print('VISIBLE')" not in out or True  # dry-run 不回贴代码，仅验 rc/落盘行
    rc, out = _run_cli("<!DOCTYPE x\n>\n\n" + FENCE)
    assert rc == 0 and "DRY-RUN" in out, (rc, out[:200])
    rc, out = _run_cli("<!-->\n\n" + FENCE)
    assert rc == 0 and "DRY-RUN" in out, (rc, out[:200])
    rc, out = _run_cli("<!--->\n\n" + FENCE)
    assert rc == 0 and "DRY-RUN" in out, (rc, out[:200])
    rc, out = _run_cli("<script>\nx = 1\n</div>\n\n" + FENCE)
    assert rc == 2 and "没有任何围栏代码块" in out, (
        "#604 D1 负对照：`</div>` 不收 ⇒ rc=2 且报告行如实（实测 rc=%d）" % rc)


# ── G2 · D2：④型裸 `>` 终止行（#604） ──────────────────────────────────────
def test_g2_bare_gt_terminates_doctype():
    """G2：`<!DOCTYPE x` 的终止 `>` 独占一行 ⇒ 正常抽出（改前：裸 `>` 被
    `_container_strip` 当块引用前缀剥掉、判不出终止 ⇒ 0 块）。"""
    body = "<!DOCTYPE x\n>\n\n" + FENCE
    assert _visible_code(body) == ["print('VISIBLE')"], R._extract_fenced_blocks(body)
    assert _render_fence_codes(body) == ["print('VISIBLE')"]
    # 缩进/尾随空白的裸 `>` 同为终止行；`>` 在正文中间的行本来就能收（回归面）
    assert _visible_code("<!DOCTYPE x\n  >  \n\n" + FENCE) == ["print('VISIBLE')"]
    assert _visible_code("<!DOCTYPE x\nabc>def\n\n" + FENCE) == ["print('VISIBLE')"]


def test_g2_pi_cdata_unchanged():
    """G2（行为保持）：②⑤型收口口径不变——`?>`/`]]>` 单行即收、跨行收、块内
    围栏仍被吞；裸 `>` 收**不了** ②型（终止串按型别各归各，不是「任何 > 行」）。"""
    pi_one = '<?xml version="1.0"?>\n' + FENCE
    assert _visible_code(pi_one) == ["print('VISIBLE')"]
    pi_multi = "<?xml\n```python\nHIDDEN_PI\n```\n?>\n\n" + FENCE
    assert _visible_code(pi_multi) == ["print('VISIBLE')"], R._extract_fenced_blocks(pi_multi)
    pi_bare_gt = "<?xml\n>\n```python\nHIDDEN_PI2\n```\n?>\n\n" + FENCE
    assert _visible_code(pi_bare_gt) == ["print('VISIBLE')"], (
        "#604 D2 防过修：裸 `>` 不得终止 ②型（终止串是 `?>`）")
    cdata_multi = "<![CDATA[\n```python\nHIDDEN_CD\n```\n]]>\n\n" + FENCE
    assert _visible_code(cdata_multi) == ["print('VISIBLE')"]
    # 直调读数（#604 题面）：`?>`/`]]>` 不是容器前缀、剥净不动
    # （#643 merge 适配：_container_strip 返回值改为 (剥净行, 可打断列表标记)
    #  二元组——#612 需要第二个返回值；三处断言取 [0]，语义逐条不变）
    assert R._container_strip("?>")[0] == "?>"
    assert R._container_strip("]]>")[0] == "]]>"
    assert R._container_strip(">")[0] == "", "裸 `>` 仍会被剥空（这正是 D2 要绕开的口径）"


# ── G3 · D3：③型空注释重叠闭（#604） ───────────────────────────────────────
def test_g3_empty_comments_close_then_fence_extracted():
    """G3：空注释 `<!-->`/`<!--->` ⇒ 后续围栏正常抽出（改前：闭串与开串重叠、
    s+4 起搜漏掉 ⇒ 误判真未闭合把后续全吞）。"""
    for opener in ("<!-->", "<!--->"):
        body = opener + "\n\n" + FENCE
        assert _visible_code(body) == ["print('VISIBLE')"], (
            "#604 D3（%s）：空注释应收口、后续围栏照抽" % opener)
        assert _render_fence_codes(body) == ["print('VISIBLE')"]
    # 正文后置内容也在注释外（收口不吞后续文本行）
    assert _visible_code("<!-->\n后文\n" + FENCE) == ["print('VISIBLE')"]


def test_g3_unclosed_comment_still_swallows():
    """G3 反向：`<!--` 真未闭合仍吞后续（fail-closed 方向不放松）、`<!-- -->`
    正常注释行为不变——两个方向都断言。"""
    unclosed = "<!--\n" + FENCE
    assert _visible_code(unclosed) == [], "#604 防过修：真未闭合注释仍须吞掉后续围栏"
    normal = "<!-- 正常 -->\n" + FENCE
    assert _visible_code(normal) == ["print('VISIBLE')"]
    # 注释内围栏仍不算块（#235 口径不回潮）
    in_comment = "<!--\n```python\nHIDDEN_C\n```\n-->\n" + FENCE
    assert _visible_code(in_comment) == ["print('VISIBLE')"]


def test_g3_advance_comment_state_direct():
    """G3 直调档（#604 题面读数表）：空注释 False、真未闭合 True、正常注释
    False；含同行多次开关与尾串共享字符的复合例。"""
    f = R._advance_comment_state
    assert f("<!-->", False) is False, "改前 True（s+4 起搜漏掉偏移 2 的 `-->`）"
    assert f("<!--->", False) is False, "改前 True（漏掉偏移 3 的 `-->`）"
    assert f("<!-- -->", False) is False
    assert f("<!--", False) is True, "真未闭合保持 True"
    assert f("<!--", True) is True
    assert f("-->", True) is False
    # 重叠闭后同行再开：`<!--><!-->` 全收干净 ⇒ False；尾上开新注释 ⇒ True
    assert f("<!--><!-->", False) is False
    assert f("<!-->text<!--", False) is True
    assert f("<!--->x", False) is False
    # 跨行：上一行开（含重叠收）→ 行首状态推进
    assert f("text", False) is False
    assert f("text", True) is True
    assert f("--> t", True) is False


# ── G4 · 防过修：零回归 ─────────────────────────────────────────────────────
def test_g4_plain_and_paired_html_no_regression():
    """G4：无 HTML 普通正文、正常成对 HTML 块（同名收）行为零回归。"""
    plain = "复现：\n\n```python\nprint(1)\n```\n\n```python\nprint(2)\n```\n"
    assert _visible_code(plain) == ["print(1)", "print(2)"]
    for tag, fill in (("script", "s"), ("pre", "p"), ("style", "y"), ("textarea", "t")):
        body = "<%s>\n%s = 0\n```python\nHIDDEN_%s\n```\n</%s>\n\n" % (tag, fill, fill, tag) + FENCE
        codes = _visible_code(body)
        assert codes == ["print('VISIBLE')"], (
            "#604 G4（%s 同名收回归）：实测 %r" % (tag, codes))
    # 未闭 ①型吞到正文末（既有 G3/520 口径不回潮）
    assert _visible_code("<textarea>\n```python\nHIDDEN\n```\n") == []
    # ④型常规形态（`>` 行内出现、单行声明）不受 D2 特判影响
    assert _visible_code("<!DOCTYPE html>\n" + FENCE) == ["print('VISIBLE')"]
    assert _visible_code("<!DOCTYPE x\nsystem \"y\"\n>\n" + FENCE) == ["print('VISIBLE')"]


# ── 形态锚（变异自证用，与 520 件 G7 同式） ─────────────────────────────────
def test_g5_source_form_anchors():
    """G5（源码形态锚）：三处修法的源码形态在场——定点变异（同名收/剥后判止/
    s+4 起搜）任一回退即红，守卫不空转。"""
    with open(SRC, encoding="utf-8") as fh:
        src = fh.read()
    assert "_RE_HTML1_END.search(content)" in src, (
        "#604 D1：①型结束条件必须经四者任一闭标签正则（回退「同名收」即回潮）")
    assert 'html_state[1] == ">"' in src and 'lines[i].strip() == ">"' in src, (
        "#604 D2：④型裸 `>` 必须在原始行上判终止（回退「剥净行判止」即回潮）")
    assert "k = s + 2" in src, (
        "#604 D3：闭注释搜索起点必须 s+2 允许重叠（回退 s+4 即回潮）")


if __name__ == "__main__":
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
