"""Weather-normalised PV health check (soiling / degradation) using Open-Meteo irradiance.

Usage: python analysis/pvcheck.py [config:path.json] [latitude:N] [longitude:N]
                                  [tiltDeg:N] [azimuthDeg:N] [panelWatts:N]
                                  [valuePerKwh:N] [weatherDir:DIR]

Fetches hourly GHI/DNI/DHI/air temperature for the site from the free Open-Meteo
historical archive (cached per month in weatherData/), models plane-of-array
irradiance for the panels, and compares it with the cached inverter PV records
(inverterData/day-*.json) to get a performance ratio (PR = actual / expected).
Weather cancels out, so a falling PR - especially year-on-year for the same month,
since PR has a seasonal shape - points at dirt, shading or degradation.

azimuthDeg is degrees from south, positive towards west (Open-Meteo convention).
If tiltDeg/azimuthDeg are not set they are fitted from the data (best match of the
hourly PV shape to modelled irradiance) - save the printed values to config.json.
Settings come from the "pvCheck" object in config.json, then config.local.json (git-ignored -
put latitude/longitude there, not in the public config.json), then key:value args.
"""
import glob
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from analysis.energyday import _fill_gaps  # noqa: E402

DEFAULTS = {
    'latitude': None,
    'longitude': None,
    'tiltDeg': None,
    'azimuthDeg': None,
    'panelWatts': None,       # falls back to top-level existingPvWatts, then 3115
    'valuePerKwh': 0.20,      # £ per kWh lost, for the "is cleaning worth it" line
    'weatherDir': 'weatherData',
}
LOCAL_CONFIG = 'config.local.json'   # git-ignored: keep latitude/longitude out of the public repo
ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'
HOURLY_VARS = 'shortwave_radiation,direct_normal_irradiance,diffuse_radiation,temperature_2m'
FINAL_AFTER_DAYS = 10         # archive data for a month is treated as final this long after it ends
LOCAL_TZ = ZoneInfo('Europe/London')   # Sunsynk record times are local clock time
ALBEDO = 0.2
TEMP_COEFF = -0.0035          # per °C of cell temperature above 25 °C (typical mono-Si)
CELL_HEAT = 0.03              # °C per W/m² of POA irradiance (≈ NOCT 44 °C model)
MIN_POA = 100                 # W/m²: hours below this are dominated by model error
SUNNY_DNI = 400               # W/m²: "sunny hour" threshold for the cleanest PR signal
CLIP_FRACTION = 0.97          # hours with a 5-min sample this close to the max ever are excluded


# ─── settings ────────────────────────────────────────────────────────────────

def _load_settings(argv):
    """Merge DEFAULTS < config.json "pvCheck" < key:value CLI args (keys case-insensitive)."""
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
            name = canonical[key.lower()]
            overrides[name] = value if name == 'weatherDir' else float(value)
    settings = dict(DEFAULTS)
    config = {}
    for path in (config_path, LOCAL_CONFIG):   # LOCAL_CONFIG (git-ignored) holds the site location
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                loaded = json.load(f)
            config = config or loaded
            settings.update({k: v for k, v in (loaded.get('pvCheck') or {}).items() if k in DEFAULTS})
    settings.update(overrides)
    if settings['panelWatts'] is None:
        settings['panelWatts'] = float(config.get('existingPvWatts', 3115))
    return settings


# ─── Open-Meteo archive (cached per month) ───────────────────────────────────

def _month_starts(first: date, last: date):
    d = first.replace(day=1)
    while d <= last:
        yield d
        d = (d + timedelta(days=32)).replace(day=1)


def _fetch_month(lat, lon, month_start: date, last: date) -> dict:
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    params = {'latitude': f'{lat:.2f}', 'longitude': f'{lon:.2f}',
              'start_date': month_start.isoformat(), 'end_date': min(month_end, last).isoformat(),
              'hourly': HOURLY_VARS, 'timezone': 'GMT'}
    url = f'{ARCHIVE_URL}?{urllib.parse.urlencode(params)}'
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def load_weather(settings, first: date, last: date, today: date | None = None) -> dict:
    """{utc hour-ending datetime: (ghi, dni, dhi, temp)} for first..last, using the month cache."""
    today = today or date.today()
    os.makedirs(settings['weatherDir'], exist_ok=True)
    lat, lon = float(settings['latitude']), float(settings['longitude'])
    hours = {}
    for month_start in _month_starts(first, last):
        path = os.path.join(settings['weatherDir'],
                            f"openmeteo-{lat:.2f}_{lon:.2f}-{month_start:%Y-%m}.json")
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = _fetch_month(lat, lon, month_start, last)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            if (today - month_end).days > FINAL_AFTER_DAYS:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f)
                print(f'Write to file: {path}')
        hourly = data['hourly']
        for i, t in enumerate(hourly['time']):
            row = tuple(hourly[k][i] for k in HOURLY_VARS.split(','))
            if None not in row:
                hours[datetime.fromisoformat(t).replace(tzinfo=timezone.utc)] = row
    return hours


