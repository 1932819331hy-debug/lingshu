# -*- coding: utf-8 -*-
"""test_issue463_shared_sync_identity_guard · 共享层同步判同面守卫（lingshu #463）
============================================================================

缺陷（#463 · 成立）：`SpacetimeMemoryEngine.apply_shared_sync` 的**判同**只看接收侧
既有节点的 `layer` + `content` 两列（`SELECT layer, content FROM nodes WHERE id=?`），
而 `prepare_shared_sync` 按 `SHARED_SYNC_FIELDS`（13 个字段）导出全字段 ⇒ 源端改了
**内容以外**的字段（`tags`/`confidence`/`importance`/`state_attributes`…）后重放，
接收端判为「完全一致」入 `identical`、**不落库**，却回报成功 ⇒
**元数据变化被吞掉 + 假 identical + 两实例静默分叉**。

修法（本件）：判同**按声明字段面**（唯一真源 = `SHARED_SYNC_FIELDS`）逐字段比对，
逐字段全同才 `identical`；任一字段不同 ⇒ `conflicts`（带差异**字段名** `fields` 与
两侧值摘要 `diff`）；`overwrite=True` 才落库（无损重建）。取数为**一条** `SELECT`
（列 = 声明字段面），非 `SELECT *`、不随条目数变 N+1。

断言组（回退即红）：
  A 元数据变化必须报冲突（不得报 identical）——逐字段参数化：
    `tags` / `confidence` / `importance` / `state_attributes`。
  B 默认不覆盖：报冲突但**接收端一字不动**（绝不静默覆盖）。
  C 无损重建：`overwrite=True` 后接收端与源端**逐字段一致**。
  D 幂等：两侧全同 ⇒ 仍 `identical`、零冲突（不把正常幂等误报成冲突）。
  E 冲突可解释：每条带 `fields`（差异字段名）与 `diff`（两侧值摘要）。
  F 旧口径判别力（**定点变异**）：把判同退回「只比 layer+content」⇒ 组 A 的断言
    必须**转红**。本组同时给两路证据：
      ① 直接断言旧口径对"仅元数据变化"判为**无差异**（即它会判错）；
      ② `monkeypatch` 把 `_shared_sync_field_diffs` 换回旧口径，跑组 A 的**同一条
         断言体**，断言它此时**抛 AssertionError**（≠ 恒真）。
  G **数值等价表示不得假冲突**（#615 F4）：
      `{"n":0}` vs `{"n":0.0}`（含 `1` vs `1.0`、`100` vs `1e2`、嵌套、`-0.0`）⇒ 必须
      `identical`；反向 `0` vs `1`、`1.0` vs `1.0000001`、`2^53+1` vs `2.0^53`、
      `"0"` vs `0`、`True` vs `1`、`null` vs `0`、`[]` vs `{}` ⇒ **必须仍报冲突**
      （防止把"数值比较"做宽成"都不比"）。
      定点变异：把数值规范化退回"按文本比"⇒ 第 1 条守卫必须**转红**。

运行（仓根）：python -X utf8 -m pytest tests/test_issue463_shared_sync_identity_guard.py -q
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.core.core import (                                    # noqa: E402
    SpacetimeMemoryEngine, Role,
)

#: 逐字段参数化的变异器：每个只改**一个**声明字段（content/layer 一律不动）
FIELD_MUTATORS = {
    "tags": lambda n: setattr(n, "tags", list(n.tags) + ["refuted"]),
    "confidence": lambda n: setattr(n, "confidence", 0.1),
    "importance": lambda n: setattr(n, "importance", 0.2),
    "state_attributes": lambda n: setattr(n, "state_attributes",
                                          {"status": "refuted"}),
}


# ─────────────────────────────────────────────────────────── 夹具
def _engines(root, n=2):
    """n 个**独立库文件**的 PRIMARY 实例（#463 复现口径：两个 PRIMARY + 独立 DB）。"""
    root = str(root)
    os.makedirs(root, exist_ok=True)      # 库文件所在目录必须先存在（sqlite 不自建目录）
    out = []
    for i in range(n):
        out.append(SpacetimeMemoryEngine(os.path.join(root, "e%d.db" % i),
                                         identity="guard463-%d" % i,
                                         role=Role.PRIMARY))
    return out


