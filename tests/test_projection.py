import pytest

from analysis.energyprices import EnergyPrices
from analysis.pricedata import PriceData
from analysis.projection import DAYS_IN_MONTH, SeasonalProjection, latest_rates, rebased_annual_ev
from analysis.virtualbattery import VirtualBattery
from analysis.energysummary import QueryType
from tests.test_calculations import MockEnergyDay, make_day

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


# ─── rebased EV year (second car) ────────────────────────────────────────────

def _ev_proj(months_kwh, year=2026, split=(0.1, 0.9, 0.0)):
    """One day per listed month with total EV kWh, split peak/off-peak/from-PV."""
    p = SeasonalProjection()
    for month, kwh in months_kwh.items():
        p.add_day(f'{year}-{month:02d}-01', {'ev_peak': kwh * split[0], 'ev_off_peak': kwh * split[1],
                                            'ev_from_pv': kwh * split[2]})
    return p


def test_rebased_ev_scales_unseen_months_by_overlap_ratio():
    before = _ev_proj({m: 5.0 for m in range(1, 13)}, year=2025, split=(0.5, 0.5, 0.0))
    after = _ev_proj({m: 12.0 for m in range(5, 11)}, split=(0.1, 0.9, 0.05))   # May-Oct, ×2.4
    ev, ratio, months = rebased_annual_ev(before, after)
    assert months == 6 and ratio == pytest.approx(2.4)
    assert ev['ev_peak'] + ev['ev_off_peak'] == pytest.approx(365 * 12.0)
    assert ev['ev_peak'] == pytest.approx(365 * 12.0 * 0.1)        # split follows the after period
    assert ev['ev_from_pv'] == pytest.approx(365 * 12.0 * 0.05)


def test_rebased_ev_without_after_data_is_seasonal():
    before = _ev_proj({m: 5.0 for m in range(1, 13)}, year=2025)
    ev, ratio, months = rebased_annual_ev(before, SeasonalProjection())
    assert months == 0 and ratio == 1.0
    assert ev['ev_peak'] + ev['ev_off_peak'] == pytest.approx(365 * 5.0)


def test_rebased_ev_unseen_months_take_the_average():
    after = _ev_proj({6: 10.0, 7: 10.0})
    ev, ratio, _ = rebased_annual_ev(SeasonalProjection(), after)
    assert ratio == 1.0 and ev['ev_peak'] + ev['ev_off_peak'] == pytest.approx(365 * 10.0)


class _EvDay(MockEnergyDay):
    """Off-peak-only day: 1 kWh of house import plus ``ev_kwh`` of EV charging."""
    def __init__(self, ev_kwh):
        super().__init__(calc_import=(ev_kwh + 1) * 1000, calc_import_offpeak=(ev_kwh + 1) * 1000,
                         calc_load=(ev_kwh + 1) * 1000, calc_load_offpeak=(ev_kwh + 1) * 1000)
        self._ev = ev_kwh * 1000

    def get_calc_ev(self, qtype=QueryType.BOTH):
        return 0.0 if qtype == QueryType.PEAK else self._ev

    def get_calc_ev_from_pv(self):
        return 0.0


def test_energy_prices_ev_basis_rebases_ev_and_import():
    def run(basis):
        ep = EnergyPrices(PRICES, VirtualBattery(PriceData()), battery_enabled=False, ev_basis_from=basis)
        for date, ev_kwh in [(f'2025-{m:02d}-15', 10.0) for m in range(1, 13)] + \
                            [(f'2026-{m:02d}-15', 20.0) for m in range(1, 7)]:
            ep.check_date(date)
            ep.add_data(_EvDay(ev_kwh))
        return ep.get_derived()
    seasonal, rebased = run(''), run('2026-01-01')
    # Jan-Jun average 15 kWh/day (both years), Jul-Dec 10
    assert seasonal['annual_ev_kwh'] == pytest.approx(181 * 15 + 184 * 10, abs=0.1)
    assert rebased['annual_ev_kwh'] == pytest.approx(365 * 20.0, abs=0.1)
    assert rebased['ev_basis_ratio'] == pytest.approx(2.0) and rebased['ev_basis_months'] == 6
    # house off-peak import (1 kWh/day) is kept; only the EV part is rebased
    assert rebased['annual_import_off_peak_kwh'] - rebased['annual_ev_kwh'] == pytest.approx(365, abs=0.2)
