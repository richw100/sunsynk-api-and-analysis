"""tariff_sources.py parsers/helpers and tariffcompare.py pricing (no network)."""
import json
from datetime import date, timedelta

import pytest

from analysis.energyday import EnergyDay, ExportCorrection
from analysis.projection import SlotProjection
from analysis.tariff_sources import (
    _window_from_text, energyplus_tariff, family_key, manual_tariff, parse_charge_anytime_rate,
    parse_everviz_table, price_cap_tariff, rates_to_slots, tou_slot_rates, window_slots,
    DEFAULTS)
from analysis.tariffcompare import (
    _runs, annual_profile, best_pairs, compare, compare_exports, current_tariff, describe_rates,
    load_tariff_settings, pair_breakdown, price_export, price_tariff)
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
    go = _go_like(9.0, 30.0, '00:30-05:30')
    p = price_tariff(go, house, ev)
    assert p['as_now'] == pytest.approx((1000 * 30 + 2000 * 30 + 100 * 30) / 100 + 365 * 0.5)
    # all EV charging (the daytime top-up too) goes to the overnight rate
    assert p['retimed'] == pytest.approx((1000 * 30 + 2100 * 9) / 100 + 365 * 0.5)
    assert p['cheap_kwh'] == 0.0             # nothing imported 00:30-05:30 as things are
    smart = _go_like(7.0, 35.0, '23:30-05:30', smart=True, ev_rate=7.0)
    p = price_tariff(smart, house, ev)
    assert p['retimed'] == pytest.approx((1000 * 35 + 2100 * 7) / 100 + 365 * 0.5)
    flat = {**go, 'slot_rates': [25.0] * 48}
    p = price_tariff(flat, house, ev)
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
    p = price_tariff(_go_like(7.0, 30.0, '00:00-05:00'), house, ev)
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
    imp = {**imp_t, **price_tariff(imp_t, house, ev)}
    exp = {'income': 13.0}
    b = pair_breakdown(imp, exp, house, ev, export)
    assert (b['house_off_kwh'], b['house_peak_kwh']) == (100.0, 380.0)
    assert (b['house_off_rate'], b['house_peak_rate']) == (pytest.approx(7.0), pytest.approx(30.0))
    assert b['house_off'] + b['house_peak'] + b['ev'] + b['standing'] == pytest.approx(b['import_total'])
    assert b['export_rate'] == pytest.approx(13.0) and b['net'] == pytest.approx(imp['retimed'] - 13.0)


def test_dynamic_tariffs_priced_month_by_month():
    """A tariff with month_slot_rates is priced per calendar month against an average such
    month's kWh; EV at that month's cheapest rate; export likewise; the yearly figures are
    ignored."""
    month_house = [[0.0] * 48 for _ in range(12)]
    month_ev = [[0.0] * 48 for _ in range(12)]
    month_export = [[0.0] * 48 for _ in range(12)]
    month_house[0][36], month_house[6][36] = 100.0, 50.0      # 18:00 in Jan and Jul
    month_ev[0][2], month_ev[6][30] = 200.0, 100.0           # Jul EV charged at 15:00
    month_export[6][24] = 300.0
    ms = [[20.0] * 48 for _ in range(12)]
    ms[0][36], ms[6][36], ms[0][4], ms[6][6] = 40.0, 30.0, 10.0, 5.0
    house = [sum(m[k] for m in month_house) for k in range(48)]
    ev = [sum(m[k] for m in month_ev) for k in range(48)]
    agile = {**_go_like(99.0, 99.0, '00:00-01:00', standing=0.0), 'month_slot_rates': ms}
    p = price_tariff(agile, house, ev, month_house, month_ev)
    assert p['house_cost'] == pytest.approx((100 * 40 + 50 * 30) / 100)
    assert p['ev_now'] == pytest.approx((200 * 20 + 100 * 20) / 100)
    assert p['ev_retimed'] == pytest.approx((200 * 10 + 100 * 5) / 100)
    assert price_tariff(agile, house, ev)['house_cost'] == pytest.approx(150 * 99 / 100)   # no month data
    out = {'slot_rates': [99.0] * 48, 'month_slot_rates': [[r / 2 for r in m] for m in ms]}
    assert price_export(out, [0.0] * 48, month_export) == pytest.approx(300 * 10 / 100)
    imp = {**agile, **p}
    b = pair_breakdown(imp, {'income': 0.0}, house, ev, [0.0] * 48, month_house, month_ev)
    assert b['house_peak_kwh'] + b['house_off_kwh'] == pytest.approx(150.0)
    assert b['house_off'] + b['house_peak'] == pytest.approx(p['house_cost'])


