"""新身份分配不能消费已存在或本帧显式预留的身份。"""
from lingshu.world.world_model import UnifiedWorldModel


def _observation(category="person", pos=(2, 1.5, 2), eid=None):
    value = {"category": category, "pos": pos}
    if eid is not None:
        value["eid"] = eid
    return value


class _ZeroChoices:
    """仅为重试耗尽腿构造必然碰撞，不替换身份方法或观测写入。"""
    def choice(self, _):
        return "0"


def test_anonymous_allocation_cannot_consume_a_later_explicit_id():
    reference = UnifiedWorldModel(seed=42)
    explicit = reference.perceive([_observation()])["observations"][0]["eid"]
    model = UnifiedWorldModel(seed=42)
    result = model.perceive([_observation(), _observation(pos=(8, 1.5, 8), eid=explicit)])
    ids = [o["eid"] for o in result["observations"]]
    assert len(set(ids)) == 2, "anonymous allocation consumed a reserved explicit ID"
    assert len(model.nodes) == 2 and result["new"] == 2 and result["matched"] == 0
    assert model.nodes[explicit].pos == (8, 1.5, 8)
    assert model.nodes[ids[0]].pos == (2, 1.5, 2)


def test_long_fallback_cannot_overwrite_an_existing_entity():
    model = UnifiedWorldModel()
    model.perceive([_observation("old-a", eid="wm_000000"),
                    _observation("old-b", (4, 1.5, 4), "wm_000000000000")])
    model._rng = _ZeroChoices()
    result = model.perceive([_observation("new", (8, 1.5, 8))])
    assert len(model.nodes) == 3, "long fallback ID overwrote an existing entity"
    assert result["observations"][0]["eid"] not in {"wm_000000", "wm_000000000000"}
    assert model.nodes["wm_000000000000"].pos == (4, 1.5, 4)


def test_long_fallback_cannot_consume_a_later_explicit_id():
    model = UnifiedWorldModel()
    model.perceive([_observation("old", eid="wm_000000")])
    model._rng = _ZeroChoices()
    result = model.perceive([_observation("new", (5, 1.5, 5)),
        _observation("incoming", (8, 1.5, 8), "wm_000000000000")])
    assert len(model.nodes) == 3, "long fallback consumed a reserved explicit ID"
    assert len({o["eid"] for o in result["observations"]}) == 2
    assert model.nodes["wm_000000000000"].pos == (8, 1.5, 8)


def test_short_retry_and_unused_long_fallback_still_allocate_normally():
    model = UnifiedWorldModel()
    model._rng = _ZeroChoices()
    result = model.perceive([_observation("explicit", eid="wm_000000"),
                            _observation("anonymous", (8, 1.5, 8))])
    assert len(model.nodes) == 2
    assert result["observations"][1]["eid"] == "wm_000000000000"


def test_nearby_anonymous_observation_still_reuses_its_existing_track():
    model = UnifiedWorldModel(seed=7)
    first = model.perceive([_observation()])["observations"][0]["eid"]
    result = model.perceive([_observation(pos=(2.1, 1.5, 2))])
    assert result["observations"][0]["eid"] == first
    assert result["matched"] == 1 and len(model.nodes) == 1


def test_distinct_explicit_ids_and_positions_are_preserved():
    model = UnifiedWorldModel()
    result = model.perceive([_observation(eid="first"), _observation(pos=(8, 1.5, 8), eid="second")])
    assert [o["eid"] for o in result["observations"]] == ["first", "second"]
    assert set(model.nodes) == {"first", "second"}
    assert model.nodes["first"].pos == (2, 1.5, 2)
    assert model.nodes["second"].pos == (8, 1.5, 8)
