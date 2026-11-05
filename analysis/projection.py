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



class SlotProjection:
    """A seasonally weighted year of grid import and export per local half-hour (48 slots).

    Per calendar month it sums house import (total less the EV's share), EV import and
    export per slot. ``annual_house()`` / ``annual_export()`` weight each month like
    SeasonalProjection.annual_kwh(); ``ev_shape()`` is the EV import's half-hour shape, from
    days on or after ``ev_basis_from`` when given (current charging habits), which callers
    scale to a projected EV year. ``monthly()`` gives an average month of each series.
    """

    def __init__(self, ev_basis_from: str = ''):
        self.ev_basis_from = ev_basis_from or ''
        self._house = [[0.0] * 48 for _ in range(12)]
        self._export = [[0.0] * 48 for _ in range(12)]
        self._days = [0] * 12
        self._ev_all = [0.0] * 48
        self._ev_after = [0.0] * 48
        self._ev_month_all = [0.0] * 12      # EV import kWh per calendar month, every day
        self._ev_month_after = [0.0] * 12    # ... and only days since ev_basis_from
        self._after_days = [0] * 12

    def add_day(self, date_str: str, total_wh: list, ev_wh: list, export_wh: list | None = None):
        m = int(date_str[5:7]) - 1
        self._days[m] += 1
        after = bool(self.ev_basis_from) and date_str >= self.ev_basis_from
        ev_kwh = sum(ev_wh) / 1000
        self._ev_month_all[m] += ev_kwh
        if after:
            self._ev_month_after[m] += ev_kwh
            self._after_days[m] += 1
        for k in range(48):
            self._house[m][k] += (total_wh[k] - ev_wh[k]) / 1000
            self._ev_all[k] += ev_wh[k] / 1000
            if after:
                self._ev_after[k] += ev_wh[k] / 1000
            if export_wh is not None:
                self._export[m][k] += export_wh[k] / 1000

    @property
    def days(self) -> int:
        return sum(self._days)

    @property
    def seasonal(self) -> bool:
        return all(self._days)

    def _annual(self, series: list) -> list:
        if self.seasonal:
            return [sum(series[m][k] / self._days[m] * DAYS_IN_MONTH[m] for m in range(12))
                    for k in range(48)]
        if self.days == 0:
            return [0.0] * 48
        return [365 * sum(month[k] for month in series) / self.days for k in range(48)]

    def annual_house(self) -> list:
        """House import kWh per half-hour slot over a year (same weighting as SeasonalProjection)."""
        return self._annual(self._house)

    def annual_export(self) -> list:
        """Export kWh per half-hour slot over a year."""
        return self._annual(self._export)

    def monthly(self) -> dict:
        """{month index: (house import kWh, export kWh)} for an average such month (seen months only)."""
        return {m: (sum(self._house[m]) / self._days[m] * DAYS_IN_MONTH[m],
                    sum(self._export[m]) / self._days[m] * DAYS_IN_MONTH[m])
                for m in range(12) if self._days[m]}

    def ev_shape(self) -> list:
        """Fraction of EV import in each slot (sums to 1, or all 0 without EV data)."""
        src = self._ev_after if sum(self._ev_after) > 0 else self._ev_all
        total = sum(src)
        return [v / total if total else 0.0 for v in src]

    def monthly_ev(self, ev_year: float) -> dict:
        """{month index: EV kWh} summing to ``ev_year``: months seen since ev_basis_from at
        their measured daily rate, earlier-only months scaled by the after/before ratio over
        the months seen in both (as rebased_annual_ev), x month length."""
        shared = [m for m in range(12) if self._after_days[m] and self._days[m] > self._after_days[m]]
        def all_per_day(m):
            return self._ev_month_all[m] / self._days[m]
        def after_per_day(m):
            return self._ev_month_after[m] / self._after_days[m]
        before = sum(all_per_day(m) for m in shared)
        ratio = sum(after_per_day(m) for m in shared) / before if shared and before > 0 else 1.0
        raw = {m: (after_per_day(m) if self._after_days[m]
                   else all_per_day(m) * (ratio if self.ev_basis_from else 1.0)) * DAYS_IN_MONTH[m]
               for m in range(12) if self._days[m]}
        total = sum(raw.values())
        return {m: v * ev_year / total for m, v in raw.items()} if total > 0 else raw

    def _month_slots(self, series: list) -> list:
        return [[series[m][k] / self._days[m] * DAYS_IN_MONTH[m] if self._days[m] else 0.0
                 for k in range(48)] for m in range(12)]

    def month_slot_house(self) -> list:
        """House import kWh per calendar month x half-hour for an average such month (12 x 48;
        once ``seasonal`` the months sum to annual_house())."""
        return self._month_slots(self._house)

    def month_slot_export(self) -> list:
        """Export kWh per calendar month x half-hour (12 x 48)."""
        return self._month_slots(self._export)

    def month_slot_ev(self, ev_year: float) -> list:
        """EV kWh per calendar month x half-hour: monthly_ev() spread over ev_shape()."""
        monthly = self.monthly_ev(ev_year)
        shape = self.ev_shape()
        return [[monthly.get(m, 0.0) * f for f in shape] for m in range(12)]
