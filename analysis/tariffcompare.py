"""Compare current UK electricity import tariffs against this house's projected year of import.

Runs the same day loop as collectdata.py/graphs.py (first energyPrices file, baseline PV,
EV charging included) to build a seasonally weighted year of grid import per local
half-hour: house import from SlotProjection, plus the EV's projected year (rebased on
evBasisFrom, exactly as the PROJECTED YEARLY IMPORT figure) spread over its half-hour shape.
That year is then priced against every tariff tariff_sources.py fetches, each at its own
time-of-use windows, plus the current tariff (newest period of the energyPrices file).

Import - each tariff's cost split into house energy, EV energy and a year of standing charge:
  EV as now      - every half-hour at the tariff's rate, with today's charging times
  EV re-timed    - EV energy now charged in the current off-peak window moves to the tariff's
                   cheapest rate (you'd re-schedule the charger); on smart-charging tariffs
                   (Intelligent Octopus Go, OVO Charge Anytime) all EV import gets the EV rate
  Total = house + the cheaper EV figure + standing charge.
Gas - a seasonally weighted year of gas (each calendar month's average day from the E.ON
  Next half-hourly meter cache, eondata.py, x the month's length) priced at every gas offer
  (unit rate x kWh + a year of standing charge): Kraken products with a gas tariff, energyplus
  Dual Fuel rows, the Ofgem gas cap, manualGasTariffs; the current gas tariff is the latest
  billed E.ON rate (eonData/tariffs.json) or tariffCompare.currentGas {rate, standingCharge}.
  OVERALL ranks import + gas - export: each pair with its own/same-supplier gas and with the
  cheapest gas anywhere (gas can come from a different supplier).
Export - the projected year of export per half-hour (SlotProjection, seasonal) priced at each
  export tariff (Kraken export products, the current energyPrices export rate, and
  manualExportTariffs), then the best import + export pairs, honouring same-supplier
  requirements (exportRequires).

Usage: python analysis/tariffcompare.py [key:value ...]
  Same keys as collectdata.py, plus:
  region:X          DNO region letter A-P (default: tariffCompare.region in config.local.json)
  rateDays:N        days of unit-rate history averaged per half-hour (default 14)
  tariffDataDir:DIR cache for fetched tariff data (default tariffData)
  top:N             only print the N cheapest tariffs (default: all)
"""
import asyncio
import csv
import json
import os
import re
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis.collectdata import (_load_settings, _make_battery, _iter_energy_days, _load_price_file,
                                  _load_ev_profiles)
from analysis.energy_client import SunsynkEnergyClient
from analysis.energyprices import EnergyPrices
from analysis.pricedata import PriceData
from analysis.projection import SlotProjection, latest_rates
from analysis import tariff_sources
from analysis.eon_client import load_daily_kwh, load_tariffs
from analysis.tariff_sources import DEFAULTS as TARIFF_DEFAULTS, tou_slot_rates, window_slots

LOCAL_CONFIG = 'config.local.json'


def load_tariff_settings(settings: dict, argv: list, local_path: str = LOCAL_CONFIG) -> dict:
    """tariffCompare settings: defaults < config.json < config.local.json < CLI."""
    cfg = dict(TARIFF_DEFAULTS)
    cfg.update(settings.get('tariffCompare') or {})
    try:
        with open(local_path, encoding='utf-8') as f:
            cfg.update(json.load(f).get('tariffCompare') or {})
    except FileNotFoundError:
        pass
    cfg['top'] = 0
    for arg in argv:
        key, _, value = arg.partition(':')
        key = key.lower()
        if key == 'region':
            cfg['region'] = value
        elif key == 'ratedays':
            cfg['rateDays'] = int(value)
        elif key == 'tariffdatadir':
            cfg['dataDir'] = value
        elif key == 'top':
            cfg['top'] = int(value)
    return cfg


def current_tariff(prices: dict, today: str | None = None) -> dict:
    """The energyPrices file's newest started period as a tariff (rates in p/kWh)."""
    rates = latest_rates(prices, today)
    item = next(p for p in prices['data']['prices'] if p['datefrom'] == rates['datefrom'])
    window = f"{item['offpeakStart']}-{item['offpeakStop']}"
    return {
        'supplier': 'Current', 'name': f"energyPrices from {rates['datefrom']}", 'code': 'CURRENT',
        'source': 'config', 'kind': '', 'direction': 'IMPORT',
        'slot_rates': tou_slot_rates(rates['peak'] * 100, rates['off_peak'] * 100, window),
        'standing': rates['standing'] * 100, 'smart_ev': False, 'ev_rate': None, 'note': '',
        'window': window,
    }


