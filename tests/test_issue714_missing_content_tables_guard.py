# -*- coding: utf-8 -*-
"""#714 定点守卫：内容表缺席的库上 `activate()` 必须落到空工作态，不得抛异常。

判据来源与口径（为什么这样判，不是自创第三套）
--------------------------------------------
issue #714（P1）：`_ensure_tables` 只建 `activation_nodes`，而 `_load_graph` /
`_seed_by_text` 的两条 SELECT 无保护 ⇒ 全新文件路径上构造成功（不报错），调用
`activate()` 时两条腿各自 `sqlite3.OperationalError: no such table: nodes`，整轮
无法返回。同文件 `workset_state()` 对**同一异常**已显式降级，源码注释逐字写着
「恢复库无内容表(独立激活库)——仅返回激活值」——即「只有激活表的库」是本模块
**已认定的受支持场景**。故 `activate()` 应返回既有空工作态（`status=ok, size=0`），
而不是抛异常。

本件把三条腿对齐到**既有**那一侧口径（`workset_state` 的 `except
sqlite3.OperationalError`），不新增第三套语义：
  · `_load_graph` 两条 SELECT 各自降级 ⇒ 视作空图（`nodes=[]` / `edges=[]`）；
  · `_seed_by_text` 的 SELECT 降级 ⇒ 无可投票词元（返回空种子，与 #161 空查询同形）；
  · 构造期给出一条明确 `RuntimeWarning`（构造成功 ≠ 图已就绪，调用方须能检索到提示）。

量具有效性的正/反两向对照（下方每条断言都有对应读数，不是恒绿）：
  · 正向：干净 `db_path` 上两条腿都降级、`activate()` 返回 `status=ok, size=0`；
  · 反向：同一路径补齐 `nodes`/`edges` 后节点/边真读到 ⇒ 缺陷精确落在「表缺失」，
    与表内容无关；`nodes` 在场而 `edges` 缺席时节点域仍保留（两条腿分开降级）。

运行（仓根）：python -X utf8 -m pytest tests/test_issue714_missing_content_tables_guard.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import lingshu.core.activation as activation
except ModuleNotFoundError as error:            # pragma: no cover
    if error.name != "numpy":
        raise
    pytest.skip("ActivationEngine requires NumPy", allow_module_level=True)

from lingshu.core.core import LayeredStore  # noqa: E402

NODES_DDL = ("CREATE TABLE nodes (id TEXT PRIMARY KEY, content TEXT, tags TEXT,"
             " condition_space TEXT)")
EDGES_DDL = ("CREATE TABLE edges (id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT,"
             " relation_type TEXT, condition_space TEXT)")


def table_names(path):
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
    finally:
        con.close()


def bare_engine(tmp_path, name):
    """全新文件路径上构造引擎（只有 `activation_nodes` 的库）。"""
    path = str(tmp_path / ("%s.db" % name))
    eng = activation.ActivationEngine(db_path=path,
                                      audit_path=str(tmp_path / ("%s.jsonl" % name)))
    return path, eng


def add_content_tables(path, nodes=(("n1", "灵枢 认知图 上下文"),),
                       edges=(("e1", "n1", "n1", "causal"),)):
    """补齐内容表（模拟 `LayeredStore` 建的库）——反向对照用。"""
    con = sqlite3.connect(path)
    con.execute(NODES_DDL)
    con.execute(EDGES_DDL)
    for nid, content in nodes:
        con.execute("INSERT INTO nodes (id, content, tags, condition_space) VALUES (?,?,?,?)",
                    (nid, content, "[]", None))
    for eid, src, dst, rtype in edges:
        con.execute("INSERT INTO edges (id, source_id, target_id, relation_type,"
                    " condition_space) VALUES (?,?,?,?,?)", (eid, src, dst, rtype, None))
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
# 前提与量具正对照
# --------------------------------------------------------------------------- #
def test_premise_fresh_path_has_only_activation_table(tmp_path):
    """前提（正对照）：`_ensure_tables` 只建 `activation_nodes`，内容表不在场。"""
    path, _eng = bare_engine(tmp_path, "premise")
    names = table_names(path)
    assert names, "库中一张表都没有——前提不成立，后续用例失去意义"
    assert "activation_nodes" in names, names
    assert "nodes" not in names and "edges" not in names, names


def test_positive_control_content_tables_are_read_when_present(tmp_path):
    """正对照：内容表在场时两条腿真读到节点/边（证明降级不是「永远读不到」）。"""
    path, eng = bare_engine(tmp_path, "present")
    add_content_tables(path)
    eng._load_graph()
    assert len(eng._node_ids) == 1, eng._node_ids
    assert len(eng._edge_meta[0]) == 1, eng._edge_meta
    assert eng._seed_by_text("灵枢") == [("n1", 1.0)]
    result = eng.activate("灵枢", workset="w714present")
    assert result["status"] == "ok" and result["size"] == 1, result


# --------------------------------------------------------------------------- #
# 缺陷面：内容表缺席
# --------------------------------------------------------------------------- #
def test_load_graph_degrades_to_empty_graph(tmp_path):
    """`_load_graph` 在无 `nodes`/`edges` 表时视作空图，不抛 OperationalError。"""
    _path, eng = bare_engine(tmp_path, "load")
    eng._load_graph()                       # 旧实现：OperationalError: no such table: nodes
    assert eng._node_ids == [], eng._node_ids
    assert eng._edge_meta == ([], [], []), eng._edge_meta
    assert eng._inh_meta == ([], [], []), eng._inh_meta


def test_seed_by_text_degrades_to_no_tokens(tmp_path):
    """`_seed_by_text` 在无 `nodes` 表时返回空种子（与 #161 空查询同形），不抛。"""
    _path, eng = bare_engine(tmp_path, "seed")
    seeds = eng._seed_by_text("灵枢")        # 旧实现：OperationalError: no such table: nodes
    assert seeds == [], seeds
    assert eng._seed_by_text("") == []


