"""#332: finite confident mistakes must retain the exact CE correction signal."""
import numpy as np
import pytest
from lingshu.nn.hex_hier import HexHierNet, OBJ, ce_loss, train_hier

INDEX = {name: i for i, name in enumerate(OBJ)}


def reference(z):
    maximum = np.max(z, axis=-1)
    return maximum + np.log(np.exp(z - maximum[..., None]).sum(axis=-1))


@pytest.mark.parametrize('value', [20., 50., 100., 1000.])
def test_confident_wrong_logits_do_not_saturate(value):
    z = np.array([[-value, value] + [0.] * 7])
    actual = ce_loss(z, np.array([OBJ[0]]), INDEX)
    expected = float(reference(z)[0] - z[0, 0])
    assert actual == pytest.approx(expected, abs=1e-10), 'cross entropy saturated instead of tracking the wrong-logit gap'


def test_common_logit_offset_does_not_change_loss():
    z = np.array([[-100., 100.] + [0.] * 7])
    assert ce_loss(z + 12345., [OBJ[0]], INDEX) == pytest.approx(ce_loss(z, [OBJ[0]], INDEX))


def test_ordinary_range_matches_probability_definition():
    z = np.array([[.1, .2, .3, -.4, .5, -.6, .7, -.8, .9]])
    p = np.exp(z) / np.exp(z).sum(axis=1, keepdims=True)
    assert ce_loss(z, [OBJ[4]], INDEX) == pytest.approx(float(-np.log(p[0, 4])), abs=1e-10)


def test_confident_correct_prediction_has_nonnegative_loss():
    z = np.array([[1000., -1000.] + [0.] * 7])
    assert ce_loss(z, [OBJ[0]], INDEX) >= 0., 'perfect prediction produced a negative cross entropy'


def task(wrong_class=1):
    net = HexHierNet(n_kernels=1, stacked=False)
    net.head[:] = 0.
    net.head[0, :] = -100.
    net.head[wrong_class, :] = 100.
    lat = np.ones((2, 4, 4, 3))
    labels = {'shape': np.array(['circle']*2), 'obj': np.array(['circle|red']*2)}
    return net, lat, labels


def test_real_training_updates_a_saturated_wrong_object():
    net, lat, labels = task()
    before = net.get_vec().copy()
    sf, cf = net.l2_features(lat)
    initial = float((reference(net.l3_logits(sf, cf)) - net.l3_logits(sf, cf)[:, 0]).mean())
    train_hier(net, lat, labels, steps=3, samples_per_step=net.n_params(), batch=2)
    sf, cf = net.l2_features(lat)
    z = net.l3_logits(sf, cf)
    final = float((reference(z) - z[:, 0]).mean())
    assert np.count_nonzero(before != net.get_vec()) > 0, 'finite confident wrong prediction could not update any parameter'
    assert final < initial, 'training did not reduce the true object objective'


def test_shape_auxiliary_loss_retains_low_probability_group():
    net, lat, labels = task(wrong_class=3)
    sf, cf = net.l2_features(lat)
    z = net.l3_logits(sf, cf)
    d_obj = (reference(z) - z[:, 0]).mean()
    d_shape = (reference(z) - reference(z[:, :3])).mean()
    result = train_hier(net, lat, labels, steps=1, samples_per_step=0, batch=2)
    assert result['init_D'] == pytest.approx(round(float(d_obj + .5*d_shape), 4), abs=1e-4), 'shape auxiliary objective was clipped or grouped incorrectly'
