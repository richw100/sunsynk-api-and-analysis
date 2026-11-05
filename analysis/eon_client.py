"""E.ON Next smart-meter data client (half-hourly electricity import/export and gas).

E.ON Next runs on Kraken (api.eonnext-kraken.energy), but its GraphQL API no longer accepts an
email/password ``obtainKrakenToken``. The E.ON Next Android app (React Native) signs in with
Auth0 at auth.eonnext.com (authorization code + PKCE, client ``kN3SN5h3...``, connection
``prod-eon-next-uk-customers``, redirect to the app's ``com.eonnextmobile.app.auth0://`` scheme)
and then sends the Auth0 **ID token** itself as the Kraken ``Authorization`` header
(``sessionKind: 'auth0'``). This client does the same:

* First run: prints the Auth0 sign-in URL; the user signs in in a desktop browser (DevTools
  Network tab open with "Preserve log", since the browser can't open the app's scheme) and
  pastes the ``...callback?code=...`` URL back (or sets ``$EON_CALLBACK``). The code is
  exchanged for Auth0 tokens with the PKCE verifier.
* Later runs: the saved refresh token (``offline_access``) renews the 24 h ID token via
  ``grant_type=refresh_token``; a fresh sign-in is only needed if that is rejected.

Tokens live in ``{data_dir}/.session.json`` (mode 0600, git-ignored). The data
(``property.measurements``, RAW_INTERVAL = half-hourly kWh) is cached as one file per meter
and month: ``{kind}-YYYY-MM.json`` with kind ``import`` / ``export`` / ``gas``, holding
``[[startAt ISO, kWh], ...]``. A month file is rewritten on every run until the month ended
more than ``FINAL_GRACE_DAYS`` ago (smart-meter data arrives late and is sometimes backfilled),
after which it is reused without a request. Account numbers, MPAN/MPRN and the address are
never written to the cache or printed.
"""
import base64
import glob
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp

AUTH0_DOMAIN = 'https://auth.eonnext.com'
AUTH0_CLIENT_ID = 'kN3SN5h3H30hxpOjlhgmGeMfELKgn5Xg'
AUTH0_CONNECTION = 'prod-eon-next-uk-customers'
REDIRECT_URI = 'com.eonnextmobile.app.auth0://auth.eonnext.com/android/com.eonnextmobile.app/callback'
SCOPE = 'openid profile email offline_access'
GRAPHQL_URL = 'https://api.eonnext-kraken.energy/v1/graphql/'
SESSION_FILE = '.session.json'
FINAL_GRACE_DAYS = 5
REFRESH_WITHIN_SECONDS = 600     # renew the ID token when it has less than this left
REQUEST_TIMEOUT = 60
PAGE_SIZE = 500
LOCAL_TZ = ZoneInfo('Europe/London')
KINDS = ('import', 'export', 'gas')
# Largest believable half-hour: a 100 A single-phase supply is ~23 kW (11.5 kWh/30 min); a big
# combi boiler ~35 kW. Larger values are meter catch-ups (e.g. the export meter's first two
# readings, ~100 kWh each) and are dropped when loading (the cache keeps the raw readings).
MAX_KWH_PER_HALF_HOUR = {'import': 12.0, 'export': 12.0, 'gas': 20.0}

ACCOUNTS_QUERY = '''{ viewer { accounts { ... on AccountType { properties { id
  electricityMeterPoints { direction supplyEndDate }
  gasMeterPoints { supplyEndDate } } } } } }'''

MEASUREMENTS_QUERY = '''query($id: ID!, $f: [UtilityFiltersInput!], $s: DateTime, $e: DateTime, $after: String) {
  property(id: $id) { measurements(first: %d, after: $after, startAt: $s, endAt: $e,
      timezone: "Europe/London", utilityFilters: $f) {
    pageInfo { hasNextPage endCursor }
    edges { node { value unit ... on IntervalMeasurementType { startAt durationInSeconds } } } } } }''' % PAGE_SIZE

