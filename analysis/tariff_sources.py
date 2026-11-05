"""Fetch current electricity import tariffs from the major UK suppliers, as half-hourly rates.

Every tariff becomes a dict::

    {'supplier', 'name', 'code', 'source', 'kind' ('variable' | 'fixed 12m' | ...),
     'slot_rates': [48 p/kWh, local Europe/London half-hours from 00:00],
     'standing': p/day, 'smart_ev': bool, 'ev_rate': p/kWh or None, 'note': str}

Sources (all public, no login):

* Kraken platform REST APIs: Octopus (also lists Co-op and London Power), E.ON Next and EDF
  all run on Kraken and publish ``/v1/products/``. The single-register Direct Debit tariff for
  the region gives the standing charge, and its ``standard-unit-rates`` timeline (UTC) over
  the last ``rateDays`` days is averaged into local half-hour slots, so time-of-use windows
  (Go 00:30-05:30, Next Drive 00:00-06:00 ...) and dynamic prices (Agile) come out exactly.
* energyplus.co.uk tariff pages, for suppliers without an API (British Gas, OVO, Scottish
  Power): regional "Electricity, Monthly Direct Debit, standard-rate / Economy 7" tables.
  These pages don't give an off-peak window, so an Economy 7-only (EV) tariff is used only
  when ``windows`` maps its name to one.
* Ofgem's regional price-cap table (the standard variable tariff of every capped supplier),
  read from the everviz widget embedded on its page.
* OVO's Charge Anytime add-on rate (smart-charged EV energy), from OVO's own page.

Every response is cached under ``{dataDir}/{YYYY-MM-DD}/`` so re-runs on the same day
make no network calls.
"""
import asyncio
import html
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

LOCAL_TZ = ZoneInfo('Europe/London')
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
REQUEST_TIMEOUT = 30
CONCURRENCY = 6

KRAKEN_HOSTS = {
    'octopus': 'https://api.octopus.energy',
    'eon': 'https://api.eonnext-kraken.energy',
    'edf': 'https://api.edfgb-kraken.energy',
}
BRANDS = {'OCTOPUS_ENERGY': 'Octopus', 'COOP_ENERGY': 'Co-op', 'LONDON_POWER': 'London Power',
          'EON_NEXT': 'E.ON Next', 'SMART_NEW_CO': 'E.ON Next', 'SAINSBURYS': "Sainsbury's (E.ON)",
          'EDF': 'EDF'}
HOST_SUPPLIER = {host: label for label, host in
                 (('Octopus', 'https://api.octopus.energy'), ('E.ON Next', 'https://api.eonnext-kraken.energy'),
                  ('EDF', 'https://api.edfgb-kraken.energy'))}
ENERGYPLUS = 'https://www.energyplus.co.uk'
OFGEM_CAP_URL = ('https://www.ofgem.gov.uk/your-energy-supply/your-energy-bill/'
                 'energy-price-cap-unit-rates-and-standing-charges')
EVERVIZ_INJECT = 'https://app.everviz.com/inject/{id}/'
OVO_CHARGE_ANYTIME_URL = 'https://www.ovoenergy.com/electric-cars/charge-anytime'

# Grid supply point group (as Octopus's /v1/industry/grid-supply-points/ returns it) ->
# the names energyplus.co.uk and Ofgem use for that region (matched case-insensitively).
REGION_NAMES = {
    'A': ['Eastern England', 'Eastern'],
    'B': ['East Midlands'],
    'C': ['London'],
    'D': ['Merseyside & North Wales', 'North Wales and Mersey', 'Merseyside and North Wales',
          'North Wales & Mersey'],
    'E': ['West Midlands'],
    'F': ['North East England', 'Northern England', 'North East'],
    'G': ['North West England', 'North Western England', 'North West'],
    'H': ['South England', 'Southern England', 'Southern'],
    'J': ['South East England', 'South Eastern England', 'South East'],
    'K': ['South Wales', 'Southern Wales'],
    'L': ['South West England', 'South Western England', 'South West'],
    'M': ['Yorkshire'],
    'N': ['South Scotland', 'Southern Scotland'],
    'P': ['North Scotland', 'Northern Scotland'],
}

