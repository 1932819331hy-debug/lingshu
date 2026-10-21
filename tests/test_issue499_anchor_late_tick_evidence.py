# -*- coding: utf-8 -*-
"""test_issue499_anchor_late_tick_evidence · #499 守卫：observation_tick 不是接收闸
============================================================================
守卫对象（lingshu/world/anchor_verify.py · AnchorVerification.add_channel_evidence）：
  #499：PR #445（9b56bff）新增的 `observation_tick` 按**锚点级**最新 tick 丢弃一切
  更早证据——`_latest_tick` 游标只需要「防止更早时刻推进 `_round` / `verified_rounds`」，
  实现却在写入 `channel_evidence` 与 registry **之前**提前 return，把更早 tick 的任何
  证据整条拒收（返回正常 anchor_state、无任何标记）。

  触发面：多通道传感器读数**乱序到达**（触觉/行动类强通道天然慢一拍）。
  改前实测（本件守卫的 RED 读数）：视觉/听觉 tick 10 支持 → `ACCEPT_strong`；
  触觉 tick 9 完全反驳（evidence=0.0）⇒ `channel_evidence` 里没有 `tactile`、
  registry hits/misses=0/0、`verify_anchor` 仍报 `ACCEPT_strong`。
  后果＝锚点确认度判错 + `ChannelCredibilityRegistry` 统计失真，且静默（返回值无提示）。

修法（最小改动，走「修实现」而非「改契约」）：
  `_latest_tick` 只作「是否推进稳定轮数」的游标——只有 `observation_tick >= _latest_tick`
  时才把 `_round` 前移；更早 tick 的证据照常入账、照常更新 registry，只是不推进轮数。
  选路理由：本参数声明（docstring「同一观测时刻只可推进一次稳定轮数」＋
  `docs/world_model_metrics_migration.md`「可传 observation_tick 合并同刻证据」＋
  `bench/README.md`「Use observation_tick for same-time channel grouping」）把 tick 定义为
  **同刻合并**，不是「丢弃陈旧观测」的接收闸；且丢弃的是**强通道反证**，与 #400/#402 的
  fail-closed 方向相反（不得乐观兜底）。

守卫点（每条都是可判别的读数断言）：
  A 迟到一拍的另一通道证据入账（channel_evidence 有 tactile=0.0）；
  B 该反证使确认度落到 NOT_ACCEPTED、稳定轮数清零；
  C 该反证进 ChannelCredibilityRegistry（hits/misses=0/1）；
  D 迟到一拍**不推进**稳定轮数（更早 tick 不新增轮次）；
  E 迟到一拍之后，更新的 tick 照常推进（游标未被回退）；
  F 同 tick 多通道仍只算一轮（#445 声明的性质不回归）；
  G 正序（tick=10）读数与修复前一致——修复只放宽「接收」，未动「轮数」口径；
  H tick 游标是**锚点级**（#499 标题措辞）——另一锚点用更早 tick 不受阻（如实登记
    当前粒度，非缺陷主张；派单摘要「按全局时刻推进轮数」与 issue 正文不符，见报告）。

运行：python -X utf8 -m pytest tests/test_issue499_anchor_late_tick_evidence.py
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world.anchor_verify import STABLE_MIN_INTERVAL, AnchorVerification  # noqa: E402
from lingshu.world.channel_credibility import ChannelCredibilityRegistry  # noqa: E402


def _late_refutation(late_tick):
    """视觉/听觉在 tick 10 支持（→ ACCEPT_strong），触觉强反证在 `late_tick` 到达。"""
    reg = ChannelCredibilityRegistry()
    v = AnchorVerification(registry=reg, clock=lambda: 0.0)
    v.add_channel_evidence("chair", "visual", 1.0, observation_tick=10)
    v.add_channel_evidence("chair", "audio", 1.0, observation_tick=10)
    before = v.verify_anchor("chair")
    v.add_channel_evidence("chair", "tactile", 0.0, observation_tick=late_tick)
    after = v.verify_anchor("chair")
    return before, after, reg


# ---- A：迟到一拍的另一通道证据必须入账（主断言，变异自证点名点）----

def test_late_tick_evidence_from_another_channel_is_recorded():
    _, after, _ = _late_refutation(9)
    assert after["channel_evidence"].get("tactile") == 0.0, (
        "tick 9 的触觉反证被静默丢弃：channel_evidence=%r" % (after["channel_evidence"],)
    )


# ---- B：该反证必须使确认度下降、稳定轮数清零 ----

def test_late_tick_evidence_degrades_confirmation():
    before, after, _ = _late_refutation(9)
    assert before["confirmation"] == "ACCEPT_strong"
    assert after["confirmation"] == "NOT_ACCEPTED"
    assert after["verified_rounds"] == 0


# ---- C：该反证必须进通道可信度 registry ----

def test_late_tick_evidence_updates_registry():
    _, _, reg = _late_refutation(9)
    st = reg.channel_state("tactile")
    assert (st["hits"], st["misses"]) == (0, 1), (
        "registry 未收到迟到反证：hits/misses=%r/%r" % (st["hits"], st["misses"])
    )


# ---- D：迟到一拍不推进稳定轮数 ----

def test_late_tick_evidence_does_not_advance_stable_rounds():
    v = AnchorVerification(clock=lambda: 0.0)
    v.add_channel_evidence("chair", "visual", 1.0, observation_tick=10)
    v.add_channel_evidence("chair", "audio", 1.0, observation_tick=10)
    assert v.verify_anchor("chair")["verified_rounds"] == 1
    # 迟到的**支持**证据（tick 9）：入账，但不得让更早时刻再算一轮
    v.add_channel_evidence("chair", "tactile", 1.0, observation_tick=9)
    r = v.verify_anchor("chair")
    assert r["channel_evidence"]["tactile"] == 1.0
    assert r["verified_rounds"] == 1


# ---- E：迟到一拍之后，更新的 tick 照常推进 ----

def test_newer_tick_still_advances_after_a_late_one():
    t = [0.0]
    v = AnchorVerification(clock=lambda: t[0])
    rounds = []
    for tick in (10, 9, 11):
        v.add_channel_evidence("chair", "visual", 1.0, observation_tick=tick)
        v.add_channel_evidence("chair", "audio", 1.0, observation_tick=tick)
        rounds.append(v.verify_anchor("chair")["verified_rounds"])
        t[0] += STABLE_MIN_INTERVAL
    assert rounds == [1, 1, 2], "轮数序列=%r（tick 9 不应推进，tick 11 应推进）" % (rounds,)


# ---- F：同 tick 多通道仍只算一轮（#445 声明的性质）----

def test_same_tick_multiple_channels_still_count_once():
    # 时钟每轮前移 ≥ STABLE_MIN_INTERVAL：若 tick 合并没有生效，三次提交会各推一轮
    t = [0.0]
    v = AnchorVerification(clock=lambda: t[0])
    for channel in ("tactile", "audio", "action"):
        t[0] += STABLE_MIN_INTERVAL
        v.add_channel_evidence("a", channel, 0.9, observation_tick=1)
        result = v.verify_anchor("a")
    assert result["verified_rounds"] == 1


# ---- G：正序读数与修复前一致（修复只放宽接收，未动轮数）----

def test_forward_order_reading_unchanged():
    _, after, reg = _late_refutation(10)
    assert after["channel_evidence"]["tactile"] == 0.0
    assert after["confirmation"] == "NOT_ACCEPTED"
    assert (reg.channel_state("tactile")["hits"],
            reg.channel_state("tactile")["misses"]) == (0, 1)


# ---- H：tick 游标是锚点级（如实登记当前粒度）----

def test_tick_cursor_is_per_anchor():
    v = AnchorVerification()
    v.add_channel_evidence("a", "visual", 1.0, observation_tick=5)
    v.add_channel_evidence("a", "audio", 1.0, observation_tick=5)
    assert v.verify_anchor("a")["confirmation"] == "ACCEPT_strong"
    # b 锚点用更早 tick：锚点级游标 ⇒ 不受 a 的 tick 5 影响
    v.add_channel_evidence("b", "visual", 1.0, observation_tick=1)
    v.add_channel_evidence("b", "audio", 1.0, observation_tick=1)
    rb = v.verify_anchor("b")
    assert rb["confirmation"] == "ACCEPT_strong"
    assert rb["verified_rounds"] == 1
