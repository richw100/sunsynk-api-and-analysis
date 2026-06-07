import asyncio
import os
import json
import sys
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis.energy_client import SunsynkEnergyClient

from analysis.pricedata import PriceData
from analysis.virtualbattery import VirtualBattery
from analysis.energyprices import EnergyPrices

from datetime import datetime
from tqdm import tqdm


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
        elif key_lower == 'usebattery':
            overrides.setdefault('virtualBattery', {})['enabled'] = value
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

    # Merge virtualBattery subsection before the top-level update to avoid
    # a shallow-copy replacing the whole sub-dict with a partial override.
    vb_override = overrides.pop('virtualBattery', {})
    settings.update(overrides)
    settings.setdefault('virtualBattery', {}).update(vb_override)

    defaults = {
        'showDays': 'ON',
        'energyPrices': '_EnergyPrices.json',
        'startDate': '',
        'stopDate': '',
        'scanFromYear': 2025,
        'originalPrice': 6206.47,
        'offPeakShift': 'ON',
        'offPeakBaseline': 0.96,
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
        },
    }
    for key, value in defaults.items():
        settings.setdefault(key, value)
    # Fill any missing virtualBattery sub-keys
    for key, value in defaults['virtualBattery'].items():
        settings['virtualBattery'].setdefault(key, value)

    return settings


def _print_usage():
    print("Usage: collectdata.py [key:value ...]")
    print("")
    print("General options:")
    print("  config:path.json        Config file to load (default: config.json)")
    print("  showDays:ON|OFF         Print per-day output")
    print("  energyPrices:file.json  Energy prices file in inverterData/ (or list in config)")
    print("  startDate:YYYY-MM-DD    Process days after this date")
    print("  stopDate:YYYY-MM-DD     Stop processing after this date")
    print("  scanFromYear:YYYY       First year to scan for data")
    print("  originalPrice:N         Installation cost for ROI calculation")
    print("  offPeakShift:ON|OFF     Include off-peak load-shifting in savings totals (default: ON)")
    print("  offPeakBaseline:N       Expected kWh/day at off-peak for a 7-hour window (default: 0.96)")
    print("")
    print("Virtual battery options:")
    print("  useBattery:ON|OFF       Enable virtual battery simulation (default: ON)")
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


def _make_battery(settings, price_data):
    vb = settings['virtualBattery']
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

    # energyPrices can be a single filename string or a list of filenames
    raw_files = settings['energyPrices']
    energy_prices_files = raw_files if isinstance(raw_files, list) else [raw_files]

    vb = settings['virtualBattery']
    print(f"Username: {sunsynk_username}")
    print(
        f"showDays:{settings['showDays']}  "
        f"energyPrices:{energy_prices_files}  "
        f"startDate:{start_date or '(all)'}  "
        f"stopDate:{stop_date or '(all)'}  "
        f"scanFromYear:{scan_from_year}"
    )
    print(f"originalPrice:£{original_price}")
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

                label = os.path.splitext(os.path.basename(price_file))[0]
                tmp_price = PriceData()
                battery = _make_battery(settings, tmp_price)
                prices = EnergyPrices(
                    energy_prices_data, battery,
                    original_price=original_price, label=label,
                    off_peak_baseline_kwh=float(settings['offPeakBaseline']),
                    off_peak_shift_enabled=bool(re.match('^on', str(settings['offPeakShift']), re.IGNORECASE)),
                    battery_enabled=bool(re.match('^on', str(vb['enabled']), re.IGNORECASE)),
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
                            energymonth = await client.get_energy_month(inverter.plant.id, monthtocheck)
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

                                        energyday = await client.get_energy_day(
                                            inverter.plant.id, day['time'], energymonth,
                                            prices.battery,
                                            prices.price_data.current_off_peak_start,
                                            prices.price_data.current_off_peak_stop
                                        )
                                        prices.add_data(energyday)

                                        if show_days:
                                            energyday.print()

                            count += 1
                        yearcount += 1

                prices.get_grand_totals()
                all_prices.append(prices)

            for prices in all_prices:
                if len(all_prices) > 1:
                    print(f"\n{'─' * 60}")
                    print(f"  {prices.label}")
                    print('─' * 60)
                prices.print_energy_summary()
                prices.print_averages()
                prices.print_costs()
                prices.print_savings()
                prices.print_return_on_investment()
                prices.print_battery()
                prices.print_totals()

            if len(all_prices) > 1:
                _print_comparison(all_prices)


asyncio.run(main())
