"""EV vs petrol: home charging against a petrol car doing the same miles at local supermarket prices.

Python mirror of the Android EV tab (analysis/EvSavings.kt + api/FuelPriceSource.kt); run as part
of tariffcompare.py, which feeds it each analysed day's EV import half-hours and solar into the car.

Charging cost per day: EV import half-hours at the energyPrices period's off-peak rate (inside its
window) or peak rate, plus solar into the car at the export rate (the export given up), i.e. the
EV CHARGING cost. Indra days before the first analysed day are priced at the first period's
off-peak rate; evSavings.beforeCharger months (charging the charger never saw, e.g. from supplier
credits) at their own rate; evSavings.credits (one-off £, e.g. a free-miles bonus) come off their
month's charging cost.

Miles = kWh x chargeEfficiency x milesPerKwh; litres = miles / mpg x 4.54609. Petrol p/litre per
day: the mean E10 price of the chosen supermarket forecourts (each held from its last change) on
days at least 2/3 of them are priced, else the DESNZ weekly UK average (gov.uk content API -> the
2018-on CSV) plus the mean local - UK gap over the weeks both exist.

Forecourts come from fuelcosts.co.uk (the government Fuel Finder open data, from Jan 2026):
`fuelPostcode:XXX [fuelRadius:N]` (miles, default 8) finds the Tesco/Asda/Sainsbury's/Morrisons
ones near a postcode (postcodes.io; the postcode is never stored) and saves them under
evSavings.stations in git-ignored config.local.json (they reveal the area). Each station's
`history?days=365` is merged into fuelData/station-{id}.json (same format as the app's cache), so
older changes are kept; prices are refetched when over 12 hours old.
"""
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

LOCAL_TZ = ZoneInfo('Europe/London')
LITRES_PER_GALLON = 4.54609
MAX_AGE_HOURS = 12
GOVUK = 'https://www.gov.uk/api/content/government/statistics/weekly-road-fuel-prices'
FUELCOSTS = 'https://fuelcosts.co.uk/api'
POSTCODES = 'https://api.postcodes.io/postcodes/'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/124.0 Safari/537.36')
SUPERMARKETS = re.compile('TESCO|ASDA|SAINSBURY|MORRISON', re.IGNORECASE)
NOT_SUPERMARKET = re.compile('DAILY', re.IGNORECASE)        # Morrisons Daily: franchised garages
DEFAULTS = {'mpg': 50.0, 'milesPerKwh': 3.9, 'chargeEfficiency': 0.9, 'beforeCharger': [], 'credits': [],
            'stations': [], 'fuelDataDir': 'fuelData'}


# ------------------------------------------------------------------ settings

def load_settings(settings: dict, local_path: str) -> dict:
    """evSavings: defaults < config.json < config.local.json (stations live there)."""
    cfg = dict(DEFAULTS)
    cfg.update(settings.get('evSavings') or {})
    try:
        with open(local_path, encoding='utf-8') as f:
            cfg.update(json.load(f).get('evSavings') or {})
    except FileNotFoundError:
        pass
    return cfg


def save_stations(local_path: str, stations: list):
    """Store the chosen forecourts under evSavings.stations in config.local.json."""
    try:
        with open(local_path, encoding='utf-8') as f:
            local = json.load(f)
    except FileNotFoundError:
        local = {}
    local.setdefault('evSavings', {})['stations'] = stations
    with open(local_path, 'w', encoding='utf-8') as f:
        json.dump(local, f, indent=2)


# ------------------------------------------------------------------ charging

def _period(prices: dict, day: str):
    return next((p for p in prices['data']['prices'] if p['datefrom'] <= day <= p['dateto']), None)


def _window(p: dict) -> tuple:
    def slot(t):
        return int(t[:2]) * 2 + (int(t[3:5]) >= 30)
    return slot(p['offpeakStart']), slot(p['offpeakStop'])


def _in_window(k: int, a: int, b: int) -> bool:
    return a <= k < b if a <= b else (k >= a or k < b)


def ev_day(day: str, ev_import_kwh: list, solar_kwh: float, prices: dict):
    """(kWh at the charger, £) for one analysed day, or None outside the energyPrices periods."""
    p = _period(prices, day)
    if p is None:
        return None
    a, b = _window(p)
    off, peak, export = float(p['offpeakRate']), float(p['peakRate']), float(p['exportRate'])
    cost = sum(kwh * (off if _in_window(k, a, b) else peak) for k, kwh in enumerate(ev_import_kwh))
    return sum(ev_import_kwh) + solar_kwh, cost + solar_kwh * export


