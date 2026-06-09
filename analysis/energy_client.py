import json
from datetime import datetime

from sunsynk.client import SunsynkClient
from analysis.virtualbattery import VirtualBattery
from analysis.energyday import EnergyDay
from analysis.energymonth import EnergyMonth


class SunsynkEnergyClient(SunsynkClient):
    """Extends SunsynkClient with energy history methods and local file caching.

    Note: _get_cached calls self._SunsynkClient__get() to reach the parent's
    private HTTP method. This ties us to the parent class being named SunsynkClient.
    """

    def __init__(self, username: str, password: str, base_url: str = None):
        super().__init__(username, password, base_url)
        self._today = datetime.today().strftime('%Y-%m-%d')

    async def get_energy_month(self, plant_id: str, date: str) -> EnergyMonth:
        return EnergyMonth((await self.get_energy_month_raw(plant_id, date))['data'])

    async def get_energy_month_raw(self, plant_id: str, date: str) -> dict:
        return await self._get_cached(
            f'api/v1/plant/energy/{plant_id}/month?lan=en&date={date}&id={plant_id}',
            'month', date[:7]
        )

    async def get_energy_day(self, plant_id: str, date: str, month: EnergyMonth,
                             battery: VirtualBattery, offpeakstart: str, offpeakstop: str) -> EnergyDay:
        return EnergyDay(
            (await self.get_energy_day_raw(plant_id, date))['data'],
            date, month, battery, offpeakstart, offpeakstop
        )

    async def get_energy_day_raw(self, plant_id: str, date: str) -> dict:
        return await self._get_cached(
            f'api/v1/plant/energy/{plant_id}/day?lan=en&date={date}&id={plant_id}', 'day', date
        )

    async def _get_cached(self, path: str, req_type: str, date: str):
        try:
            with open(f'inverterData/{req_type}-{date}.json') as data_file:
                body = json.load(data_file)
        except Exception:
            resp = await self._SunsynkClient__get(path)
            body = await resp.json()
            if date != self._today[:len(date)]:
                with open(f'inverterData/{req_type}-{date}.json', 'w') as file:
                    json.dump(body, file, indent=2)
                    print(f'Write to file: {req_type}-{date}.json')
        return body
