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

# Run collectdata analysis
python collectdata.py [showDays:ON|OFF] [batterySizeW] [usePVToChargeBattery:ON|OFF] [startChargeW] [stopChargeW] [_EnergyPrices.json|DEFAULT] [startDate:YYYY-MM-DD] [stopDate:YYYY-MM-DD]
# Example (Windows): runCollectData.bat
```

## Architecture

There are two distinct layers:

### 1. API client library (`sunsynk/`)

A thin async wrapper around the Sunsynk REST API (`https://api.sunsynk.net`). Authentication uses RSA+MD5 — the client fetches a public key, RSA-encrypts the password, and signs the request with MD5 (see `client.py:login`). The `cryptography` package is used for RSA encryption.

- `SunsynkClient` — upstream library class (v1.0.9); async context manager. Use `async with SunsynkClient(u, p) as client:` or `await SunsynkClient.create(u, p)`.
- `SunsynkEnergyClient` (`energy_client.py`) — our subclass of `SunsynkClient` that adds energy history methods and local file caching. This is what `collectdata.py` uses.
- API data models: `Battery` (`battery.py`), `Grid`, `Input`, `Output`, `Inverter`, `Plant`, `Vip` — all extend `Resource` (provides `__repr__`)

`SunsynkEnergyClient` accesses the parent's private `__get` HTTP method via its mangled name `_SunsynkClient__get`. This is intentional — noted in the class docstring.

`SunsynkEnergyClient._get_cached()` caches daily API responses as JSON files under `inverterData/day-YYYY-MM-DD.json`, skipping the API call if a file already exists (does **not** cache the current day).

### 2. Energy analysis tool (`collectdata.py` + `sunsynk/calculations.py` + `sunsynk/energyday.py`)

`collectdata.py` is a standalone CLI script that iterates over all historical months/days, calculates cost and savings, and prints a financial analysis of the solar/battery installation. It uses `SunsynkEnergyClient`.

`sunsynk/energyday.py` parses the 5-minute interval timeseries returned by the plant energy endpoints into `EnergyDay` and `EnergyMonth` objects.

`sunsynk/calculations.py` contains the analysis engine:

- `PriceData` — holds the current energy tariff rates (off-peak rate, peak rate, export rate, standing charge, compare rate, off-peak window). Hardcoded defaults, overridden per-period by `EnergyPrices.checkDate()`.
- `VirtualBattery` — simulates battery charge/discharge over historical 5-minute intervals, tracking kWh drawn, charged, and PV-charged to compute potential savings. Named `VirtualBattery` to distinguish it from `sunsynk/battery.py`'s `Battery` (the API response model for realtime battery state).
- `EnergySummary` — accumulates per-day data for a single tariff period; supports `newMonth()` to compound interest on cumulative savings.
- `EnergySummaryAggregator` — holds a list of `EnergySummary` objects (one per tariff period) and computes cross-period grand totals.
- `EnergyPrices` — top-level orchestrator; reads `_EnergyPrices.json` for tariff history, calls `checkDate()` on each day to switch tariff periods, and exposes `print_*` methods for the final report.

### Energy price data files (`inverterData/_EnergyPrices*.json`)

JSON files defining tariff periods with fields: `datefrom`, `dateto`, `offpeakRate`, `offpeakStart`, `offpeakStop`, `peakRate`, `exportRate`, `standingCharge`, `CompareRate`, `CompareStandingCharge`, `InterestRate`.

## Tests

Tests use `pytest-asyncio` and `pytest-aiohttp`. The `MockApiServer` in `tests/mock_api_server.py` spins up a local aiohttp server that generates a real RSA key pair, serves it via `/anonymous/publicKey`, and verifies the encrypted password on login — matching the real API's auth flow. Tests are all async and use `aiohttp_client` + `event_loop` fixtures.
