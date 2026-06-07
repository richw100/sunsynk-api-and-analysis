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
            overrides['batterySize'] = int(value)
        elif key_lower == 'usepv':
            overrides['usePV'] = value
        elif key_lower == 'startcharge':
            overrides['startCharge'] = int(value)
        elif key_lower == 'stopcharge':
            overrides['stopCharge'] = int(value)
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
            overrides['exportWindowStart'] = value
        elif key_lower == 'exportwindowstop':
            overrides['exportWindowStop'] = value
        elif key_lower == 'dischargeefficiency':
            overrides.setdefault('virtualBattery', {})['dischargeEfficiency'] = float(value)
        elif key_lower == 'pvchargeefficiency':
            overrides.setdefault('virtualBattery', {})['pvChargeEfficiency'] = float(value)
        elif key_lower == 'maxoutputw':
            overrides.setdefault('virtualBattery', {})['maxOutputW'] = float(value)
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
        'batterySize': 5000,
        'usePV': 'OFF',
        'startCharge': 1000,
        'stopCharge': 2000,
        'energyPrices': '_EnergyPrices.json',
        'startDate': '',
        'stopDate': '',
        'scanFromYear': 2025,
        'originalPrice': 6206.47,
        'exportWindowStart': '17:00',
        'exportWindowStop': '19:00',
        'virtualBattery': {
            'dischargeEfficiency': 0.92,
            'pvChargeEfficiency': 0.96,
            'maxOutputW': 2400,
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
    print("  energyPrices:file.json  Energy prices file in inverterData/")
    print("  startDate:YYYY-MM-DD    Process days after this date")
    print("  stopDate:YYYY-MM-DD     Stop processing after this date")
    print("  scanFromYear:YYYY       First year to scan for data")
    print("  originalPrice:N         Installation cost for ROI calculation")
    print("  exportWindowStart:HH:MM Battery-to-grid export window start")
    print("  exportWindowStop:HH:MM  Battery-to-grid export window stop")
    print("")
    print("Virtual battery options:")
    print("  batterySize:N           Battery capacity in Wh")
    print("  usePV:ON|OFF            Charge battery from PV surplus")
    print("  startCharge:N           PV charging starts below this level (Wh)")
    print("  stopCharge:N            PV charging stops at this level (Wh)")
    print("  dischargeEfficiency:N   Discharge efficiency (e.g. 0.92)")
    print("  pvChargeEfficiency:N    PV charge efficiency (e.g. 0.96)")
    print("  maxOutputW:N            Maximum battery output in watts (e.g. 2400)")


async def main():
    if '--help' in sys.argv or '-h' in sys.argv:
        _print_usage()
        return

    sunsynk_username = os.getenv('SUNSYNK_USERNAME')
    sunsynk_password = os.getenv('SUNSYNK_PASSWORD')

    settings = _load_settings(sys.argv[1:])

    show_days = bool(re.match('^on', str(settings['showDays']), re.IGNORECASE))
    battery_size = int(settings['batterySize'])
    use_pv = 1 if re.match('^on', str(settings['usePV']), re.IGNORECASE) else 0
    start_charge = int(settings['startCharge'])
    stop_charge = int(settings['stopCharge'])
    energy_prices_file = settings['energyPrices']
    start_date = str(settings['startDate'])
    stop_date = str(settings['stopDate'])
    scan_from_year = int(settings['scanFromYear'])
    original_price = float(settings['originalPrice'])
    export_window_start = settings['exportWindowStart']
    export_window_stop = settings['exportWindowStop']
    vb = settings['virtualBattery']
    discharge_efficiency = float(vb['dischargeEfficiency'])
    pv_charge_efficiency = float(vb['pvChargeEfficiency'])
    max_output_w = float(vb['maxOutputW'])
    process_date = not start_date

    print(f"Username: {sunsynk_username}")
    print(f"showDays:{settings['showDays']}  energyPrices:{energy_prices_file}  startDate:{start_date or '(all)'}  stopDate:{stop_date or '(all)'}  scanFromYear:{scan_from_year}")
    print(f"originalPrice:£{original_price}  exportWindow:{export_window_start}-{export_window_stop}")
    print(f"Virtual battery: batterySize:{battery_size}  usePV:{settings['usePV']}  startCharge:{start_charge}  stopCharge:{stop_charge}")
    print(f"                 dischargeEfficiency:{discharge_efficiency}  pvChargeEfficiency:{pv_charge_efficiency}  maxOutputW:{max_output_w}")

    async with SunsynkEnergyClient(sunsynk_username, sunsynk_password, "https://api.sunsynk.net") as client:
        inverters = await client.get_inverters()
        for inverter in inverters:
            await client.get_inverter_realtime_grid(inverter.sn)
            await client.get_inverter_realtime_battery(inverter.sn)
            await client.get_inverter_realtime_input(inverter.sn)
            await client.get_inverter_realtime_output(inverter.sn)

            prices_filename = "inverterData/" + energy_prices_file

            tmp_price = PriceData()
            battery = VirtualBattery(
                tmp_price, battery_size, use_pv, start_charge, stop_charge,
                export_window_start=export_window_start,
                export_window_stop=export_window_stop,
                discharge_efficiency=discharge_efficiency,
                pv_charge_efficiency=pv_charge_efficiency,
                max_output_w=max_output_w,
            )

            energy_prices_data = None

            try:
                with open(prices_filename) as data_file:
                    print(f"Loading: {prices_filename}")
                    energy_prices_data = json.load(data_file)
            except Exception as e:
                print(e)
                exit(-1)

            prices = EnergyPrices(energy_prices_data, battery, original_price=original_price)

            days = 0
            current_month = datetime.today().strftime('%Y-%m')
            total_months = (int(current_month[:4]) - scan_from_year) * 12 + int(current_month[5:7])
            hasyear = True
            yearcount = scan_from_year
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
                                    days += 1

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

            prices.print_energy_summary()
            prices.print_averages()
            prices.print_costs()
            prices.print_savings()
            prices.print_return_on_investment()
            prices.print_battery()
            prices.print_totals()


asyncio.run(main())
