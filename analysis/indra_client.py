"""Indra EV charger client (api.indra.co.uk) with local JSON caching.

Indra has no public API; this talks to the same REST endpoints the Indra Android
app uses (reverse-engineered from its generated API client). Login is a magic
link, not a password: ``api/user/check/{email}/{deviceKey}/{os}`` makes Indra
email a sign-in link and returns a check hash; opening the link (or passing it to
``api/user/verify/{email}/{mobileHash}/{hash}``) activates it, after which
``api/user/token/{email}/{deviceKey}/{checkHash}/{os}`` returns a 30-day JWT
(renewed via ``api/authorize/refresh``). We register as our own random
``deviceKey`` so the phone app's session is untouched. Email, device key, check
hash and token are saved to ``{data_dir}/.session.json`` (mode 0600).
"""
import asyncio
import base64
import calendar
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

BASE_URL = 'https://api.indra.co.uk'
OS_ANDROID = 2                  # the app's `os` path value: 1 = iOS, 2 = Android
SESSION_FILE = '.session.json'
VERIFY_LINK_RE = re.compile(r'/mobile/verify/(?:[^/]+/[^/]+/)?([0-9A-Fa-f]+)/([0-9A-Fa-f]+)/?$')
# Charges that run past midnight (and late uploads) keep changing a month's report
# for a little while after it ends, so it's only treated as final after this.
FINAL_GRACE_DAYS = 3
REFRESH_WITHIN_DAYS = 7         # renew the JWT once it has less than this left
TOKEN_POLL_ATTEMPTS = 10
TOKEN_POLL_DELAY = 3.0          # seconds between api/user/token polls
REQUEST_TIMEOUT = 60            # seconds per request
NETWORK_RETRIES = 3             # attempts per request on timeout / connection error


class IndraAuthError(Exception):
    """The server rejected our token, or the sign-in link was never activated."""


def _q(value) -> str:
    return quote(str(value), safe='')


def token_expiry(token: str):
    """UTC expiry of a JWT from its unverified payload, or None if unreadable."""
    try:
        payload = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        return datetime.fromtimestamp(claims['exp'], tz=timezone.utc)
    except (IndexError, KeyError, ValueError):
        return None


def parse_verify_link(link: str):
    """(mobileHash, hash) from an emailed sign-in link
    ``https://indrawebapp.azurewebsites.net/mobile/verify/{env}/{email}/{mobileHash}/{hash}``."""
    match = VERIFY_LINK_RE.search(link.strip())
    if not match:
        raise ValueError(f'Not an Indra sign-in link: {link!r}')
    return match.group(1), match.group(2)


def month_is_final(year: int, month: int, today: date, grace_days: int = FINAL_GRACE_DAYS) -> bool:
    """True once a month ended more than ``grace_days`` ago (safe to reuse from the cache)."""
    end = date(year, month, calendar.monthrange(year, month)[1])
    return (today - end).days > grace_days


def _subtotal_kwh(entry: dict) -> dict:
    """{'off_peak', 'standard', 'total', 'cost'} for a daily report / transaction entry."""
    out = {'off_peak': 0.0, 'standard': 0.0}
    for sub in entry.get('subtotals') or entry.get('subTotals') or []:
        key = 'off_peak' if sub['name'].lower().replace('-', '') == 'offpeak' else 'standard'
        out[key] += float(sub.get('energyImportedKwh') or 0)
    totals = entry.get('totals') or {}
    out['total'] = float(totals.get('energyImportedKwh') or 0)
    out['cost'] = float(totals.get('costInclTax') or 0)
    return out