FILTERS = {
    'import': [{'electricityFilters': {'readingFrequencyType': 'RAW_INTERVAL', 'readingDirection': 'CONSUMPTION'}}],
    'export': [{'electricityFilters': {'readingFrequencyType': 'RAW_INTERVAL', 'readingDirection': 'GENERATION'}}],
    'gas': [{'gasFilters': {'readingFrequencyType': 'RAW_INTERVAL'}}],
}


AGREEMENTS_QUERY = """{ viewer { accounts { ... on AccountType { properties {
  electricityMeterPoints { direction agreements(includeInactive: true) { validFrom validTo isRevoked
    tariff { ... on TariffType { displayName productCode tariffCode } } } }
  gasMeterPoints { agreements(includeInactive: true) { validFrom validTo isRevoked
    tariff { displayName productCode tariffCode } } } } } } } }"""

CHARGES_QUERY = """query($after: String) { viewer { accounts { ... on AccountType {
  transactions(first: 100, after: $after, transactionTypes: [ENERGY_CHARGES]) {
    pageInfo { hasNextPage endCursor }
    edges { node { postedDate title isReversed amounts { net tax gross }
      ... on Charge { isExport consumption { startDate endDate quantity unit usageCost supplyCharge } } } } } } } } }"""

PRODUCTS_URL = 'https://api.eonnext-kraken.energy/v1/products/'
TARIFFS_FILE = 'tariffs.json'


class EonAuthError(Exception):
    """Sign-in, refresh or token rejection failed."""


# ---------------------------------------------------------------- pure helpers

def pkce_pair() -> tuple:
    """(code_verifier, S256 code_challenge)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b'=').decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


def authorize_url(challenge: str, state: str, domain: str = AUTH0_DOMAIN) -> str:
    """The Auth0 sign-in URL (the E.ON Next app's client, PKCE S256)."""
    return f'{domain}/authorize?' + urllib.parse.urlencode({
        'response_type': 'code', 'client_id': AUTH0_CLIENT_ID, 'connection': AUTH0_CONNECTION,
        'redirect_uri': REDIRECT_URI, 'scope': SCOPE, 'code_challenge': challenge,
        'code_challenge_method': 'S256', 'state': state})


def parse_callback(url: str, state: str) -> str:
    """The authorization code from a pasted ``...callback?code=...&state=...`` URL."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url.strip()).query)
    if 'error' in query:
        raise EonAuthError(f"Sign-in failed: {query.get('error_description', query['error'])[0]}")
    if query.get('state', [None])[0] != state:
        raise EonAuthError('The pasted URL is from a different sign-in attempt (state mismatch)')
    code = query.get('code', [None])[0]
    if not code:
        raise EonAuthError('No ?code= in the pasted URL')
    return code


def jwt_expiry(token: str) -> float:
    """``exp`` (epoch seconds) of a JWT, or 0 if it can't be read."""
    try:
        seg = token.split('.')[1]
        return float(json.loads(base64.urlsafe_b64decode(seg + '=' * (-len(seg) % 4)))['exp'])
    except (IndexError, KeyError, ValueError):
        return 0.0


def month_bounds(year: int, month: int) -> tuple:
    """Local (Europe/London) start of the month and of the next, as ISO strings with offsets."""
    start = datetime(year, month, 1, tzinfo=LOCAL_TZ)
    nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=LOCAL_TZ)
    return start.isoformat(), nxt.isoformat()


def month_is_final(year: int, month: int, today: date, grace_days: int = FINAL_GRACE_DAYS) -> bool:
    """True once the month ended at least `grace_days` ago (late readings have arrived)."""
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (today - nxt).days >= grace_days


