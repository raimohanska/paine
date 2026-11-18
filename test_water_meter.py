"""Water commissioning, temporal gates, discovery and bounded diagnostics."""

import json
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from paine import mqtt_pressure, water_reader, water_store
from paine.app_config import load_gauges
from paine.water_state import decide, decode


P = [20, 10, 2, 83, 21, 79]  # 2108.28 m³


def test_offset_correction_requires_independent_anchor_and_both_offsets(monkeypatch):
    from paine import water_model
    rows = [[*P[:4], p, 79] for p in (40, 9, 10, 45)]
    audits = [{'template_applied': True}, {}, {}, {}]
    raw = [None, (39, 41), (40, 11), None]
    monkeypatch.setattr(water_model, 'shifted_fifth', lambda model, crop, dx: crop[-dx-1])
    water_model.corroborate_fours(None, raw, rows, audits)
    assert [r[4] for r in rows] == [40, 9, 10, 45]
    audits[0] = {}  # unmodified model prediction from a separate real frame
    water_model.corroborate_fours(None, raw, rows, audits)
    assert [r[4] for r in rows] == [40, 39, 10, 45]
    assert audits[2]['left_phases'] == [40, 11]


def step(state, p, t, rows=None):
    return decide(state, rows if rows is not None else [p]*9, p, t, 60, 40)


def test_carry_uncertainty_and_real_frame_votes():
    assert decode([20, 10, 2, 83, 24, 96]) is None
    assert decode([20, 10, 2, 83, 30, 99]) == 210830
    other = [*P[:-1], 70]
    assert step({}, P, 600, [P]*4+[other]*5)[1]['decision'] == 'weak_burst'
    assert step({}, other, 600, [P]*7+[other]*2)[1]['decision'] == 'weak_burst'


def test_restart_bootstrap_rate_limits_and_confirmation(tmp_path):
    state = {}
    for timestamp in (60, 120, 180):
        state, e = step(state, P, timestamp)
        water_store.atomic(tmp_path/'state.json', json.dumps(state).encode())
        state = water_store.load_state(tmp_path)
    assert state == {'last': 210828, 'at': 180, 'day_start': 210828}
    for p, t, reason in [(P, 170, 'clock_reversed'), ([*P[:-1], 71], 240, 'decrease'),
                         ([30, *P[1:]], 240, 'flow_limit')]:
        unchanged, result = step(state, p, t)
        assert (unchanged['last'], unchanged['at']) == (state['last'], state['at'])
        assert result['decision'] == reason
    next_p = [*P[:-1], 90]
    state, e = step(state, next_p, 240)
    assert e['decision'] == 'confirm_change' and state['at'] == 180
    state, e = step(state, next_p, 300)
    assert state == {'last': 210829, 'at': 300, 'day_start': 210828}


def test_daily_rollover_waits_for_first_confirmed_local_reading():
    midnight = int(datetime(2026, 6, 1).timestamp())
    state = {'last': 210828, 'at': midnight-60, 'day_start': 210800}
    changed = [*P[:-1], 90]
    pending, _ = step(state, changed, midnight)
    assert pending['day_start'] == 210800
    confirmed, _ = step(pending, changed, midnight+60)
    assert confirmed['day_start'] == confirmed['last'] == 210829


def test_duplicate_stale_or_weak_bursts_never_confirm():
    state, _ = step({}, P, 60)
    state, _ = step(state, P, 60)
    assert state['count'] == 1
    state, e = step(state, P, 120, [P]*5)
    assert e['decision'] == 'weak_burst' and state['pending_at'] == 60
    state, _ = step(state, P, 660)
    assert state['count'] == 1


def test_publish_only_confirmed_observations(tmp_path, monkeypatch):
    settings = SimpleNamespace(config={}, interval=60, timeout=10, water_flow_lpm=40)
    clock = iter([60, 60, 120, 120, 180, 180, 240, 240])
    monkeypatch.setattr(water_reader, 'time', SimpleNamespace(time=lambda: next(clock)))
    bursts = iter([[P]*9]*3 + [[]])
    preview = np.zeros((40, 150, 3), np.uint8)
    monkeypatch.setattr(water_reader, 'observe', lambda s: (
        next(bursts), P, preview, {}, [preview], preview))
    published = []
    monkeypatch.setattr(water_reader, 'publish_water', lambda _, value, t: published.append(value))
    for _ in range(4):
        assert water_reader.water_once(settings, tmp_path, lambda _: None, lambda _: 'failure') == 0
    assert published == [None, None, None, None, 210828, 0, None, None]
    assert water_store.load_state(tmp_path)['at'] == 180
    assert (tmp_path/'overview.jpg').exists() and not (tmp_path/'reading.png').exists()
    events = [json.loads(line) for line in (tmp_path/'events.jsonl').read_text().splitlines()]
    assert [e['decision'] for e in events] == ['bootstrap','bootstrap','confirmed','insufficient_alignment']


