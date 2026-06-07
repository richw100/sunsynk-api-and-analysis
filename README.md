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

Settings are loaded from `config.json` in the project root. Any argument on the command line overrides the config file using `key:value` syntax.

### General options

| Argument | Values | Description |
|---|---|---|
| `config:path` | file path | Config file to load (default: `config.json`) |
| `showDays:` | `ON`\|`OFF` | Print per-day breakdown |
| `energyPrices:` | filename | Energy prices JSON file in `inverterData/` (or a list in `config.json` for multi-tariff comparison) |
| `startDate:` | `YYYY-MM-DD` | Process days after this date |
| `stopDate:` | `YYYY-MM-DD` | Stop processing after this date |
| `scanFromYear:` | `YYYY` | First year to scan for historical data |
| `originalPrice:` | number | Installation cost (£) used for ROI calculation |
| `exportWindowStart:` | `HH:MM` | Battery-to-grid export window start |
| `exportWindowStop:` | `HH:MM` | Battery-to-grid export window stop |
| `useExport:` | `ON`\|`OFF` | Sell battery charge back to grid during export window |

### Virtual battery options

| Argument | Values | Description |
|---|---|---|
| `batterySize:` | integer (Wh) | Battery capacity |
| `usePV:` | `ON`\|`OFF` | Charge battery from PV surplus |
| `startCharge:` | integer (Wh) | Begin PV charging when battery falls below this level |
| `stopCharge:` | integer (Wh) | Stop PV charging when battery reaches this level |
| `dischargeEfficiency:` | float (e.g. `0.92`) | Fraction of stored energy delivered to load |
| `chargeEfficiency:` | float (e.g. `0.95`) | Fraction of grid energy actually stored (default `1.0`) |
| `pvChargeEfficiency:` | float (e.g. `0.96`) | Fraction of PV surplus actually stored |
| `maxOutputW:` | float (e.g. `2400`) | Maximum battery output in watts |

### Multi-tariff comparison

To compare multiple tariff scenarios side by side, set `energyPrices` to a list in `config.json`:

```json
{ "energyPrices": ["_EnergyPricesA.json", "_EnergyPricesB.json"] }
```

Each file is simulated independently and a `COMPARISON` table is printed at the end. A single filename string is still accepted for backward compatibility.

## Understanding the output

### Interval-calculated vs inverter-reported values

Many energy totals are shown as two values separated by `|`:

- **Interval-calculated**: derived from 5-minute power readings fetched from the Sunsynk API. More granular but subject to sampling artefacts and small rounding errors.
- **Inverter-reported**: daily kWh totals reported directly by the inverter — what the Sunsynk Connect app and PowerView portal display. Considered ground truth for billing purposes.

Small differences between the two are normal.

---

### ENERGY TOTALS / DAILY AVERAGES

Total (and per-day average) kWh for the analysis period, split by:

- **Export**: electricity sent to the grid
- **Import**: electricity drawn from the grid
- **PV gen**: solar generation
- **Load**: total household consumption
- **Peak / Off-peak**: import and export during and outside the off-peak tariff window

---

### ENERGY COSTS

Actual energy costs for the period:

- **Total paid to energy company**: grid import cost plus standing charge
- **Total paid after deducting SEG income**: net bill after subtracting export payments
- **Smart Export Guarantee (SEG) income**: payments received for electricity exported to the grid under the SEG scheme
- **Estimated cost without solar or battery**: what the energy bill would have been with the same household load but no solar or battery

---

### SOLAR SAVINGS

Money saved compared to having no solar installation:

- **Savings excl. export income**: reduction in grid import cost from solar generation alone
- **Savings incl. SEG income**: total saving including export payments received

---

### RETURN ON INVESTMENT

- **Solar savings from generation**: cumulative savings from solar (import reduction + export income), excluding the off-peak load-shifting benefit. Shown as interval-calculated and inverter-reported, each as a percentage of the original installation cost.
- **Total savings incl. off-peak load shifting**: adds savings from deliberately running loads during the cheaper off-peak tariff window.
- **Cumulative savings (with compound interest)**: savings accumulated month-by-month, with compound interest applied each month at the configured `InterestRate` — models what the savings are worth as a cash return.
- **Alternative investment value**: what the original installation cost would be worth if invested at the same interest rate over the same period — used as the denominator for the net ROI percentage.
- **Net ROI**: cumulative savings ÷ alternative investment value × 100.

---

### OFF-PEAK IMPORT ANALYSIS

- **Total off-peak import**: all electricity imported during the off-peak window.
- **Excess above expected baseline**: off-peak import beyond a per-day baseline (96% of off-peak window hours ÷ 7). Positive values indicate load has been deliberately shifted to the cheap tariff (e.g. EV charging, dishwasher timer).
- **Saving from off-peak load shifting**: excess kWh × (peak rate − off-peak rate).

---

### VIRTUAL BATTERY SIMULATION

Models the financial benefit of a battery that is fully charged from the grid each off-peak period and discharged to meet peak-time demand. All values are simulated — they represent potential savings for a battery you are evaluating, or actual savings if you already have one.

- **Charged from grid at off-peak**: total kWh stored from the grid each night, and what it cost at the off-peak rate (adjusted for grid charging efficiency if `chargeEfficiency` < 1).
- **Peak-rate value of energy delivered**: what the discharged energy would have cost if drawn from the grid at the peak rate — the gross saving before costs.
- **PV energy diverted to battery**: PV surplus redirected to top up the battery instead of being exported. Shown as the foregone SEG income (opportunity cost of PV charging).
- **Re-exported to grid during export window**: energy discharged back to the grid (when `useExport:ON`), and the SEG income received.
- **Days battery ran out before peak demand met**: days where the battery was exhausted mid-peak and the shortfall had to be drawn from the grid at full price.
- **Net potential saving**: `peak saving + export income − charging cost − foregone export income`. The formula is shown on the following line.
- **Per day / Annualised**: net potential saving expressed as a daily rate and extrapolated to a full year.

---

### COMPARISON (multi-tariff mode)

When `energyPrices` lists multiple files, each tariff is simulated independently. After the per-tariff output, a columnar `COMPARISON` table shows all key metrics side by side. When exactly two tariff files are provided, a `Difference` column shows the change from the first to the second.

---

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
