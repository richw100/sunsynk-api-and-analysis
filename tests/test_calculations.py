import pytest
from datetime import datetime

from analysis.energysummary import EnergySummary, EnergySummaryAggregator, QueryType
from analysis.pricedata import PriceData
from analysis.virtualbattery import VirtualBattery
from analysis.energyprices import EnergyPrices


def make_price_data(peak=0.30, offpeak=0.10, export=0.15, compare=0.25,
                    standing=0.60, compare_standing=0.53,
                    offpeak_start="00:00", offpeak_stop="06:00",
                    interest_rate=3.7):
    pd = PriceData()
    pd.current_peak = peak
    pd.current_off_peak = offpeak
    pd.current_export = export
    pd.compare_rate = compare
    pd.standing_charge = standing
    pd.compare_standing_charge = compare_standing
    pd.current_off_peak_start = offpeak_start
    pd.current_off_peak_stop = offpeak_stop
    pd.interest_rate = interest_rate
    return pd


class MockEnergyDay:
    """Minimal EnergyDay stub for testing EnergySummary without API calls."""
    def __init__(self, calc_import=0, calc_import_peak=0, calc_import_offpeak=0,
                 calc_export=0, calc_export_peak=0, calc_export_offpeak=0,
                 calc_pv=0, calc_load=0, calc_load_peak=0, calc_load_offpeak=0,
                 supplied_load=0.0, supplied_import=0.0, supplied_pv=0.0, supplied_export=0.0):
        self._import = calc_import
        self._import_peak = calc_import_peak
        self._import_offpeak = calc_import_offpeak
        self._export = calc_export
        self._export_peak = calc_export_peak
        self._export_offpeak = calc_export_offpeak
        self._pv = calc_pv
        self._load = calc_load
        self._load_peak = calc_load_peak
        self._load_offpeak = calc_load_offpeak
        self._supplied_load = supplied_load
        self._supplied_import = supplied_import
        self._supplied_pv = supplied_pv
        self._supplied_export = supplied_export

    def get_calc_import(self, qtype=QueryType.BOTH):
        if qtype == QueryType.PEAK:    return self._import_peak
        if qtype == QueryType.OFFPEAK: return self._import_offpeak
        return self._import

    def get_calc_export(self, qtype=QueryType.BOTH):
        if qtype == QueryType.PEAK:    return self._export_peak
        if qtype == QueryType.OFFPEAK: return self._export_offpeak
        return self._export

    def get_calc_pv(self):              return self._pv
    def get_calc_load(self):            return self._load
    def get_calc_load_peak(self):       return self._load_peak
    def get_calc_load_off_peak(self):   return self._load_offpeak
    def get_supplied_load(self):        return self._supplied_load
    def get_supplied_import(self):      return self._supplied_import
    def get_supplied_pv(self):          return self._supplied_pv
    def get_supplied_export(self):      return self._supplied_export


# ─── VirtualBattery ──────────────────────────────────────────────────────────

def test_virtual_battery_recharge_fills_and_tracks_charge_amount():
    battery = VirtualBattery(PriceData(), battery_size=5000)
    battery.battery_status = 3000
    battery.recharge()
    assert battery.battery_status == 5000
    assert battery.charge_amount == 2000
    assert battery.get_charge_amount_kwh() == 2.0


def test_virtual_battery_utilise_draws_with_92_percent_efficiency():
    battery = VirtualBattery(PriceData(), battery_size=5000)
    t = datetime.strptime("12:00", "%H:%M")
    result = battery.utilise(100, t)
    assert result == 0
    assert battery.battery_status == 4900
    assert battery.drawn == pytest.approx(92.0)
    assert battery.total_drawn_kwh() == 0.09


