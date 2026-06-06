import json
from datetime import datetime

from sunsynk.client import SunsynkClient
from sunsynk.calculations import VirtualBattery
from sunsynk.energyday import EnergyDay, EnergyMonth


class SunsynkEnergyClient(SunsynkClient):
    """Extends SunsynkClient with energy history methods and local file caching.

    Note: _get_cached calls self._SunsynkClient__get() to reach the parent's
    private HTTP method. This ties us to the parent class being named SunsynkClient.
    """

    def __init__(self, username: str, password: str, base_url: str = None):
        super().__init__(username, password, base_url)
        self._today = datetime.today().strftime('%Y-%m-%d')

    async def get_energy_month(self, plant_id: str, date: str) -> EnergyMonth:
        resp = await self._SunsynkClient__get(
            f'api/v1/plant/energy/{plant_id}/month?lan=en&date={date}&id={plant_id}'
        )
        body = await resp.json()
        return EnergyMonth(body['data'])

    async def get_energy_day(self, plant_id: str, date: str, month: EnergyMonth,
                             battery: VirtualBattery, offpeakstart: str, offpeakstop: str) -> EnergyDay:
        body = await self._get_cached(
            f'api/v1/plant/energy/{plant_id}/day?lan=en&date={date}&id={plant_id}', 'day', date
        )
        return EnergyDay(body['data'], date, month, battery, offpeakstart, offpeakstop)

    async def _get_cached(self, path: str, req_type: str, date: str):
        try:
            with open(f'inverterData/{req_type}-{date}.json') as data_file:
                body = json.load(data_file)
        except Exception:
            resp = await self._SunsynkClient__get(path)
            body = await resp.json()
            if date != self._today:
                with open(f'inverterData/{req_type}-{date}.json', 'w') as file:
                    json.dump(body, file, indent=2)
                    print(f'Write to file: {req_type}-{date}.json')
        return body