def ev_days(days: list, prices: dict, indra_daily: dict | None = None) -> list:
    """[(date, kWh, £)] from [(date, 48 EV import kWh, solar kWh)], plus Indra days (`{date: kWh}`)
    before the first analysed day at the first period's off-peak rate."""
    out = []
    for day, ev, solar in days:
        r = ev_day(day, ev, solar, prices)
        if r and r[0] > 0:
            out.append((day, r[0], r[1]))
    if days and indra_daily:
        first = min(d for d, _, _ in days)
        first_period = min(prices['data']['prices'], key=lambda p: p['datefrom'])
        off = float(first_period['offpeakRate'])
        out += [(d, kwh, kwh * off) for d, kwh in indra_daily.items() if d < first and kwh > 0]
    return sorted(out)


# ------------------------------------------------------------------ petrol prices

def parse_national(text: str) -> list:
    """DESNZ weekly CSV -> [(date, ULSP pump p/litre)], oldest first."""
    out = []
    for line in text.lstrip('﻿').splitlines()[1:]:
        cols = line.strip().split(',')
        try:
            out.append((datetime.strptime(cols[0].strip(), '%d/%m/%Y').date(), float(cols[1])))
        except (ValueError, IndexError):
            continue
    return sorted(out)


def parse_stations(body: dict) -> list:
    """Big-four supermarket forecourts from a /stations response: [{id, name, postcode}]."""
    out = []
    for s in body.get('stations') or []:
        name = f"{s.get('trading_name') or ''} {s.get('brand_name') or ''}"
        if not SUPERMARKETS.search(name) or NOT_SUPERMARKET.search(name) or s.get('is_permanently_closed'):
            continue
        out.append({'id': s['node_id'], 'name': s.get('trading_name') or name.strip(),
                    'postcode': (s.get('location') or {}).get('postcode') or ''})
    return out


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace('Z', '+00:00'))


def parse_history(body: dict) -> list:
    """[(utc datetime, p/litre)] from a station history response."""
    return [(_utc(h['timestamp']), float(h['pricePence'])) for h in body.get('history') or []
            if h.get('timestamp') and h.get('pricePence') is not None]


def merge(old: list, new: list) -> list:
    """Union of two change lists by timestamp (the newer fetch wins a clash)."""
    by_time = dict(old)
    by_time.update(dict(new))
    return sorted(by_time.items())


def local_daily(stations: dict, start: date, stop: date) -> dict:
    """{date: (stations priced, mean p/litre)}, each station's price held from its change (local
    date) until the next; prices outside 100-250p are ignored."""
    by_day = defaultdict(list)
    for changes in stations.values():
        changes = sorted((t, p) for t, p in changes if 100 <= p <= 250)
        i, cur, d = 0, None, start
        while d <= stop:
            while i < len(changes) and changes[i][0].astimezone(LOCAL_TZ).date() <= d:
                cur = changes[i][1]
                i += 1
            if cur is not None:
                by_day[d.isoformat()].append(cur)
            d += timedelta(days=1)
    return {k: (len(v), sum(v) / len(v)) for k, v in by_day.items()}


