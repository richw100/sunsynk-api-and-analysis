"""Fuse Energy (SEG export supplier) web-app client with local JSON caching.

Fuse has no public API; this talks to the same tRPC endpoints the web app at
www.fuseenergy.com uses. Login is phone number + SMS one-time code, so it can't
run fully unattended — instead the auth cookies (``app-auth`` lasts a year) are
saved to ``{data_dir}/.session.json`` and reused, and the OTP prompt only
appears on the first run or after the session expires.
"""
import calendar
import json
import os
import re
import uuid
from datetime import date, datetime

import aiohttp
from yarl import URL

BASE_URL = 'https://www.fuseenergy.com'
APP_VERSION = '0.5.390'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')
SESSION_FILE = '.session.json'
# A period that ended more than this many days ago is cached even if some of its
# bars never became REALISED, so a permanently non-final day isn't refetched forever.
FINAL_GRACE_DAYS = 14

_PREMISES_RE = re.compile(r'"premises":\{"id":"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"')


class FuseAuthError(Exception):
    """The server rejected our session (expired/missing cookies or bad OTP)."""


def period_end(year: int, month: int = None, day: int = None) -> date:
    """Last calendar day covered by a year/month/day display-data index."""
    if day is not None:
        return date(year, month, day)
    if month is not None:
        return date(year, month, calendar.monthrange(year, month)[1])
    return date(year, 12, 31)


def iter_bars(body: dict):
    """Yield every ``bar`` dict (index, money, kWh, type) from a display-data response."""
    chart = body['result']['data']['data']['chart']
    for supply in chart.get('supplies') or []:
        for entry in supply.get('bars') or []:
            yield entry['bar']


def _realised_bars(path: str):
    with open(path, encoding='utf-8') as f:
        body = json.load(f)
    return [b for b in iter_bars(body) if b.get('type') == 'REALISED']


def load_daily_export_kwh(data_dir: str = 'fuseenergydata') -> dict:
    """``{'YYYY-MM-DD': export kWh}`` from the local cache only (no network).

    Month files give daily bars; days not covered by any month file (e.g. the
    current month, whose month file isn't cached until it's final) are filled by
    summing that day's hourly bars. Only REALISED bars count. Missing dir -> {}.
    """
    try:
        names = sorted(os.listdir(data_dir))
    except FileNotFoundError:
        return {}
    daily = {}
    for name in names:
        if re.fullmatch(r'month-\d{4}-\d{2}\.json', name):
            for b in _realised_bars(os.path.join(data_dir, name)):
                idx = b['index']
                daily[f"{idx['year']:04d}-{idx['month']:02d}-{idx['day']:02d}"] = float(b['kWh'])
    for name in names:
        match = re.fullmatch(r'day-(\d{4}-\d{2}-\d{2})\.json', name)
        if match and match.group(1) not in daily:
            bars = _realised_bars(os.path.join(data_dir, name))
            if bars:
                daily[match.group(1)] = sum(float(b['kWh']) for b in bars)
    return daily


def load_hourly_export_kwh(data_dir: str, date_str: str) -> list:
    """``[(hour, export kWh), ...]`` (REALISED bars, sorted by hour) for one
    ``'YYYY-MM-DD'`` from the cached ``day-*.json``; ``[]`` if not cached."""
    try:
        bars = _realised_bars(os.path.join(data_dir, f'day-{date_str}.json'))
    except FileNotFoundError:
        return []
    return sorted((b['index']['hour'], float(b['kWh'])) for b in bars)


def is_final(body: dict, end: date, today: date, grace_days: int = FINAL_GRACE_DAYS) -> bool:
    """True if a display-data response is safe to cache permanently.

    Smart-meter data arrives late, so "the period is in the past" isn't enough:
    every bar must also be REALISED (not FORECASTED/estimated). Periods older than
    ``grace_days`` are treated as final regardless.
    """
    if end >= today:
        return False
    if (today - end).days > grace_days:
        return True
    bars = list(iter_bars(body))
    return bool(bars) and all(b.get('type') == 'REALISED' for b in bars)


