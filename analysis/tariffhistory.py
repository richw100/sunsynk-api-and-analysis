"""Price this house's actual half-hourly import and export at historic tariff rates.

Where tariffcompare.py prices a projected year at today's rates, this prices every day of
real data (the same day loop as collectdata.py: first energyPrices file, baseline PV, EV
charging included) at the rates each tariff actually charged on that date, half-hour by
half-hour. The rates come from the Octopus Kraken API's unit-rate and standing-charge
history (Agile, Intelligent Octopus Go, Go, Flexible, Agile Outgoing, Outgoing), and the
actual tariff from the energyPrices file's period for each date.

Import - each tariff's cost is house energy + EV energy + standing charge, where the EV is:
  as charged  - every half-hour at the tariff's rate, at the times the car actually charged
  re-timed    - the EV energy charged in the actual off-peak window moves to that night's
                cheapest half-hours (22:00-07:00, at most chargerKw x 0.5 h each), as a
                scheduled charger would; on smart-charging tariffs (Intelligent Octopus Go)
                all EV import gets the day's cheapest rate
  Totals use the cheaper EV figure. The actual tariff is always "as charged".
Export - each half-hour of corrected export at each export tariff's rate for that half-hour,
  and the actual export rate (energyPrices) for each date.
Pairs - import + export combinations, an export that `requires` a supplier only with that
  supplier's import, and the actual import only with the actual export.

Usage: python analysis/tariffhistory.py [key:value ...]
  Same keys as collectdata.py (startDate/stopDate limit the days), plus:
  region:X          DNO region letter A-P (default: tariffCompare.region in config.local.json)
  tariffDataDir:DIR cache for fetched tariff data (default tariffData; completed months
                    go in DIR/history/ and are never refetched)
  chargerKw:N       EV charger power for re-timing (default 7.4)
"""
import asyncio
import bisect
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import aiohttp

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis.collectdata import (_load_settings, _make_battery, _iter_energy_days, _load_price_file,
                                  _load_ev_profiles)
from analysis.energy_client import SunsynkEnergyClient
from analysis.energyprices import EnergyPrices
from analysis.pricedata import PriceData
from analysis.tariff_sources import (Fetcher, KRAKEN_HOSTS, LOCAL_TZ, REGION_NAMES, _parse_utc,
                                     tou_slot_rates, window_slots)
from analysis.tariffcompare import load_tariff_settings

# Octopus variable products whose rates keep being published for existing customers, so
# each one has an unbroken history (a fixed product only covers its own sale window).
HISTORY_TARIFFS = [
    {'name': 'Agile Octopus', 'product': 'AGILE-24-10-01', 'direction': 'IMPORT'},
    {'name': 'Intelligent Octopus Go', 'product': 'INTELLI-VAR-24-10-29', 'direction': 'IMPORT',
     'smartEv': True},
    {'name': 'Octopus Go', 'product': 'GO-VAR-22-10-14', 'direction': 'IMPORT'},
    {'name': 'Flexible Octopus (~price cap)', 'product': 'VAR-22-11-01', 'direction': 'IMPORT'},
    {'name': 'Agile Outgoing', 'product': 'AGILE-OUTGOING-19-05-13', 'direction': 'EXPORT',
     'requires': 'Octopus'},
    {'name': 'Outgoing Octopus', 'product': 'OUTGOING-VAR-24-10-26', 'direction': 'EXPORT',
     'requires': 'Octopus'},
]
SUPPLIER = 'Octopus'
NIGHT_SLOTS = list(range(44, 48)) + list(range(0, 14))     # 22:00-07:00, where re-timed EV can go
DEFAULT_CHARGER_KW = 7.4


# ---------------------------------------------------------------- rate timelines

