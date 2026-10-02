import asyncio
import functools
import os
import re
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import plotly.graph_objects as go

from analysis.collectdata import _load_settings, _make_battery, _iter_energy_days, _load_price_file
from analysis.fuse_client import load_daily_export_kwh, load_hourly_export_kwh
from analysis.dailyseries import DailySeriesBuilder
from analysis.energy_client import SunsynkEnergyClient
from analysis.energyprices import EnergyPrices
from analysis.pricedata import PriceData

# Fixed-order categorical palette (dataviz skill default), assigned per trace and
# never reassigned based on which traces happen to be visible.
_BLUE = '#2a78d6'
_ORANGE = '#eb6834'
_AQUA = '#1baf7a'
_YELLOW = '#eda100'
_GRAY = '#8a8a8a'


def _print_usage():
    print("Usage: graphs.py [key:value ...]")
    print("")
    print("Reads the same config.json / CLI keys as collectdata.py (config:, startDate:,")
    print("stopDate:, scanFromYear:, energyPrices:, batterySize:, useBattery:, etc. —")
    print("see collectdata.py --help). Builds a per-day series for the FIRST energyPrices")
    print("file, FIRST virtualBattery config, and baseline (no-boost) pvBoost scenario.")
    print("")
    print("Graph-specific options:")
    print("  outputPath:path.html   Explicit output file (default: results/_{timestamp}-graphs.html)")
    print("  rollingWindow:N        Rolling average window in days for the spend/net-cost charts (default: 4)")
    print("  plotlyCdn:ON|OFF       ON loads plotly.js from a CDN (smaller file); OFF embeds it (default: OFF)")
    print("  fuseDataDir:DIR        Fuse Energy cache (from fusedata.py) overlaid as meter export on the")
    print("                         energy-flows chart and (hourly) on the day-detail charts;")
    print("                         skipped if empty (default: fuseenergydata)")


def _resolve_output_path(settings: dict) -> str:
    output_path = str(settings.get('outputPath', '')).strip()
    if output_path:
        return output_path if os.path.isabs(output_path) else os.path.join(PROJECT_ROOT, output_path)

    results_dir = os.path.join(PROJECT_ROOT, 'results')
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    desc = re.sub(r'[^\w\-]', '_', str(settings.get('description', ''))).strip('_')
    suffix = f'-{desc}' if desc else ''
    return os.path.join(results_dir, f'_{timestamp}{suffix}-graphs.html')


def _rolling_average(values: list, window: int) -> list:
    """Trailing mean over up to `window` prior points; uses fewer for the first
    window-1 points so the result is the same length as `values` (no leading gaps)."""
    if window < 1:
        window = 1
    result = []
    running_sum = 0.0
    for i, v in enumerate(values):
        running_sum += v
        span_start = max(0, i - window + 1)
        if i >= window:
            running_sum -= values[i - window]
        count = i - span_start + 1
        result.append(running_sum / count)
    return result


