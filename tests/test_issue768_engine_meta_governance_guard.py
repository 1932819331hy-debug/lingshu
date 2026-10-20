# -*- coding: utf-8 -*-
"""#768 守卫：`engine_meta` 治理键写面的**一次性收口**（四处写入口角色闸）。

缺陷态（修复前 main，issue #768 复现实测）：`LayeredStore.set_meta` / `merge_meta`
对治理键既无 key 白名单、也无角色闸；`engine_meta` 是**库级**表，其中 5 个治理键
在**启动期被回读** ⇒ 任何非 PRIMARY 实例（无需设计者密钥）都能持久化改写身份/信任
快照、验证阈值、注意力偏好、情境层 FIFO 上限与结构写入器开关，且重启后原样生效：

  · `self_model_snapshot`      → 重启后身份/价值观/信任态被整份替换（#479 面）；
  · `verifier_config`          → `_restore_structure_configuration` 无钳制恢复
                                 （`dedup_static` 0.85→0.0，绕过 `#118` 钳制）；
  · `attention_weights`        → 同函数恢复注意力偏好权重；
  · `context_max`              → `_load_context_max()` 重启后 200→1；
  · `structure_writer_schema`  → 重启后 `_structure_writer_active` False→True。

**同一治理面的完整无闸写入口是四处**（逐键补必再漏，故一次性收口）：

  ① `LayeredStore.set_meta` —— `INSERT OR REPLACE INTO engine_meta (key, value)`；
  ② `LayeredStore.merge_meta` —— 同一句 `INSERT OR REPLACE`（读改写单点）；
  ③ `SpacetimeEngine.set_context_cap` —— 公共门面，经 ① 落库，且**先改内存值**；
  ④ `SpacetimeMemoryEngine.adjust_attention_weight` —— **自开一句**
     `INSERT OR REPLACE INTO engine_meta(key,value)`，不经 ①/② ⇒ 只堵 ①② 拦不住它。

修法（落点 `lingshu/core/core.py`）：
  · 治理键集合**上提到 `LayeredStore.ENGINE_META_GUARDED_KEYS`**（单一真源），
    `SpacetimeMemoryEngine.ENGINE_META_GUARDED_KEYS` 改为**别名引用**；
  · 新增 `LayeredStore._assert_meta_key_writable(key)`：`key in 治理键集合` 且
    `role != Role.PRIMARY` 且库**跨进程持久** ⇒ `PermissionError`；
  · 四处写入口在**写动作之前**调用该闸（③ 在改内存值之前、④ 在 INSERT 之前）；
  · **非治理键照旧放行**（`_last_decay_at` / `_schema_version`，红线：不一刀切）；
  · **按库归属分档**（收窄，防打断既有合法用法）：`:memory:` 库由实例独占、不跨
    进程 ⇒ 危害链「SUB 改写 → 重启后 PRIMARY 回读」不成立，故放行。依据：
    `tests/test_issue259_context_fifo_insertion_order_guard.py` 的 B 组即以
    `Role.SUB` + `:memory:` 调 `set_context_cap(5)`（配置**自有**库的上限）。

判据选择：**角色闸**，不用设计者密钥闸——`set_context_cap` 是常规公开 API
（`tests/test_issue246_*` / `_259_*` / `_493_*` / `_655_*` 都以默认 PRIMARY 引擎调
它），改成要密钥会打断既有正常用法；而「谁能写共享/治理面」的仓内单一真源本就是
`Role`（`SHARED_LAYERS` 写闸 / `add_node` / `_assert_edge_role` 同口径），
`StructureWriter.__init__` 已是同款先例。

断言组（把四处闸回退到修复前形态 ⇒ G1~G5 必红；还原 ⇒ 全绿、`git diff` 为空）：

  G1 SUB 逐键直写 5 个治理键 ⇒ 全 `PermissionError`，且关库重开 PRIMARY 回读
     **5 键值逐位不变**（库内值不动）。
  G2 SUB 走 `merge_meta` 写同一权威键 ⇒ `PermissionError`，且 `value_fn` **未被
     调用**（读改写不得在拒路径上先跑取值回调）。
  G3 SUB 调 `set_context_cap(3)` ⇒ `PermissionError`，且**内存值也未变**（防
     「内存已改、盘面未写」的静默分叉，与 `#191` 同款病）。
  G4 SUB 调 `adjust_attention_weight(...)` ⇒ `PermissionError`，且内存权重与
     `engine_meta` 值**都不动**（该写点自开 INSERT，不经 `set_meta`）。
  G5 阴性对照（防过修）：SUB 写**非治理键**（`_last_decay_at`）⇒ **成功**落库。
  G6 阳性对照（防过修）：PRIMARY 走 G1~G4 的**四条**路径 ⇒ 读数**修前修后逐位
     相同**（硬编码期望值）。
  G7 单一真源恒等：`SpacetimeMemoryEngine.ENGINE_META_GUARDED_KEYS`
     **就是**（`is`）`LayeredStore.ENGINE_META_GUARDED_KEYS`，且键集逐字为那 5 个。
  G8 `import_all` 的 D-007 行为**不变**：无密钥导入含治理快照的备份 ⇒
     `PermissionError` ＋ 零落库。
  G9 库归属分档：SUB 在 `:memory:` 自有库上写治理键**照旧放行**（收窄面钉死，
     防日后被改成一刀切而打断 `#259` 的既有用法）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue768_engine_meta_governance_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace

import pytest

from lingshu.core.core import LayeredStore, Role, SpacetimeMemoryEngine

#: 5 个治理键（逐字，与本件守卫的「逐键一例」面一致）。
GOVERNED = ("self_model_snapshot", "verifier_config", "attention_weights",
            "context_max", "structure_writer_schema")

#: 修复前的基线载荷（PRIMARY 写入；SUB 试图改写它们）。
BASELINE = {
    "self_model_snapshot": json.dumps({"identity": "基线身份", "values": ["基线价值"]},
                                      ensure_ascii=False),
    "verifier_config": json.dumps({"dedup_static": 0.85, "deviation_threshold": 0.3},
                                  ensure_ascii=False),
    "attention_weights": json.dumps({"recall": 0.5}, ensure_ascii=False),
    "context_max": "200",
    "structure_writer_schema": "1",
}


def _engine(path, role=None):
    return SpacetimeMemoryEngine(path, **({"role": role} if role else {}))


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "gov768.db")


def _seed_primary(db):
    """PRIMARY 建库并写入 5 个治理键的基线值。"""
    e = _engine(db)
    for key in GOVERNED:
        e.store.set_meta(key, BASELINE[key])
    e.close()


def _meta_all(db):
    e = _engine(db)
    try:
        return {k: e.store.get_meta(k).get(k) for k in GOVERNED}
    finally:
        e.close()


# ---------------------------------------------------------------- G1 逐键直写

@pytest.mark.parametrize("key", GOVERNED)
def test_g1_sub_direct_write_of_each_governed_key_is_refused(db, key):
    """G1：SUB 逐键直写治理键 ⇒ `PermissionError`，且库内值逐位不变。"""
    _seed_primary(db)
    sub = _engine(db, role=Role.SUB)
    try:
        with pytest.raises(PermissionError) as ei:
            sub.store.set_meta(key, "forged")
        msg = str(ei.value)
        assert "role=sub" in msg and key in msg, (
            "#768 拒绝文案应含角色与键名：%s" % msg)
        assert "治理键由父节点主控" in msg, "#768 拒绝文案应点明治理面归属：%s" % msg
    finally:
        sub.close()
    after = _meta_all(db)
    assert after[key] == BASELINE[key], (
        "#768：SUB 写 %s 后库内值被改动（关库重开 PRIMARY 回读）—— 实得 %r"
        % (key, after[key]))
    assert after == BASELINE, "#768：5 键中至少一键被 SUB 改动：%r" % (after,)


# ---------------------------------------------------------------- G2 merge_meta

def test_g2_sub_merge_meta_is_refused_and_value_fn_not_called(db):
    """G2：SUB 走 `merge_meta` ⇒ 拒，且取值回调**未被调用**。"""
    _seed_primary(db)
    calls = []

    def _value_fn(old):
        calls.append(old)
        return "forged"

    sub = _engine(db, role=Role.SUB)
    try:
        with pytest.raises(PermissionError):
            sub.store.merge_meta("self_model_snapshot", _value_fn)
    finally:
        sub.close()
    assert calls == [], (
        "#768：治理键被拒的路径上仍跑了 `value_fn`（读改写回调有副作用风险）：%r" % calls)
    after = _meta_all(db)
    assert after["self_model_snapshot"] == BASELINE["self_model_snapshot"], (
        "#768：merge_meta 被拒后库内快照值仍被改动：%r" % after["self_model_snapshot"])


# ---------------------------------------------------------------- G3 set_context_cap

def test_g3_sub_set_context_cap_refused_memory_unchanged(db):
    """G3：SUB 调 `set_context_cap(3)` ⇒ 拒，且**内存值也未变**（防静默分叉）。"""
    _seed_primary(db)
    sub = _engine(db, role=Role.SUB)
    try:
        before_mem = sub._context_max
        with pytest.raises(PermissionError):
            sub.set_context_cap(3)
        assert sub._context_max == before_mem == 200, (
            "#768：`set_context_cap` 被拒后内存值已改（静默分叉，与 #191 同款病）"
            "—— %s → %s" % (before_mem, sub._context_max))
    finally:
        sub.close()
    after = _meta_all(db)
    assert after["context_max"] == "200", (
        "#768：SUB 调 `set_context_cap` 后盘面上限被改动：%r" % after["context_max"])


# ---------------------------------------------------------------- G4 adjust_attention_weight

def test_g4_sub_adjust_attention_weight_refused_nothing_changed(db):
    """G4：SUB 调 `adjust_attention_weight` ⇒ 拒；内存权重与盘面值都不动。

    该写点**自开一句** `INSERT OR REPLACE INTO engine_meta`，不经 `set_meta`
    ⇒ 只堵 `set_meta`/`merge_meta` 拦不住它（逐键补必再漏的实例）。
    """
    _seed_primary(db)
    sub = _engine(db, role=Role.SUB)
    try:
        weights = {"recall": 0.5}
        sub._attention_policy = SimpleNamespace(
            preference_weights=weights,
            set_weight=lambda key, value, source, reason, role: weights.update(
                {key: value}))
        with pytest.raises(PermissionError):
            sub.adjust_attention_weight("recall", 0.99, "operator", "probe")
        assert weights == {"recall": 0.5}, (
            "#768：`adjust_attention_weight` 被拒后内存权重已被改写：%r" % weights)
    finally:
        sub.close()
    after = _meta_all(db)
    assert after["attention_weights"] == BASELINE["attention_weights"], (
        "#768：SUB 调 `adjust_attention_weight` 后盘面权重被改写：%r"
        % after["attention_weights"])


# ---------------------------------------------------------------- G5 阴性对照

def test_g5_negative_control_sub_may_write_nongovernance_key(db):
    """G5（防过修）：SUB 写**非治理键**照旧成功——红线：不一刀切。"""
    _seed_primary(db)
    sub = _engine(db, role=Role.SUB)
    try:
        sub.store.set_meta("_last_decay_at", "12345")
        assert sub.store.get_meta("_last_decay_at").get("_last_decay_at") == "12345", (
            "#768 过修：非治理键 `_last_decay_at` 被闸拦住（本闸只应作用于治理键）")
        # 非治理键的 merge 路径同样放行
        old = sub.store.merge_meta("_last_decay_at", lambda o: str(int(o) + 1))
        assert old == "12345" and sub.store.get_meta(
            "_last_decay_at")["_last_decay_at"] == "12346", (
            "#768 过修：非治理键 `merge_meta` 被拦（old=%r）" % old)
    finally:
        sub.close()


# ---------------------------------------------------------------- G6 阳性对照

def test_g6_positive_control_primary_four_paths_bit_identical(db):
    """G6（防过修）：PRIMARY 走四条路径 ⇒ 读数与修复前**逐位相同**（硬编码）。"""
    e = _engine(db)
    try:
        # ① set_meta
        e.store.set_meta("verifier_config", '{"dedup_static": 0.6}')
        assert e.store.get_meta("verifier_config") == {
            "verifier_config": '{"dedup_static": 0.6}'}, "G6①：PRIMARY set_meta 读数变了"
        # ② merge_meta（首次：旧值为 None）
        old = e.store.merge_meta("attention_weights", lambda o: '{"recall": 0.4}')
        assert old is None, "G6②：merge_meta 首次返回旧值应为 None，实得 %r" % (old,)
        assert e.store.get_meta("attention_weights") == {
            "attention_weights": '{"recall": 0.4}'}, "G6②：PRIMARY merge_meta 读数变了"
        # ③ set_context_cap
        e.set_context_cap(7)
        assert e._context_max == 7, "G6③：内存上限读数变了：%r" % (e._context_max,)
        assert e.store.get_meta("context_max") == {
            "context_max": "7"}, "G6③：盘面上限读数变了"
        # ④ adjust_attention_weight
        weights = {"recall": 0.5}
        e._attention_policy = SimpleNamespace(
            preference_weights=weights,
            set_weight=lambda key, value, source, reason, role: weights.update(
                {key: value}))
        assert e.adjust_attention_weight("recall", 0.7, "operator", "probe") is True, (
            "G6④：PRIMARY adjust_attention_weight 返回值变了")
        assert e.store.get_meta("attention_weights") == {
            "attention_weights": '{"recall": 0.7}'}, "G6④：PRIMARY 权重读数变了"
    finally:
        e.close()


# ---------------------------------------------------------------- G7 单一真源

def test_g7_governed_keys_single_source_identity():
    """G7：治理键集合只有一份——引擎侧是 store 侧的**别名**（`is` 恒等）。"""
    assert (SpacetimeMemoryEngine.ENGINE_META_GUARDED_KEYS
            is LayeredStore.ENGINE_META_GUARDED_KEYS), (
        "#768 单一真源：`SpacetimeMemoryEngine.ENGINE_META_GUARDED_KEYS` 不是 "
        "`LayeredStore.ENGINE_META_GUARDED_KEYS` 的别名（各写一份必漂移）")
    assert SpacetimeMemoryEngine.ENGINE_META_GUARDED_KEYS == set(GOVERNED), (
        "#768 治理键集逐字不符：%r"
        % sorted(SpacetimeMemoryEngine.ENGINE_META_GUARDED_KEYS))
    # 与 #671 的分类互斥面不回退
    assert not (LayeredStore.ENGINE_META_GUARDED_KEYS
                & SpacetimeMemoryEngine.ENGINE_META_NONGOVERNANCE_KEYS), (
        "#768：治理键与非治理白名单重叠")
    assert SpacetimeMemoryEngine.SELF_MODEL_META_KEY in (
        LayeredStore.ENGINE_META_GUARDED_KEYS), (
        "#768：`SELF_MODEL_META_KEY` 未在治理键集内")


# ---------------------------------------------------------------- G8 D-007 不回退

def test_g8_import_all_d007_unchanged(monkeypatch, tmp_path):
    """G8：`import_all` 的 D-007 密钥闸行为**不变**（无密钥导入治理快照 ⇒ 拒＋零落库）。"""
    monkeypatch.delenv("AEIS_DESIGNER_KEY", raising=False)
    monkeypatch.delenv("LINGSHU_DESIGNER_KEY", raising=False)
    backup = tmp_path / "meta-only.json"
    backup.write_text(json.dumps({"engine_meta": [
        {"key": "self_model_snapshot",
         "value": json.dumps({"identity": "攻击者人格"}, ensure_ascii=False)}]},
        ensure_ascii=False), encoding="utf-8")
    e = _engine(str(tmp_path / "import768.db"))
    try:
        with pytest.raises(PermissionError) as ei:
            e.import_all(str(backup))
        assert "engine_meta" in str(ei.value) and "设计者认证失败" in str(ei.value), (
            "#768：D-007 拒绝文案变了：%s" % ei.value)
        assert e.store.get_meta("self_model_snapshot").get(
            "self_model_snapshot") is None, "#768：无密钥导入治理快照仍落库（零落库不成立）"
    finally:
        e.close()


# ---------------------------------------------------------------- G9 库归属分档

def test_g9_ownership_split_sub_own_memory_db_still_writable():
    """G9：SUB 在**自有 `:memory:` 库**上写治理键照旧放行（按库归属分档）。

    收窄依据：`:memory:` 库由实例独占、不跨进程持久 ⇒ `#768` 的危害链
    「SUB 改写 → **重启后** PRIMARY 回读被篡改值」不成立；仓内
    `tests/test_issue259_context_fifo_insertion_order_guard.py` 的 B 组即以此形态
    配置自有库的 FIFO 上限（一刀切会把它打成红）。
    """
    e = _engine(":memory:", role=Role.SUB)
    try:
        e.set_context_cap(5)
        assert e._context_max == 5, "#768 收窄面：SUB 自有内存库上限被拦：%r" % (e._context_max,)
        assert e.store.get_meta("context_max") == {"context_max": "5"}, (
            "#768 收窄面：SUB 自有内存库盘面上限未落：%r" % (e.store.get_meta("context_max"),))
        e.store.set_meta("_last_decay_at", "1")
        assert e.store.get_meta("_last_decay_at") == {"_last_decay_at": "1"}
    finally:
        e.close()
