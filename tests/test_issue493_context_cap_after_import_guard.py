# -*- coding: utf-8 -*-
"""#493 · `import_all` 恢复备份后**运行期容量状态必须随盘面刷新** —— 守卫

缺陷（lingshu 公开 issue #493；判定成立）：
  `#246` 给 `_context_max` 加了跨进程持久化（`engine_meta` key `context_max`），
  但**运行期副本只在 `__init__` 读一次**。`import_all` 逐表 `INSERT OR REPLACE`
  把备份的 `engine_meta`（含 `context_max`）落库后，`self._context_max` 仍是
  构造期读到的旧值；而 `enforce_context_cap` 的 FIFO 淘汰作用于**整库**情境层
  ⇒ 下一次 `add_context` 就按旧上限（默认 200）静默批量删掉备份里多出的情境
  记忆。issue 原文读数：`export_all` 600 条（cap 显式调 1000）→ 新库 `import_all`
  后 600 条 → 写 1 条 → **200 条**（最早写入的「青霉素过敏」前提被物理删除）。

判据来源（实现侧口径 · 与「启动时从库读」同口径）：
  `import_all` **成功导入（提交之后）** 必须把运行期容量状态随盘面刷新，复用
  既有读数路径 `SpacetimeMemoryEngine._load_context_max()`（不触发淘汰，与
  `__init__` 同一口径），**不**改动 `set_context_cap` 的「显式设置优先」语义，
  也**不**改 `enforce_context_cap` 的裁剪判据。
  · 为什么读点必须在 `_commit_locked()` 之后：导入是「替换/覆盖整库」路径，
    刷新要在**提交之后**读，否则拿到的是未落定的状态。
  · 为什么这样判：#493 的主张是「盘面与运行期不一致」这一**读数**，故断言取
    「运行期 `_context_max` == 盘面 `engine_meta['context_max']`」＋「下一次写入
    不批量淘汰」两条公开面读数（不靠私有实现细节定罪）。

断言组（抽掉刷新即红）：
  M1 issue 原文最小复现：600 条 → `import_all` → 写 1 条 ⇒ **601 条**、最早节点存活、
     运行期上限 == 1000（旧实现：200 条、最早节点被删、运行期上限 200）。
  M2 重启行不回归：导出 → 新引擎打开 ⇒ 上限随盘面（`#246` 已修的那一行）。
  M3 浮点容量行不回归：`set_context_cap(3.0)` 不抛 `TypeError`、按 3 淘汰。
  P1 正对照（防过修）：导入后**显式** `set_context_cap(50)` ⇒ 上限 50（显式优先）。
  P2 正对照（量具有效）：普通（非导入）路径容量语义逐字不变（超上限仍按既有口径裁剪）。
  M6 导入空库 / 无情境层备份 ⇒ 不抛异常、容量不被清零（随盘面）。
  M7 `import_all` 返回体**形状与取值**逐字不变（防过修）。
  M8 备份携带**更小**上限时也随盘面刷新（钉住「随盘面」而非「取较大值」的口径）。
  M9 刷新**时机**在提交之后：提交失败（事务回滚）时运行期容量**不得**已被刷成备份值
     （把刷新挪到 `_commit_locked()` 之前 ⇒ 本用例红）。

边界（如实标注）：
  · `#671`/`#479` 把 `context_max` 纳入 `ENGINE_META_GUARDED_KEYS` ⇒ 在**本守卫的
    基线**上，无密钥导入一份含 `context_max` 的备份会先抛 `PermissionError`
    （零落库，`#493` 路径根本走不到）。故本守卫的灾备用例一律**持有效密钥**，
    以隔离 `#493` 这一面；「无密钥 ⇒ 拒」另由 `tests/test_issue671_*.py` 负责。
  · 只断言公开面（情境层计数 + 节点存活 + 盘面 meta + 私有 `_context_max`）与
    返回值形状；**不**断言淘汰的物理实现（`rowid`/`created_at` 排序属 #259 面）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue493_context_cap_after_import_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine, MemoryLayer  # noqa: E402

_KEY = "guard-493-designer-key"


@pytest.fixture
def designer_key(monkeypatch):
    """持有效设计者密钥（#671 起 `context_max` 属治理键，灾备用例须持密钥）。"""
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)
    return _KEY