def test_virtual_battery_utilise_returns_shortfall_when_exhausted():
    battery = VirtualBattery(PriceData(), battery_size=1000)
    battery.battery_status = 100
    t = datetime.strptime("12:00", "%H:%M")
    result = battery.utilise(200, t)
    assert battery.battery_status == 0
    assert result > 0
    assert battery.drawn == pytest.approx(100 * 0.92)
    assert battery.extra_required == pytest.approx(result)


def test_virtual_battery_set_ran_out_increments_days():
    battery = VirtualBattery(PriceData())
    battery.set_ran_out()
    battery.set_ran_out()
    assert battery.days_run_out == 2


def test_virtual_battery_pv_charge_does_nothing_when_disabled():
    battery = VirtualBattery(PriceData(), battery_size=5000, use_pv=0)
    battery.battery_status = 500
    t = datetime.strptime("12:00", "%H:%M")
    battery.pv_charge(100, t)
    assert battery.battery_status == 500
    assert battery.pv_input == 0


def test_virtual_battery_pv_charge_charges_at_96_percent_efficiency():
    battery = VirtualBattery(PriceData(), battery_size=5000, use_pv=1,
                             start_charging=1000, stop_charging=2000)
    battery.battery_status = 500  # below start_charging → triggers should_pv_charge=1
    t = datetime.strptime("12:00", "%H:%M")
    battery.pv_charge(100, t)
    assert battery.battery_status == pytest.approx(500 + 100 * 0.96)
    assert battery.pv_input == pytest.approx(100 * 0.96)


def test_virtual_battery_pv_charge_stops_at_stop_threshold():
    battery = VirtualBattery(PriceData(), battery_size=5000, use_pv=1,
                             start_charging=1000, stop_charging=2000)
    battery.battery_status = 2000  # at stop_charging → clears should_pv_charge
    battery.should_pv_charge = 1
    t = datetime.strptime("12:00", "%H:%M")
    battery.pv_charge(100, t)
    assert battery.battery_status == 2000
    assert battery.pv_input == 0


def test_virtual_battery_get_savings_computes_correctly():
    pd = make_price_data(peak=0.30, offpeak=0.10, export=0.15)
    battery = VirtualBattery(pd, battery_size=10000)
    battery.charge_amount = 10000  # 10 kWh charged off-peak
    battery.drawn = 5000           # 5 kWh drawn at peak
    battery.pv_input = 2000        # 2 kWh from PV (foregone export)
    battery.exported = 1000        # 1 kWh re-exported

    battery.get_savings()

    assert battery.cost_from_grid   == pytest.approx(10000 / 1000 * 0.10, rel=1e-6)  # 1.00
    assert battery.nominal_cost     == pytest.approx(5000 * 0.30 / 1000,  rel=1e-6)  # 1.50
    assert battery.lost_export_cost == pytest.approx(2000 * 0.15 / 1000,  rel=1e-6)  # 0.30
    assert battery.export_cost      == pytest.approx(1000 / 1000 * 0.15,  rel=1e-6)  # 0.15
    assert battery.savings          == pytest.approx(1.50 + 0.15 - 1.00 - 0.30)      # 0.35


# ─── EnergySummary ───────────────────────────────────────────────────────────

def make_summary(interest_rate=3.7, cumulative=0.0, alt_invest=0.0):
    pd = make_price_data(interest_rate=interest_rate)
    battery = VirtualBattery(pd)
    return EnergySummary(pd, battery, current_cumulative_savings=cumulative,
                         current_alt_investment_value=alt_invest), pd, battery


def make_day(calc_import=95, calc_import_peak=50, calc_import_offpeak=45,
             calc_export=10, calc_export_peak=10, calc_export_offpeak=0,
             calc_pv=200, calc_load=200, calc_load_peak=150, calc_load_offpeak=50,
             supplied_load=5.2, supplied_import=1.4, supplied_pv=8.3, supplied_export=2.1):
    return MockEnergyDay(
        calc_import=calc_import, calc_import_peak=calc_import_peak,
        calc_import_offpeak=calc_import_offpeak,
        calc_export=calc_export, calc_export_peak=calc_export_peak,
        calc_export_offpeak=calc_export_offpeak,
        calc_pv=calc_pv, calc_load=calc_load,
        calc_load_peak=calc_load_peak, calc_load_offpeak=calc_load_offpeak,
        supplied_load=supplied_load, supplied_import=supplied_import,
        supplied_pv=supplied_pv, supplied_export=supplied_export,
    )