def load_daily_charge_kwh(data_dir: str = 'indraData') -> dict:
    """``{'YYYY-MM-DD': {'off_peak', 'standard', 'total', 'cost'}}`` from the cached
    monthly reports only (no network). Indra splits a session's energy across the
    days it was drawn on. Missing dir -> {}."""
    try:
        names = sorted(os.listdir(data_dir))
    except FileNotFoundError:
        return {}
    daily = {}
    for name in names:
        if re.fullmatch(r'month-\d{4}-\d{2}\.json', name):
            with open(os.path.join(data_dir, name), encoding='utf-8') as f:
                body = json.load(f)
            for day in body.get('dailyReports') or []:
                daily[day['date']] = _subtotal_kwh(day)
    return daily


class IndraClient:
    """Async context manager: ``async with IndraClient(...) as client:``.

    ``email`` is only needed for a fresh sign-in; ``link_provider`` is a zero-arg
    callable returning the emailed sign-in link (defaults to ``input()``; returning
    an empty string means "I opened it on my phone", so we just poll).
    ``fetched``/``cached`` count API calls vs cache hits for the run summary.
    """

    def __init__(self, email: str = None, data_dir: str = 'indraData', base_url: str = BASE_URL,
                 link_provider=None, today: date = None, poll_delay: float = TOKEN_POLL_DELAY):
        self.email = email
        self.data_dir = data_dir
        self.base_url = base_url.rstrip('/')
        self._link_provider = link_provider or (lambda: input(
            'Paste the sign-in link from the Indra email (or press Enter once you have '
            'opened it on your phone): ').strip())
        self.today = today or datetime.today().date()
        self.poll_delay = poll_delay
        self._session = None
        self._state = {}
        self.fetched = 0
        self.cached = 0

    async def __aenter__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
        return self

    async def __aexit__(self, *exc):
        await self._session.close()

    # ── auth ──────────────────────────────────────────────────────────────────

    @property
    def _session_path(self):
        return os.path.join(self.data_dir, SESSION_FILE)

    def _load_state(self):
        try:
            with open(self._session_path, encoding='utf-8') as f:
                self._state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._state = {}

    def _save_state(self):
        fd = os.open(self._session_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(self._state, f, indent=2)

    async def login(self):
        """Reuse the saved token (renewing it if it expires soon), else sign in by email link."""
        self._load_state()
        if self.email and self._state.get('email') not in (None, self.email):
            self._state = {}    # different account: start again
        if self._state.get('token'):
            try:
                expiry = token_expiry(self._state['token'])
                soon = datetime.now(timezone.utc) + timedelta(days=REFRESH_WITHIN_DAYS)
                if expiry is None or expiry < soon:
                    await self._refresh()
                else:
                    await self._get('api/devices', retry_auth=False)
                return
            except IndraAuthError:
                print('Saved Indra session has expired, signing in again.')
        await self._sign_in()

    async def _refresh(self):
        token = await self._get('api/authorize/refresh', retry_auth=False)
        if not isinstance(token, str) or not token:
            raise IndraAuthError('Token refresh returned no token')
        self._state['token'] = token
        self._save_state()

    async def _sign_in(self):
        email = (self.email or self._state.get('email')
                 or input('Enter Indra account email: ').strip())
        device_key = self._state.get('deviceKey') or str(uuid.uuid4())
        self._state = {'email': email, 'deviceKey': device_key}
        check_hash = await self._get(f'api/user/check/{_q(email)}/{_q(device_key)}/{OS_ANDROID}',
                                     auth=False)
        if not isinstance(check_hash, str) or not check_hash or 'no account' in check_hash.lower():
            raise IndraAuthError(f'Indra has no account for {email}')
        self._state['checkHash'] = check_hash
        self._save_state()
        print(f'Indra has emailed a sign-in link to {email}.')
        link = self._link_provider()
        if link:
            mobile_hash, link_hash = parse_verify_link(link)
            await self._get(f'api/user/verify/{_q(email)}/{_q(mobile_hash)}/{_q(link_hash)}',
                            auth=False)
        for _ in range(TOKEN_POLL_ATTEMPTS):
            try:
                token = await self._get(f'api/user/token/{_q(email)}/{_q(device_key)}/'
                                        f'{_q(check_hash)}/{OS_ANDROID}', auth=False)
            except IndraAuthError:
                token = None    # 400 "User Not Activated" until the link has been opened
            if isinstance(token, str) and token:
                self._state['token'] = token
                self._save_state()
                print(f'Indra login OK, session saved to {self._session_path}')
                return
            await asyncio.sleep(self.poll_delay)
        raise IndraAuthError('Sign-in link was not activated in time; run again')

    # ── HTTP ──────────────────────────────────────────────────────────────────

    async def _get(self, path: str, auth: bool = True, retry_auth: bool = True):
        """GET ``path`` (relative to base_url) and return the decoded JSON (None for 404),
        retrying timeouts / dropped connections with a short backoff."""
        for attempt in range(1, NETWORK_RETRIES + 1):
            try:
                return await self._get_once(path, auth, retry_auth)
            except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as err:
                if attempt == NETWORK_RETRIES:
                    raise
                print(f'Indra request /{path} failed ({type(err).__name__}), retrying...')
                await asyncio.sleep(2 * attempt)
        return None

    async def _get_once(self, path: str, auth: bool, retry_auth: bool):
        headers = {'Accept': 'application/json'}
        if auth:
            headers['Authorization'] = f"Bearer {self._state.get('token', '')}"
        async with self._session.get(f'{self.base_url}/{path}', headers=headers) as resp:
            if resp.status == 404:
                return None
            if resp.status in (400, 401, 403):
                text = await resp.text()
                if auth and resp.status != 400 and retry_auth:
                    print('Indra session rejected, signing in again.')
                    await self._sign_in()
                    return await self._get(path, auth, retry_auth=False)
                raise IndraAuthError(f'HTTP {resp.status} from /{path}: {text[:200]}')
            if resp.status != 200:
                raise RuntimeError(f'HTTP {resp.status} from /{path}')
            return await resp.json(content_type=None)

    # ── data ──────────────────────────────────────────────────────────────────

    async def _get_cached(self, path: str, name: str, final) -> Any:
        """Return ``{name}.json`` if cached and ``final(body)``; otherwise fetch and (re)write it,
        so the latest copy of a not-yet-final period is always on disk for the loaders."""
        file_path = os.path.join(self.data_dir, f'{name}.json')
        try:
            with open(file_path, encoding='utf-8') as f:
                body = json.load(f)
            if final(body):
                self.cached += 1
                return body
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        body = await self._get(path)
        self.fetched += 1
        if body is not None:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(body, f, indent=2)
        return body

    async def get_month_raw(self, year: int, month: int) -> dict:
        """Monthly report: totals plus one ``dailyReports`` entry per day with charging."""
        return await self._get_cached(f'api/reports/monthly/{year}/{month}',
                                      f'month-{year:04d}-{month:02d}',
                                      lambda body: month_is_final(year, month, self.today))

    async def get_transaction_index_raw(self, year: int, month: int) -> list:
        """``[{id, localStartDate}]`` for the charging sessions started in a month."""
        return await self._get_cached(f'api/reports/transactions/index/{year}/{month}',
                                      f'sessions-{year:04d}-{month:02d}',
                                      lambda body: month_is_final(year, month, self.today)) or []

    async def get_transaction_raw(self, transaction_id: str) -> dict:
        """One session: start/end (UTC), kWh/cost totals and the off-peak/standard split."""
        return await self._get_cached(f'api/reports/transactions/{_q(transaction_id)}',
                                      f'session-{transaction_id}',
                                      lambda body: bool(body.get('end')))

    async def get_telemetry_raw(self, transaction_id: str, ended: bool) -> list:
        """Per-minute ``[{time, powerKw, tariffRateType}]`` for one session (cached once ended)."""
        return await self._get_cached(f'api/reports/transactions/{_q(transaction_id)}/telemetry',
                                      f'telemetry-{transaction_id}', lambda body: ended) or []