def current_export(prices: dict, today: str | None = None) -> dict:
    """The energyPrices file's current export rate as a flat export tariff."""
    rates = latest_rates(prices, today)
    return {
        'supplier': 'Current', 'name': f"export rate from {rates['datefrom']}",
        'code': 'CURRENT_EXPORT', 'source': 'config', 'kind': '', 'direction': 'EXPORT',
        'slot_rates': [rates['export'] * 100] * 48, 'standing': 0.0, 'smart_ev': False,
        'ev_rate': None, 'note': '', 'requires': None,
    }


def annual_profile(slots: SlotProjection, annual_import_kwh: float) -> tuple:
    """(house kWh[48], EV kWh[48]): the seasonal house year, and the EV's share of the
    projected annual import spread over the EV half-hour shape."""
    house = slots.annual_house()
    ev_year = max(0.0, annual_import_kwh - sum(house))
    return house, [ev_year * f for f in slots.ev_shape()]


def price_tariff(tariff: dict, house: list, ev: list, current_off_slots: set) -> dict:
    """Annual £ for one import tariff, split into house energy, EV energy (as now and
    re-timed, see module doc) and standing charge."""
    rates = tariff['slot_rates']
    cheapest = min(rates)
    standing = 365 * tariff['standing'] / 100
    house_cost = sum(h * r for h, r in zip(house, rates)) / 100
    ev_now = sum(e * r for e, r in zip(ev, rates)) / 100
    if tariff.get('smart_ev'):
        ev_rate = tariff['ev_rate'] if tariff.get('ev_rate') is not None else cheapest
        ev_retimed = sum(ev) * min(ev_rate, cheapest) / 100
    else:
        ev_retimed = sum(e * (cheapest if k in current_off_slots else rates[k])
                         for k, e in enumerate(ev)) / 100
    ev_retimed = min(ev_now, ev_retimed)
    cheap_slots = [k for k in range(48) if rates[k] <= cheapest + 0.005]
    ev_kwh = sum(ev)
    return {
        'house_cost': house_cost,
        'ev_now': ev_now,
        'ev_retimed': ev_retimed,
        'ev_p_per_kwh': 100 * ev_retimed / ev_kwh if ev_kwh else 0.0,
        'standing_year': standing,
        'as_now': house_cost + ev_now + standing,
        'retimed': house_cost + ev_retimed + standing,
        'cheap_kwh': (sum(house[k] + ev[k] for k in cheap_slots)
                      if len(cheap_slots) < 48 else 0.0),
    }


def price_export(tariff: dict, export: list) -> float:
    """Annual £ earned exporting ``export`` kWh per half-hour at the tariff's rates."""
    return sum(x * r for x, r in zip(export, tariff['slot_rates'])) / 100


def describe_rates(rates: list, best_high: bool = False) -> tuple:
    """(type, text): 'single' '25.60', 'tou' '9.40 00:30-05:30 / 34.71', 'dynamic' 'avg 28.1'.
    A time-of-use text leads with the cheapest rate and its window, or for export
    (``best_high``) the highest rate and its window."""
    distinct = sorted({round(r, 2) for r in rates})
    if len(distinct) == 1:
        return 'single', f'{distinct[0]:.2f}'
    if len(distinct) > 4:
        return 'dynamic', f'avg {sum(rates) / 48:.1f} ({distinct[0]:.1f}-{distinct[-1]:.1f})'
    best = max(rates) if best_high else min(rates)
    parts = []
    for start, stop in _runs([k for k in range(48) if abs(rates[k] - best) <= 0.005]):
        parts.append(f'{_slot_time(start)}-{_slot_time(stop)}')
    others = sorted({round(r, 2) for r in rates if abs(r - best) > 0.005})
    return 'tou', f"{best:.2f} {','.join(parts)} / " + '/'.join(f'{r:.2f}' for r in others)