def test_energy_summary_add_data_accumulates_totals():
    summary, _, _ = make_summary()
    summary.add_data(make_day())

    assert summary.days == 1
    assert summary.total_calc_import == 95
    assert summary.total_calc_import_peak == 50
    assert summary.total_calc_import_off_peak == 45
    assert summary.total_calc_export == 10
    assert summary.total_calc_pv == 200
    assert summary.total_calc_load == 200
    assert summary.total_supplied_load == pytest.approx(5.2)
    assert summary.total_supplied_import == pytest.approx(1.4)
    assert summary.total_saved_calc == 200 - 95  # load minus import (Wh)


def test_energy_summary_add_data_accumulates_across_multiple_days():
    summary, _, _ = make_summary()
    summary.add_data(make_day(calc_import=95, calc_pv=200))
    summary.add_data(make_day(calc_import=80, calc_pv=150))

    assert summary.days == 2
    assert summary.total_calc_import == 175
    assert summary.total_calc_pv == 350


def test_energy_summary_recalculate_total_cost():
    summary, _, _ = make_summary()
    summary.add_data(make_day())
    summary.recalculate()

    # total_cost = (peak_import_kWh * peak_rate) + (offpeak_import_kWh * offpeak_rate) + standing
    expected = (50 / 1000 * 0.30) + (45 / 1000 * 0.10) + 0.60
    assert summary.total_cost == pytest.approx(expected, rel=1e-6)


def test_energy_summary_recalculate_compare_cost():
    summary, _, _ = make_summary()
    summary.add_data(make_day())
    summary.recalculate()

    expected = (95 / 1000 * 0.25) + 0.53
    assert summary.compare_cost == pytest.approx(expected, rel=1e-6)


def test_energy_summary_recalculate_export_amount():
    summary, _, _ = make_summary()
    summary.add_data(make_day())
    summary.recalculate()

    expected = (10 / 1000) * 0.15
    assert summary.total_export_amount_calc == pytest.approx(expected, rel=1e-6)


def test_energy_summary_new_month_adds_interest_to_cumulative():
    summary, _, _ = make_summary(interest_rate=12.0, cumulative=1000.0, alt_invest=2000.0)
    summary.add_data(make_day())

    summary.new_month()

    # alt_invest_value grows by 1% per month (12% / 1200 * month)
    assert summary.alt_invest_value == pytest.approx(2000.0 * (1 + 12.0 / 1200), rel=1e-6)
    # cumulative_savings grows by interest (10) plus any period savings
    assert summary.cumulative_savings > 1000.0


# ─── EnergySummaryAggregator ─────────────────────────────────────────────────