def _seed(src):
    """源端写一条结构层节点（tags=['structure'], confidence=1.0）。"""
    return src.add_structure_node("塔身结构一条")


def _snap(engine, nid):
    n = engine.store.get_node(nid)
    if n is None:
        return None
    return {"tags": list(n.tags), "confidence": n.confidence,
            "importance": n.importance, "state_attributes": dict(n.state_attributes),
            "content": n.content, "layer": n.layer.value}


def _payload_of(src, nid):
    p = src.prepare_shared_sync()
    return p, [it for it in p["sync_payload"] if it["id"] == nid][0]


# ─────────────────────────────────────────────────────────── 旧口径（仅供判别力对照）
def _legacy_two_col_same(existing_row, incoming_item):
    """**改前的判据**：只有 `layer` 与 `content` 相同就算「完全一致」。

    这是 issue #463 的病灶本体，写在这里作为**对照面**——组 F 用它证明
    「组 A 的断言在旧口径下会失败」，从而证明本守卫不是恒真的假绿。
    """
    return (existing_row.get("layer") == incoming_item.get("layer")
            and existing_row.get("content") == incoming_item.get("content"))


def _legacy_diffs(self, existing, incoming):
    """旧口径的 `_shared_sync_field_diffs`（用于 monkeypatch 定点变异）。"""
    if _legacy_two_col_same(existing, incoming):
        return []
    return [{"field": "content", "existing": str(existing.get("content"))[:80],
             "incoming": str(incoming.get("content"))[:80]}]


# ─────────────────────────────────────────────────────────── 断言体（组 A 的可复用场景）
def _assert_metadata_only_change_is_conflict(tmp_path):
    """**元数据变化必须报冲突**的完整场景体。

    组 A 与组 F 跑的是**同一个函数**：组 A 在真实现下必须通过，
    组 F 在旧口径下必须失败（⇒ 断言有判别力）。
    """
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        nid = node.id
        _p1, _ = _payload_of(src, nid)
        r0 = rcv.apply_shared_sync(_p1)
        assert r0["applied"] == [nid], "首次 apply 应为 applied"

        before = _snap(rcv, nid)

        # 源端只改「内容以外」的字段
        got = src.store.get_node(nid)
        got.tags = list(got.tags) + ["refuted"]
        got.confidence = 0.1
        src.store.add_node(got)

        _p2, item2 = _payload_of(src, nid)
        # 前提：content 与 layer **没有**动（所以旧口径必然判成 identical）
        assert item2["content"] == node.content and item2["layer"] == "structure"

        r = rcv.apply_shared_sync(_p2)
        # ★ 核心断言（旧口径在这里必红）
        assert r["identical"] == [], \
            "元数据已变却报 identical ⇒ 假成功（#463）"
        assert [c["id"] for c in r["conflicts"]] == [nid], \
            "元数据变化必须记入 conflicts"
        # 默认不覆盖 ⇒ 接收端一字不动
        assert _snap(rcv, nid) == before, "overwrite=False 不得改动接收端"
    finally:
        for e in (src, rcv):
            e.close()


# ═══════════════════════════════════════════════ A 元数据变化 ⇒ 冲突（逐字段）
@pytest.mark.parametrize("field", sorted(FIELD_MUTATORS))
def test_a_declared_field_change_is_conflict(tmp_path, field):
    """声明字段任一变化 ⇒ `conflicts` 非空、指出该字段名，且**不得**报 identical。"""
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        nid = node.id
        p1, _ = _payload_of(src, nid)
        assert rcv.apply_shared_sync(p1)["applied"] == [nid]
        before = _snap(rcv, nid)

        got = src.store.get_node(nid)
        FIELD_MUTATORS[field](got)
        src.store.add_node(got)

        p2, item2 = _payload_of(src, nid)
        assert item2["content"] == node.content, "本用例只改内容以外的字段"
        assert item2["layer"] == "structure"

        r = rcv.apply_shared_sync(p2)
        assert r["identical"] == [], "%s 变化被吞掉并报成 identical" % field
        assert [c["id"] for c in r["conflicts"]] == [nid]
        assert field in r["conflicts"][0]["fields"], \
            "冲突必须点名差异字段；实得 %r" % (r["conflicts"][0].get("fields"),)
        # C：默认不覆盖
        assert _snap(rcv, nid) == before
    finally:
        for e in (src, rcv):
            e.close()