def _fig_daily_spend(rows: list, rolling_window: int) -> go.Figure:
    dates = [r.date for r in rows]
    spend = [r.spend for r in rows]
    rolling = _rolling_average(spend, rolling_window)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=spend, name='Daily spend', mode='lines',
                              line=dict(color=_BLUE, width=1), opacity=0.4))
    fig.add_trace(go.Scatter(x=dates, y=rolling, name=f'{rolling_window}-day average', mode='lines',
                              line=dict(color=_BLUE, width=3)))
    fig.update_layout(title='Daily electricity spend (£)', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _fig_net_cost(rows: list, rolling_window: int) -> go.Figure:
    dates = [r.date for r in rows]
    net_cost = [r.spend - r.export_income for r in rows]
    rolling = _rolling_average(net_cost, rolling_window)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=net_cost, name='Net cost', mode='lines',
                              line=dict(color=_ORANGE, width=1), opacity=0.4))
    fig.add_trace(go.Scatter(x=dates, y=rolling, name=f'{rolling_window}-day average', mode='lines',
                              line=dict(color=_ORANGE, width=3)))
    fig.update_layout(title='Net daily cost (spend − export income) (£)', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _fig_roi_gbp(rows: list) -> go.Figure:
    dates = [r.date for r in rows]
    cumulative = [r.cumulative_savings for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=cumulative, name='Cumulative savings', mode='lines',
                              line=dict(color=_BLUE, width=2)))
    fig.update_layout(title='Return on investment: cumulative savings (£)', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _fig_roi_rate_gbp(rows: list, rolling_window: int) -> go.Figure:
    """Day-over-day rate of change of the ROI (£) chart. This is just each day's own
    contribution to cumulative_savings (savings_inc_seg), not a finite difference of the
    cumulative series — equivalent, but avoids floating-point drift and the first-day edge case."""
    dates = [r.date for r in rows]
    daily = [r.savings_inc_seg for r in rows]
    rolling = _rolling_average(daily, rolling_window)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=daily, name='Daily change', mode='lines',
                              line=dict(color=_BLUE, width=1), opacity=0.4))
    fig.add_trace(go.Scatter(x=dates, y=rolling, name=f'{rolling_window}-day average', mode='lines',
                              line=dict(color=_BLUE, width=3)))
    fig.update_layout(title='ROI rate of change (£/day)', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _fig_roi_pct(rows: list) -> go.Figure:
    dates = [r.date for r in rows]
    roi_pct = [r.roi_pct for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=roi_pct, name='ROI %', mode='lines',
                              line=dict(color=_AQUA, width=2)))
    fig.update_layout(title='Return on investment (%)', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _fig_roi_rate_pct(rows: list, rolling_window: int) -> go.Figure:
    """Day-over-day rate of change of the ROI (%) chart, derived the same way as
    _fig_roi_rate_gbp (each day's own contribution, scaled to cumulative_investment)."""
    dates = [r.date for r in rows]
    daily_pct = [
        100 * r.savings_inc_seg / r.cumulative_investment if r.cumulative_investment > 0 else 0.0
        for r in rows
    ]
    rolling = _rolling_average(daily_pct, rolling_window)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=daily_pct, name='Daily change', mode='lines',
                              line=dict(color=_AQUA, width=1), opacity=0.4))
    fig.add_trace(go.Scatter(x=dates, y=rolling, name=f'{rolling_window}-day average', mode='lines',
                              line=dict(color=_AQUA, width=3)))
    fig.update_layout(title='ROI rate of change (%/day)', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _fig_payback_curve(rows: list) -> go.Figure:
    dates = [r.date for r in rows]
    investment = [r.cumulative_investment for r in rows]
    savings = [r.cumulative_savings for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=investment, name='Cumulative investment', mode='lines',
                              line=dict(color=_GRAY, width=2, dash='dash')))
    fig.add_trace(go.Scatter(x=dates, y=savings, name='Cumulative savings', mode='lines',
                              line=dict(color=_BLUE, width=2)))
    fig.update_layout(title='Payback: cumulative investment vs cumulative savings',
                       hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _fig_energy_flows(rows: list, fuse_export: dict = None) -> go.Figure:
    """Daily PV/Load/Export/Import from the inverter. If ``fuse_export`` (date -> kWh,
    from fuse_client.load_daily_export_kwh) is given, the smart-meter export the
    supplier actually measured is overlaid as a dashed line for comparison."""
    dates = [r.date for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=[r.pv_kwh for r in rows], name='PV',
                              mode='lines', line=dict(color=_BLUE, width=1.5)))
    fig.add_trace(go.Scatter(x=dates, y=[r.load_kwh for r in rows], name='Load',
                              mode='lines', line=dict(color=_ORANGE, width=1.5)))
    fig.add_trace(go.Scatter(x=dates, y=[r.export_kwh for r in rows], name='Export',
                              mode='lines', line=dict(color=_AQUA, width=1.5)))
    if fuse_export:
        fuse_dates = [d for d in sorted(fuse_export) if dates[0] <= d <= dates[-1]]
        if fuse_dates:
            fig.add_trace(go.Scatter(x=fuse_dates, y=[fuse_export[d] for d in fuse_dates],
                                      name='Export (Fuse meter)', mode='lines',
                                      line=dict(color=_AQUA, width=1.5, dash='dash')))
    fig.add_trace(go.Scatter(x=dates, y=[r.import_kwh for r in rows], name='Import',
                              mode='lines', line=dict(color=_YELLOW, width=1.5)))
    fig.update_layout(title='Daily energy flows (kWh)', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _pick_unique_months(ranked: list, limit: int) -> list:
    """Walk `ranked` (already sorted, best/worst first) picking at most one row per
    calendar month (row.date[:7]), so a single unusually good/bad month can't take
    every slot. Returns (rank, row) pairs — `rank` is the row's 1-indexed position in
    `ranked` itself (not in the picked list), so a skipped-ahead pick like the 7th
    still reads as '#7', not renumbered to '#3'."""
    picked = []
    seen_months = set()
    for i, row in enumerate(ranked, start=1):
        month = row.date[:7]
        if month in seen_months:
            continue
        seen_months.add(month)
        picked.append((i, row))
        if len(picked) == limit:
            break
    return picked


def _select_extreme_days(rows: list, key_func, n: int = 5) -> tuple:
    """Rank days by key_func(row) and return (top_n, bottom_n): top_n descending
    (highest first), bottom_n ascending (lowest first), each capped at one row per
    calendar month. Each entry is a (rank, row) pair, rank being the day's true
    position in the full ranking (1 = best for top_n, 1 = worst for bottom_n) —
    preserved even when month-uniqueness skips over a higher/lower-ranked same-month day."""
    ranked_desc = sorted(rows, key=key_func, reverse=True)
    top_n = _pick_unique_months(ranked_desc, n)
    bottom_n = _pick_unique_months(list(reversed(ranked_desc)), n)
    return top_n, bottom_n


def _select_extreme_roi_days(rows: list, n: int = 5) -> tuple:
    """Rank days by their own contribution to ROI (savings_inc_seg, the same value the
    ROI rate-of-change charts plot)."""
    return _select_extreme_days(rows, key_func=lambda r: r.savings_inc_seg, n=n)


def _interval_time_series(interval_summary) -> tuple:
    """Return (times, values_w) sorted by time-of-day from an IntervalSummary's raw
    records (5-minute resolution from the real API; coarser in test fixtures)."""
    pairs = sorted(interval_summary.records, key=lambda r: r['time'])
    times = [r['time'] for r in pairs]
    values = [float(r['value']) for r in pairs]
    return times, values


def _grid_import_export_series(grid_summary) -> tuple:
    """Split the signed Grid record series into (times, import_w, export_w):
    positive readings are import (drawn from grid), negative are export (fed back)."""
    pairs = sorted(grid_summary.records, key=lambda r: r['time'])
    times = [r['time'] for r in pairs]
    import_w = [max(float(r['value']), 0.0) for r in pairs]
    export_w = [max(-float(r['value']), 0.0) for r in pairs]
    return times, import_w, export_w


def _fuse_hourly_series(hourly: list) -> tuple:
    """Turn Fuse ``[(hour, kWh), ...]`` into (times, avg_w) for a step ('hv') trace on
    the interval charts' 'HH:MM' category axis: each hour's kWh x 1000 is its average W.
    A closing '23:55' point repeats the last value so the final hour's step is drawn."""
    times = [f'{hour:02d}:00' for hour, _ in hourly]
    watts = [kwh * 1000 for _, kwh in hourly]
    if hourly and hourly[-1][0] == 23:
        times.append('23:55')
        watts.append(watts[-1])
    return times, watts


def _fig_day_interval_detail(energyday, title: str, fuse_hourly: list = None) -> go.Figure:
    """One day's raw interval PV/Load/Export/Import power (W) across time-of-day —
    the actual within-day shape, not just that day's kWh total. ``fuse_hourly``
    (``[(hour, kWh)]`` from fuse_client.load_hourly_export_kwh) overlays the smart
    meter's hourly export as an average-W step line."""
    pv_times, pv_w = _interval_time_series(energyday.pv)
    load_times, load_w = _interval_time_series(energyday.load)
    grid_times, import_w, export_w = _grid_import_export_series(energyday.grid)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pv_times, y=pv_w, name='PV', mode='lines',
                              line=dict(color=_BLUE, width=1.5)))
    fig.add_trace(go.Scatter(x=load_times, y=load_w, name='Load', mode='lines',
                              line=dict(color=_ORANGE, width=1.5)))
    fig.add_trace(go.Scatter(x=grid_times, y=export_w, name='Export', mode='lines',
                              line=dict(color=_AQUA, width=1.5)))
    if fuse_hourly:
        fuse_times, fuse_w = _fuse_hourly_series(fuse_hourly)
        fig.add_trace(go.Scatter(x=fuse_times, y=fuse_w, name='Export (Fuse meter, hourly avg)',
                                  mode='lines', line=dict(color=_AQUA, width=2, dash='dash',
                                                          shape='hv')))
    fig.add_trace(go.Scatter(x=grid_times, y=import_w, name='Import', mode='lines',
                              line=dict(color=_YELLOW, width=1.5)))
    fig.update_layout(title=title, hovermode='x unified',
                       xaxis_title='Time of day', yaxis_title='Power (W)')
    return fig


