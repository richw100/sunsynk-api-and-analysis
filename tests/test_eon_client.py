"""eon_client.py / eondata.py: Auth0 PKCE + refresh, Kraken GraphQL paging, cache rules and loaders."""
import base64
import json
import os
import stat
import time
import urllib.parse
from datetime import date

import pytest
from aiohttp import web

from analysis.eon_client import (
    EonAuthError, EonNextClient, authorize_url, jwt_expiry, load_daily_kwh, load_half_hourly,
    load_readings, month_bounds, month_is_final, parse_callback, pkce_pair, readings_to_slots)
from analysis.eondata import collect, compare_monthly, load_settings, months_back, zero_filled

TODAY = date(2026, 11, 10)


def _jwt(exp: float) -> str:
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=').decode()
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp})}.sig"


# ---------------------------------------------------------------- pure helpers

def test_pkce_and_authorize_url():
    verifier, challenge = pkce_pair()
    assert len(verifier) >= 43 and len(challenge) == 43
    q = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_url(challenge, 'st8')).query)
    assert q['code_challenge'] == [challenge] and q['code_challenge_method'] == ['S256']
    assert q['state'] == ['st8'] and 'offline_access' in q['scope'][0]
    assert q['redirect_uri'][0].startswith('com.eonnextmobile.app.auth0://')


def test_parse_callback():
    url = 'com.eonnextmobile.app.auth0://auth.eonnext.com/android/com.eonnextmobile.app/callback?code=abc&state=st8'
    assert parse_callback(' ' + url + '\n', 'st8') == 'abc'
    with pytest.raises(EonAuthError, match='state'):
        parse_callback(url, 'other')
    with pytest.raises(EonAuthError, match='denied'):
        parse_callback('x://y/callback?error=access_denied&error_description=denied&state=st8', 'st8')


def test_jwt_expiry():
    assert jwt_expiry(_jwt(1234.0)) == 1234.0
    assert jwt_expiry('not-a-jwt') == 0.0


def test_month_bounds_are_local_and_final_rule():
    assert month_bounds(2026, 7) == ('2026-07-01T00:00:00+01:00', '2026-08-01T00:00:00+01:00')
    assert month_bounds(2026, 12) == ('2026-12-01T00:00:00+00:00', '2027-01-01T00:00:00+00:00')
    assert not month_is_final(2026, 10, date(2026, 11, 5))
    assert month_is_final(2026, 10, date(2026, 11, 6))


def test_readings_to_slots_local_days_and_long_readings():
    slots = readings_to_slots([
        ['2026-07-14T23:30:00+00:00', 1.0, 1800],     # 00:30 BST on the 15th
        ['2026-01-15T00:00:00+00:00', 2.0, 3600],     # an hour: split over two slots
    ])
    assert slots['2026-07-15'][1] == 1.0
    assert slots['2026-01-15'][0] == slots['2026-01-15'][1] == 1.0


def test_loaders_drop_implausible_half_hours(tmp_path):
    body = {'kind': 'export', 'month': '2025-07', 'final': True, 'readings': [
        ['2025-07-01T00:00:00+01:00', 99.8, 1800], ['2025-07-01T12:00:00+01:00', 1.5, 1800]]}
    (tmp_path / 'export-2025-07.json').write_text(json.dumps(body))
    keep, dropped = load_readings(str(tmp_path), 'export')
    assert len(keep) == 1 and dropped[0][1] == 99.8
    assert load_daily_kwh(str(tmp_path), 'export') == {'2025-07-01': 1.5}
    assert load_half_hourly(str(tmp_path), 'gas') == {}


