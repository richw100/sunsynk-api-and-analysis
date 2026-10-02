import os

import pytest

from analysis.collectdata import _parse_cli_args
from analysis.dailyseries import DailyRow
from analysis.graphs import (
    _resolve_output_path, _rolling_average, _fig_energy_flows, _fig_battery_daily, _fig_net_cost,
    _fig_roi_rate_gbp, _fig_roi_rate_pct, _select_extreme_days, _select_extreme_roi_days,
    _interval_time_series, _grid_import_export_series, _fig_day_interval_detail,
    _interval_detail_figures, _top_roi_days_interval_figures, _bottom_roi_days_interval_figures,
    _top_spend_days_interval_figures, _fuse_hourly_series,
)


# ─── _resolve_output_path ────────────────────────────────────────────────────

def test_resolve_output_path_default_is_timestamped_in_results():
    path = _resolve_output_path({'description': ''})
    assert path.endswith('-graphs.html')
    assert os.sep + 'results' + os.sep in path


def test_resolve_output_path_description_is_sanitized():
    path = _resolve_output_path({'description': 'a b/c!d'})
    filename = os.path.basename(path)
    assert 'a_b_c_d' in filename
    assert filename.endswith('-graphs.html')


def test_resolve_output_path_explicit_override():
    path = _resolve_output_path({'outputPath': 'my-report.html'})
    assert path.endswith(os.path.join('sunsynk', 'my-report.html')) or path.endswith('my-report.html')


def test_resolve_output_path_absolute_override_unchanged():
    path = _resolve_output_path({'outputPath': '/tmp/out.html'})
    assert path == '/tmp/out.html'


# ─── _rolling_average ────────────────────────────────────────────────────────

def test_rolling_average_window_one_is_identity():
    values = [1.0, 5.0, -2.0, 3.5]
    assert _rolling_average(values, 1) == values


def test_rolling_average_uses_fewer_points_at_start():
    values = [10.0, 20.0, 30.0, 40.0]
    result = _rolling_average(values, 3)
    assert result[0] == pytest.approx(10.0)                    # just itself
    assert result[1] == pytest.approx((10.0 + 20.0) / 2)        # 2 points so far
    assert result[2] == pytest.approx((10.0 + 20.0 + 30.0) / 3)  # full window
    assert result[3] == pytest.approx((20.0 + 30.0 + 40.0) / 3)  # trailing window of 3


def test_rolling_average_same_length_as_input():
    values = list(range(50))
    assert len(_rolling_average(values, 30)) == len(values)


# ─── _parse_cli_args new keys ─────────────────────────────────────────────────

def test_parse_cli_graph_keys():
    _, overrides = _parse_cli_args(
        ['outputPath:my.html', 'rollingWindow:14', 'plotlyCdn:ON', 'fuseDataDir:/tmp/fz'])
    assert overrides['fuseDataDir'] == '/tmp/fz'
    assert overrides['outputPath'] == 'my.html'
    assert overrides['rollingWindow'] == 14
    assert overrides['plotlyCdn'] == 'ON'


# ─── figure builders (structural checks only) ─────────────────────────────────

def _make_rows():
    return [
        DailyRow(date='2025-06-10', pv_kwh=5.0, load_kwh=4.0, export_kwh=1.5, import_kwh=0.5,
                 spend=0.3, export_income=0.2, savings=0.9, savings_inc_seg=1.1,
                 cumulative_savings=1.1, cumulative_investment=6000, roi_pct=0.02,
                 battery_charge_kwh=1.0, battery_discharge_kwh=0.8,
                 battery_pv_charge_kwh=0.2, battery_export_kwh=0.1),
        DailyRow(date='2025-06-11', pv_kwh=2.0, load_kwh=4.0, export_kwh=0.0, import_kwh=2.0,
                 spend=1.2, export_income=0.0, savings=0.2, savings_inc_seg=0.2,
                 cumulative_savings=1.3, cumulative_investment=6000, roi_pct=0.022,
                 battery_charge_kwh=0.5, battery_discharge_kwh=0.4,
                 battery_pv_charge_kwh=0.1, battery_export_kwh=0.0),
    ]


def test_fig_energy_flows_without_fuse_has_four_traces():
    for fuse in (None, {}, {'2024-01-01': 3.0}):   # out-of-range data is dropped too
        fig = _fig_energy_flows(_make_rows(), fuse)
        assert [t.name for t in fig.data] == ['PV', 'Load', 'Export', 'Import']