def test_corrupt_checkpoint_never_bootstraps(tmp_path):
    (tmp_path/'state.json').write_text('{"last":1}')
    with pytest.raises(ValueError):
        water_store.load_state(tmp_path)


def test_five_frame_majority_conditioned_on_confirmed_history():
    candidate = [20, 10, 2, 83, 29, 50]  # 2108.35
    other = [20, 10, 2, 83, 43, 50]
    rows = [candidate]*5 + [other]*4
    baseline = {'last':210828, 'at':60}
    for state, t in [({}, 180), (baseline, 120), ({'last':210836, 'at':60}, 180)]:
        assert step(state, other, t, rows)[1]['decision'] == 'weak_burst'
    pending, e = step(baseline, other, 180, rows)
    assert e['decision'] == 'confirm_change' and pending['last'] == 210828
    confirmed, e = step(pending, other, 240, rows)
    assert e['decision'] == 'confirmed' and confirmed['last'] == 210835


def test_advancing_shower_requires_supported_and_rate_bounded_second_endpoint():
    baseline = {'last':210836, 'at':60}
    p37, p39, p42 = ([20,10,0,80,30,70], [20,10,0,80,30,90], [20,10,0,80,40,20])
    pending, _ = step(baseline, p37, 360)
    for composite, rows in [(p37, [p39]*8), (p39, [p39]*6), (p42, [p42]*9)]:
        unchanged, _ = step(pending, composite, 420, rows)
        assert unchanged['last'] == 210836
    updated, event = step(pending, p39, 420, [p39]*8)
    assert updated['last'] == 210839 and event['moving_from_ticks'] == 210837


@pytest.mark.parametrize('bad', [110828, 310828])
def test_correct_mistaken_baseline_only_after_independent_consistent_evidence(tmp_path, bad):
    state = {'last':bad, 'at':60}
    for t in (120,180,240,300):
        state, event = step(state, P, t)
        assert state['last'] == bad and state['at'] == 60
        water_store.atomic(tmp_path/'state.json', json.dumps(state).encode())
        state = water_store.load_state(tmp_path)
    assert state['alt_hits'] == 4
    # A weak burst or an implausible change in the alternative cannot promote it.
    weak, _ = step(state, P, 360, [P]*5)
    assert 'alt' not in weak
    jump, _ = step(state, [40,*P[1:]], 360)
    assert jump['alt_hits'] == 1
    new, event = step(state, [*P[:-1],90], 360)
    assert event['decision'] == 'baseline_corrected' and event['previous_ticks'] == bad
    assert new == {'last':210829, 'at':360, 'day_start':210829}


def test_rotation_and_hourly_samples(tmp_path):
    img = np.random.default_rng(2).integers(0, 256, (250, 225, 3), dtype=np.uint8)
    for hour in range(49):
        for category in ('reject', 'steady', 'change'):
            water_store.save_sample(tmp_path, hour*3600, category, img)
    assert len(list(tmp_path.glob('*.jpg'))) == 72
    for n in range(1600):
        water_store.journal(tmp_path, {'n': n, 'data': 'x'*4000}, lambda _: None)
    assert len(list(tmp_path.glob('events*'))) == 4
    assert sum(p.stat().st_size for p in tmp_path.iterdir()) < 10_000_000


def test_water_only_options_and_discovery(tmp_path, monkeypatch):
    path = tmp_path/'options.json'
    options = {'water_meter_camera_host':'camera', 'water_meter_camera_user':'user',
               'water_meter_camera_password':'secret'}
    path.write_text(json.dumps(options))
    water, = load_gauges(path)
    assert water.water_flow_lpm == 40
    sent = []
    monkeypatch.setattr(mqtt_pressure, '_publish', lambda *args: sent.append(args))
    mqtt_pressure.publish_water(water.config, 210828)
    _, discovery, state, payload, value, _ = sent[0]
    assert discovery == 'homeassistant/sensor/paine_water_total/config'
    assert state == payload['state_topic'] == 'paine/water_meter/state'
    assert (payload['state_class'], payload['device_class'], value) == ('total_increasing','water','2108.28')
    assert 'expire_after' not in payload
    path.write_text(json.dumps(dict(options, camera_host='pressure', camera_user='user',
                                    camera_password='secret', state_topic=state)))
    with pytest.raises(ValueError, match='distinct'):
        load_gauges(path)
