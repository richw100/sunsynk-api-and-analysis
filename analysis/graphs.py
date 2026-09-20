import asyncio
import os
import re
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import plotly.graph_objects as go

from analysis.collectdata import _load_settings, _make_battery, _iter_energy_days, _load_price_file
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


def _fig_energy_flows(rows: list) -> go.Figure:
    dates = [r.date for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=[r.pv_kwh for r in rows], name='PV',
                              mode='lines', line=dict(color=_BLUE, width=1.5)))
    fig.add_trace(go.Scatter(x=dates, y=[r.load_kwh for r in rows], name='Load',
                              mode='lines', line=dict(color=_ORANGE, width=1.5)))
    fig.add_trace(go.Scatter(x=dates, y=[r.export_kwh for r in rows], name='Export',
                              mode='lines', line=dict(color=_AQUA, width=1.5)))
    fig.add_trace(go.Scatter(x=dates, y=[r.import_kwh for r in rows], name='Import',
                              mode='lines', line=dict(color=_YELLOW, width=1.5)))
    fig.update_layout(title='Daily energy flows (kWh)', hovermode='x unified')
    fig.update_xaxes(rangeslider_visible=True)
    return fig


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

        async for date_str, energyday, battery_delta in _iter_energy_days(
                client, inverter, prices, pv_extra_ratio, pv_extra_clip_w,
                start_date, stop_date, scan_from_year, show_days,
                month_cache, day_raw_cache, energyday_cache, cache_stats):
            if show_days:
                print(f"Calculating: {date_str}")
            builder.add_day(date_str, energyday, prices.price_data, battery_delta)

        print(f"[cache] day files: {cache_stats['misses']} loaded from disk, {cache_stats['hits']} served from memory cache")

    rows = builder.rows()
    if not rows:
        print("No days in range — nothing to graph.")
        return

    figures = [
        ('Daily spend', _fig_daily_spend(rows, rolling_window)),
        ('Net daily cost', _fig_net_cost(rows, rolling_window)),
        ('ROI (£)', _fig_roi_gbp(rows)),
        ('ROI rate of change (£)', _fig_roi_rate_gbp(rows, rolling_window)),
        ('ROI (%)', _fig_roi_pct(rows)),
        ('ROI rate of change (%)', _fig_roi_rate_pct(rows, rolling_window)),
        ('Payback curve', _fig_payback_curve(rows)),
        ('Energy flows', _fig_energy_flows(rows)),
        ('Battery daily charge/discharge', _fig_battery_daily(rows, battery_enabled)),
    ]

    output_path = _resolve_output_path(settings)
    _write_dashboard_html(figures, output_path, embed_plotly_js=not plotly_cdn)
    print(f"Graphs saved: {output_path}  ({len(rows)} days)")


if __name__ == '__main__':
    asyncio.run(main())