def readings_to_slots(readings: list) -> dict:
    """``[[startAt, kWh], ...]`` -> ``{'YYYY-MM-DD': [48 kWh]}`` by local date and local
    half-hour (as EnergyDay.import_slots buckets 'HH:MM'); a reading longer than 30 min is
    spread evenly over its half-hours."""
    days = {}
    for start_at, kwh, *rest in readings:
        duration = rest[0] if rest and rest[0] else 1800
        start = datetime.fromisoformat(start_at).astimezone(LOCAL_TZ)
        parts = max(1, round(duration / 1800))
        for i in range(parts):
            t = (start + timedelta(minutes=30 * i)).astimezone(LOCAL_TZ)
            slot = t.hour * 2 + (t.minute >= 30)
            days.setdefault(t.date().isoformat(), [0.0] * 48)[slot] += float(kwh) / parts
    return days


def plausible(kind: str, readings: list) -> tuple:
    """(readings within MAX_KWH_PER_HALF_HOUR, dropped readings)."""
    cap = MAX_KWH_PER_HALF_HOUR[kind]
    keep, dropped = [], []
    for r in readings:
        duration = r[2] if len(r) > 2 and r[2] else 1800
        (keep if float(r[1]) <= cap * max(1.0, duration / 1800) else dropped).append(r)
    return keep, dropped


def load_readings(data_dir: str = 'eonData', kind: str = 'import') -> tuple:
    """Network-free: (plausible readings, dropped readings) from the cached month files."""
    readings = []
    for path in sorted(glob.glob(os.path.join(data_dir, f'{kind}-????-??.json'))):
        with open(path, encoding='utf-8') as f:
            readings += json.load(f)['readings']
    return plausible(kind, readings)


def load_half_hourly(data_dir: str = 'eonData', kind: str = 'import') -> dict:
    """Network-free: ``{'YYYY-MM-DD': [48 kWh]}`` for one kind (implausible readings dropped)."""
    return readings_to_slots(load_readings(data_dir, kind)[0])


def agreements_from(data: dict) -> list:
    """Flatten AGREEMENTS_QUERY into ``[{meter, from, to, name, product, tariff}]`` (oldest first;
    revoked agreements dropped). ``meter`` is ``import`` / ``export`` / ``gas``."""
    out = []
    for account in data['viewer']['accounts']:
        for prop in account.get('properties') or []:
            points = [('export' if mp.get('direction') == 'EXPORT' else 'import', mp)
                      for mp in prop.get('electricityMeterPoints') or []]
            points += [('gas', mp) for mp in prop.get('gasMeterPoints') or []]
            for meter, mp in points:
                for ag in mp.get('agreements') or []:
                    if ag.get('isRevoked'):
                        continue
                    t = ag.get('tariff') or {}
                    out.append({'meter': meter, 'from': (ag.get('validFrom') or '')[:10],
                                'to': (ag.get('validTo') or '')[:10], 'name': t.get('displayName'),
                                'product': t.get('productCode'), 'tariff': t.get('tariffCode')})
    return sorted(out, key=lambda a: (KINDS.index(a['meter']), a['from']))


def charges_from(nodes: list) -> list:
    """Billed energy charges -> ``[{fuel, export, start, end, days, kwh, usage_p, supply_p, net_p,
    tax_p, gross_p, rate, standing}]`` (not reversed, with consumption), oldest first.
    ``usageCost``/``supplyCharge`` include VAT (net = gross / (1 + VAT)), so ``rate`` (p/kWh)
    and ``standing`` (p/day) are VAT-inclusive, as billed."""
    out = []
    for n in nodes:
        c = n.get('consumption') or {}
        if n.get('isReversed') or not c.get('startDate'):
            continue
        days = (date.fromisoformat(c['endDate']) - date.fromisoformat(c['startDate'])).days + 1
        kwh = float(c.get('quantity') or 0)
        usage, supply = c.get('usageCost') or 0, c.get('supplyCharge') or 0
        amounts = n.get('amounts') or {}
        out.append({'fuel': n.get('title'), 'export': bool(n.get('isExport')) or usage < 0,
                    'start': c['startDate'], 'end': c['endDate'], 'days': days, 'kwh': kwh,
                    'usage_p': usage, 'supply_p': supply, 'net_p': amounts.get('net'),
                    'tax_p': amounts.get('tax'), 'gross_p': amounts.get('gross'),
                    'rate': usage / kwh if kwh else None, 'standing': supply / days if days else None})
    return sorted(out, key=lambda r: (r['start'], r['fuel'] or ''))


