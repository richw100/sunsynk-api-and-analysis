import pytest

from analysis.dailyseries import DailySeriesBuilder
from tests.test_calculations import MockEnergyDay, make_price_data


def test_add_day_computes_spend_and_savings_correctly():
    price_data = make_price_data(peak=0.30, offpeak=0.10, export=0.15, standing=0.60)
    day = MockEnergyDay(
        calc_import_peak=2000, calc_import_offpeak=1000,   # Wh
        calc_load_peak=3000, calc_load_offpeak=1000,
        calc_export=500,
        calc_pv=4000, calc_load=4000,
    )
    builder = DailySeriesBuilder(cumulative_investment=6000)
    row = builder.add_day('2025-06-10', day, price_data, {
        'charge_kwh': 0.0, 'discharge_kwh': 0.0, 'pv_charge_kwh': 0.0, 'export_kwh': 0.0,
    })

    expected_spend = 2.0 * 0.30 + 1.0 * 0.10 + 0.60
    expected_cost_without_solar = 3.0 * 0.30 + 1.0 * 0.10 + 0.60
    expected_export_income = 0.5 * 0.15
    expected_savings = expected_cost_without_solar - expected_spend
    expected_savings_inc_seg = expected_savings + expected_export_income

    assert row.spend == pytest.approx(expected_spend)
    assert row.export_income == pytest.approx(expected_export_income)
    assert row.savings == pytest.approx(expected_savings)
    assert row.savings_inc_seg == pytest.approx(expected_savings_inc_seg)
    assert row.import_kwh == pytest.approx(3.0)
    assert row.export_kwh == pytest.approx(0.5)
    assert row.pv_kwh == pytest.approx(4.0)
    assert row.load_kwh == pytest.approx(4.0)


def test_add_day_accumulates_cumulative_savings_across_days():
    price_data = make_price_data()
    builder = DailySeriesBuilder(cumulative_investment=1000)
    zero_delta = {'charge_kwh': 0.0, 'discharge_kwh': 0.0, 'pv_charge_kwh': 0.0, 'export_kwh': 0.0}

    day1 = MockEnergyDay(calc_import_peak=500, calc_load_peak=1000)
    row1 = builder.add_day('2025-06-10', day1, price_data, zero_delta)

    day2 = MockEnergyDay(calc_import_peak=500, calc_load_peak=1000, calc_export=500)
    row2 = builder.add_day('2025-06-11', day2, price_data, zero_delta)

    assert row1.cumulative_savings == pytest.approx(row1.savings_inc_seg)
    assert row2.cumulative_savings == pytest.approx(row1.savings_inc_seg + row2.savings_inc_seg)
    assert row2.cumulative_savings != pytest.approx(row2.savings_inc_seg)


def test_add_day_roi_pct_relative_to_cumulative_investment():
    price_data = make_price_data()
    builder = DailySeriesBuilder(cumulative_investment=200)
    day = MockEnergyDay(calc_import_peak=0, calc_load_peak=1000)  # full self-consumption
    row = builder.add_day('2025-06-10', day, price_data, {
        'charge_kwh': 0.0, 'discharge_kwh': 0.0, 'pv_charge_kwh': 0.0, 'export_kwh': 0.0,
    })
    assert row.roi_pct == pytest.approx(100 * row.cumulative_savings / 200)


def test_add_day_battery_deltas_pass_through_unchanged():
    price_data = make_price_data()
    builder = DailySeriesBuilder(cumulative_investment=1000)
    day = MockEnergyDay()
    delta = {'charge_kwh': 1.2, 'discharge_kwh': 0.9, 'pv_charge_kwh': 0.3, 'export_kwh': 0.1}
    row = builder.add_day('2025-06-10', day, price_data, delta)

    assert row.battery_charge_kwh == 1.2
    assert row.battery_discharge_kwh == 0.9
    assert row.battery_pv_charge_kwh == 0.3
    assert row.battery_export_kwh == 0.1


def test_daily_series_builder_rows_and_to_dicts_roundtrip():
    price_data = make_price_data()
    builder = DailySeriesBuilder(cumulative_investment=1000)
    zero_delta = {'charge_kwh': 0.0, 'discharge_kwh': 0.0, 'pv_charge_kwh': 0.0, 'export_kwh': 0.0}
    builder.add_day('2025-06-10', MockEnergyDay(), price_data, zero_delta)
    builder.add_day('2025-06-11', MockEnergyDay(), price_data, zero_delta)

    rows = builder.rows()
    dicts = builder.to_dicts()

    assert len(rows) == 2
    assert len(dicts) == 2
    assert [r.date for r in rows] == ['2025-06-10', '2025-06-11']
    assert dicts[0]['date'] == '2025-06-10'
    assert dicts[1]['cumulative_savings'] == pytest.approx(rows[1].cumulative_savings)
