import json
import os
import stat
from datetime import date

import pytest

from analysis.fuse_client import (
    FuseAuthError, FuseEnergyClient, is_final, load_daily_export_kwh, load_hourly_export_kwh,
    period_end,
)
from analysis.fusedata import _load_settings, _month_summary, collect
from tests.mock_fuse_server import MockFuseServer, OTP, PHONE, PREMISES_FID, TODAY


def _otp_counter():
    calls = []

    def provider():
        calls.append(1)
        return OTP
    return provider, calls


def _no_otp():
    raise AssertionError('OTP should not be requested')


def _client(base_url, data_dir, otp_provider=None, phone=PHONE):
    return FuseEnergyClient(phone=phone, data_dir=str(data_dir), base_url=base_url,
                            otp_provider=otp_provider or _otp_counter()[0], today=TODAY)


def _body(bar_types):
    bars = [{'bar': {'index': {}, 'kWh': '1', 'money': {'amount': '0'}, 'type': t}, 'breakdown': []}
            for t in bar_types]
    return {'result': {'data': {'data': {'chart': {'supplies': [{'bars': bars}] if bars else []}}}}}


# ─── is_final / period_end ───────────────────────────────────────────────────

def test_period_end():
    assert period_end(2026) == date(2026, 12, 31)
    assert period_end(2026, 2) == date(2026, 2, 28)
    assert period_end(2026, 9, 30) == date(2026, 9, 30)


def test_is_final_rules():
    today = date(2026, 10, 2)
    assert not is_final(_body(['REALISED']), today, today)                       # period not over
    assert is_final(_body(['REALISED', 'REALISED']), date(2026, 10, 1), today)   # over + realised
    assert not is_final(_body(['REALISED', 'FORECASTED']), date(2026, 10, 1), today)
    assert not is_final(_body([]), date(2026, 10, 1), today)                     # no data yet
    assert is_final(_body(['FORECASTED']), date(2026, 9, 1), today)              # past grace period


# ─── auth ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_saves_session(aiohttp_server, tmp_path):
    server = MockFuseServer(aiohttp_server)
    base_url = await server.base_url()
    provider, calls = _otp_counter()
    async with _client(base_url, tmp_path, provider) as client:
        await client.login()
    assert len(calls) == 1
    path = tmp_path / '.session.json'
    saved = json.loads(path.read_text())
    assert saved['app-auth'] in server.valid_tokens
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.asyncio
async def test_saved_session_reused_without_otp(aiohttp_server, tmp_path):
    server = MockFuseServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
    async with _client(base_url, tmp_path, _no_otp, phone=None) as client:
        await client.login()
        await client.get_month_raw(PREMISES_FID, 2026, 9)
    assert server.calls['phoneSignIn'] == 1


@pytest.mark.asyncio
async def test_expired_session_triggers_relogin(aiohttp_server, tmp_path):
    server = MockFuseServer(aiohttp_server)
    base_url = await server.base_url()
    provider, calls = _otp_counter()
    async with _client(base_url, tmp_path, provider) as client:
        await client.login()
        server.expire()
        body = await client.get_month_raw(PREMISES_FID, 2026, 9)
    assert len(body['result']['data']['data']['chart']['supplies'][0]['bars']) == 30
    assert len(calls) == 2
    assert json.loads((tmp_path / '.session.json').read_text())['app-auth'] in server.valid_tokens


@pytest.mark.asyncio
async def test_expired_saved_session_on_login(aiohttp_server, tmp_path):
    server = MockFuseServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
    server.expire()
    provider, calls = _otp_counter()
    async with _client(base_url, tmp_path, provider) as client:
        await client.login()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_bad_otp_raises(aiohttp_server, tmp_path):
    server = MockFuseServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path, lambda: '000000') as client:
        with pytest.raises(FuseAuthError):
            await client.login()
    assert not (tmp_path / '.session.json').exists()


@pytest.mark.asyncio
async def test_get_premises_fid(aiohttp_server, tmp_path):
    server = MockFuseServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
        assert await client.get_premises_fid() == PREMISES_FID