def _runs(slots: list) -> list:
    """Contiguous [start, stop) runs of slot indices, joining a run across midnight."""
    runs = []
    for k in slots:
        if runs and runs[-1][1] == k:
            runs[-1][1] = k + 1
        else:
            runs.append([k, k + 1])
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == 48:
        runs[0][0] = runs.pop()[0]
    return runs


def _slot_time(k: int) -> str:
    k %= 48
    return f'{k // 2:02d}:{30 * (k % 2):02d}'


def compare(tariffs: list, current: dict, house: list, ev: list) -> list:
    """Priced import rows, cheapest total first; each row is the tariff plus its prices."""
    off_slots = window_slots(current['window'])
    base = price_tariff(current, house, ev, off_slots)
    rows = []
    for t in [current] + [t for t in tariffs if t.get('direction', 'IMPORT') == 'IMPORT']:
        p = price_tariff(t, house, ev, off_slots)
        kind, text = describe_rates(t['slot_rates'])
        rows.append({**t, **p, 'rate_type': kind, 'rates_text': text,
                     'vs_current': p['retimed'] - base['retimed']})
    rows.sort(key=lambda r: r['retimed'])
    return rows


def compare_exports(tariffs: list, current: dict, export: list) -> list:
    """Priced export rows, highest income first."""
    base = price_export(current, export)
    rows = []
    for t in [current] + [t for t in tariffs if t.get('direction') == 'EXPORT']:
        income = price_export(t, export)
        kind, text = describe_rates(t['slot_rates'], best_high=True)
        rows.append({**t, 'income': income, 'rate_type': kind, 'rates_text': text,
                     'vs_current': income - base})
    rows.sort(key=lambda r: -r['income'])
    return rows


def best_pairs(import_rows: list, export_rows: list, n: int = 10) -> list:
    """The ``n`` cheapest (import, export, net £) pairs, an export tariff only going with an
    import tariff from the supplier it requires (and a ``flat_import_only`` one only with a
    single-rate import tariff). A new import tariff can pair with the current
    export (a separate export supplier, e.g. Fuse); the current import only with the current
    export, and that pair is always included."""
    pairs = []
    for imp in import_rows:
        for exp in export_rows:
            current_pair = imp['code'] == 'CURRENT' and exp['code'] == 'CURRENT_EXPORT'
            if exp.get('requires') and exp['requires'] != imp['supplier']:
                continue
            if exp.get('flat_import_only') and imp.get('rate_type', 'single') != 'single':
                continue
            if imp['code'] == 'CURRENT' and not current_pair:
                continue    # the current import is shown with its own export only
            pairs.append((imp, exp, imp['retimed'] - exp['income'], current_pair))
    pairs.sort(key=lambda p: p[2])
    best = [p[:3] for p in pairs[:n]]
    best += [p[:3] for p in pairs[n:] if p[3]]
    return best


DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def annual_gas(daily: dict) -> dict:
    """A seasonally weighted gas year from ``{'YYYY-MM-DD': kWh}``: each calendar month's
    average day (over every year seen) x its length. ``seasonal`` is False (and the year is
    365 x the overall daily average) until all 12 months have data."""
    by_month = {}
    for day, kwh in daily.items():
        by_month.setdefault(int(day[5:7]) - 1, []).append(kwh)
    monthly = {m: sum(v) / len(v) * DAYS_IN_MONTH[m] for m, v in by_month.items()}
    seasonal = len(monthly) == 12
    days = sum(len(v) for v in by_month.values())
    kwh = sum(monthly.values()) if seasonal else (365 * sum(daily.values()) / days if days else 0.0)
    return {'kwh': kwh, 'monthly': monthly, 'seasonal': seasonal, 'days': days}


def current_gas(tariffs_body: dict, cfg: dict) -> dict | None:
    """The current gas tariff: the newest billed E.ON gas charge (VAT-inclusive p/kWh and
    p/day from eonData/tariffs.json), else tariffCompare.currentGas {rate, standingCharge}."""
    bills = [c for c in (tariffs_body or {}).get('charges') or []
             if (c.get('fuel') or '').lower() == 'gas' and c.get('rate') and c.get('kwh')]
    base = {'supplier': 'Current', 'code': 'CURRENT_GAS', 'source': 'config', 'kind': ''}
    if bills:
        last = max(bills, key=lambda c: c['start'])
        return tariff_sources.gas_offer({**base, 'name': f"gas billed from {last['start']}",
                                         'source': 'eonData'}, last['rate'], last['standing'])
    manual = cfg.get('currentGas')
    if manual:
        return tariff_sources.gas_offer({**base, 'name': 'gas (config)'}, manual['rate'],
                                        manual['standingCharge'])
    return None


