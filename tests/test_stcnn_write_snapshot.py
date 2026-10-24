"""#302 residual: recall aliases cannot serve as an independent write receipt."""
from lingshu.nn.stcnn import SpatiotemporalMemory


def memory():
    mem = SpatiotemporalMemory()
    mem.remember({'direction': 'right', 'speed': 2.0, 'trajectory_len': 4}, 'ball', 10)
    mem.remember({'direction': 'static', 'speed': 0.0}, 'room', 20)
    return mem


def test_returned_event_mutation_is_detected():
    mem = SpatiotemporalMemory()
    event = mem.remember({'direction': 'right'}, 'ball')
    event['direction'] = 'left'
    assert mem.verify_consistency() is False, 'modified returned event passed the write receipt check'


def test_recall_alias_mutation_is_detected():
    mem = memory()
    mem.recall({'label': 'ball'})[0]['speed'] = 300.0
    assert mem.verify_consistency() is False, 'modified recall alias passed the write receipt check'


def test_lost_event_is_detected_even_when_recall_matches_remaining_events():
    mem = memory()
    mem.events.pop()
    assert mem.verify_consistency() is False, 'lost event was accepted as complete memory'


def test_complete_fabricated_event_is_detected():
    mem = memory()
    mem.events.append(dict(mem.events[0], label='never observed'))
    assert mem.verify_consistency() is False, 'complete unwritten event was accepted'


def test_reordered_events_are_detected():
    mem = memory()
    mem.events.reverse()
    assert mem.verify_consistency() is False, 'reordered write history was accepted'


def test_nested_label_is_copied_independently():
    mem = SpatiotemporalMemory()
    label = {'name': ['observed']}
    mem.remember({'direction': 'right'}, label)
    label['name'].append('unwritten')
    assert mem.verify_consistency() is False, 'nested label alias changed the independent receipt'


def test_healthy_empty_and_repeated_events_remain_consistent():
    mem = SpatiotemporalMemory()
    assert mem.verify_consistency() is True
    for _ in range(3):
        mem.remember({'direction': 'right', 'period': 3}, 'ball', 5)
    assert mem.verify_consistency() is True
    assert len(mem.recall({'label': 'ball'})) == 3


def test_restoring_original_value_restores_consistency():
    mem = memory()
    event = mem.events[0]
    old = event['speed']
    event['speed'] = -3
    assert mem.verify_consistency() is False, 'temporary event mutation went undetected'
    event['speed'] = old
    assert mem.verify_consistency() is True
