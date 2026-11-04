import pytest

from analysis.energyprices import EnergyPrices
from analysis.pricedata import PriceData
from analysis.projection import DAYS_IN_MONTH, SeasonalProjection, latest_rates
from analysis.virtualbattery import VirtualBattery
from tests.test_calculations import make_day

PRICES = {'data': {'prices': [
    {'datefrom': '2025-01-01', 'dateto': '2025-12-31', 'offpeakRate': '0.07', 'offpeakStart': '00:00',
     'offpeakStop': '07:00', 'peakRate': '0.28', 'exportRate': '0.165', 'standingCharge': '0.6'},
    {'datefrom': '2026-01-01', 'dateto': '2040-12-31', 'offpeakRate': '0.04', 'offpeakStart': '00:00',
     'offpeakStop': '06:00', 'peakRate': '0.25', 'exportRate': '0.13', 'standingCharge': '0.57'},
]}}


# ─── latest_rates ────────────────────────────────────────────────────────────

def test_latest_rates_picks_newest_started_period():
    assert latest_rates(PRICES, '2026-10-18') == {
        'peak': 0.25, 'off_peak': 0.04, 'export': 0.13, 'standing': 0.57, 'datefrom': '2026-01-01'}
    assert latest_rates(PRICES, '2025-06-01')['export'] == 0.165


def test_latest_rates_before_any_period_uses_newest():
    assert latest_rates(PRICES, '2020-01-01')['datefrom'] == '2026-01-01'


# ─── SeasonalProjection ──────────────────────────────────────────────────────

def test_partial_year_falls_back_to_simple_average():
    p = SeasonalProjection()
    p.add_day('2026-06-01', {'pv': 20.0})
    p.add_day('2026-06-02', {'pv': 10.0})
    assert not p.seasonal and p.months_covered == 1
    assert p.annual_kwh('pv') == pytest.approx(365 * 15.0)


def test_empty_projection_is_zero():
    assert SeasonalProjection().annual_kwh('pv') == 0.0


def test_month_seen_twice_counts_once():
    # June recorded in two years (20 days) must not outweigh the other months
    p = SeasonalProjection()
    for month in range(1, 13):
        p.add_day(f'2025-{month:02d}-01', {'pv': 1.0})
    for day in range(1, 21):
        p.add_day(f'2026-06-{day:02d}', {'pv': 1.0 if day % 2 else 5.0})
    assert p.seasonal
    june_avg = (1.0 + 10 * 1.0 + 10 * 5.0) / 21
    assert p.annual_kwh('pv') == pytest.approx(365 - 30 + 30 * june_avg)
    # the plain ×365/days average would be pulled up by the over-represented month
    assert p.annual_kwh('pv') < 365 * (12 + 60) / 32


def test_annual_money_prices_kwh_at_given_rates():
    p = SeasonalProjection()
    for month in range(1, 13):
        for _ in range(DAYS_IN_MONTH[month - 1]):
            p.add_day(f'2026-{month:02d}-01', {
                'load_peak': 8.0, 'import_peak': 3.0, 'load_off_peak': 2.0, 'import_off_peak': 2.0,
                'export': 4.0, 'batt_drawn': 2.0, 'batt_grid_charge': 1.0,
                'batt_pv_diverted': 1.0, 'batt_exported': 0.5})
    rates = {'peak': 0.25, 'off_peak': 0.04, 'export': 0.13}
    m = p.annual_money(rates)
    assert m['self_consumption'] == pytest.approx(365 * 5.0 * 0.25)
    assert m['export'] == pytest.approx(365 * 4.0 * 0.13)
    assert m['solar'] == pytest.approx(m['self_consumption'] + m['export'])
    assert m['battery'] == pytest.approx(365 * (2.0 * 0.25 + 0.5 * 0.13 - 1.0 * 0.04 - 1.0 * 0.13))


# ─── EnergyPrices integration ────────────────────────────────────────────────

def test_energy_prices_projection_reprices_old_days_at_latest_rates():
    ep = EnergyPrices(PRICES, VirtualBattery(PriceData()), original_price=1000.0, battery_enabled=False)
    for month in range(1, 13):
        ep.check_date(f'2025-{month:02d}-15')     # all in the old (higher SEG) period
        ep.add_data(make_day(calc_load_peak=6000, calc_import_peak=2000,
                             calc_load_offpeak=0, calc_import_offpeak=0, calc_export=3000))
    d = ep.get_derived()
    assert d['projection_seasonal']
    assert d['projection_rates']['export'] == 0.13
    # one day per month ⇒ annual = per-day × 365, priced at 25p / 13p, not 28p / 16.5p
    assert d['annualised_solar'] == pytest.approx(365 * (4.0 * 0.25 + 3.0 * 0.13), abs=0.01)
    assert d['solar_payback_years'] == pytest.approx(round(1000.0 / d['annualised_solar'], 1))
    assert d['solar_payback_remaining_years'] < d['solar_payback_years']
    assert d['annual_import_peak_kwh'] == pytest.approx(365 * 2.0)
    assert d['annual_import_off_peak_kwh'] == 0.0
    assert d['annual_import_peak_cost'] == pytest.approx(365 * 2.0 * 0.25, abs=0.01)
    assert d['annual_standing_charge'] == pytest.approx(365 * 0.57, abs=0.01)


def test_energy_prices_projection_tracks_battery_deltas_across_tariff_switch():
    ep = EnergyPrices(PRICES, VirtualBattery(PriceData()), battery_enabled=True)
    ep.check_date('2025-12-31')
    ep.battery.drawn += 2000
    ep.add_data(make_day())
    ep.check_date('2026-01-01')                 # new period ⇒ fresh battery, counters at 0
    ep.battery.drawn += 1000
    ep.add_data(make_day())
    assert ep.projection.annual_kwh('batt_drawn') == pytest.approx(365 * 1.5)
