import json
import math
from datetime import date, datetime, timezone

import pytest

from analysis.pvcheck import (
    _load_settings,
    _year_back,
    load_pv_hours,
    poa_irradiance,
    solar_position,
    summarise,
)

MISSING_CONFIG = 'config:/nonexistent/does-not-exist.json'


# ─── solar geometry ──────────────────────────────────────────────────────────

def test_solar_noon_greenwich_equinox():
    # ~12:07 UTC at 0° lon on the March equinox: sun due south, zenith ≈ latitude
    zenith, azimuth = solar_position(datetime(2026, 3, 20, 12, 7, tzinfo=timezone.utc), 51.5, 0.0)
    assert zenith == pytest.approx(51.5, abs=0.6)
    assert azimuth == pytest.approx(0.0, abs=2.0)


def test_solar_azimuth_sign_morning_east_afternoon_west():
    _, am = solar_position(datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc), 53.0, -1.0)
    _, pm = solar_position(datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc), 53.0, -1.0)
    assert am < -45 and pm > 45


def test_solar_midsummer_noon_zenith():
    # declination +23.44° ⇒ zenith ≈ 53.0 − 23.44 at solar noon (~12:06 UTC at 1.0° W)
    zenith, _ = solar_position(datetime(2026, 6, 21, 12, 6, tzinfo=timezone.utc), 53.0, -1.0)
    assert zenith == pytest.approx(29.56, abs=0.6)


# ─── plane-of-array irradiance ───────────────────────────────────────────────

def test_poa_flat_panel_is_ghi():
    # tilt 0: beam·cos(zenith) + full sky diffuse, no ground term — equals GHI when consistent
    ghi, dhi, zen = 600.0, 150.0, 40.0
    dni = (ghi - dhi) / math.cos(math.radians(zen))
    assert poa_irradiance(ghi, dni, dhi, zen, 0.0, 0.0, 0.0) == pytest.approx(ghi)


def test_poa_facing_sun_gets_full_beam():
    # panel tilted to the sun's zenith and azimuth: AOI 0 ⇒ full DNI
    poa = poa_irradiance(0.0, 800.0, 0.0, 35.0, 20.0, 35.0, 20.0)
    assert poa == pytest.approx(800.0)


def test_poa_sun_behind_panel_no_beam():
    # south-facing 40° panel, sun low in the north-west: only diffuse/ground remain
    poa = poa_irradiance(100.0, 500.0, 80.0, 85.0, 150.0, 40.0, 0.0)
    assert poa < 100.0


# ─── PV hour binning ─────────────────────────────────────────────────────────

def _write_day(tmp_path, day, records):
    path = tmp_path / f'day-{day}.json'
    path.write_text(json.dumps({'data': {'infos': [{'label': 'PV', 'records': records}]}}))
    return str(path)


def test_load_pv_hours_bst_to_utc_hour_ending(tmp_path):
    # 12:00-12:55 BST local = 11:00-11:55 UTC ⇒ Open-Meteo hour labelled 12:00 UTC
    records = [{'time': f'12:{m:02d}', 'value': '1200'} for m in range(0, 60, 5)]
    pv_wh, clipped = load_pv_hours([_write_day(tmp_path, '2026-07-01', records)])
    key = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert pv_wh == {key: pytest.approx(1200.0)}
    assert clipped[key] is True   # every sample is the max seen ⇒ flagged as clipping


def test_load_pv_hours_gmt_and_negative_clamped(tmp_path):
    records = [{'time': '10:00', 'value': '600'}, {'time': '10:05', 'value': '-5'}]
    pv_wh, _ = load_pv_hours([_write_day(tmp_path, '2026-01-10', records)])
    assert pv_wh == {datetime(2026, 1, 10, 11, 0, tzinfo=timezone.utc): pytest.approx(50.0)}


# ─── aggregation helpers ─────────────────────────────────────────────────────

def test_year_back_handles_leap_day():
    assert _year_back(date(2026, 10, 1)) == date(2025, 10, 1)
    assert _year_back(date(2028, 2, 29)) == date(2027, 2, 28)


def test_summarise_ratios():
    days = {date(2026, 7, 1): [800.0, 1000.0, 400.0, 500.0],
            date(2026, 7, 2): [900.0, 1000.0, 0.0, 0.0]}
    pr, pr_sunny = summarise(days, list(days))
    assert pr == pytest.approx(0.85)
    assert pr_sunny == pytest.approx(0.8)


def test_summarise_empty_is_none():
    assert summarise({}, []) == (None, None)


# ─── settings ────────────────────────────────────────────────────────────────

def test_settings_cli_overrides_case_insensitive():
    s = _load_settings([MISSING_CONFIG, 'latitude:53.0', 'AZIMUTHDEG:-38', 'weatherDir:wx'])
    assert s['latitude'] == 53.0
    assert s['azimuthDeg'] == -38.0
    assert s['weatherDir'] == 'wx'