DEFAULTS = {
    'region': '',
    'sources': ['octopus', 'eon', 'edf', 'energyplus', 'ofgem', 'ovoChargeAnytime'],
    'energyplusSuppliers': ['british-gas', 'ovo-energy', 'scottish-power'],
    # Products that aren't a plain household import tariff, or need hardware we don't have.
    'exclude': ('EXPORT|OUTGOING|SEG|ZERO|TEST|EMPLOYEE|ENGAGE|POWER.PACK|PODPOINT|POD.POINT|'
                'HEAT.PUMP|COSY|SNUG|FLUX|_GAS|PREPAY|PARTNER|SAMSUNG|-OEV-|OPTIMISE'),
    # Smart-charging tariffs: the supplier schedules the car, all of its energy at the cheap rate.
    'smartEvPattern': 'INTELLI|IOG',
    # Off-peak windows for Economy 7-only tariffs scraped from energyplus (name regex -> window).
    'windows': {'EV Power': '00:00-05:00', 'EV Saver': '00:00-05:00'},
    'rateDays': 14,
    'dataDir': 'tariffData',
    'manualTariffs': [],
    # Export tariffs worth comparing (Zero Bills, Power Pack and Flux need hardware/new builds).
    # E.ON Next Export Premium needs a system installed by E.ON's installation company.
    'exportExclude': 'ZERO|POWER.PACK|FLUX|TEST|NEXT_EXPORT_PREMIUM',
    # Export tariffs that only go with a flat (non time-of-use) import tariff:
    # E.ON Next Export Exclusive excludes "time of use tariffs i.e. Next Drive".
    'exportFlatImportOnly': 'NEXT_EXPORT_EXCLUSIVE',
    # Export tariffs assumed to need an import tariff from the same supplier (check before switching).
    'exportRequires': {'OUTGOING-VAR|AGILE-OUTGOING|OUTGOING-PRIME': 'Octopus',
                       'NEXT_EXPORT|NEXT_FLEX_EXPORT': 'E.ON Next',
                       'COOP-SEG-FIX': 'Co-op', 'LP-SEG-FIX': 'London Power'},
    'manualExportTariffs': [],
    # Gas offers (Kraken products with a gas tariff, energyplus Dual Fuel rows, the Ofgem gas cap).
    # E.ON Next Optimise needs solar panels *and* a home battery.
    'gasExclude': ('EXPORT|OUTGOING|SEG|ZERO|TEST|EMPLOYEE|ENGAGE|POWER.PACK|HEAT.PUMP|COSY|SNUG|FLUX|'
                   'PREPAY|PARTNER|OPTIMISE'),
    'manualGasTariffs': [],
}


def gas_code_key(code: str) -> str:
    """Product code with its gas marker removed, so E.ON's NEXT_DRIVE_FIXED_GAS_V37 matches the
    electricity product NEXT_DRIVE_FIXED_V37."""
    return re.sub(r'(?i)[-_]GAS(?=[-_]|$)', '', code)


def gas_offer(base: dict, rate, standing, note: str = '') -> dict | None:
    """A gas tariff dict (direction GAS: p/kWh and p/day inc VAT), or None if unreadable."""
    if rate is None or standing is None:
        return None
    return {'supplier': base['supplier'], 'name': base['name'], 'code': base['code'],
            'source': base['source'], 'kind': base.get('kind', ''), 'direction': 'GAS',
            'gas_rate': float(rate), 'gas_standing': float(standing), 'note': note}


def _kraken_gas(detail: dict, region: str):
    """(rate, standing) of a Kraken product's Direct Debit (or 'varying') gas tariff."""
    by_payment = (detail.get('single_register_gas_tariffs') or {}).get(f'_{region}') or {}
    t = by_payment.get('direct_debit_monthly') or by_payment.get('varying')
    if not t:
        return None, None
    return t.get('standard_unit_rate_inc_vat'), t.get('standing_charge_inc_vat')


class SourceError(Exception):
    pass


# ---------------------------------------------------------------- generic helpers

def family_key(code: str) -> str:
    """Product code without its version/date suffix, so only the newest version is kept."""
    key = re.sub(r'_HH$', '', code)
    key = re.sub(r'(-\d{2}){3}$', '', key)                 # Octopus: OE-FIX-12M-26-11-03
    key = re.sub(r'[_-]V\d+(_\d+)*$', '', key, flags=re.I)  # E.ON/EDF: NEXT_DRIVE_FIXED_V37
    return key