def test_aggregator_grand_totals_sums_across_periods():
    pd1 = make_price_data()
    s1 = EnergySummary(pd1, VirtualBattery(pd1))
    s1.add_data(make_day(calc_import=100, calc_import_peak=60, calc_import_offpeak=40,
                         calc_pv=200, calc_load=200, calc_load_peak=160, calc_load_offpeak=40,
                         supplied_load=5.0, supplied_import=2.0, supplied_pv=4.0, supplied_export=1.0,
                         calc_export=20, calc_export_peak=20, calc_export_offpeak=0))

    pd2 = make_price_data()
    s2 = EnergySummary(pd2, VirtualBattery(pd2))
    s2.add_data(make_day(calc_import=80, calc_import_peak=40, calc_import_offpeak=40,
                         calc_pv=150, calc_load=150, calc_load_peak=110, calc_load_offpeak=40,
                         supplied_load=4.0, supplied_import=1.5, supplied_pv=3.0, supplied_export=0.5,
                         calc_export=10, calc_export_peak=10, calc_export_offpeak=0))

    agg = EnergySummaryAggregator()
    agg.add_summary(s1)
    agg.add_summary(s2)
    totals = agg.get_grand_totals()

    assert totals["days"] == 2
    assert totals["total_calc_import"] == pytest.approx((100 + 80) / 1000, rel=1e-6)
    assert totals["total_calc_pv"] == pytest.approx((200 + 150) / 1000, rel=1e-6)
    assert totals["total_supplied_load"] == pytest.approx(5.0 + 4.0, rel=1e-6)
    assert totals["total_calc_export"] == pytest.approx((20 + 10) / 1000, rel=1e-6)


def test_aggregator_get_grand_totals_rounds_to_two_decimal_places():
    pd = make_price_data()
    s = EnergySummary(pd, VirtualBattery(pd))
    s.add_data(make_day(calc_import=1, calc_pv=1, calc_load=1,
                        calc_import_peak=1, calc_import_offpeak=0,
                        calc_load_peak=1, calc_load_offpeak=0))
    agg = EnergySummaryAggregator()
    agg.add_summary(s)
    totals = agg.get_grand_totals()
    for val in totals.values():
        if isinstance(val, float):
            assert val == round(val, 2)


def test_aggregator_update_last_summary():
    pd = make_price_data()
    s = EnergySummary(pd, VirtualBattery(pd))
    agg = EnergySummaryAggregator()
    agg.add_summary(s)
    agg.update_last_summary(make_day(calc_import=50))
    assert agg.get_last_summary().total_calc_import == 50


def test_aggregator_raises_when_empty():
    agg = EnergySummaryAggregator()
    with pytest.raises(IndexError):
        agg.update_last_summary(make_day())
    with pytest.raises(IndexError):
        agg.get_last_summary()


# ─── VirtualBattery.discharge() ──────────────────────────────────────────────

def test_virtual_battery_discharge_exports_during_window():
    battery = VirtualBattery(PriceData(), battery_size=5000)
    battery.export = 1
    battery.battery_status = 3000
    t = datetime.strptime("18:00", "%H:%M")  # within 17:00-19:00 export window
    battery.discharge(t)
    assert battery.battery_status < 3000
    assert battery.exported > 0


def test_virtual_battery_discharge_no_effect_outside_window():
    battery = VirtualBattery(PriceData(), battery_size=5000)
    battery.export = 1
    battery.battery_status = 3000
    t = datetime.strptime("12:00", "%H:%M")  # outside export window
    battery.discharge(t)
    assert battery.battery_status == 3000
    assert battery.exported == 0


def test_virtual_battery_pv_charge_does_not_overflow_battery():
    # When battery_status + charge > battery_size, no charge is added.
    battery = VirtualBattery(PriceData(), battery_size=5000, use_pv=1,
                             start_charging=1000, stop_charging=2000)
    battery.battery_status = 4990
    battery.should_pv_charge = 1
    t = datetime.strptime("12:00", "%H:%M")
    battery.pv_charge(200, t)  # 200*0.96=192; 4990+192=5182 > 5000 → no charge
    assert battery.battery_status == 4990
    assert battery.pv_input == 0


def test_virtual_battery_get_pv_charge_kwh():
    battery = VirtualBattery(PriceData(), battery_size=5000, use_pv=1,
                             start_charging=1000, stop_charging=2000)
    battery.battery_status = 500
    t = datetime.strptime("12:00", "%H:%M")
    battery.pv_charge(100, t)
    assert battery.get_pv_charge_kwh() == pytest.approx(round(100 * 0.96 / 1000, 2))


# ─── EnergyPrices ────────────────────────────────────────────────────────────