# ─── caching + collect walk ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_caches_only_final_periods(aiohttp_server, tmp_path):
    server = MockFuseServer(aiohttp_server)
    server.pending_days.add(date(2026, 9, 30))
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
        months = await collect(client, PREMISES_FID, None, TODAY, delay=0)

    assert sorted(months) == [(2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]
    files = set(os.listdir(tmp_path))
    assert 'year-2025.json' in files            # empty, but long over -> final
    assert 'year-2026.json' not in files        # current year
    assert 'contracts-2026.json' not in files
    assert 'month-2026-08.json' in files
    assert 'month-2026-09.json' not in files    # contains a still-pending day
    assert 'month-2026-10.json' not in files
    assert 'day-2026-06-01.json' in files
    assert 'day-2026-09-29.json' in files
    assert 'day-2026-09-30.json' not in files   # pending
    assert 'day-2026-10-01.json' in files
    assert 'day-2026-10-02.json' not in files   # today
    assert 'day-2026-10-03.json' not in files   # future: never fetched
    # 2 years + 1 contracts + 5 months + (30+31+31+30+2) days
    assert server.calls['premisesDisplayData'] == 2 + 5 + 124

    # Second run: only the non-final periods hit the API again.
    server.calls.clear()
    async with _client(base_url, tmp_path, _no_otp) as client:
        await client.login()
        await collect(client, PREMISES_FID, None, TODAY, delay=0)
        assert client.cached == 1 + 3 + 122     # year-2025, 3 months, all final days
    # year-2026, month-09, month-10, day-09-30, day-10-02
    assert server.calls['premisesDisplayData'] == 5
    assert server.calls['phoneSignIn'] == 0


@pytest.mark.asyncio
async def test_collect_respects_date_range_and_days_off(aiohttp_server, tmp_path):
    server = MockFuseServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
        months = await collect(client, PREMISES_FID, date(2026, 8, 15), date(2026, 9, 10), delay=0)
        assert sorted(months) == [(2026, 8), (2026, 9)]
        days = sorted(f for f in os.listdir(tmp_path) if f.startswith('day-'))
        assert days[0] == 'day-2026-08-15.json' and days[-1] == 'day-2026-09-10.json'
        assert len(days) == 17 + 10

        server.calls.clear()
        await collect(client, PREMISES_FID, None, TODAY, fetch_days=False, delay=0)
    assert not any(f.startswith('day-2026-10') for f in os.listdir(tmp_path))


def test_month_summary():
    body = {'result': {'data': {'data': {'chart': {
        'supplies': [{'realised_usage_kWh': '12.873'}],
        'total_realised_money': {'amount': '-1.67'}}}}}}
    assert _month_summary(body) == pytest.approx((12.873, 1.67))


def test_load_settings(tmp_path, monkeypatch):
    cfg = tmp_path / 'c.json'
    cfg.write_text(json.dumps({'fuse': {'premisesFid': 'abc', 'phone': '+44cfg'}}))
    monkeypatch.delenv('FUSE_PHONE', raising=False)
    s = _load_settings([f'config:{cfg}', 'startdate:2026-07-01', 'DAYS:OFF'])
    assert s['premisesFid'] == 'abc'
    assert s['phone'] == '+44cfg'
    assert s['startDate'] == '2026-07-01'
    assert s['days'] == 'OFF'
    monkeypatch.setenv('FUSE_PHONE', '+44env')
    assert _load_settings([f'config:{cfg}'])['phone'] == '+44env'


# ─── load_daily_export_kwh ───────────────────────────────────────────────────

def _write_bars(path, bars):
    """bars: list of (index dict, kWh, type)."""
    entries = [{'bar': {'index': idx, 'kWh': str(kwh), 'money': {'amount': '0'}, 'type': t},
                'breakdown': []} for idx, kwh, t in bars]
    path.write_text(json.dumps(
        {'result': {'data': {'data': {'chart': {'supplies': [{'bars': entries}]}}}}}))


def test_load_daily_export_kwh(tmp_path):
    _write_bars(tmp_path / 'month-2026-09.json', [
        ({'year': 2026, 'month': 9, 'day': 29}, 10.5, 'REALISED'),
        ({'year': 2026, 'month': 9, 'day': 30}, 9.0, 'FORECASTED'),
    ])
    # Covered by the month file: month value wins.
    _write_bars(tmp_path / 'day-2026-09-29.json', [({'hour': 12}, 99.0, 'REALISED')])
    # Not in any month file: summed from hourly REALISED bars.
    _write_bars(tmp_path / 'day-2026-10-01.json', [
        ({'hour': 11}, 1.25, 'REALISED'), ({'hour': 12}, 2.0, 'REALISED'),
        ({'hour': 13}, 5.0, 'FORECASTED')])
    # Nothing realised yet: omitted.
    _write_bars(tmp_path / 'day-2026-10-02.json', [({'hour': 12}, 1.0, 'FORECASTED')])
    (tmp_path / 'year-2026.json').write_text('{}')   # ignored
    (tmp_path / '.session.json').write_text('{}')    # ignored

    assert load_daily_export_kwh(str(tmp_path)) == {'2026-09-29': 10.5, '2026-10-01': 3.25}


def test_load_daily_export_kwh_missing_dir(tmp_path):
    assert load_daily_export_kwh(str(tmp_path / 'nope')) == {}


def test_load_hourly_export_kwh(tmp_path):
    _write_bars(tmp_path / 'day-2026-10-01.json', [
        ({'hour': 13}, 2.0, 'REALISED'), ({'hour': 12}, 1.25, 'REALISED'),
        ({'hour': 14}, 5.0, 'FORECASTED')])
    assert load_hourly_export_kwh(str(tmp_path), '2026-10-01') == [(12, 1.25), (13, 2.0)]
    assert load_hourly_export_kwh(str(tmp_path), '2026-10-02') == []