def window_slots(window: str) -> set:
    """'HH:MM-HH:MM' (may wrap midnight) -> set of half-hour slot indices it covers."""
    start, stop = (int(t[:2]) * 2 + (int(t[3:5]) >= 30) for t in window.split('-'))
    if start == stop:
        return set()
    if start < stop:
        return set(range(start, stop))
    return set(range(start, 48)) | set(range(0, stop))


def tou_slot_rates(peak: float, off_peak: float, window: str) -> list:
    cheap = window_slots(window)
    return [off_peak if k in cheap else peak for k in range(48)]


def rates_to_slots(rates: list, day_from: date, day_to: date):
    """Average Kraken unit-rate periods (UTC ``valid_from``/``valid_to``, None = open) over
    each local half-hour slot of the days [day_from, day_to). Returns 48 p/kWh values, with
    None for slots no period covered."""
    periods = []
    for r in rates:
        start = _parse_utc(r['valid_from']) if r.get('valid_from') else datetime.min.replace(tzinfo=timezone.utc)
        stop = _parse_utc(r['valid_to']) if r.get('valid_to') else datetime.max.replace(tzinfo=timezone.utc)
        periods.append((start, stop, float(r['value_inc_vat'])))
    sums, counts = [0.0] * 48, [0] * 48
    day = day_from
    while day < day_to:
        for k in range(48):
            local = datetime(day.year, day.month, day.day, k // 2, 30 * (k % 2), tzinfo=LOCAL_TZ)
            instant = local.astimezone(timezone.utc)
            for start, stop, value in periods:
                if start <= instant < stop:
                    sums[k] += value
                    counts[k] += 1
                    break
        day += timedelta(days=1)
    return [sums[k] / counts[k] if counts[k] else None for k in range(48)]


def _parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)


def region_names(region: str) -> list:
    return [n.lower() for n in REGION_NAMES.get(region.upper().lstrip('_'), [])]


