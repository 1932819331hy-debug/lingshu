# -*- coding: utf-8 -*-
"""#153 A-2 终裁通过的验证标准重启即失效 —— 守卫（lingshu/core/core.py）

缺陷（lingshu issue #153 · 判定成立）：
  `SpacetimeMemoryEngine.__init__` 把运行配置写死为默认值
  （`self._verifier_config = {"dedup_static": 0.85, "deviation_threshold": 0.3}`），
  而四步制衡终裁（`adjudicate_verifier_standard`）通过后只改**内存**属性
  ⇒ **重启即失效**：盘上 `verifier_standards` 的 `approved` 行仍在，
  却不再生效（实测：终裁 0.9 → 内存 0.9 → 重启后回 0.85）。

修法（最小 · 复用既有持久面）：`__init__` 里在写死默认值之后调用
`_replay_verifier_standards()`——按 `decided_at` 升序回放 `status='approved'`
的行，走与终裁同一应用/钳制口径 `_apply_verifier_standard`（后者复用
`set_dedup_config` 的 `min(0.95, max(0.5, ...))` 钳制）。

判据来源：issue #153 报告口径；仓内理论章节追不到（属**经验标定**，本件）。
  · 未改「`set_dedup_config` 公开无密钥」一侧：设计稿
    `docs/plans/治理与写入边界_修复设计_v0.1.md` §4 修法形状 4 明确把
    `set_dedup_config` 当作「应用时**复用**其钳制」的**公开入口**（未被列为
    待加闸项）；是否给它加设计者闸属 §1.3 明列的「作用域」取舍，须设计者拍板，
    本件不擅自扩大。

断言组（抽掉 `_replay_verifier_standards()` 调用即红）：
  A 重启不失效：终裁 `dedup_static=0.9` 后重开同一库 ⇒ 配置仍为 0.9
     （缺陷形态：回 0.85）。
  B 多条按 decided_at 升序回放、后批覆盖先批（最后生效=最新一条）。
  C 越界 approved 值回放时**仍走钳制**（0.1 ⇒ 0.5）。
  D 防误杀：无 approved 行 ⇒ 基线 0.85 不变；`denied` 行不回放。
  E `deviation_threshold` 也回放（无认知组件时至少进 `_verifier_config`）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue153_verifier_standard_replay_guard.py -q --no-header
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

KEY = "GUARD-153-KEY"


def _db(name):
    return os.path.join(tempfile.mkdtemp(prefix="lingshu153_"), name + ".db")


def _approve(e, param, value, proposer="提议", reviewer="复核", cs="空间", adj="终裁"):
    vid = e.store.add_verifier_standard("标准", param, value, "理由", proposer)
    assert e.store.review_verifier_standard(vid, reviewer, True) is True
    assert e.store.cs_review_verifier_standard(vid, cs, True) is True
    assert e.adjudicate_verifier_standard(vid, adj, True, designer_key=KEY) is True
    return vid


# ---------- A 重启不失效 ----------

def test_a_approved_standard_survives_restart(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    db = _db("a")
    e = SpacetimeMemoryEngine(db)
    _approve(e, "dedup_static", 0.9)
    assert e.get_verifier_config()["dedup_static"] == 0.9
    e.close()

    e2 = SpacetimeMemoryEngine(db)
    try:
        assert e2.get_verifier_config()["dedup_static"] == 0.9, (
            "A 终裁标准重启后失效（缺陷形态：__init__ 写死默认 0.85、不重放 approved）：%r"
            % (e2.get_verifier_config(),))
        assert e2._dedup_static == 0.9, "A `_dedup_static` 未回放：%r" % (e2._dedup_static,)
    finally:
        e2.close()


# ---------- B 多条按 decided_at 升序、后批覆盖先批 ----------

def test_b_latest_approved_wins(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    db = _db("b")
    e = SpacetimeMemoryEngine(db)
    _approve(e, "dedup_static", 0.6)
    _approve(e, "dedup_static", 0.9)
    e.close()

    e2 = SpacetimeMemoryEngine(db)
    try:
        assert e2.get_verifier_config()["dedup_static"] == 0.9, (
            "B 多条 approved 回放次序错（应按 decided_at 升序、后批覆盖先批）：%r"
            % (e2.get_verifier_config(),))
    finally:
        e2.close()


# ---------- C 越界值回放仍走钳制 ----------

def test_c_out_of_range_approved_value_is_clamped_on_replay(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    db = _db("c")
    e = SpacetimeMemoryEngine(db)
    _approve(e, "dedup_static", 0.1)
    e.close()

    e2 = SpacetimeMemoryEngine(db)
    try:
        assert e2.get_verifier_config()["dedup_static"] == 0.5, (
            "C 回放未走钳制口径（应与 set_dedup_config 同款 min(0.95,max(0.5,·))）：%r"
            % (e2.get_verifier_config(),))
    finally:
        e2.close()


# ---------- D 防误杀 ----------

def test_d_no_approved_rows_keeps_baseline():
    e = SpacetimeMemoryEngine(_db("d"))
    try:
        assert e.get_verifier_config() == {"dedup_static": 0.85,
                                          "deviation_threshold": 0.3}, (
            "D 无 approved 行时基线被改动：%r" % (e.get_verifier_config(),))
    finally:
        e.close()


def test_d_denied_rows_are_not_replayed(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    db = _db("d2")
    e = SpacetimeMemoryEngine(db)
    vid = e.store.add_verifier_standard("标准", "dedup_static", 0.6, "理由", "甲")
    assert e.store.review_verifier_standard(vid, "乙", True) is True
    assert e.store.cs_review_verifier_standard(vid, "丙", True) is True
    # 终裁拒绝 ⇒ denied，不得回放
    assert e.adjudicate_verifier_standard(vid, "丁", False, designer_key=KEY) is True
    e.close()

    e2 = SpacetimeMemoryEngine(db)
    try:
        assert e2.get_verifier_config()["dedup_static"] == 0.85, (
            "D denied 行被回放（只应回放 approved）：%r" % (e2.get_verifier_config(),))
    finally:
        e2.close()


# ---------- E deviation_threshold 也回放 ----------

def test_e_deviation_threshold_replayed(monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", KEY)
    db = _db("e")
    e = SpacetimeMemoryEngine(db)
    _approve(e, "deviation_threshold", 0.42)
    e.close()

    e2 = SpacetimeMemoryEngine(db)
    try:
        assert e2.get_verifier_config()["deviation_threshold"] == 0.42, (
            "E deviation_threshold 未回放：%r" % (e2.get_verifier_config(),))
    finally:
        e2.close()
