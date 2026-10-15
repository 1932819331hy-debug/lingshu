# -*- coding: utf-8 -*-
"""test_issue600_simloop_bom_priors_guard · load_priors 对 BOM 笔记的载入守卫（#600）
====================================================================================
缺陷（#600，成立）：``SimulationLoop.load_priors`` 扫描 ``{mdcg_root}/contextual/*.md``
时用 ``encoding="utf-8"`` 读文件——带 UTF-8 BOM 的笔记（#412 族语料）首字符是
``\ufeff``，``text.startswith("---")`` 判否 ⇒ frontmatter 取空 ⇒ tags 探测失败
⇒ ``continue`` **整篇静默跳过**（先验不载入、无告警、无报错）。

改前读数（本机，Windows / py3.12，main @ 375cf55）：同一篇合法先验笔记写两份
（``bom.md`` 带 BOM、``plain.md`` 不带），``load_priors`` 返回
``seeded=1，entities=[('plain.md', 'ap1')]``——BOM 件被丢。

修法：读取改 ``encoding="utf-8-sig"``（无 BOM 文件逐字节等价，BOM 在场时剥掉）。
与 #437（coggraph 四处同族读取点）同口径，本处为库内漏网的第五处。

守卫判据（注入式断言，回退修复即红）：
  G1  BOM 笔记与无 BOM 笔记**同等载入**（两篇都进 entities）；
  G2  无 BOM 路径零回归（既有行为不变）。

判据来源：issue #600 取证 + #437/#412 同族先例（「BOM 让 startswith("---")
判否 ⇒ 整篇跳过」的失效形态表）；理论章节不涉及编码口径。
运行：pytest tests/test_issue600_simloop_bom_priors_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.world.wm_simloop import SimulationLoop  # noqa: E402

NOTE = (
    "---\n"
    "title: simloop-prior\n"
    "tags: [wm_simloop, world]\n"
    "---\n"
    "\n"
    "- entity: apple id=ap1 pos=(1.0, 2.0, 3.0)\n"
)


def _make_root():
    """临时 mdcg 根：contextual/ 下 BOM 与无 BOM 各一篇同内容笔记。"""
    root = tempfile.mkdtemp(prefix="issue600_")
    ctx = os.path.join(root, "contextual")
    os.makedirs(ctx)
    with open(os.path.join(ctx, "bom.md"), "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(NOTE)
    with open(os.path.join(ctx, "plain.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(NOTE)
    return root


def test_bom_note_is_seeded_equally():
    """G1：BOM 笔记必须与无 BOM 笔记同等载入（#600：不得整篇静默跳过）。"""
    loop = SimulationLoop(wal_path=None)
    r = loop.load_priors(mdcg_root=_make_root(), limit=10)
    sources = {e["source"] for e in r["entities"]}
    assert sources == {"bom.md", "plain.md"}, (
        "BOM 笔记被 load_priors 静默跳过（#600）：seeded=%d sources=%r——"
        "contextual/*.md 读取须用 utf-8-sig（BOM 剥除），与 #437 coggraph 四处"
        "读取点同口径。" % (r["seeded"], sources))


def test_plain_note_still_seeded():
    """G2（防过修）：无 BOM 笔记载入路径零回归。"""
    loop = SimulationLoop(wal_path=None)
    r = loop.load_priors(mdcg_root=_make_root(), limit=10)
    assert r["seeded"] == 2, r
    assert any(e["eid"] == "ap1" and e["category"] == "apple"
               and tuple(e["pos"]) == (1.0, 2.0, 3.0) for e in r["entities"]), r


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