def test_a_conflict_carries_field_names_and_value_summaries(tmp_path):
    """E 冲突可解释：每条带 `fields`（字段名）与 `diff`（两侧值摘要）。"""
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        nid = node.id
        p1, _ = _payload_of(src, nid)
        rcv.apply_shared_sync(p1)
        got = src.store.get_node(nid)
        got.tags = list(got.tags) + ["refuted"]
        got.confidence = 0.1
        src.store.add_node(got)
        p2, _ = _payload_of(src, nid)
        r = rcv.apply_shared_sync(p2)
        c = [x for x in r["conflicts"] if x["id"] == nid][0]
        assert set(c["fields"]) == {"tags", "confidence"}, c["fields"]
        detail = {d["field"]: d for d in c["diff"]}
        assert set(detail) == {"tags", "confidence"}
        assert "refuted" in detail["tags"]["incoming"]
        assert "refuted" not in detail["tags"]["existing"]
        assert "1.0" in detail["confidence"]["existing"]
        assert "0.1" in detail["confidence"]["incoming"]
        # 冲突记录必须仍带既有的层键（对外形态未变）
        assert c["incoming_layer"] == "structure" and c["existing_layer"] == "structure"
    finally:
        for e in (src, rcv):
            e.close()


# ═══════════════════════════════════════════════ C 无损重建（overwrite=True）
def test_c_overwrite_rebuilds_losslessly_field_by_field(tmp_path):
    """`overwrite=True` ⇒ 接收端与源端在**声明字段面**上逐字段一致（无损重建）。"""
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        nid = node.id
        p1, _ = _payload_of(src, nid)
        rcv.apply_shared_sync(p1)

        got = src.store.get_node(nid)
        got.tags = list(got.tags) + ["refuted"]
        got.confidence = 0.1
        got.state_attributes = {"status": "refuted"}
        src.store.add_node(got)

        p2, item2 = _payload_of(src, nid)
        r = rcv.apply_shared_sync(p2, overwrite=True)
        assert r["applied"] == [nid]
        assert [c["id"] for c in r["conflicts"]] == [nid], \
            "覆盖后仍须留在 conflicts 里（绝不静默覆盖）"
        assert r["overwritten"] is True

        after = _snap(rcv, nid)
        assert after["tags"] == item2["tags"], (after["tags"], item2["tags"])
        assert after["confidence"] == pytest.approx(item2["confidence"], abs=0.0)
        assert after["importance"] == pytest.approx(item2["importance"], abs=0.0)
        assert after["state_attributes"] == item2["state_attributes"]
        assert after["content"] == item2["content"]
        assert after["layer"] == item2["layer"]

        # 逐字段判同面必须一致（用生产判据自身核对，避免自造判据）
        existing_row = rcv._shared_sync_existing_row(nid)
        assert rcv._shared_sync_field_diffs(existing_row, item2) == [], \
            "覆盖后仍报差异 ⇒ 无损重建不成立"
    finally:
        for e in (src, rcv):
            e.close()


# ═══════════════════════════════════════════════ D 幂等（不误报冲突）
def test_d_identical_replay_stays_identical(tmp_path):
    """两侧全同 ⇒ 仍 `identical`、零冲突（正常幂等不得被报成冲突）。"""
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        nid = node.id
        p1, _ = _payload_of(src, nid)
        assert rcv.apply_shared_sync(p1)["applied"] == [nid]
        r = rcv.apply_shared_sync(p1)
        assert r["applied"] == []
        assert r["identical"] == [nid]
        assert r["conflicts"] == []
    finally:
        for e in (src, rcv):
            e.close()