SAMPLE_PRICES = {
    'data': {
        'prices': [
            {
                'datefrom': '2024-01-01',
                'dateto': '2024-12-31',
                'offpeakRate': '0.075',
                'offpeakStart': '00:30',
                'offpeakStop': '07:30',
                'peakRate': '0.28',
                'exportRate': '0.15',
                'standingCharge': '0.60',
                'CompareStandingCharge': '0.53',
                'CompareRate': '0.25',
                'InterestRate': '3.5'
            },
            {
                'datefrom': '2025-01-01',
                'dateto': '2026-12-31',
                'offpeakRate': '0.085',
                'offpeakStart': '00:30',
                'offpeakStop': '07:30',
                'peakRate': '0.30',
                'exportRate': '0.165',
                'standingCharge': '0.65',
                'CompareStandingCharge': '0.55',
                'CompareRate': '0.27',
                'InterestRate': '3.7'
            }
        ]
    }
}


def test_energy_prices_init():
    battery = VirtualBattery(PriceData())
    ep = EnergyPrices(SAMPLE_PRICES, battery)
    assert ep.price_data is not None
    assert ep.grand_totals is not None
    assert ep.grand_totals['days'] == 0


def test_energy_prices_check_date_switches_period():
    # 2024-06-15 is before the default PriceData range (2025-07-10)
    # so check_date should switch to the 2024 period from SAMPLE_PRICES.
    battery = VirtualBattery(PriceData())
    ep = EnergyPrices(SAMPLE_PRICES, battery)
    ep.check_date('2024-06-15')
    assert ep.price_data.current_peak == pytest.approx(0.28)
    assert ep.price_data.current_off_peak == pytest.approx(0.075)


def test_energy_prices_check_date_in_range_no_switch():
    # 2025-09-01 is within the default range (2025-07-10 to 2026-09-10);
    # peak rate should remain at the PriceData default.
    battery = VirtualBattery(PriceData())
    ep = EnergyPrices(SAMPLE_PRICES, battery)
    default_peak = ep.price_data.current_peak
    ep.check_date('2025-09-15')  # not the 1st, so no new_month either
    assert ep.price_data.current_peak == default_peak


def test_energy_prices_check_date_new_month_does_not_raise():
    # First-of-month date triggers new_month() on the last EnergySummary.
    battery = VirtualBattery(PriceData())
    ep = EnergyPrices(SAMPLE_PRICES, battery)
    ep.check_date('2025-09-01')  # within default range, but first of month
    totals = ep.get_grand_totals()
    assert totals is not None


def test_energy_prices_add_data_and_get_grand_totals():
    battery = VirtualBattery(PriceData())
    ep = EnergyPrices(SAMPLE_PRICES, battery)
    ep.check_date('2024-06-15')  # switch to 2024 period
    ep.add_data(make_day(calc_import=100, calc_import_peak=60, calc_import_offpeak=40,
                         calc_export=20, calc_export_peak=20, calc_export_offpeak=0,
                         calc_pv=200, calc_load=200, calc_load_peak=160, calc_load_offpeak=40,
                         supplied_load=5.0, supplied_import=2.0, supplied_pv=4.0, supplied_export=1.0))
    totals = ep.get_grand_totals()
    assert totals['days'] == 1
    assert totals['total_calc_import'] == pytest.approx(100 / 1000, rel=1e-6)


def test_energy_prices_get_grand_totals_uses_cache():
    # Second call to get_grand_totals() should return the cached result.
    battery = VirtualBattery(PriceData())
    ep = EnergyPrices(SAMPLE_PRICES, battery)
    ep.add_data(make_day(calc_import=50, calc_import_peak=50, calc_import_offpeak=0,
                         calc_load=50, calc_load_peak=50, calc_load_offpeak=0))
    totals1 = ep.get_grand_totals()
    totals2 = ep.get_grand_totals()  # should use cached result (changed=False)
    assert totals1 == totals2
