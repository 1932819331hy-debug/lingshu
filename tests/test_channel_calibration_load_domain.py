"""Malformed persisted calibration must not poison registry() or healthy neighbors."""
import json
import math
import pytest
from lingshu.world.channel_credibility import ChannelCredibilityRegistry, PRIOR_A, PRIOR_B


def load(tmp_path, data):
    path = tmp_path / 'calibration.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    old = path.read_bytes()
    reg = ChannelCredibilityRegistry(str(path))
    assert path.read_bytes() == old, 'loading rewrote the original calibration file'
    return reg, path


@pytest.mark.parametrize('data', [[], None, 42])
def test_non_object_file_root_keeps_prior(tmp_path, data):
    try:
        reg, _ = load(tmp_path, data)
        states = reg.registry()
    except Exception as exc:
        pytest.fail('invalid calibration root escaped recovery: ' + type(exc).__name__)
    assert all(s['a'] == PRIOR_A and s['b'] == PRIOR_B for s in states.values())


@pytest.mark.parametrize('field,value', [
    ('hits', float('nan')), ('misses', float('inf')), ('hits', -1),
    ('a', 0), ('b', -9), ('a', 10**400),
], ids=['nan_hits','inf_misses','negative_hits','zero_beta','negative_beta','overflow_integer'])
def test_bad_channel_resets_whole_calibration_without_losing_neighbor(tmp_path, field, value):
    visual = {'a':10., 'b':2., 'hits':4., 'misses':1.}
    visual[field] = value
    data = {'visual':visual, 'audio':{'a':7.,'b':3.,'hits':5.,'misses':2.}}
    try:
        reg, _ = load(tmp_path, data)
        states = reg.registry()
    except Exception as exc:
        pytest.fail('invalid channel escaped recovery: ' + type(exc).__name__)
    bad = states['visual']
    assert (bad['a'], bad['b'], bad['hits'], bad['misses']) == (PRIOR_A, PRIOR_B, 0, 0), 'invalid calibration was not reset as one complete channel'
    assert states['audio']['a'] == 7. and states['audio']['hits'] == 5, 'healthy neighbor was lost during calibration recovery'
    assert all(math.isfinite(s['credibility']) and 0 <= s['credibility'] <= 1 for s in states.values())


def test_overflowed_beta_total_is_rejected(tmp_path):
    reg, _ = load(tmp_path, {'visual':{'a':1e308,'b':1e308,'hits':4,'misses':1}})
    st = reg.channel_state('visual')
    assert (st['a'],st['b'],st['hits'],st['misses']) == (PRIOR_A, PRIOR_B, 0, 0), 'overflowed Beta total survived loading'


def test_healthy_values_and_unknown_channel_policy_are_preserved(tmp_path):
    reg, _ = load(tmp_path, {'visual':{'a':8.,'b':2.,'hits':3,'misses':1},'typo':{'a':99,'b':1}})
    st = reg.channel_state('visual')
    assert st['credibility'] == .8 and st['hits'] == 3
    assert not reg.channel_state('typo')['known']


def test_recovery_then_real_update_saves_a_reloadable_registry(tmp_path):
    reg, path = load(tmp_path, {'visual':{'a':10,'b':2,'hits':float('nan')},'audio':{'a':7,'b':3,'hits':5,'misses':2}})
    try:
        reg.record_hit('visual', strong=True)
        reg.record_miss('audio', conf=.5)
        states = reg.registry()
        reopened = ChannelCredibilityRegistry(str(path))
        assert reopened.registry() == states
    except Exception as exc:
        pytest.fail('recovered registry could not update and reopen: ' + type(exc).__name__)
    assert states['visual']['hits'] == 1
    assert states['audio']['hits'] == 5 and states['audio']['misses'] == 3
