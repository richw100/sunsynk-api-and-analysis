"""analysis/ev_savings.py (parity with android EvSavingsTest)."""
import json
from datetime import date, datetime, timezone

import pytest

from analysis import ev_savings as ev


def _prices():
    def period(f, t, off, peak, start, stop, export):
        return {'datefrom': f, 'dateto': t, 'offpeakRate': str(off), 'peakRate': str(peak),
                'offpeakStart': start, 'offpeakStop': stop, 'exportRate': str(export)}
    return {'data': {'prices': [period('2025-06-10', '2025-06-30', 0.07, 0.26, '00:00', '04:00', 0.165),
                                period('2025-07-01', '2025-12-31', 0.06, 0.28, '23:30', '05:30', 0.15)]}}


def test_ev_days_price_each_half_hour_and_add_indra_days_before():
    jun = [0.0] * 48
    jun[0], jun[10] = 2.0, 1.0                          # 00:00 off-peak, 05:00 peak
    jul = [0.0] * 48
    jul[47] = jul[5] = jul[11] = 1.0                    # 23:30 + 02:30 in the window, 05:30 not
    days = ev.ev_days([('2025-06-20', jun, 3.0), ('2025-07-05', jul, 0.0)], _prices(),
                      {'2025-06-01': 10.0, '2025-06-25': 99.0})
    assert [d for d, _, _ in days] == ['2025-06-01', '2025-06-20', '2025-07-05']
    assert days[0][2] == pytest.approx(10 * 0.07)
    assert days[1][1] == pytest.approx(6.0)
    assert days[1][2] == pytest.approx(2 * 0.07 + 0.26 + 3 * 0.165)
    assert days[2][2] == pytest.approx(2 * 0.06 + 0.28)


def test_local_daily_holds_each_stations_last_price_in_local_time():
    def t(s):
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    stations = {'a': [(t('2026-07-01T23:30:00'), 150.0), (t('2026-07-03T10:00:00'), 152.0)],
                'b': [(t('2026-07-02T09:00:00'), 148.0), (t('2026-07-02T10:00:00'), 999.0)]}
    d = ev.local_daily(stations, date(2026, 7, 1), date(2026, 7, 3))
    assert '2026-07-01' not in d                        # 23:30Z = 00:30 BST on the 2nd
    assert d['2026-07-02'] == (2, 149.0)
    assert d['2026-07-03'] == (2, 150.0)


def test_parse_national_offset_and_petrol_price():
    csv_text = ('﻿Date,ULSP (Ultra low sulphur unleaded petrol) Pump price in pence/litre,ULSD\n'
                '29/06/2026,151.02,160\n06/07/2026,149.80,161\n13/07/2026,150.53,162\n')
    n = ev.parse_national(csv_text)
    assert len(n) == 3 and n[1] == (date(2026, 7, 6), 149.80)
    local = {'2026-07-06': (15, 146.80), '2026-07-13': (16, 147.53), '2026-06-29': (3, 100.0)}
    assert ev.min_stations(22) == 15
    off, weeks = ev.offset(n, local, 15)
    assert off == pytest.approx(-3.0) and weeks == 2
    p, src = ev.petrol_price('2026-07-01', n, local, off, 15)
    assert p == pytest.approx(151.02 - 3.0) and src == 'national-adjusted'
    assert ev.petrol_price('2026-07-13', n, local, off, 15)[1] == 'local'


def test_calculate_miles_litres_savings_and_credits():
    n = [(date(2025, 1, 6), 140.0)]
    cfg = dict(ev.DEFAULTS, mpg=50.0, milesPerKwh=4.0, chargeEfficiency=0.9,
               beforeCharger=[{'month': '2025-02', 'kwh': 10.0, 'rate': 7.0}],
               credits=[{'month': '2025-03', 'amount': 30.0, 'note': 'bonus'}, {'month': '2025-05', 'amount': 10.0}])
    r = ev.calculate([('2025-03-02', 20.0, 1.5)], cfg, n, {}, 0, date(2025, 7, 1))
    assert [m['month'] for m in r['months']] == ['2025-02', '2025-03', '2025-05']
    mar = r['months'][1]
    litres = 72.0 / 50 * ev.LITRES_PER_GALLON
    assert mar['miles'] == pytest.approx(72.0)
    assert mar['petrol_cost'] == pytest.approx(litres * 1.40)
    assert ev.saved(mar) == pytest.approx(litres * 1.40 - 1.5 + 30.0)
    assert r['months'][0]['ev_cost'] == pytest.approx(0.7)
    assert ev.saved(r['months'][2]) == pytest.approx(10.0)              # a credit-only month
    assert ev.saved(ev.total(r['months'])) == pytest.approx(sum(ev.saved(m) for m in r['months']))
    assert ev.saved(ev.last_12(r['months'], date(2025, 7, 1))) == pytest.approx(ev.saved(ev.total(r['months'])))
    assert r['latest'][1] == 'national'


def test_stations_keep_the_big_four_and_history_merges():
    body = {'stations': [
        {'node_id': '1', 'trading_name': 'TOWN SUPERSTORE', 'brand_name': 'TESCO', 'location': {'postcode': 'X1'}},
        {'node_id': '2', 'trading_name': 'ASDA RING ROAD EXPRESS PETROL', 'brand_name': 'Esso'},
        {'node_id': '3', 'trading_name': 'Village Garage', 'brand_name': 'Morrisons Daily'},
        {'node_id': '4', 'trading_name': 'Some Garage', 'brand_name': 'BP'},
        {'node_id': '5', 'trading_name': 'SAINSBURYS OLD', 'brand_name': 'Sainsburys', 'is_permanently_closed': True}]}
    assert [s['id'] for s in ev.parse_stations(body)] == ['1', '2']
    h = ev.parse_history({'history': [{'timestamp': '2026-02-07T21:40:02.554Z', 'pricePence': 128},
                                      {'timestamp': '2026-02-17T14:40:38Z', 'pricePence': 127}]})
    old = [(datetime(2025, 12, 1, tzinfo=timezone.utc), 130.0), (h[0][0], 1.0)]
    assert [p for _, p in ev.merge(old, h)] == [130.0, 128.0, 127.0]


def test_settings_layering_and_saving_stations(tmp_path):
    local = tmp_path / 'config.local.json'
    local.write_text(json.dumps({'tariffCompare': {'region': 'L'}, 'evSavings': {'mpg': 40}}))
    cfg = ev.load_settings({'evSavings': {'mpg': 45, 'milesPerKwh': 4.2}}, str(local))
    assert cfg['mpg'] == 40 and cfg['milesPerKwh'] == 4.2 and cfg['chargeEfficiency'] == 0.9
    ev.save_stations(str(local), [{'id': 'a', 'name': 'A', 'postcode': ''}])
    saved = json.loads(local.read_text())
    assert saved['tariffCompare'] == {'region': 'L'} and saved['evSavings']['stations'][0]['id'] == 'a'
    assert saved['evSavings']['mpg'] == 40
