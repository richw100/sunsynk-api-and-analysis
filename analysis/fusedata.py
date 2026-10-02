"""Download Fuse Energy smart-meter export data into fuseenergydata/ JSON files.

Usage: python analysis/fusedata.py [config:path.json] [startDate:YYYY-MM-DD] [stopDate:YYYY-MM-DD]
                                   [days:ON|OFF] [premisesFid:UUID] [dataDir:DIR]

Settings come from the "fuse" object in config.json, overridden by key:value args.
The phone number is read from $FUSE_PHONE (or fuse.phone in config) and is only
needed when a fresh SMS login is required; $FUSE_OTP can supply the code.
"""
import asyncio
import json
import os
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis.fuse_client import FuseEnergyClient, iter_bars  # noqa: E402

DEFAULTS = {
    'phone': '',
    'premisesFid': '',
    'startDate': '',
    'stopDate': '',
    'days': 'ON',
    'dataDir': 'fuseenergydata',
}
EARLIEST_YEAR = 2015   # safety floor for the backwards year walk
REQUEST_DELAY = 0.2    # seconds between API calls


def _load_settings(argv):
    """Merge DEFAULTS < config.json "fuse" object < key:value CLI args (keys case-insensitive)."""
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
    try:
        with open(config_path, encoding='utf-8') as f:
            fuse_cfg = json.load(f).get('fuse', {})
    except FileNotFoundError:
        fuse_cfg = {}
    settings = {**DEFAULTS, **fuse_cfg, **overrides}
    settings['phone'] = os.getenv('FUSE_PHONE') or settings['phone']
    return settings


def _parse_date(value):
    return date.fromisoformat(value) if value else None


def _bar_date(index):
    return date(index['year'], index.get('month', 1), index.get('day', 1))


def _month_summary(body):
    """(realised export kWh, realised £ credit) for a month display-data response."""
    chart = body['result']['data']['data']['chart']
    kwh = sum(float(s.get('realised_usage_kWh') or 0) for s in chart.get('supplies') or [])
    money = -float((chart.get('total_realised_money') or {}).get('amount') or 0)
    return kwh, money


async def collect(client, premises_fid, start, stop, fetch_days=True, delay=REQUEST_DELAY):
    """Walk year -> month -> day views from ``stop`` back to ``start``/first data.

    Returns ``{(year, month): month_body}`` for the summary. Future (FORECASTED)
    months/days are skipped; cached files mean re-runs only hit the API for
    periods that weren't final last time.
    """
    async def pause():
        if delay:
            await asyncio.sleep(delay)

    months = {}
    for year in range(stop.year, EARLIEST_YEAR - 1, -1):
        if start and year < start.year:
            break
        year_body = await client.get_year_raw(premises_fid, year)
        month_idx = sorted({(b['index']['year'], b['index']['month'])
                            for b in iter_bars(year_body)})
        if not month_idx:
            break   # no supply before this year
        await client.get_contracts_raw(premises_fid, year)
        await pause()
        for y, m in month_idx:
            if date(y, m, 1) > stop or (start and (y, m) < (start.year, start.month)):
                continue
            month_body = await client.get_month_raw(premises_fid, y, m)
            months[(y, m)] = month_body
            await pause()
            if not fetch_days:
                continue
            for day_bar in iter_bars(month_body):
                day = _bar_date(day_bar['index'])
                if day > stop or (start and day < start):
                    continue
                await client.get_day_raw(premises_fid, day.year, day.month, day.day)
                await pause()
    return months


def _print_summary(months, client):
    print()
    print(f'{"Month":<8} {"Export kWh":>11} {"Credit £":>9}')
    total_kwh = total_money = 0.0
    for (y, m) in sorted(months):
        kwh, money = _month_summary(months[(y, m)])
        total_kwh += kwh
        total_money += money
        print(f'{y:04d}-{m:02d}  {kwh:>11.3f} {money:>9.2f}')
    print(f'{"Total":<8} {total_kwh:>11.3f} {total_money:>9.2f}')
    print(f'\nAPI calls: {client.fetched}, cache hits: {client.cached}')


async def main():
    """Log in (reusing the saved session), download/cache data, print a monthly summary."""
    settings = _load_settings(sys.argv[1:])
    start = _parse_date(settings['startDate'])
    stop = min(_parse_date(settings['stopDate']) or date.today(), date.today())
    otp_env = os.getenv('FUSE_OTP')

    async with FuseEnergyClient(
            phone=settings['phone'] or None,
            data_dir=settings['dataDir'],
            otp_provider=(lambda: otp_env) if otp_env else None) as client:
        await client.login()
        premises_fid = settings['premisesFid'] or await client.get_premises_fid()
        print(f'Premises: {premises_fid}')
        months = await collect(client, premises_fid, start, stop,
                               fetch_days=str(settings['days']).upper() != 'OFF')
        _print_summary(months, client)


if __name__ == '__main__':
    asyncio.run(main())