def price_gas(gas: dict, kwh: float) -> float:
    """£ for a year: kWh at the unit rate plus 365 days of standing charge."""
    return (kwh * gas['gas_rate'] + 365 * gas['gas_standing']) / 100


def compare_gas(tariffs: list, current: dict | None, kwh: float) -> list:
    """Priced gas rows, cheapest first."""
    offers = ([current] if current else []) + [t for t in tariffs if t.get('direction') == 'GAS']
    base = price_gas(current, kwh) if current else None
    rows = [{**g, 'gas_cost': price_gas(g, kwh),
             'vs_current': price_gas(g, kwh) - base if base is not None else 0.0} for g in offers]
    rows.sort(key=lambda r: r['gas_cost'])
    return rows


def overall(pairs: list, gas_rows: list, kwh: float) -> list:
    """Every import + export pair with gas: ``own`` = the import tariff's own gas (dual fuel /
    matching gas product), else its supplier's cheapest gas, (the current pair keeps the
    current gas); ``any`` = the cheapest gas offer from any supplier. Rows (imp, exp, elec net,
    own gas row or None, any gas row, best total), cheapest best total first."""
    market = [g for g in gas_rows if g['code'] != 'CURRENT_GAS']
    cheapest = market[0] if market else None
    current = next((g for g in gas_rows if g['code'] == 'CURRENT_GAS'), None)
    out = []
    for imp, exp, net in pairs:
        if imp['code'] == 'CURRENT':
            own = current
        elif imp.get('gas'):
            own = {**imp['gas'], 'gas_cost': price_gas(imp['gas'], kwh)}
        else:
            own = next((g for g in market if g['supplier'] == imp['supplier']), None)
        options = [g for g in (own, cheapest) if g]
        if not options:
            continue
        out.append((imp, exp, net, own, cheapest, net + min(g['gas_cost'] for g in options)))
    out.sort(key=lambda r: r[5])
    return out


def print_gas(gas_year: dict, rows: list, top: int = 0):
    """PROJECTED GAS and GAS TARIFFS."""
    print('')
    note = '' if gas_year['seasonal'] else '  (provisional: not all 12 months seen yet)'
    print(f"PROJECTED GAS  {gas_year['kwh']:,.0f} kWh/year from {gas_year['days']} days of E.ON meter data{note}")
    names = 'J F M A M J J A S O N D'.split()
    print('  ' + ' '.join(f'{n:>6}' for n in names))
    print('  ' + ' '.join(f"{gas_year['monthly'][m]:6.0f}" if m in gas_year['monthly'] else f"{'-':>6}"
                          for m in range(12)) + '  kWh')
    print('')
    print('GAS TARIFFS  (£/year = kWh x unit rate + 365 x standing charge)')
    head = (f"  {'#':>3} {'Supplier':<18} {'Tariff':<40} {'Kind':<10} {'p/kWh':>7} {'p/day':>7}"
            f" {'£/year':>8} {'vs curr':>8}")
    print(head)
    print('  ' + '-' * (len(head) - 2))
    shown = rows[:top] + [r for r in rows[top:] if r['code'] == 'CURRENT_GAS'] if top else rows
    for r in shown:
        print(f"  {rows.index(r) + 1:>3} {_short(r['supplier'], 18):<18} {_short(r['name'], 40):<40}"
              f" {_short(r.get('kind') or '', 10):<10} {r['gas_rate']:7.3f} {r['gas_standing']:7.2f}"
              f" {r['gas_cost']:8.2f} {r['vs_current']:+8.2f}"
              + ('   <== current' if r['code'] == 'CURRENT_GAS' else ''))


