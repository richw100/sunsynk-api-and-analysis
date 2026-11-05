import json
from datetime import datetime, timezone

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


def test_synthesise_unknown_split_charges_in_the_schedule_window():
    # Indra labelled it all Standard (no tariff set): it goes into the 00:10-06:55 BST window,
    # not from plug-in; a 5-minute event isn't a window.
    s = _session('u', '2025-07-01T22:03:00Z', '2025-07-02T07:58:00Z', std=6.0,
                 events=[('2025-07-01T22:05:00Z', '2025-07-01T22:10:00Z'),
                         ('2025-07-01T23:10:00Z', '2025-07-02T05:55:00Z')])
    minutes = synthesise_wh(s, charger_kw=6.0, split_known=False)
    assert sum(w for _, w in minutes) == pytest.approx(6000)
    assert min(t for t, _ in minutes) == datetime(2025, 7, 1, 23, 10, tzinfo=timezone.utc)
    assert max(t for t, _ in minutes) == datetime(2025, 7, 2, 0, 9, tzinfo=timezone.utc)
    # known split (the default): Standard energy charges from plug-in
    assert min(t for t, _ in synthesise_wh(s, charger_kw=6.0)).hour == 22


def test_synthesise_unknown_split_without_window_starts_at_local_midnight():
    overnight = _session('n', '2025-06-15T13:28:00Z', '2025-06-16T06:58:00Z', std=3.0)
    minutes = synthesise_wh(overnight, charger_kw=6.0, split_known=False)
    assert min(t for t, _ in minutes) == datetime(2025, 6, 15, 23, 0, tzinfo=timezone.utc)   # 00:00 BST
    assert len(minutes) == 30
    daytime = _session('d', '2025-06-11T08:46:00Z', '2025-06-11T10:26:00Z', std=1.0)
    assert min(t for t, _ in synthesise_wh(daytime, charger_kw=6.0, split_known=False)).minute == 46


def test_load_ev_profiles_treats_sessions_before_the_first_off_peak_one_as_scheduled(tmp_path):
    early = _session('e', '2025-07-01T22:03:00Z', '2025-07-02T07:58:00Z', std=1.0,
                     events=[('2025-07-01T23:10:00Z', '2025-07-02T05:55:00Z')])
    late = _session('l', '2025-08-01T22:03:00Z', '2025-08-02T07:58:00Z', off=1.0, std=0.5,
                    events=[('2025-08-01T23:10:00Z', '2025-08-02T05:55:00Z')])
    for s in (early, late):
        (tmp_path / f"session-{s['id']}.json").write_text(json.dumps(s))
    prof = load_ev_profiles(str(tmp_path), charger_kw=6.0)
    assert set(prof['2025-07-02']) == {'00:10', '00:15'}              # 1 kWh at 6 kW from 00:10 BST
    assert '2025-07-01' not in prof
    assert '23:00' in prof['2025-08-01'] and '00:10' in prof['2025-08-02']   # standard from plug-in


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
