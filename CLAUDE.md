# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This project is a fork of [sunsynk-api-client](https://github.com/jamesridgway/sunsynk-api-client) by James Ridgway, extended with energy cost analysis and battery simulation tools.

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
./run-pylint.sh        # lint the sunsynk package

# Run a single test
./venv/bin/pytest tests/test_client.py::test_get_inverters

# Run collectdata analysis (from project root)
python analysis/collectdata.py [showDays:ON|OFF] [batterySizeW] [usePVToChargeBattery:ON|OFF] [startChargeW] [stopChargeW] [_EnergyPrices.json|DEFAULT] [startDate:YYYY-MM-DD] [stopDate:YYYY-MM-DD]
# Example (Windows, run from project root): analysis\runCollectData.bat
```

## Architecture

The project has three top-level packages:

### `sunsynk/` — upstream API client library (unmodified)

A thin async wrapper around the Sunsynk REST API (`https://api.sunsynk.net`). Authentication uses RSA+MD5 — the client fetches a public key, RSA-encrypts the password, and signs the request with MD5 (see `sunsynk/client.py:login`). The `cryptography` package is used for RSA encryption.

- `SunsynkClient` — upstream library class (v1.0.9); async context manager. Use `async with SunsynkClient(u, p) as client:` or `await SunsynkClient.create(u, p)`.
- API data models: `Battery` (`battery.py`), `Grid`, `Input`, `Output`, `Inverter`, `Plant`, `Vip` — all extend `Resource` (provides `__repr__`)

Do not add custom code here — this package tracks the upstream library and should remain clean for easy updates.

### `analysis/` — our extensions and energy analysis engine

- `collectdata.py` — standalone CLI script; iterates over all historical months/days and prints a financial analysis of the solar/battery installation. Run from the project root as `python analysis/collectdata.py ...`. On Windows use `analysis\runCollectData.bat`.
- `energy_client.py` — `SunsynkEnergyClient(SunsynkClient)`: subclass that adds `get_energy_day()`, `get_energy_month()`, and `_get_cached()` (local file caching of daily API responses to `inverterData/day-YYYY-MM-DD.json`, skipping the API call if a file exists and not caching the current day). Accesses the parent's private HTTP method via its mangled name `_SunsynkClient__get` — noted in the class docstring.
- `energyday.py` — `EnergyDay` and `EnergyMonth`: parse the 5-minute interval timeseries from the plant energy endpoints.
- `calculations.py` — the analysis engine:
  - `PriceData` — current energy tariff rates (off-peak/peak/export rates, standing charge, off-peak window). Hardcoded defaults, overridden per-period by `EnergyPrices.checkDate()`.
  - `VirtualBattery` — simulates battery charge/discharge over historical 5-minute intervals, tracking kWh drawn, charged, and PV-charged to compute potential savings. Named `VirtualBattery` to distinguish it from `sunsynk/battery.py`'s `Battery` (the realtime API response model).
  - `EnergySummary` — accumulates per-day data for a single tariff period; `newMonth()` compounds interest on cumulative savings.
  - `EnergySummaryAggregator` — holds one `EnergySummary` per tariff period and computes cross-period grand totals.
  - `EnergyPrices` — top-level orchestrator; reads `_EnergyPrices.json`, calls `checkDate()` to switch tariff periods, and exposes `print_*` methods for the final report.

### `inverterData/` — local data cache (not in git)

### Energy price data files (`inverterData/_EnergyPrices*.json`)

JSON files defining tariff periods with fields: `datefrom`, `dateto`, `offpeakRate`, `offpeakStart`, `offpeakStop`, `peakRate`, `exportRate`, `standingCharge`, `CompareRate`, `CompareStandingCharge`, `InterestRate`.

## Tests

Tests use `pytest-asyncio` and `pytest-aiohttp`. The `MockApiServer` in `tests/mock_api_server.py` spins up a local aiohttp server that generates a real RSA key pair, serves it via `/anonymous/publicKey`, and verifies the encrypted password on login — matching the real API's auth flow. Tests are all async and use `aiohttp_client` + `event_loop` fixtures.
