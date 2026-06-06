# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

A thin async wrapper around the Sunsynk/PowerView REST API (`https://pv.inteless.com`). It uses RSA+MD5 to authenticate (see `client.py:login`), then exposes typed data objects:

- `SunsynkClient` — async context manager; use `async with SunsynkClient(u, p) as client:` or `await SunsynkClient.create(u, p)`
- API data models: `Battery`, `Grid`, `Input`, `Output`, `Inverter`, `Plant`, `pviv.Vip` — all extend `Resource` (provides `__repr__`)
- `EnergyDay` / `EnergyMonth` — parse the 5-minute interval timeseries returned by the plant energy endpoints

`client.__getJSON()` caches daily API responses as JSON files under `inverterData/day-YYYY-MM-DD.json`, skipping the API call if a file already exists (does **not** cache the current day).

The `SunsynkClient` supports an alternate base URL and source string so it can target `https://api.sunsynk.net` (used by `collectdata.py`) vs `https://pv.inteless.com` (default/library).

### 2. Energy analysis tool (`collectdata.py` + `sunsynk/calculations.py`)

`collectdata.py` is a standalone CLI script that iterates over all historical months/days, calculates cost and savings, and prints a financial analysis of the solar/battery installation.

`sunsynk/calculations.py` contains the analysis engine:

- `PriceData` — holds the current energy tariff rates (off-peak rate, peak rate, export rate, standing charge, compare rate, off-peak window). These values are hardcoded as defaults but are overridden per-period by `EnergyPrices.checkDate()`.
- `Battery` (in `calculations.py`, not `battery.py`) — simulates battery charge/discharge over historical 5-minute intervals, tracking kWh drawn, charged, and PV-charged to compute potential savings.
- `EnergySummary` — accumulates per-day data for a single tariff period; supports `newMonth()` to compound interest on cumulative savings.
- `EnergySummaryAggregator` — holds a list of `EnergySummary` objects (one per tariff period) and computes cross-period grand totals.
- `EnergyPrices` — top-level orchestrator; reads `_EnergyPrices.json` for tariff history, calls `checkDate()` on each day to switch tariff periods, and exposes `print_*` methods for the final report.

**Name collision:** `sunsynk/battery.py` is the API response model (realtime battery state). `sunsynk/calculations.py` also defines a `Battery` class which is the simulation model. `collectdata.py` imports the calculations one explicitly.

### Energy price data files (`inverterData/_EnergyPrices*.json`)

JSON files defining tariff periods with fields: `datefrom`, `dateto`, `offpeakRate`, `offpeakStart`, `offpeakStop`, `peakRate`, `exportRate`, `standingCharge`, `CompareRate`, `CompareStandingCharge`, `InterestRate`.

## Tests

Tests use `pytest-asyncio` and `pytest-aiohttp`. The `MockApiServer` in `tests/mock_api_server.py` spins up a local aiohttp server to simulate the Sunsynk API, eliminating real network calls. Tests are all async and use `aiohttp_client` + `event_loop` fixtures.
