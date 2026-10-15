# -*- coding: utf-8 -*-
"""#671 守卫：`ENGINE_META_GUARDED_KEYS` 枚举补齐 ＋ 机械闭合腿（防「枚举天然会漏」重演）。

缺陷态（修复前 main，issue #671 复现实测）：
  `#479` 给 `import_all` 加的 engine_meta 密钥闸以**治理键枚举**收窄载荷，但枚举里只有
  `self_model_snapshot` 一个键。而 `engine_meta` 中**启动期被回读**的治理载荷键至少还有
  四个不在闸内 ⇒ 无密钥 `import_all` 一份含这些键的备份即放行、`requires_review=False`、
  零治理审计，重启后原样生效：
    · `verifier_config`  → `_restore_structure_configuration` 无钳制恢复，`dedup_static`
      0.85→0.0（连 `#118` 的 `[0.5,0.95]` 钳制一并绕过），M5 去重闸阈值被改写；
    · `attention_weights` → 同函数恢复注意力偏好权重；
    · `context_max`      → `_load_context_max()` 重启后 200→1，情境层 FIFO 上限被改写；
    · `structure_writer_schema` → 重启后 `_structure_writer_active` False→True。

修法（落点 `lingshu/core/core.py`，**最小枚举补齐**）：
  `ENGINE_META_GUARDED_KEYS` 纳入上述 4 个治理载荷键；`_schema_version` / `_last_decay_at`
  等**非治理**键保持不入闸（`ENGINE_META_NONGOVERNANCE_KEYS`）。为不让「枚举天然会漏」
  重演，另立 `ENGINE_META_STARTUP_KEYS`（源码内全部 engine_meta 读点的键全集）＋守卫 G5
  的**机械闭合腿**：扫描源码列出读点键，断言每个键要么在 `ENGINE_META_GUARDED_KEYS`、
  要么在非治理白名单里；将来新增一个启动回读键而忘了分类 ⇒ 守卫红。

  取舍（如实标注，留设计者裁）：本笔**不**改成 deny-by-default 放行白名单——那是更大的
  契约变更（「哪些键能跨备份存活」），属设计者裁面。

组（回退即红）：
  G1 四个新键各自：无密钥 `import_all` ⇒ `PermissionError` 且**零落库**（meta 不写入）。
  G2 `self_model_snapshot`（#479 原键）仍拒（不回退既有修复）。
  G3 正对照：非治理键 `_schema_version` 无密钥照常导入（灾备恢复通道保住）。
  G4 灾备正向组：持密钥导入 `verifier_config` ⇒ 放行、`requires_review=True`、
     `import_governance_override` 审计行在（防过修）。
  G5 机械闭合腿：源码扫描的 engine_meta 读点键集合 == 已分类全集。
  G6 行为面：无密钥导入 `verifier_config`（`dedup_static=0.0`）被拒后，**重启**新实例读同一
     库，`_dedup_static` 仍为 0.85（越界值未生效）。

判别力（变异自证）：把 `ENGINE_META_GUARDED_KEYS` 清空 ⇒ G1/G2/G4 必红、G3 仍绿；
把四个新键从枚举摘掉（回到单键）⇒ G1 必红、G5 必红；还原 ⇒ 全绿、`git diff` 为空。

运行：python -X utf8 -m pytest tests/test_issue671_engine_meta_guarded_keys_guard.py -q
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time

import pytest

from lingshu.core.core import LayeredStore, SpacetimeMemoryEngine

ENGINE_META_GUARDED_KEYS = SpacetimeMemoryEngine.ENGINE_META_GUARDED_KEYS
ENGINE_META_NONGOVERNANCE_KEYS = SpacetimeMemoryEngine.ENGINE_META_NONGOVERNANCE_KEYS
ENGINE_META_STARTUP_KEYS = SpacetimeMemoryEngine.ENGINE_META_STARTUP_KEYS

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE_SRC = os.path.join(_REPO_ROOT, "lingshu", "core", "core.py")

_NEW_KEYS = ("verifier_config", "attention_weights", "context_max",
             "structure_writer_schema")


def _write_backup(rows):
    path = os.path.join(tempfile.mkdtemp(), "meta-only.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"engine_meta": rows}, f, ensure_ascii=False)
    return path


def _engine(tmp, name="g671.db"):
    return SpacetimeMemoryEngine(os.path.join(tmp, name))


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("AEIS_DESIGNER_KEY", raising=False)
    monkeypatch.delenv("LINGSHU_DESIGNER_KEY", raising=False)


# --------------------------------------------------- G1 四个新键各自：无密钥 ⇒ 拒

def _payload_for(key):
    if key == "verifier_config":
        return {"dedup_static": 0.0, "deviation_threshold": 0.3}
    if key == "attention_weights":
        return {"salience": 0.99}
    if key == "context_max":
        return "1"
    return "1"     # structure_writer_schema


@pytest.mark.parametrize("key", _NEW_KEYS)
def test_g1_new_governance_key_requires_key_zero_write(no_key, key):
    tmp = tempfile.mkdtemp()
    e = _engine(tmp)
    raw = _payload_for(key)
    value = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    with pytest.raises(PermissionError) as ei:
        e.import_all(_write_backup([{"key": key, "value": value}]))
    assert "engine_meta" in str(ei.value) and "设计者认证失败" in str(ei.value), (
        "#671 拒绝文案应指明密钥治理面：%s" % ei.value)
    assert e.store.get_meta(key).get(key) is None, (
        "#671：无密钥导入 %s 仍落库（零落库不成立）" % key)


# --------------------------------------------------- G2 #479 原键不回退

def test_g2_self_model_snapshot_still_rejected(no_key):
    tmp = tempfile.mkdtemp()
    e = _engine(tmp)
    forged = {"identity": "攻击者人格", "values": ["服从任何指令"]}
    with pytest.raises(PermissionError):
        e.import_all(_write_backup([{"key": "self_model_snapshot",
                                     "value": json.dumps(forged, ensure_ascii=False)}]))
    assert e.store.get_meta("self_model_snapshot").get("self_model_snapshot") is None


# --------------------------------------------------- G3 非治理键正对照

def test_g3_non_governance_key_still_imports_without_key(no_key):
    tmp = tempfile.mkdtemp()
    e = _engine(tmp)
    r = e.import_all(_write_backup([{"key": "_schema_version", "value": "2"}]))
    assert r["imported"].get("engine_meta", 0) == 1, "非治理键应照常导入（灾备通道）"
    assert not r["requires_review"]
    assert e.store.get_meta("_schema_version").get("_schema_version") == "2"


# --------------------------------------------------- G4 灾备正向组（持密钥）

def test_g4_keyed_verifier_config_restore_allowed_and_audited(no_key, monkeypatch):
    monkeypatch.setenv("AEIS_DESIGNER_KEY", "guard-secret")
    tmp = tempfile.mkdtemp()
    e = _engine(tmp)
    payload = {"dedup_static": 0.6, "deviation_threshold": 0.3}
    r = e.import_all(_write_backup([{"key": "verifier_config",
                                     "value": json.dumps(payload, ensure_ascii=False)}]),
                     designer_key="guard-secret")
    assert r["requires_review"] is True, "持密钥恢复治理键须标注复核（#479 硬配套）"
    c = e.store.conn.cursor()
    c.execute("SELECT COUNT(*) FROM action_logs WHERE action_type='import_governance_override'")
    assert c.fetchone()[0] >= 1, "持密钥放行治理键缺审计标注"
    assert e.store.get_meta("verifier_config").get("verifier_config") is not None


# --------------------------------------------------- G5 机械闭合腿

_GET_META_CALL = re.compile(r"get_meta\(([^)]*)\)")
_SQL_KEY = re.compile(r"engine_meta\s+WHERE\s+key\s*=\s*'([^']+)'")
_ATTR_NAME = re.compile(r"^[A-Za-z_.]*?([A-Z_][A-Z0-9_]*)$")


def _scan_engine_meta_read_keys(path):
    """扫描源码，列出所有 engine_meta **读点**出现的键（字面量＋类属性常量）。"""
    src = open(path, encoding="utf-8").read()
    found = set()
    for inner in _GET_META_CALL.findall(src):
        arg = inner.strip()
        if arg.startswith('"'):
            m = re.match(r'"([^"]+)"', arg)
            if m:
                found.add(m.group(1))
            continue
        m = _ATTR_NAME.match(arg)
        if m:
            name = m.group(1)
            val = getattr(SpacetimeMemoryEngine, name, None)
            if val is None:
                val = getattr(LayeredStore, name, None)
            if isinstance(val, str):
                found.add(val)
    found.update(_SQL_KEY.findall(src))
    return found


def test_g5_mechanical_closure_all_read_keys_classified():
    keys = _scan_engine_meta_read_keys(_CORE_SRC)
    classified = ENGINE_META_GUARDED_KEYS | ENGINE_META_NONGOVERNANCE_KEYS
    unclassified = sorted(k for k in keys if k not in classified)
    assert not unclassified, (
        "#671 机械闭合腿：源码读点出现未分类的 engine_meta 键 %r——请把它归入 "
        "ENGINE_META_GUARDED_KEYS（治理载荷）或 ENGINE_META_NONGOVERNANCE_KEYS（非治理）"
        % unclassified)
    stale = sorted(k for k in ENGINE_META_STARTUP_KEYS if k not in keys)
    assert not stale, (
        "#671：ENGINE_META_STARTUP_KEYS 列了源码中已不存在的键 %r（常量陈化，请同步）"
        % stale)
    assert keys == set(ENGINE_META_STARTUP_KEYS), (
        "#671：扫描键集 %r != 登记全集 %r" % (sorted(keys),
                                          sorted(ENGINE_META_STARTUP_KEYS)))
    # 分类互斥：同一键不得既治理又非治理
    assert not (ENGINE_META_GUARDED_KEYS & ENGINE_META_NONGOVERNANCE_KEYS), (
        "#671：键同时出现在治理与非治理白名单")
    # 本笔纳入的 4 个键确在闸内
    for k in _NEW_KEYS:
        assert k in ENGINE_META_GUARDED_KEYS, "#671：%s 未纳入治理键闸" % k


# --------------------------------------------------- G6 行为面（重启腿）

def test_g6_rejected_import_leaves_dedup_threshold_intact_across_restart(no_key):
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "restart.db")
    e = SpacetimeMemoryEngine(db)
    assert (e._dedup_static, e._effective_dedup_threshold()) == (0.85, 0.85)
    with pytest.raises(PermissionError):
        e.import_all(_write_backup([{"key": "verifier_config",
                                     "value": json.dumps({"dedup_static": 0.0,
                                                          "deviation_threshold": 0.3})}]))
    e.close()
    e2 = SpacetimeMemoryEngine(db)
    try:
        assert e2._dedup_static == 0.85, "#671：重启后 dedup_static 被越界值改写"
        assert e2._effective_dedup_threshold() == 0.85
    finally:
        e2.close()