def rate_changes(rows: list, start: str, end: str) -> list:
    """Kraken unit-rate rows -> ``[(local date, sorted distinct p/kWh inc VAT)]`` whenever the
    set of rates in force changes, between ``start`` and ``end`` (YYYY-MM-DD; '' = open).
    Direct Debit / unspecified rows only."""
    by_day = {}
    for r in rows:
        if r.get('payment_method') not in (None, 'DIRECT_DEBIT'):
            continue
        if not r.get('valid_from'):
            day = start                                   # open-start period: in force from the start
        else:
            day = max(start, datetime.fromisoformat(r['valid_from'].replace('Z', '+00:00'))
                      .astimezone(LOCAL_TZ).date().isoformat())
        by_day.setdefault(day, set()).add(round(float(r['value_inc_vat']), 3))
    out, prev = [], None
    for day in sorted(by_day):
        if day < start or (end and day >= end):
            continue
        # a day that only shows part of a timetable (one rate) after a full one is a boundary artefact
        if prev and len(by_day[day]) < len(prev) and by_day[day] <= prev:
            continue
        if by_day[day] != prev:
            out.append((day, sorted(by_day[day])))
            prev = by_day[day]
    return out


def load_tariffs(data_dir: str = 'eonData') -> dict:
    """Network-free: the last saved ``{'agreements', 'charges', 'published'}`` (or {})."""
    try:
        with open(os.path.join(data_dir, TARIFFS_FILE), encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def load_daily_kwh(data_dir: str = 'eonData', kind: str = 'import') -> dict:
    """Network-free: ``{'YYYY-MM-DD': kWh}`` for one kind."""
    return {d: sum(v) for d, v in load_half_hourly(data_dir, kind).items()}


# ---------------------------------------------------------------- client

class EonNextClient:  # pylint: disable=too-many-instance-attributes
    """``async with EonNextClient(data_dir) as client: await client.login()``.

    ``callback_provider(url)`` is called with the sign-in URL and returns the pasted callback
    URL (default: print + ``input()``, or ``$EON_CALLBACK``). ``fetched``/``cached`` count
    API requests vs cache hits for the run summary.
    """

    def __init__(self, data_dir: str = 'eonData', callback_provider=None, today: date = None,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                 auth_url: str = AUTH0_DOMAIN, graphql_url: str = GRAPHQL_URL):
        self.data_dir = data_dir
        self.today = today or date.today()
        self.auth_url = auth_url.rstrip('/')
        self.graphql_url = graphql_url
        self._callback_provider = callback_provider or _prompt_callback
        self._session = None
        self._tokens = {}
        self.fetched = 0
        self.cached = 0
        self._properties = None

    async def __aenter__(self):
        os.makedirs(self.data_dir, mode=0o700, exist_ok=True)
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
        return self

    async def __aexit__(self, *exc):
        await self._session.close()

    # ── auth ──────────────────────────────────────────────────────────────

    @property
    def _session_path(self):
        return os.path.join(self.data_dir, SESSION_FILE)

    def _load_tokens(self):
        try:
            with open(self._session_path, encoding='utf-8') as f:
                saved = json.load(f)
            self._tokens = saved.get('auth0', saved)
        except (FileNotFoundError, json.JSONDecodeError):
            self._tokens = {}

    def _save_tokens(self):
        fd = os.open(self._session_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'auth0': self._tokens}, f, indent=2)

    def _id_token_fresh(self) -> bool:
        return jwt_expiry(self._tokens.get('id_token', '')) - time.time() > REFRESH_WITHIN_SECONDS

    async def login(self):
        """Use the saved ID token, else renew it with the refresh token, else sign in."""
        self._load_tokens()
        if self._id_token_fresh():
            return
        if self._tokens.get('refresh_token'):
            try:
                await self._refresh()
                return
            except EonAuthError as e:
                print(f'E.ON session could not be renewed ({e}); signing in again.')
        await self._sign_in()

    async def _token_request(self, body: dict) -> dict:
        async with self._session.post(f'{self.auth_url}/oauth/token', json=body) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise EonAuthError(f"{data.get('error', resp.status)}: {data.get('error_description', '')}")
            return data

    async def _refresh(self):
        data = await self._token_request({'grant_type': 'refresh_token', 'client_id': AUTH0_CLIENT_ID,
                                          'refresh_token': self._tokens['refresh_token']})
        self._tokens.update(data)          # keeps the old refresh token unless a new one is issued
        self._save_tokens()

    async def _sign_in(self):
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(16)
        pasted = self._callback_provider(authorize_url(challenge, state, self.auth_url))
        code = parse_callback(pasted, state)
        self._tokens = await self._token_request({
            'grant_type': 'authorization_code', 'client_id': AUTH0_CLIENT_ID, 'code': code,
            'code_verifier': verifier, 'redirect_uri': REDIRECT_URI})
        self._save_tokens()
        print(f'E.ON Next sign-in OK, session saved to {self._session_path}')

    # ── GraphQL ───────────────────────────────────────────────────────────

    async def graphql(self, query: str, variables: dict = None, retry_auth: bool = True) -> dict:
        """POST a query with the ID token; on an auth error renew once and retry."""
        self.fetched += 1
        async with self._session.post(self.graphql_url, json={'query': query, 'variables': variables or {}},
                                      headers={'Authorization': self._tokens.get('id_token', '')}) as resp:
            body = await resp.json(content_type=None)
        errors = body.get('errors') or []
        if errors:
            auth = any('KT-CT-11' in json.dumps(e) or 'auth' in str(e.get('message', '')).lower()
                       or 'signature' in str(e.get('message', '')).lower() for e in errors)
            if auth and retry_auth:
                await self._refresh_or_sign_in()
                return await self.graphql(query, variables, retry_auth=False)
            raise EonAuthError(errors[0].get('message')) if auth else RuntimeError(errors[0].get('message'))
        return body['data']

    async def _refresh_or_sign_in(self):
        try:
            await self._refresh()
        except (EonAuthError, KeyError):
            await self._sign_in()

    async def properties(self) -> dict:
        """{kind: property id} for the meters this login can see (``export`` only when an
        export meter point exists). If several properties have one kind, the first wins."""
        if self._properties is None:
            data = await self.graphql(ACCOUNTS_QUERY)
            out = {}
            for account in data['viewer']['accounts']:
                for prop in account.get('properties') or []:
                    for mp in prop.get('electricityMeterPoints') or []:
                        out.setdefault('export' if mp.get('direction') == 'EXPORT' else 'import', prop['id'])
                    if prop.get('gasMeterPoints'):
                        out.setdefault('gas', prop['id'])
            self._properties = out
        return self._properties

    async def measurements(self, property_id: str, kind: str, start: str, end: str) -> list:
        """``[[startAt, kWh, seconds], ...]`` for [start, end), following pagination."""
        out, after = [], None
        while True:
            data = await self.graphql(MEASUREMENTS_QUERY, {'id': property_id, 'f': FILTERS[kind],
                                                           's': start, 'e': end, 'after': after})
            conn = data['property']['measurements']
            for edge in conn['edges']:
                node = edge['node']
                if node.get('startAt') is not None:
                    out.append([node['startAt'], float(node['value']), node.get('durationInSeconds') or 1800])
            if not conn['pageInfo']['hasNextPage']:
                return out
            after = conn['pageInfo']['endCursor']

    async def agreements(self) -> list:
        """Every (non-revoked) tariff agreement on every meter, past and present."""
        return agreements_from(await self.graphql(AGREEMENTS_QUERY))

    async def charges(self) -> list:
        """Every billed energy charge on every account (all pages)."""
        nodes, n_accounts, i = [], None, 0
        while n_accounts is None or i < n_accounts:
            after = None
            while True:
                data = await self.graphql(CHARGES_QUERY, {'after': after})
                accounts = data['viewer']['accounts']
                n_accounts = len(accounts)
                if i >= n_accounts:
                    break
                conn = accounts[i]['transactions']
                nodes += [e['node'] for e in conn['edges']]
                if not conn['pageInfo']['hasNextPage']:
                    break
                after = conn['pageInfo']['endCursor']
            i += 1
        return charges_from(nodes)

    async def tariffs(self) -> dict:
        """Agreements, billed charges and each agreement's published rate changes; saved to
        ``{data_dir}/tariffs.json`` (always refreshed: it's a handful of requests)."""
        agreements = await self.agreements()
        published = []
        for ag in agreements:
            rows = await _public_rates(self._session, ag) if ag['product'] and ag['tariff'] else []
            self.fetched += 1
            published.append(rate_changes(rows, ag['from'], ag['to']))
        body = {'agreements': agreements, 'charges': await self.charges(), 'published': published}
        with open(os.path.join(self.data_dir, TARIFFS_FILE), 'w', encoding='utf-8') as f:
            json.dump(body, f, indent=1)
        return body

    async def get_month(self, kind: str, year: int, month: int) -> dict | None:
        """One kind's readings for a month: from the cache if final, else from the API (and
        rewritten). None when this login has no meter of that kind."""
        path = os.path.join(self.data_dir, f'{kind}-{year:04d}-{month:02d}.json')
        final = month_is_final(year, month, self.today)
        if final and os.path.exists(path):
            self.cached += 1
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        prop = (await self.properties()).get(kind)
        if prop is None:
            return None
        start, end = month_bounds(year, month)
        readings = await self.measurements(prop, kind, start, end)
        body = {'kind': kind, 'month': f'{year:04d}-{month:02d}', 'final': final, 'readings': readings}
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(body, f)
        return body