# ─── solar geometry and plane-of-array irradiance ────────────────────────────

def solar_position(when_utc: datetime, lat: float, lon: float):
    """(zenith°, azimuth° from south, +west) using the NOAA general solar position formulas."""
    doy = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    ha = math.radians((hour * 60 + eqtime + 4 * lon) / 4 - 180)
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    zenith = math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))
    azimuth = math.degrees(math.atan2(
        math.sin(ha), math.cos(ha) * math.sin(phi) - math.tan(decl) * math.cos(phi)))
    return zenith, azimuth


def poa_irradiance(ghi, dni, dhi, zenith, sun_az, tilt, panel_az):
    """Plane-of-array W/m²: beam on the tilted plane + isotropic sky diffuse + ground reflection."""
    z, b = math.radians(zenith), math.radians(tilt)
    cos_aoi = (math.cos(z) * math.cos(b)
               + math.sin(z) * math.sin(b) * math.cos(math.radians(sun_az - panel_az)))
    beam = dni * max(0.0, cos_aoi) if zenith < 90 else 0.0
    return beam + dhi * (1 + math.cos(b)) / 2 + ghi * ALBEDO * (1 - math.cos(b)) / 2


# ─── inverter PV, binned to Open-Meteo's UTC hours ───────────────────────────

def load_pv_hours(day_files):
    """({utc hour-ending datetime: PV Wh}, {that hour: True if any 5-min sample is near clipping}).

    Open-Meteo hourly radiation is the mean over the PRECEDING hour, so a sample at
    10:20 UTC belongs to the hour labelled 11:00.
    """
    samples = []
    for path in day_files:
        day = os.path.basename(path)[4:14]
        with open(path, encoding='utf-8') as f:
            infos = json.load(f)['data']['infos']
        pv = next((i for i in infos if i['label'] == 'PV'), None)
        if pv is None:
            continue
        for r in _fill_gaps(pv['records']):
            local = datetime.fromisoformat(f"{day}T{r['time']}").replace(tzinfo=LOCAL_TZ)
            samples.append((local.astimezone(timezone.utc), max(0.0, float(r['value']))))
    peak = max((w for _, w in samples), default=0.0)
    wh, clipped = defaultdict(float), defaultdict(bool)
    for t, w in samples:
        key = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        wh[key] += w / 12
        clipped[key] |= w >= CLIP_FRACTION * peak
    return dict(wh), dict(clipped)


# ─── model ───────────────────────────────────────────────────────────────────

def build_hours(pv_wh, clipped, weather, lat, lon):
    """Daytime unclipped hours: [(utc_hour, pv_wh, ghi, dni, dhi, temp, zenith, sun_az)]."""
    rows = []
    for t, (ghi, dni, dhi, temp) in sorted(weather.items()):
        if t not in pv_wh or clipped.get(t) or ghi <= 0:
            continue
        zenith, sun_az = solar_position(t - timedelta(minutes=30), lat, lon)
        if zenith < 88:
            rows.append((t, pv_wh[t], ghi, dni, dhi, temp, zenith, sun_az))
    return rows


def expected_wh(row, tilt, panel_az, panel_watts):
    """Hour's expected Wh for a clean, new array: STC rating × POA/1000 × temperature derate."""
    _, _, ghi, dni, dhi, temp, zenith, sun_az = row
    poa = poa_irradiance(ghi, dni, dhi, zenith, sun_az, tilt, panel_az)
    cell = temp + CELL_HEAT * poa
    return panel_watts * poa / 1000 * (1 + TEMP_COEFF * (cell - 25)), poa


def fit_orientation(rows, panel_watts):
    """Grid-search tilt/azimuth maximising the correlation of hourly PV with modelled output."""
    def score(tilt, az):
        pairs = [(r[1], expected_wh(r, tilt, az, panel_watts)[0]) for r in rows]
        sa = sum(a * e for a, e in pairs)
        se = sum(e * e for _, e in pairs)
        if se == 0:
            return float('inf')
        k = sa / se
        return sum((a - k * e) ** 2 for a, e in pairs)

    best = min(((t, a) for t in range(10, 66, 5) for a in range(-120, 121, 10)),
               key=lambda p: score(*p))
    return min(((best[0] + dt, best[1] + da) for dt in range(-4, 5, 2) for da in range(-8, 9, 2)
                if 0 <= best[0] + dt <= 90), key=lambda p: score(*p))


def performance_by_day(rows, tilt, panel_az, panel_watts):
    """{local date: [pv, exp, pv_sunny, exp_sunny]} in Wh, over hours with POA >= MIN_POA."""
    days = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for row in rows:
        exp, poa = expected_wh(row, tilt, panel_az, panel_watts)
        if poa < MIN_POA:
            continue
        d = days[row[0].astimezone(LOCAL_TZ).date()]
        d[0] += row[1]
        d[1] += exp
        if row[3] >= SUNNY_DNI:
            d[2] += row[1]
            d[3] += exp
    return days


