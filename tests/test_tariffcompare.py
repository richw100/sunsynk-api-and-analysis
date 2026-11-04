"""tariff_sources.py parsers/helpers and tariffcompare.py pricing (no network)."""
import json
from datetime import date

import pytest

from analysis.energyday import EnergyDay, ExportCorrection
from analysis.projection import SlotProjection
from analysis.tariff_sources import (
    _window_from_text, energyplus_tariff, family_key, manual_tariff, parse_charge_anytime_rate,
    parse_everviz_table, price_cap_tariff, rates_to_slots, tou_slot_rates, window_slots,
    DEFAULTS)
from analysis.tariffcompare import (
    _runs, annual_profile, best_pairs, compare, compare_exports, current_tariff, describe_rates,
    load_tariff_settings, pair_breakdown, price_tariff)
from tests.test_calculations import _FakeMonth, _boost_day_data


# ---------------------------------------------------------------- helpers

@pytest.mark.parametrize('code,family', [
    ('OE-FIX-12M-26-11-03', 'OE-FIX-12M'),
    ('GO-VAR-22-10-14', 'GO-VAR'),
    ('NEXT_DRIVE_FIXED_V37', 'NEXT_DRIVE_FIXED'),
    ('NEXT_SMART_SAVER_FIXED_12M_GAS_V47_1', 'NEXT_SMART_SAVER_FIXED_12M_GAS'),
    ('EDF_EV_FIX_GOELEC_18M_V5_HH', 'EDF_EV_FIX_GOELEC_18M'),
    ('NEXT_FLEX', 'NEXT_FLEX'),
])
def test_family_key_strips_versions(code, family):
    assert family_key(code) == family


def test_window_slots_plain_and_wrapping():
    assert window_slots('00:30-05:30') == set(range(1, 11))
    assert window_slots('23:30-05:30') == {47} | set(range(0, 11))
    assert window_slots('00:00-00:00') == set()


def test_rates_to_slots_local_time_in_gmt_and_bst():
    # Octopus Go publishes UTC periods; the cheap window is 00:30-05:30 LOCAL all year.
    gmt = [{'valid_from': '2026-11-02T05:30:00Z', 'valid_to': '2026-11-03T00:30:00Z', 'value_inc_vat': 30},
           {'valid_from': '2026-11-02T00:30:00Z', 'valid_to': '2026-11-02T05:30:00Z', 'value_inc_vat': 9},
           {'valid_from': '2026-11-01T05:30:00Z', 'valid_to': '2026-11-02T00:30:00Z', 'value_inc_vat': 30}]
    slots = rates_to_slots(gmt, date(2026, 11, 2), date(2026, 11, 3))
    assert [k for k in range(48) if slots[k] == 9] == list(range(1, 11))
    bst = [{'valid_from': '2026-07-01T23:30:00Z', 'valid_to': '2026-07-02T04:30:00Z', 'value_inc_vat': 9},
           {'valid_from': '2026-07-01T04:30:00Z', 'valid_to': '2026-07-01T23:30:00Z', 'value_inc_vat': 30},
           {'valid_from': '2026-06-30T23:30:00Z', 'valid_to': '2026-07-01T04:30:00Z', 'value_inc_vat': 9}]
    slots = rates_to_slots(bst, date(2026, 7, 1), date(2026, 7, 2))
    assert [k for k in range(48) if slots[k] == 9] == list(range(1, 11))


def test_rates_to_slots_averages_days_and_open_ended_and_gaps():
    rates = [{'valid_from': '2026-11-02T00:00:00Z', 'valid_to': None, 'value_inc_vat': 20},
             {'valid_from': '2026-11-01T00:00:00Z', 'valid_to': '2026-11-02T00:00:00Z', 'value_inc_vat': 10}]
    assert rates_to_slots(rates, date(2026, 11, 1), date(2026, 11, 3)) == [15.0] * 48
    assert rates_to_slots(rates[:1], date(2026, 11, 1), date(2026, 11, 2)) == [None] * 48


def test_window_from_text():
    assert _window_from_text('a super low rate between 23:30 - 05:30 every night') == '23:30-05:30'
    assert _window_from_text('cheap from 0:30 to 5:30') == '00:30-05:30'
    assert _window_from_text('no times here') is None


