"""Minimal stand-in for the Fuse Energy web app's tRPC endpoints.

Export supply exists from June 2026; "today" is TODAY. Bars before TODAY are
REALISED, the rest FORECASTED; dates in ``pending_days`` stay FORECASTED to
mimic late-arriving smart-meter data.
"""
import calendar
import json
from collections import Counter
from datetime import date

from aiohttp import web

TODAY = date(2026, 10, 2)
PREMISES_FID = '54944c88-1989-4765-ab08-c0c85c548dcb'
SUPPLY_FID = 'd40012c9-70e1-4fce-8fac-53c4aaa3ba27'
PHONE = '+447700900123'
OTP = '123456'
FIRST_MONTH = (2026, 6)


def _bar(index, kwh, bar_type):
    return {'bar': {'index': index, 'money': {'amount': f'{-kwh * 0.13:.2f}', 'currency': 'GBP'},
                    'kWh': f'{kwh:.3f}', 'type': bar_type}, 'breakdown': []}


class MockFuseServer:
    def __init__(self, aiohttp_server):
        self.aiohttp_server = aiohttp_server
        self.calls = Counter()
        self.valid_tokens = set()
        self.pending_days = set()
        self._token_n = 0
        self.app = web.Application()
        self.app.router.add_post('/api/trpc/phoneSignIn', self.phone_sign_in)
        self.app.router.add_post('/api/trpc/verifyOtp', self.verify_otp)
        self.app.router.add_get('/api/trpc/getUnreadNotificationsCount', self.notifications)
        self.app.router.add_get('/api/trpc/premisesDisplayData', self.display_data)
        self.app.router.add_get('/api/trpc/premisesHistoricalContracts', self.contracts)
        self.app.router.add_get('/app', self.app_rsc)

    async def base_url(self):
        server = await self.aiohttp_server(self.app)
        return str(server.make_url('')).rstrip('/')

    def expire(self):
        """Invalidate every issued app-auth token (session expiry)."""
        self.valid_tokens.clear()

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _result(data):
        return web.json_response({'result': {'data': data}})

    @staticmethod
    def _unauthorized():
        return web.json_response({'error': {'message': 'UNAUTHORIZED', 'code': -32001,
                                            'data': {'code': 'UNAUTHORIZED', 'httpStatus': 401}}},
                                 status=401)

    def _authed(self, request):
        return request.cookies.get('app-auth') in self.valid_tokens

    def _bar_type(self, d):
        return 'REALISED' if d < TODAY and d not in self.pending_days else 'FORECASTED'

    def _chart(self, bars):
        realised = [b for b in bars if b['bar']['type'] == 'REALISED']
        kwh = sum(float(b['bar']['kWh']) for b in realised)
        money = {'amount': f'{sum(float(b["bar"]["money"]["amount"]) for b in realised):.2f}', 'currency': 'GBP'}
        supplies = [{'supply_fid': SUPPLY_FID, 'supply_type': 'ELEC_EXPORT', 'bars': bars,
                     'realised_usage_kWh': f'{kwh:.3f}', 'realised_money': money}] if bars else []
        return {'chart': {'supplies': supplies, 'total_bars': [b['bar'] for b in bars],
                          'total_realised_money': money}}

    # ── handlers ──────────────────────────────────────────────────────────────

    async def phone_sign_in(self, request):
        self.calls['phoneSignIn'] += 1
        body = await request.json()
        resp = self._result({'challenge_type': 'PHONE_OTP', 'auth_flow_token': None,
                             'data': {'phone_number': body['phone']}})
        resp.set_cookie('session_id', 'sess-1')
        resp.set_cookie('otp', 'otp-for-' + body['phone'])
        return resp

    async def verify_otp(self, request):
        self.calls['verifyOtp'] += 1
        body = await request.json()
        if body.get('code') != OTP or request.cookies.get('otp') != 'otp-for-' + PHONE:
            return self._unauthorized()
        self._token_n += 1
        token = f'token-{self._token_n}'
        self.valid_tokens.add(token)
        resp = self._result({'challenge_type': 'AUTHORIZED', 'auth_flow_token': None,
                             'data': {'access_token': '[n/a]', 'refresh_token': '[n/a]'}})
        resp.set_cookie('app-auth', token)
        resp.del_cookie('otp')
        return resp

    async def notifications(self, request):
        self.calls['getUnreadNotificationsCount'] += 1
        return self._result({'count': 0}) if self._authed(request) else self._unauthorized()

    async def display_data(self, request):
        if not self._authed(request):
            return self._unauthorized()
        req = json.loads(request.query['input'])
        assert req['premisesFid'] == PREMISES_FID
        idx = req['index']
        self.calls['premisesDisplayData'] += 1
        y = idx['year']
        if 'day' in idx:
            d = date(y, idx['month'], idx['day'])
            bars = [_bar({**idx, 'hour': h}, 1.0 if 9 <= h <= 15 else 0.0, self._bar_type(d)) for h in range(24)]
        elif 'month' in idx:
            m = idx['month']
            bars = [_bar({'year': y, 'month': m, 'day': d}, 10.0, self._bar_type(date(y, m, d)))
                    for d in range(1, calendar.monthrange(y, m)[1] + 1)]
        else:
            bars = [_bar({'year': y, 'month': m}, 300.0,
                         self._bar_type(date(y, m, calendar.monthrange(y, m)[1])))
                    for m in range(1, 13) if (y, m) >= FIRST_MONTH]
        return self._result({'data': {**self._chart(bars), 'index': idx}})

    async def contracts(self, request):
        if not self._authed(request):
            return self._unauthorized()
        self.calls['premisesHistoricalContracts'] += 1
        return self._result({'contracts': {'contracts': [{'supply_fid': SUPPLY_FID, 'from_date_uk': '2026-06-25'}]}})

    async def app_rsc(self, request):
        assert request.headers.get('RSC') == '1'
        return web.Response(text='1:["$","$L20",null,{"premises":[{"premises":{"id":"' + PREMISES_FID +
                                 '","has_active_meters":true}}]}]\n')
