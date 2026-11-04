import asyncio
import base64
import json
import os
import stat
from datetime import date, datetime, timedelta, timezone

import pytest

from analysis.indra_client import (
    IndraAuthError, IndraClient, load_daily_charge_kwh, month_is_final, parse_verify_link, token_expiry,
)
from analysis.indradata import _load_settings, collect
from tests.mock_indra_server import EMAIL, LINK, MOBILE_HASH, CHECK_HASH, TODAY, MockIndraServer


def _jwt(exp: datetime) -> str:
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip('=')
    return f"{b64({'alg': 'HS256'})}.{b64({'email': EMAIL, 'exp': int(exp.timestamp())})}.sig"


def _no_link():
    raise AssertionError('sign-in link should not be requested')


def _client(base_url, data_dir, link_provider=None, email=EMAIL):
    return IndraClient(email=email, data_dir=str(data_dir), base_url=base_url,
                       link_provider=link_provider or (lambda: LINK), today=TODAY, poll_delay=0)


def _write_session(data_dir, token):
    (data_dir / '.session.json').write_text(json.dumps(
        {'email': EMAIL, 'deviceKey': 'k', 'checkHash': CHECK_HASH, 'token': token}))


# ─── pure helpers ────────────────────────────────────────────────────────────

def test_parse_verify_link():
    assert parse_verify_link(LINK) == (MOBILE_HASH, CHECK_HASH)
    assert parse_verify_link(f'https://x/mobile/verify/{MOBILE_HASH}/{CHECK_HASH}/') == (MOBILE_HASH, CHECK_HASH)
    with pytest.raises(ValueError):
        parse_verify_link('https://example.com/nope')


def test_token_expiry():
    exp = datetime(2026, 12, 4, 16, 23, 42, tzinfo=timezone.utc)
    assert token_expiry(_jwt(exp)) == exp
    assert token_expiry('not-a-jwt') is None


def test_month_is_final():
    assert not month_is_final(2026, 10, date(2026, 10, 20))
    assert not month_is_final(2026, 9, date(2026, 10, 2))    # inside the grace period
    assert month_is_final(2026, 9, date(2026, 10, 4))


# ─── auth ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sign_in_with_pasted_link_saves_session(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
    path = tmp_path / '.session.json'
    saved = json.loads(path.read_text())
    assert saved['email'] == EMAIL and saved['checkHash'] == CHECK_HASH
    assert saved['token'] in server.valid_tokens
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert server.calls['verify'] == 1