async def _public_rates(session, agreement: dict) -> list:
    """Published Direct Debit unit-rate timeline for an agreement's tariff, from the public
    products API ([] if E.ON doesn't publish that tariff any more)."""
    fuel = 'gas' if agreement['meter'] == 'gas' else 'electricity'
    url = f"{PRODUCTS_URL}{agreement['product']}/{fuel}-tariffs/{agreement['tariff']}/standard-unit-rates/"
    params = {'page_size': 1500, 'period_from': f"{agreement['from']}T00:00:00Z"}
    if agreement['to']:
        params['period_to'] = f"{agreement['to']}T00:00:00Z"
    rows = []
    while url:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            page = await resp.json(content_type=None)
        rows += page.get('results') or []
        url, params = page.get('next'), None
    return rows


def _prompt_callback(url: str) -> str:
    env = os.getenv('EON_CALLBACK')
    if env:
        return env
    print('\nSign in to E.ON Next in a desktop browser:\n'
          '  1. Open a new tab, press F12, choose the Network tab and tick "Preserve log".\n'
          '  2. Open this URL in that tab and sign in:\n\n'
          f'{url}\n\n'
          '  3. The page then appears to stall (it is trying to open the E.ON Next app).\n'
          '     In the Network list find the request starting "callback?code=",\n'
          '     right-click > Copy > Copy URL, and paste it below (the code expires in minutes).\n')
    return input('Callback URL: ').strip()