class Timeline:
    """Kraken rate periods (UTC valid_from/valid_to, None = open-ended) for lookup by instant.
    Non-Direct-Debit rows are dropped; where periods overlap the latest-starting one wins."""

    def __init__(self, rows: list):
        far_past = datetime.min.replace(tzinfo=timezone.utc)
        far_future = datetime.max.replace(tzinfo=timezone.utc)
        periods = []
        for r in rows:
            if r.get('payment_method') not in (None, 'DIRECT_DEBIT'):
                continue
            start = _parse_utc(r['valid_from']) if r.get('valid_from') else far_past
            stop = _parse_utc(r['valid_to']) if r.get('valid_to') else far_future
            periods.append((start, stop, float(r['value_inc_vat'])))
        periods.sort(key=lambda p: p[0])
        self._starts = [p[0] for p in periods]
        self._periods = periods

    def __len__(self):
        return len(self._periods)

    def at(self, instant: datetime):
        """The rate in force at `instant` (UTC), or None."""
        i = bisect.bisect_right(self._starts, instant) - 1
        while i >= 0:
            start, stop, value = self._periods[i]
            if start <= instant < stop:
                return value
            i -= 1
        return None


def slot_instants(day: date) -> list:
    """UTC start instant of each local (Europe/London) half-hour slot of `day`."""
    return [datetime(day.year, day.month, day.day, k // 2, 30 * (k % 2), tzinfo=LOCAL_TZ)
            .astimezone(timezone.utc) for k in range(48)]


def day_rates(timeline: Timeline, instants: list):
    """48 p/kWh for one day; gaps filled with the day's mean, None if the day has no rates."""
    rates = [timeline.at(t) for t in instants]
    present = [r for r in rates if r is not None]
    if not present:
        return None
    mean = sum(present) / len(present)
    return [mean if r is None else r for r in rates]


def months_between(start: date, stop: date) -> list:
    """First day of every calendar month touching [start, stop]."""
    out, m = [], date(start.year, start.month, 1)
    while m <= stop:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def _next_month(m: date) -> date:
    return date(m.year + (m.month == 12), m.month % 12 + 1, 1)


async def _all_pages(fetcher: Fetcher, url: str, params: dict | None) -> list:
    rows = []
    while url:
        page = await fetcher.get_json(url, params)
        rows += page.get('results', [])
        url, params = page.get('next'), None
    return rows


async def fetch_history(hist: Fetcher, daily: Fetcher, region: str, entries: list,  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
                        start: date, stop: date, today: date) -> list:
    """Unit-rate and standing-charge timelines for each HISTORY_TARIFFS-style entry over
    [start, stop]. Months that ended before `today` are read through `hist` (a permanent
    cache); the current month through `daily` (today's cache)."""
    host = KRAKEN_HOSTS['octopus']
    out = []
    for entry in entries:
        code = entry['product']
        try:
            product = await hist.get_json(f'{host}/v1/products/{code}/')
            by_payment = product['single_register_electricity_tariffs'][f'_{region}']
            tariff = by_payment.get('direct_debit_monthly') or by_payment.get('varying') \
                or next(iter(by_payment.values()))
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f'  [skip] {code}: {e}')
            continue
        base = f"{host}/v1/products/{code}/electricity-tariffs/{tariff['code']}"
        unit_rows = []
        for i, m in enumerate(months_between(start, stop)):
            frm = m - timedelta(days=1) if i == 0 else m     # local midnight in BST is 23:00 UTC
            fetcher = hist if _next_month(m) <= today else daily
            unit_rows += await _all_pages(fetcher, f'{base}/standard-unit-rates/', {
                'page_size': 1500, 'period_from': f'{frm}T00:00:00Z',
                'period_to': f'{_next_month(m)}T00:00:00Z'})
        standing_rows = await _all_pages(daily, f'{base}/standing-charges/', {
            'page_size': 1500, 'period_from': f'{start - timedelta(days=1)}T00:00:00Z'})
        out.append({
            'name': entry['name'], 'code': code, 'supplier': entry.get('supplier', SUPPLIER),
            'direction': entry.get('direction', 'IMPORT'), 'smart_ev': bool(entry.get('smartEv')),
            'requires': entry.get('requires'),
            'unit': Timeline(unit_rows), 'standing': Timeline(standing_rows),
        })
    return out