def _engine(path):
    return SpacetimeMemoryEngine(str(path))


def _context_count(e):
    return e.store.count_layer(MemoryLayer.CONTEXT)


def _backup_with_context(tmp_path, n=600, cap=1000, name="bk.json"):
    """导出 n 条情境、上限显式调为 cap 的备份，返回 (备份路径, 最早节点 id)。"""
    src = _engine(tmp_path / "src.db")
    src.set_context_cap(cap)
    first = src.add_context("用户：我对青霉素过敏，开药前务必提醒")
    for i in range(n - 1):
        src.add_context("会话第 %d 轮的情境" % i)
    bk = str(tmp_path / name)
    src.export_all(bk)
    assert src.store.get_node(first.id) is not None, "夹具自检：最早节点应已落库"
    return bk, first.id


# --------------------------------------------------------------- M1 issue 原文最小复现

def test_m1_import_refreshes_runtime_cap_and_next_write_keeps_backup(tmp_path, designer_key):
    """M1：600 条 → import_all → 600 条 → 写 1 条 ⇒ 601 条、最早节点存活。

    旧实现（import_all 后不刷新运行期上限）：写 1 条后 200 条、最早节点被物理删除。
    """
    bk, first_id = _backup_with_context(tmp_path, n=600, cap=1000)
    dst = _engine(tmp_path / "dst.db")
    assert dst._context_max == SpacetimeMemoryEngine.DEFAULT_CONTEXT_MAX, \
        "夹具自检：新库构造后应为默认上限 %r" % (dst._context_max,)

    ret = dst.import_all(bk, designer_key=designer_key)
    assert _context_count(dst) == 600, \
        "夹具自检：导入后情境层应 600 条：%r" % (_context_count(dst),)
    assert dst.store.get_meta("context_max").get("context_max") == "1000", \
        "盘面 meta 应为备份携带的 1000：%r" % (dst.store.get_meta("context_max"),)

    # ★ 判据一：运行期上限随盘面刷新（旧实现 200）
    assert dst._context_max == 1000, \
        "M1 运行期上限未随盘面刷新：%r != 1000（#493 缺陷态）" % (dst._context_max,)

    # ★ 判据二：下一次写入不按旧上限批量淘汰
    dst.add_context("导入后的第一条情境")
    assert _context_count(dst) == 601, \
        "M1 写 1 条后被静默批量淘汰：%r != 601（issue 原文缺陷读数 = 200）" % (
            _context_count(dst),)
    assert dst.store.get_node(first_id) is not None, \
        "M1 被淘汰的正是最早写入的会话前提（青霉素那条）"


# --------------------------------------------------------------- M2 重启行不回归

def test_m2_restart_line_no_regression(tmp_path):
    """M2：`#246` 的重启行——导出后新引擎打开，上限随盘面（不回归）。"""
    bk, _ = _backup_with_context(tmp_path, n=30, cap=1000, name="restart.json")
    src2 = _engine(tmp_path / "src2.db")
    src2.set_context_cap(1000)
    for i in range(30):
        src2.add_context("重启行 %d" % i)
    assert src2._context_max == 1000

    reopened = _engine(tmp_path / "src2.db")
    assert reopened._context_max == 1000, \
        "M2 重启后上限未从盘面读回：%r" % (reopened._context_max,)
    reopened.add_context("重启后第一条")
    assert _context_count(reopened) == 31, \
        "M2 重启后第一条写入被按默认上限淘汰：%r" % (_context_count(reopened),)


# --------------------------------------------------------------- M3 浮点容量行不回归

