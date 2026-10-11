# -*- coding: utf-8 -*-
"""#154 递归反思把自己的归档当对照 + 否定式被误判不可逆 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #154 · 判定成立，实读复现见下）：
  ① `_reflect_verify` 缺省分支用 `recall(claim, limit=3)` 做「记忆对照」，而
     `_archive_reflection` 每轮都把 `[反思链] <claim> ...`（tags=['reflection_chain']）
     写回知识层 ⇒ 同一 claim 第二次反思必然命中自己上一轮的归档，
     `deviation = len(hits) == 0` **从第二次起恒为 False**（自证循环）。
     实测（HEAD，修前）：r1 dev=True hits=[]；r2 dev=False
     hits=[{'content': '[反思链] 服务器磁盘占用率异常升高需要排查 | 偏差=True ...'}]
  ② `_terminal_judgment` 纯子串匹配不可逆词 ⇒「不删除用户数据，只做只读查询」
     被判 `reversibility='不可逆'`、`needs_designer=True`（否定式假阳性，且
     可逆词「查询」被不可逆词提前短路）。

修法（最小）：
  ① 对照检索先取 10 条候选，跳过带 `reflection_chain` 标签的**自身归档**，
     取前 3 条真对照（过滤后仍凑不齐 3 条即照实少给）。
  ② 对每个候选词加**紧邻否定前缀**判定（词前一位 ∈ 不没别勿非未莫 ⇒ 视为否定）；
     所有出现位置都被否定才算该词未命中。

判据来源：分诊表 `.tmp/triage_lingshu_ALL.md` 行 154；仓内无规定「反思对照须
排除自身归档」「否定式可逆性」的理论章节，口径为**经验标定**（本件 #154），
追不到更早出处。
边界（削掉的判别力）：②只判紧邻一位否定字，不解析「避免删除」「不是不可逆」
这类远距离/双重否定——这类仍按字面判不可逆（保守，偏升级设计者）。

断言组（抽掉任一处修复即对应项变红）：
  A 自引用：同一 claim 连reflect 两次，第二次 `deviation` 仍须为 True（修前 False）。
  B 否定式：`不删除用户数据，只做只读查询` ⇒ 可逆 + needs_designer False。
  C 防误杀（真对照仍在）：库中先写一条**非** reflection_chain 的同类记忆 ⇒ 对照命中，
    deviation=False；且该 hit 不得来自 reflection_chain 节点。
  D 防误杀（字面不可逆仍升级）：`删除用户数据` ⇒ 不可逆 + needs_designer True。

运行（仓根）：python -X utf8 -m pytest tests/test_issue154_reflection_self_reference_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

CLAIM = "服务器磁盘占用率异常升高需要排查"


def _db(name):
    return os.path.join(tempfile.mkdtemp(prefix="lingshu154_"), name + ".db")


def _hits_are_self_archive(hits):
    return bool(hits) and all("[反思链]" in h["content"] for h in hits)


def test_a_no_self_reference_second_reflection():
    """A 第二次反思不得被自己的归档消解偏差。"""
    e = SpacetimeMemoryEngine(_db("selfref"))
    r1 = e.recursive_reflect(CLAIM)
    r2 = e.recursive_reflect(CLAIM)
    assert r1["verification"]["deviation"] is True
    assert r2["verification"]["deviation"] is True, (
        "第二次反思偏差被自身归档消解：hits=%r" % (r2["verification"]["memory_hits"],))
    assert not _hits_are_self_archive(r2["verification"]["memory_hits"] or []), (
        "对照命中里含 reflection_chain 自身归档：%r"
        % (r2["verification"]["memory_hits"],))


def test_b_negated_irreversible_not_escalated():
    """B 「不删除」不得被判不可逆/升级设计者。"""
    e = SpacetimeMemoryEngine(_db("neg"))
    v = e.recursive_reflect("不删除用户数据，只做只读查询")["verdict"]
    assert v["reversibility"] == "可逆", v
    assert v["needs_designer"] is False, v


def test_c_genuine_memory_still_counts_as_hit():
    """C 防误杀：真对照记忆仍应命中（不得因过滤自身归档而把对照面清空）。"""
    e = SpacetimeMemoryEngine(_db("genuine"))
    e.add_perception(CLAIM + "，已定位为日志轮转配置错误", importance=0.6)
    r = e.recursive_reflect(CLAIM)
    hits = r["verification"]["memory_hits"]
    assert hits, "真对照记忆未命中：%r" % (hits,)
    assert r["verification"]["deviation"] is False, r["verification"]
    assert not _hits_are_self_archive(hits), hits


def test_d_literal_irreversible_still_escalated():
    """D 防误杀：字面「删除」仍判不可逆并升级设计者。"""
    e = SpacetimeMemoryEngine(_db("lit"))
    v = e.recursive_reflect("删除用户数据")["verdict"]
    assert v["reversibility"] == "不可逆", v
    assert v["needs_designer"] is True, v