# ---------------------------------------------------------------- the actual tariff

def actual_period(prices: dict, day: str):
    """The energyPrices period covering `day` (first match, as EnergyPrices.check_date)."""
    for item in prices['data']['prices']:
        if item['datefrom'] <= day <= item['dateto']:
            return item
    return None


def actual_day(prices: dict, day: str):
    """(import slot rates p/kWh, off-peak slot set, standing p/day, export p/kWh) or None."""
    item = actual_period(prices, day)
    if item is None:
        return None
    window = f"{item['offpeakStart']}-{item['offpeakStop']}"
    return (tou_slot_rates(float(item['peakRate']) * 100, float(item['offpeakRate']) * 100, window),
            window_slots(window), float(item['standingCharge']) * 100, float(item['exportRate']) * 100)


# ---------------------------------------------------------------- pricing one day

def retime_ev(ev_kwh: list, rates: list, off_slots: set, slot_cap_kwh: float) -> float:
    """p for the day's EV energy with what was charged in `off_slots` moved into the
    cheapest NIGHT_SLOTS (at most `slot_cap_kwh` each); the rest stays at its own rate."""
    cost = sum(ev_kwh[k] * rates[k] for k in range(48) if k not in off_slots)
    remaining = sum(ev_kwh[k] for k in off_slots)
    night = sorted(NIGHT_SLOTS, key=lambda k: rates[k])
    for k in night:
        if remaining <= 0:
            break
        take = min(slot_cap_kwh, remaining)
        cost += take * rates[k]
        remaining -= take
    if remaining > 0:
        cost += remaining * rates[night[-1]]
    return cost


def price_import_day(rates: list, house: list, ev: list, off_slots: set, smart_ev: bool,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                     slot_cap_kwh: float, actual: bool = False) -> dict:
    """£ for one day's house and EV import (kWh per slot) at 48 slot rates (p/kWh)."""
    house_p = sum(h * r for h, r in zip(house, rates))
    ev_now = sum(e * r for e, r in zip(ev, rates))
    if actual:
        ev_best = ev_now
    elif smart_ev:
        ev_best = sum(ev) * min(rates)
    else:
        ev_best = retime_ev(ev, rates, off_slots, slot_cap_kwh)
    return {'house': house_p / 100, 'ev_now': ev_now / 100, 'ev': min(ev_now, ev_best) / 100}


# ---------------------------------------------------------------- the whole history

def _blank():
    return defaultdict(float)