def test_eondata_helpers(tmp_path):
    assert list(months_back(date(2026, 2, 10), date(2025, 12, 1))) == [(2026, 2), (2026, 1), (2025, 12)]
    assert zero_filled({'2026-01-01': 1.0, '2026-01-03': 2.0}) == {
        '2026-01-01': 1.0, '2026-01-02': 0.0, '2026-01-03': 2.0}
    r = compare_monthly({'2026-01-01': 5.0, '2026-01-02': 6.0},
                        {'inv': {'2026-01-01': 3.0}, 'ev': {'2026-01-01': 1.5, '2026-01-02': 0.0}})
    assert dict(r['2026-01']) == {'days': 1, 'meter': 5.0, 'inv': 3.0, 'ev': 1.5}
    local = tmp_path / 'local.json'
    local.write_text(json.dumps({'eon': {'dataDir': 'x'}}))
    s = load_settings(['config:/nonexistent.json', 'startDate:2026-01-01'], str(local))
    assert s['dataDir'] == 'x' and s['startDate'] == '2026-01-01' and s['compare'] == 'ON'


# ---------------------------------------------------------------- mock Auth0 + Kraken

class MockEon:
    """/oauth/token (authorization_code + refresh_token) and /graphql (accounts + paged
    measurements; 2 readings per page). Only the newest issued ID token is accepted."""

    def __init__(self):
        self.calls = []
        self.valid = set()
        self.n = 0
        self.app = web.Application()
        self.app.router.add_post('/oauth/token', self.token)
        self.app.router.add_post('/graphql', self.graphql)

    def _issue(self):
        self.n += 1
        tok = _jwt(time.time() + 86400) + str(self.n)
        self.valid = {tok}
        return tok

    async def token(self, request):
        body = await request.json()
        self.calls.append(body['grant_type'])
        if body['grant_type'] == 'authorization_code':
            if body.get('code') != 'good-code' or not body.get('code_verifier'):
                return web.json_response({'error': 'invalid_grant', 'error_description': 'bad code'}, status=403)
            return web.json_response({'id_token': self._issue(), 'refresh_token': 'rt1',
                                      'access_token': 'opaque', 'expires_in': 86400})
        if body.get('refresh_token') != 'rt1':
            return web.json_response({'error': 'invalid_grant', 'error_description': 'expired'}, status=403)
        return web.json_response({'id_token': self._issue(), 'access_token': 'opaque', 'expires_in': 86400})

    async def graphql(self, request):
        body = await request.json()
        self.calls.append('graphql')
        if request.headers.get('Authorization') not in self.valid:
            return web.json_response({'errors': [{'message': 'Signature of the JWT has expired.',
                                                  'extensions': {'errorCode': 'KT-CT-1124'}}]})
        if 'viewer' in body['query']:
            return web.json_response({'data': {'viewer': {'accounts': [
                {'properties': [{'id': 'P-EXP', 'electricityMeterPoints': [{'direction': 'EXPORT'}], 'gasMeterPoints': []}]},
                {'properties': [{'id': 'P-IMP', 'electricityMeterPoints': [{'direction': 'IMPORT'}],
                                 'gasMeterPoints': [{'supplyEndDate': None}]}]}]}}})
        v = body['variables']
        readings = [{'node': {'value': '0.5', 'unit': 'kwh', 'startAt': f'2026-10-01T0{i}:00:00+01:00',
                              'durationInSeconds': 1800}} for i in range(5)] if v['id'] == 'P-IMP' and v['s'].startswith('2026-10') else []
        start = int(v['after'] or 0)
        page = readings[start:start + 2]
        return web.json_response({'data': {'property': {'measurements': {
            'pageInfo': {'hasNextPage': start + 2 < len(readings), 'endCursor': str(start + 2)},
            'edges': page}}}})


async def _client(aiohttp_server, tmp_path, mock, pasted=None):
    server = await aiohttp_server(mock.app)
    base = str(server.make_url('')).rstrip('/')

    def provider(url):
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['state'][0]
        provider.urls.append(url)
        return pasted or f'x://cb?code=good-code&state={state}'
    provider.urls = []
    client = EonNextClient(str(tmp_path), callback_provider=provider, today=TODAY,
                           auth_url=base, graphql_url=base + '/graphql')
    return client, provider


