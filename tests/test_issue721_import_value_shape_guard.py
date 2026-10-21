# -*- coding: utf-8 -*-
"""#721 `import_all` 值形状闸：容器值直接绑 SQL 令 M13 恢复入口整体抛异常 —— 守卫
（lingshu/core/core.py）

缺陷（公开 issue #721，本守卫独立复现，读数见交付报告）：
  `import_all` 的 ② 只管**键**白名单（`#125`），**值形状**零校验。备份里某 TEXT(JSON)
  列的值若是**容器**（嵌套 dict/list，例如 `action_logs.context` = `{"k":1}` 而非
  `'{"k":1}'` 文本），`tuple(r.get(col) for col in cols)` 直接绑 `INSERT OR REPLACE`
  时绑定层抛 `ProgrammingError: type 'dict' is not supported`（部分 sqlite 版本族报
  `IntegrityError: datatype mismatch`）⇒ **整批回滚、整表 0 落库**，既不「部分恢复」
  也报不出坏行位置——与 docstring 承诺的「跳过并报告 / 保持部分恢复语义」相悖。

修法（与 `_tolerant_nodes`「逐行容错、如实计数」同款，且**不丢数据**）：
  绑定前逐行逐列归一——列声明类型为 TEXT ⇒ 容器值 `json.dumps` 归一为 JSON 文本
  （`export_all` 写的正是 JSON 文本 ⇒ 无损往返），按表按列计数上报
  `normalized_values`；其余列型无法容纳容器 ⇒ 跳过该行并计入 `skipped_rows`
  （`imported` 计数同步扣除），其余行照常落库。

断言组（把归一逻辑抽掉 ⇒ A/C 变红并复现 `ProgrammingError`）：
  A 坏值在 TEXT(JSON) 列（`action_logs.context`）⇒ `import_all` 不抛、该行落库、
    落库值是**合法 JSON 文本**且可 `json.loads` 还原为原容器、`normalized_values`
    如实点名该列；同备份的 `nodes` 行照常落库（部分恢复语义）。
  B 正对照（严格反向，值均为合法 JSON 文本）⇒ 行为与返回值逐字不变
     （`normalized_values` / `skipped_rows` 均为空、行数一致）。
  C 坏值在**非 TEXT** 列（`nodes.access_count` = dict）⇒ 该行被跳过并计入
     `skipped_rows`（不猜「这个 dict 想表达什么数」），其余表照常落库、不抛。
  D 正对照（量具非恒绿）：合法备份导入 ⇒ `normalized_values == {}` 且
     `skipped_rows == {}`（归一闸不得把合法值也改掉）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue721_import_value_shape_guard.py -q --no-header
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

_KEY = "guard-721"


@pytest.fixture
def with_key(monkeypatch):
    """备份含带 id 的 `action_logs` 行（#479 治理载荷）⇒ 导入需设计者密钥。

    本守卫的目标是**值形状**面，故按 #479 既有契约配好密钥，使闸不成为干扰项。
    """
    monkeypatch.setenv("AEIS_DESIGNER_KEY", _KEY)
    return _KEY


def _backup_with(action_logs=None, nodes=None):
    """用 export_all 产出一份真备份，再按需替换两张表的内容。"""
    d = tempfile.mkdtemp(prefix="lingshu721_")
    src = SpacetimeMemoryEngine(os.path.join(d, "src.db"))
    src.add_context("情境甲 待备份的记忆", importance=0.5)
    src.store.log_action("probe_seed", "播种审计行", outcome={"n": 1})
    path = os.path.join(d, "backup.json")
    src.export_all(path)
    src.close()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if action_logs is not None:
        data["action_logs"] = action_logs
    if nodes is not None:
        data["nodes"] = nodes
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def test_a_container_value_in_text_column_is_normalized(with_key):
    """A：TEXT 列容器值 ⇒ 不抛、落库为 JSON 文本、如实上报归一列。"""
    with open(_backup_with(), encoding="utf-8") as f:
        log_rows = json.load(f)["action_logs"]
    assert log_rows, "A 前置：备份须含 action_logs 行"
    poisoned = [dict(log_rows[0], context={"k": 1})]
    dst = SpacetimeMemoryEngine(":memory:")
    try:
        res = dst.import_all(_backup_with(action_logs=poisoned), designer_key=_KEY)

        assert res["imported"]["nodes"] == 1, (
            "#721 坏值让整批回滚：nodes 应照常落库，实测 %r" % (res["imported"],))
        rows = dst.store.conn.execute(
            "SELECT context FROM action_logs WHERE action_type='probe_seed'").fetchall()
        assert len(rows) == 1, "#721 该审计行未落库：%r" % (rows,)
        got = rows[0][0]
        assert isinstance(got, str), "#721 落库值不是文本：%r" % (got,)
        assert json.loads(got) == {"k": 1}, (
            "#721 归一后无法还原原容器：%r" % (got,))
        assert res["normalized_values"].get("action_logs", {}).get("context") == 1, (
            "#721 未如实上报归一列：%r" % (res["normalized_values"],))
        assert res["skipped_rows"] == {}, (
            "#721 TEXT 列可无损归一，不应整行跳过：%r" % (res["skipped_rows"],))
    finally:
        dst.close()


def test_b_positive_control_strict_roundtrip_unchanged(with_key):
    """B（正对照）：值均为合法 JSON 文本 ⇒ 返回值与行为逐字不变。"""
    dst = SpacetimeMemoryEngine(":memory:")
    try:
        res = dst.import_all(_backup_with(), designer_key=_KEY)
        assert res["imported"]["nodes"] == 1, "B 正对照：nodes 未落库：%r" % (res["imported"],)
        assert res["imported"]["action_logs"] == 1, (
            "B 正对照：action_logs 未落库：%r" % (res["imported"],))
        assert res["integrity_ok"] is True, "B 正对照：integrity_ok 应为 True"
        assert res["normalized_values"] == {}, (
            "B 正对照：合法备份被误报归一：%r" % (res["normalized_values"],))
        assert res["skipped_rows"] == {}, (
            "B 正对照：合法备份被误报跳行：%r" % (res["skipped_rows"],))
    finally:
        dst.close()


def test_c_container_value_in_non_text_column_skips_row_only(with_key):
    """C：非 TEXT 列容器值 ⇒ 跳过该行并计数，不抛、其余表照常落库。"""
    with open(_backup_with(), encoding="utf-8") as f:
        node_rows = json.load(f)["nodes"]
    assert node_rows, "C 前置：备份须含 nodes 行"
    poisoned = [dict(node_rows[0], access_count={"nested": 1})]
    dst = SpacetimeMemoryEngine(":memory:")
    try:
        res = dst.import_all(_backup_with(nodes=poisoned), designer_key=_KEY)

        assert res["skipped_rows"].get("nodes") == 1, (
            "#721 非 TEXT 列容器值未跳行上报：%r" % (res["skipped_rows"],))
        assert res["imported"]["nodes"] == 0, (
            "#721 imported 未扣除跳过的行：%r" % (res["imported"],))
        n = dst.store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert n == 0, "#721 无法容纳的坏行仍落了库：%r" % (n,)
    finally:
        dst.close()


def test_d_positive_control_no_normalization_for_valid_values(with_key):
    """D（正对照）：合法值（str/int/float/None/bytes）一律原样通过。"""
    with open(_backup_with(), encoding="utf-8") as f:
        node_rows = json.load(f)["nodes"]
    assert node_rows, "D 前置：备份须含 nodes 行"
    dst = SpacetimeMemoryEngine(":memory:")
    try:
        res = dst.import_all(_backup_with(), designer_key=_KEY)
        assert res["normalized_values"] == {} and res["skipped_rows"] == {}, (
            "#721 合法值被归一/跳过：%r / %r"
            % (res["normalized_values"], res["skipped_rows"]))
        got = dst.store.conn.execute(
            "SELECT content FROM nodes").fetchone()[0]
        assert "待备份的记忆" in got, "D 正对照：合法内容未原样落库：%r" % (got,)
    finally:
        dst.close()
