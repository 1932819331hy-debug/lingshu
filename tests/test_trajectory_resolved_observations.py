"""运行中的真实观测必须进入轨迹、配对误差和旋转派生视图。"""
from copy import deepcopy

from lingshu.world import world_model as module
from lingshu.world.world_model import UnifiedWorldModel


def _anonymous(pos=(2, 1.5, 2), **extra):
    return {"category": "person", "pos": pos, **extra}


def _history_projection(model):
    result = {}
    for frame in model.history:
        for eid, pos in frame["entities"].items():
            result.setdefault(eid, []).append((frame["tick"], tuple(pos)))
    return {eid: rows[-256:] for eid, rows in result.items()}


def test_anonymous_real_observations_reach_the_trajectory_buffer():
    model = UnifiedWorldModel(seed=7)
    for x in (2, 2.2, 2.4):
        model.perceive([_anonymous((x, 1.5, 2))])
    assert model._traj_buf == _history_projection(model), "anonymous observations missing from trajectory buffer"
    assert sum(len(v) for v in model._traj_buf.values()) == 3


def test_default_sensor_observations_reach_the_trajectory_buffer():
    model = UnifiedWorldModel()
    eid = model.world.add_entity("person", pos=(2, 1.5, 2), speed=0)
    for i, x in enumerate((2, 2.2, 2.4)):
        model.world.entities[eid].pos = (x, 1.5, 2)
        model.perceive(tick=20+i, timestamp=100+i)
    assert model._traj_buf == _history_projection(model), "default sensor observations missing from trajectory buffer"
    assert [t for t, _ in model._traj_buf[eid]] == [20, 21, 22]


def test_default_sensor_can_support_smooth_motion_after_a_random_prefix():
    model = UnifiedWorldModel()
    eid = model.world.add_entity("person", pos=(2, 1.5, 2), speed=0)
    for offset in (0, 4, 0, 4, 0, 0.1, 0.4, 0.9, 1.6):
        model.world.entities[eid].pos = (2 + offset, 1.5, 2)
        model.perceive()
    prediction = model.generate()["predictions"][eid]
    assert prediction["mode_source"] == "bounded_stochastic"
    assert prediction["mode"] == "smooth", "validated smooth suffix missing from sensor prediction"
    assert abs(prediction["predicted"][0] - 4.5) < 1e-9


def test_default_sensor_random_motion_keeps_its_stochastic_bound():
    model = UnifiedWorldModel()
    eid = model.world.add_entity("person", pos=(2, 1.5, 2), speed=0)
    for offset in (0, 4, 0, 4, 0, 0.1, 0.4, 0.9, 1.1):
        model.world.entities[eid].pos = (2 + offset, 1.5, 2)
        model.perceive()
    prediction = model.generate()["predictions"][eid]
    assert prediction["mode"] == "bounded_stochastic", "random sensor motion was promoted to smooth"
    assert tuple(prediction["predicted"]) == model.nodes[eid].pos
    assert prediction["bound"] > model.hit_threshold


def test_anonymous_prediction_errors_reach_the_paired_guard():
    model = UnifiedWorldModel()
    for _ in range(10):
        model.perceive([_anonymous()])
    eid = next(iter(model.nodes))
    prediction = model.generate()["predictions"][eid]
    assert prediction["mode"] == "static", "anonymous history never formed a static prediction"
    model.perceive([_anonymous()])
    pairs = getattr(model, "_err_ovr", None) or {}
    assert pairs.get(eid) == [(0.0, 0.0)], "resolved observation missing from paired guard"
    assert model._err_model[eid] == [0.0]
    assert len(model._err_persist[eid]) == 10


def test_anonymous_quaternions_reach_the_resolved_rotation_track(monkeypatch):
    monkeypatch.setitem(module._ROT, "enabled", True)
    model = UnifiedWorldModel()
    for _ in range(3):
        model.perceive([_anonymous(rot_quat=(1, 0, 0, 0))])
    eid = next(iter(model.nodes))
    rotations = getattr(model, "_rot_buf", None) or {}
    assert len(rotations.get(eid, [])) == 3, "resolved observation missing from rotation buffer"
    assert [t for t, _ in rotations[eid]] == [1, 2, 3]


def test_explicit_ids_ticks_and_timestamps_keep_their_existing_projection():
    model = UnifiedWorldModel()
    for tick, timestamp, x in ((10, 1.0, 2), (15, 1.5, 2.5)):
        out = model.perceive([_anonymous((x, 1.5, 2), eid="explicit")], tick=tick, timestamp=timestamp)
        assert out["tick"] == tick
        assert model.history[-1]["timestamp"] == timestamp
    assert model._traj_buf == {"explicit": [(10, (2, 1.5, 2)), (15, (2.5, 1.5, 2))]}


def test_empty_observation_frame_does_not_reinsert_unseen_entities():
    model = UnifiedWorldModel()
    model.perceive([_anonymous()])
    before = deepcopy(model._traj_buf)
    model.perceive([])
    assert model._traj_buf == before
    assert model.history[-1]["entities"] == {}
