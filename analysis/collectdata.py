import asyncio
import os
import json
import sys
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis.energy_client import SunsynkEnergyClient
from analysis.energyday import EnergyDay
from analysis.pricedata import PriceData
from analysis.virtualbattery import VirtualBattery
from analysis.energyprices import EnergyPrices, _battery_warnings

from datetime import datetime
from tqdm import tqdm


class _Tee:
    """Write to multiple streams simultaneously (stdout + results file)."""
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def _parse_cli_args(argv):
    """Parse key:value CLI args. Returns (config_path, overrides_dict)."""
    config_path = 'config.json'
    overrides = {}
    for arg in argv:
        if ':' not in arg:
            continue
        key, _, value = arg.partition(':')
        key_lower = key.lower()
        if key_lower == 'config':
            config_path = value
        elif key_lower == 'showdays':
            overrides['showDays'] = value
        elif key_lower == 'batterysize':
            overrides.setdefault('virtualBattery', {})['batterySize'] = int(value)
        elif key_lower == 'usepv':
            overrides.setdefault('virtualBattery', {})['usePV'] = value
        elif key_lower == 'startcharge':
            overrides.setdefault('virtualBattery', {})['startCharge'] = int(value)
        elif key_lower == 'stopcharge':
            overrides.setdefault('virtualBattery', {})['stopCharge'] = int(value)
        elif key_lower == 'energyprices':
            overrides['energyPrices'] = value
        elif key_lower == 'startdate':
            overrides['startDate'] = value
        elif key_lower == 'stopdate':
            overrides['stopDate'] = value
        elif key_lower == 'scanfromyear':
            overrides['scanFromYear'] = int(value)
        elif key_lower == 'originalprice':
            overrides['originalPrice'] = float(value)
        elif key_lower == 'existingpvwatts':
            overrides['existingPvWatts'] = float(value)
        elif key_lower == 'pvboostwatts':
            overrides.setdefault('pvBoostCli', {})['addWatts'] = float(value)
        elif key_lower == 'pvboostcost':
            overrides.setdefault('pvBoostCli', {})['cost'] = float(value)
        elif key_lower == 'pvboostclipw':
            overrides.setdefault('pvBoostCli', {})['inverterClipW'] = float(value)
        elif key_lower == 'exportwindowstart':
            overrides.setdefault('virtualBattery', {})['exportWindowStart'] = value
        elif key_lower == 'exportwindowstop':
            overrides.setdefault('virtualBattery', {})['exportWindowStop'] = value
        elif key_lower == 'useexport':
            overrides.setdefault('virtualBattery', {})['useExport'] = value
        elif key_lower == 'offpeakshift':
            overrides['offPeakShift'] = value
        elif key_lower == 'offpeakbaseline':
            overrides['offPeakBaseline'] = float(value)
        elif key_lower == 'dischargeefficiency':
            overrides.setdefault('virtualBattery', {})['dischargeEfficiency'] = float(value)
        elif key_lower == 'pvchargeefficiency':
            overrides.setdefault('virtualBattery', {})['pvChargeEfficiency'] = float(value)
        elif key_lower == 'maxoutputw':
            overrides.setdefault('virtualBattery', {})['maxOutputW'] = float(value)
        elif key_lower == 'chargeefficiency':
            overrides.setdefault('virtualBattery', {})['chargeEfficiency'] = float(value)
        elif key_lower == 'gridcharge':
            overrides.setdefault('virtualBattery', {})['gridCharge'] = value
        elif key_lower == 'dischargereservewh':
            overrides.setdefault('virtualBattery', {})['dischargeReserveWh'] = int(value)
        elif key_lower == 'dischargereserveuntil':
            overrides.setdefault('virtualBattery', {})['dischargeReserveUntil'] = value
        elif key_lower == 'usebattery':
            overrides.setdefault('virtualBattery', {})['enabled'] = value
        elif key_lower == 'batteryprice':
            overrides.setdefault('virtualBattery', {})['batteryPrice'] = float(value)
        elif key_lower == 'description':
            overrides['description'] = value
    return config_path, overrides


