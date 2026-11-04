"""Minimal stand-in for the Indra charger API (api.indra.co.uk).

Charging exists from FIRST_MONTH; "today" is TODAY. One overnight session per day,
started at 18:00 UTC and split 2 kWh standard + 8 kWh off-peak across that day and
the next. Sign-in needs the emailed link to be "opened" (``activate()``) before
api/user/token returns a token.
"""
import calendar
from collections import Counter
from datetime import date, datetime, timedelta

from aiohttp import web

TODAY = date(2026, 10, 2)
EMAIL = 'driver@example.com'
FIRST_MONTH = (2026, 8)
CHECK_HASH = 'ABC123'
MOBILE_HASH = 'DEF456'
LINK = f'https://indrawebapp.azurewebsites.net/mobile/verify/production/{EMAIL}/{MOBILE_HASH}/{CHECK_HASH}'


def _sub(name, kwh):
    return {'name': name, 'costInclTax': round(kwh * (0.04 if name == 'Off-Peak' else 0.25), 2),
            'energyExportedKwh': 0, 'energyImportedKwh': kwh, 'energyKwh': kwh, 'rangeMiles': kwh * 3}


def _entry(subs, key='subtotals'):
    kwh = sum(s['energyImportedKwh'] for s in subs)
    return {key: subs, 'totals': {'costInclTax': round(sum(s['costInclTax'] for s in subs), 2),
                                  'energyExportedKwh': 0, 'energyImportedKwh': kwh, 'energyKwh': kwh}}


class MockIndraServer:
    def __init__(self, aiohttp_server):
        self.aiohttp_server = aiohttp_server
        self.calls = Counter()
        self.valid_tokens = set()
        self.activated = False
        self._token_n = 0
        self.app = web.Application()
        r = self.app.router
        r.add_get('/api/user/check/{email}/{key}/{os}', self.check)
        r.add_get('/api/user/verify/{email}/{mobile}/{hash}', self.verify)
        r.add_get('/api/user/token/{email}/{key}/{hash}/{os}', self.token)
        r.add_get('/api/authorize/refresh', self.refresh)
        r.add_get('/api/devices', self.devices)
        r.add_get('/api/reports/monthly/{y}/{m}', self.monthly)
        r.add_get('/api/reports/transactions/index/{y}/{m}', self.index)
        r.add_get('/api/reports/transactions/{id}/telemetry', self.telemetry)
        r.add_get('/api/reports/transactions/{id}', self.transaction)

    async def base_url(self):
        server = await self.aiohttp_server(self.app)
        return str(server.make_url('')).rstrip('/')

    def activate(self):
        """The user opened the emailed link on their phone."""
        self.activated = True

    def expire(self):
        self.valid_tokens.clear()

    def issue(self, prefix='token'):
        """A fresh valid token; JWT-shaped with a far-future exp unless ``prefix`` says otherwise."""
        self._token_n += 1
        token = f'{prefix}-{self._token_n}'
        self.valid_tokens.add(token)
        return token

    # ── helpers ───────────────────────────────────────────────────────────────

    def _authed(self, request):
        auth = request.headers.get('Authorization', '')
        return auth.startswith('Bearer ') and auth[7:] in self.valid_tokens

    @staticmethod
    def _unauthorized():
        return web.Response(status=401)

    @staticmethod
    def _has_data(d):
        return FIRST_MONTH <= (d.year, d.month) and d < TODAY

    # ── auth handlers ─────────────────────────────────────────────────────────

    async def check(self, request):
        self.calls['check'] += 1
        assert request.match_info['os'] == '2'
        if request.match_info['email'] != EMAIL:
            return web.json_response('no account')
        return web.json_response(CHECK_HASH)

    async def verify(self, request):
        self.calls['verify'] += 1
        if request.match_info['mobile'] == MOBILE_HASH and request.match_info['hash'] == CHECK_HASH:
            self.activated = True
            return web.json_response('User verified')
        return web.json_response({'statusCode': 'BadRequest', 'errorMessage': 'Bad link'}, status=400)

    async def token(self, request):
        self.calls['token'] += 1
        if not self.activated or request.match_info['hash'] != CHECK_HASH:
            return web.json_response({'statusCode': 'BadRequest', 'error': 101,
                                      'errorMessage': 'User Not Activated'}, status=400)
        return web.json_response(self.issue())

    async def refresh(self, request):
        self.calls['refresh'] += 1
        if not self._authed(request):
            return self._unauthorized()
        return web.json_response(self.issue())

    async def devices(self, request):
        self.calls['devices'] += 1
        return web.json_response([{'deviceUID': 'dev-1'}]) if self._authed(request) else self._unauthorized()

    # ── data handlers ─────────────────────────────────────────────────────────

    async def monthly(self, request):
        if not self._authed(request):
            return self._unauthorized()
        self.calls['monthly'] += 1
        y, m = int(request.match_info['y']), int(request.match_info['m'])
        days = []
        for d in range(1, calendar.monthrange(y, m)[1] + 1):
            day = date(y, m, d)
            if self._has_data(day):
                days.append({'date': day.isoformat(), **_entry([_sub('Off-Peak', 8.0), _sub('Standard', 2.0)])})
        return web.json_response({'yearMonth': f'{y:04d}-{m:02d}', 'dailyReports': days})

    async def index(self, request):
        if not self._authed(request):
            return self._unauthorized()
        self.calls['index'] += 1
        y, m = int(request.match_info['y']), int(request.match_info['m'])
        return web.json_response([{'id': f'tx-{date(y, m, d).isoformat()}', 'localStartDate': f'{date(y, m, d)}T00:00:00'}
                                  for d in range(1, calendar.monthrange(y, m)[1] + 1)
                                  if self._has_data(date(y, m, d))])

    def _start(self, tx_id):
        return datetime.fromisoformat(tx_id[3:]) + timedelta(hours=18)

    async def transaction(self, request):
        if not self._authed(request):
            return self._unauthorized()
        self.calls['transaction'] += 1
        start = self._start(request.match_info['id'])
        end = start + timedelta(hours=14)
        return web.json_response({'id': request.match_info['id'], 'start': start.isoformat() + 'Z',
                                  'end': end.isoformat() + 'Z' if end.date() < TODAY else None,
                                  **_entry([_sub('Off-Peak', 8.0), _sub('Standard', 2.0)], 'subTotals')})

    async def telemetry(self, request):
        if not self._authed(request):
            return self._unauthorized()
        self.calls['telemetry'] += 1
        start = self._start(request.match_info['id'])
        return web.json_response([{'time': (start + timedelta(minutes=i)).isoformat() + 'Z',
                                   'powerKw': 7.0, 'tariffRateType': 1} for i in range(3)])
