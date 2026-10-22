"""本地文件收据经过真实世界恢复入口，失败时不得半替换活动运行态。"""
from copy import deepcopy
import json

import pytest

from lingshu.world.wm_simloop import SimulationLoop


class ReceiptStore:
    """仅提供 read_content 文件协议；不是在线脑端或 MCP 验收。"""
    def __init__(self, path, state):
        self.path = path
        path.write_text("\n```lingshu-world\n" + json.dumps(state) + "\n```", encoding="utf-8")

    def read_content(self, memory_id):
        assert memory_id == "receipt"
        return self.path.read_text(encoding="utf-8")


def _source():
    loop = SimulationLoop(seed=3)
    loop.scene.add_entity("new", pos=(2, 1.5, 2), speed=0)
    loop.scene.add_entity("other", pos=(6, 1.5, 6), speed=0)
    loop.run(3)
    content = loop.flush_payloads()["payload"]["body_md"]
    return json.loads(content.partition("\n```lingshu-world\n")[2].partition("\n```")[0])


def _target():
    loop = SimulationLoop(seed=9)
    keep = loop.scene.add_entity("existing", pos=(5, 1.5, 5), speed=0)
    loop.run(2)
    loop.wm.generate()
    loop._hypotheses = {keep: {"node": keep, "phase": "testing"}}
    loop._rolling_rates = [0.25]
    return loop


def _snapshot(loop):
    values = {key: deepcopy(value) for key, value in vars(loop.wm).items()
        if type(value) in (dict, list, tuple, str, int, float, bool, type(None))}
    return {"model_id": id(loop.wm), "values": values, "rng": loop.wm._rng.getstate(),
        "scene": deepcopy(loop.scene.entities), "hypotheses": deepcopy(loop._hypotheses),
        "rates": list(loop._rolling_rates), "wal": deepcopy(loop.wal), "seq": loop._seq}


@pytest.mark.parametrize("broken", ["missing_motion", "bad_edge", "bad_history", "bad_hypothesis", "missing_rates"])
def test_failed_restore_preserves_the_entire_running_state(tmp_path, broken):
    state = _source()
    first = next(iter(state["nodes"]))
    if broken == "missing_motion":
        del state["motion_estimates"]
    elif broken == "bad_edge":
        state["edges"].append({"source": first})
    elif broken == "bad_history":
        state["history"][0]["entities"][first] = ["invalid-position", 0, 0]
    elif broken == "bad_hypothesis":
        state["hypotheses"] = {first: {"phase": "confirmed"}}
    else:
        del state["rolling_rates"]
    target = _target()
    before = _snapshot(target)
    with pytest.raises((KeyError, TypeError, ValueError)):
        target.load_priors(brain=ReceiptStore(tmp_path / "receipt.md", state), memory_id="receipt")
    assert _snapshot(target) == before, "failed prior restore changed running state"


def test_invalid_first_stage_nodes_already_leave_the_world_untouched(tmp_path):
    state = _source()
    state["nodes"]["bad-node"] = {"category": "invalid"}
    target = _target()
    before = _snapshot(target)
    with pytest.raises(KeyError):
        target.load_priors(brain=ReceiptStore(tmp_path / "receipt.md", state), memory_id="receipt")
    assert _snapshot(target) == before


def test_valid_restore_preserves_the_held_model_object_and_observation_boundary(tmp_path):
    state = _source()
    target = _target()
    held = target.wm
    result = target.load_priors(brain=ReceiptStore(tmp_path / "receipt.md", state), memory_id="receipt")
    assert target.wm is held, "restoration replaced the held model object"
    assert result["seeded"] == 2 and not result["truncated"]
    assert target.wm.tick == state["tick"] and target.wm.size == state["size"]
    assert target.wm.history == state["history"]
    assert set(target.wm.nodes) == set(state["nodes"])
    assert sum(map(len, target.wm._traj_buf.values())) == 6
    assert target.wm.verify()["total"] == 0
    assert set(target.wm.generate()["predictions"]) == set(state["nodes"])


def test_valid_limit_truncation_keeps_only_selected_history_and_relations(tmp_path):
    state = _source()
    target = _target()
    result = target.load_priors(brain=ReceiptStore(tmp_path / "receipt.md", state), memory_id="receipt", limit=1)
    assert result["seeded"] == 1 and result["truncated"]
    selected = set(target.wm.nodes)
    assert all(set(frame["entities"]) <= selected for frame in target.wm.history)
    assert all(edge.source in selected and edge.target in selected for edge in target.wm.edges)
    assert set(target.wm._traj_buf) == selected