def _load_settings(argv):
    """Load config file then apply CLI overrides, falling back to hardcoded defaults."""
    config_path, overrides = _parse_cli_args(argv)

    try:
        with open(config_path) as f:
            settings = json.load(f)
        print(f"Config: {config_path}")
    except FileNotFoundError:
        settings = {}
        print(f"Config: {config_path} not found, using defaults")

    vb_override = overrides.pop('virtualBattery', {})
    pv_boost_cli = overrides.pop('pvBoostCli', {})
    settings.update(overrides)

    defaults = {
        'showDays': 'ON',
        'energyPrices': '_EnergyPrices.json',
        'startDate': '',
        'stopDate': '',
        'scanFromYear': 2025,
        'originalPrice': 6206.47,
        'existingPvWatts': 3115,
        'offPeakShift': 'ON',
        'offPeakBaseline': 0.96,
        'description': '',
        'virtualBattery': {
            'enabled': 'ON',
            'batterySize': 5000,
            'usePV': 'OFF',
            'startCharge': 1000,
            'stopCharge': 2000,
            'exportWindowStart': '17:00',
            'exportWindowStop': '19:00',
            'useExport': 'OFF',
            'dischargeEfficiency': 0.92,
            'pvChargeEfficiency': 0.96,
            'maxOutputW': 2400,
            'chargeEfficiency': 1.0,
            'gridCharge': 'ON',
            'batteryPrice': 0,
            'dischargeReserveWh': 0,
            'dischargeReserveUntil': '17:00',
        },
    }
    for key, value in defaults.items():
        settings.setdefault(key, value)

    # Fill missing virtualBattery sub-keys; support both a single config dict and a list.
    # CLI overrides (vb_override) are applied to every config to allow temporary adjustments.
    vb_defaults = defaults['virtualBattery']
    vb_raw = settings['virtualBattery']
    if isinstance(vb_raw, list):
        for bc in vb_raw:
            for key, value in vb_defaults.items():
                bc.setdefault(key, value)
            bc.update(vb_override)
    else:
        vb_raw.update(vb_override)
        for key, value in vb_defaults.items():
            vb_raw.setdefault(key, value)

    settings['pvBoost'] = _normalize_pv_boosts(
        settings.get('pvBoost'), float(settings['existingPvWatts']), pv_boost_cli
    )

    settings['_configPath'] = config_path
    return settings


def _normalize_pv_boosts(raw, existing_pv_watts: float, cli: dict) -> list:
    """Return a list of PV-boost scenario dicts, always including a zero-add baseline first.

    Each scenario dict has: label, addWatts, cost, inverterClipW (W or None),
    pv_extra_ratio (addWatts / existing_pv_watts).  Models bolt-on "plug-in solar" kits:
    a scaled copy of the measured PV curve, optionally flat-topped by a microinverter clip.
    """
    baseline = {'label': 'Baseline', 'addWatts': 0.0, 'cost': 0.0, 'inverterClipW': None}

    if cli:
        entries = [dict(baseline), dict(cli)]
    elif raw is None:
        entries = [dict(baseline)]
    elif isinstance(raw, list):
        entries = [dict(e) for e in raw]
    else:
        entries = [dict(raw)]

    for e in entries:
        e['addWatts'] = float(e.get('addWatts', 0) or 0)
        e['cost'] = float(e.get('cost', 0) or 0)
        clip = e.get('inverterClipW')
        e['inverterClipW'] = float(clip) if clip and float(clip) > 0 else None
        e.setdefault('label', 'Baseline' if e['addWatts'] == 0 else f"+{e['addWatts']:.0f}W")

    if any(e['addWatts'] > 0 for e in entries) and not any(e['addWatts'] == 0 for e in entries):
        entries.insert(0, dict(baseline))

    for e in entries:
        e['pv_extra_ratio'] = e['addWatts'] / existing_pv_watts if existing_pv_watts > 0 else 0.0

    return entries


