"""tariffhistory.py: rate timelines, local half-hour lookup, day pricing and pairs (no network)."""
from datetime import date, datetime, timezone

import pytest

from analysis.tariffhistory import (
    NIGHT_SLOTS, Timeline, actual_day, best_pairs, day_rates, last_12_months, months_between,
    price_history, price_import_day, retime_ev, slot_instants, totals)
from analysis.tariff_sources import tou_slot_rates

PRICES = {'data': {'prices': [
    {'datefrom': '2026-03-01', 'dateto': '2026-04-30', 'offpeakRate': '0.05', 'offpeakStart': '00:00',
     'offpeakStop': '06:00', 'peakRate': '0.25', 'exportRate': '0.15', 'standingCharge': '0.5'},
    {'datefrom': '2026-04-30', 'dateto': '2026-12-31', 'offpeakRate': '0.04', 'offpeakStart': '00:00',
     'offpeakStop': '06:00', 'peakRate': '0.26', 'exportRate': '0.13', 'standingCharge': '0.6'},
]}}


def _utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _half_hourly(day: date, value_for_local_slot, tz_offset_h: int):
    """Kraken-style UTC rows for one local day, value chosen by local slot index."""
    rows = []
    for k, start in enumerate(slot_instants(day)):
        rows.append({'valid_from': start.isoformat().replace('+00:00', 'Z'),
                     'valid_to': None if k == 47 else slot_instants(day)[k + 1].isoformat(),
                     'value_inc_vat': value_for_local_slot(k), 'payment_method': None})
    assert slot_instants(day)[0].hour == (24 - tz_offset_h) % 24
    return rows


def test_timeline_latest_start_wins_and_skips_non_direct_debit():
    t = Timeline([
        {'valid_from': '2026-01-01T00:00:00Z', 'valid_to': None, 'value_inc_vat': 20.0},
        {'valid_from': '2026-03-01T00:00:00Z', 'valid_to': '2026-04-01T00:00:00Z', 'value_inc_vat': 25.0,
         'payment_method': 'DIRECT_DEBIT'},
        {'valid_from': '2026-03-01T00:00:00Z', 'valid_to': None, 'value_inc_vat': 99.0,
         'payment_method': 'NON_DIRECT_DEBIT'},
    ])
    assert len(t) == 2
    assert t.at(_utc('2025-12-31T23:59:00')) is None
    assert t.at(_utc('2026-02-01T00:00:00')) == 20.0
    assert t.at(_utc('2026-03-15T00:00:00')) == 25.0
    assert t.at(_utc('2026-04-01T00:00:00')) == 20.0     # back to the open-ended period


@pytest.mark.parametrize('day,offset', [(date(2026, 1, 15), 0), (date(2026, 7, 15), 1)])
def test_day_rates_follow_local_half_hours_in_gmt_and_bst(day, offset):
    t = Timeline(_half_hourly(day, lambda k: float(k), offset))
    assert day_rates(t, slot_instants(day)) == [float(k) for k in range(48)]


def test_day_rates_fill_gaps_with_mean_and_none_when_empty():
    day = date(2026, 1, 15)
    rows = [r for r in _half_hourly(day, lambda k: 10.0 if k < 24 else 30.0, 0)]
    del rows[47]
    rows[46]['valid_to'] = slot_instants(day)[47].isoformat()
    rates = day_rates(Timeline(rows), slot_instants(day))
    assert rates[47] == pytest.approx((24 * 10 + 23 * 30) / 47)
    assert day_rates(Timeline([]), slot_instants(day)) is None


def test_months_between_and_last_12():
    assert months_between(date(2025, 11, 20), date(2026, 2, 1)) == [
        date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]
    months = [f'2025-{m:02d}' for m in range(1, 13)] + ['2026-01', '2099-01']
    assert last_12_months(months) == months[1:13]


def test_actual_day_uses_first_matching_period():
    rates, off, standing, export = actual_day(PRICES, '2026-04-30')
    assert rates[0] == pytest.approx(5.0) and rates[20] == pytest.approx(25.0)
    assert off == set(range(12)) and standing == pytest.approx(50.0) and export == pytest.approx(15.0)
    assert actual_day(PRICES, '2027-01-01') is None


