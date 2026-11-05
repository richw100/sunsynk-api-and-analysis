"""Download E.ON Next half-hourly smart-meter data (electricity import/export, gas) into eonData/.

Usage: python analysis/eondata.py [config:path.json] [startDate:YYYY-MM-DD] [stopDate:YYYY-MM-DD]
                                  [compare:ON|OFF] [tariffs:ON|OFF] [dataDir:DIR]

Settings come from the "eon" object in config.json, then config.local.json (git-ignored), then
key:value args. The first run asks you to sign in in a browser and paste back the callback URL
(see eon_client.py; $EON_CALLBACK can supply it); the session is saved to eonData/.session.json.

Months are walked back from stopDate (default: this month) to startDate, or until
EMPTY_MONTHS_STOP months in a row have no readings for any meter. The summary prints monthly
kWh per meter and, with compare:ON (default), the meter against the local caches on the days
both have readings:
  import: E.ON meter vs Sunsynk grid import + Indra EV charging (the charger is beyond the CT)
  export: E.ON meter vs Sunsynk export, and Fuse meter where the Fuse cache has that day
With tariffs:ON (default) it also saves every tariff agreement, every billed charge and each
agreement's published unit-rate changes to eonData/tariffs.json and prints them: the rates you
actually paid for electricity import, export and gas.
"""
import asyncio
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis.eon_client import KINDS, EonNextClient, load_daily_kwh, load_readings, plausible  # noqa: E402
from analysis.fuse_client import load_daily_export_kwh  # noqa: E402
from analysis.indra_client import load_daily_charge_kwh  # noqa: E402

DEFAULTS = {'startDate': '', 'stopDate': '', 'compare': 'ON', 'tariffs': 'ON', 'dataDir': 'eonData',
            'inverterDir': 'inverterData', 'fuseDataDir': 'fuseenergydata', 'indraDataDir': 'indraData'}
LOCAL_CONFIG = 'config.local.json'
EMPTY_MONTHS_STOP = 3
EARLIEST_YEAR = 2015


def _read_section(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f).get('eon', {})
    except FileNotFoundError:
        return {}


def load_settings(argv, local_path: str = LOCAL_CONFIG):
    """Merge DEFAULTS < config.json "eon" < config.local.json "eon" < key:value CLI args."""
    config_path, overrides = 'config.json', {}
    canonical = {k.lower(): k for k in DEFAULTS}
    for arg in argv:
        key, sep, value = arg.partition(':')
        if not sep:
            continue
        if key.lower() == 'config':
            config_path = value
        elif key.lower() in canonical:
            overrides[canonical[key.lower()]] = value
        else:
            print(f'Ignoring unknown option: {key}')
    return {**DEFAULTS, **_read_section(config_path), **_read_section(local_path), **overrides}


def months_back(stop: date, start: date | None):
    """(year, month) from `stop` backwards, down to `start`'s month if given."""
    y, m = stop.year, stop.month
    while y >= EARLIEST_YEAR and (start is None or (y, m) >= (start.year, start.month)):
        yield y, m
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)


async def collect(client: EonNextClient, start: date | None, stop: date) -> dict:
    """{(year, month): {kind: kWh}} walking back from ``stop``; stops after EMPTY_MONTHS_STOP
    empty months in a row (when no ``start``) or at ``start``."""
    out, empty_run = {}, 0
    for y, m in months_back(stop, start):
        totals = {}
        for kind in KINDS:
            body = await client.get_month(kind, y, m)
            if body is not None:
                totals[kind] = sum(r[1] for r in plausible(kind, body['readings'])[0])
        out[(y, m)] = totals
        empty_run = 0 if any(v > 0 for v in totals.values()) else empty_run + 1
        if start is None and empty_run >= EMPTY_MONTHS_STOP:
            break
    return out


def sunsynk_daily(inverter_dir: str, label: str) -> dict:
    """{'YYYY-MM-DD': kWh} for 'Import' or 'Export' from cached Sunsynk month files."""
    names = {'Import': ('Import', 'Purchased electricity'), 'Export': ('Export', 'Sold electricity')}[label]
    out = {}
    for path in sorted(glob.glob(os.path.join(inverter_dir, 'month-????-??.json'))):
        with open(path, encoding='utf-8') as f:
            infos = (json.load(f).get('data') or {}).get('infos') or []
        for info in infos:
            if info.get('label') in names:
                for r in info.get('records') or []:
                    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', r.get('time', '')):
                        out[r['time']] = float(r['value'])
    return out


def zero_filled(daily: dict) -> dict:
    """``daily`` with 0.0 for every missing date between its first and last day (Indra only
    has entries for days the car charged)."""
    if not daily:
        return {}
    out, d, last = {}, date.fromisoformat(min(daily)), date.fromisoformat(max(daily))
    while d <= last:
        out[d.isoformat()] = daily.get(d.isoformat(), 0.0)
        d = date.fromordinal(d.toordinal() + 1)
    return out


def compare_monthly(meter: dict, sources: dict) -> dict:
    """{month: {'days', 'meter', name: kWh ...}} over days present in ``meter`` and every
    source (``sources`` = {name: {date: kWh}}), so each month compares like with like."""
    out = defaultdict(lambda: defaultdict(float))
    for day, kwh in meter.items():
        if all(day in s for s in sources.values()):
            row = out[day[:7]]
            row['days'] += 1
            row['meter'] += kwh
            for name, s in sources.items():
                row[name] += s[day]
    return out