@pytest.mark.asyncio
async def test_sign_in_link_opened_on_phone_polls_until_activated(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    polls = []

    def opened_on_phone():
        polls.append(1)
        server.activate()
        return ''
    async with _client(base_url, tmp_path, opened_on_phone) as client:
        await client.login()
    assert server.calls['verify'] == 0 and server.calls['token'] == 1


@pytest.mark.asyncio
async def test_sign_in_gives_up_if_never_activated(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path, lambda: '') as client:
        with pytest.raises(IndraAuthError):
            await client.login()
    assert 'token' not in json.loads((tmp_path / '.session.json').read_text())


@pytest.mark.asyncio
async def test_unknown_email_raises(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path, _no_link, email='nobody@example.com') as client:
        with pytest.raises(IndraAuthError):
            await client.login()


@pytest.mark.asyncio
async def test_saved_token_reused_without_sign_in(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    token = _jwt(datetime.now(timezone.utc) + timedelta(days=25))
    server.valid_tokens.add(token)
    _write_session(tmp_path, token)
    async with _client(base_url, tmp_path, _no_link, email=None) as client:
        await client.login()
    assert server.calls['check'] == 0 and server.calls['refresh'] == 0
    assert server.calls['devices'] == 1


@pytest.mark.asyncio
async def test_token_near_expiry_is_refreshed(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    token = _jwt(datetime.now(timezone.utc) + timedelta(days=2))
    server.valid_tokens.add(token)
    _write_session(tmp_path, token)
    async with _client(base_url, tmp_path, _no_link) as client:
        await client.login()
    assert server.calls['refresh'] == 1 and server.calls['check'] == 0
    assert json.loads((tmp_path / '.session.json').read_text())['token'] != token


@pytest.mark.asyncio
async def test_rejected_token_triggers_sign_in(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
        server.expire()
        server.activated = False
        body = await client.get_month_raw(2026, 9)
    assert len(body['dailyReports']) == 30
    assert server.calls['check'] == 2


# ─── caching + collect walk ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_caches_final_months_and_ended_sessions(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
        months = await collect(client, None, TODAY, delay=0)

    assert sorted(months) == [(2026, 8), (2026, 9), (2026, 10)]
    assert months[(2026, 10)][1] == 1           # one session (1 Oct) so far
    files = set(os.listdir(tmp_path))
    assert {'month-2026-08.json', 'month-2026-09.json', 'month-2026-10.json'} <= files
    assert 'session-tx-2026-08-01.json' in files and 'telemetry-tx-2026-08-01.json' in files
    # walked back through Jul/Jun/May (empty) and stopped
    assert server.calls['monthly'] == 3 + 3
    assert server.calls['transaction'] == 31 + 30 + 1

    # Second run: Sep is still inside the grace period, Oct is current and the 1 Oct
    # session hasn't ended; Aug and the ended sessions come from the cache.
    server.calls.clear()
    async with _client(base_url, tmp_path, _no_link) as client:
        await client.login()
        await collect(client, None, TODAY, delay=0)
    assert server.calls['monthly'] == 2         # Sep, Oct (the empty months are final too)
    assert server.calls['index'] == 2
    assert server.calls['transaction'] == 1 and server.calls['telemetry'] == 1
    assert server.calls['check'] == 0


@pytest.mark.asyncio
async def test_collect_respects_start_date_and_sessions_off(aiohttp_server, tmp_path):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    async with _client(base_url, tmp_path) as client:
        await client.login()
        months = await collect(client, date(2026, 9, 15), TODAY, sessions=False, delay=0)
    assert sorted(months) == [(2026, 9), (2026, 10)]
    assert server.calls['index'] == 0
    assert not any(f.startswith('session-') for f in os.listdir(tmp_path))


def test_load_daily_charge_kwh(tmp_path):
    (tmp_path / 'month-2026-10.json').write_text(json.dumps({'dailyReports': [
        {'date': '2026-10-02',
         'subtotals': [{'name': 'Off-Peak', 'energyImportedKwh': 5.79},
                       {'name': 'Standard', 'energyImportedKwh': 0.5}],
         'totals': {'energyImportedKwh': 6.29, 'costInclTax': 0.36}}]}))
    (tmp_path / 'session-x.json').write_text('{}')     # ignored
    assert load_daily_charge_kwh(str(tmp_path)) == {
        '2026-10-02': {'off_peak': 5.79, 'standard': 0.5, 'total': 6.29, 'cost': 0.36}}
    assert load_daily_charge_kwh(str(tmp_path / 'nope')) == {}


def test_load_settings_local_config_and_env(tmp_path, monkeypatch):
    cfg = tmp_path / 'c.json'
    cfg.write_text(json.dumps({'indra': {'email': 'cfg@example.com', 'telemetry': 'OFF'}}))
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.local.json').write_text(json.dumps({'indra': {'email': 'local@example.com'}}))
    monkeypatch.delenv('INDRA_EMAIL', raising=False)
    s = _load_settings([f'config:{cfg}', 'startdate:2026-07-01'])
    assert s['email'] == 'local@example.com'
    assert s['telemetry'] == 'OFF' and s['startDate'] == '2026-07-01'
    monkeypatch.setenv('INDRA_EMAIL', 'env@example.com')
    assert _load_settings([f'config:{cfg}'])['email'] == 'env@example.com'


@pytest.mark.asyncio
async def test_network_error_is_retried(aiohttp_server, tmp_path, monkeypatch):
    server = MockIndraServer(aiohttp_server)
    base_url = await server.base_url()
    monkeypatch.setattr('analysis.indra_client.asyncio.sleep', _instant)
    async with _client(base_url, tmp_path) as client:
        await client.login()
        real, fails = client._get_once, []

        async def flaky(path, auth, retry_auth):
            if not fails:
                fails.append(path)
                raise asyncio.TimeoutError
            return await real(path, auth, retry_auth)
        monkeypatch.setattr(client, '_get_once', flaky)
        body = await client.get_month_raw(2026, 9)
    assert fails and len(body['dailyReports']) == 30


async def _instant(_seconds):
    return None
