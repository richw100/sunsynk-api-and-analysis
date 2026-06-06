# Sunsynk API and Analysis

> Builds on [sunsynk-api-client](https://pypi.org/project/sunsynk-api-client/) by James Ridgway (installed via pip), extended with energy cost analysis and battery simulation tools.

Reads data from the Sunsynk API (used by the Sunsynk Connect apps and [PowerView](https://pv.inteless.com/) portal) and provides a CLI tool for analysing historical energy usage, costs, and solar/battery return on investment.

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/richw100/sunsynk-api-and-analysis.git
cd sunsynk-api-and-analysis
```

### 2. Create the virtual environment

```bash
bash setup.sh
```

This creates a `venv/` directory and installs `sunsynk-api-client` and all dependencies.

### 3. Set credentials

```bash
export SUNSYNK_USERNAME=your@email.com
export SUNSYNK_PASSWORD=yourpassword
```

Or leave them unset — the `runCollectData.sh` launcher will prompt for them.

## Running the analysis

```bash
# Linux/Debian
./analysis/runCollectData.sh

# Windows
analysis\runCollectData.bat
```

Optional arguments (positional):

```
showDays            ON|OFF              Show per-day breakdown (default: Off)
batterySizeW        integer             Battery size in watts (default: 5000)
usePVToChargeBattery ON|OFF            Count PV charging in savings (default: on)
startChargeW        integer             Charge start threshold in watts (default: 2500)
stopChargeW         integer             Charge stop threshold in watts (default: 5000)
pricesFile          filename|DEFAULT    Energy prices JSON file (default: DEFAULT)
startDate           YYYY-MM-DD          Start of analysis period
stopDate            YYYY-MM-DD          End of analysis period
```

## Example API usage

```python
import asyncio
import os

from sunsynk.client import SunsynkClient


async def main():
    async with SunsynkClient(os.getenv('SUNSYNK_USERNAME'), os.getenv('SUNSYNK_PASSWORD')) as client:
        inverters = await client.get_inverters()
        for inverter in inverters:
            grid = await client.get_inverter_realtime_grid(inverter.sn)
            battery = await client.get_inverter_realtime_battery(inverter.sn)
            solar_pv = await client.get_inverter_realtime_input(inverter.sn)

            print(f"Inverter {inverter.sn}: grid={grid.get_power()}kW, "
                  f"battery={battery.power}kW, solar={solar_pv.get_power()}kW")

asyncio.run(main())
```

## Running tests

```bash
./run-tests.sh
```
