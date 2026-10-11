# -*- coding: utf-8 -*-
"""test_issue20_146_342_provenance_guard —— provenance 三件缺陷的定点守卫。

每一条断言都钉住「缺陷形态不再存在」（抽掉修复即红），不是「代码能跑」。
全部用纯标准库 + `:memory:`/字符串输入，不落盘、不碰在役记忆库。

覆盖（lingshu issue 号）：

  #20-A  验证方式可反推性：`VERIFY_METHODS` 六项中此前仅 2 项可从 legacy 标签
         反推（`白箱校验`→whitebox_code、`llm_verify`），`human_review` 属
         `STRONG_METHODS` 却永远推不出 ⇒ `['verified','human_review']` 的
         methods 掉成 `['none']`、strength 由 strong 静默降为 weak。
         断言：每一个规范方式名都有一条 legacy 入口可反推；历史中文标签
         `白箱校验` 仍映射到 whitebox_code（旧数据口径不破）。
  #20-B  可信度不自相矛盾：`白箱校验` 记录被填 `source="assistant"`，而
         `is_traceable()` 要求 user/assistant 必须有 session_id ⇒ 同一条记录
         `strength()=="strong"` 而 `is_traceable()==False`。断言：不存在
         「strong 且不可回溯」的组合；同时钉住 strong 判定本身未被削弱
         （防止用「让 strength 永不 strong」把不变量做成恒真）。
  #146  往返：`to_tags` 写出的规范英文方式名与 `vref:<ref>` 凭证，
         `from_legacy` 此前一个都不读（只认中文 `白箱校验`/`llm_verify`、全函数
         无 `vref:` 读取）⇒ strong 记录往返后降 weak、`validate()` 反报
         「未声明 verify_methods / 缺 verify_ref」。断言：往返后方式集合、
         凭证、strength 不变，且 validate() 不再报这两条。
  #342  形状异常既有行：`from_legacy` / `is_default_cs` / `Provenance.from_json`
         遇形状不对的行（tags 列是数字/字符串标量、condition_space 是数组、
         `time_window=[null,null]`——core 自己的 ConditionSpace 规范化产物、
         非数值时间窗）直接抛异常，未按契约「推不出就留 unknown」。断言：
         这些输入一律不抛，且落 unknown / 默认窗。

判据来源：
  · #20-A/#20-B/#146/#342 的正文与实测表（GitHub issue，见各断言注释）；
  · #20-B 的豁免口径属**口径决策**（issue 正文「建议判据」第 2 条明写
    「若该组合在语义上合法……则应在 is_traceable() 上显式豁免该 source/方式
    组合，并写一条用例固化这个豁免」）——本件即那条用例；削掉的判别力写在
    `is_traceable()` docstring（强验证记录即便无会话、来源 unknown 也判可回溯）。

与本批其它守卫的关系：`tests/test_provenance.py` 守的是 `verified` 状态反推、
`tests/test_provenance_sources.py` 守的是来源优先级，与本件三条断言**无重叠**
（本件断言在抽掉本件修复后必红，见 docstring 末的定点变异记录）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue20_146_342_provenance_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import ConditionSpace  # noqa: E402
from lingshu.core.provenance import (  # noqa: E402
    Provenance, STRONG_METHODS, VERIFY_METHODS, from_legacy, is_default_cs,
    new_provenance)

_TS = 1786728904.0


def _legacy(tags, created_at=_TS, cs=None, modality="text"):
    return from_legacy(tags, created_at, cs, modality)


# =============================================================================
# #20-A  每一个验证方式都必须能从 legacy 标签反推出来
# =============================================================================

def test_20a_every_verify_method_is_inferable():
    """VERIFY_METHODS 的每一项（除 none）都必须有 legacy 入口可反推。

    缺陷形态：`['verified','human_review']` → methods=['none']（strong 静默降 weak）。
    """
    missing = []
    for m in sorted(VERIFY_METHODS - {"none"}):
        p = _legacy([m])
        if m not in p.verify_methods:
            missing.append((m, list(p.verify_methods)))
    assert not missing, f"这些验证方式无法从 legacy 标签反推: {missing}"


def test_20a_strong_methods_keep_strength():
    """强验证方式经 legacy 反推后必须仍是 strong（不是只剩 none/weak）。"""
    for m in sorted(STRONG_METHODS):
        p = _legacy(["verified", m])
        assert m in p.verify_methods, (m, p.verify_methods)
        assert p.strength() == "strong", (m, p.verify_methods, p.strength())


def test_20a_legacy_chinese_tag_still_maps():
    """历史中文标签 `白箱校验` 仍映射到 whitebox_code（旧数据口径不破）。"""
    p = _legacy(["白箱校验", "llm_verify", "partial"])
    assert p.verify_methods == ["whitebox_code", "llm_verify"], p.verify_methods


# =============================================================================
# #20-B  strength=strong 与 is_traceable=False 不得并存
# =============================================================================

def test_20b_no_strong_yet_untraceable():
    """不存在「强验证却不可回溯」的记录（issue #20 建议判据第 2 条）。

    缺陷形态：`['anchored','白箱校验']` → strength=strong 而 traceable=False。
    """
    cases = [
        ["verified", "白箱校验"],
        ["anchored", "白箱校验"],
        ["verified", "human_review"],
        ["anchored", "action_world"],
        ["verified", "whitebox_code"],
        ["白箱校验", "llm_verify", "partial"],
    ]
    for tags in cases:
        p = _legacy(tags)
        assert p.strength() == "strong", (tags, p.verify_methods, p.strength())
        assert p.is_traceable(), f"{tags} 强验证却不可回溯（strength/traceable 自相矛盾）"


def test_20b_strong_judgement_not_weakened():
    """不变量不得被做成恒真：strong 判定本身必须还在（防「strength 永不 strong」式绕过）。"""
    p = _legacy(["anchored", "白箱校验"])
    assert p.verify_methods == ["whitebox_code"], p.verify_methods
    assert p.strength() == "strong", p.strength()


def test_20b_weak_records_stay_untraceable_without_session():
    """反向钉住：弱验证 / 无验证且无会话的记录仍判不可回溯（豁免不外溢）。"""
    for tags in (["partial"], ["verified", "cross_channel"], ["llm_verify", "verified"]):
        p = _legacy(tags)
        assert p.strength() != "strong", (tags, p.strength())
        assert not p.is_traceable(), (tags, p.strength())


def test_20b_traceable_with_session_unchanged():
    """有会话的 user 记录仍判可回溯（既有正例不破）。"""
    p = _legacy(["dsh", "user", "session:dsh-1"])
    assert p.is_traceable()


# =============================================================================
# #146  to_tags → from_legacy 往返：方式 / 凭证 / 强度 不变
# =============================================================================

def test_146_round_trip_preserves_methods_ref_strength():
    """任意合法 verify_methods + 非空 verify_ref 往返后集合、凭证、strength 不变。

    缺陷形态：往返后 methods=['none']、ref=''、strength=weak。
    """
    combos = [
        ["whitebox_code"],
        ["action_world", "cross_channel"],
        ["whitebox_code", "action_world", "cross_channel"],
        ["human_review"],
        ["llm_verify", "cross_channel"],
    ]
    for methods in combos:
        p = new_provenance(
            "测试台", "bench_fixture", "fixture", verify_status="partial",
            verify_methods=list(methods),
            verify_ref="tests/test_x.py::test_y", observed_at=_TS)
        cs, tags = p.to_sql()
        q = from_legacy(tags, _TS, cs)
        assert set(q.verify_methods) == set(methods), (methods, q.verify_methods)
        assert q.verify_ref == "tests/test_x.py::test_y", (methods, q.verify_ref)
        assert q.strength() == p.strength(), (methods, q.strength(), p.strength())


def test_146_round_trip_clears_validate_complaints():
    """往返后 validate() 不再报「未声明 verify_methods / 缺 verify_ref」。"""
    p = new_provenance(
        "测试台", "bench_fixture", "fixture", verify_status="partial",
        verify_methods=["whitebox_code", "action_world", "cross_channel"],
        verify_ref="tests/test_x.py::test_y", observed_at=_TS)
    cs, tags = p.to_sql()
    q = from_legacy(tags, _TS, cs)
    problems = [x for x in q.validate()
                if "verify_methods" in x or "verify_ref" in x]
    assert not problems, problems


def test_146_round_trip_keeps_strong():
    """强验证往返后 strength 仍是 strong（防「往返降级为 weak」的形态）。"""
    p = new_provenance(
        "测试台", "bench_fixture", "fixture", verify_status="anchored",
        verify_methods=["whitebox_code"], verify_ref="pytest tests/test_x.py",
        observed_at=_TS)
    cs, tags = p.to_sql()
    q = from_legacy(tags, _TS, cs)
    assert q.verify_methods == ["whitebox_code"], q.verify_methods
    assert q.verify_ref == "pytest tests/test_x.py", q.verify_ref
    assert q.strength() == "strong", q.strength()


def test_146_vref_with_colon_is_read_whole():
    """凭证里的 ':' 不得被截断（vref: 前缀只削一次）。"""
    q = from_legacy(["fixture", "partial", "whitebox_code",
                     "vref:tests/test_a.py::TestB::test_c"])
    assert q.verify_ref == "tests/test_a.py::TestB::test_c", q.verify_ref


# =============================================================================
# #342  形状异常的既有行不得抛异常（按契约落 unknown）
# =============================================================================

def test_342_core_produced_null_time_window_does_not_crash():
    """core 自己 ConditionSpace 规范化后落库的 [null,null] 不得毒化读链。

    这条是 #342 的**构造式**来源：ConditionSpace.from_json({"time_window": null})
    会把 None 规范化成 (None, None)，to_json 落成 [null, null]。
    """
    cs_json = ConditionSpace.from_json(json.dumps(
        {"observation_position": "外部观测位", "observation_tool": "文本语义分析",
         "time_window": None, "existence_constraint": "（未声明）"})).to_json()
    assert json.loads(cs_json)["time_window"] == [None, None], cs_json
    p = from_legacy(["user", "session:s1"], _TS, cs_json)
    assert p.time_window == (_TS, _TS + 3600.0), p.time_window   # 回落到 created_at


def test_342_anomalous_tags_do_not_crash():
    """tags 列是数字 / null / 字符串标量 / 对象时不得抛，且落 unknown。"""
    assert from_legacy("5").source == "unknown"          # 旧：TypeError
    assert from_legacy(5).source == "unknown"            # 旧：TypeError
    assert from_legacy("null").source == "unknown"
    assert from_legacy('{"a": 1}').source == "unknown"
    assert from_legacy("").source == "unknown"
    # JSON 字符串标量按**单个标签**处理（旧：被拆成 ['d','s','h']）
    assert from_legacy('"dsh"').observation_tool == "dsh"


def test_342_anomalous_condition_space_does_not_crash():
    """condition_space 是 JSON 数组 / 标量时不得抛，视同未声明。"""
    assert from_legacy([], None, "[1,2]").source == "unknown"    # 旧：AttributeError
    assert from_legacy([], None, "5").source == "unknown"
    assert from_legacy([], None, "null").source == "unknown"


def test_342_anomalous_time_window_does_not_crash():
    """非数值 / 长度不对的 time_window 不得抛，回落到 created_at 并记 synthetic。"""
    for tw in (["2026-10-01", "2026-10-02"], [None, None], [1], "abc", 5):
        p = from_legacy([], _TS, {"time_window": tw})
        assert p.time_window == (_TS, _TS + 3600.0), (tw, p.time_window)
        assert "time_window" in p.synthetic, (tw, p.synthetic)


def test_342_is_default_cs_anomalous_does_not_crash():
    """is_default_cs 遇 JSON 数组 / 标量不得抛，一律视同「没有信息量」。"""
    assert is_default_cs("[1]") is True      # 旧：AttributeError
    assert is_default_cs("5") is True
    assert is_default_cs(None) is True
    assert is_default_cs("{}") is True


def test_342_from_json_anomalous_does_not_crash():
    """Provenance.from_json 遇 null / 数字 / 坏 JSON 不得抛，落字段默认值。"""
    assert Provenance.from_json("null").source == "unknown"    # 旧：AttributeError
    assert Provenance.from_json("5").source == "unknown"       # 旧：AttributeError
    assert Provenance.from_json("{").source == "unknown"       # 坏 JSON
    assert Provenance.from_json('{"time_window": [null, null]}').time_window == (0.0, 0.0)
    assert Provenance.from_json('{"time_window": "abc"}').time_window == (0.0, 0.0)
    assert Provenance.from_json("").time_window == (0.0, 0.0)


def test_342_wellformed_input_still_parsed():
    """反向钉住：形状正常的行仍被正确解析（兜底不得吞掉正常路径）。"""
    p = new_provenance("测试台", "bench_fixture", "fixture",
                       verify_status="anchored", verify_methods=["whitebox_code"],
                       verify_ref="pytest x", observed_at=_TS)
    q = Provenance.from_json(p.to_json())
    assert q.source == "fixture" and q.verify_ref == "pytest x"
    assert q.time_window == p.time_window
    assert from_legacy(p.to_tags(), _TS, p.to_condition_space()).verify_ref == "pytest x"