def test_d2_identical_then_metadata_and_back_is_stable(tmp_path):
    """幂等与判据可逆：改元数据 ⇒ 冲突；覆盖 ⇒ 再重放 ⇒ identical。"""
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        nid = node.id
        p1, _ = _payload_of(src, nid)
        rcv.apply_shared_sync(p1)
        got = src.store.get_node(nid)
        got.tags = list(got.tags) + ["refuted"]
        src.store.add_node(got)
        p2, _ = _payload_of(src, nid)
        assert rcv.apply_shared_sync(p2)["identical"] == []
        assert rcv.apply_shared_sync(p2, overwrite=True)["applied"] == [nid]
        r = rcv.apply_shared_sync(p2)
        assert r["identical"] == [nid] and r["conflicts"] == []
    finally:
        for e in (src, rcv):
            e.close()


def test_d3_local_layer_still_rejected(tmp_path):
    """不回归：本地层条目仍入 `rejected`、零落库（#165 的越界面不动）。"""
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        p1, item = _payload_of(src, node.id)
        forged = dict(item)
        forged["id"] = "forged_knowledge"
        forged["layer"] = "knowledge"
        res = rcv.apply_shared_sync({"sync_payload": p1["sync_payload"] + [forged]})
        assert "forged_knowledge" in [r["id"] for r in res["rejected"]]
        assert rcv.store.get_node("forged_knowledge") is None
    finally:
        for e in (src, rcv):
            e.close()


# ═══════════════════════════════════════════════ F 定点变异：回退即红
def test_f0_legacy_two_column_criterion_misjudges_metadata_change(tmp_path):
    """F① 旧口径本体确实**判错**：仅元数据变化时，它判「无差异」。"""
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        nid = node.id
        p1, _ = _payload_of(src, nid)
        rcv.apply_shared_sync(p1)
        got = src.store.get_node(nid)
        got.tags = list(got.tags) + ["refuted"]
        got.confidence = 0.1
        src.store.add_node(got)
        _p2, item2 = _payload_of(src, nid)

        existing_row = rcv._shared_sync_existing_row(nid)
        assert _legacy_two_col_same(existing_row, item2) is True, \
            "旧口径应把它判成「完全一致」（这正是 #463 的病灶）"
        # 而逐字段真判必须报差异
        diffs = rcv._shared_sync_field_diffs(existing_row, item2)
        assert {d["field"] for d in diffs} == {"tags", "confidence"}, diffs
    finally:
        for e in (src, rcv):
            e.close()


def test_f1_mutation_back_to_two_columns_turns_guard_red(tmp_path, monkeypatch):
    """F② 定点变异：把判同退回「只比 layer+content」⇒ 组 A 的断言**必须转红**。

    跑的是**与组 A 完全相同**的场景体 `_assert_metadata_only_change_is_conflict`；
    真实现下它通过（组 A 已证），变异后必须抛 `AssertionError` ——
    这条证明组 A 的判据有判别力、不是恒真的假绿。
    """
    # 真实现下：同一断言体通过
    _assert_metadata_only_change_is_conflict(tmp_path / "real")

    # 定点变异：判同口径退回两列
    monkeypatch.setattr(SpacetimeMemoryEngine, "_shared_sync_field_diffs",
                        _legacy_diffs, raising=True)
    with pytest.raises(AssertionError):
        _assert_metadata_only_change_is_conflict(tmp_path / "mutated")