def min_stations(count: int) -> int:
    return max(1, (2 * count + 2) // 3)


def offset(national: list, local: dict, minimum: int):
    """(mean local - national p/litre, weeks) over national weeks with well-covered local prices."""
    diffs = [local[d.isoformat()][1] - p for d, p in national
             if d.isoformat() in local and local[d.isoformat()][0] >= minimum]
    return (sum(diffs) / len(diffs), len(diffs)) if diffs else None


def petrol_price(day: str, national: list, local: dict, off, minimum: int):
    """(p/litre, source) for a date: 'local', 'national-adjusted' or 'national'; None before the data."""
    if day in local and local[day][0] >= minimum:
        return local[day][1], 'local'
    d = date.fromisoformat(day)
    weeks = [p for w, p in national if w <= d]
    if not weeks:
        return None
    return weeks[-1] + (off or 0.0), ('national-adjusted' if off is not None else 'national')


# ------------------------------------------------------------------ the comparison

def _month(m: str) -> dict:
    return {'month': m, 'kwh': 0.0, 'miles': 0.0, 'ev_cost': 0.0, 'litres': 0.0, 'petrol_cost': 0.0,
            'local_days': 0, 'days': 0, 'credit': 0.0}


def saved(m: dict) -> float:
    return m['petrol_cost'] - (m['ev_cost'] - m['credit'])


def calculate(days: list, cfg: dict, national: list, local: dict, station_count: int,
              today: date | None = None) -> dict:
    """Monthly miles, charging £, petrol £ and saving. `days` = [(date, kWh, £)]."""
    today = today or date.today()
    minimum = min_stations(station_count)
    off = offset(national, local, minimum)
    every = list(days) + [(f"{e['month']}-15", e['kwh'], e['kwh'] * e['rate'] / 100) for e in cfg['beforeCharger']]
    months = {}
    for day, kwh, cost in every:
        price = petrol_price(day, national, local, off[0] if off else None, minimum)
        if price is None:
            continue
        miles = kwh * cfg['chargeEfficiency'] * cfg['milesPerKwh']
        litres = miles / cfg['mpg'] * LITRES_PER_GALLON
        m = months.setdefault(day[:7], _month(day[:7]))
        m['kwh'] += kwh
        m['miles'] += miles
        m['ev_cost'] += cost
        m['litres'] += litres
        m['petrol_cost'] += litres * price[0] / 100
        m['local_days'] += price[1] == 'local'
        m['days'] += 1
    for c in cfg['credits']:
        months.setdefault(c['month'], _month(c['month']))['credit'] += float(c['amount'])
    latest = petrol_price(today.isoformat(), national, local, off[0] if off else None, minimum)
    return {'months': [months[k] for k in sorted(months)], 'offset': off[0] if off else None,
            'offset_weeks': off[1] if off else 0, 'latest': latest}


def total(months: list) -> dict:
    t = _month('')
    for m in months:
        for k in t:
            if k != 'month':
                t[k] += m[k]
    return t


def last_12(months: list, today: date | None = None) -> dict:
    """The last 12 complete months before today's month."""
    today = today or date.today()
    end = today.replace(day=1)
    start = date(end.year - 1, end.month, 1)
    return total([m for m in months if start.isoformat()[:7] <= m['month'] < end.isoformat()[:7]])


# ------------------------------------------------------------------ fetching

class FuelPrices:
    """Fetches and caches the DESNZ CSV and station histories in `data_dir`."""

    def __init__(self, session: aiohttp.ClientSession, data_dir: str):
        self.session = session
        self.dir = data_dir
        self.calls = 0
        os.makedirs(data_dir, exist_ok=True)

    async def _get(self, url: str, params: dict | None = None) -> str:
        self.calls += 1
        async with self.session.get(url, params=params, headers={'User-Agent': USER_AGENT},
                                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                raise RuntimeError(f'{url}: HTTP {resp.status}')
            return await resp.text()

    def _fresh(self, path: str) -> bool:
        return os.path.exists(path) and (datetime.now().timestamp() - os.path.getmtime(path)) < MAX_AGE_HOURS * 3600

    async def national_csv(self) -> str:
        path = os.path.join(self.dir, 'national.csv')
        if not self._fresh(path):
            try:
                content = json.loads(await self._get(GOVUK))
                url = next(a['url'] for a in content['details']['attachments']
                           if a.get('url', '').endswith('.csv') and 'CSV' in a.get('title', '')
                           and '2018' in a.get('title', ''))
                text = await self._get(url)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(text)
            except (aiohttp.ClientError, RuntimeError, StopIteration, KeyError, TimeoutError) as e:
                if not os.path.exists(path):
                    raise RuntimeError(f'weekly fuel prices: {e}') from e
        with open(path, encoding='utf-8') as f:
            return f.read()

    def cached_history(self, station_id: str) -> list:
        try:
            with open(os.path.join(self.dir, f'station-{station_id}.json'), encoding='utf-8') as f:
                return [(_utc(t), float(p)) for t, p in json.load(f)]
        except FileNotFoundError:
            return []

    async def history(self, station_id: str) -> list:
        path = os.path.join(self.dir, f'station-{station_id}.json')
        old = self.cached_history(station_id)
        if self._fresh(path):
            return old
        body = json.loads(await self._get(f'{FUELCOSTS}/stations/{station_id}/history',
                                          {'fuel': 'E10', 'days': '365'}))
        merged = merge(old, parse_history(body))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([[t.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'), p] for t, p in merged], f)
        return merged

    async def find_stations(self, postcode: str, radius_miles: float) -> list:
        """Supermarket forecourts selling E10 near a postcode (which isn't stored)."""
        loc = json.loads(await self._get(POSTCODES + postcode.replace(' ', '')))['result']
        body = json.loads(await self._get(f'{FUELCOSTS}/stations', {
            'lat': str(loc['latitude']), 'lon': str(loc['longitude']), 'radius': str(radius_miles),
            'fuel': 'E10', 'limit': '1000'}))
        return parse_stations(body)


# ------------------------------------------------------------------ report

def _pounds(v: float) -> str:
    return f'£{v:,.0f}' if v >= 0 else f'-£{-v:,.0f}'


def print_report(result: dict, cfg: dict, today: date | None = None):
    months = result['months']
    if not months:
        return
    t, y = total(months), last_12(months, today)
    stations = len(cfg['stations'])
    print('\nEV VS PETROL  (home charging vs a %g mpg petrol car; EVs %g mi/kWh at %d%% charging efficiency)'
          % (cfg['mpg'], cfg['milesPerKwh'], round(cfg['chargeEfficiency'] * 100)))
    print(f"  {'Month':8}{'kWh':>7}{'Miles':>8}{'EV £':>9}{'Petrol £':>10}{'p/L':>8}{'Saved £':>9}")
    for m in months:
        ppl = m['petrol_cost'] * 100 / m['litres'] if m['litres'] else 0
        flag = ('' if m['local_days'] == m['days'] else '*') + ('†' if m['credit'] else '')
        print(f"  {m['month']:8}{m['kwh']:7.0f}{m['miles']:8.0f}{m['ev_cost'] - m['credit']:9.2f}"
              f"{m['petrol_cost']:10.2f}{ppl:8.1f}{saved(m):9.2f} {flag}")
    print(f"  {'Total':8}{t['kwh']:7.0f}{t['miles']:8.0f}{t['ev_cost'] - t['credit']:9.2f}{t['petrol_cost']:10.2f}"
          f"{'':8}{saved(t):9.2f}")
    print(f"  Saved {_pounds(saved(t))} since {months[0]['month']}: {t['miles']:,.0f} miles for "
          f"{_pounds(t['ev_cost'] - t['credit'])} of electricity"
          + (f" (after {_pounds(t['credit'])} of credits)" if t['credit'] else '')
          + f" instead of {_pounds(t['petrol_cost'])} of petrol")
    if y['days']:
        print(f"  Last 12 full months: {_pounds(saved(y))} saved over {y['miles']:,.0f} miles")
    if result['latest']:
        p, src = result['latest']
        where = {'local': f'{stations} local supermarkets',
                 'national-adjusted': f"UK average {result['offset']:+.1f}p local adjustment"}.get(src, 'UK average')
        print(f'  Petrol now: {p:.1f}p/litre ({where})')
    if result['offset'] is not None:
        print(f"  Local supermarkets average {result['offset']:+.1f}p/litre vs the UK weekly average over "
              f"{result['offset_weeks']} weeks; * = days priced from that, † = after one-off credits")
    elif not stations:
        print('  No forecourts chosen (fuelPostcode:XXX [fuelRadius:N]): priced at the UK weekly average')


async def report(settings: dict, analysed_days: list, prices: dict, local_path: str, argv: list,
                 indra_daily: dict | None = None) -> dict | None:
    """Fetch petrol prices (finding forecourts first if fuelPostcode: is given), compute and print."""
    cfg = load_settings(settings, local_path)
    args = {k.lower(): v for k, _, v in (a.partition(':') for a in argv)}
    if args.get('evsavings', 'ON').upper() == 'OFF':
        return None
    async with aiohttp.ClientSession() as session:
        fuel = FuelPrices(session, cfg['fuelDataDir'])
        if args.get('fuelpostcode'):
            found = await fuel.find_stations(args['fuelpostcode'], float(args.get('fuelradius') or 8))
            if not found:
                raise RuntimeError('No Tesco, Asda, Sainsbury\'s or Morrisons forecourts found')
            save_stations(local_path, found)
            cfg['stations'] = found
            print(f'\nEV vs petrol: {len(found)} supermarket forecourts saved to {os.path.basename(local_path)}')
        national = parse_national(await fuel.national_csv())
        histories = {}
        for s in cfg['stations']:
            try:
                histories[s['id']] = await fuel.history(s['id'])
            except (aiohttp.ClientError, RuntimeError, TimeoutError):
                histories[s['id']] = fuel.cached_history(s['id'])
    changes = [t for h in histories.values() for t, _ in h]
    local = local_daily(histories, min(changes).astimezone(LOCAL_TZ).date(), date.today()) if changes else {}
    result = calculate(ev_days(analysed_days, prices, indra_daily), cfg, national, local, len(cfg['stations']))
    print_report(result, cfg)
    return result