def test_m3_float_cap_line_no_regression(tmp_path, designer_key):
    """M3：`#246` 的浮点容量行——`set_context_cap(3.0)` 不抛 `TypeError`。"""
    e = _engine(tmp_path / "float.db")
    for i in range(6):
        e.add_context("浮点行 %d" % i)
    e.set_context_cap(3.0)          # 旧缺陷：float 落列表切片抛 TypeError
    assert isinstance(e._context_max, int), \
        "M3 上限未归一为 int：%r" % (type(e._context_max),)
    assert _context_count(e) == 3, "M3 浮点上限未生效：%r" % (_context_count(e),)

    # 再导入一次备份，刷新后仍须是 int（刷新路径不得把 float 带回运行期）
    bk, _ = _backup_with_context(tmp_path, n=5, cap=7, name="float_bk.json")
    e.import_all(bk, designer_key=designer_key)
    assert isinstance(e._context_max, int) and e._context_max == 7, \
        "M3 刷新后上限形状/取值异常：%r" % (e._context_max,)


# --------------------------------------------------------------- P1 正对照（防过修）

def test_p1_explicit_set_after_import_still_wins(tmp_path, designer_key):
    """P1：导入后**显式** `set_context_cap(50)` 仍然优先（刷新不得覆盖显式设置）。"""
    bk, _ = _backup_with_context(tmp_path, n=60, cap=1000, name="p1.json")
    dst = _engine(tmp_path / "p1.db")
    dst.import_all(bk, designer_key=designer_key)
    assert dst._context_max == 1000, "夹具自检：导入后应随盘面为 1000：%r" % (dst._context_max,)

    dst.set_context_cap(50)
    assert dst._context_max == 50, \
        "P1 显式设置被覆盖（过修）：%r != 50" % (dst._context_max,)
    assert dst.store.get_meta("context_max").get("context_max") == "50", \
        "P1 显式设置未落盘：%r" % (dst.store.get_meta("context_max"),)
    assert _context_count(dst) == 50, \
        "P1 显式设置的裁剪未生效：%r" % (_context_count(dst),)
    newest = dst.add_context("显式设置后的写入")
    assert dst.store.get_node(newest.id) is not None, \
        "P1 显式设置后新写入的节点被当场删除"
    assert _context_count(dst) == 50, \
        "P1 显式设置后写入未按 50 裁剪：%r" % (_context_count(dst),)


# --------------------------------------------------------------- P2 正对照（量具有效）

def test_p2_non_import_path_cap_semantics_unchanged(tmp_path):
    """P2：普通（非导入）路径容量语义逐字不变——超上限仍按既有口径裁剪。"""
    e = _engine(tmp_path / "plain.db")
    assert _context_count(e) == 0, "夹具自检：新库情境层应为空"
    e.set_context_cap(3)
    for i in range(10):
        e.add_context("普通路径 %d" % i)
    assert _context_count(e) == 3, \
        "P2 普通路径上限语义变了：%r != 3" % (_context_count(e),)

    d = _engine(tmp_path / "default.db")
    for i in range(SpacetimeMemoryEngine.DEFAULT_CONTEXT_MAX + 5):
        d.add_context("默认上限 %d" % i)
    assert _context_count(d) == SpacetimeMemoryEngine.DEFAULT_CONTEXT_MAX, \
        "P2 默认上限裁剪语义变了：%r" % (_context_count(d),)


# --------------------------------------------------------------- M6 空库 / 无情境层备份