def test_f2_mutation_makes_identity_claim_wrong(tmp_path, monkeypatch):
    """F② 补强：变异后**实际读数**变成 `identical` 且 `conflicts` 为空（假成功）。"""
    monkeypatch.setattr(SpacetimeMemoryEngine, "_shared_sync_field_diffs",
                        _legacy_diffs, raising=True)
    src, rcv = _engines(str(tmp_path))
    try:
        node = _seed(src)
        nid = node.id
        p1, _ = _payload_of(src, nid)
        rcv.apply_shared_sync(p1)
        got = src.store.get_node(nid)
        got.tags = list(got.tags) + ["refuted"]
        got.confidence = 0.1
        src.store.add_node(got)
        p2, _ = _payload_of(src, nid)
        r = rcv.apply_shared_sync(p2)
        assert r["identical"] == [nid] and r["conflicts"] == [], \
            "变异后应复现 #463 的假 identical 读数"
        # 且接收端与源端已分叉（静默分叉的实测形态）
        assert _snap(rcv, nid)["confidence"] != src.store.get_node(nid).confidence
    finally:
        for e in (src, rcv):
            e.close()


# ═══════════════════════════════════════════════ G 数值等价 ⇒ 不得假冲突（#615 F4）
#
# 缺陷（观察级 · 属实报）：判同的 JSON 列曾按 `json.dumps(sort_keys=True)` 的**文本**比，
# 而 Python 侧 `int` 与 `float` 落成文本会分叉（`{"n": 0}` vs `{"n": 0.0}`）⇒
# **数值相等的两条记录被判成差异**（假冲突）。方向虽保守（宁可多报），但属误报。
# 修法：规范文本按**数值**归一（整值浮点折成同值整数十进制），并给非数值打类型标记。

#: 数值等价表示 —— **必须**判 `identical`（假冲突面）
JSON_NUM_EQUIVALENT = [
    ("int0_vs_float0", {"n": 0}, {"n": 0.0}),
    ("int1_vs_float1", {"n": 1}, {"n": 1.0}),
    ("int_vs_exp", {"n": 100}, {"n": 1e2}),
    ("neg_zero_vs_zero", {"n": -0.0}, {"n": 0}),
    ("nested_mixed", {"a": [1, 2.0], "b": {"c": 3}}, {"a": [1.0, 2], "b": {"c": 3.0}}),
]

#: 数值**不等** / 类型不同 —— **必须**仍报冲突（防止"数值比较"做宽成"都不比"）
JSON_NUM_DISTINCT = [
    ("0_vs_1", {"n": 0}, {"n": 1}),
    ("1_vs_2", {"n": 1}, {"n": 2.0}),
    ("close_floats", {"n": 1.0}, {"n": 1.0000001}),
    ("int_vs_lossy_float", {"n": 9007199254740993}, {"n": 9007199254740992.0}),
    ("str_vs_num", {"n": "0"}, {"n": 0}),
    ("bool_vs_num", {"n": True}, {"n": 1}),
    ("null_vs_zero", {"n": None}, {"n": 0}),
    ("list_vs_dict", {"n": []}, {"n": {}}),
]


def _apply_json_attrs(root, src_attrs, rcv_attrs):
    """源端 / 接收端的 `state_attributes` 取不同表示后重放；返回 `(result, nid)`。

    只动这一个 JSON 列 ⇒ 差异（若有）必然落在 `state_attributes` 上。
    """
    src, rcv = _engines(str(root))
    try:
        node = _seed(src)
        nid = node.id
        rcv.apply_shared_sync(src.prepare_shared_sync())
        a = src.store.get_node(nid)
        a.state_attributes = dict(src_attrs)
        src.store.add_node(a)
        b = rcv.store.get_node(nid)
        b.state_attributes = dict(rcv_attrs)
        rcv.store.add_node(b)
        return rcv.apply_shared_sync(src.prepare_shared_sync()), nid
    finally:
        for e in (src, rcv):
            e.close()


def _legacy_text_canon(cls, value, empty="{}"):
    """**改前的文本口径**（`json.dumps(sort_keys=True)`）—— 用于 G3 定点变异。"""
    if isinstance(value, str):
        try:
            value = json.loads(value or empty)
        except (TypeError, ValueError):
            return None
    elif value is None:
        try:
            value = json.loads(empty)
        except (TypeError, ValueError):
            return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None