def print_summary(months: dict, client: EonNextClient):
    """Monthly kWh per meter, plus any implausible readings dropped."""
    print(f"\nE.ON NEXT SMART METER (kWh)  {client.fetched} requests, {client.cached} months from cache")
    for kind in KINDS:
        dropped = load_readings(client.data_dir, kind)[1]
        if dropped:
            print(f"  {kind}: dropped {len(dropped)} implausible half-hours "
                  f"({', '.join(f'{r[0][:16]} {r[1]:.1f} kWh' for r in dropped[:3])})")
    print(f"{'Month':8} {'import':>9} {'export':>9} {'gas':>9}")
    for (y, m) in sorted(months):
        t = months[(y, m)]
        if not any(t.values()):
            continue
        print(f"{y:04d}-{m:02d} " + ' '.join(f"{t[k]:9.1f}" if k in t else f"{'-':>9}" for k in KINDS))


def print_compare(settings: dict):
    """E.ON meter vs Sunsynk (+ Indra EV for import, + Fuse for export) by month."""
    data_dir = settings['dataDir']
    imp = load_daily_kwh(data_dir, 'import')
    exp = {d: v for d, v in load_daily_kwh(data_dir, 'export').items() if v > 0}
    inv_imp = sunsynk_daily(settings['inverterDir'], 'Import')
    inv_exp = sunsynk_daily(settings['inverterDir'], 'Export')
    ev = zero_filled({d: v['total'] for d, v in load_daily_charge_kwh(settings['indraDataDir']).items()})
    fuse = load_daily_export_kwh(settings['fuseDataDir'])

    if imp:
        print('\nIMPORT: E.ON meter vs Sunsynk grid import + Indra EV (days with all three)')
        print(f"{'Month':8} {'days':>4} {'meter':>8} {'sunsynk':>8} {'EV':>7} {'sum':>8} {'diff':>7}")
        for m, r in sorted(compare_monthly(imp, {'sunsynk': inv_imp, 'ev': ev}).items()):
            both = r['sunsynk'] + r['ev']
            pct = 100 * (r['meter'] - both) / both if both else 0.0
            print(f"{m:8} {r['days']:4.0f} {r['meter']:8.1f} {r['sunsynk']:8.1f} {r['ev']:7.1f} {both:8.1f} {pct:6.1f}%")
    if exp:
        print('\nEXPORT: E.ON meter vs Sunsynk export (days with both)')
        print(f"{'Month':8} {'days':>4} {'meter':>8} {'sunsynk':>8} {'ratio':>6}")
        for m, r in sorted(compare_monthly(exp, {'sunsynk': inv_exp}).items()):
            ratio = r['meter'] / r['sunsynk'] if r['sunsynk'] else 0.0
            print(f"{m:8} {r['days']:4.0f} {r['meter']:8.1f} {r['sunsynk']:8.1f} {ratio:6.3f}")
        overlap = compare_monthly(exp, {'fuse': fuse})
        if overlap:
            print('  E.ON export meter vs Fuse meter on the same days:')
            for m, r in sorted(overlap.items()):
                print(f"  {m:8} {r['days']:4.0f} days  E.ON {r['meter']:7.1f}  Fuse {r['fuse']:7.1f}")
    print('\nNotes: Indra daily reports are bucketed by UTC day and Sunsynk export is the raw '
          'inverter figure (before exportCorrection), so small daily offsets are expected.')


def print_tariffs(body: dict):
    """Agreements with their published rate changes, then the billed charges (VAT included)."""
    print('\nTARIFF HISTORY (published Direct Debit unit rates, p/kWh inc VAT)')
    for ag, changes in zip(body['agreements'], body['published']):
        print(f"  {ag['meter']:6} {ag['from']} to {ag['to'] or 'open':10}  {ag['name']}")
        for day, rates in changes:
            print(f"         from {day}: " + ' / '.join(f'{r:.3f}' for r in rates))
        if not changes:
            print('         (not published; see the billed rates below)')
    print('\nBILLED CHARGES (p/kWh and p/day as billed, VAT included)')
    print(f"  {'Fuel':18} {'from':10} {'to':10} {'days':>4} {'kWh':>9} {'p/kWh':>7} {'SC p/day':>8} {'£':>8}")
    for c in body['charges']:
        fuel = (c['fuel'] or '') + (' export' if c['export'] else '')
        rate = f"{c['rate']:7.2f}" if c['rate'] is not None else f"{'-':>7}"
        print(f"  {fuel[:18]:18} {c['start']} {c['end']} {c['days']:4d} {c['kwh']:9.1f} {rate} "
              f"{c['standing'] or 0:8.2f} {(c['gross_p'] or 0) / 100:8.2f}")


async def main():
    """Sign in, download the months, print the summary and comparison."""
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        return
    settings = load_settings(sys.argv[1:])
    start = date.fromisoformat(settings['startDate']) if settings['startDate'] else None
    stop = date.fromisoformat(settings['stopDate']) if settings['stopDate'] else date.today()
    async with EonNextClient(settings['dataDir']) as client:
        await client.login()
        months = await collect(client, start, stop)
        tariffs = await client.tariffs() if re.match('^on', str(settings['tariffs']), re.IGNORECASE) else None
    print_summary(months, client)
    if tariffs:
        print_tariffs(tariffs)
    if re.match('^on', str(settings['compare']), re.IGNORECASE):
        print_compare(settings)


if __name__ == '__main__':
    asyncio.run(main())