def test_m6_empty_and_contextless_backup_do_not_zero_cap(tmp_path, designer_key):
    """M6：导入空备份 / 无情境层备份 ⇒ 不抛异常、容量不被清零。"""
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")

    e = _engine(tmp_path / "m6a.db")
    r = e.import_all(str(empty), designer_key=designer_key)
    assert r["imported"]["nodes"] == 0 and r["imported"]["engine_meta"] == 0, \
        "M6 空备份不应导入任何行：%r" % (r["imported"],)
    assert e._context_max == SpacetimeMemoryEngine.DEFAULT_CONTEXT_MAX, \
        "M6 空备份把容量清零/改写：%r" % (e._context_max,)

    # 目标库显式设过上限，导入「无情境层、无 engine_meta」的备份 ⇒ 上限不被清零
    e2 = _engine(tmp_path / "m6b.db")
    e2.set_context_cap(1000)
    nodes_only = tmp_path / "nodes_only.json"
    nodes_only.write_text(json.dumps({
        "nodes": [{"id": "k1", "layer": "knowledge", "content": "无情境层备份",
                   "modality": "text", "importance": 0.5, "confidence": 0.5,
                   "created_at": 1.0}],
    }, ensure_ascii=False), encoding="utf-8")
    r2 = e2.import_all(str(nodes_only), designer_key=designer_key)
    assert r2["imported"]["nodes"] == 1, "M6 无情境层备份应导入 1 节点：%r" % (r2["imported"],)
    assert e2._context_max == 1000, \
        "M6 无 engine_meta 的备份把运行期容量清零：%r" % (e2._context_max,)
    for i in range(3):
        e2.add_context("导入无情境层备份后 %d" % i)
    assert _context_count(e2) == 3, "M6 容量被清零导致误删：%r" % (_context_count(e2),)


# --------------------------------------------------------------- M7 返回体形状/取值

def test_m7_return_shape_and_counts_unchanged(tmp_path, designer_key):
    """M7：`import_all` 的返回计数**形状与取值**逐字不变（防过修）。

    重锚（#721 跨批冲突）：本断言原为「顶层键集合**恰等**」。#721（同批）给
    `import_all` 返回体**新增**两个上报键 `normalized_values` / `skipped_rows`
    （与 #652② 加 `missing_columns` 同例，都是**追加**）。而 M7 的意图是「#493
    那一行修法没有改返回体形状」——正确锚是「原有键一个不少、取值不变」，不是
    「键集合恰等于某一刻的快照」（后者会被任何合法的追加键打破）。
    故改为**超集**（`>=`）：下方全部取值断言逐字保留，另断旧键一个不缺。

    同上（#130 跨批冲突）：内层 `ret["imported"]` 当时**漏改**、仍是「恰等」。
    #130 给引擎新增治理表 `designer_todos`（进 `_SCHEMA_TABLES` / `M13_TABLES`，
    随导出/导入参与备份），`import_all` 的 `imported` 计数因此多出该键——同属
    **追加**。故内层与顶层同款重锚为**超集**；取值断言（nodes=600 /
    engine_meta=2 / escalation_points=6）逐字保留。
    """
    bk, _ = _backup_with_context(tmp_path, n=600, cap=1000, name="m7.json")
    dst = _engine(tmp_path / "m7.db")
    ret = dst.import_all(bk, designer_key=designer_key)

    assert set(ret.keys()) >= {
        "imported", "skipped_tables", "dangling_rows", "orphan_edges",
        "orphan_edges_sample", "integrity_ok", "discarded_keys", "missing_columns",
        "immutable_rows", "requires_review", "rejected_invalid_layer",
    }, "M7 返回体缺了旧键（改名/删除）：%r" % (sorted(ret.keys()),)
    assert set(ret["imported"].keys()) >= {
        "nodes", "edges", "blindspots", "skills", "promotion_proposals",
        "protections", "rejected_paths", "verifier_standards", "escalation_points",
        "action_logs", "engine_meta", "gap_history", "flywheel_reuse",
    }, "M7 imported 缺了旧键（改名/删除）：%r" % (sorted(ret["imported"].keys()),)
    assert ret["imported"]["nodes"] == 600, "M7 nodes 计数：%r" % (ret["imported"],)
    assert ret["imported"]["engine_meta"] == 2, \
        "M7 engine_meta 计数（_schema_version + context_max）：%r" % (ret["imported"],)
    assert ret["imported"]["escalation_points"] == 6, \
        "M7 escalation_points 种子计数：%r" % (ret["imported"],)
    assert ret["dangling_rows"] == 0 and ret["orphan_edges"] == 0, \
        "M7 完整性读数：%r" % (ret,)
    assert ret["integrity_ok"] is True, "M7 integrity_ok：%r" % (ret["integrity_ok"],)
    assert ret["discarded_keys"] == {} and ret["missing_columns"] == {}, \
        "M7 白名单/缺列读数：%r / %r" % (ret["discarded_keys"], ret["missing_columns"])
    assert ret["immutable_rows"] == [], "M7 不可变行读数：%r" % (ret["immutable_rows"],)
    assert ret["requires_review"] is True, \
        "M7 持密钥导入治理键（context_max）应标注复核：%r" % (ret["requires_review"],)
    assert ret["rejected_invalid_layer"] == {"count": 0, "sample": []}, \
        "M7 毒行读数：%r" % (ret["rejected_invalid_layer"],)


