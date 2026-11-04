"""Download Indra EV-charger history into indraData/ JSON files.

Usage: python analysis/indradata.py [config:path.json] [startDate:YYYY-MM-DD] [stopDate:YYYY-MM-DD]
                                    [sessions:ON|OFF] [telemetry:ON|OFF] [dataDir:DIR]

Settings come from the "indra" object in config.json, then config.local.json (git-ignored;
the place for your account email, since the repo is public), then key:value args.
The email ($INDRA_EMAIL beats config) is only needed when a fresh email-link sign-in is
required; $INDRA_LINK can supply the emailed link.
"""
import asyncio
import json
import os
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis.indra_client import IndraClient, _subtotal_kwh  # noqa: E402

DEFAULTS = {
    'email': '',
    'startDate': '',
    'stopDate': '',
    'sessions': 'ON',
    'telemetry': 'ON',
    'dataDir': 'indraData',
}
LOCAL_CONFIG = 'config.local.json'
EMPTY_MONTHS_STOP = 3   # without a startDate, stop walking back after this many empty months
EARLIEST_YEAR = 2015    # safety floor for the backwards month walk
REQUEST_DELAY = 0.2     # seconds between API calls


def _read_section(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f).get('indra', {})
    except FileNotFoundError:
        return {}


def _load_settings(argv):
    """Merge DEFAULTS < config.json "indra" < config.local.json "indra" < key:value CLI args."""
    config_path = 'config.json'
    overrides = {}
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
    settings = {**DEFAULTS, **_read_section(config_path), **_read_section(LOCAL_CONFIG),
                **overrides}
    settings['email'] = os.getenv('INDRA_EMAIL') or settings['email']
    return settings


def _parse_date(value):
    return date.fromisoformat(value) if value else None


def _months_back(stop):
    y, m = stop.year, stop.month
    while y >= EARLIEST_YEAR:
        yield y, m
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)


async def collect(client, start, stop, sessions=True, telemetry=True, delay=REQUEST_DELAY):
    """Walk months from ``stop`` back to ``start`` (or until EMPTY_MONTHS_STOP empty months
    in a row), fetching each monthly report and, optionally, every session it lists plus
    its per-minute telemetry. Returns ``{(year, month): (month_body, session_count)}``."""
    async def pause():
        if delay:
            await asyncio.sleep(delay)

    months = {}
    empty_run = 0
    for y, m in _months_back(stop):
        if start and (y, m) < (start.year, start.month):
            break
        body = await client.get_month_raw(y, m) or {}
        await pause()
        if not body.get('dailyReports'):
            empty_run += 1
            if not start and empty_run >= EMPTY_MONTHS_STOP:
                break
            continue
        empty_run = 0
        count = 0
        if sessions:
            for entry in await client.get_transaction_index_raw(y, m):
                await pause()
                detail = await client.get_transaction_raw(entry['id'])
                count += 1
                if telemetry and detail:
                    await pause()
                    await client.get_telemetry_raw(entry['id'], ended=bool(detail.get('end')))
        months[(y, m)] = (body, count)
    return months


def _print_summary(months, client):
    print()
    print(f'{"Month":<8} {"Total kWh":>10} {"Off-peak":>9} {"Standard":>9} '
          f'{"Cost £":>8} {"Sessions":>9}')
    tot = {'total': 0.0, 'off_peak': 0.0, 'standard': 0.0, 'cost': 0.0}
    total_sessions = 0
    for (y, m) in sorted(months):
        body, count = months[(y, m)]
        month = {'total': 0.0, 'off_peak': 0.0, 'standard': 0.0, 'cost': 0.0}
        for day in body.get('dailyReports') or []:
            for key, value in _subtotal_kwh(day).items():
                month[key] += value
        for key in tot:
            tot[key] += month[key]
        total_sessions += count
        print(f'{y:04d}-{m:02d}  {month["total"]:>10.2f} {month["off_peak"]:>9.2f} '
              f'{month["standard"]:>9.2f} {month["cost"]:>8.2f} {count:>9}')
    print(f'{"Total":<8} {tot["total"]:>10.2f} {tot["off_peak"]:>9.2f} {tot["standard"]:>9.2f} '
          f'{tot["cost"]:>8.2f} {total_sessions:>9}')
    print(f'\nAPI calls: {client.fetched}, cache hits: {client.cached}')


async def main():
    """Log in (reusing the saved token), download/cache charge history, print a monthly summary."""
    settings = _load_settings(sys.argv[1:])
    start = _parse_date(settings['startDate'])
    stop = min(_parse_date(settings['stopDate']) or date.today(), date.today())
    link_env = os.getenv('INDRA_LINK')

    async with IndraClient(email=settings['email'] or None, data_dir=settings['dataDir'],
                           link_provider=(lambda: link_env) if link_env else None) as client:
        await client.login()
        months = await collect(client, start, stop,
                               sessions=str(settings['sessions']).upper() != 'OFF',
                               telemetry=str(settings['telemetry']).upper() != 'OFF')
        _print_summary(months, client)


if __name__ == '__main__':
    asyncio.run(main())
