"""5-minute EV charging profiles from the cached Indra charger data (indraData/).

The charger sits between the inverter's CT and the supply meter, so the Sunsynk data
never sees it: every watt it draws is either PV that the CT counted as export, or grid
import. EnergyDay adds these profiles to Load and Grid (see its ``ev_watts`` argument).

Indra keeps per-minute telemetry for only the last few months. Older sessions are
reconstructed from what each session record does keep: start/end, the off-peak/standard
kWh split and the scheduled charging window(s) (``events``). The car draws a flat
``charger_kw`` while charging, so off-peak energy is placed from the start of the
scheduled window and standard-rate energy from plug-in, minute by minute. Any energy
that doesn't fit is spread evenly over the session.

Until Indra is told the tariff, it labels every kWh "Standard", so the split means nothing for
sessions before the first one with any off-peak energy (``split_known=False``). The E.ON
meter shows those were the car's own overnight charges (Jun-Jul 2025: all at 00:00-05:00),
so their energy goes into the scheduled window (ignoring windows under
``MIN_WINDOW_MINUTES``), else from the first local midnight in the session, else from plug-in.
"""
import glob
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo('Europe/London')
DEFAULT_CHARGER_KW = 7.4       # used when no telemetry is cached to measure it from
SAMPLE_CAP_S = 300             # a telemetry sample never stands for more than 5 minutes
MIN_WINDOW_MINUTES = 30        # shorter scheduled 'events' (e.g. 23:05-23:10) aren't charging windows


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace('Z', '+00:00'))


def _slot(when: datetime):
    """(local date 'YYYY-MM-DD', 5-minute slot 'HH:MM') for an aware datetime."""
    local = when.astimezone(LOCAL_TZ)
    return local.date().isoformat(), f'{local.hour:02d}:{local.minute - local.minute % 5:02d}'


def _subtotals(session: dict) -> tuple:
    """(off-peak kWh, standard kWh) for a session record."""
    off = std = 0.0
    for sub in session.get('subTotals') or session.get('subtotals') or []:
        kwh = float(sub.get('energyImportedKwh') or 0)
        if sub['name'].lower().replace('-', '') == 'offpeak':
            off += kwh
        else:
            std += kwh
    return off, std


def telemetry_wh(rows: list) -> list:
    """[(utc datetime, Wh)] for per-minute telemetry: each sample's power held until the
    next sample (capped at SAMPLE_CAP_S; the last sample counts for one minute)."""
    times = [_utc(r['time']) for r in rows]
    out = []
    for i, row in enumerate(rows):
        span = (times[i + 1] - times[i]).total_seconds() if i + 1 < len(rows) else 60
        out.append((times[i], float(row['powerKw']) * 1000 * min(span, SAMPLE_CAP_S) / 3600))
    return out


def _first_local_midnight(start: datetime, end: datetime):
    """The first local midnight after ``start`` (UTC), or None if the session ends first."""
    local = start.astimezone(LOCAL_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), LOCAL_TZ)
    midnight = midnight.astimezone(start.tzinfo)
    return midnight if midnight < end else None


def synthesise_wh(session: dict, charger_kw: float, split_known: bool = True) -> list:
    """[(utc minute, Wh)] for a session without telemetry (see module docstring)."""
    start, end = _utc(session['start']), _utc(session['end'])
    minutes = []
    t = start.replace(second=0, microsecond=0)
    while t < end:
        minutes.append(t)
        t += timedelta(minutes=1)
    if not minutes:
        return []
    windows = [(_utc(e['start']), _utc(e['end'])) for e in session.get('events') or []]

    def in_window(m):
        return any(a <= m < b for a, b in windows)

    per_min = charger_kw * 1000 / 60
    energy = defaultdict(float)
    off_wh, std_wh = (kwh * 1000 for kwh in _subtotals(session))
    if not split_known:
        # Indra didn't know the tariff: it was all scheduled (overnight) charging.
        windows = [(a, b) for a, b in windows if (b - a) >= timedelta(minutes=MIN_WINDOW_MINUTES)]
        off_wh, std_wh = off_wh + std_wh, 0.0
        if not windows:
            midnight = _first_local_midnight(start, end)
            windows = [(midnight, end)] if midnight else [(start, end)]
    # off-peak into the scheduled window(s); standard from plug-in outside them. With no
    # schedule, everything charges straight away from plug-in.
    for wh, pool in ((off_wh, [m for m in minutes if in_window(m)] if windows else minutes),
                     (std_wh, [m for m in minutes if not in_window(m)])):
        for m in pool:
            if wh <= 0:
                break
            room = per_min - energy[m]
            if room <= 0:
                continue
            take = min(room, wh)
            energy[m] += take
            wh -= take
        if wh > 0:      # didn't fit at full power: spread the rest over the session
            for m in minutes:
                energy[m] += wh / len(minutes)
    return sorted(energy.items())


def _charger_kw(telemetry: dict) -> float:
    samples = [float(r['powerKw']) for rows in telemetry.values() for r in rows
               if float(r['powerKw']) > 1]
    return statistics.median(samples) if samples else DEFAULT_CHARGER_KW


def load_ev_profiles(data_dir: str = 'indraData', charger_kw: float | None = None) -> dict:
    """``{'YYYY-MM-DD': {'HH:MM': W}}`` (local time, 5-minute average power) for every cached
    session; telemetry when cached, else reconstructed. Missing dir -> {}."""
    sessions, telemetry = {}, {}
    for path in glob.glob(os.path.join(data_dir, 'session-*.json')):
        with open(path, encoding='utf-8') as f:
            body = json.load(f)
        if body and body.get('start') and body.get('end'):
            sessions[body['id']] = body
    for sid in sessions:
        path = os.path.join(data_dir, f'telemetry-{sid}.json')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                rows = json.load(f)
            if rows:
                telemetry[sid] = rows
    kw = charger_kw or _charger_kw(telemetry)
    # Indra only splits off-peak/standard once it knows the tariff (see module docstring).
    split_from = min((s['start'] for s in sessions.values() if _subtotals(s)[0] > 0), default=None)

    wh_by_slot = defaultdict(lambda: defaultdict(float))
    for sid, session in sessions.items():
        samples = telemetry_wh(telemetry[sid]) if sid in telemetry else synthesise_wh(
            session, kw, split_known=split_from is not None and session['start'] >= split_from)
        for when, wh in samples:
            day, slot = _slot(when)
            wh_by_slot[day][slot] += wh
    return {day: {slot: wh * 12 for slot, wh in slots.items()} for day, slots in wh_by_slot.items()}