def _print_usage():
    print("Usage: collectdata.py [key:value ...]")
    print("")
    print("General options:")
    print("  config:path.json        Config file to load (default: config.json)")
    print("  showDays:ON|OFF         Print per-day output")
    print("  description:text        Appended to the results filename, e.g. description:2.6kWh-test")
    print("  energyPrices:file.json  Energy prices file in inverterData/ (or list in config)")
    print("  startDate:YYYY-MM-DD    Process days after this date")
    print("  stopDate:YYYY-MM-DD     Stop processing after this date")
    print("  scanFromYear:YYYY       First year to scan for data")
    print("  originalPrice:N         Installation cost for ROI calculation")
    print("  offPeakShift:ON|OFF     Include off-peak load-shifting in savings totals (default: ON)")
    print("  offPeakBaseline:N       Expected kWh/day at off-peak for a 7-hour window (default: 0.96)")
    print("")
    print("Plug-in solar (PV boost) options:")
    print("  existingPvWatts:N       Current array size in W for scaling (default: 3115 = 7x445W)")
    print("  pvBoostWatts:N          Added panel capacity in W (compared against a no-boost baseline)")
    print("  pvBoostCost:N           Cost of the added panels in £ (for payback period)")
    print("  pvBoostClipW:N          Microinverter AC output clip for the added panels in W")
    print("  (config.json 'pvBoost' may instead be a list of {label,addWatts,cost,inverterClipW})")
    print("")
    print("Virtual battery options:")
    print("  useBattery:ON|OFF       Enable virtual battery simulation (default: ON)")
    print("  batteryPrice:N          Battery purchase cost in £ (for payback period calculation)")
    print("  batterySize:N           Battery capacity in Wh")
    print("  usePV:ON|OFF            Charge battery from PV surplus")
    print("  startCharge:N           PV charging starts below this level (Wh)")
    print("  stopCharge:N            PV charging stops at this level (Wh)")
    print("  exportWindowStart:HH:MM Battery-to-grid export window start")
    print("  exportWindowStop:HH:MM  Battery-to-grid export window stop")
    print("  useExport:ON|OFF        Sell virtual battery back to grid during export window")
    print("  dischargeEfficiency:N   Discharge efficiency (e.g. 0.92)")
    print("  chargeEfficiency:N      Grid charging efficiency (e.g. 0.95; default 1.0)")
    print("  pvChargeEfficiency:N    PV charge efficiency (e.g. 0.96)")
    print("  maxOutputW:N            Maximum battery output in watts (e.g. 2400)")
    print("  dischargeReserveWh:N    Hold back N Wh until dischargeReserveUntil time (default: 0)")
    print("  dischargeReserveUntil:HH:MM  Release reserve for full discharge after this time (default: 17:00)")


def _make_battery(vb, price_data):
    return VirtualBattery(
        price_data,
        int(vb['batterySize']),
        1 if re.match('^on', str(vb['usePV']), re.IGNORECASE) else 0,
        int(vb['startCharge']),
        int(vb['stopCharge']),
        export_window_start=vb['exportWindowStart'],
        export_window_stop=vb['exportWindowStop'],
        discharge_efficiency=float(vb['dischargeEfficiency']),
        pv_charge_efficiency=float(vb['pvChargeEfficiency']),
        max_output_w=float(vb['maxOutputW']),
        use_export=bool(re.match('^on', str(vb['useExport']), re.IGNORECASE)),
        charge_efficiency=float(vb['chargeEfficiency']),
        grid_charge=bool(re.match('^on', str(vb['gridCharge']), re.IGNORECASE)),
        discharge_reserve_wh=int(vb['dischargeReserveWh']),
        discharge_reserve_until=str(vb['dischargeReserveUntil']),
    )


def _print_comparison(all_prices):
    labels = [p.label for p in all_prices]
    derived = [p.get_derived() for p in all_prices]
    totals = [p.get_grand_totals() for p in all_prices]

    col_w = max(14, max(len(l) for l in labels) + 2)
    row_w = 32
    show_diff = len(all_prices) == 2

    header = " " * row_w + "".join(l.rjust(col_w) for l in labels)
    if show_diff:
        header += "Difference".rjust(col_w)

    def row(name, values):
        line = name.ljust(row_w) + "".join(f"£{v:.2f}".rjust(col_w) for v in values)
        if show_diff:
            diff = round(values[1] - values[0], 2)
            sign = "+" if diff > 0 else ""
            line += f"{sign}£{diff:.2f}".rjust(col_w)
        print(line)

    print("\nCOMPARISON")
    print(header)
    row("Total Cost:",               [t['total_cost'] for t in totals])
    row("Total Cost (no solar):",    [t['total_cost_without_solar'] for t in totals])
    row("SEG Income:",               [t['total_export_amount_calc'] for t in totals])
    row("Bill savings (inc SEG):",   [d['bill_savings_inc_seg'] for d in derived])
    row("Full solar savings:",       [d['calc_savings'] for d in derived])
    if all_prices[0].battery_enabled:
        row("Battery potential saving:", [d['battery_savings'] for d in derived])