def test_fig_energy_flows_overlays_fuse_export_within_row_range():
    fuse = {'2025-06-09': 9.0, '2025-06-10': 1.4, '2025-06-11': 0.1, '2025-06-12': 9.0}
    fig = _fig_energy_flows(_make_rows(), fuse)
    assert [t.name for t in fig.data] == ['PV', 'Load', 'Export', 'Export (Fuse meter)', 'Import']
    trace = fig.data[3]
    assert list(trace.x) == ['2025-06-10', '2025-06-11']
    assert list(trace.y) == [1.4, 0.1]
    assert trace.line.dash == 'dash'


def _make_ranked_rows(n=10):
    """n rows with distinct, easily-checkable savings_inc_seg values (n, n-1, ..., 1),
    one per calendar month (2025-01-15, 2025-02-15, ...) so the one-day-per-month cap
    never interferes with these plain ranking tests. spend is independently ranked in
    the opposite order (1, 2, ..., n) so spend-based and ROI-based selection can be
    tested separately without one masking the other; load_kwh mirrors spend as the
    checkable field for spend-day figures."""
    return [
        DailyRow(date=f'2025-{i + 1:02d}-15', pv_kwh=float(n - i), load_kwh=float(i + 1),
                 export_kwh=0.0, import_kwh=0.0, spend=float(i + 1), export_income=0.0,
                 savings=0.0, savings_inc_seg=float(n - i),
                 cumulative_savings=0.0, cumulative_investment=1000, roi_pct=0.0,
                 battery_charge_kwh=0.0, battery_discharge_kwh=0.0,
                 battery_pv_charge_kwh=0.0, battery_export_kwh=0.0)
        for i in range(n)
    ]


def test_select_extreme_roi_days_orders_top_desc_bottom_asc():
    rows = _make_ranked_rows(10)  # savings_inc_seg: 10, 9, ..., 1
    top, bottom = _select_extreme_roi_days(rows, n=5)
    assert [r.savings_inc_seg for _, r in top] == [10, 9, 8, 7, 6]
    assert [rank for rank, _ in top] == [1, 2, 3, 4, 5]
    assert [r.savings_inc_seg for _, r in bottom] == [1, 2, 3, 4, 5]
    assert [rank for rank, _ in bottom] == [1, 2, 3, 4, 5]


def test_select_extreme_roi_days_caps_at_available_rows():
    rows = _make_ranked_rows(3)
    top, bottom = _select_extreme_roi_days(rows, n=5)
    assert len(top) == 3
    assert len(bottom) == 3


def test_select_extreme_days_ranks_by_arbitrary_key():
    rows = _make_ranked_rows(10)  # spend: 1, 2, ..., 10 (opposite order to savings_inc_seg)
    top, bottom = _select_extreme_days(rows, key_func=lambda r: r.spend, n=5)
    assert [r.spend for _, r in top] == [10, 9, 8, 7, 6]
    assert [r.spend for _, r in bottom] == [1, 2, 3, 4, 5]


def test_select_extreme_days_caps_at_one_per_calendar_month():
    # 5 days in June (savings_inc_seg 5,4,3,2,1), one day in July (4.5) ranked between
    # June's #1 and #2, and one day in August (0.5) ranked last. Full ranking by
    # savings_inc_seg desc: June-10(5)#1, Jul-01(4.5)#2, June-11(4)#3, June-12(3)#4,
    # June-13(2)#5, June-14(1)#6, Aug-01(0.5)#7 — only 3 distinct months exist, so
    # requesting n=3 must pick one day per month while preserving each day's TRUE
    # rank (#1, #2, #7), not renumbering them (#1, #2, #3).
    def _row(date, value):
        return DailyRow(date=date, pv_kwh=0, load_kwh=0, export_kwh=0, import_kwh=0,
                         spend=0, export_income=0, savings=0, savings_inc_seg=value,
                         cumulative_savings=0, cumulative_investment=1000, roi_pct=0,
                         battery_charge_kwh=0, battery_discharge_kwh=0,
                         battery_pv_charge_kwh=0, battery_export_kwh=0)

    rows = [_row(f'2025-06-{10 + i:02d}', float(5 - i)) for i in range(5)]
    rows.append(_row('2025-07-01', 4.5))
    rows.append(_row('2025-08-01', 0.5))

    top, _ = _select_extreme_days(rows, key_func=lambda r: r.savings_inc_seg, n=3)

    assert [rank for rank, _ in top] == [1, 2, 7]
    assert [r.date for _, r in top] == ['2025-06-10', '2025-07-01', '2025-08-01']