class FuseEnergyClient:
    """Async context manager: ``async with FuseEnergyClient(...) as client:``.

    ``phone`` is only needed when a fresh SMS login is required; ``otp_provider``
    is a zero-arg callable returning the SMS code (defaults to ``input()``).
    ``fetched``/``cached`` count API calls vs cache hits for the run summary.
    """

    def __init__(self, phone: str = None, data_dir: str = 'fuseenergydata',
                 base_url: str = BASE_URL, otp_provider=None, today: date = None):
        self.phone = phone
        self.data_dir = data_dir
        self.base_url = base_url.rstrip('/')
        self._otp_provider = otp_provider or (lambda: input('Enter Fuse SMS code: ').strip())
        self.today = today or datetime.today().date()
        self._session = None
        self.fetched = 0
        self.cached = 0

    async def __aenter__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        # unsafe=True lets the jar keep cookies for IP-address hosts (mock server in tests).
        self._session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        return self

    async def __aexit__(self, *exc):
        await self._session.close()

    # ── auth ──────────────────────────────────────────────────────────────────

    @property
    def _session_path(self):
        return os.path.join(self.data_dir, SESSION_FILE)

    def _load_session(self) -> bool:
        try:
            with open(self._session_path, encoding='utf-8') as f:
                cookies = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        self._session.cookie_jar.update_cookies(cookies, response_url=URL(self.base_url))
        return 'app-auth' in cookies

    def _save_session(self):
        cookies = {name: morsel.value
                   for name, morsel in self._session.cookie_jar.filter_cookies(URL(self.base_url)).items()
                   if name in ('session_id', 'app-auth')}
        fd = os.open(self._session_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, indent=2)

    async def login(self):
        """Reuse saved cookies if they still work, otherwise do the phone + OTP sign-in."""
        if self._load_session():
            try:
                await self._trpc_get('getUnreadNotificationsCount', retry_auth=False)
                return
            except FuseAuthError:
                print('Saved Fuse session has expired, signing in again.')
        await self._sign_in()

    async def _sign_in(self):
        if not self.phone:
            self.phone = input('Enter Fuse phone number (e.g. +447...): ').strip()
        self._session.cookie_jar.clear()
        body = await self._trpc_post('phoneSignIn', {'phone': self.phone})
        if body.get('challenge_type') != 'PHONE_OTP':
            raise FuseAuthError(f'Unexpected phoneSignIn challenge: {body.get("challenge_type")}')
        code = self._otp_provider()
        body = await self._trpc_post('verifyOtp', {'code': code})
        if body.get('challenge_type') != 'AUTHORIZED':
            raise FuseAuthError(f'OTP not accepted (challenge_type={body.get("challenge_type")})')
        self._save_session()
        print(f'Fuse login OK, session saved to {self._session_path}')

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _headers(self, referer_path='/app'):
        return {
            'X-Fuse-App-Version': APP_VERSION,
            'X-Request-Id': str(uuid.uuid4()),
            'Timezone': 'Europe/London',
            'User-Agent': USER_AGENT,
            'Accept': '*/*',
            'Accept-Language': 'en-GB,en;q=0.9',
            'Content-Type': 'application/json',
            'Origin': self.base_url,
            'Referer': self.base_url + referer_path,
        }

    @staticmethod
    async def _parse_trpc(resp) -> dict:
        if resp.status in (401, 403):
            raise FuseAuthError(f'HTTP {resp.status} from {resp.url.path}')
        try:
            body = await resp.json(content_type=None)
        except (json.JSONDecodeError, aiohttp.ContentTypeError) as err:
            raise RuntimeError(f'Non-JSON response (HTTP {resp.status}) from {resp.url.path}') from err
        if 'error' in body:
            err = body['error'].get('json', body['error'])
            data = err.get('data') or {}
            if data.get('code') in ('UNAUTHORIZED', 'FORBIDDEN') or data.get('httpStatus') in (401, 403):
                raise FuseAuthError(err.get('message', 'unauthorized'))
            raise RuntimeError(f'Fuse API error from {resp.url.path}: {err.get("message", err)}')
        if resp.status != 200:
            raise RuntimeError(f'HTTP {resp.status} from {resp.url.path}')
        return body['result']['data']

    async def _trpc_post(self, procedure: str, payload: dict) -> dict:
        async with self._session.post(f'{self.base_url}/api/trpc/{procedure}', json=payload,
                                      headers=self._headers('/')) as resp:
            return await self._parse_trpc(resp)

    async def _trpc_get(self, procedure: str, input_obj: dict = None, retry_auth: bool = True) -> dict:
        params = {'input': json.dumps(input_obj, separators=(',', ':'))} if input_obj is not None else None
        try:
            async with self._session.get(f'{self.base_url}/api/trpc/{procedure}', params=params,
                                         headers=self._headers()) as resp:
                return await self._parse_trpc(resp)
        except FuseAuthError:
            if not retry_auth:
                raise
            print('Fuse session rejected, signing in again.')
            await self._sign_in()
            return await self._trpc_get(procedure, input_obj, retry_auth=False)

    # ── data ──────────────────────────────────────────────────────────────────

    async def get_premises_fid(self) -> str:
        """Scrape the first premises ID from the /app React Server Components payload."""
        headers = self._headers()
        headers['RSC'] = '1'
        async with self._session.get(f'{self.base_url}/app', headers=headers) as resp:
            text = await resp.text()
        match = _PREMISES_RE.search(text)
        if not match:
            raise RuntimeError('Could not find a premises ID on /app; set "premisesFid" under "fuse" in config.json')
        return match.group(1)

    async def _get_cached(self, procedure: str, input_obj: dict, name: str, final) -> dict:
        """Return ``{name}.json`` if cached, else fetch; write the file only if ``final(body)``."""
        path = os.path.join(self.data_dir, f'{name}.json')
        try:
            with open(path, encoding='utf-8') as f:
                body = json.load(f)
            self.cached += 1
            return body
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        body = {'result': {'data': await self._trpc_get(procedure, input_obj)}}  # raw tRPC envelope
        self.fetched += 1
        if final(body):
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(body, f, indent=2)
            print(f'Write to file: {name}.json')
        return body

    async def _get_display_data(self, premises_fid: str, index: dict, name: str) -> dict:
        end = period_end(index['year'], index.get('month'), index.get('day'))
        return await self._get_cached('premisesDisplayData', {'premisesFid': premises_fid, 'index': index},
                                      name, lambda body: is_final(body, end, self.today))

    async def get_year_raw(self, premises_fid: str, year: int) -> dict:
        """Monthly export bars for ``year``."""
        return await self._get_display_data(premises_fid, {'year': year}, f'year-{year:04d}')

    async def get_month_raw(self, premises_fid: str, year: int, month: int) -> dict:
        """Daily export bars for ``year``-``month``."""
        return await self._get_display_data(premises_fid, {'year': year, 'month': month},
                                            f'month-{year:04d}-{month:02d}')

    async def get_day_raw(self, premises_fid: str, year: int, month: int, day: int) -> dict:
        """Hourly export bars for one day."""
        return await self._get_display_data(premises_fid, {'year': year, 'month': month, 'day': day},
                                            f'day-{year:04d}-{month:02d}-{day:02d}')

    async def get_contracts_raw(self, premises_fid: str, year: int) -> dict:
        """Export tariff contract periods for ``year`` (cached once the year is over)."""
        return await self._get_cached('premisesHistoricalContracts',
                                      {'premisesFid': premises_fid, 'year': year},
                                      f'contracts-{year:04d}',
                                      lambda body: period_end(year) < self.today)