# ---------------------------------------------------------------- scrapers

def _ep_table(title, header, rows):
    cells = ''.join(f'<th>{h}</th>' for h in header)
    body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows)
    return f'<h3>{title}</h3><p>Unit rates...</p><table><tr>{cells}</tr>{body}</table>'


EP_STD = ['PES region', 'Elec rate (p/kWh)', 'Elec standing (p/day)', 'Contract', 'Exit fee (per fuel)']
EP_E7 = ['PES region', 'Elec rate (p/kWh)', 'E7 off-peak (p/kWh)', 'Elec standing (p/day)', 'Contract',
         'Exit fee (per fuel)']


def test_energyplus_standard_rate_row_for_region():
    page = ('<h1>Fix and Fall Oct28 v5 by British Gas: regional prices</h1>'
            + _ep_table('Electricity, Monthly Direct Debit, Economy 7: regional prices', EP_E7,
                        [['South West England', '30.0', '15.0', '50.00', '24 months', '£50']])
            + _ep_table('Electricity, Monthly Direct Debit, standard-rate: regional prices', EP_STD,
                        [['East Midlands', '26.0', '55.00', '24 months', '£50'],
                         ['South West England', '27.38', '56.24', '24 months', '£50']]))
    t, reason = energyplus_tariff(page, 'fix-and-fall', 'british-gas', 'L', DEFAULTS)
    assert reason is None
    assert (t['supplier'], t['name'], t['kind']) == ('British Gas', 'Fix and Fall Oct28 v5', 'fixed 24m')
    assert t['slot_rates'] == [27.38] * 48 and t['standing'] == 56.24


def test_energyplus_e7_only_needs_a_window():
    page = ('<h1>EV Power Apr28 v2 by British Gas: regional prices</h1>'
            + _ep_table('Electricity, Monthly Direct Debit, Economy 7: regional prices', EP_E7,
                        [['South West England', '35.49', '9.50', '56.51', '18 months', '£50']]))
    t, _ = energyplus_tariff(page, 'ev-power', 'british-gas', 'L', DEFAULTS)
    assert t['slot_rates'][:10] == [9.5] * 10 and t['slot_rates'][10:] == [35.49] * 38
    other = page.replace('EV Power Apr28 v2', 'Night Saver')
    t, reason = energyplus_tariff(other, 'night', 'british-gas', 'L', DEFAULTS)
    assert t is None and 'window' in reason
    t, reason = energyplus_tariff(page, 'ev-power', 'british-gas', 'A', DEFAULTS)
    assert t is None and 'region' in reason


def test_everviz_price_cap_uses_newest_period():
    rows = [["Region", "Daily standing charge July to September 2026",
             "Daily standing charge October to December 2026",
             "Unit rate July to September 2026", "Unit rate  October to December 2026"],
            ["East Midlands", "50.00 pence per day", "51.00 pence per day", "25.00 pence per kWh",
             "25.10 pence per kWh"],
            ["South Western England", "59.71 pence per day", "57.23 pence per day", "25.33 pence per kWh",
             "25.60 pence per kWh"]]
    js = ('var x = {"title":{"text":"<h3>Electricity standing charges and unit rates paid by Direct '
          'Debit, single rate</h3>"}, "data": ' + json.dumps(rows) + '};')
    title, parsed = parse_everviz_table(js)
    assert title.startswith('Electricity standing charges') and parsed == rows
    cap = price_cap_tariff(parsed, 'L')
    assert cap['slot_rates'] == [25.6] * 48 and cap['standing'] == 57.23
    assert 'October to December 2026' in cap['name']
    assert price_cap_tariff(parsed, 'P') is None


def test_charge_anytime_rate():
    page = ('<p>The Charge Anytime pay as you go add-on rate on 1/10/2026 is 13.33p per kWh. '
            'Actual sum per mile is 3.33p.</p>')
    assert parse_charge_anytime_rate(page) == 13.33
    assert parse_charge_anytime_rate('<p>nothing</p>') is None