# ─── 5-minute interval detail charts ───────────────────────────────────────────

class _FakeIntervalSummary:
    def __init__(self, records):
        self.records = records


class _FakeEnergyDay:
    def __init__(self, pv_records, load_records, grid_records):
        self.pv = _FakeIntervalSummary(pv_records)
        self.load = _FakeIntervalSummary(load_records)
        self.grid = _FakeIntervalSummary(grid_records)


def _r(time, value):
    return {'time': time, 'value': str(float(value))}


def _make_fake_energyday():
    # Grid: import at 08:00 (500W), export at 12:00 (-300W) — out of chronological
    # order in the raw list, to also exercise the by-time sort.
    return _FakeEnergyDay(
        pv_records=[_r('12:00', 1200), _r('08:00', 400)],
        load_records=[_r('12:00', 700), _r('08:00', 500)],
        grid_records=[_r('12:00', -300), _r('08:00', 500)],
    )


def test_interval_time_series_sorted_by_time():
    energyday = _make_fake_energyday()
    times, values = _interval_time_series(energyday.pv)
    assert times == ['08:00', '12:00']
    assert values == [400.0, 1200.0]


def test_grid_import_export_series_splits_by_sign():
    energyday = _make_fake_energyday()
    times, import_w, export_w = _grid_import_export_series(energyday.grid)
    assert times == ['08:00', '12:00']
    assert import_w == [500.0, 0.0]
    assert export_w == [0.0, 300.0]


def test_fig_day_interval_detail_has_four_traces_from_raw_records():
    fig = _fig_day_interval_detail(_make_fake_energyday(), 'Test day')
    assert len(fig.data) == 4
    assert [t.name for t in fig.data] == ['PV', 'Load', 'Export', 'Import']
    assert list(fig.data[0].x) == ['08:00', '12:00']
    assert fig.layout.title.text == 'Test day'


def test_fuse_hourly_series_kwh_to_avg_watts_with_closing_point():
    times, watts = _fuse_hourly_series([(22, 0.0), (23, 0.25)])
    assert times == ['22:00', '23:00', '23:55']
    assert watts == [0.0, 250.0, 250.0]
    assert _fuse_hourly_series([(12, 1.5)]) == (['12:00'], [1500.0])
    assert _fuse_hourly_series([]) == ([], [])


def test_fig_day_interval_detail_overlays_fuse_hourly():
    fig = _fig_day_interval_detail(_make_fake_energyday(), 'Test day', [(8, 0.0), (12, 0.3)])
    assert [t.name for t in fig.data] == [
        'PV', 'Load', 'Export', 'Export (Fuse meter, hourly avg)', 'Import']
    trace = fig.data[3]
    assert list(trace.x) == ['08:00', '12:00']
    assert list(trace.y) == [0.0, 300.0]
    assert trace.line.shape == 'hv'


def test_interval_detail_figures_passes_fuse_lookup_per_date():
    rows, energyday_by_date = _make_ranked_rows_with_energydays(2)
    ranked = list(enumerate(rows, start=1))
    hourly = {rows[0].date: [(12, 1.0)]}   # second date has no Fuse data
    figures = _interval_detail_figures(ranked, energyday_by_date, 'Rank',
                                       fuse_hourly_lookup=lambda d: hourly.get(d, []))
    assert len(figures[0][1].data) == 5
    assert len(figures[1][1].data) == 4


def _make_ranked_rows_with_energydays(n=10):
    rows = _make_ranked_rows(n)
    energyday_by_date = {row.date: _make_fake_energyday() for row in rows}
    return rows, energyday_by_date


def test_interval_detail_figures_one_per_row_in_order():
    rows, energyday_by_date = _make_ranked_rows_with_energydays(3)
    ranked = list(enumerate(rows, start=1))
    figures = _interval_detail_figures(ranked, energyday_by_date, 'Rank')
    assert len(figures) == 3
    titles = [title for title, _ in figures]
    assert titles == [
        f'Rank #1: {rows[0].date} (5-minute interval detail)',
        f'Rank #2: {rows[1].date} (5-minute interval detail)',
        f'Rank #3: {rows[2].date} (5-minute interval detail)',
    ]