def test_activate_returns_empty_workset_instead_of_raising(tmp_path):
    """核心判据：`activate()` 返回既有空工作态契约（`status=ok, size=0`），不抛。"""
    _path, eng = bare_engine(tmp_path, "act")
    result = eng.activate("灵枢 认知图", workset="w714")
    assert result["status"] == "ok", result
    assert result["size"] == 0 and result["top"] == [] and result["seeds"] == 0, result
    assert result["hops"] == 2, result
    # 落表/工作态读取两侧同样自洽（空态而非「查询无命中」的假象要能读出来）
    assert eng.carry_vector("w714") == {}
    assert eng.workset_state("w714")["members"] == []
    assert eng.export_workset("w714")["members"] == []


def test_activate_writes_audit_with_zero_size(tmp_path):
    """审计面同样成立：空图激活留痕 `workset_size=0`、两跳零激活（既有空图契约）。"""
    _path, eng = bare_engine(tmp_path, "audit")
    eng.activate("灵枢", workset="w714a")
    records = [json.loads(line) for line in
               Path(eng.audit_path).read_text(encoding="utf-8").splitlines()]
    assert records, "审计文件为空——落痕面未执行"
    audit, = records
    assert audit["workset_size"] == 0 and audit["seeds"] == 0 and audit["top"] == []
    propagation = [s for s in audit["steps"] if s["phase"] == "propagate"]
    assert len(propagation) == 2, audit["steps"]
    assert all(s["max_act"] == 0.0 for s in propagation), propagation


def test_second_activate_on_same_bare_engine_also_survives(tmp_path):
    """重复激活（自条件开启）也不得抛——坏表缺席不随轮次变化，但每轮都重读全图。"""
    _path, eng = bare_engine(tmp_path, "repeat")
    first = eng.activate("灵枢", workset="w714r")
    second = eng.activate("认知图", workset="w714r",
                          self_condition=activation.SELF_CONDITION_WEIGHT)
    assert first["status"] == second["status"] == "ok"
    assert second["size"] == 0 and second["carried"] == 0, second


def test_workset_state_existing_degradation_is_preserved(tmp_path):
    """既有口径对照（`workset_state`）：有激活行、无内容表 ⇒ 仍返回激活值，内容留空。

    这是**既有**行为（源码注释「恢复库无内容表(独立激活库)——仅返回激活值」），
    本件不改它，只钉住「本条的修复与它同口径、没有把它改坏」。
    """
    path, eng = bare_engine(tmp_path, "ws")
    con = sqlite3.connect(path)
    # 显式列名（#505 给激活表补了 query 列；位置式 7 值写法不再适用）。
    con.execute("INSERT OR REPLACE INTO activation_nodes"
                " (id, workset, node_id, activation, source, hop, ts)"
                " VALUES (?,?,?,?,?,?,?)",
                ("act_1", "w714ws", "absent-node", 0.9, "seed", 0, 1.0))
    con.commit()
    con.close()
    state = eng.workset_state("w714ws", 5)
    assert state["size"] == 1, state
    member, = state["members"]
    assert member["node_id"] == "absent-node" and member["activation"] == 0.9
    assert member["content"] == "" and member["layer"] is None


def test_edges_missing_while_nodes_present_keeps_node_domain(tmp_path):
    """两条腿**分开**降级：`nodes` 在场而 `edges` 缺席 ⇒ 节点域保留、边域为空。"""
    path, eng = bare_engine(tmp_path, "legs")
    con = sqlite3.connect(path)
    con.execute(NODES_DDL)
    con.execute("INSERT INTO nodes (id, content, tags, condition_space) VALUES (?,?,?,?)",
                ("n1", "灵枢 认知图 上下文", "[]", None))
    con.commit()
    con.close()
    eng._load_graph()
    assert len(eng._node_ids) == 1, eng._node_ids
    assert eng._edge_meta == ([], [], []), eng._edge_meta
    assert eng._seed_by_text("灵枢") == [("n1", 1.0)]


# --------------------------------------------------------------------------- #
# 构造期告警（调用方须能检索到「空图」这一事实）
# --------------------------------------------------------------------------- #
def test_missing_nodes_table_warns_at_construction(tmp_path):
    """构造期对缺失 `nodes` 表给一条明确 warning（不再静默「构造成功即已就绪」）。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        activation.ActivationEngine(db_path=str(tmp_path / "warn.db"),
                                    audit_path=str(tmp_path / "warn.jsonl"))
    messages = [str(w.message) for w in caught]
    assert messages, "缺内容表却零告警——调用方无从判断激活面是空图"
    assert any("nodes" in m for m in messages), messages
    assert any(issubclass(w.category, RuntimeWarning) for w in caught), \
        [(w.category, str(w.message)) for w in caught]


def test_no_warning_when_nodes_table_present(tmp_path):
    """反面对照：内容表在场时**不得**告警（否则告警失去判别力）。"""
    path = str(tmp_path / "quiet.db")
    store = LayeredStore(path)
    store.close()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        activation.ActivationEngine(db_path=path,
                                    audit_path=str(tmp_path / "quiet.jsonl"))
    assert [w for w in caught if "nodes" in str(w.message)] == [], \
        [str(w.message) for w in caught]