@pytest.mark.asyncio
async def test_sign_in_saves_private_session_then_reuses_it(aiohttp_server, tmp_path):
    mock = MockEon()
    client, provider = await _client(aiohttp_server, tmp_path, mock)
    async with client:
        await client.login()
    path = tmp_path / '.session.json'
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text())['auth0']['refresh_token'] == 'rt1'
    assert mock.calls == ['authorization_code'] and len(provider.urls) == 1
    client2, provider2 = await _client(aiohttp_server, tmp_path, mock)
    async with client2:
        await client2.login()
    assert mock.calls == ['authorization_code'] and not provider2.urls


@pytest.mark.asyncio
async def test_expired_id_token_is_refreshed_without_sign_in(aiohttp_server, tmp_path):
    mock = MockEon()
    (tmp_path / '.session.json').write_text(json.dumps({'auth0': {'id_token': _jwt(time.time() - 10),
                                                                  'refresh_token': 'rt1'}}))
    client, provider = await _client(aiohttp_server, tmp_path, mock)
    async with client:
        await client.login()
        assert await client.properties() == {'export': 'P-EXP', 'import': 'P-IMP', 'gas': 'P-IMP'}
    assert mock.calls[0] == 'refresh_token' and not provider.urls
    assert json.loads((tmp_path / '.session.json').read_text())['auth0']['refresh_token'] == 'rt1'


@pytest.mark.asyncio
async def test_rejected_token_mid_run_refreshes_once(aiohttp_server, tmp_path):
    mock = MockEon()
    client, _ = await _client(aiohttp_server, tmp_path, mock)
    async with client:
        await client.login()
        mock.valid = set()                      # server-side expiry
        await client.properties()
    assert mock.calls == ['authorization_code', 'graphql', 'refresh_token', 'graphql']


@pytest.mark.asyncio
async def test_bad_callback_code_raises(aiohttp_server, tmp_path):
    mock = MockEon()
    client, _ = await _client(aiohttp_server, tmp_path, mock)

    def bad(url):
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['state'][0]
        return f'x://cb?code=wrong&state={state}'
    client._callback_provider = bad  # pylint: disable=protected-access
    async with client:
        with pytest.raises(EonAuthError, match='invalid_grant'):
            await client.login()


@pytest.mark.asyncio
async def test_month_paging_cache_and_collect(aiohttp_server, tmp_path):
    mock = MockEon()
    client, _ = await _client(aiohttp_server, tmp_path, mock)
    async with client:
        await client.login()
        body = await client.get_month('import', 2026, 10)
        assert len(body['readings']) == 5 and body['final']
        gql = mock.calls.count('graphql')
        assert gql == 1 + 3                      # accounts + 3 pages
        again = await client.get_month('import', 2026, 10)
        assert again == body and mock.calls.count('graphql') == gql and client.cached == 1
        cur = await client.get_month('gas', 2026, 11)          # current month: never final
        assert not cur['final'] and (tmp_path / 'gas-2026-11.json').exists()
        months = await collect(client, date(2026, 9, 1), date(2026, 10, 1))
    assert months[(2026, 10)]['import'] == pytest.approx(2.5)
    assert months[(2026, 9)] == {'import': 0.0, 'export': 0.0, 'gas': 0.0}
    assert load_half_hourly(str(tmp_path), 'import')['2026-10-01'][2] == 0.5


# ---------------------------------------------------------------- tariffs and bills