def test_manual_tariff():
    t = manual_tariff({'supplier': 'X', 'name': 'EV', 'peakRate': 30, 'offpeakRate': 7,
                       'offpeakWindow': '00:00-05:00', 'standingCharge': 50, 'evRate': 7, 'smartEv': True})
    assert t['slot_rates'][:10] == [7] * 10 and t['slot_rates'][10] == 30
    assert t['ev_rate'] == 7 and t['smart_ev']


def test_load_tariff_settings_layers(tmp_path):
    local = tmp_path / 'local.json'
    local.write_text(json.dumps({'tariffCompare': {'region': 'L', 'rateDays': 7}}))
    cfg = load_tariff_settings({'tariffCompare': {'region': 'A', 'rateDays': 3}},
                               ['rateDays:21', 'top:5'], str(local))
    assert (cfg['region'], cfg['rateDays'], cfg['top']) == ('L', 21, 5)
    assert cfg['sources'] == DEFAULTS['sources']


# ---------------------------------------------------------------- profile + pricing

def test_import_slots_match_interval_totals():
    ev_watts = {'00:00': 7000, '12:00': 1000, '03:00': 2400}
    day = EnergyDay(_boost_day_data(), '2025-06-10', _FakeMonth(), None, '00:00', '06:00',
                    export_correction=ExportCorrection(gain=0.95, standby_w=11), ev_watts=ev_watts)
    total, ev = day.import_slots()
    assert sum(total) == pytest.approx(day.get_calc_import())
    assert sum(ev) == pytest.approx(day.get_calc_ev() - day.get_calc_ev_from_pv())
    assert ev[0] == pytest.approx(7000 / 12) and ev[6] == pytest.approx(2400 / 12)
    assert ev[24] == pytest.approx(0)          # 12:00: EV soaked up exported PV


def test_slot_projection_seasonal_weighting_and_ev_shape():
    proj = SlotProjection('2026-05-01')
    one = [0.0] * 48
    for m in range(1, 13):
        total = list(one)
        total[0] = 1000.0 * m              # 1 kWh/day in Jan ... 12 in Dec, slot 0
        proj.add_day(f'2025-{m:02d}-15', total, one)
    assert proj.seasonal
    expected = sum(m * d for m, d in zip(range(1, 13), (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)))
    assert proj.annual_house()[0] == pytest.approx(expected)
    ev_before, ev_after = list(one), list(one)
    ev_before[2], ev_after[4] = 5000.0, 3000.0
    proj.add_day('2026-04-01', ev_before, ev_before)
    proj.add_day('2026-06-01', ev_after, ev_after)
    shape = proj.ev_shape()
    assert shape[4] == pytest.approx(1.0) and shape[2] == 0.0      # habits since the basis date


def test_annual_profile_spreads_rebased_ev():
    proj = SlotProjection()
    total, ev = [0.0] * 48, [0.0] * 48
    total[10], total[2], ev[2] = 2000.0, 4000.0, 4000.0
    proj.add_day('2026-06-01', total, ev)
    house, ev_year = annual_profile(proj, 365 * 2 + 3000)
    assert sum(house) == pytest.approx(365 * 2)
    assert ev_year[2] == pytest.approx(3000)


def _go_like(cheap, peak, window, smart=False, ev_rate=None, standing=50.0):
    return {'supplier': 'S', 'name': 'T', 'code': 'C', 'kind': 'variable', 'source': 'x',
            'slot_rates': tou_slot_rates(peak, cheap, window), 'standing': standing,
            'smart_ev': smart, 'ev_rate': ev_rate, 'note': ''}