def print_overall(rows: list, n: int = 15):  # pylint: disable=too-many-locals
    """OVERALL: electricity import + gas - export, the cheapest ``n`` (plus the current)."""
    if not rows:
        return
    base = next((r[5] for r in rows if r[0]['code'] == 'CURRENT' and r[1]['code'] == 'CURRENT_EXPORT'), None)
    shown = rows[:n] + [r for r in rows[n:] if r[0]['code'] == 'CURRENT']
    print('')
    print('OVERALL  (£/year = electricity import + gas - export; gas "own" = the tariff\'s own or'
          ' its supplier\'s gas, "any" = cheapest gas anywhere)')
    head = f"  {'Import + export':<62} {'Elec net':>9} {'Gas own':>9} {'Gas any':>9} {'Total':>9} {'vs curr':>8}"
    print(head)
    print('  ' + '-' * (len(head) - 2))
    for imp, exp, net, own, any_gas, total in shown:
        label = _short(f"{imp['supplier']} {imp['name']} + {exp['supplier']} {exp['name']}", 62)
        vs = f'{total - base:+8.2f}' if base is not None else ''
        own_s = f"{own['gas_cost']:9.2f}" if own else f"{'-':>9}"
        any_s = f"{any_gas['gas_cost']:9.2f}" if any_gas and imp['code'] != 'CURRENT' else f"{'-':>9}"
        print(f"  {label:<62} {net:9.2f} {own_s} {any_s} {total:9.2f} {vs:>8}"
              + ('   <== current' if imp['code'] == 'CURRENT' else ''))
    cheapest = next((r[4] for r in rows if r[4]), None)
    if cheapest:
        print(f"  Cheapest gas anywhere: {cheapest['supplier']} {cheapest['name']}"
              f" ({cheapest['gas_rate']:.2f}p/kWh, {cheapest['gas_standing']:.2f}p/day)")
    print('  "own" gas: the same product\'s gas tariff (dual fuel), else the supplier\'s cheapest gas offer.')