# --------------------------------------------------------------- M8 「随盘面」而非「取较大值」

def test_m8_refresh_follows_disk_even_when_backup_cap_is_smaller(tmp_path, designer_key):
    """M8：备份携带**更小**上限时，运行期同样随盘面（钉住口径，不是「取较大值」）。"""
    bk, _ = _backup_with_context(tmp_path, n=40, cap=50, name="m8.json")
    dst = _engine(tmp_path / "m8.db")
    dst.set_context_cap(1000)
    assert dst._context_max == 1000, "夹具自检：目标库显式设为 1000"

    dst.import_all(bk, designer_key=designer_key)
    assert dst.store.get_meta("context_max").get("context_max") == "50", \
        "夹具自检：备份 meta 应把盘面覆盖为 50：%r" % (dst.store.get_meta("context_max"),)
    assert dst._context_max == 50, \
        "M8 运行期未随盘面刷新（口径应为「随盘面」而非「取较大值」）：%r != 50" % (
            dst._context_max,)


# --------------------------------------------------------------- M9 刷新时机（提交之后）

def test_m9_refresh_only_after_commit(tmp_path, designer_key, monkeypatch):
    """M9：刷新时机必须在 `_commit_locked()` **之后**。

    构造：让本次导入的**最外层提交**失败（事务回滚）⇒ 备份的 `context_max` 并未
    落库，故运行期容量**不得**已被刷成备份值。把刷新挪到提交之前 ⇒ 本用例红
    （提交前读：同一连接读得到未提交写 ⇒ 运行期已被刷成 1000，而库内其实没落）。
    ★桩只拦「最外层提交」（`_tx_depth <= 1`）：内层（`log_action` 等嵌套
    `@_transactional`）照常放行，否则异常会在刷新之前就抛出、本用例失去判别力。
    """
    bk, _ = _backup_with_context(tmp_path, n=30, cap=1000, name="m9.json")
    dst = _engine(tmp_path / "m9.db")
    assert dst._context_max == SpacetimeMemoryEngine.DEFAULT_CONTEXT_MAX, \
        "夹具自检：新库构造后应为默认上限 %r" % (dst._context_max,)

    import lingshu.core.core as _core
    _orig = _core.LayeredStore._commit_locked

    def _outer_commit_boom(self):
        if getattr(self, "_tx_depth", 0) <= 1:
            raise RuntimeError("M9 模拟最外层提交失败")
        return _orig(self)

    monkeypatch.setattr(_core.LayeredStore, "_commit_locked", _outer_commit_boom)
    with pytest.raises(RuntimeError):
        dst.import_all(bk, designer_key=designer_key)
    monkeypatch.undo()

    assert dst.store.get_meta("context_max") == {}, \
        "M9 夹具自检：提交失败后盘面不应有 context_max：%r" % (
            dst.store.get_meta("context_max"),)
    assert dst._context_max == SpacetimeMemoryEngine.DEFAULT_CONTEXT_MAX, \
        "M9 提交失败（事务回滚）后运行期容量已被刷成备份值 %r ⇒ 刷新时机在提交之前" % (
            dst._context_max,)
