"""Real checkpoint handoff: metadata cannot erase seven-weight learned kernels."""
import json
from pathlib import Path

import numpy as np
import pytest

from lingshu.nn import hex_cnn as H


def _rejects(call, expected, failure):
    try:
        call()
    except ValueError as exc:
        assert expected in str(exc), (failure, str(exc))
    else:
        assert False, failure


def test_metadata_cannot_replace_learned_checkpoint(tmp_path):
    path = tmp_path / "learned.json"
    weights = np.array([0.3, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2])
    returned = H.save_consolidated({"learned": weights},
        {"kernels": {}, "consolidated": False, "algo": "stale", "source": "sensor"}, str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload["kernels"]) == {"learned"}, "metadata erased learned kernel"
    assert payload["consolidated"] is True and payload["algo"] == H.ALGO
    assert payload["source"] == "sensor" and returned == str(path)
    restored = H.load_consolidated(str(path))["learned"]
    field = np.ones((3, 4, 2), dtype=np.float32)
    np.testing.assert_allclose(H.hex_conv(field, restored), weights.sum(), atol=1e-6)


def test_asset_and_normal_checkpoint_still_roundtrip(tmp_path):
    asset = Path(H.__file__).parent / "assets/kernels_img0.json"
    kernels = H.load_consolidated(str(asset))
    assert kernels and all(k.shape == (7,) and k.dtype == np.float32 for k in kernels.values())
    path = tmp_path / "normal.json"
    H.save_consolidated(kernels, {"epochs": 3}, str(path))
    restored = H.load_consolidated(str(path))
    assert restored.keys() == kernels.keys()
    for name in kernels:
        np.testing.assert_allclose(restored[name], kernels[name], atol=5.1e-7, rtol=0)


@pytest.mark.parametrize("values", [[0.1] * 6, [0.1] * 8, [[0.1] * 7]], ids=["six", "eight", "matrix"])
def test_load_rejects_wrong_weight_shape(tmp_path, values):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"consolidated": True, "kernels": {"broken": values}}), encoding="utf-8")
    _rejects(lambda: H.load_consolidated(str(path)), "broken", "load accepted malformed seven-weight kernel")


@pytest.mark.parametrize("payload", [
    {"consolidated": False, "kernels": {}},
    {"kernels": {}},
    {"consolidated": True, "kernels": []},
    [],
], ids=["false", "missing", "kernel_list", "root_list"])
def test_load_rejects_invalid_checkpoint_envelope(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        H.load_consolidated(str(path))
    except ValueError as exc:
        assert "consolidated" in str(exc)
    except (AttributeError, TypeError) as exc:
        assert False, "invalid envelope escaped as " + type(exc).__name__
    else:
        assert False, "load accepted invalid checkpoint envelope"


@pytest.mark.parametrize("values", [[0.1] * 6, [0.1] * 8, [float("nan")] * 7, [1e40] * 7],
                         ids=["six", "eight", "nan", "float32_overflow"])
def test_bad_save_preserves_old_bytes_before_any_io(tmp_path, values):
    path = tmp_path / "old.json"
    # Seed independently, so mutants cannot fail while constructing the fixture.
    original = b'{"consolidated":true,"kernels":{"old":[1,0,0,0,0,0,0]}}'
    path.write_bytes(original)
    _rejects(lambda: H.save_consolidated({"broken": values}, {}, str(path)),
             "broken", "invalid save replaced good checkpoint")
    assert path.read_bytes() == original, "invalid save changed old checkpoint bytes"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["old.json"]
    missing = tmp_path / "not-created" / "new.json"
    _rejects(lambda: H.save_consolidated({"broken": values}, {}, str(missing)),
             "broken", "invalid save created a destination")
    assert not missing.parent.exists(), "validation happened after creating directories"


@pytest.mark.parametrize("values", [np.ones(6), np.ones(8), np.ones((2, 7))],
                         ids=["six", "eight", "wrong_channels"])
def test_direct_convolution_rejects_incompatible_shape(values):
    field = np.ones((3, 4, 3), dtype=np.float32)
    try:
        H.hex_conv(field, values)
    except ValueError as exc:
        assert "kernel7" in str(exc), "convolution failed late instead of validating shape"
    except IndexError:
        assert False, "convolution indexed a malformed kernel"
    else:
        assert False, "convolution silently ignored extra kernel weights"


def test_load_rejects_nonfinite_persisted_weights(tmp_path):
    path = tmp_path / "nan.json"
    path.write_text(json.dumps({"consolidated": True, "kernels": {"broken": [float("inf")] * 7}}), encoding="utf-8")
    _rejects(lambda: H.load_consolidated(str(path)), "broken", "load accepted nonfinite kernel")


def test_shared_and_per_channel_convolution_keep_analytic_response():
    field = np.ones((4, 5, 3), dtype=np.float32)
    shared = np.array([1., 0., 0., 0., 0., 0., 0.])
    np.testing.assert_array_equal(H.hex_conv(field, shared), field)
    per_channel = np.stack([shared, shared * 2, shared * -3])
    np.testing.assert_array_equal(H.hex_conv(field, per_channel), field * np.array([1, 2, -3]))
