import os

import pytest

from analysis.collectdata import _parse_cli_args
from analysis.dailyseries import DailyRow
from analysis.graphs import (
    _resolve_output_path, _rolling_average, _fig_energy_flows, _fig_battery_daily, _fig_net_cost,
    _fig_roi_rate_gbp, _fig_roi_rate_pct,
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
        ['outputPath:my.html', 'rollingWindow:14', 'plotlyCdn:ON'])
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