class Fetcher:
    """aiohttp GETs with a per-day file cache and a concurrency cap."""

    def __init__(self, session: aiohttp.ClientSession, cache_dir: str):
        self.session = session
        self.cache_dir = cache_dir
        self.calls = 0
        self.hits = 0
        self._sem = asyncio.Semaphore(CONCURRENCY)
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, url: str, params: dict | None) -> str:
        key = url.split('://', 1)[-1] + ('?' + '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
                                          if params else '')
        return os.path.join(self.cache_dir, re.sub(r'[^\w.-]+', '_', key)[:200])

    async def get_text(self, url: str, params: dict | None = None) -> str:
        path = self._path(url, params)
        if os.path.exists(path):
            self.hits += 1
            with open(path, encoding='utf-8') as f:
                return f.read()
        async with self._sem:
            self.calls += 1
            async with self.session.get(url, params=params, headers={'User-Agent': USER_AGENT},
                                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
                if resp.status != 200:
                    raise SourceError(f'{url}: HTTP {resp.status}')
                text = await resp.text()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return text

    async def get_json(self, url: str, params: dict | None = None):
        return json.loads(await self.get_text(url, params))


# ---------------------------------------------------------------- Kraken (Octopus, E.ON Next, EDF)

def _is_export(product: dict) -> bool:
    if product.get('direction'):
        return product['direction'] == 'EXPORT'
    return bool(re.search(r'EXPORT|OUTGOING|SEG', product['code'], re.I))


async def kraken_tariffs(fetcher: Fetcher, host: str, region: str, cfg: dict, today: date,
                         direction: str = 'IMPORT') -> list:
    """Current import (or, with direction='EXPORT', export) tariffs from one Kraken host."""
    export = direction == 'EXPORT'
    products, url = [], f'{host}/v1/products/'
    params = {'page_size': 500}
    while url:
        page = await fetcher.get_json(url, params)
        products += page['results']
        url, params = page.get('next'), None
    exclude = re.compile(cfg['exportExclude' if export else 'exclude'], re.I)
    latest = {}
    for p in products:
        if p.get('is_business') or p.get('is_prepay') or _is_export(p) != export:
            continue
        if exclude.search(p['code']) or exclude.search(p['full_name'] or ''):
            continue
        fam = (p.get('brand'), family_key(p['code']))
        if fam not in latest or (p['available_from'] or '') > (latest[fam]['available_from'] or ''):
            latest[fam] = p
    results = await asyncio.gather(*(_kraken_product(fetcher, host, p, region, cfg, today)
                                     for p in latest.values()), return_exceptions=True)
    tariffs = []
    for p, r in zip(latest.values(), results):
        if isinstance(r, Exception):
            print(f"  [skip] {p['code']}: {r}")
        elif r:
            tariffs.append(_as_export(r, cfg) if export else r)
    return tariffs


def _as_export(tariff: dict, cfg: dict) -> dict:
    """An export tariff: no standing charge, a same-supplier import requirement (assumed from
    ``exportRequires``), and the first sentence of the product description as its note."""
    requires = next((sup for pat, sup in cfg['exportRequires'].items()
                     if re.search(pat, tariff['code'], re.I)), None)
    first = re.split(r'(?<=\.)\s', tariff.get('description') or '')[0][:90]
    flat_only = bool(cfg.get('exportFlatImportOnly')
                     and re.search(cfg['exportFlatImportOnly'], tariff['code'], re.I))
    if flat_only:
        first = (first + ' Not with time-of-use import tariffs.').strip()
    return {**tariff, 'direction': 'EXPORT', 'standing': 0.0, 'smart_ev': False,
            'requires': requires, 'flat_import_only': flat_only, 'note': first}


async def _kraken_product(fetcher: Fetcher, host: str, product: dict, region: str, cfg: dict,
                          today: date):
    code = product['code']
    detail = await fetcher.get_json(f'{host}/v1/products/{code}/')
    name = detail.get('display_name') or product['display_name']
    term = product.get('term')
    result = {
        'supplier': BRANDS.get(product.get('brand') or '', HOST_SUPPLIER.get(host, host)),
        'name': name,
        'code': code,
        'source': host.split('//')[-1],
        'kind': 'variable' if product.get('is_variable') else (f'fixed {term}m' if term else 'fixed'),
        'smart_ev': bool(re.search(cfg['smartEvPattern'], code, re.I)),
        'ev_rate': None,
        'note': '',
        'direction': 'IMPORT',
        'description': re.sub(r'\s+', ' ', detail.get('description') or '').strip(),
    }
    result['gas'] = gas_offer(result, *_kraken_gas(detail, region))
    single = ((detail.get('single_register_electricity_tariffs') or {})
              .get(f'_{region}', {}).get('direct_debit_monthly'))
    if single:
        slots = await _kraken_unit_rate_slots(fetcher, single, product, cfg, today)
        if slots is None:
            return None
        return {**result, 'slot_rates': slots, 'standing': float(single['standing_charge_inc_vat'])}

    # Four-rate EV tariffs (Intelligent Octopus Go): day/night rates without times (the
    # window is only in the description) plus a rate for smart-charged EV energy.
    ev4 = ((detail.get('four_rate_ev_electricity_tariffs') or {})
           .get(f'_{region}', {}).get('direct_debit_monthly'))
    if ev4 and ev4.get('day_unit_rate_inc_vat') is not None:
        window = _window_from_text(detail.get('description') or '') or next(
            (w for pat, w in cfg['windows'].items() if re.search(pat, name, re.I)), None)
        if not window:
            print(f'  [skip] {code}: four-rate EV tariff with no off-peak window in its description')
            return None
        ev_off = ev4.get('ev_device_off_peak_unit_rate_inc_vat')
        return {**result,
                'slot_rates': tou_slot_rates(float(ev4['day_unit_rate_inc_vat']),
                                             float(ev4['night_unit_rate_inc_vat']), window),
                'standing': float(ev4['standing_charge_inc_vat']),
                'smart_ev': ev_off is not None or result['smart_ev'],
                'ev_rate': float(ev_off) if ev_off is not None else None,
                'note': f'off-peak {window} (from description); smart-charged EV at its EV rate'}
    return None                      # gas-only, Economy 7 meters only, or not sold in this region


async def kraken_gas_offers(fetcher: Fetcher, host: str, region: str, cfg: dict) -> list:  # pylint: disable=too-many-locals
    """Current gas tariffs from one Kraken host: the newest version of every product family
    (gas-only products included) with a Direct Debit gas tariff for the region."""
    products, url = [], f'{host}/v1/products/'
    params = {'page_size': 500}
    while url:
        page = await fetcher.get_json(url, params)
        products += page['results']
        url, params = page.get('next'), None
    exclude = re.compile(cfg['gasExclude'], re.I)
    latest = {}
    for p in products:
        if p.get('is_business') or p.get('is_prepay') or _is_export(p):
            continue
        if exclude.search(p['code']) or exclude.search(p['full_name'] or ''):
            continue
        fam = (p.get('brand'), family_key(p['code']))
        if fam not in latest or (p['available_from'] or '') > (latest[fam]['available_from'] or ''):
            latest[fam] = p
    details = await asyncio.gather(*(fetcher.get_json(f"{host}/v1/products/{p['code']}/")
                                     for p in latest.values()), return_exceptions=True)
    offers = []
    for p, detail in zip(latest.values(), details):
        if isinstance(detail, Exception):
            continue
        term = p.get('term')
        base = {'supplier': BRANDS.get(p.get('brand') or '', HOST_SUPPLIER.get(host, host)),
                'name': detail.get('display_name') or p['display_name'], 'code': p['code'],
                'source': host.split('//')[-1],
                'kind': 'variable' if p.get('is_variable') else (f'fixed {term}m' if term else 'fixed')}
        offer = gas_offer(base, *_kraken_gas(detail, region))
        if offer:
            offers.append(offer)
    return offers


def _window_from_text(text: str):
    m = re.search(r'(\d{1,2}:\d{2})\s*(?:-|–|to|and)\s*(\d{1,2}:\d{2})', text)
    if not m:
        return None
    return '-'.join(t.zfill(5) for t in m.groups())


async def _kraken_unit_rate_slots(fetcher: Fetcher, tariff: dict, product: dict, cfg: dict,
                                  today: date):
    """48 local half-hour p/kWh from the tariff's unit-rate timeline over the last rateDays
    days (or its first rateDays if it launched since), else its flat standard rate."""
    rates_url = next((l['href'] for l in tariff['links'] if l['rel'] == 'standard_unit_rates'), None)
    days = int(cfg['rateDays'])
    available = (product.get('available_from') or '')[:10]
    day_from = max(today - timedelta(days=days), date.fromisoformat(available) if available else today)
    day_to = min(day_from + timedelta(days=days), today + timedelta(days=2))
    slots: list = [None] * 48
    if rates_url:
        rates, url = [], rates_url
        params: dict | None = {'period_from': f'{day_from}T00:00:00Z',
                               'period_to': f'{day_to}T00:00:00Z', 'page_size': 1500}
        while url:
            page = await fetcher.get_json(url, params)
            rates += page['results']
            url, params = page.get('next'), None
        slots = rates_to_slots(rates, day_from, day_to)
    flat = tariff.get('standard_unit_rate_inc_vat')
    known = [s for s in slots if s is not None]
    if not known:
        return [float(flat)] * 48 if flat is not None else None
    fill = float(flat) if flat is not None else max(known)
    return [fill if s is None else s for s in slots]


# ---------------------------------------------------------------- energyplus.co.uk

def parse_energyplus_tables(page: str) -> dict:
    """{table title: [row cells, ...]} for every table on an energyplus tariff page."""
    tables = {}
    for match in re.finditer(r'<table.*?</table>', page, flags=re.S):
        before = page[max(0, match.start() - 1500):match.start()]
        heads = re.findall(r'<h[2-4][^>]*>(.*?)</h[2-4]>', before, re.S)
        if not heads:
            continue
        title = _text(heads[-1])
        rows = [[_text(c) for c in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.S)]
                for row in re.findall(r'<tr.*?</tr>', match.group(0), re.S)]
        tables.setdefault(title, rows)
    return tables


