import pytest

from sunsynk.client import SunsynkClient, InvalidCredentialsException
from analysis.energy_client import SunsynkEnergyClient
from analysis.energyday import EnergyDay
from analysis.energymonth import EnergyMonth
from analysis.calculations import VirtualBattery, PriceData, QueryType
from tests.mock_api_server import MockApiServer


@pytest.mark.asyncio
async def test_get_energy_month(aiohttp_client):
    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()

    month = await client.get_energy_month(12345, '2025-06-01')

    assert isinstance(month, EnergyMonth)
    assert month.get_Load()['records'][0]['time'] == '2025-06-10'
    assert float(month.get_Load()['records'][0]['value']) == 5.2
    assert float(month.get_PV()['records'][0]['value']) == 8.3
    assert float(month.get_Export()['records'][0]['value']) == 2.1
    assert float(month.get_Import()['records'][0]['value']) == 1.4


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

    # Import by query type
    assert round(day.getCalcImport(), 1) == 95.0
    assert round(day.getCalcImport(QueryType.PEAK), 1) == 50.0
    assert round(day.getCalcImport(QueryType.OFFPEAK), 1) == 45.0
    assert round(day.getCalcImportPeak(), 1) == 50.0
    assert round(day.getCalcImportOffPeak(), 1) == 45.0

    # Export (all zero — no negative Grid values in this day)
    assert day.getCalcExport() == 0
    assert day.getCalcExport(QueryType.PEAK) == 0
    assert day.getCalcExport(QueryType.OFFPEAK) == 0
    assert day.getCalcExportPeak() == 0
    assert day.getCalcExportOffPeak() == 0

    # PV
    assert round(day.getCalcPV(), 1) == 200.0
    assert round(day.getCalcPVPeak(), 1) == 200.0
    assert day.getCalcPVOffPeak() == 0.0

    # Load
    assert round(day.getCalcLoad(), 1) == 95.0
    assert round(day.getCalcLoadPeak(), 1) == 50.0
    assert round(day.getCalcLoadOffPeak(), 1) == 45.0

    # Supplied totals from the month summary
    assert day.getSuppliedLoad() == 5.2
    assert day.getSuppliedImport() == 1.4
    assert day.getSuppliedPV() == 8.3
    assert day.getSuppliedExport() == 2.1


@pytest.mark.asyncio
async def test_get_energy_day_with_peak_export(aiohttp_client, tmp_path, monkeypatch):
    # 2025-06-11 mock returns Grid = -600 W at 08:00 (peak export).
    # With offpeak 00:00-06:00:
    #   value = -600/12 = -50 Wh → extra_load=2.3, peakexport=47.7
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()

    month = await client.get_energy_month(12345, '2025-06-01')
    battery = VirtualBattery(PriceData())
    day = await client.get_energy_day(12345, '2025-06-11', month, battery, '00:00', '06:00')

    assert isinstance(day, EnergyDay)
    assert round(day.getCalcExport(), 2) == round(47.7, 2)
    assert round(day.getCalcExport(QueryType.PEAK), 2) == round(47.7, 2)
    assert day.getCalcExport(QueryType.OFFPEAK) == 0
    assert day.getCalcExportPeak() == pytest.approx(47.7, abs=0.1)


@pytest.mark.asyncio
async def test_get_energy_day_battery_runs_out(aiohttp_client, tmp_path, monkeypatch):
    # With a 30 Wh battery and a 600 W peak Grid record (value=50 Wh),
    # the battery is exhausted: 50 > 30/0.92=32.6 → setRanOut() is called.
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()

    month = await client.get_energy_month(12345, '2025-06-01')
    tiny_battery = VirtualBattery(PriceData(), batterySize=30)
    await client.get_energy_day(12345, '2025-06-10', month, tiny_battery, '00:00', '06:00')

    assert tiny_battery.daysRunOut == 1


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
    assert round(day2.getCalcImport(), 1) == 95.0
