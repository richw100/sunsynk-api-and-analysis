import pytest

from sunsynk.client import SunsynkClient, InvalidCredentialsException
from analysis.energy_client import SunsynkEnergyClient
from analysis.energyday import EnergyDay, EnergyMonth
from analysis.calculations import VirtualBattery, PriceData
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
    assert round(day.getCalcImport(), 1) == 95.0
    assert round(day.getCalcImportOffPeak(), 1) == 45.0
    assert round(day.getCalcImportPeak(), 1) == 50.0
    assert round(day.getCalcPV(), 1) == 200.0
    assert day.getSuppliedLoad() == 5.2
    assert day.getSuppliedImport() == 1.4
    assert day.getSuppliedPV() == 8.3
    assert day.getSuppliedExport() == 2.1


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
