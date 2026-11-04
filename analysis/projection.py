"""Seasonally weighted annual projection, priced at the latest tariff rates.

Annualising as total × 365/days is biased twice over: by which months the data covers
(a summer day saves ~3× a winter one, and months recorded in two years weigh double),
and by pricing old days at old tariffs (the SEG rate has since dropped). Instead,
SeasonalProjection keeps kWh totals per calendar month. annual_kwh() averages each
calendar month over every year it was seen and weights it by the month's length;
annual_money() then prices those kWh at a single rate set — normally latest_rates().
Until all 12 months have been seen it falls back to the plain ×365/days average
(`seasonal` is False), which callers should flag as provisional.
"""
from datetime import date

DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

# Per-day kWh recorded for each date.
#   batt_grid_charge is grid energy bought (charge_amount / charge_efficiency);
#   batt_pv_diverted is raw PV surplus sent to the battery instead of exported.
KEYS = ('load_peak', 'load_off_peak', 'import_peak', 'import_off_peak', 'export',
        'pv', 'pv_clip_loss',
        'batt_drawn', 'batt_exported', 'batt_grid_charge', 'batt_pv_diverted')


def latest_rates(prices: dict, today: str | None = None) -> dict:
    """The newest tariff period that has started by `today` (default: real today);
    if none has started, the newest period in the file."""
    today = today or date.today().isoformat()
    items = prices['data']['prices']
    started = [p for p in items if p['datefrom'] <= today]
    item = max(started or items, key=lambda p: p['datefrom'])
    return {
        'peak': float(item['peakRate']),
        'off_peak': float(item['offpeakRate']),
        'export': float(item['exportRate']),
        'standing': float(item.get('standingCharge', 0.0)),
        'datefrom': item['datefrom'],
    }


class SeasonalProjection:
    def __init__(self):
        self._kwh: list[dict[str, float]] = [{key: 0.0 for key in KEYS} for _ in range(12)]
        self._days = [0] * 12

    def add_day(self, date_str: str, kwh: dict):
        m = int(date_str[5:7]) - 1
        self._days[m] += 1
        for key, value in kwh.items():
            self._kwh[m][key] += value

    @property
    def days(self) -> int:
        return sum(self._days)

    @property
    def months_covered(self) -> int:
        return sum(1 for d in self._days if d)

    @property
    def seasonal(self) -> bool:
        return self.months_covered == 12

    def annual_kwh(self, key: str) -> float:
        if self.seasonal:
            return sum(self._kwh[m][key] / self._days[m] * DAYS_IN_MONTH[m] for m in range(12))
        if self.days == 0:
            return 0.0
        return 365 * sum(month[key] for month in self._kwh) / self.days

    def annual_money(self, rates: dict) -> dict:
        """Annual £: solar self-consumption, export (SEG), their sum, and the virtual
        battery's net saving (same formula as VirtualBattery.get_savings)."""
        a = self.annual_kwh
        self_consumption = ((a('load_peak') - a('import_peak')) * rates['peak']
                            + (a('load_off_peak') - a('import_off_peak')) * rates['off_peak'])
        export = a('export') * rates['export']
        battery = (a('batt_drawn') * rates['peak'] + a('batt_exported') * rates['export']
                   - a('batt_grid_charge') * rates['off_peak']
                   - a('batt_pv_diverted') * rates['export'])
        return {
            'self_consumption': self_consumption,
            'export': export,
            'solar': self_consumption + export,
            'battery': battery,
        }