def _text(fragment: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment))).strip()


def _region_row(rows: list, names: list):
    for row in rows:
        if row and row[0].strip().lower() in names:
            return row
    return None


def _num(text: str):
    m = re.search(r'-?\d+(?:\.\d+)?', text.replace(',', ''))
    return float(m.group(0)) if m else None


def energyplus_tariff(page: str, slug: str, supplier_slug: str, region: str, cfg: dict):
    """One tariff from an energyplus page, or (None, reason)."""
    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S)
    title = _text(h1.group(1)) if h1 else slug
    m = re.match(r'(.*?) by (.*?):', title)
    name, supplier = (m.group(1), m.group(2)) if m else (title, supplier_slug)
    tables = parse_energyplus_tables(page)
    names = region_names(region)
    header = None

    def find(kind):
        nonlocal header
        for t, rows in tables.items():
            if t.startswith('Electricity, Monthly Direct Debit') and kind in t and rows:
                row = _region_row(rows[1:], names)
                if row:
                    header = rows[0]
                    return dict(zip(rows[0], row))
        return None

    std = find('standard-rate')
    e7 = None if std else find('Economy 7')
    gas_row = None
    for t, rows in tables.items():
        if t.startswith('Dual Fuel, Monthly Direct Debit') and rows:
            gas_row = _region_row(rows[1:], names)
            if gas_row:
                gas_row = dict(zip(rows[0], gas_row))
                break
    base = {'supplier': supplier, 'name': name, 'code': slug, 'source': 'energyplus.co.uk',
            'smart_ev': False, 'ev_rate': None, 'note': '', 'direction': 'IMPORT'}
    row = std or e7
    if row is None:
        return None, 'no Monthly Direct Debit electricity row for this region'
    contract = row.get('Contract', '')
    months = _num(contract)
    lname = name.lower()
    base['kind'] = ('tracker' if 'tracker' in lname else
                    'variable' if 'variable' in lname else
                    f'fixed {int(months)}m' if months else 'fixed')
    base['standing'] = _num(row.get('Elec standing (p/day)', ''))
    base['gas'] = gas_offer(base, _num(gas_row.get('Gas rate (p/kWh)', '')),
                            _num(gas_row.get('Gas standing (p/day)', ''))) if gas_row else None
    peak = _num(row.get('Elec rate (p/kWh)', ''))
    if peak is None or base['standing'] is None:
        return None, f'unreadable row {row}'
    if std:
        base['slot_rates'] = [peak] * 48
        return base, None
    window = next((w for pat, w in cfg['windows'].items() if re.search(pat, name, re.I)), None)
    if not window:
        return None, 'Economy 7 only and no off-peak window configured (tariffCompare.windows)'
    off = _num(row.get('E7 off-peak (p/kWh)', ''))
    if off is None:
        return None, f'no off-peak rate in {row}'
    base['slot_rates'] = tou_slot_rates(peak, off, window)
    base['note'] = f'off-peak window {window} assumed'
    return base, None