def _year_back(d: date):
    """Same calendar date a year earlier (28 Feb for 29 Feb)."""
    return d.replace(year=d.year - 1, day=min(d.day, 28) if d.month == 2 else d.day)


def summarise(days, dates):
    """(PR all hours, PR sunny hours) over the given dates."""
    tot = [sum(days[d][i] for d in dates) for i in range(4)]
    return _pr(tot[0], tot[1]), _pr(tot[2], tot[3])


def _pr(num, den):
    return num / den if den else None


def _fmt(x, spec='.3f'):
    return format(x, spec) if x is not None else '   -  '


def main(argv):
    """Fetch weather, fit/apply orientation, print monthly and year-on-year PR."""
    settings = _load_settings(argv)
    if settings['latitude'] is None or settings['longitude'] is None:
        sys.exit('Set pvCheck.latitude / pvCheck.longitude in config.local.json '
                 '(or latitude:N longitude:N).')
    lat, lon = float(settings['latitude']), float(settings['longitude'])
    watts = float(settings['panelWatts'])

    day_files = sorted(glob.glob('inverterData/day-*.json'))
    first = date.fromisoformat(os.path.basename(day_files[0])[4:14])
    last = date.fromisoformat(os.path.basename(day_files[-1])[4:14])
    pv_wh, clipped = load_pv_hours(day_files)
    weather = load_weather(settings, first, last)
    rows = build_hours(pv_wh, clipped, weather, lat, lon)
    print(f"Site {lat:.2f}, {lon:.2f}; {watts:g} Wp; {len(rows)} usable daylight hours "
          f"{first}..{max(r[0] for r in rows).date()}")

    tilt, az = settings['tiltDeg'], settings['azimuthDeg']
    if tilt is None or az is None:
        clean = [r for r in rows if r[0].date() < first + timedelta(days=120)]
        tilt, az = fit_orientation(clean, watts)
        print(f"Fitted orientation (first 120 days): tilt {tilt}°, azimuth {az:+}° from south "
              f"({'west' if az > 0 else 'east'} of south) - save as pvCheck.tiltDeg / azimuthDeg in config.json")
    else:
        print(f"Orientation: tilt {tilt:g}°, azimuth {az:+g}° from south")

    days = performance_by_day(rows, float(tilt), float(az), watts)
    by_month = defaultdict(list)
    for d in days:
        by_month[d.strftime('%Y-%m')].append(d)
    print(f"\nPerformance ratio (actual / expected for clean new panels), "
          f"hours with POA >= {MIN_POA} W/m²."
          f"\nYoY columns compare only dates present in both years.")
    print(f"{'month':8} {'PV kWh':>7} {'PR all':>7} {'PR sunny':>9} | "
          f"{'YoY all':>7} {'YoY sunny':>9} {'days':>5}")
    matched_now, matched_prev = [], []
    for key in sorted(by_month):
        dates = by_month[key]
        pr, prs = summarise(days, dates)
        now = [d for d in dates if _year_back(d) in days]
        prev = [_year_back(d) for d in now]
        matched_now += now
        matched_prev += prev
        yoy = yoys = None
        if now:
            (a, b), (c, e) = summarise(days, now), summarise(days, prev)
            yoy = a / c if a and c else None
            yoys = b / e if b and e else None
        pv_kwh = sum(days[d][0] for d in dates) / 1000
        print(f"{key:8} {pv_kwh:7.1f} {_fmt(pr):>7} {_fmt(prs):>9} | "
              f"{_fmt(yoy):>7} {_fmt(yoys):>9} "
              f"{len(now) or '':>5}")

    if matched_now:
        cur, old = summarise(days, matched_now)[1], summarise(days, matched_prev)[1]
        loss = 1 - cur / old
        year_kwh = sum(v for t, v in pv_wh.items() if t.date() > last - timedelta(days=365)) / 1000
        value = loss * year_kwh * float(settings['valuePerKwh'])
        print(f"\nSunny-hour PR on {len(matched_now)} matched dates "
              f"({min(matched_now)}..{max(matched_now)} vs a year earlier): "
              f"{old:.3f} -> {cur:.3f} ({-100 * loss:+.1f}%)")
        print(f"Last 365 days PV {year_kwh:.0f} kWh; "
              + (f"a {100 * loss:.1f}% loss ≈ {loss * year_kwh:.0f} kWh/yr ≈ "
                 f"£{value:.0f}/yr at £{settings['valuePerKwh']}/kWh"
                 if loss > 0 else "no year-on-year loss detected"))


if __name__ == '__main__':
    main(sys.argv[1:])