def test_agreements_from_flattens_and_drops_revoked():
    from analysis.eon_client import agreements_from
    t = {'displayName': 'X', 'productCode': 'P', 'tariffCode': 'E-1R-P-L'}
    data = {'viewer': {'accounts': [
        {'properties': [{'electricityMeterPoints': [{'direction': 'EXPORT', 'agreements': [
            {'validFrom': '2025-06-30T00:00:00+01:00', 'validTo': None, 'isRevoked': False, 'tariff': t}]}],
            'gasMeterPoints': []}]},
        {'properties': [{'electricityMeterPoints': [{'direction': 'IMPORT', 'agreements': [
            {'validFrom': '2026-02-28T00:00:00+00:00', 'validTo': '2027-02-28T00:00:00+00:00', 'isRevoked': False, 'tariff': t},
            {'validFrom': '2025-10-10T00:00:00+01:00', 'validTo': '2026-02-28T00:00:00+00:00', 'isRevoked': False, 'tariff': t},
            {'validFrom': '2025-09-01T00:00:00+01:00', 'validTo': None, 'isRevoked': True, 'tariff': t}]}],
            'gasMeterPoints': [{'agreements': [{'validFrom': '2025-06-19T00:00:00+01:00', 'validTo': None,
                                                'isRevoked': False, 'tariff': t}]}]}]}]}}
    out = agreements_from(data)
    assert [(a['meter'], a['from'], a['to']) for a in out] == [
        ('import', '2025-10-10', '2026-02-28'), ('import', '2026-02-28', '2027-02-28'),
        ('export', '2025-06-30', ''), ('gas', '2025-06-19', '')]


def test_charges_from_rates_include_vat_and_skip_reversed():
    from analysis.eon_client import charges_from
    nodes = [
        {'title': 'Electricity', 'isReversed': False, 'isExport': False, 'amounts': {'net': 9444, 'tax': 472, 'gross': 9916},
         'consumption': {'startDate': '2026-01-20', 'endDate': '2026-02-19', 'quantity': '492.03',
                         'usageCost': 8057, 'supplyCharge': 1860}},
        {'title': 'Electricity', 'isReversed': True, 'amounts': {}, 'consumption': {
            'startDate': '2026-01-20', 'endDate': '2026-02-19', 'quantity': '1', 'usageCost': 1, 'supplyCharge': 1}},
        {'title': 'Electricity', 'isReversed': False, 'isExport': False, 'amounts': {'net': -3397, 'tax': 0, 'gross': -3397},
         'consumption': {'startDate': '2026-06-05', 'endDate': '2026-06-24', 'quantity': '205.83',
                         'usageCost': -3397, 'supplyCharge': 0}},
        {'title': 'Fee', 'isReversed': False, 'amounts': {}},
    ]
    out = charges_from(nodes)
    assert len(out) == 2
    assert out[0]['days'] == 31 and out[0]['standing'] == pytest.approx(60.0)
    assert out[0]['rate'] == pytest.approx(8057 / 492.03)
    assert out[1]['export'] and out[1]['rate'] == pytest.approx(-16.504, abs=1e-3)


def test_rate_changes_local_days_and_boundary_artefacts():
    from analysis.eon_client import rate_changes

    def row(frm, v, pm=None):
        return {'valid_from': frm, 'value_inc_vat': v, 'payment_method': pm}
    rows = [
        row('2026-02-28T00:00:00Z', 7.5), row('2026-02-28T06:00:00Z', 30.422),
        row('2026-03-31T23:00:00Z', 3.993), row('2026-04-01T05:00:00Z', 26.915),   # 1 Apr local (BST)
        row('2026-10-25T06:00:00Z', 26.915),                                       # one-rate artefact
        row('2026-04-01T05:00:00Z', 99.0, 'NON_DIRECT_DEBIT'),
        row(None, 1.0),                                                            # open start
    ]
    out = rate_changes(rows, '2026-02-28', '2027-02-28')
    assert out == [('2026-02-28', [1.0, 7.5, 30.422]), ('2026-04-01', [3.993, 26.915])]
    assert rate_changes([row('2026-01-01T00:00:00Z', 5.0)], '2026-02-01', '') == [('2026-02-01', [5.0])]