def _interval_detail_figures(ranked_rows: list, energyday_by_date: dict, label: str,
                             fuse_hourly_lookup=None) -> list:
    """Build one (title, figure) pair per (rank, row) pair — as returned by
    _select_extreme_days — for days whose EnergyDay is available, in the given order.
    `rank` (the day's true position in the full ranking, not its position in this
    possibly month-deduplicated list) is shown in the title so '#7' still means what
    it says against the whole date range. Shared by the top/bottom ROI-day and
    top-spend-day charts — only the pre-ranked `ranked_rows` list (and its label) differs.
    `fuse_hourly_lookup(date_str) -> [(hour, kWh)]`, if given, adds the Fuse meter overlay."""
    figures = []
    for rank, row in ranked_rows:
        energyday = energyday_by_date.get(row.date)
        if energyday is None:
            continue
        title = f'{label} #{rank}: {row.date} (5-minute interval detail)'
        fuse_hourly = fuse_hourly_lookup(row.date) if fuse_hourly_lookup else None
        figures.append((title, _fig_day_interval_detail(energyday, title, fuse_hourly)))
    return figures


def _top_roi_days_interval_figures(rows: list, energyday_by_date: dict, n: int = 5,
                                      fuse_hourly_lookup=None) -> list:
    top_n, _ = _select_extreme_roi_days(rows, n)
    return _interval_detail_figures(top_n, energyday_by_date, 'Top ROI day', fuse_hourly_lookup)