def price_history(days: dict, prices: dict, tariffs: list, charger_kw: float = DEFAULT_CHARGER_KW) -> dict:  # pylint: disable=too-many-locals
    """Price every day of `days` ({date: (house Wh[48], ev Wh[48], export Wh[48])}) at the
    actual tariff and each history tariff. Returns
    {'imports': {name: {month: {house, ev_now, ev, standing, kwh, days}}},
     'exports': {name: {month: {income, kwh, days}}}, 'skipped': {name: n_days},
     'meta': {name: tariff}, 'kwh': {month: {house, ev, export, days}}}.
    A day is only counted when every tariff has rates for it, so all columns cover the same days."""
    slot_cap = charger_kw / 2
    imports, exports = defaultdict(lambda: defaultdict(_blank)), defaultdict(lambda: defaultdict(_blank))
    kwh, skipped = defaultdict(_blank), defaultdict(int)
    meta = {'Actual': {'name': 'Actual', 'supplier': 'Actual', 'direction': 'IMPORT', 'requires': None},
            'Actual export': {'name': 'Actual export', 'supplier': 'Actual', 'direction': 'EXPORT',
                              'requires': None}}
    meta.update({t['name']: t for t in tariffs})
    for day in sorted(days):
        house_wh, ev_wh, export_wh = days[day]
        house = [h / 1000 for h in house_wh]
        ev = [e / 1000 for e in ev_wh]
        export = [x / 1000 for x in export_wh]
        act = actual_day(prices, day)
        if act is None:
            skipped['Actual'] += 1
            continue
        act_rates, off_slots, act_standing, act_export = act
        instants = slot_instants(date.fromisoformat(day))
        day_tariffs = []
        for t in tariffs:
            rates = day_rates(t['unit'], instants)
            standing = t['standing'].at(instants[24]) if len(t['standing']) else 0.0
            if rates is None or standing is None:
                skipped[t['name']] += 1
                break
            day_tariffs.append((t, rates, standing))
        else:
            month = day[:7]
            k = kwh[month]
            k['house'] += sum(house)
            k['ev'] += sum(ev)
            k['export'] += sum(export)
            k['days'] += 1
            _add_import(imports['Actual'][month],
                        price_import_day(act_rates, house, ev, off_slots, False, slot_cap, actual=True),
                        act_standing, sum(house) + sum(ev))
            _add_export(exports['Actual export'][month], sum(export) * act_export / 100, sum(export))
            for t, rates, standing in day_tariffs:
                if t['direction'] == 'EXPORT':
                    _add_export(exports[t['name']][month],
                                sum(x * r for x, r in zip(export, rates)) / 100, sum(export))
                else:
                    _add_import(imports[t['name']][month],
                                price_import_day(rates, house, ev, off_slots, t['smart_ev'], slot_cap),
                                standing, sum(house) + sum(ev))
    return {'imports': imports, 'exports': exports, 'skipped': dict(skipped), 'meta': meta, 'kwh': kwh}


def _add_import(acc: dict, day: dict, standing_p: float, kwh_total: float):
    acc['house'] += day['house']
    acc['ev_now'] += day['ev_now']
    acc['ev'] += day['ev']
    acc['standing'] += standing_p / 100
    acc['kwh'] += kwh_total
    acc['days'] += 1


def _add_export(acc: dict, income: float, kwh_total: float):
    acc['income'] += income
    acc['kwh'] += kwh_total
    acc['days'] += 1


def import_total(m: dict) -> float:
    """£ house + EV (re-timed) + standing for one accumulator."""
    return m['house'] + m['ev'] + m['standing']


def totals(by_month: dict, months: list) -> dict:
    """Sum one tariff's monthly accumulators over `months`."""
    out = _blank()
    for month in months:
        for key, value in by_month.get(month, {}).items():
            out[key] += value
    return out


def best_pairs(result: dict, months: list) -> list:
    """[(net £, import name, export name, import £, export £)] cheapest first, honouring
    `requires` and keeping the actual import with the actual export only."""
    meta, pairs = result['meta'], []
    for imp, by_month in result['imports'].items():
        imp_total = import_total(totals(by_month, months))
        for exp, ex_month in result['exports'].items():
            req = meta[exp].get('requires')
            if req and req != meta[imp].get('supplier'):
                continue
            if imp == 'Actual' and exp != 'Actual export':
                continue
            income = totals(ex_month, months)['income']
            pairs.append((imp_total - income, imp, exp, imp_total, income))
    return sorted(pairs)


# ---------------------------------------------------------------- report

def _months(result: dict) -> list:
    return sorted(result['kwh'])


def last_12_months(months: list) -> list:
    """The newest 12 months with data, dropping a partial current month."""
    full = [m for m in months if m < date.today().strftime('%Y-%m')]
    return full[-12:]


