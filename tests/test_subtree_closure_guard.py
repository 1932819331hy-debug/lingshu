"""旧子树枚举必须闭合；查询预算不能被当成完整删除范围。"""
import pytest

from lingshu.core.core import EdgeType, SpacetimeMemoryEngine


pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    for name in ("LINGSHU_FTS", "LINGSHU_TMS", "LINGSHU_EVIDENCE_LEDGER"):
        monkeypatch.delenv(name, raising=False)
    engine = SpacetimeMemoryEngine(str(tmp_path / "closure.db"))
    try:
        yield engine
    finally:
        engine.close()


def _tree(engine, count, *, chain=False):
    """只走生产写入入口，在一个事务里快速建图；旧根须在父范围查询内。"""
    with engine.store._tx():
        parent = engine.add_perception("parent", skip_dedup=True)
        old = engine.add_perception("old root", importance=1.0, skip_dedup=True)
        engine.add_edge(old.id, parent.id, relation_type=EdgeType.HIERARCHICAL)
        ids, target = [], old.id
        for i in range(count):
            child = engine.add_perception(f"old child {i}", skip_dedup=True)
            engine.add_edge(child.id, target, relation_type=EdgeType.HIERARCHICAL)
            ids.append(child.id)
            if chain:
                target = child.id
    return parent.id, old.id, ids


def _snapshot(engine):
    conn = engine.store.conn
    return {table: sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))
            for table in ("nodes", "edges", "protections", "action_logs", "archive_state")}


def _replace(engine, parent, old):
    return engine.subgraph_replace(parent, old, {"id": "new", "content": {"shape": "new"}})


def _assert_incomplete_rejected(engine, parent, old):
    before = _snapshot(engine)
    interactions = engine._interaction_count
    rejected = False
    try:
        _replace(engine, parent, old)
    except ValueError as exc:
        rejected = "旧子树不完整" in str(exc)
    assert rejected, "incomplete subtree was replaced instead of rejected"
    assert _snapshot(engine) == before, "rejection changed the stored graph"
    assert engine._interaction_count == interactions, "rejection started attaching new nodes"


def test_fanout_beyond_500_is_rejected_without_any_writes(engine):
    parent, old, _ = _tree(engine, 501)
    _assert_incomplete_rejected(engine, parent, old)


def test_budget_hidden_protected_grandchild_keeps_the_whole_tree(engine):
    parent, old, children = _tree(engine, 500)
    with engine.store._tx():
        hidden = engine.add_perception("protected grandchild", skip_dedup=True)
        engine.add_edge(hidden.id, children[-1], relation_type=EdgeType.HIERARCHICAL)
        engine.store.protect_node(hidden.id, "retain this descendant")
    _assert_incomplete_rejected(engine, parent, old)
    assert hidden.id in engine.store.get_protected_nodes()


def test_depth_65_is_rejected_and_all_ancestor_edges_survive(engine):
    parent, old, _ = _tree(engine, 65, chain=True)
    _assert_incomplete_rejected(engine, parent, old)


@pytest.mark.parametrize("count,chain", [(500, False), (64, True)])
def test_exact_boundary_complete_subtree_is_still_replaceable(engine, count, chain):
    parent, old, children = _tree(engine, count, chain=chain)
    result = _replace(engine, parent, old)
    assert result["removed_nodes"] == count + 1
    assert engine.store.get_node(parent) is not None
    assert engine.store.get_node(result["new_root_id"]) is not None
    assert all(engine.store.get_node(nid) is None for nid in [old, *children])
    assert engine.verify_integrity()["integrity_ok"]


def test_unrelated_edges_do_not_make_the_hierarchy_incomplete(engine):
    parent, old, children = _tree(engine, 3)
    other = engine.add_perception("causal peer", skip_dedup=True)
    engine.add_edge(other.id, children[-1], relation_type=EdgeType.CAUSAL)
    result = _replace(engine, parent, old)
    assert result["removed_nodes"] == 4
    assert engine.store.get_node(other.id) is not None
    assert engine.verify_integrity()["integrity_ok"]
