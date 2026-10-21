# -*- coding: utf-8 -*-
"""#709 定点守卫：形状异常的 `condition_space` 不得让整轮激活抛异常。

判据来源与口径
--------------
issue #709（P0）：`cond_match` 的守卫只校验「是 list 且长 2」，**不校验元素**——
`if ta and tb` 对 `[None, None]` 为真（列表非空）⇒ 直接进 `max(ta[0], tb[0])` ⇒
`TypeError: '>' not supported between instances of 'NoneType' and 'float'`。
调用点又落在 `_load_graph` 唯一那个 try/except（只为 `json.loads` 而设）之外，
于是**一条**形状异常记录把整轮 `_load_graph`（进而 `activate()`）抛穿。

实现侧口径（为什么这样判，不是自创）：
  · `cond_match` 自己的 docstring 已声明中性值——「单边无条件声明→中性(不奖励也
    不惩罚)」，取值 `0.5`；同文件对 `condition_space=null` 与非法 JSON 已按该口径
    回落 `{}`（`_load_graph` 的 `except (json.JSONDecodeError, TypeError)`）。
    ⇒ 非 dict 入参、元素非有限数值的 `time_window` 一律回落到**同一个**中性口径。
  · `[null, null]` 形状**不是臆造**：`tests/test_issue20_146_342_provenance_guard.py`
    钉住 `ConditionSpace.from_json({"time_window": None}).to_json()` 的产物就是
    `[None, None]`（core 自己的规范化链路产物）。
  · 坏形状**不参与本次查询也要**不崩：`_load_graph` 每轮全图重读。
  · 元素级判据是「有限数值」：`bool` 不算数值（`True` 与 `1` 在 Python 里 `==`，
    但把布尔当时间戳是坏形状）；`nan` / `inf` 不算有限（比较会静默给出伪读数）。

本件钉「缺陷不再存在」，且**量具本身有效**（下方每条都有正对照，不是恒绿/恒红）。
运行（仓根）：python -X utf8 -m pytest tests/test_issue709_cond_match_shape_guard.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
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

REPO = str(Path(__file__).resolve().parents[1])

# 正常条件空间（4 维全等）——正对照与「坏形状落在别处」的对照底物
GOOD_CS = json.dumps({"observation_position": "位置A", "observation_tool": "工具A",
                      "existence_constraint": "约束A",
                      "time_window": [100.0, 200.0]})

# 坏形状清单：**整列非 dict** 与 **time_window 元素非有限数值** 两类
BAD_COLUMN = {
    "column=5": "5",
    "column=[1,2]": "[1,2]",
    "column=null": "null",
    "column=\"str\"": '"abc"',
}
BAD_WINDOW = {
    "time_window=[null,null]": json.dumps({"time_window": [None, None]}),
    "time_window=[\"a\",\"b\"]": json.dumps({"time_window": ["a", "b"]}),
    "time_window=[1]": json.dumps({"time_window": [1]}),
    "time_window={}": json.dumps({"time_window": {"lo": 1, "hi": 2}}),
    "time_window=[true,false]": json.dumps({"time_window": [True, False]}),
    "time_window=[NaN,1.0]": json.dumps({"time_window": [float("nan"), 1.0]}),
    "time_window=[Inf,1.0]": json.dumps({"time_window": [float("inf"), 1.0]}),
}
BAD_ALL = dict(BAD_COLUMN, **BAD_WINDOW)


def make_db(tmp_path, name, node_cs, edges):
    """建一个**真 schema** 的库（LayeredStore 建表）后按原始 SQL 覆写条件列。

    为什么要覆写：`LayeredStore.add_node` 会走 `ConditionSpace` 规范化，**写不出**
    坏形状；而 issue 的触发面正是「库里已有的坏记录」（外部卡 / 灾备恢复产物）。
    """
    store = LayeredStore(str(tmp_path / ("%s.db" % name)))
    path = store.db_path
    store.close()
    con = sqlite3.connect(path)
    for nid, cs in node_cs:
        con.execute("INSERT INTO nodes (id, content, tags, condition_space) VALUES (?,?,?,?)",
                    (nid, "合成查询词 上下文 " + nid, "[]", cs))
    for i, (src, dst, rtype) in enumerate(edges):
        con.execute("INSERT INTO edges (id, source_id, target_id, relation_type,"
                    " condition_space) VALUES (?,?,?,?,?)", ("e%d" % i, src, dst, rtype, None))
    con.commit()
    con.close()
    return path


def engine_for(tmp_path, path, name):
    return activation.ActivationEngine(db_path=path,
                                       audit_path=str(tmp_path / ("%s.jsonl" % name)))


# --------------------------------------------------------------------------- #
# 量具正对照：正常形态必须读到精确读数（证明断言能测到、不是恒绿）
# --------------------------------------------------------------------------- #
def test_positive_control_cond_match_reads_exact_values():
    """正对照：正常 `time_window` 的重合率读数必须精确（量具有效）。"""
    a = {"observation_position": "p", "observation_tool": "t",
         "existence_constraint": "c", "time_window": [0.0, 100.0]}
    overlap = {"observation_position": "p", "observation_tool": "t",
               "existence_constraint": "c", "time_window": [50.0, 150.0]}
    disjoint = {"observation_position": "x", "observation_tool": "y",
                "existence_constraint": "z", "time_window": [1e6, 1e6 + 10.0]}
    assert activation.cond_match(a, a) == 1.0
    assert activation.cond_match(a, overlap) == 1.0
    assert activation.cond_match(a, disjoint) == 0.0
    assert activation.cond_match({}, a) == activation.COND_MATCH_NEUTRAL
    assert activation.cond_match(a, {}) == activation.COND_MATCH_NEUTRAL
    # 3 维相同、时间窗不相交 ⇒ 3/4（窗口维度不贡献）
    three_same = dict(disjoint, observation_position="p", observation_tool="t",
                      existence_constraint="c")
    assert activation.cond_match(a, three_same) == 0.75


def test_positive_control_load_graph_decay_matches_edge_base(tmp_path):
    """正对照：正常库的装载衰减 = 边类型 base × 1.0，精确吻合 `EDGE_BASE_DECAY`。"""
    path = make_db(tmp_path, "ok709",
                   [("a", GOOD_CS), ("b", GOOD_CS), ("c", GOOD_CS)],
                   [("a", "b", "causal"), ("b", "c", "similar")])
    eng = engine_for(tmp_path, path, "ok709")
    eng._load_graph()
    rows, cols, decays = eng._edge_meta
    assert len(eng._node_ids) == 3
    assert rows == [0, 1] and cols == [1, 2]
    assert decays == pytest.approx([0.85, 0.75]), decays
    result = eng.activate("合成查询词", workset="w709ok")
    assert result["status"] == "ok" and result["size"] == 3


# --------------------------------------------------------------------------- #
# 缺陷面一：cond_match 自身对坏入参 / 坏 time_window 不得抛，且回落中性
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label,raw", sorted(BAD_ALL.items()))
def test_cond_match_never_raises_on_malformed_input(label, raw):
    """坏形状入参（整列非 dict / time_window 元素坏）不得抛；非 dict 侧一律中性 0.5。

    精确读数（窗口维度记 0 分等）由下方专测钉住，这里只钉「不抛 + 落在合法值域 +
    非 dict 侧的中性口径」——判别力来自旧实现在 `[null,null]` 上直接 TypeError。
    """
    assert BAD_ALL, "坏形状清单为空——本用例会空转成假绿"
    bad = json.loads(raw)
    good = json.loads(GOOD_CS)
    for other in (good, bad):
        got = activation.cond_match(bad, other)
        assert isinstance(got, float) and 0.0 <= got <= 1.0, (label, got)
        if not isinstance(bad, dict) or not isinstance(other, dict):
            assert got == activation.COND_MATCH_NEUTRAL, (label, got)
    # 两侧都是坏形状也必须不抛
    assert isinstance(activation.cond_match(bad, bad), float), label


def test_cond_match_non_dict_returns_neutral_exactly():
    """非 dict 入参（JSON 的 5 / [1,2] / null / 字符串）一律中性 0.5。"""
    good = json.loads(GOOD_CS)
    cases = [5, [1, 2], None, "abc", True, 3.5]
    assert cases, "非 dict 入参清单为空"
    for bad in cases:
        assert activation.cond_match(bad, good) == activation.COND_MATCH_NEUTRAL, bad
        assert activation.cond_match(good, bad) == activation.COND_MATCH_NEUTRAL, bad
        assert activation.cond_match(bad, bad) == activation.COND_MATCH_NEUTRAL, bad


def test_cond_match_invalid_window_contributes_zero_not_crash():
    """`time_window` 元素坏 ⇒ 窗口维度记 0 分（其余 3 维相同的读数恰为 0.75）。"""
    three_same = {"observation_position": "p", "observation_tool": "t",
                  "existence_constraint": "c"}
    bad_windows = [None, [None, None], ["a", "b"], [1], [True, False],
                   [float("nan"), 1.0], [float("inf"), 0.0], {"lo": 0, "hi": 1}]
    assert bad_windows, "坏 time_window 清单为空"
    for tw in bad_windows:
        got = activation.cond_match(dict(three_same, time_window=tw),
                                    dict(three_same, time_window=[0.0, 10.0]))
        assert got == 0.75, (tw, got)
    # 反面对照：**正常**窗必须真贡献（否则上面的 0.75 可能来自「窗口永远不算」）
    assert activation.cond_match(dict(three_same, time_window=[0.0, 10.0]),
                                 dict(three_same, time_window=[5.0, 20.0])) == 1.0


# --------------------------------------------------------------------------- #
# 缺陷面二：一条坏记录不得中止整轮（`_load_graph` 每轮全图重读）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label,raw", sorted(BAD_ALL.items()))
def test_activate_survives_malformed_row_off_query_path(tmp_path, label, raw):
    """坏形状落在**与查询无关**的节点上 ⇒ `_load_graph`/`activate()` 都不得抛。"""
    path = make_db(tmp_path, "bad_off", [("a", GOOD_CS), ("b", GOOD_CS), ("c", raw)],
                   [("a", "b", "causal"), ("b", "c", "similar")])
    eng = engine_for(tmp_path, path, "bad_off")
    eng._load_graph()                       # 旧实现：此处抛 TypeError/AttributeError
    assert len(eng._node_ids) == 3, label
    result = eng.activate("合成查询词", workset="w709off")
    assert result["status"] == "ok" and result["seeds"] == 3, (label, result)


@pytest.mark.parametrize("label,raw", sorted(BAD_ALL.items()))
def test_activate_survives_malformed_row_on_query_path(tmp_path, label, raw):
    """坏形状落在**被查询命中**的节点上 ⇒ 同样不得抛（触发面与查询无关）。"""
    path = make_db(tmp_path, "bad_on", [("a", GOOD_CS), ("b", raw)],
                   [("a", "b", "similar")])
    eng = engine_for(tmp_path, path, "bad_on")
    result = eng.activate("合成查询词", workset="w709on")
    assert result["status"] == "ok", (label, result)


def test_load_graph_neutralizes_unexpected_cond_match_error(tmp_path, monkeypatch):
    """per-node 兜底：`cond_match` **意外**抛错时该条边回落中性衰减，不中止整轮。

    这是「把调用纳入 per-node try」那一条修复的判别用例——它必须能测到兜底本身：
    `cond_match` 被替换成必抛版本后，整轮仍须返回、且该边衰减 = base × 中性。
    """
    path = make_db(tmp_path, "boom", [("a", GOOD_CS), ("b", GOOD_CS)],
                   [("a", "b", "causal")])
    eng = engine_for(tmp_path, path, "boom")

    def boom(*_a, **_k):
        raise TypeError("synthetic cond_match failure")

    monkeypatch.setattr(activation, "cond_match", boom)
    eng._load_graph()
    assert len(eng._edge_meta[2]) == 1, eng._edge_meta
    assert eng._edge_meta[2][0] == pytest.approx(0.85 * activation.COND_MATCH_NEUTRAL)
    result = eng.activate("合成查询词", workset="w709boom")
    assert result["status"] == "ok", result


def test_neutral_fallback_value_is_the_documented_one():
    """中性常量与 `cond_match` docstring 声明的中性值同源（单一真源，防两处漂移）。"""
    assert activation.COND_MATCH_NEUTRAL == 0.5
    assert activation.cond_match(None, None) == activation.COND_MATCH_NEUTRAL