async def energyplus_tariffs(fetcher: Fetcher, region: str, cfg: dict) -> list:
    tariffs = []
    exclude = re.compile(cfg['exclude'].replace('_', '[-_ ]'), re.I)
    for supplier in cfg['energyplusSuppliers']:
        index = await fetcher.get_text(f'{ENERGYPLUS}/suppliers/{supplier}/tariffs')
        slugs = sorted(set(re.findall(rf'href="/suppliers/{re.escape(supplier)}/tariffs/([^"/?#]+)"', index)))
        slugs = [s for s in slugs if not exclude.search(s)]
        pages = await asyncio.gather(*(fetcher.get_text(f'{ENERGYPLUS}/suppliers/{supplier}/tariffs/{s}')
                                       for s in slugs), return_exceptions=True)
        for slug, page in zip(slugs, pages):
            if not isinstance(page, str):
                print(f'  [skip] {supplier}/{slug}: {page}')
                continue
            tariff, reason = energyplus_tariff(page, slug, supplier, region, cfg)
            if tariff:
                tariffs.append(tariff)
            else:
                print(f'  [skip] {supplier}/{slug}: {reason}')
    return tariffs


# ---------------------------------------------------------------- Ofgem price cap

def parse_everviz_table(js: str):
    """(title, rows) from an everviz table 'inject' script (a JSON array of row arrays)."""
    m = re.search(r'<h3>(.*?)</h3>', js)
    title = _text(m.group(1)) if m else ''
    start = js.find('[["')
    if start < 0:
        return title, []
    depth, end = 0, start
    for i in range(start, len(js)):
        if js[i] == '[':
            depth += 1
        elif js[i] == ']':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        rows = json.loads(js[start:end])
    except json.JSONDecodeError:
        return title, []
    return title, rows