def test_month_slot_averages_and_seasonal_yearly():
    from analysis.tariff_sources import complete_months_before, month_slot_averages, slot_instants
    months = complete_months_before(date(2026, 11, 5))
    assert (months[0], months[-1]) == (date(2025, 11, 1), date(2026, 10, 1))
    rows = []
    for m in months:              # each half-hour of a month priced at month number + slot/100
        d = m
        while d.month == m.month:
            for k, t in enumerate(slot_instants(d)):
                rows.append({'valid_from': t.isoformat(), 'valid_to': (t + timedelta(minutes=30)).isoformat(),
                             'value_inc_vat': m.month + k / 100})
            d += timedelta(days=1)
    ms = month_slot_averages(rows, months)
    assert ms[0][0] == pytest.approx(1.0) and ms[6][47] == pytest.approx(7.47)   # Jan 00:00, Jul 23:30 (BST)
    assert month_slot_averages(rows[:100], months) is None


def test_slot_projection_month_slots_and_monthly_ev():
    sp = SlotProjection('2026-05-01')
    ev_before, ev_after = [0.0] * 48, [0.0] * 48
    ev_before[2], ev_after[2] = 1000.0, 3000.0
    for d in range(1, 31):        # every month, 2025 before the basis and 2026 after it for May-Aug
        for m in range(1, 13):
            sp.add_day(f'2025-{m:02d}-{d:02d}', [h + 500.0 for h in ev_before], ev_before, [100.0] * 48)
        for m in range(5, 9):
            sp.add_day(f'2026-{m:02d}-{d:02d}', [h + 500.0 for h in ev_after], ev_after, [100.0] * 48)
    monthly = sp.monthly_ev(1200.0)
    assert sum(monthly.values()) == pytest.approx(1200.0)
    # Jan (before only, 1 kWh/day) x the shared months' after/all ratio (3 / 2) vs Jun's 3 kWh/day after
    assert monthly[0] / 31 * 2 == pytest.approx(monthly[5] / 30)
    house = sp.month_slot_house()
    assert sum(map(sum, house)) == pytest.approx(sum(sp.annual_house()))
    assert sum(map(sum, sp.month_slot_export())) == pytest.approx(sum(sp.annual_export()))
    month_ev = sp.month_slot_ev(1200.0)
    assert sum(map(sum, month_ev)) == pytest.approx(1200.0) and month_ev[3][2] == pytest.approx(monthly[3])


# ---------------------------------------------------------------- gas

EP_DUAL = ['PES region', 'Elec rate (p/kWh)', 'Elec standing (p/day)', 'Gas rate (p/kWh)',
           'Gas standing (p/day)', 'Contract', 'Exit fee (per fuel)']


def test_energyplus_gas_from_dual_fuel_table():
    page = ('<h1>Fix and Fall Oct28 v5 by British Gas: regional prices</h1>'
            + _ep_table('Electricity, Monthly Direct Debit, standard-rate: regional prices', EP_STD,
                        [['South West England', '27.38', '56.24', '24 months', '£50']])
            + _ep_table('Dual Fuel, Monthly Direct Debit, standard-rate: regional prices', EP_DUAL,
                        [['South West England', '27.38', '56.24', '9.151', '28.72', '24 months', '£50']]))
    t, _ = energyplus_tariff(page, 'fix-and-fall', 'british-gas', 'L', DEFAULTS)
    assert t['gas']['direction'] == 'GAS'
    assert (t['gas']['gas_rate'], t['gas']['gas_standing']) == (9.151, 28.72)
    no_gas = page.split('<h3>Dual Fuel')[0]
    assert energyplus_tariff(no_gas, 'fix-and-fall', 'british-gas', 'L', DEFAULTS)[0]['gas'] is None


def test_gas_code_key_and_link_gas():
    from analysis.tariff_sources import gas_code_key, gas_offer, link_gas
    assert gas_code_key('NEXT_DRIVE_FIXED_GAS_V37') == 'NEXT_DRIVE_FIXED_V37'
    assert gas_code_key('NEXT_OPTIMISE_IMPORT_GAS') == 'NEXT_OPTIMISE_IMPORT'
    base = {'supplier': 'E.ON Next', 'source': 'x'}
    gas = gas_offer({**base, 'name': 'Drive gas', 'code': 'NEXT_DRIVE_FIXED_GAS_V37'}, 8.8, 29.7)
    elec = {**base, 'name': 'Drive', 'code': 'NEXT_DRIVE_FIXED_V37', 'direction': 'IMPORT', 'gas': None}
    bundled = gas_offer({**base, 'supplier': 'Octopus', 'name': 'Flex', 'code': 'VAR'}, 7.9, 28.75)
    octo = {'supplier': 'Octopus', 'name': 'Flex', 'code': 'VAR', 'direction': 'IMPORT', 'gas': bundled}
    out = link_gas([elec, gas, octo])
    assert elec['gas'] is gas
    assert [t for t in out if t.get('direction') == 'GAS'] == [gas, bundled]   # bundled gas listed once
    assert gas_offer(base | {'name': 'n', 'code': 'c'}, None, 30) is None