def _print_battery_comparison(all_battery_results, price_file_idx=0):
    """Side-by-side battery metrics for multiple battery configs at a given price-file index."""
    entries = [
        (label, all_prices[price_file_idx])
        for label, all_prices in all_battery_results
        if price_file_idx < len(all_prices) and all_prices[price_file_idx].battery_enabled
    ]
    if len(entries) < 2:
        return

    labels = [label for label, _ in entries]
    prices_list = [p for _, p in entries]
    derived = [p.get_derived() for p in prices_list]
    totals = [p.get_grand_totals() for p in prices_list]
    days = totals[0]['days']

    col_w = max(14, max(len(l) for l in labels) + 2)
    row_w = 36
    show_diff = len(entries) == 2

    header = " " * row_w + "".join(l.rjust(col_w) for l in labels)
    if show_diff:
        header += "Difference".rjust(col_w)

    def row_money(name, values):
        line = name.ljust(row_w) + "".join(f"£{v:.2f}".rjust(col_w) for v in values)
        if show_diff:
            diff = round(values[1] - values[0], 2)
            sign = "+" if diff > 0 else ""
            line += f"{sign}£{diff:.2f}".rjust(col_w)
        print(line)

    def row_kwh(name, values):
        line = name.ljust(row_w) + "".join(f"{v:.2f}kWh".rjust(col_w) for v in values)
        if show_diff:
            diff = round(values[1] - values[0], 2)
            sign = "+" if diff > 0 else ""
            line += f"{sign}{diff:.2f}kWh".rjust(col_w)
        print(line)

    def row_int(name, values):
        line = name.ljust(row_w) + "".join(str(v).rjust(col_w) for v in values)
        if show_diff:
            diff = values[1] - values[0]
            sign = "+" if diff > 0 else ""
            line += f"{sign}{diff}".rjust(col_w)
        print(line)

    print("\nBATTERY COMPARISON")
    print(header)
    row_kwh("Charged from grid (kWh):", [t['battery_charge_amount'] for t in totals])
    row_money("Charging cost:", [t['battery_cost_from_grid'] for t in totals])
    row_money("Peak-rate value delivered:", [t['battery_nominal_cost'] for t in totals])
    row_money("Foregone export income:", [t['battery_lost_export_cost'] for t in totals])
    row_money("Re-exported income:", [t['battery_export_cost'] for t in totals])
    row_int("Days battery ran out:", [t['battery_days_run_out'] for t in totals])
    row_money("Net potential saving:", [d['battery_savings'] for d in derived])
    if days > 0:
        row_money("Annualised:", [d['annualised_battery'] for d in derived])

    if any(p.battery_price > 0 for p in prices_list):
        payback_values = [d['payback_years'] for d in derived]
        line = "Payback period (years):".ljust(row_w)
        line += "".join(
            (f"{v:.1f}yr" if v is not None else "N/A").rjust(col_w)
            for v in payback_values
        )
        if show_diff and payback_values[0] is not None and payback_values[1] is not None:
            diff = round(payback_values[1] - payback_values[0], 1)
            sign = "+" if diff > 0 else ""
            line += f"{sign}{diff:.1f}yr".rjust(col_w)
        print(line)

    def has_warnings(prices: EnergyPrices) -> bool:
        for summary in prices.aggregator.summaries:
            if _battery_warnings(prices.orig_battery, summary.price_data):
                return True
        return False

    warn_flags = [has_warnings(p) for p in prices_list]
    if any(warn_flags):
        line = "Warnings:".ljust(row_w) + "".join(
            "WARNING".rjust(col_w) if w else "".rjust(col_w) for w in warn_flags
        )
        if show_diff:
            line += "".rjust(col_w)
        print(line)


