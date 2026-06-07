import pytest

from sunsynk.client import SunsynkClient, InvalidCredentialsException
from analysis.energy_client import SunsynkEnergyClient
from analysis.energyday import EnergyDay
from analysis.energymonth import EnergyMonth
from analysis.energysummary import QueryType
from analysis.pricedata import PriceData
from analysis.virtualbattery import VirtualBattery
from tests.mock_api_server import MockApiServer


@pytest.mark.asyncio
async def test_get_energy_month(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()

    month = await client.get_energy_month(12345, '2025-06-01')

    assert isinstance(month, EnergyMonth)
    assert month.get_load()['records'][0]['time'] == '2025-06-10'
    assert float(month.get_load()['records'][0]['value']) == 22.9
    assert float(month.get_pv()['records'][0]['value']) == 19.6
    assert float(month.get_export()['records'][0]['value']) == 10.1
    assert float(month.get_import()['records'][0]['value']) == 1.9


@pytest.mark.asyncio
async def test_get_energy_month_uses_cache(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()

    # First call fetches from the mock server and writes a cache file
    await client.get_energy_month(12345, '2025-06-01')
    assert (tmp_path / 'inverterData' / 'month-2025-06.json').exists()

    # Second call should read from the cache file, not the server
    month2 = await client.get_energy_month(12345, '2025-06-01')
    assert isinstance(month2, EnergyMonth)
    assert float(month2.get_load()['records'][0]['value']) == 22.9


@pytest.mark.asyncio
async def test_get_energy_day(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()

    month = await client.get_energy_month(12345, '2025-06-01')
    battery = VirtualBattery(PriceData())
    day = await client.get_energy_day(12345, '2025-06-10', month, battery, '00:00', '06:00')

    assert isinstance(day, EnergyDay)

    # Import — sunny day, offpeak 00:00-05:00 (Grid 787–798 W), peak exports 08:00–16:00
    # offpeak = 7344/12 = 612.0 Wh; peak = 749.75 - 9*2.3 ≈ 729.05 Wh
    assert round(day.get_calc_import(), 1) == 1341.1
    assert round(day.get_calc_import(QueryType.PEAK), 1) == 729.1
    assert round(day.get_calc_import(QueryType.OFFPEAK), 1) == 612.0
    assert round(day.get_calc_import_peak(), 1) == 729.1
    assert round(day.get_calc_import_off_peak(), 1) == 612.0

    # Export — peak export from PV surplus 08:00–16:00; peakexport = 721.2 Wh
    assert round(day.get_calc_export(), 1) == 721.2
    assert round(day.get_calc_export(QueryType.PEAK), 1) == 721.2
    assert day.get_calc_export(QueryType.OFFPEAK) == 0
    assert round(day.get_calc_export_peak(), 1) == 721.2
    assert day.get_calc_export_off_peak() == 0

    # PV — 18880/12 = 1573.3 Wh (all in peak, no PV before 06:00)
    assert round(day.get_calc_pv(), 1) == 1573.3
    assert round(day.get_calc_pv_peak(), 1) == 1573.3
    assert day.get_calc_pv_off_peak() == 0.0

    # Load — offpeak 612.0 Wh; peak 19025/12 = 1585.4 Wh
    assert round(day.get_calc_load(), 1) == 2197.4
    assert round(day.get_calc_load_peak(), 1) == 1585.4
    assert round(day.get_calc_load_off_peak(), 1) == 612.0

    # Supplied totals from the month summary (daily kWh from real API)
    assert day.get_supplied_load() == 22.9
    assert day.get_supplied_import() == 1.9
    assert day.get_supplied_pv() == 19.6
    assert day.get_supplied_export() == 10.1


@pytest.mark.asyncio
async def test_get_energy_day_with_peak_export(aiohttp_client, tmp_path, monkeypatch):
    # 2025-06-11: cloudy day with PV surplus exported to grid 07:00-15:00.
    # 9 negative Grid records; all have |value|/12 > 2.3 → extra_load=2.3 each.
    # peakexport = 5*(82/12-2.3) + (300/12-2.3) + (480/12-2.3) + (384/12-2.3) + (304/12-2.3) = 135.8 Wh
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()

    month = await client.get_energy_month(12345, '2025-06-01')
    battery = VirtualBattery(PriceData())
    day = await client.get_energy_day(12345, '2025-06-11', month, battery, '00:00', '06:00')

    assert isinstance(day, EnergyDay)
    assert day.get_calc_export() == pytest.approx(135.8, abs=0.1)
    assert day.get_calc_export(QueryType.PEAK) == pytest.approx(135.8, abs=0.1)
    assert day.get_calc_export(QueryType.OFFPEAK) == 0
    assert day.get_calc_export_peak() == pytest.approx(135.8, abs=0.1)

    # Import: offpeak 968/12=80.7 Wh, peak 2799/12-9*2.3≈212.6 Wh
    assert round(day.get_calc_import(), 1) == 293.2
    assert round(day.get_calc_import(QueryType.PEAK), 1) == 212.6
    assert round(day.get_calc_import(QueryType.OFFPEAK), 1) == 80.7

    # PV: 5312/12 = 442.7 Wh (all peak, no PV before 07:00)
    assert round(day.get_calc_pv(), 1) == 442.7

    # Load: offpeak 80.7 Wh, peak 6270/12=522.5 Wh
    assert round(day.get_calc_load(), 1) == 603.2


@pytest.mark.asyncio
async def test_get_energy_day_battery_runs_out(aiohttp_client, tmp_path, monkeypatch):
    # With a 30 Wh battery and a 765 W peak Grid record at 06:00 (value=63.75 Wh),
    # the battery is exhausted: 63.75 > 30/0.92=32.6 → setRanOut() is called.
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()

    month = await client.get_energy_month(12345, '2025-06-01')
    tiny_battery = VirtualBattery(PriceData(), battery_size=30)
    await client.get_energy_day(12345, '2025-06-10', month, tiny_battery, '00:00', '06:00')

    assert tiny_battery.days_run_out == 1


@pytest.mark.asyncio
async def test_get_energy_day_uses_cache(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()
    month = await client.get_energy_month(12345, '2025-06-01')

    # First call fetches from the mock server and writes a cache file
    battery = VirtualBattery(PriceData())
    await client.get_energy_day(12345, '2025-06-10', month, battery, '00:00', '06:00')
    assert (tmp_path / 'inverterData' / 'day-2025-06-10.json').exists()

    # Second call should read from the cache file, not the server
    battery2 = VirtualBattery(PriceData())
    day2 = await client.get_energy_day(12345, '2025-06-10', month, battery2, '00:00', '06:00')
    assert round(day2.get_calc_import(), 1) == 1341.1