def test_price_tariff_as_now_and_ev_retimed():
    house, ev = [0.0] * 48, [0.0] * 48
    house[20] = 1000.0                       # 10:00, peak everywhere
    ev[0], ev[30] = 2000.0, 100.0            # 00:00 (current off-peak) and a 15:00 top-up
    current_off = window_slots('00:00-06:00')
    go = _go_like(9.0, 30.0, '00:30-05:30')
    p = price_tariff(go, house, ev, current_off)
    assert p['as_now'] == pytest.approx((1000 * 30 + 2000 * 30 + 100 * 30) / 100 + 365 * 0.5)
    assert p['retimed'] == pytest.approx((1000 * 30 + 2000 * 9 + 100 * 30) / 100 + 365 * 0.5)
    assert p['cheap_kwh'] == 0.0             # nothing imported 00:30-05:30 as things are
    smart = _go_like(7.0, 35.0, '23:30-05:30', smart=True, ev_rate=7.0)
    p = price_tariff(smart, house, ev, current_off)
    assert p['retimed'] == pytest.approx((1000 * 35 + 2100 * 7) / 100 + 365 * 0.5)
    flat = {**go, 'slot_rates': [25.0] * 48}
    p = price_tariff(flat, house, ev, current_off)
    assert p['as_now'] == p['retimed'] and p['cheap_kwh'] == 0.0


def test_current_tariff_and_compare_ordering():
    prices = {'data': {'prices': [
        {'datefrom': '2025-01-01', 'dateto': '2040-01-01', 'offpeakRate': '0.07', 'offpeakStart': '00:00',
         'offpeakStop': '05:00', 'peakRate': '0.30', 'exportRate': '0.15', 'standingCharge': '0.60'},
        {'datefrom': '2026-10-01', 'dateto': '2040-01-01', 'offpeakRate': '0.038', 'offpeakStart': '00:00',
         'offpeakStop': '06:00', 'peakRate': '0.2563', 'exportRate': '0.13', 'standingCharge': '0.5714'}]}}
    cur = current_tariff(prices, today='2026-11-04')
    assert cur['window'] == '00:00-06:00'
    assert cur['slot_rates'][11] == pytest.approx(3.8) and cur['slot_rates'][12] == pytest.approx(25.63)
    house, ev = [100.0] * 48, [0.0] * 48
    rows = compare([_go_like(1.0, 50.0, '00:00-01:00'), _go_like(20.0, 20.0, '00:00-01:00')],
                   cur, house, ev)
    assert [r['retimed'] for r in rows] == sorted(r['retimed'] for r in rows)
    cur_row = next(r for r in rows if r['code'] == 'CURRENT')
    assert cur_row['vs_current'] == 0.0


def test_describe_rates_and_runs():
    assert describe_rates([25.6] * 48) == ('single', '25.60')
    kind, text = describe_rates(tou_slot_rates(36.98, 6.5, '23:30-05:30'))
    assert kind == 'tou' and text == '6.50 23:30-05:30 / 36.98'
    assert describe_rates([float(k) for k in range(48)])[0] == 'dynamic'
    assert _runs([0, 1, 47]) == [[47, 2]]


# ---------------------------------------------------------------- export

def test_export_slots_sum_to_calc_export():
    day = EnergyDay(_boost_day_data(), '2025-06-10', _FakeMonth(), None, '00:00', '06:00',
                    export_correction=ExportCorrection(gain=0.95, standby_w=11),
                    ev_watts={'12:00': 1000})
    slots = day.export_slots()
    assert sum(slots) == pytest.approx(day.get_calc_export())
    assert slots[16] > 0 and slots[24] > 0 and sum(slots[:16]) == 0


def test_slot_projection_export_and_monthly():
    proj = SlotProjection()
    zero = [0.0] * 48
    exp = list(zero)
    exp[24] = 2000.0
    proj.add_day('2026-06-01', zero, zero, exp)
    proj.add_day('2026-06-02', zero, zero, exp)
    assert proj.annual_export()[24] == pytest.approx(365 * 2)      # provisional: 2 kWh/day
    assert proj.monthly()[5] == (0.0, pytest.approx(60.0))


def _export(code, rate, requires=None, supplier='S'):
    return {'supplier': supplier, 'name': code, 'code': code, 'kind': '', 'source': 'x',
            'direction': 'EXPORT', 'slot_rates': [rate] * 48 if not isinstance(rate, list) else rate,
            'standing': 0.0, 'smart_ev': False, 'ev_rate': None, 'note': '', 'requires': requires}