def _pv_boost_metrics(all_boost_results, battery_idx=0, price_idx=0):
    """Per-scenario ROI of added 'plug-in solar' panels vs the baseline (boost index 0).

    all_boost_results: list of (boost_dict, all_battery_results), where all_battery_results
    is a list of (bc_label, [EnergyPrices, ...]).  Returns one metrics dict per boost option,
    all money figures annualised (x365/days) and quoted as a delta against the baseline.
    """
    def _prices(entry):
        return entry[1][battery_idx][1][price_idx]

    p0 = _prices(all_boost_results[0])
    t0 = p0.get_grand_totals()
    d0 = p0.get_derived()
    days = t0['days']
    ann = 365.0 / days if days > 0 else 0.0
    battery_enabled = bool(p0.battery_enabled)

    rows = []
    for entry in all_boost_results:
        boost = entry[0]
        pi = _prices(entry)
        ti = pi.get_grand_totals()
        di = pi.get_derived()
        export_rate = pi.price_data.current_export

        deliv = round(ann * (ti['total_calc_pv'] - t0['total_calc_pv']), 2)
        clip = round(ann * ti['total_pv_clip_loss'], 2)
        clip_pct = round(100 * clip / (deliv + clip), 1) if (deliv + clip) > 0 else 0.0
        self_cons = round(ann * (t0['total_cost'] - ti['total_cost']), 2)
        export_inc = round(ann * (ti['total_export_amount_calc'] - t0['total_export_amount_calc']), 2)
        batt = round(ann * (di['battery_savings'] - d0['battery_savings']), 2) if battery_enabled else 0.0
        total_extra = round(self_cons + export_inc + batt, 2)
        cost = float(boost['cost'])
        payback = round(cost / total_extra, 1) if total_extra > 0 else None
        roi = round(100 * total_extra / cost, 1) if cost > 0 else None

        rows.append({
            'label': boost['label'],
            'add_watts': float(boost['addWatts']),
            'clip_w': boost['inverterClipW'],
            'deliv': deliv,
            'clip': clip,
            'clip_pct': clip_pct,
            'clip_value': round(clip * export_rate, 2),
            'self_cons': self_cons,
            'export_inc': export_inc,
            'batt': batt,
            'battery_enabled': battery_enabled,
            'total_extra': total_extra,
            'cost': cost,
            'payback': payback,
            'roi': roi,
        })
    return rows


def _print_pv_boost_comparison(all_boost_results, existing_pv_watts, battery_idx=0, price_idx=0):
    """Side-by-side ROI / payback of added PV panels against a no-extra-panels baseline."""
    if len(all_boost_results) < 2:
        return
    p0 = all_boost_results[0][1][battery_idx][1][price_idx]
    days = p0.get_grand_totals()['days']
    if days <= 0:
        return

    m = _pv_boost_metrics(all_boost_results, battery_idx, price_idx)
    labels = [x['label'] for x in m]
    col_w = max(14, max(len(l) for l in labels) + 2)
    row_w = 40
    battery_enabled = m[0]['battery_enabled']

    # Every figure below is already a delta vs the baseline (its column is all zero),
    # so no separate "Difference" column is needed.
    header = " " * row_w + "".join(l.rjust(col_w) for l in labels)

    def row(name, key, fmt):
        line = name.ljust(row_w) + "".join(fmt(x[key]).rjust(col_w) for x in m)
        print(line)

    def row_text(name, values):
        print(name.ljust(row_w) + "".join(v.rjust(col_w) for v in values))

    def money(v):
        return f"£{v:.2f}"

    def kwh(v):
        return f"{v:.1f}kWh"

    print(f"\nPV BOOST COMPARISON  (Δ vs baseline, annualised ×365/{days}d; "
          f"existing array {existing_pv_watts:.0f}W; battery cfg [{battery_idx}], tariff [{price_idx}])")
    print(header)

    row("Added PV capacity:", 'add_watts', lambda v: f"{v:.0f}W")
    row_text("Microinverter clip:",
             [(f"{x['clip_w']:.0f}W" if x['clip_w'] else "none") for x in m])
    row("Extra PV delivered:", 'deliv', kwh)
    row("Lost to clipping:", 'clip', kwh)
    row("Clipping loss (% of potential):", 'clip_pct', lambda v: f"{v:.1f}%")
    row("  value of clipped energy (~export):", 'clip_value', money)
    row("Extra self-consumption saving:", 'self_cons', money)
    row("Extra export (SEG) income:", 'export_inc', money)
    if battery_enabled:
        row("Extra battery saving:", 'batt', money)
    row("Total extra annual saving:", 'total_extra', money)
    row("Kit cost:", 'cost', money)

    row_text("Payback:",
             [(f"{x['payback']:.1f}yr" if x['payback'] is not None else "N/A") for x in m])
    row_text("Simple ROI (%/yr):",
             [(f"{x['roi']:.1f}%" if x['roi'] is not None else "N/A") for x in m])

    print("")
    print("  Assumes added panels share the existing array's generation profile (aspect,")
    print("  tilt, shading). Microinverter clipping is modelled from 5-min average power")
    print("  and slightly under-states real instantaneous clipping. DNO/G98 export limits")
    print("  and DC-side losses are not modelled; payback uses historical tariff rates.")


