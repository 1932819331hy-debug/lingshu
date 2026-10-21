# -*- coding: utf-8 -*-
"""test_issue613_retrieval_determinism_guard · 检索结果序的确定性守卫（#613）

现象（issue #613）：**同一引擎、同一查询、连续两次调用，返回结果顺序不同**。
根因链（两个符号锚）：
  1. 候选池 SQL 按 `importance DESC, last_access DESC` 定序（`core.py` 的
     `search_content`），排序列上**大量并列**，并列项次序落到 SQLite 的物理取行序；
  2. 同一检索路径在返回前调 `increment_access_many`（`touch` 默认真）**写回
     `last_access`** ⇒ 下一次调用的候选池次序即变；
  3. 终排 `scored.sort(key=(-sim, -importance, -confidence))` **无确定性末位键**，
     Python `sort` 稳定 ⇒ 池内并列次序原样泄漏进最终结果序。

修复：两处都补**确定性末位键 `id`**（库内唯一、跨平台稳定、字符串比较无浮点误差）：
  · 候选池 SQL：`ORDER BY importance DESC, last_access DESC, id ASC`（主查询与
    `LIMIT 500` 全表兜底两支都补）；
  · 终排：`(-sim, -importance, -confidence, node.id)`。
`id` 只在**各键全并列**时起作用 ⇒ 非并列情形的排序语义**逐位不变**。

本守卫锁住的性质（**同状态 ⇒ 同序**）：
  · 同引擎连续多次调用：**逐位相同**（含 310 条超候选池、触发截断的场景）；
  · 首调会写回 `last_access`（状态确实被改动），而改后仍逐位相同
    ⇒ 证明「写回近因」不再泄漏进结果序；
  · 正对照：`importance` 互异时本来就只有一种顺序（证明量具能测出顺序差异）；
  · 语义不变：非并列样本的次序仍由 `(-sim, -importance, -confidence)` 决定。

**不**锁住的性质（明示边界，避免守卫比实现承诺得更多）：
  两个**全新库**（同内容、同插入序）的首调次序**不保证相同**——它们的**存储态本就
  不同**：①节点 `id` 含 `uuid4().hex[:8]`（契约，逐库随机）；②候选池主键
  `last_access` 是 wall-clock，毫秒级碰撞分组逐次不同。故「跨库不同序」是
  **输入态不同**，不是本 issue 所指的非确定性；本 issue 所指的非确定性是
  **同一存储态**（同一库文件、同一查询）给出不同序——已由本守卫锁死。
  （若日后要求「跨库逐位可比」，需另裁：改 id 契约或改候选池定序键，均非 tie-break 可解。）

变异哨兵：把终排末位键 `node.id` 去掉（回到三键），
`test_same_engine_repeated_calls_bit_identical` 与
`test_truncated_pool_repeated_calls_bit_identical` 必红。
"""
import pytest

from lingshu.core.core import SpacetimeMemoryEngine

pytestmark = pytest.mark.filterwarnings("ignore")

Q = "超时"


def _engine(tmp_path):
    return SpacetimeMemoryEngine(str(tmp_path / "e.db"))


def _add(e, n, importance_of, content_of):
    for i in range(n):
        e.add_perception(content_of(i), importance=importance_of(i), skip_dedup=True)


def _ids(e, limit):
    return [n.id for n, _ in e.store.search_content(Q, limit=limit)]


def test_same_engine_repeated_calls_bit_identical(tmp_path):
    """全并列（importance 同、内容等长同族）下，同引擎连续 5 次调用逐位相同。"""
    e = _engine(tmp_path)
    _add(e, 12, lambda i: 0.5, lambda i: "%s%02d" % (Q, i))
    seq = [_ids(e, 20) for _ in range(5)]
    assert all(s == seq[0] for s in seq), (
        "同引擎重复调用出现不同顺序：%r" % ([len({tuple(s) for s in seq})]))


def test_recency_writeback_does_not_leak_into_order(tmp_path):
    """首调确实写回了 `last_access`（状态被改动），但结果序仍逐位相同。"""
    e = _engine(tmp_path)
    _add(e, 12, lambda i: 0.5, lambda i: "%s%02d" % (Q, i))
    c = e.store.conn.cursor()
    c.execute("SELECT id, last_access FROM nodes")
    before = dict(c.fetchall())
    first = _ids(e, 20)
    c.execute("SELECT id, last_access FROM nodes")
    after = dict(c.fetchall())
    # 前置断言：写回确实发生了（否则本用例退化成「无扰动」的弱版本）
    assert any(before[k] != after[k] for k in before), "未观察到 last_access 写回"
    assert _ids(e, 20) == first


def test_truncated_pool_repeated_calls_bit_identical(tmp_path):
    """310 条 > 候选池上限（300）：触发截断，同引擎连续 4 次调用集合与顺序都逐位相同。"""
    e = _engine(tmp_path)
    _add(e, 310, lambda i: 0.5, lambda i: "%s%03d" % (Q, i))
    seq = [_ids(e, 20) for _ in range(4)]
    assert all(s == seq[0] for s in seq)
    assert len({frozenset(s) for s in seq}) == 1, "返回集合跨调用漂移"


def test_distinct_importance_control_single_order(tmp_path):
    """正对照：`importance` 互异 ⇒ 消除并列，本就只有一种顺序（量具自证）。"""
    e = _engine(tmp_path)
    _add(e, 12, lambda i: 0.30 + i * 0.01, lambda i: "%s%02d" % (Q, i))
    seq = [_ids(e, 20) for _ in range(5)]
    assert len({tuple(s) for s in seq}) == 1


def test_non_tied_order_semantics_unchanged(tmp_path):
    """语义不变：相似度并列时仍按 `importance` 降序（末位 `id` 键只在全并列时起作用）。

    构造：内容等长同族 ⇒ 二元组 Jaccard 全等（相似度这一主键并列），
    `importance` 互异 ⇒ 次序必须严格按 `importance` 降序——与是否补 `id` 无关。
    """
    e = _engine(tmp_path)
    _add(e, 8, lambda i: 0.30 + i * 0.01, lambda i: "%s%02d" % (Q, i))
    got = _ids(e, 8)
    rows = {r[0]: r[1] for r in e.store.conn.execute(
        "SELECT id, importance FROM nodes").fetchall()}
    assert [rows[i] for i in got] == sorted((rows[i] for i in got), reverse=True)


def test_cross_store_states_differ_each_self_consistent(tmp_path):
    """边界明示：两个全新库的**存储态本就不同**（id/last_access 逐库不同），
    故跨库次序不保证相同；但每个库自身**自洽**（重复调用逐位相同）。"""
    a = SpacetimeMemoryEngine(str(tmp_path / "a.db"))
    _add(a, 12, lambda i: 0.5, lambda i: "%s%02d" % (Q, i))
    b = SpacetimeMemoryEngine(str(tmp_path / "b.db"))
    _add(b, 12, lambda i: 0.5, lambda i: "%s%02d" % (Q, i))
    assert _ids(a, 20) == _ids(a, 20)
    assert _ids(b, 20) == _ids(b, 20)
    ids_a = {r[0] for r in a.store.conn.execute("SELECT id FROM nodes").fetchall()}
    ids_b = {r[0] for r in b.store.conn.execute("SELECT id FROM nodes").fetchall()}
    assert ids_a != ids_b, "两库 id 集合相同——本用例的前提（id 逐库随机）不成立"
