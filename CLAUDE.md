# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This project builds on [sunsynk-api-client](https://pypi.org/project/sunsynk-api-client/) by James Ridgway (installed via pip) and adds energy cost analysis and battery simulation tools.

## Setup

```bash
bash setup.sh          # creates venv and installs requirements
```

Credentials are passed via environment variables:
```bash
export SUNSYNK_USERNAME=...
export SUNSYNK_PASSWORD=...
```

## Commands

```bash
./run-tests.sh         # run tests with coverage
./run-pylint.sh        # lint the analysis package

# Run a single test
./venv/bin/pytest tests/test_client.py::test_get_inverters

# Run collectdata analysis (works from any directory)
# Settings are loaded from config.json; any key:value arg overrides the file.
python analysis/collectdata.py [config:path/to/config.json] [showDays:ON|OFF] \
  [energyPrices:file.json] [startDate:YYYY-MM-DD] [stopDate:YYYY-MM-DD] \
  [offPeakShift:ON|OFF] [offPeakBaseline:N] \
  [useBattery:ON|OFF] [batterySize:N] [usePV:ON|OFF] [startCharge:N] [stopCharge:N] \
  [exportWindowStart:HH:MM] [exportWindowStop:HH:MM] [useExport:ON|OFF] [gridCharge:ON|OFF]
# Linux/Debian: analysis/runCollectData.sh [key:value ...]
# Windows:      analysis\runCollectData.bat [key:value ...]
```

## Architecture

### `sunsynk/` — upstream API client library (PyPI dependency)

Installed via `pip install sunsynk-api-client`. A thin async wrapper around the Sunsynk REST API (`https://api.sunsynk.net`). Authentication uses RSA+MD5.

- `SunsynkClient` — async context manager. Use `async with SunsynkClient(u, p) as client:` or `await SunsynkClient.create(u, p)`.
- API data models: `Battery`, `Grid`, `Input`, `Output`, `Inverter`, `Plant`, `Vip` — all extend `Resource`

### `analysis/` — our extensions and energy analysis engine

- `collectdata.py` — standalone CLI script; iterates over all historical months/days and prints a financial analysis of the solar/battery installation. Can be run from any directory (`python analysis/collectdata.py ...` or `python3 collectdata.py ...` from within `analysis/`). It sets `PROJECT_ROOT` from `__file__` and calls `os.chdir(PROJECT_ROOT)` at startup so imports and `inverterData/` paths always resolve correctly. Settings are loaded from `config.json` (project root) then overridden by any `key:value` CLI args; use `config:path.json` to load a different file. Battery-related settings (`batterySize`, `usePV`, `startCharge`, `stopCharge`, `exportWindowStart`, `exportWindowStop`, `useExport`, efficiencies) all live under the `virtualBattery` key in config.
- `runCollectData.sh` — Linux/Debian launcher; prompts for credentials if not already set as env vars (password prompt is silent). Run from any directory.
- `runCollectData.bat` — Windows equivalent of the above.
- `energy_client.py` — `SunsynkEnergyClient(SunsynkClient)`: subclass that adds `get_energy_day()`, `get_energy_month()`, and `_get_cached()` (local file caching to `inverterData/day-YYYY-MM-DD.json` and `inverterData/month-YYYY-MM.json`; skips the API call if a cached file exists, and skips writing for the current day or month). The "do not cache" check uses `date != self._today[:len(date)]` — works for both day (`YYYY-MM-DD`) and month (`YYYY-MM`) keys. Accesses the parent's private HTTP method via its mangled name `_SunsynkClient__get` — noted in the class docstring.
- `energymonth.py` — `EnergyMonth`: parses monthly daily kWh totals from the plant energy month endpoint (Load, PV, Export, Import labels).
- `energyday.py` — `IntervalSummary` and `EnergyDay`: parse the 5-minute interval timeseries from the plant energy day endpoint. `IntervalSummary` accumulates peak/offpeak Wh totals for one label (PV, Grid, Load); named `IntervalSummary` to avoid a name clash with `energysummary.py`'s `EnergySummary`.
- `pricedata.py` — `PriceData` dataclass: energy tariff rates (off-peak/peak/export rates, standing charge, off-peak window). Hardcoded defaults, overridden per-period by `EnergyPrices.check_date()`. `off_peak_baseline_kwh` is the configurable reference (kWh/day for a 7-hour window) used to estimate baseline off-peak usage; `off_peak_average` is computed from it by `EnergySummary` — do not set directly.
- `virtualbattery.py` — `VirtualBattery`: simulates battery charge/discharge over historical 5-minute intervals, tracking kWh drawn, charged, and PV-charged to compute potential savings. Named `VirtualBattery` to distinguish it from the upstream `Battery` model (realtime API response). `grid_charge=False` disables grid top-up at off-peak (models PV-only charging).
- `energysummary.py` — `QueryType` enum, `EnergySummary`, and `EnergySummaryAggregator`:
  - `EnergySummary` — accumulates per-day financial data for a single tariff period; `new_month()` compounds interest on cumulative savings. Computes `off_peak_average` from `price_data.off_peak_baseline_kwh` scaled to the actual window length.
  - `EnergySummaryAggregator` — holds one `EnergySummary` per tariff period and computes cross-period grand totals.
- `energyprices.py` — `EnergyPrices`: top-level orchestrator; reads one or more `_EnergyPrices*.json` files (supports multi-file tariff comparison), calls `check_date()` to switch tariff periods, and exposes `print_*` methods for the final report. Constructor params: `off_peak_baseline_kwh`, `off_peak_shift_enabled` (include off-peak load-shifting in savings), `battery_enabled` (show/include virtual battery section), `battery_price` (£ cost of battery; triggers payback-period display in `print_battery()` and comparison table).

Import chain (no cycles): `pricedata` → `virtualbattery` → `energysummary` → `energyprices`.

### `inverterData/` — local data cache (not in git)

### Energy price data files (`inverterData/_EnergyPrices*.json`)

JSON files defining tariff periods with fields: `datefrom`, `dateto`, `offpeakRate`, `offpeakStart`, `offpeakStop`, `peakRate`, `exportRate`, `standingCharge`, `InterestRate`. (`CompareRate` and `CompareStandingCharge` are silently ignored if present in older files.)

`energyPrices` in `config.json` can be a single filename string or a list of filenames for side-by-side tariff comparison.

`virtualBattery` in `config.json` can be a single config object (current default) or a list of config objects for side-by-side battery comparison. Each object in the list can have an optional `label` field and only needs to specify fields that differ from the defaults. CLI battery overrides (e.g. `batterySize:N`) apply to all configs in the list. In multi-battery mode, solar/tariff output is printed once (first config); battery sections are printed per config; a `BATTERY COMPARISON` table follows. `_make_battery(vb, price_data)` takes the battery config dict directly.

## Tests

Tests use `pytest-asyncio` and `pytest-aiohttp`. The `MockApiServer` in `tests/mock_api_server.py` spins up a local aiohttp server that generates a real RSA key pair, serves it via `/anonymous/publicKey`, and verifies the encrypted password on login — matching the real API's auth flow. It also serves `/api/v1/plant/energy/{plant_id}/month` and `/api/v1/plant/energy/{plant_id}/day` for energy endpoint tests. Tests are all async and use `aiohttp_client` + `event_loop` fixtures.

- `tests/test_client.py` — integration tests for `SunsynkEnergyClient`: energy month/day fetching, caching, and battery simulation via the mock server.
- `tests/test_calculations.py` — unit tests for `VirtualBattery`, `EnergySummary`, `EnergySummaryAggregator`, and `EnergyPrices`.