def _bottom_roi_days_interval_figures(rows: list, energyday_by_date: dict, n: int = 5,
                                         fuse_hourly_lookup=None) -> list:
    _, bottom_n = _select_extreme_roi_days(rows, n)
    return _interval_detail_figures(bottom_n, energyday_by_date, 'Bottom ROI day', fuse_hourly_lookup)


def _top_spend_days_interval_figures(rows: list, energyday_by_date: dict, n: int = 5,
                                        fuse_hourly_lookup=None) -> list:
    top_n, _ = _select_extreme_days(rows, key_func=lambda r: r.spend, n=n)
    return _interval_detail_figures(top_n, energyday_by_date, 'Top spend day', fuse_hourly_lookup)


def _fig_battery_daily(rows: list, battery_enabled: bool) -> go.Figure:
    dates = [r.date for r in rows]
    title = 'Battery daily charge / discharge (kWh)'
    if not battery_enabled:
        title += '  (simulated — battery disabled in config)'

    fig = go.Figure()
    fig.add_trace(go.Bar(x=dates, y=[r.battery_charge_kwh for r in rows], name='Charged',
                          marker_color=_BLUE))
    fig.add_trace(go.Bar(x=dates, y=[r.battery_discharge_kwh for r in rows], name='Discharged',
                          marker_color=_ORANGE))
    fig.update_layout(title=title, barmode='group', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


def _write_dashboard_html(figures: list, output_path: str, embed_plotly_js: bool = True) -> None:
    """Writes all figures into one self-contained HTML file. plotly.js ships once
    (with the first figure); the rest reference the already-loaded copy."""
    parts = []
    include_js = True if embed_plotly_js else 'cdn'
    for i, (label, fig) in enumerate(figures):
        parts.append(f'<h2>{label}</h2>')
        parts.append(fig.to_html(full_html=False, include_plotlyjs=(include_js if i == 0 else False)))
    html = '<html><head><title>Sunsynk energy graphs</title></head><body>\n' + '\n'.join(parts) + '\n</body></html>'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


async def main():
    if '--help' in sys.argv or '-h' in sys.argv:
        _print_usage()
        return

    sunsynk_username = os.getenv('SUNSYNK_USERNAME')
    sunsynk_password = os.getenv('SUNSYNK_PASSWORD')

    settings = _load_settings(sys.argv[1:])

    show_days = bool(re.match('^on', str(settings['showDays']), re.IGNORECASE))
    start_date = str(settings['startDate'])
    stop_date = str(settings['stopDate'])
    scan_from_year = int(settings['scanFromYear'])
    original_price = float(settings['originalPrice'])
    rolling_window = int(settings['rollingWindow'])
    plotly_cdn = bool(re.match('^on', str(settings['plotlyCdn']), re.IGNORECASE))

    raw_files = settings['energyPrices']
    price_file = (raw_files if isinstance(raw_files, list) else [raw_files])[0]

    vb_raw = settings['virtualBattery']
    battery_config = (vb_raw if isinstance(vb_raw, list) else [vb_raw])[0]
    battery_enabled = bool(re.match('^on', str(battery_config['enabled']), re.IGNORECASE))
    battery_price = float(battery_config['batteryPrice'])

    boost = settings['pvBoost'][0]  # baseline (addWatts == 0), always first
    pv_extra_ratio = boost['pv_extra_ratio']
    pv_extra_clip_w = boost['inverterClipW']

    energy_prices_data = _load_price_file(price_file)

    tmp_price = PriceData()
    battery = _make_battery(battery_config, tmp_price)
    cumulative_investment = original_price + (battery_price if battery_enabled else 0.0)
    prices = EnergyPrices(
        energy_prices_data, battery,
        original_price=original_price, label=os.path.splitext(os.path.basename(price_file))[0],
        off_peak_baseline_kwh=float(settings['offPeakBaseline']),
        off_peak_shift_enabled=bool(re.match('^on', str(settings['offPeakShift']), re.IGNORECASE)),
        battery_enabled=battery_enabled,
        battery_price=battery_price,
    )
    builder = DailySeriesBuilder(cumulative_investment)

    async with SunsynkEnergyClient(sunsynk_username, sunsynk_password, "https://api.sunsynk.net") as client:
        inverters = await client.get_inverters()
        inverter = inverters[0]

        month_cache: dict = {}
        day_raw_cache: dict = {}
        energyday_cache: dict = {}
        cache_stats = {'hits': 0, 'misses': 0}
        energyday_by_date: dict = {}  # date_str -> EnergyDay, for the interval-detail charts

        async for date_str, energyday, battery_delta in _iter_energy_days(
                client, inverter, prices, pv_extra_ratio, pv_extra_clip_w,
                start_date, stop_date, scan_from_year, show_days,
                month_cache, day_raw_cache, energyday_cache, cache_stats,
                settings['exportCorrection']):
            if show_days:
                print(f"Calculating: {date_str}")
            builder.add_day(date_str, energyday, prices.price_data, battery_delta)
            energyday_by_date[date_str] = energyday

        print(f"[cache] day files: {cache_stats['misses']} loaded from disk, {cache_stats['hits']} served from memory cache")

    rows = builder.rows()
    if not rows:
        print("No days in range — nothing to graph.")
        return

    fuse_export = load_daily_export_kwh(settings['fuseDataDir'])
    if fuse_export:
        print(f"Fuse export: {len(fuse_export)} days loaded from {settings['fuseDataDir']}")

    figures = [
        ('Daily spend', _fig_daily_spend(rows, rolling_window)),
        ('Net daily cost', _fig_net_cost(rows, rolling_window)),
        ('ROI (£)', _fig_roi_gbp(rows)),
        ('ROI rate of change (£)', _fig_roi_rate_gbp(rows, rolling_window)),
        ('ROI (%)', _fig_roi_pct(rows)),
        ('ROI rate of change (%)', _fig_roi_rate_pct(rows, rolling_window)),
        ('Payback curve', _fig_payback_curve(rows)),
        ('Energy flows', _fig_energy_flows(rows, fuse_export)),
        ('Battery daily charge/discharge', _fig_battery_daily(rows, battery_enabled)),
    ]
    fuse_hourly_lookup = functools.partial(load_hourly_export_kwh, settings['fuseDataDir'])
    figures.extend(_top_spend_days_interval_figures(rows, energyday_by_date,
                                                    fuse_hourly_lookup=fuse_hourly_lookup))
    figures.extend(_top_roi_days_interval_figures(rows, energyday_by_date,
                                                  fuse_hourly_lookup=fuse_hourly_lookup))
    figures.extend(_bottom_roi_days_interval_figures(rows, energyday_by_date,
                                                     fuse_hourly_lookup=fuse_hourly_lookup))

    output_path = _resolve_output_path(settings)
    _write_dashboard_html(figures, output_path, embed_plotly_js=not plotly_cdn)
    print(f"Graphs saved: {output_path}  ({len(rows)} days)")


if __name__ == '__main__':
    asyncio.run(main())