def test_compare_exports_and_describe_high_window():
    export = [0.0] * 48
    export[24], export[34] = 100.0, 10.0
    prime = tou_slot_rates(9.0, 16.0, '16:00-19:00')     # 'off-peak' arg = the prime rate here
    rows = compare_exports([_export('FLAT', 15.0), _export('PRIME', prime)],
                           _export('CURRENT_EXPORT', 13.0), export)
    assert [r['code'] for r in rows] == ['FLAT', 'CURRENT_EXPORT', 'PRIME']
    assert rows[0]['income'] == pytest.approx(110 * 0.15)
    assert rows[1]['vs_current'] == 0.0
    assert rows[2]['rates_text'] == '16.00 16:00-19:00 / 9.00'


def test_best_pairs_respect_required_supplier():
    imports = [{'code': 'CURRENT', 'supplier': 'Current', 'retimed': 800.0},
               {'code': 'OCT', 'supplier': 'Octopus', 'retimed': 1000.0},
               {'code': 'EDF', 'supplier': 'EDF', 'retimed': 1010.0}]
    exports = [{'code': 'CURRENT_EXPORT', 'requires': None, 'income': 300.0},
               {'code': 'AGILE_OUT', 'requires': 'Octopus', 'income': 400.0},
               {'code': 'SEG', 'requires': None, 'income': 100.0}]
    pairs = [(i['code'], e['code'], net) for i, e, net in best_pairs(imports, exports, n=10)]
    assert pairs[0] == ('CURRENT', 'CURRENT_EXPORT', 500.0)
    assert ('OCT', 'AGILE_OUT', 600.0) in pairs
    assert not any(i == 'EDF' and e == 'AGILE_OUT' for i, e, _ in pairs)
    assert not any(i == 'CURRENT' and e != 'CURRENT_EXPORT' for i, e, _ in pairs)
    assert [p[:2] for p in pairs] == [p[:2] for p in sorted(pairs, key=lambda p: p[2])]
    # beyond n, the current pair is still listed
    assert ('CURRENT', 'CURRENT_EXPORT') in [(i['code'], e['code'])
                                             for i, e, _ in best_pairs(imports[::-1], exports, n=0)]


def test_price_tariff_breakdown_adds_up():
    house, ev = [10.0] * 48, [0.0] * 48
    ev[2] = 50.0
    p = price_tariff(_go_like(7.0, 30.0, '00:00-05:00'), house, ev, window_slots('00:00-06:00'))
    assert p['retimed'] == pytest.approx(p['house_cost'] + p['ev_retimed'] + p['standing_year'])
    assert p['ev_p_per_kwh'] == pytest.approx(7.0)


def test_best_pairs_flat_import_only_export():
    imports = [{'code': 'CURRENT', 'supplier': 'Current', 'retimed': 800.0, 'rate_type': 'tou'},
               {'code': 'DRIVE', 'supplier': 'E.ON Next', 'retimed': 1000.0, 'rate_type': 'tou'},
               {'code': 'FIX', 'supplier': 'E.ON Next', 'retimed': 1500.0, 'rate_type': 'single'}]
    exports = [{'code': 'CURRENT_EXPORT', 'requires': None, 'income': 300.0},
               {'code': 'EXCL', 'requires': 'E.ON Next', 'flat_import_only': True, 'income': 300.0}]
    pairs = [(i['code'], e['code']) for i, e, _ in best_pairs(imports, exports)]
    assert ('DRIVE', 'EXCL') not in pairs and ('FIX', 'EXCL') in pairs


def test_pair_breakdown_adds_up():
    house, ev, export = [10.0] * 48, [0.0] * 48, [0.0] * 48
    ev[2], export[24] = 50.0, 100.0
    imp_t = _go_like(7.0, 30.0, '00:00-05:00')
    imp = {**imp_t, **price_tariff(imp_t, house, ev, window_slots('00:00-06:00'))}
    exp = {'income': 13.0}
    b = pair_breakdown(imp, exp, house, ev, export)
    assert (b['house_off_kwh'], b['house_peak_kwh']) == (100.0, 380.0)
    assert (b['house_off_rate'], b['house_peak_rate']) == (pytest.approx(7.0), pytest.approx(30.0))
    assert b['house_off'] + b['house_peak'] + b['ev'] + b['standing'] == pytest.approx(b['import_total'])
    assert b['export_rate'] == pytest.approx(13.0) and b['net'] == pytest.approx(imp['retimed'] - 13.0)