def test_annual_gas_seasonal_weighting():
    from analysis.tariffcompare import annual_gas
    daily = {f'2026-{m:02d}-01': 10.0 * m for m in range(1, 13)}
    daily['2025-01-01'] = 30.0                                  # January averages (10+30)/2 = 20/day
    year = annual_gas(daily)
    assert year['seasonal'] and year['monthly'][0] == pytest.approx(20 * 31)
    assert year['kwh'] == pytest.approx(20 * 31 + sum(10.0 * m * d for m, d in
                                                      zip(range(2, 13), (28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31))))
    partial = annual_gas({'2026-01-01': 10.0, '2026-07-01': 2.0})
    assert not partial['seasonal'] and partial['kwh'] == pytest.approx(365 * 6.0)


def test_current_gas_from_bills_or_config():
    from analysis.tariffcompare import current_gas
    body = {'charges': [
        {'fuel': 'Gas', 'start': '2026-08-20', 'rate': 5.124, 'standing': 34.06, 'kwh': 223.0},
        {'fuel': 'Gas', 'start': '2026-09-20', 'rate': 5.125, 'standing': 34.07, 'kwh': 352.0},
        {'fuel': 'Electricity', 'start': '2026-10-01', 'rate': 7.6, 'standing': 57.16, 'kwh': 414.0}]}
    g = current_gas(body, {})
    assert (g['code'], g['gas_rate'], g['gas_standing']) == ('CURRENT_GAS', 5.125, 34.07)
    g = current_gas({}, {'currentGas': {'rate': 6.0, 'standingCharge': 30.0}})
    assert (g['gas_rate'], g['gas_standing']) == (6.0, 30.0)
    assert current_gas({}, {}) is None


def test_compare_gas_and_overall_pick_own_or_cheapest_gas():
    from analysis.tariff_sources import gas_offer
    from analysis.tariffcompare import compare_gas, overall, price_gas
    g = lambda sup, code, r, s: gas_offer({'supplier': sup, 'name': code, 'code': code, 'source': 'x'}, r, s)  # noqa: E731
    cur = {**g('Current', 'CURRENT_GAS', 5.0, 34.0)}
    offers = [g('A', 'A_GAS', 8.0, 30.0), g('B', 'B_GAS', 7.0, 25.0)]
    rows = compare_gas(offers + [{'direction': 'IMPORT'}], cur, 10000.0)
    assert [r['code'] for r in rows] == ['CURRENT_GAS', 'B_GAS', 'A_GAS']
    assert rows[1]['gas_cost'] == pytest.approx(price_gas(offers[1], 10000.0)) == pytest.approx(700 + 91.25)
    assert rows[1]['vs_current'] == pytest.approx(price_gas(offers[1], 10000) - price_gas(cur, 10000))
    imp_cur = {'code': 'CURRENT', 'supplier': 'Current'}
    exp_cur = {'code': 'CURRENT_EXPORT'}
    imp_a = {'code': 'A1', 'supplier': 'A', 'gas': None}
    imp_x = {'code': 'X1', 'supplier': 'X', 'gas': g('X', 'X_GAS', 6.0, 20.0)}
    out = overall([(imp_cur, exp_cur, 500.0), (imp_a, exp_cur, 400.0), (imp_x, exp_cur, 450.0)], rows, 10000.0)
    by = {r[0]['code']: r for r in out}
    assert by['CURRENT'][3]['code'] == 'CURRENT_GAS' and by['CURRENT'][5] == pytest.approx(500 + 500 + 124.1)
    assert by['A1'][3]['code'] == 'A_GAS'                          # supplier's own gas
    assert by['A1'][5] == pytest.approx(400 + price_gas(offers[1], 10000))   # but cheapest anywhere wins
    assert by['X1'][5] == pytest.approx(450 + price_gas(imp_x['gas'], 10000))  # its own dual-fuel gas
    assert [r[5] for r in out] == sorted(r[5] for r in out)
