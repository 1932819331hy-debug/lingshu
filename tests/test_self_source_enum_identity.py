"""Sample identity must preserve real evaluator membership and measured ownership."""
import numpy as np
import pytest

from lingshu.gen import hexgen_self_source as S
from lingshu.gen import hexgen_c1_real as C


def _small_batch(repeats, recipes=None):
    # Bound memory only. Every image still comes from the production renderer.
    render = S.render_self
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(S, "render_self", lambda tup: render(tup, size=48))
        if recipes is not None:
            patch.setattr(S, "enumerate_tuples", lambda: recipes)
        return S.build_enum_items(repeats=repeats)


@pytest.fixture(scope="module")
def items11():
    items = _small_batch(11)
    assert len(items) == 297
    return items


def test_eleven_repeats_keep_each_rendered_sample(items11):
    assert len({it["file"] for it in items11}) == 297
    assert len({it["id"] for it in items11}) == 297, "rendered samples share an evaluation ID"


def test_large_repeat_count_does_not_use_a_fixed_id_stride():
    # Two real recipes suffice to distinguish an ID fix from a larger constant.
    items = _small_batch(101, recipes=S.enumerate_tuples()[:2])
    assert len(items) == 202
    assert len({it["id"] for it in items}) == 202, "large-repeat evaluation IDs collided"


def test_recipe_split_also_separates_evaluation_ids(items11):
    cal, hold = S.split_self(items11)
    assert {it["sid"] for it in cal}.isdisjoint({it["sid"] for it in hold})
    assert {it["id"] for it in cal}.isdisjoint({it["id"] for it in hold}), "evaluation IDs cross the recipe split"
    assert (len(cal), len(hold)) == (165, 132)


def test_real_evaluator_selects_exactly_the_split_members(items11):
    cal, hold = S.split_self(items11)
    # Empty observations deliberately isolate membership, not model accuracy.
    measured = {"items": items11, "by_id": {it["id"]: [] for it in items11}, "d": 4}
    for side in (cal, hold):
        result = C.evaluate(measured, {}, {it["id"] for it in side})
        assert result["n_img"] == len(side), "evaluator selected images from the other split"
        assert result["count"][1] == len(side)


def test_real_measurements_keep_their_recipe_owner(items11):
    by_file = {it["file"]: it for it in items11}
    first = by_file["self://enum/0/10"]
    second = by_file["self://enum/1/0"]
    assert not np.array_equal(first["img"], second["img"])
    first_obs = C.measure_item(first, tol=C.TOL, d=1, erode_r=0)[0]
    second_obs = C.measure_item(second, tol=C.TOL, d=1, erode_r=0)[0]
    assert first_obs and second_obs, "measurement fixture must contain detected objects"
    assert not np.allclose(first_obs[0]["core_rgb"], second_obs[0]["core_rgb"])
    by_id = {first["id"]: first_obs, second["id"]: second_obs}
    assert by_id[first["id"]] is first_obs, "another recipe overwrote the measured observation"
    assert by_id[second["id"]] is second_obs


@pytest.mark.parametrize("repeats", [1, 3, 10], ids=["default", "three", "ten"])
def test_existing_small_repeat_identities_are_unchanged(repeats):
    items = _small_batch(repeats)
    by_file = {it["file"]: it for it in items}
    # Frozen legacy public IDs, including a later recipe; no new formula oracle.
    expected = {"self://enum/0/0": 0, "self://enum/1/0": 10, "self://enum/26/0": 260}
    if repeats == 10:
        expected["self://enum/26/9"] = 269
    for filename, item_id in expected.items():
        assert by_file[filename]["id"] == item_id, "small-repeat identities changed"
    assert len(items) == 27 * repeats
    assert all(isinstance(it["id"], int) and it["seed"] < repeats for it in items)


def test_empty_repeat_requests_keep_existing_empty_result():
    assert S.build_enum_items(repeats=0) == []
    assert S.build_enum_items(repeats=-1) == []