def price_cap_tariff(rows: list, region: str):
    """The Direct Debit single-rate cap for the region, at the newest period in the table."""
    header, row = rows[0], _region_row(rows[1:], region_names(region))
    if not row:
        return None
    sc_cols = [i for i, h in enumerate(header) if 'standing' in h.lower()]
    unit_cols = [i for i, h in enumerate(header) if 'unit rate' in h.lower()]
    if not sc_cols or not unit_cols:
        return None
    period = re.sub(r'(?i)unit rate\s*', '', header[unit_cols[-1]]).strip()
    unit, standing = _num(row[unit_cols[-1]]), _num(row[sc_cols[-1]])
    return {
        'supplier': 'Price cap', 'name': f'Standard variable ({period})', 'code': 'OFGEM_CAP',
        'source': 'ofgem.gov.uk', 'kind': 'variable', 'slot_rates': [unit] * 48,
        'standing': standing, 'smart_ev': False, 'ev_rate': None, 'direction': 'IMPORT',
        'note': 'standard variable tariff of British Gas, OVO, Scottish Power, E.ON Next, EDF ...',
    }


async def ofgem_cap_tariff(fetcher: Fetcher, region: str):
    """The electricity price-cap tariff, with the gas cap (Direct Debit) as its ``gas``."""
    page = await fetcher.get_text(OFGEM_CAP_URL)
    elec, gas_rows = None, None
    for widget in sorted(set(re.findall(r'everviz\.com/inject/([A-Za-z0-9_-]+)', page))):
        title, rows = parse_everviz_table(await fetcher.get_text(EVERVIZ_INJECT.format(id=widget)))
        t = title.lower()
        if t.startswith('electricity') and 'direct debit' in t and 'single rate' in t and rows:
            elec = price_cap_tariff(rows, region)
        elif t.startswith('gas') and 'direct debit' in t and rows:
            gas_rows = rows
    if elec is None:
        raise SourceError('Direct Debit single-rate electricity table not found on the Ofgem page')
    gas = price_cap_tariff(gas_rows, region) if gas_rows else None
    elec['gas'] = gas_offer(elec, gas['slot_rates'][0], gas['standing']) if gas else None
    return elec


# ---------------------------------------------------------------- OVO Charge Anytime

def parse_charge_anytime_rate(page: str):
    text = _text(re.sub(r'<script.*?</script>|<style.*?</style>', '', page, flags=re.S))
    m = re.search(r'Charge Anytime[^.]{0,80}?rate[^.]{0,40}? is (\d+(?:\.\d+)?)p per kWh', text)
    if not m:
        m = re.search(r'discounted to (\d+(?:\.\d+)?)p per kWh for smart charging', text)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- manual entries

def manual_gas_tariff(entry: dict) -> dict:
    """A config entry {supplier, name, rate, standingCharge [, kind, note]} (p/kWh, p/day)."""
    base = {'supplier': entry.get('supplier', 'Manual'), 'name': entry.get('name', 'Manual gas'),
            'code': entry.get('code', 'MANUAL_GAS'), 'source': 'config', 'kind': entry.get('kind', '')}
    return gas_offer(base, entry['rate'], entry['standingCharge'], entry.get('note', ''))


def link_gas(tariffs: list) -> list:
    """Gas offers for every import tariff's bundled gas (as GAS tariffs, de-duplicated), and
    each import tariff's ``gas`` filled from a separate gas product of the same supplier with
    the same code (E.ON publishes NEXT_DRIVE_FIXED_GAS_V37 beside NEXT_DRIVE_FIXED_V37)."""
    gas = [t for t in tariffs if t.get('direction') == 'GAS']
    by_key = {(g['supplier'], gas_code_key(g['code'])): g for g in gas}
    for t in tariffs:
        if t.get('direction', 'IMPORT') == 'IMPORT' and not t.get('gas'):
            t['gas'] = by_key.get((t['supplier'], gas_code_key(t['code'])))
    seen = {(g['supplier'], g['code']) for g in gas}
    extra = []
    for t in tariffs:
        g = t.get('gas') if t.get('direction', 'IMPORT') == 'IMPORT' else None
        if g and (g['supplier'], g['code']) not in seen and not t['code'].endswith('+CA'):
            seen.add((g['supplier'], g['code']))
            extra.append(g)
    return tariffs + extra