def test_interval_detail_figures_shows_true_rank_not_list_position():
    rows, energyday_by_date = _make_ranked_rows_with_energydays(3)
    ranked = [(1, rows[0]), (7, rows[1])]  # e.g. rank 7 after month-uniqueness skipped ahead
    figures = _interval_detail_figures(ranked, energyday_by_date, 'Rank')
    assert figures[1][0].startswith(f'Rank #7: {rows[1].date}')


def test_interval_detail_figures_skips_dates_missing_from_lookup():
    rows = _make_ranked_rows(3)
    energyday_by_date = {rows[0].date: _make_fake_energyday()}  # rows[1]/[2] missing
    ranked = list(enumerate(rows, start=1))
    figures = _interval_detail_figures(ranked, energyday_by_date, 'Rank')
    assert len(figures) == 1
    assert rows[0].date in figures[0][0]


def test_top_roi_days_interval_figures_uses_highest_savings_days():
    rows, energyday_by_date = _make_ranked_rows_with_energydays(10)  # savings_inc_seg: 10..1
    figures = _top_roi_days_interval_figures(rows, energyday_by_date, n=5)
    assert len(figures) == 5
    assert 'Top ROI day #1: 2025-01-15' in figures[0][0]  # highest savings_inc_seg (10) first


def test_bottom_roi_days_interval_figures_uses_lowest_savings_days():
    rows, energyday_by_date = _make_ranked_rows_with_energydays(10)
    figures = _bottom_roi_days_interval_figures(rows, energyday_by_date, n=5)
    assert len(figures) == 5
    assert 'Bottom ROI day #1: 2025-10-15' in figures[0][0]  # lowest savings_inc_seg (1) first


def test_top_spend_days_interval_figures_uses_highest_spend_days():
    rows, energyday_by_date = _make_ranked_rows_with_energydays(10)  # spend: 1..10
    figures = _top_spend_days_interval_figures(rows, energyday_by_date, n=5)
    assert len(figures) == 5
    assert 'Top spend day #1: 2025-10-15' in figures[0][0]  # highest spend (10) first


def test_fig_energy_flows_has_four_traces_matching_dates():
    fig = _fig_energy_flows(_make_rows())
    assert len(fig.data) == 4
    assert [t.name for t in fig.data] == ['PV', 'Load', 'Export', 'Import']
    assert list(fig.data[0].x) == ['2025-06-10', '2025-06-11']


def test_fig_battery_daily_notes_when_disabled():
    fig = _fig_battery_daily(_make_rows(), battery_enabled=False)
    assert 'disabled' in fig.layout.title.text
    assert len(fig.data) == 2


def test_fig_battery_daily_no_note_when_enabled():
    fig = _fig_battery_daily(_make_rows(), battery_enabled=True)
    assert 'disabled' not in fig.layout.title.text


def test_fig_net_cost_nets_spend_minus_export_income():
    rows = _make_rows()
    fig = _fig_net_cost(rows, rolling_window=30)
    assert len(fig.data) == 2
    assert list(fig.data[0].y) == pytest.approx([r.spend - r.export_income for r in rows])


def test_fig_net_cost_rolling_average_matches_helper():
    rows = _make_rows()
    net_cost = [r.spend - r.export_income for r in rows]
    fig = _fig_net_cost(rows, rolling_window=1)
    assert list(fig.data[1].y) == pytest.approx(_rolling_average(net_cost, 1))


def test_fig_roi_rate_gbp_is_daily_savings_contribution():
    rows = _make_rows()
    fig = _fig_roi_rate_gbp(rows, rolling_window=1)
    assert len(fig.data) == 2
    assert list(fig.data[0].y) == pytest.approx([r.savings_inc_seg for r in rows])


def test_fig_roi_rate_gbp_sums_to_final_cumulative_savings():
    rows = _make_rows()
    fig = _fig_roi_rate_gbp(rows, rolling_window=1)
    assert sum(fig.data[0].y) == pytest.approx(rows[-1].cumulative_savings)


def test_fig_roi_rate_pct_matches_daily_savings_scaled_to_investment():
    rows = _make_rows()
    fig = _fig_roi_rate_pct(rows, rolling_window=1)
    expected = [100 * r.savings_inc_seg / r.cumulative_investment for r in rows]
    assert list(fig.data[0].y) == pytest.approx(expected)
