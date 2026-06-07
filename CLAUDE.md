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
python analysis/collectdata.py [showDays:ON|OFF] [batterySizeW] [usePVToChargeBattery:ON|OFF] [startChargeW] [stopChargeW] [_EnergyPrices.json|DEFAULT] [startDate:YYYY-MM-DD] [stopDate:YYYY-MM-DD]
# Linux/Debian: analysis/runCollectData.sh
# Windows:      analysis\runCollectData.bat
```

## Architecture

### `sunsynk/` — upstream API client library (PyPI dependency)

Installed via `pip install sunsynk-api-client`. A thin async wrapper around the Sunsynk REST API (`https://api.sunsynk.net`). Authentication uses RSA+MD5.

- `SunsynkClient` — async context manager. Use `async with SunsynkClient(u, p) as client:` or `await SunsynkClient.create(u, p)`.
- API data models: `Battery`, `Grid`, `Input`, `Output`, `Inverter`, `Plant`, `Vip` — all extend `Resource`

### `analysis/` — our extensions and energy analysis engine

- `collectdata.py` — standalone CLI script; iterates over all historical months/days and prints a financial analysis of the solar/battery installation. Can be run from any directory (`python analysis/collectdata.py ...` or `python3 collectdata.py ...` from within `analysis/`). It sets `PROJECT_ROOT` from `__file__` and calls `os.chdir(PROJECT_ROOT)` at startup so imports and `inverterData/` paths always resolve correctly.
- `runCollectData.sh` — Linux/Debian launcher; prompts for credentials if not already set as env vars (password prompt is silent). Run from any directory.
- `runCollectData.bat` — Windows equivalent of the above.
- `energy_client.py` — `SunsynkEnergyClient(SunsynkClient)`: subclass that adds `get_energy_day()`, `get_energy_month()`, and `_get_cached()` (local file caching of daily API responses to `inverterData/day-YYYY-MM-DD.json`, skipping the API call if a file exists and not caching the current day). Accesses the parent's private HTTP method via its mangled name `_SunsynkClient__get` — noted in the class docstring.
- `energymonth.py` — `EnergyMonth`: parses monthly daily kWh totals from the plant energy month endpoint (Load, PV, Export, Import labels).
- `energyday.py` — `IntervalSummary` and `EnergyDay`: parse the 5-minute interval timeseries from the plant energy day endpoint. `IntervalSummary` accumulates peak/offpeak Wh totals for one label (PV, Grid, Load); named `IntervalSummary` to avoid a name clash with `calculations.py`'s `EnergySummary`.
- `calculations.py` — the analysis engine:
  - `PriceData` — current energy tariff rates (off-peak/peak/export rates, standing charge, off-peak window). Hardcoded defaults, overridden per-period by `EnergyPrices.checkDate()`.
  - `VirtualBattery` — simulates battery charge/discharge over historical 5-minute intervals, tracking kWh drawn, charged, and PV-charged to compute potential savings. Named `VirtualBattery` to distinguish it from the upstream `Battery` model (realtime API response).
  - `EnergySummary` — accumulates per-day financial data for a single tariff period; `newMonth()` compounds interest on cumulative savings.
  - `EnergySummaryAggregator` — holds one `EnergySummary` per tariff period and computes cross-period grand totals.
  - `EnergyPrices` — top-level orchestrator; reads `_EnergyPrices.json`, calls `checkDate()` to switch tariff periods, and exposes `print_*` methods for the final report.

### `inverterData/` — local data cache (not in git)

### Energy price data files (`inverterData/_EnergyPrices*.json`)

JSON files defining tariff periods with fields: `datefrom`, `dateto`, `offpeakRate`, `offpeakStart`, `offpeakStop`, `peakRate`, `exportRate`, `standingCharge`, `CompareRate`, `CompareStandingCharge`, `InterestRate`.

## Tests

Tests use `pytest-asyncio` and `pytest-aiohttp`. The `MockApiServer` in `tests/mock_api_server.py` spins up a local aiohttp server that generates a real RSA key pair, serves it via `/anonymous/publicKey`, and verifies the encrypted password on login — matching the real API's auth flow. It also serves `/api/v1/plant/energy/{plant_id}/month` and `/api/v1/plant/energy/{plant_id}/day` for energy endpoint tests. Tests are all async and use `aiohttp_client` + `event_loop` fixtures.

- `tests/test_client.py` — integration tests for `SunsynkEnergyClient`: energy month/day fetching, caching, and battery simulation via the mock server.
- `tests/test_calculations.py` — unit tests for `calculations.py`: `VirtualBattery`, `EnergySummary`, `EnergySummaryAggregator`, and `EnergyPrices`.