def manual_tariff(entry: dict, direction: str = 'IMPORT') -> dict:
    """A config entry {supplier, name, peakRate, standingCharge [, offpeakRate, offpeakWindow,
    evRate, smartEv, kind, note]} in p/kWh and p/day. Export entries (``manualExportTariffs``)
    may give ``rate`` instead of peakRate, no standing charge, and ``requires`` (a supplier
    whose import tariff they need)."""
    peak = float(entry['peakRate'] if 'peakRate' in entry else entry['rate'])
    window = entry.get('offpeakWindow')
    slots = (tou_slot_rates(peak, float(entry['offpeakRate']), window)
             if window and 'offpeakRate' in entry else [peak] * 48)
    return {
        'supplier': entry.get('supplier', 'Manual'), 'name': entry.get('name', 'Manual tariff'),
        'code': entry.get('code', 'MANUAL'), 'source': 'config', 'kind': entry.get('kind', ''),
        'slot_rates': slots, 'standing': float(entry.get('standingCharge', 0.0)),
        'smart_ev': bool(entry.get('smartEv', False)),
        'ev_rate': float(entry['evRate']) if entry.get('evRate') is not None else None,
        'note': entry.get('note', ''),
        'direction': direction,
        'requires': entry.get('requires'),
        'gas': gas_offer({'supplier': entry.get('supplier', 'Manual'), 'name': entry.get('name', 'Manual tariff'),
                          'code': entry.get('code', 'MANUAL'), 'source': 'config'},
                         entry.get('gasRate'), entry.get('gasStandingCharge')),
    }


# ---------------------------------------------------------------- everything

async def fetch_all(cfg: dict, today: date | None = None) -> tuple:
    """(tariffs, fetcher stats) for the configured sources; a failing source is reported and
    skipped rather than aborting the comparison."""
    today = today or date.today()
    region = cfg['region'].upper().lstrip('_')
    if region not in REGION_NAMES:
        raise SourceError(f"tariffCompare.region must be one of {''.join(REGION_NAMES)} "
                          f"(got {cfg['region']!r}); find it with "
                          "https://api.octopus.energy/v1/industry/grid-supply-points/?postcode=...")
    sources = cfg['sources']
    tariffs = []
    async with aiohttp.ClientSession() as session:
        fetcher = Fetcher(session, os.path.join(cfg['dataDir'], today.isoformat()))
        for name, host in KRAKEN_HOSTS.items():
            if name in sources:
                try:
                    tariffs += await kraken_tariffs(fetcher, host, region, cfg, today)
                    tariffs += await kraken_tariffs(fetcher, host, region, cfg, today, 'EXPORT')
                    tariffs += await kraken_gas_offers(fetcher, host, region, cfg)
                except (SourceError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                    print(f'  [source failed] {name}: {e}')
        if 'energyplus' in sources:
            try:
                tariffs += await energyplus_tariffs(fetcher, region, cfg)
            except (SourceError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f'  [source failed] energyplus: {e}')
        cap = None
        if 'ofgem' in sources:
            try:
                cap = await ofgem_cap_tariff(fetcher, region)
                if cap:
                    tariffs.append(cap)
            except (SourceError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f'  [source failed] ofgem: {e}')
        if 'ovoChargeAnytime' in sources:
            try:
                rate = parse_charge_anytime_rate(await fetcher.get_text(OVO_CHARGE_ANYTIME_URL))
                if rate is None:
                    raise SourceError('rate not found on the page')
                ovo = [t for t in tariffs if t['supplier'].lower().startswith('ovo')
                       and t.get('direction', 'IMPORT') == 'IMPORT']
                bases = [min(ovo, key=lambda t: t['slot_rates'][0] * 3000 + t['standing'] * 365)] if ovo else []
                if cap:
                    bases.append({**cap, 'supplier': 'OVO', 'name': 'Standard variable (cap)',
                                  'code': 'OVO_SVT', 'note': ''})
                for t in bases:
                    tariffs.append({**t, 'name': f"{t['name']} + Charge Anytime",
                                    'code': t['code'] + '+CA', 'ev_rate': rate, 'smart_ev': True,
                                    'note': f'EV smart-charged at {rate}p (compatible car/charger needed)'})
            except (SourceError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f'  [source failed] OVO Charge Anytime: {e}')
    tariffs += [manual_tariff(e) for e in cfg.get('manualTariffs') or []]
    tariffs += [manual_tariff(e, 'EXPORT') for e in cfg.get('manualExportTariffs') or []]
    tariffs += [manual_gas_tariff(e) for e in cfg.get('manualGasTariffs') or []]
    return link_gas(tariffs), {'calls': fetcher.calls, 'hits': fetcher.hits}
