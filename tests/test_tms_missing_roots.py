"""已删除前提无论在传播闭包内外都应按缺失节点计信念 0。"""
import pytest

from lingshu.core.core import SpacetimeMemoryEngine


pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.delenv("LINGSHU_EVIDENCE_LEDGER", raising=False)
    monkeypatch.delenv("LINGSHU_TMS", raising=False)
    e = SpacetimeMemoryEngine(str(tmp_path / "truth.db"))
    e.enable_tms()
    try:
        yield e
    finally:
        e.close()


def _fact(engine, text):
    return engine.add_perception(text, source="sensor:A", skip_dedup=True).id


def _conclusion(engine, text):
    return engine.add_perception(text, source="self:reason", self_generated=True, skip_dedup=True).id


def _deleted_premise(engine):
    p, c = _fact(engine, "source fact"), _conclusion(engine, "derived result")
    engine.add_justification(c, [p])
    assert engine.store.delete_node(p)
    return p, c


def test_deleted_root_cannot_recover_confidence_from_retained_evidence(engine):
    p, c = _deleted_premise(engine)
    retained = engine._evidence.entries(p)
    assert retained
    outcome = engine._tms.propagate([p])
    assert outcome[p] == 0.0, "deleted propagation root recovered stale evidence confidence"
    assert outcome[c] == 0.0
    assert engine._evidence.entries(p) == retained


def test_reopened_database_agrees_with_the_undermined_report(engine):
    p, c = _deleted_premise(engine)
    engine._tms.propagate([p])
    other = SpacetimeMemoryEngine(engine.store.db_path)
    try:
        assert other.store.get_node(c).confidence == 0.0, "persisted conclusion retained a deleted premise belief"
        report = engine.evidence_report(c)
        assert report["status"] == "undermined" and report["belief"] == 0.0
        assert report["undermined_by"] == [p]
    finally:
        other.close()


def test_missing_intermediate_cannot_transmit_corroborated_own_evidence(engine):
    root, middle, c = _fact(engine, "live source"), _fact(engine, "intermediate result"), _conclusion(engine, "final result")
    engine.add_evidence(middle, "independent:B")
    engine.add_justification(middle, [root])
    engine.add_justification(c, [middle])
    assert engine.evidence_report(middle)["status"] == "corroborated"
    assert engine.store.delete_node(middle)
    outcome = engine._tms.propagate([middle])
    assert outcome[c] == 0.0, "missing corroborated intermediate still supported its descendant"


def test_unknown_propagation_root_has_no_implicit_prior(engine):
    outcome = engine._tms.propagate(["never-created"])
    assert outcome == {"never-created": 0.0}, "unknown propagation roots received a prior"


def test_a_live_alternative_justification_still_supports_the_conclusion(engine):
    gone, live, c = _fact(engine, "old source"), _fact(engine, "live alternative"), _conclusion(engine, "disjunctive result")
    engine.add_justification(c, [gone])
    engine.add_justification(c, [live])
    before = engine.store.get_node(c).confidence
    assert engine.store.delete_node(gone)
    engine._tms.propagate([gone])
    assert engine.store.get_node(c).confidence == before


def test_independently_corroborated_live_conclusion_is_not_retracted(engine):
    p, c = _fact(engine, "old premise"), _conclusion(engine, "independent result")
    engine.add_justification(c, [p])
    engine.add_evidence(c, "independent:A")
    engine.add_evidence(c, "independent:B")
    before = engine.evidence_report(c)["belief"]
    assert engine.store.delete_node(p)
    engine._tms.propagate([p])
    report = engine.evidence_report(c)
    assert report["status"] == "corroborated" and report["belief"] == before