def _assert_numeric_equivalence_is_identical(root):
    """**数值等价 ⇒ identical** 的断言体（组 G1 与组 G3 变异跑的是同一个函数）。"""
    res, nid = _apply_json_attrs(root, {"n": 0}, {"n": 0.0})
    assert res["conflicts"] == [], "数值等价表示被误报为冲突（#615 F4）"
    assert res["identical"] == [nid]


def test_g0_canon_number_equivalence_and_type_tags():
    """G0 口径直测：数值按数值等价；非数值（串/布尔/null/容器）仍分型。"""
    K = SpacetimeMemoryEngine._shared_canon_json
    assert K({"n": 0}) == K({"n": 0.0})
    assert K({"n": 1}) == K({"n": 1.0})
    assert K({"n": 100}) == K({"n": 1e2})
    assert K({"n": -0.0}) == K({"n": 0})
    assert K({"a": [1, 2.0]}) == K({"a": [1.0, 2]})
    # 数值不等：绝不并入
    assert K({"n": 0}) != K({"n": 1})
    assert K({"n": 1.0}) != K({"n": 1.0000001})
    assert K({"n": 9007199254740993}) != K({"n": 9007199254740992.0})
    # 非数值面语义不变（类型标记）
    assert K({"n": "0"}) != K({"n": 0})
    assert K({"n": True}) != K({"n": 1})
    assert K({"n": None}) != K({"n": 0})
    assert K({"n": []}) != K({"n": {}})
    # NaN 自等（幂等前提；与 json.dumps 文本口径一致）
    assert K({"n": float("nan")}) == K({"n": float("nan")})


@pytest.mark.parametrize("name,s,r", JSON_NUM_EQUIVALENT,
                         ids=[c[0] for c in JSON_NUM_EQUIVALENT])
def test_g1_numerically_equal_json_is_identical(tmp_path, name, s, r):
    """G1 数值等价表示 ⇒ `identical`、零冲突（**不得**报假冲突）。"""
    res, nid = _apply_json_attrs(tmp_path, s, r)
    assert res["conflicts"] == [], "%s：数值等价却被报冲突" % name
    assert res["identical"] == [nid]


@pytest.mark.parametrize("name,s,r", JSON_NUM_DISTINCT,
                         ids=[c[0] for c in JSON_NUM_DISTINCT])
def test_g2_numerically_distinct_json_still_conflicts(tmp_path, name, s, r):
    """G2 反向守卫：数值不等 / 类型不同 ⇒ **必须**报冲突（防"都不比"）。"""
    res, nid = _apply_json_attrs(tmp_path, s, r)
    assert res["identical"] == [], "%s：真差异被吞成 identical" % name
    assert [c["id"] for c in res["conflicts"]] == [nid]
    assert "state_attributes" in res["conflicts"][0]["fields"]


def test_g3_mutation_text_only_canon_turns_numeric_guard_red(tmp_path, monkeypatch):
    """G3 定点变异：数值规范化退回"按文本比"⇒ G1 的断言**必须转红**。

    跑的是**与组 G1 完全相同**的断言体 `_assert_numeric_equivalence_is_identical`：
    真实现下它通过（G1 已证），变异后必须抛 `AssertionError` ⇒ 判据有判别力。
    """
    _assert_numeric_equivalence_is_identical(tmp_path / "real")
    monkeypatch.setattr(SpacetimeMemoryEngine, "_shared_canon_json",
                        classmethod(_legacy_text_canon), raising=True)
    with pytest.raises(AssertionError):
        _assert_numeric_equivalence_is_identical(tmp_path / "mutated")


def test_g4_mutation_keeps_true_differences_reported(tmp_path, monkeypatch):
    """G4 变异下反向仍成立：退文本口径后 `0` vs `1` 依旧报冲突（不会变"都不比"）。"""
    monkeypatch.setattr(SpacetimeMemoryEngine, "_shared_canon_json",
                        classmethod(_legacy_text_canon), raising=True)
    res, nid = _apply_json_attrs(tmp_path, {"n": 0}, {"n": 1})
    assert res["identical"] == [] and [c["id"] for c in res["conflicts"]] == [nid]
