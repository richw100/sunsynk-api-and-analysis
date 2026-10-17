"""Check the exportCorrection calibration against the Fuse smart meter.

Usage: python analysis/exportcheck.py [config:path.json] [startDate:YYYY-MM-DD]
                                      [splitDate:YYYY-MM-DD] [fuseDataDir:DIR]

Network-free: reads only cached inverterData/day-*.json and fuseenergydata/ files, so
refresh both first (fusedata.py, then collectdata.py over the new dates). For every date
present in both caches it prints raw inverter export, the corrected estimate (config.json
exportCorrection) and the Fuse meter reading; then period totals before/after splitDate,
and a least-squares refit of gain/standby W to show whether the calibration has drifted.
"""
import glob
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis import fuse_client  # noqa: E402
from analysis.collectdata import _export_correction_for  # noqa: E402
from analysis.energyday import _fill_gaps  # noqa: E402

DEFAULTS = {
    'config': 'config.json',
    'startDate': '2026-08-07',   # first day of the original calibration window
    'splitDate': '2026-10-01',   # days from here on are reported as "new" (after the fit)
    'fuseDataDir': 'fuseenergydata',
}


def _load_settings(argv):
    settings = dict(DEFAULTS)
    canonical = {k.lower(): k for k in DEFAULTS}
    for arg in argv:
        key, sep, value = arg.partition(':')
        if sep and key.lower() in canonical:
            settings[canonical[key.lower()]] = value
    return settings


def _day_rows(settings, cfg):
    """[(date, raw_kwh, export_wh_samples, est_kwh, fuse_kwh, standby_w)], one per cached date."""
    fuse = fuse_client.load_daily_export_kwh(settings['fuseDataDir'])
    rows = []
    for path in sorted(glob.glob('inverterData/day-*.json')):
        date = os.path.basename(path)[4:14]
        if date not in fuse or date < settings['startDate']:
            continue
        with open(path, encoding='utf-8') as f:
            infos = json.load(f)['data']['infos']
        grid = next(i for i in infos if i['label'] == 'Grid')
        samples = [-float(r['value']) / 12 for r in _fill_gaps(grid['records'])
                   if float(r['value']) < 0]
        corr = _export_correction_for(date, cfg)
        est = sum(corr.split(wh)[0] for wh in samples) / 1000
        rows.append((date, sum(samples) / 1000, samples, est, fuse[date], corr.standby_w))
    return rows


def _summary(label, rows):
    if not rows:
        return
    raw, est, meter = (sum(r[i] for r in rows) for i in (1, 3, 4))
    print(f"{label:22} days={len(rows):3} raw={raw:8.2f} est={est:8.2f} fuse={meter:8.2f} "
          f"err={100 * (est - meter) / meter:+.2f}%  fuse/raw={meter / raw:.4f}")


def _fit(label, rows, cfg):
    """Grid-search gain/standby minimising squared daily kWh error (charger-on days only)."""
    if not rows:
        return

    def sse(gain, standby_w):
        return sum((sum(max(0.0, gain * wh - standby_w / 12) for wh in r[2]) / 1000 - r[4]) ** 2
                   for r in rows)

    g0, s0 = min(((g / 1000, s) for g in range(900, 1001, 2) for s in range(0, 41, 2)),
                 key=lambda p: sse(*p))
    gain, standby = min(((g0 + dg / 1000, s0 + ds) for dg in range(-2, 3) for ds in range(-2, 3)),
                        key=lambda p: sse(*p))
    gain_only = min((g / 1000 for g in range(900, 1001)), key=lambda g: sse(g, 0))
    cur_g, cur_s = float(cfg['gain']), float(cfg['chargerStandbyW'])
    n = len(rows)
    print(f"{label:22} best gain={gain:.3f} standby={standby:.0f}W"
          f" rmse={(sse(gain, standby) / n) ** .5:.3f} kWh"
          f" | current ({cur_g:g}, {cur_s:g}W) rmse={(sse(cur_g, cur_s) / n) ** .5:.3f}"
          f" | gain-only {gain_only:.3f} rmse={(sse(gain_only, 0) / n) ** .5:.3f}")


def main(argv):
    """Print the per-day table, period totals and refits."""
    settings = _load_settings(argv)
    with open(settings['config'], encoding='utf-8') as f:
        cfg = json.load(f)['exportCorrection']
    rows = _day_rows(settings, cfg)
    split = settings['splitDate']

    print(f"{'date':10} {'raw':>7} {'est':>7} {'fuse':>7} {'err%':>6}")
    for date, raw, _, est, meter, standby_w in rows:
        err = 100 * (est - meter) / meter if meter else 0.0
        note = '  (charger off)' if standby_w == 0 else ''
        print(f"{date} {raw:7.2f} {est:7.2f} {meter:7.2f} {err:6.1f}{note}")

    print()
    _summary(f"before {split}", [r for r in rows if r[0] < split])
    _summary(f"from {split}", [r for r in rows if r[0] >= split])
    _summary("all", rows)
    print()
    _fit(f"before {split}", [r for r in rows if r[5] > 0 and r[0] < split], cfg)
    _fit(f"from {split}", [r for r in rows if r[5] > 0 and r[0] >= split], cfg)
    _fit("all", [r for r in rows if r[5] > 0], cfg)


if __name__ == '__main__':
    main(sys.argv[1:])