def print_report(result: dict, region: str):  # pylint: disable=too-many-locals,too-many-statements
    """Monthly import/export tables, then totals and pairs for all data and the last 12 months."""
    months = _months(result)
    if not months:
        print('No days could be priced.')
        return
    imports, exports = list(result['imports']), list(result['exports'])
    days = sum(result['kwh'][m]['days'] for m in months)
    print(f"\nHISTORIC TARIFF COMPARISON - region {region} ({REGION_NAMES.get(region, ['?'])[0]}), "
          f"{months[0]} to {months[-1]}, {days:.0f} days")
    if result['skipped']:
        print('  days left out (no rates): ' + ', '.join(f'{k} {v}' for k, v in result['skipped'].items()))
    width = max(12, max(len(n) for n in imports + exports) + 1)
    width = min(width, 16)

    print('\nIMPORT COST BY MONTH (£, house + EV + standing; EV re-timed, actual as charged)')
    print(f"{'Month':8} {'house':>6} {'EV':>6} " + ''.join(f'{n[:width - 1]:>{width}}' for n in imports))
    for m in months:
        k = result['kwh'][m]
        print(f"{m:8} {k['house']:6.0f} {k['ev']:6.0f} " +
              ''.join(f"{import_total(result['imports'][n].get(m, _blank())):{width}.2f}" for n in imports))

    print('\nEXPORT INCOME BY MONTH (£, and achieved p/kWh)')
    print(f"{'Month':8} {'kWh':>6} " + ''.join(f'{n[:width - 1]:>{width}}' for n in exports))
    for m in months:
        cells = []
        for n in exports:
            e = result['exports'][n].get(m, _blank())
            rate = 100 * e['income'] / e['kwh'] if e['kwh'] else 0.0
            cells.append(f"{e['income']:7.2f} {rate:5.2f}p".rjust(width))
        print(f"{m:8} {result['kwh'][m]['export']:6.0f} " + ''.join(cells))

    for label, span in (('ALL DATA', months), ('LAST 12 FULL MONTHS', last_12_months(months))):
        if not span:
            continue
        print(f'\n{label} ({span[0]} to {span[-1]})')
        print(f"  {'Import tariff':32} {'house':>8} {'EV':>8} {'EV now':>8} {'standing':>8} {'total':>9} {'p/kWh':>6}")
        rows = sorted(((totals(result['imports'][n], span), n) for n in imports),
                      key=lambda r: import_total(r[0]))
        for t, n in rows:
            p = 100 * (t['house'] + t['ev']) / t['kwh'] if t['kwh'] else 0.0
            print(f"  {n[:32]:32} {t['house']:8.2f} {t['ev']:8.2f} {t['ev_now']:8.2f} {t['standing']:8.2f} "
                  f"{import_total(t):9.2f} {p:6.2f}")
        print(f"  {'Export tariff':32} {'kWh':>8} {'income':>9} {'p/kWh':>6}")
        ex_totals = {n: totals(result['exports'][n], span) for n in exports}
        for n in sorted(exports, key=lambda n, ex=ex_totals: -ex[n]['income']):
            t = ex_totals[n]
            print(f"  {n[:32]:32} {t['kwh']:8.0f} {t['income']:9.2f} {100 * t['income'] / t['kwh'] if t['kwh'] else 0:6.2f}")
        print(f"  {'Pair (import + export)':56} {'import':>9} {'export':>9} {'net':>9}")
        for net, imp, exp, imp_total, income in best_pairs(result, span):
            print(f"  {(imp + ' + ' + exp)[:56]:56} {imp_total:9.2f} {-income:9.2f} {net:9.2f}")


