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
        'pv', 'pv_clip_loss', 'ev_peak', 'ev_off_peak', 'ev_from_pv',
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

    def monthly_daily_avg(self, key: str) -> dict:
        """{month index 0-11: average kWh/day} for every calendar month seen."""
        return {m: self._kwh[m][key] / self._days[m] for m in range(12) if self._days[m]}

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


EV_KEYS = ('ev_peak', 'ev_off_peak', 'ev_from_pv')


def rebased_annual_ev(before: SeasonalProjection, after: SeasonalProjection):
    """A year of EV charging at the level seen since a change in use (e.g. a second car).

    Calendar months seen since the change (``after``) count as measured. A month seen only
    before it is scaled by ``ratio``: the after/before kWh ratio over the calendar months both
    periods share, so this year's growth carries over, together with last year's seasonal
    shape. The peak/off-peak/solar split follows the after period, i.e. current charging habits.
    Returns ``({key: annual kWh}, ratio, measured_months)``. Without overlapping months the
    ratio is 1, and without any after-period data the result is the plain seasonal year.
    """
    def total(proj):
        peak, off = proj.monthly_daily_avg('ev_peak'), proj.monthly_daily_avg('ev_off_peak')
        return {m: peak.get(m, 0.0) + off.get(m, 0.0) for m in set(peak) | set(off)}

    b, a = total(before), total(after)
    if not a:
        return {k: before.annual_kwh(k) for k in EV_KEYS}, 1.0, 0
    shared = [m for m in a if m in b]
    b_shared = sum(b[m] * DAYS_IN_MONTH[m] for m in shared)
    ratio = sum(a[m] * DAYS_IN_MONTH[m] for m in shared) / b_shared if b_shared > 0 else 1.0
    year = sum((a[m] if m in a else b.get(m, 0.0) * ratio) * DAYS_IN_MONTH[m] for m in range(12))
    if len(a) + sum(1 for m in b if m not in a) < 12:     # months never seen: average the rest
        seen = set(a) | set(b)
        year = year * 365 / sum(DAYS_IN_MONTH[m] for m in seen)
    split = {k: sum(v * DAYS_IN_MONTH[m] for m, v in after.monthly_daily_avg(k).items())
             for k in EV_KEYS}
    after_total = split['ev_peak'] + split['ev_off_peak']
    ev = {k: year * split[k] / after_total if after_total else 0.0 for k in EV_KEYS}
    return ev, ratio, len(a)