async def main():
    if '--help' in sys.argv or '-h' in sys.argv:
        _print_usage()
        return

    sunsynk_username = os.getenv('SUNSYNK_USERNAME')
    sunsynk_password = os.getenv('SUNSYNK_PASSWORD')

    settings = _load_settings(sys.argv[1:])

    results_dir = os.path.join(PROJECT_ROOT, 'results')
    os.makedirs(results_dir, exist_ok=True)
    _timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    _desc = re.sub(r'[^\w\-]', '_', str(settings['description'])).strip('_')
    _suffix = f'-{_desc}' if _desc else ''
    _results_path = os.path.join(results_dir, f'_{_timestamp}{_suffix}-result.txt')
    _results_file = open(_results_path, 'w', encoding='utf-8')
    sys.stdout = _Tee(sys.__stdout__, _results_file)

    show_days = bool(re.match('^on', str(settings['showDays']), re.IGNORECASE))
    start_date = str(settings['startDate'])
    stop_date = str(settings['stopDate'])
    scan_from_year = int(settings['scanFromYear'])
    original_price = float(settings['originalPrice'])

    # energyPrices can be a single filename string or a list of filenames
    raw_files = settings['energyPrices']
    energy_prices_files = raw_files if isinstance(raw_files, list) else [raw_files]

    # virtualBattery can be a single config dict or a list of configs for comparison
    vb_raw = settings['virtualBattery']
    battery_configs = vb_raw if isinstance(vb_raw, list) else [vb_raw]
    multi_battery = len(battery_configs) > 1

    # pvBoost: list of "plug-in solar" scenarios, always with a zero-add baseline first
    existing_pv_watts = float(settings['existingPvWatts'])
    pv_boosts = settings['pvBoost']
    multi_boost = len(pv_boosts) > 1

    print(f"Username: {sunsynk_username}")
    print(
        f"showDays:{settings['showDays']}  "
        f"energyPrices:{energy_prices_files}  "
        f"startDate:{start_date or '(all)'}  "
        f"stopDate:{stop_date or '(all)'}  "
        f"scanFromYear:{scan_from_year}"
    )
    print(f"originalPrice:£{original_price}")
    print(f"existingPvWatts:{existing_pv_watts:.0f}")
    if multi_boost:
        print(f"PV boost options: {len(pv_boosts)}")
        for b in pv_boosts:
            clip = f"{b['inverterClipW']:.0f}W" if b['inverterClipW'] else "none"
            print(
                f"  {b['label']}: +{b['addWatts']:.0f}W  £{b['cost']:.2f}  "
                f"clip:{clip}  (PV +{b['pv_extra_ratio']*100:.1f}%)"
            )
    if multi_battery:
        print(f"Virtual battery configs: {len(battery_configs)}")
        for bc in battery_configs:
            bc_label = bc.get('label', f"{bc['batterySize']}Wh")
            print(
                f"  {bc_label}: enabled:{bc['enabled']}  batterySize:{bc['batterySize']}  "
                f"usePV:{bc['usePV']}  startCharge:{bc['startCharge']}  stopCharge:{bc['stopCharge']}"
            )
    else:
        vb = battery_configs[0]
        print(
            f"Virtual battery: enabled:{vb['enabled']}  batterySize:{vb['batterySize']}  "
            f"usePV:{vb['usePV']}  startCharge:{vb['startCharge']}  stopCharge:{vb['stopCharge']}"
        )
        print(
            f"                 exportWindowStart:{vb['exportWindowStart']}  "
            f"exportWindowStop:{vb['exportWindowStop']}  useExport:{vb['useExport']}"
        )
        print(
            f"                 dischargeEfficiency:{vb['dischargeEfficiency']}  "
            f"chargeEfficiency:{vb['chargeEfficiency']}  "
            f"pvChargeEfficiency:{vb['pvChargeEfficiency']}  "
            f"maxOutputW:{vb['maxOutputW']}"
        )

    async with SunsynkEnergyClient(sunsynk_username, sunsynk_password, "https://api.sunsynk.net") as client:
        inverters = await client.get_inverters()
        for inverter in inverters:
            await client.get_inverter_realtime_grid(inverter.sn)
            await client.get_inverter_realtime_battery(inverter.sn)
            await client.get_inverter_realtime_input(inverter.sn)
            await client.get_inverter_realtime_output(inverter.sn)


            # Raw data caches: shared across every PV-boost × battery × price pass.
            # EnergyMonth is tariff-window-keyed; EnergyDay (parse-only, no battery) is keyed
            # by tariff window + PV-boost scale. Battery simulation is replayed per config from
            # the EnergyDay's pre-parsed interval tuples, eliminating all strptime overhead.
            _month_cache: dict = {}      # monthtocheck → EnergyMonth
            _day_raw_cache: dict = {}    # date_str → raw day dict
            _energyday_cache: dict = {}  # "{date}|{opstart}|{opstop}|{ratio}|{clip}" -> EnergyDay
            _cache_misses = 0
            _cache_hits = 0

            all_boost_results = []  # list of (boost, all_battery_results)

            for boost in pv_boosts:
                pv_extra_ratio = boost['pv_extra_ratio']
                pv_extra_clip_w = boost['inverterClipW']
                if multi_boost:
                    print(f"\n{'═' * 60}")
                    print(f"  PV boost pass: {boost['label']}  (+{boost['addWatts']:.0f}W)")
                    print('═' * 60)

                all_battery_results = []  # list of (label, list[EnergyPrices])

                for battery_config in battery_configs:
                    bc_label = battery_config.get('label', '')
                    if not bc_label and multi_battery:
                        bc_label = f"{battery_config['batterySize']}Wh"
                    battery_enabled = bool(
                        re.match('^on', str(battery_config['enabled']), re.IGNORECASE)
                    )

                    all_prices = []

                    for price_file in energy_prices_files:
                        prices_filename = "inverterData/" + price_file

                        try:
                            with open(prices_filename, encoding='utf-8') as data_file:
                                print(f"Loading: {prices_filename}")
                                energy_prices_data = json.load(data_file)
                        except Exception as e:
                            print(e)
                            sys.exit(-1)

                        pf_label = os.path.splitext(os.path.basename(price_file))[0]
                        if multi_battery and len(energy_prices_files) > 1:
                            label = f"{bc_label} / {pf_label}"
                        elif multi_battery:
                            label = bc_label
                        else:
                            label = pf_label

                        tmp_price = PriceData()
                        battery = _make_battery(battery_config, tmp_price)
                        prices = EnergyPrices(
                            energy_prices_data, battery,
                            original_price=original_price, label=label,
                            off_peak_baseline_kwh=float(settings['offPeakBaseline']),
                            off_peak_shift_enabled=bool(re.match('^on', str(settings['offPeakShift']), re.IGNORECASE)),
                            battery_enabled=battery_enabled,
                            battery_price=float(battery_config['batteryPrice']),
                        )

                        current_month = datetime.today().strftime('%Y-%m')
                        total_months = (
                            (int(current_month[:4]) - scan_from_year) * 12
                            + int(current_month[5:7])
                        )
                        hasyear = True
                        yearcount = scan_from_year
                        process_date = not start_date

                        with tqdm(total=total_months, unit='month', disable=show_days, dynamic_ncols=True) as pbar:
                            while hasyear and yearcount < 2040:
                                hasyear = False
                                count = 1
                                while count < 13:
                                    monthtocheck = f'{yearcount}-{count:02d}'
                                    if monthtocheck > current_month:
                                        break
                                    pbar.set_description(monthtocheck)
                                    if monthtocheck not in _month_cache:
                                        _month_cache[monthtocheck] = await client.get_energy_month(
                                            inverter.plant.id, monthtocheck
                                        )
                                    energymonth = _month_cache[monthtocheck]
                                    pbar.update(1)

                                    items = energymonth.get_load()

                                    if items is not None:
                                        for day in items['records']:
                                            hasyear = True
                                            prices.check_date(day['time'])

                                            check_date = datetime.strptime(day['time'], "%Y-%m-%d")
                                            if start_date:
                                                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                                                if check_date > start_dt:
                                                    process_date = True

                                            if stop_date:
                                                stop_dt = datetime.strptime(stop_date, "%Y-%m-%d")
                                                if check_date > stop_dt:
                                                    process_date = False

                                            if process_date:
                                                if show_days:
                                                    print(f"Calculating: {day['time']}")

                                                offpeakstart = prices.price_data.current_off_peak_start
                                                offpeakstop = prices.price_data.current_off_peak_stop
                                                ed_key = f"{day['time']}|{offpeakstart}|{offpeakstop}|{pv_extra_ratio:.6f}|{pv_extra_clip_w}"

                                                if ed_key not in _energyday_cache:
                                                    if day['time'] not in _day_raw_cache:
                                                        _cache_misses += 1
                                                        _day_raw_cache[day['time']] = await client.get_energy_day_raw(
                                                            inverter.plant.id, day['time']
                                                        )
                                                    _energyday_cache[ed_key] = EnergyDay(
                                                        _day_raw_cache[day['time']]['data'],
                                                        day['time'], energymonth,
                                                        None, offpeakstart, offpeakstop,
                                                        pv_extra_ratio=pv_extra_ratio, pv_extra_clip_w=pv_extra_clip_w,
                                                    )
                                                else:
                                                    _cache_hits += 1

                                                energyday = _energyday_cache[ed_key]
                                                energyday.run_battery(prices.battery)
                                                prices.add_data(energyday)

                                                if show_days:
                                                    energyday.print()

                                    count += 1
                                yearcount += 1

                        prices.get_grand_totals()
                        all_prices.append(prices)

                    all_battery_results.append((bc_label, all_prices))

                all_boost_results.append((boost, all_battery_results))

            all_battery_results = all_boost_results[0][1]  # baseline drives the main report

            print(f"[cache] day files: {_cache_misses} loaded from disk, {_cache_hits} served from memory cache")

            # Print results: solar/tariff output once (first battery config), battery per config
            first_battery = True
            for bc_label, all_prices in all_battery_results:
                if multi_battery:
                    print(f"\n{'━' * 60}")
                    print(f"  Virtual Battery: {bc_label}")
                    print('━' * 60)

                for prices in all_prices:
                    if len(all_prices) > 1:
                        print(f"\n{'─' * 60}")
                        print(f"  {prices.label}")
                        print('─' * 60)

                    if first_battery:
                        prices.print_energy_summary()
                        prices.print_averages()
                        prices.print_costs()
                        prices.print_savings()
                        prices.print_return_on_investment()
                        prices.print_totals()

                    prices.print_battery()

                first_battery = False

            # Comparison tables
            if multi_battery:
                for i, price_file in enumerate(energy_prices_files):
                    if len(energy_prices_files) > 1:
                        pf_label = os.path.splitext(os.path.basename(price_file))[0]
                        print(f"\n  [Battery comparison for: {pf_label}]")
                    _print_battery_comparison(all_battery_results, i)

            if len(energy_prices_files) > 1:
                _print_comparison(all_battery_results[0][1])

            if multi_boost:
                _print_pv_boost_comparison(all_boost_results, existing_pv_watts)

    sys.stdout = sys.__stdout__

    # Append raw config files to the results file only (not stdout)
    _results_file.write(f"\n{'═' * 60}\nCONFIGURATION FILES\n{'═' * 60}\n")
    config_files = [settings['_configPath']] + [
        "inverterData/" + pf for pf in energy_prices_files
    ]
    for path in config_files:
        _results_file.write(f"\n--- {path} ---\n")
        try:
            with open(path, encoding='utf-8') as _cf:
                _results_file.write(_cf.read())
        except Exception as _e:
            _results_file.write(f"(could not read: {_e})\n")

    _results_file.close()
    print(f"Results saved: {_results_path}")


if __name__ == '__main__':
    asyncio.run(main())
