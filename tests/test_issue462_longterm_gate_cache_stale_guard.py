# -*- coding: utf-8 -*-
"""#462 守卫：长期记忆门的全库词表缓存，在「删 1 条再增 ≥2 条」后必须全量重建。

缺陷面（公开 issue #462，2026-10-14 独立复核成立）：
`LongTermMemoryGate._known_snapshot()` 的增量分支只看三条「更多」门槛（节点数增 ∧
max(rowid) 增 ∧ 正文总长增），**不证明没有删除**——删 1 增 2 时三项全增 ⇒ 增量
并入，被删节点的 core grams 永远留在参考集里。后果：被遗忘的内容再出现时
`_novelty` 算成 0.0（新建 gate 为 1.0），`evaluate` 从 knowledge 掉到 context。

本件锁住的判据（长驻 gate 与新建 gate 对同一库状态必须给出同一读数）：
  ① 删 1 增 2（issue 原始复现）：novelty / layer / union 与新建 gate 一致；
  ② 纯追加仍走增量（`per_node` 同一对象）——修复不得把缓存效果一并废掉；
  ③ 删「最大 rowid」的行再增 2（SQLite 复用 rowid 的路径）：仍须一致；
  ④ 同 id 覆盖写（INSERT OR REPLACE 换 rowid）再增 1：仍须一致。
定点变异：删掉 `_known_snapshot` 的两道「无删除」证明 ⇒ ①②③④ 中 ①③（及④）
的 novelty 断言必红。
"""
import warnings

import pytest

warnings.filterwarnings("ignore")

from lingshu.core.core import SpacetimeMemoryEngine
from lingshu.core.longterm_gate import LongTermMemoryGate

TARGET = "量子纠缠实验在液氦温区观测到超导涡旋"
VICTIM_MAX = "光合作用暗反应里 Rubisco 的羧化方向"
OLD_CONTENT = "海岸线侵蚀速率的长期观测方法"


def _engine():
    return SpacetimeMemoryEngine(":memory:")


def _fresh(engine):
    return LongTermMemoryGate(engine)


def _snapshot(gate):
    return gate._known_snapshot()


# ---------------------------------------------------------------- ① 原始复现

def test_delete_one_then_add_two_matches_fresh_gate():
    e = _engine()
    st = e.store
    vid = e.add_perception(content=TARGET, modality="text").id
    e.add_perception(content="今天下午天气晴朗适合散步", modality="text")
    gate = e._ensure_gate()
    assert gate._novelty(TARGET) == 0.0, "探针有效性：目标已在库时应为 0"

    st.delete_node(vid)
    e.add_perception(content="厨房水龙头需要更换垫圈", modality="text")
    e.add_perception(content="周末计划去图书馆还书", modality="text")

    fresh = _fresh(e)
    assert gate._novelty(TARGET) == fresh._novelty(TARGET), (
        "长驻 gate 与新建 gate 对新奇度读数不一致（#462）：%r vs %r"
        % (gate._novelty(TARGET), fresh._novelty(TARGET)))
    assert gate._novelty(TARGET) == 1.0, "被遗忘内容再出现时必须判为新"
    assert gate.evaluate(TARGET)["layer"] == fresh.evaluate(TARGET)["layer"] == "knowledge"
    assert _snapshot(gate)[0] == _snapshot(fresh)[0], "全库 gram 并集必须与新建 gate 一致"


# ---------------------------------------------------------------- ② 增量不废

def test_pure_append_still_uses_incremental_path():
    e = _engine()
    e.add_perception(content="第一条情境记录", modality="text")
    gate = e._ensure_gate()
    u1, c1, per_node_1, sig1 = _snapshot(gate)
    e.add_perception(content="第二条情境记录", modality="text")
    u2, c2, per_node_2, sig2 = _snapshot(gate)
    assert per_node_2 is per_node_1, (
        "纯追加仍应走增量（复用同一 per_node 对象）；若这里重建，说明修复把缓存废掉了")
    assert sig2[0] == sig1[0] + 1


# ---------------------------------------------------------------- ③ rowid 复用

def test_delete_max_rowid_then_add_two_matches_fresh_gate():
    e = _engine()
    st = e.store
    e.add_perception(content="一条较早的记录", modality="text")
    victim = e.add_perception(content=VICTIM_MAX, modality="text")
    # victim 是最大 rowid：删除后 SQLite 会复用该 rowid，计数平衡证明会被骗过，
    # 必须靠「旧 id 一个不少」兜住。
    gate = e._ensure_gate()
    gate._novelty(VICTIM_MAX)
    assert st.delete_node(victim.id) is True
    # 新增正文总长必须超过被删正文（24 字），否则「正文总长增」门槛先挡下、
    # 走不到增量分支——那样本用例测不到 rowid 复用这条洞。
    e.add_perception(content="清晨的雾气很重，远处的山脊线在薄雾里若隐若现", modality="text")
    e.add_perception(content="傍晚整理书桌时翻出了三年前的旧笔记本", modality="text")

    fresh = _fresh(e)
    assert gate._novelty(VICTIM_MAX) == fresh._novelty(VICTIM_MAX) == 1.0, (
        "rowid 复用路径下缓存仍留有被删节点的词：%r vs %r"
        % (gate._novelty(VICTIM_MAX), fresh._novelty(VICTIM_MAX)))
    assert _snapshot(gate)[0] == _snapshot(fresh)[0]


# ---------------------------------------------------------------- ④ 同 id 覆盖写

def test_same_id_overwrite_then_append_matches_fresh_gate():
    e = _engine()
    st = e.store
    nid = e.add_perception(content=OLD_CONTENT, modality="text").id
    gate = e._ensure_gate()
    gate._novelty(OLD_CONTENT)          # 建缓存（旧正文在参考集里）
    node = st.get_node(nid)
    node.content = "阳台上的薄荷在午后阳光下散发出清凉的气味"   # 完整替换，非追加子串
    st.add_node(node)                   # INSERT OR REPLACE：换 rowid 的新内容版本
    e.add_perception(content="今天给绿萝换了花盆", modality="text")

    fresh = _fresh(e)
    assert gate._novelty(OLD_CONTENT) == fresh._novelty(OLD_CONTENT) == 1.0, (
        "覆盖写后旧正文已不在库中，长驻 gate 不得仍把它算作已知：%r vs %r"
        % (gate._novelty(OLD_CONTENT), fresh._novelty(OLD_CONTENT)))
    assert _snapshot(gate)[0] == _snapshot(fresh)[0], (
        "同 id 覆盖写后全库 gram 并集必须与新建 gate 一致（不得残留旧 gram）")


# ---------------------------------------------------------------- 空库与清空

def test_delete_all_then_add_matches_fresh_gate():
    e = _engine()
    st = e.store
    e.add_perception(content=TARGET, modality="text")
    e.add_perception(content=OLD_CONTENT, modality="text")
    gate = e._ensure_gate()
    gate._novelty(TARGET)
    for n in list(st.query_nodes(limit=50)):
        st.delete_node(n.id)
    e.add_perception(content="清空后写入的一条新记录", modality="text")
    fresh = _fresh(e)
    assert gate._novelty(TARGET) == fresh._novelty(TARGET) == 1.0
    assert _snapshot(gate)[0] == _snapshot(fresh)[0]