def write_csv(result: dict, path: str):
    """One row per month x tariff (both directions)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['month', 'direction', 'tariff', 'days', 'kwh', 'house', 'ev', 'ev_as_charged',
                    'standing', 'total_or_income'])
        for m in _months(result):
            for n, by_month in result['imports'].items():
                t = by_month.get(m, _blank())
                w.writerow([m, 'IMPORT', n, int(t['days']), round(t['kwh'], 3), round(t['house'], 2),
                            round(t['ev'], 2), round(t['ev_now'], 2), round(t['standing'], 2),
                            round(import_total(t), 2)])
            for n, by_month in result['exports'].items():
                t = by_month.get(m, _blank())
                w.writerow([m, 'EXPORT', n, int(t['days']), round(t['kwh'], 3), '', '', '', '',
                            round(t['income'], 2)])


# ---------------------------------------------------------------- CLI

def history_settings(cfg: dict, argv: list) -> dict:
    """Tariffs to compare (tariffCompare.historyTariffs, else HISTORY_TARIFFS) and chargerKw."""
    out = {'tariffs': cfg.get('historyTariffs') or HISTORY_TARIFFS, 'chargerKw': DEFAULT_CHARGER_KW}
    for arg in argv:
        key, _, value = arg.partition(':')
        if key.lower() == 'chargerkw':
            out['chargerKw'] = float(value)
    return out


async def main():  # pylint: disable=too-many-locals
    """Load the days, fetch the rate history, print and save the comparison."""
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        return
    settings = _load_settings(sys.argv[1:])
    cfg = load_tariff_settings(settings, sys.argv[1:])
    hist_cfg = history_settings(cfg, sys.argv[1:])
    region = cfg['region'].upper().lstrip('_')
    if region not in REGION_NAMES:
        print('Set region:X (or tariffCompare.region in config.local.json) to your DNO region letter A-P')
        return
    show_days = bool(re.match('^on', str(settings['showDays']), re.IGNORECASE))
    raw_files = settings['energyPrices']
    price_file = (raw_files if isinstance(raw_files, list) else [raw_files])[0]
    vb_raw = settings['virtualBattery']
    battery_config = (vb_raw if isinstance(vb_raw, list) else [vb_raw])[0]
    boost = settings['pvBoost'][0]
    energy_prices_data = _load_price_file(price_file)
    prices = EnergyPrices(
        energy_prices_data, _make_battery(battery_config, PriceData()),
        original_price=float(settings['originalPrice']), label='history',
        off_peak_baseline_kwh=float(settings['offPeakBaseline']), battery_enabled=False,
        ev_basis_from=str(settings['evBasisFrom']),
    )
    ev_profiles = _load_ev_profiles(settings)

    days = {}
    async with SunsynkEnergyClient(os.getenv('SUNSYNK_USERNAME'), os.getenv('SUNSYNK_PASSWORD'),
                                   "https://api.sunsynk.net") as client:
        inverter = (await client.get_inverters())[0]
        async for date_str, energyday, _ in _iter_energy_days(
                client, inverter, prices, boost['pv_extra_ratio'], boost['inverterClipW'],
                str(settings['startDate']), str(settings['stopDate']), int(settings['scanFromYear']),
                show_days, {}, {}, {}, {'hits': 0, 'misses': 0},
                settings['exportCorrection'], ev_profiles):
            total, ev = energyday.import_slots()
            days[date_str] = ([t - e for t, e in zip(total, ev)], ev, energyday.export_slots())
    if not days:
        print('No days of data.')
        return

    today = date.today()
    start, stop = date.fromisoformat(min(days)), date.fromisoformat(max(days))
    print(f"Fetching rate history for region {region}, {start} to {stop} ...")
    async with aiohttp.ClientSession() as session:
        hist = Fetcher(session, os.path.join(cfg['dataDir'], 'history'))
        daily = Fetcher(session, os.path.join(cfg['dataDir'], today.isoformat()))
        tariffs = await fetch_history(hist, daily, region, hist_cfg['tariffs'], start, stop, today)
    print(f"  {len(tariffs)} tariffs ({hist.calls + daily.calls} requests, {hist.hits + daily.hits} from cache)")

    result = price_history(days, energy_prices_data, tariffs, hist_cfg['chargerKw'])
    print_report(result, region)
    path = os.path.join(PROJECT_ROOT, 'results',
                        f"_{datetime.now().strftime('%Y%m%d-%H%M%S')}-tariffhistory-{region}.csv")
    write_csv(result, path)
    print(f'\nCSV: {path}')


if __name__ == '__main__':
    asyncio.run(main())
