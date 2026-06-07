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

    settings.update(overrides)

    defaults = {
        'showDays': 'ON',
        'batterySize': 5000,
        'usePV': 'OFF',
        'startCharge': 1000,
        'stopCharge': 2000,
        'energyPrices': '_EnergyPrices.json',
        'startDate': '',
        'stopDate': ''
    }
    for key, value in defaults.items():
        settings.setdefault(key, value)

    return settings


async def main():
    sunsynk_username = os.getenv('SUNSYNK_USERNAME')
    sunsynk_password = os.getenv('SUNSYNK_PASSWORD')

    settings = _load_settings(sys.argv[1:])

    showDays = bool(re.match('^on', str(settings['showDays']), re.IGNORECASE))
    batterySize = int(settings['batterySize'])
    usePV = 1 if re.match('^on', str(settings['usePV']), re.IGNORECASE) else 0
    startCharge = int(settings['startCharge'])
    stopCharge = int(settings['stopCharge'])
    energyPricesFile = settings['energyPrices']
    startDate = str(settings['startDate'])
    stopDate = str(settings['stopDate'])
    processDate = 0 if startDate else 1

    print(f"Username: {sunsynk_username}")
    print(f"showDays:{settings['showDays']}  batterySize:{batterySize}  usePV:{settings['usePV']}  startCharge:{startCharge}  stopCharge:{stopCharge}")
    print(f"energyPrices:{energyPricesFile}  startDate:{startDate or '(all)'}  stopDate:{stopDate or '(all)'}")

    async with SunsynkEnergyClient(sunsynk_username, sunsynk_password, "https://api.sunsynk.net") as client:
        inverters = await client.get_inverters()
        for inverter in inverters:
            await client.get_inverter_realtime_grid(inverter.sn)
            await client.get_inverter_realtime_battery(inverter.sn)
            await client.get_inverter_realtime_input(inverter.sn)
            await client.get_inverter_realtime_output(inverter.sn)

            pricesFilename = "inverterData/" + energyPricesFile

            tmpPrice = PriceData()
            battery = VirtualBattery(tmpPrice, batterySize, usePV, startCharge, stopCharge)

            energyPricesData = None

            try:
                with open(pricesFilename) as data_file:
                    print(f"Loading: {pricesFilename}")
                    energyPricesData = json.load(data_file)
            except Exception as e:
                print(e)
                exit(-1)

            prices = EnergyPrices(energyPricesData, battery)

            days = 0
            hasyear = 1
            yearcount = 2025
            while yearcount < 2040:
                if hasyear == 1:
                    hasyear = 0
                    count = 1
                    while count < 13:

                        join = "-"
                        if count < 10:
                            join = "-0"
                        monthtocheck = str(yearcount) + join + str(count)
                        energymonth = await client.get_energy_month(inverter.plant.id, monthtocheck)

                        items = energymonth.get_Load()

                        if items != None:

                            for day in items['records']:
                                hasyear = 1
                                prices.checkDate(day['time'])

                                checkDate = datetime.strptime(day['time'], "%Y-%m-%d")
                                if startDate:
                                    startDateTime = datetime.strptime(startDate, "%Y-%m-%d")
                                    if checkDate > startDateTime:
                                        processDate = 1

                                if stopDate:
                                    stopDateTime = datetime.strptime(stopDate, "%Y-%m-%d")
                                    if checkDate > stopDateTime:
                                        processDate = 0

                                if processDate == 1:
                                    days += 1

                                    if showDays:
                                        print(f"Calculating: {day['time']}")

                                    energyday = await client.get_energy_day(inverter.plant.id, day['time'], energymonth, prices.battery, prices.priceData.currentOffPeakStart, prices.priceData.currentOffPeakStop)
                                    prices.addData(energyday)

                                    if showDays:
                                        energyday.print()

                        count += 1
                yearcount += 1

            prices.get_grand_totals()

            prices.print_energy_summary()
            prices.print_averages()
            prices.print_totals()
            prices.print_return_on_investment()
            prices.print_savings()
            prices.print_battery()
            prices.print_costs()


asyncio.run(main())