def _short(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + '…'


def print_import_table(rows: list, house: list, ev: list, derived: dict, note: str, top: int = 0):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """The IMPORT TARIFFS table: house / EV / standing split (all rows, or the cheapest ``top``
    plus the current tariff)."""
    total = sum(house) + sum(ev)
    print('')
    print(f'IMPORT TARIFFS  ({note})')
    print(f"  Projected import: {total:.0f}kWh/year = house {sum(house):.0f}kWh + EV {sum(ev):.0f}kWh"
          f"  (PROJECTED YEARLY IMPORT: {derived['annual_import_peak_kwh'] + derived['annual_import_off_peak_kwh']:.0f}kWh)")
    print('  House/EV £ = energy only; EV now = today\'s charging times, EV re-timed = overnight')
    print('  charging moved to the tariff\'s cheapest rate (smart EV tariffs: all EV at their EV rate).')
    print('  Total = house + EV re-timed + standing charge.')
    print('')
    head = (f"  {'#':>3} {'Supplier':<16} {'Tariff':<34} {'Type':<10} {'Unit rates p/kWh':<30}"
            f" {'SC p/d':>6} {'House £':>8} {'EV £ now':>9} {'EV £ re-t':>9} {'EV p/kWh':>8}"
            f" {'SC £':>7} {'Total £':>8} {'vs curr':>8}")
    print(head)
    print('  ' + '-' * (len(head) - 2))
    shown = rows[:top] if top else rows
    if top and not any(r['code'] == 'CURRENT' for r in shown):
        shown = shown + [r for r in rows if r['code'] == 'CURRENT']
    for r in shown:
        rank = rows.index(r) + 1
        print(f"  {rank:>3} {_short(r['supplier'], 16):<16} {_short(r['name'], 34):<34}"
              f" {_short(r['kind'] or r['rate_type'], 10):<10} {_short(r['rates_text'], 30):<30}"
              f" {r['standing']:6.2f} {r['house_cost']:8.2f} {r['ev_now']:9.2f} {r['ev_retimed']:9.2f}"
              f" {r['ev_p_per_kwh']:8.2f} {r['standing_year']:7.2f} {r['retimed']:8.2f}"
              f" {r['vs_current']:+8.2f}" + ('   <== current' if r['code'] == 'CURRENT' else ''))
        flags = (['smart EV'] if r.get('smart_ev') else []) + \
                ([r['note']] if r.get('note') and r['code'] != 'OFGEM_CAP' else [])
        if flags:
            print(f"  {'':>3} {'':<16}   ({'; '.join(flags)})")
    print('')
    print('  Dynamic tariffs (Agile, FreePhase Dynamic) use the average price of each half-hour over')
    print('  the recent rateDays only - a guide, not a forecast. Eligibility (smart meter, compatible')
    print('  charger/car, existing-customer deals) and exit fees are not checked.')


MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def print_export(rows: list, slots: SlotProjection, export: list, top: int = 0, check: float = 0.0):
    """PROJECTED EXPORT (annual + by month + time of day) and the EXPORT TARIFFS table."""
    total = sum(export)
    print('')
    print(f"PROJECTED EXPORT  ({'seasonally weighted year' if slots.seasonal else 'PROVISIONAL: not all months seen'};"
          f" after EV soak-up and export correction)")
    print(f'  {total:.0f}kWh/year' + (f'  (SeasonalProjection export: {check:.0f}kWh)' if check else ''))
    monthly = slots.monthly()
    print('  ' + ' '.join(f'{MONTHS[m]:>6}' for m in range(12)))
    print('  ' + ' '.join(f'{monthly[m][1]:6.0f}' if m in monthly else f"{'-':>6}" for m in range(12))
          + '   kWh export, average month')
    print('  ' + ' '.join(f'{monthly[m][0]:6.0f}' if m in monthly else f"{'-':>6}" for m in range(12))
          + '   kWh house import (excl. EV), for comparison')
    peak_share = sum(export[32:38]) / total if total else 0.0
    midday = sum(export[20:32]) / total if total else 0.0
    print(f'  Exported 10:00-16:00: {100 * midday:.0f}%   16:00-19:00: {100 * peak_share:.1f}%'
          f'  (matters for time-of-day export rates)')
    print('')
    print('EXPORT TARIFFS  (projected export priced per half-hour; no standing charge)')
    head = (f"  {'#':>3} {'Supplier':<16} {'Tariff':<40} {'Type':<10} {'Rates p/kWh':<30}"
            f" {'£/year':>8} {'vs curr':>8}  Needs import from")
    print(head)
    print('  ' + '-' * (len(head) - 2))
    shown = rows[:top] if top else rows
    if top and not any(r['code'] == 'CURRENT_EXPORT' for r in shown):
        shown = shown + [r for r in rows if r['code'] == 'CURRENT_EXPORT']
    for r in shown:
        rank = rows.index(r) + 1
        print(f"  {rank:>3} {_short(r['supplier'], 16):<16} {_short(r['name'], 40):<40}"
              f" {_short(r['kind'] or r['rate_type'], 10):<10} {_short(r['rates_text'], 30):<30}"
              f" {r['income']:8.2f} {r['vs_current']:+8.2f}  {r.get('requires') or '-'}"
              + ('   <== current' if r['code'] == 'CURRENT_EXPORT' else ''))
        if r.get('note'):
            print(f"  {'':>3} {'':<16}   ({_short(r['note'], 100)})")
    print('')
    print('  "Needs import from" is assumed from the product (exportRequires) - check before switching.')
    print('  Agile Outgoing uses recent half-hour prices, which run well below summer, when most export happens.')


def print_pairs(pairs: list):
    """BEST IMPORT + EXPORT PAIRS: net yearly cost (import total - export income)."""
    if not pairs:
        return
    base = next((net for imp, exp, net in pairs
                 if imp['code'] == 'CURRENT' and exp['code'] == 'CURRENT_EXPORT'), None)
    print('')
    print('BEST IMPORT + EXPORT PAIRS  (net £/year = import total - export income)')
    head = (f"  {'Import tariff':<44} {'Export tariff':<44} {'Import £':>9} {'Export £':>9}"
            f" {'Net £':>9} {'vs curr':>8}")
    print(head)
    print('  ' + '-' * (len(head) - 2))
    for imp, exp, net in pairs:
        vs = f'{net - base:+8.2f}' if base is not None else ''
        print(f"  {_short(imp['supplier'] + ' ' + imp['name'], 44):<44}"
              f" {_short(exp['supplier'] + ' ' + exp['name'], 44):<44}"
              f" {imp['retimed']:9.2f} {exp['income']:9.2f} {net:9.2f} {vs:>8}"
              + ('   <== current' if imp['code'] == 'CURRENT' else ''))
    print('  Export tariffs needing a supplier\'s import only pair with that supplier\'s import tariffs')
    print('  (and flat-import-only ones, e.g. E.ON Export Exclusive, not with time-of-use tariffs).')


def pair_breakdown(imp: dict, exp: dict, house: list, ev: list, export: list) -> dict:
    """kWh and £ of one import + export pair: house split into the import tariff's cheapest-rate
    half-hours ("off-peak") and the rest ("peak"), EV (re-timed, as priced), standing charge,
    export, and the net total."""
    rates = imp['slot_rates']
    cheapest = min(rates)
    tou = len({round(r, 2) for r in rates}) > 1
    off = [k for k in range(48) if tou and rates[k] <= cheapest + 0.005]
    peak = [k for k in range(48) if k not in off]
    house_off_kwh = sum(house[k] for k in off)
    house_peak_kwh = sum(house[k] for k in peak)
    house_off = sum(house[k] * rates[k] for k in off) / 100
    house_peak = sum(house[k] * rates[k] for k in peak) / 100
    ev_kwh, export_kwh = sum(ev), sum(export)
    return {
        'house_peak_kwh': house_peak_kwh, 'house_peak': house_peak,
        'house_peak_rate': 100 * house_peak / house_peak_kwh if house_peak_kwh else 0.0,
        'house_off_kwh': house_off_kwh, 'house_off': house_off,
        'house_off_rate': 100 * house_off / house_off_kwh if house_off_kwh else 0.0,
        'ev_kwh': ev_kwh, 'ev': imp['ev_retimed'], 'ev_rate': imp['ev_p_per_kwh'],
        'standing': imp['standing_year'], 'import_total': imp['retimed'],
        'export_kwh': export_kwh, 'export': exp['income'],
        'export_rate': 100 * exp['income'] / export_kwh if export_kwh else 0.0,
        'net': imp['retimed'] - exp['income'],
    }


def print_pair_details(pairs: list, house: list, ev: list, export: list, n: int = 6):
    """PAIR DETAIL: rates and the house peak/off-peak, EV, standing and export split for the
    leading ``n`` pairs (plus the current pair)."""
    shown = pairs[:n] + [p for p in pairs[n:] if p[0]['code'] == 'CURRENT']
    print('')
    print('PAIR DETAIL  (£/year; "off-peak" = the import tariff\'s cheapest-rate half-hours;'
          ' p = average p/kWh paid)')
    for imp, exp, _ in shown:
        b = pair_breakdown(imp, exp, house, ev, export)
        print(f"  {imp['supplier']} {imp['name']}  +  {exp['supplier']} {exp['name']}"
              + ('   <== current' if imp['code'] == 'CURRENT' else ''))
        print(f"    Rates:    import {imp['rates_text']} p/kWh, standing {imp['standing']:.2f}p/day;"
              f" export {exp['rates_text']} p/kWh")
        print(f"    House:    peak {b['house_peak_kwh']:5.0f}kWh £{b['house_peak']:7.2f} ({b['house_peak_rate']:5.2f}p)"
              f"   off-peak {b['house_off_kwh']:5.0f}kWh £{b['house_off']:7.2f} ({b['house_off_rate']:5.2f}p)")
        print(f"    EV:       {b['ev_kwh']:5.0f}kWh £{b['ev']:7.2f} ({b['ev_rate']:5.2f}p)"
              f"   Standing: £{b['standing']:.2f}   Import total: £{b['import_total']:.2f}")
        print(f"    Export:   {b['export_kwh']:5.0f}kWh £{b['export']:7.2f} ({b['export_rate']:5.2f}p)"
              f"   NET: £{b['net']:.2f}")


def write_csv(rows: list, path: str):
    """One CSV row per priced import or export tariff."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = ['direction', 'supplier', 'name', 'code', 'kind', 'rate_type', 'rates_text', 'standing',
              'house_cost', 'ev_now', 'ev_retimed', 'ev_p_per_kwh', 'standing_year', 'retimed',
              'income', 'gas_rate', 'gas_standing', 'gas_cost', 'vs_current', 'cheap_kwh', 'smart_ev',
              'ev_rate', 'requires', 'source', 'note']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})


async def main():  # pylint: disable=too-many-locals
    """Fetch tariffs, build the half-hourly import year, print and save the comparison."""
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        return
    settings = _load_settings(sys.argv[1:])
    cfg = load_tariff_settings(settings, sys.argv[1:])
    show_days = bool(re.match('^on', str(settings['showDays']), re.IGNORECASE))

    raw_files = settings['energyPrices']
    price_file = (raw_files if isinstance(raw_files, list) else [raw_files])[0]
    vb_raw = settings['virtualBattery']
    battery_config = (vb_raw if isinstance(vb_raw, list) else [vb_raw])[0]
    boost = settings['pvBoost'][0]
    energy_prices_data = _load_price_file(price_file)
    prices = EnergyPrices(
        energy_prices_data, _make_battery(battery_config, PriceData()),
        original_price=float(settings['originalPrice']),
        label=os.path.splitext(os.path.basename(price_file))[0],
        off_peak_baseline_kwh=float(settings['offPeakBaseline']),
        off_peak_shift_enabled=bool(re.match('^on', str(settings['offPeakShift']), re.IGNORECASE)),
        battery_enabled=False,
        ev_basis_from=str(settings['evBasisFrom']),
    )
    slots = SlotProjection(str(settings['evBasisFrom']))
    ev_profiles = _load_ev_profiles(settings)

    print(f"Fetching tariffs for region {cfg['region']} ...")
    tariffs, stats = await tariff_sources.fetch_all(cfg)
    n_export = sum(1 for t in tariffs if t.get('direction') == 'EXPORT')
    print(f"  {len(tariffs) - n_export} import + {n_export} export tariffs ({stats['calls']} requests, {stats['hits']} from today's cache"
          f" in {cfg['dataDir']}/)")

    async with SunsynkEnergyClient(os.getenv('SUNSYNK_USERNAME'), os.getenv('SUNSYNK_PASSWORD'),
                                   "https://api.sunsynk.net") as client:
        inverter = (await client.get_inverters())[0]
        async for date_str, energyday, _ in _iter_energy_days(
                client, inverter, prices, boost['pv_extra_ratio'], boost['inverterClipW'],
                str(settings['startDate']), str(settings['stopDate']), int(settings['scanFromYear']),
                show_days, {}, {}, {}, {'hits': 0, 'misses': 0},
                settings['exportCorrection'], ev_profiles):
            slots.add_day(date_str, *energyday.import_slots(), energyday.export_slots())

    derived = prices.get_derived()
    house, ev = annual_profile(
        slots, derived['annual_import_peak_kwh'] + derived['annual_import_off_peak_kwh'])
    export = slots.annual_export()
    rows = compare(tariffs, current_tariff(energy_prices_data), house, ev)
    export_rows = compare_exports(tariffs, current_export(energy_prices_data), export)
    print_import_table(rows, house, ev, derived, prices.projection_note(), cfg['top'])
    print_export(export_rows, slots, export, cfg['top'], prices.projection.annual_kwh('export'))
    pairs = best_pairs(rows, export_rows)
    print_pairs(pairs)
    print_pair_details(pairs, house, ev, export)

    gas_rows = []
    gas_daily = load_daily_kwh(cfg.get('eonDataDir', 'eonData'), 'gas')
    if gas_daily:
        gas_year = annual_gas(gas_daily)
        gas_rows = compare_gas(tariffs, current_gas(load_tariffs(cfg.get('eonDataDir', 'eonData')), cfg),
                               gas_year['kwh'])
        print_gas(gas_year, gas_rows, cfg['top'])
        all_pairs = best_pairs(rows, export_rows, n=len(rows) * len(export_rows))
        print_overall(overall(all_pairs, gas_rows, gas_year['kwh']))
    else:
        print('\nNo gas data (run analysis/eondata.py to download the E.ON meter readings).')

    path = os.path.join(PROJECT_ROOT, 'results',
                        f"_{datetime.now().strftime('%Y%m%d-%H%M%S')}-tariffs-{cfg['region']}.csv")
    write_csv(rows + export_rows + gas_rows, path)
    print(f'\nCSV: {path}')


if __name__ == '__main__':
    asyncio.run(main())