def test_retime_moves_off_peak_ev_to_cheapest_night_slots_with_cap():
    rates = [30.0] * 48
    rates[46], rates[2], rates[3] = 5.0, 8.0, 9.0
    ev = [0.0] * 48
    ev[0], ev[1] = 4.0, 3.0           # 7 kWh in the off-peak window
    ev[30] = 1.0                      # daytime top-up stays at its own rate
    cost = retime_ev(ev, rates, set(range(12)), 3.7)
    assert cost == pytest.approx(3.7 * 5 + 3.3 * 8 + 1.0 * 30)
    assert 30 not in NIGHT_SLOTS and 46 in NIGHT_SLOTS


def test_price_import_day_smart_actual_and_never_worse_than_as_charged():
    rates = tou_slot_rates(30.0, 7.0, '23:30-05:30')
    house = [0.5] * 48
    ev = [0.0] * 48
    ev[2], ev[36] = 7.0, 2.0
    smart = price_import_day(rates, house, ev, set(range(12)), True, 3.7)
    assert smart['ev'] == pytest.approx(9 * 7 / 100)
    assert smart['ev_now'] == pytest.approx((7 * 7 + 2 * 30) / 100)
    assert smart['house'] == pytest.approx(sum(0.5 * r for r in rates) / 100)
    actual = price_import_day(rates, house, ev, set(range(12)), False, 3.7, actual=True)
    assert actual['ev'] == actual['ev_now']
    flat = price_import_day([20.0] * 48, house, ev, set(range(12)), False, 3.7)
    assert flat['ev'] == pytest.approx(flat['ev_now'])


def _flat_tariff(name, rate, standing=40.0, direction='IMPORT', requires=None, smart=False):
    return {'name': name, 'supplier': 'Octopus', 'direction': direction, 'requires': requires,
            'smart_ev': smart,
            'unit': Timeline([{'valid_from': '2020-01-01T00:00:00Z', 'valid_to': None, 'value_inc_vat': rate}]),
            'standing': Timeline([{'valid_from': '2020-01-01T00:00:00Z', 'valid_to': None,
                                   'value_inc_vat': standing}])}


def test_price_history_totals_and_pairs():
    house = [100.0] * 48                      # 4.8 kWh/day
    ev = [0.0] * 48
    ev[0] = 2000.0                            # 2 kWh off-peak
    export = [0.0] * 48
    export[24] = 3000.0                       # 3 kWh at noon
    days = {'2026-05-01': (house, ev, export), '2026-05-02': (house, ev, export),
            '2027-06-01': (house, ev, export)}          # no actual period: skipped
    tariffs = [_flat_tariff('Flat', 20.0), _flat_tariff('Out', 10.0, 0.0, 'EXPORT', 'Octopus'),
               _flat_tariff('Other out', 30.0, 0.0, 'EXPORT', 'Someone else')]
    r = price_history(days, PRICES, tariffs)
    assert r['skipped'] == {'Actual': 1}
    may = r['imports']['Actual']['2026-05']
    assert may['days'] == 2
    assert may["house"] == pytest.approx(2 * (3.6 * 0.26 + 1.2 * 0.04))   # 12 off-peak slots x 0.1 kWh
    assert may['ev'] == pytest.approx(2 * 2 * 0.04)
    assert may['standing'] == pytest.approx(1.2)
    flat = r['imports']['Flat']['2026-05']
    assert flat['house'] + flat['ev'] == pytest.approx(2 * 6.8 * 0.20)
    assert r['exports']['Actual export']['2026-05']['income'] == pytest.approx(2 * 3 * 0.13)
    assert r['exports']['Out']['2026-05']['income'] == pytest.approx(2 * 3 * 0.10)
    assert r['kwh']['2026-05']['export'] == pytest.approx(6.0)
    pairs = best_pairs(r, ['2026-05'])
    names = {(p[1], p[2]) for p in pairs}
    assert ('Actual', 'Actual export') in names and ('Flat', 'Out') in names and ('Flat', 'Actual export') in names
    assert ('Actual', 'Out') not in names and ('Flat', 'Other out') not in names
    assert [p[0] for p in pairs] == sorted(p[0] for p in pairs)
    assert totals(r['imports']['Flat'], ['2026-05'])['days'] == 2


def test_a_day_missing_any_tariff_is_left_out_of_every_column():
    days = {'2026-05-01': ([100.0] * 48, [0.0] * 48, [0.0] * 48)}
    gap = _flat_tariff('Gap', 20.0)
    gap['unit'] = Timeline([])
    r = price_history(days, PRICES, [_flat_tariff('Flat', 20.0), gap])
    assert r['skipped'] == {'Gap': 1}
    assert not r['imports'] and not r['kwh']
