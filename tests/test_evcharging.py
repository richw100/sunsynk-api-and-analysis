import json

import pytest

from analysis.evcharging import load_ev_profiles, synthesise_wh, telemetry_wh


def _session(sid, start, end, off=0.0, std=0.0, events=()):
    return {'id': sid, 'start': start, 'end': end,
            'subTotals': [{'name': 'Off-Peak', 'energyImportedKwh': off},
                          {'name': 'Standard', 'energyImportedKwh': std}],
            'events': [{'start': a, 'end': b} for a, b in events]}


def test_telemetry_wh_holds_each_sample_until_the_next():
    rows = [{'time': '2026-10-02T23:00:00Z', 'powerKw': 6.0},
            {'time': '2026-10-02T23:01:00Z', 'powerKw': 3.0},
            {'time': '2026-10-02T23:31:00Z', 'powerKw': 0.0}]
    wh = [w for _, w in telemetry_wh(rows)]
    assert wh == pytest.approx([100.0, 3000 * 300 / 3600, 0.0])    # 30-min gap capped at 5 min


def test_synthesise_off_peak_in_window_standard_from_plug_in():
    s = _session('a', '2026-10-02T16:00:00Z', '2026-10-03T07:00:00Z', off=6.0, std=1.0,
                 events=[('2026-10-02T23:10:00Z', '2026-10-03T04:55:00Z')])
    minutes = synthesise_wh(s, charger_kw=6.0)
    assert sum(w for _, w in minutes) == pytest.approx(7000)
    std = [t for t, _ in minutes if t.hour == 16]
    off = [t for t, _ in minutes if t.hour in (23, 0)]
    assert len(std) == 10 and std[0].minute == 0                    # 1 kWh at 6 kW = 10 min
    assert off[0].hour == 23 and off[0].minute == 10                # window start
    assert max(t for t, _ in minutes).hour == 0                     # 6 kWh = 1 hour: done by 00:10


def test_synthesise_overflow_spread_over_session():
    s = _session('b', '2026-10-02T16:00:00Z', '2026-10-02T16:10:00Z', std=2.0)
    minutes = synthesise_wh(s, charger_kw=6.0)
    assert len(minutes) == 10 and sum(w for _, w in minutes) == pytest.approx(2000)


def test_load_ev_profiles_prefers_telemetry_and_uses_local_time(tmp_path):
    with_tele = _session('t', '2026-07-01T22:58:00Z', '2026-07-01T23:10:00Z', off=1.0)
    (tmp_path / 'session-t.json').write_text(json.dumps(with_tele))
    (tmp_path / 'telemetry-t.json').write_text(json.dumps(
        [{'time': f'2026-07-01T23:0{m}:00Z', 'powerKw': 6.0, 'tariffRateType': 3} for m in range(5)]))
    synth = _session('s', '2026-01-10T12:00:00Z', '2026-01-10T13:00:00Z', std=0.5)
    (tmp_path / 'session-s.json').write_text(json.dumps(synth))
    (tmp_path / 'session-open.json').write_text(json.dumps({'id': 'open', 'start': '2026-01-11T12:00:00Z', 'end': None}))

    prof = load_ev_profiles(str(tmp_path))
    # 23:00-23:04 UTC in BST = 00:00 local on 2 July: five 100 Wh minutes = 500 Wh -> 6000 W average
    assert prof['2026-07-02'] == {'00:00': pytest.approx(6000)}
    # GMT, synthesised at the measured 6 kW: 0.5 kWh = 5 min from 12:00
    assert prof['2026-01-10'] == {'12:00': pytest.approx(6000)}
    assert '2026-01-11' not in prof
    assert load_ev_profiles(str(tmp_path / 'nope')) == {}
