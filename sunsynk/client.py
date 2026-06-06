import aiohttp

from sunsynk.battery import Battery
from sunsynk.grid import Grid
from sunsynk.input import Input
from sunsynk.inverter import Inverter
from sunsynk.output import Output
from sunsynk.plant import Plant
from sunsynk.energyday import EnergyDay
from sunsynk.energyday import EnergyMonth
import json

from datetime import datetime
import hashlib
import sys
import base64

if sys.platform == 'win32':
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
else:
    from Cryptodome.PublicKey import RSA
    from Cryptodome.Cipher import PKCS1_v1_5

# -------------------------------
# Encrypt with Public Key (like JSEncrypt)
# -------------------------------
def rsa_encrypt(public_key_pem: str, message: str) -> str:
    
    try:
        rsa_key = RSA.import_key(public_key_pem)
        cipher = PKCS1_v1_5.new(rsa_key)
        encrypted_bytes = cipher.encrypt(message.encode('utf-8'))
        return base64.b64encode(encrypted_bytes).decode('utf-8')
    except (ValueError, TypeError) as e:
        raise ValueError(f"Encryption failed: {e}")

class FailedPubKeyException(Exception):
    def __init__(self):
        super().__init__('Failed to retrieve Public Key')

class InvalidCredentialsException(Exception):
    def __init__(self):
        super().__init__('Invalid username or password')

class SunsynkClient:

    @classmethod
    async def create(cls, username: str, password: str, base_url: str = None, source: str=None):
        self = SunsynkClient(username, password, base_url, source)
        return await self.login()
        
    def __init__(self, username: str, password: str, base_url: str=None, source: str=None) :
        self.base_url = 'https://pv.inteless.com' if base_url is None else base_url
        self.source = 'inteless' if source is None else source
        self.session = aiohttp.ClientSession()
        self.access_token = None
        self.refresh_token = None
        self.username = username
        self.password = password
        self.VarCurrentDate = datetime.today().strftime('%Y-%m-%d')

    async def __aenter__(self):
        await self.login()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        await self.session.close()

    async def get_plants(self) -> list[Plant]:
        resp = await self.__get('api/v1/plants?page=1&limit=10&name=&status=')
        body = await resp.json()
        plants = body['data']['infos']
        return [Plant(data) for data in plants]

    async def get_inverters(self) -> list[Inverter]:
        resp = await self.__get('api/v1/inverters?page=1&limit=10&total=0&status=-1&sn=&plantId=&type=-2&softVer=&' \
                                'hmiVer=&agentCompanyId=-1&gsn=')
        body = await resp.json()
        inverters = body['data']['infos']
        return [Inverter(data) for data in inverters]

    async def get_inverter_realtime_input(self, inverter_sn: str) -> Input:
        resp = await self.__get(f'api/v1/inverter/{inverter_sn}/realtime/input')
        body = await resp.json()
        return Input(body['data'])

    async def get_inverter_realtime_output(self, inverter_sn: str) -> Output:
        resp = await self.__get(f'api/v1/inverter/{inverter_sn}/realtime/output')
        body = await resp.json()
        return Output(body['data'])

    async def get_inverter_realtime_grid(self, inverter_sn: str) -> Grid:
        resp = await self.__get(f'api/v1/inverter/grid/{inverter_sn}/realtime?sn={inverter_sn}')
        body = await resp.json()
        return Grid(body['data'])

    async def get_inverter_realtime_battery(self, inverter_sn: str) -> Battery:
        resp = await self.__get(f'api/v1/inverter/battery/{inverter_sn}/realtime?sn={inverter_sn}&lan')
        body = await resp.json()
        return Battery(body['data'])

    async def get_energy_day(self, plant_id: str, date: str, month: EnergyMonth, battery: Battery, offpeakstart, offpeakstop) -> EnergyDay:
        body = await self.__getJSON(f'api/v1/plant/energy/{plant_id}/day?lan=en&date={date}&id={plant_id}', 'day', date)
        #body = await resp.json()
        return EnergyDay(body['data'], date, month, battery, offpeakstart, offpeakstop)
        
    async def get_energy_month(self, plant_id: str, date: str) -> EnergyMonth:
        resp = await self.__get(f'api/v1/plant/energy/{plant_id}/month?lan=en&date={date}&id={plant_id}')
        body = await resp.json()
        return EnergyMonth(body['data'])

    async def __getJSON(self, path: str, req_type: str, date: str):
        try:
            with open(f'inverterData/{req_type}-{date}.json') as data_file:
                body = json.load(data_file)
                #print(f'Load from file: {req_type}-{date}.json')
        except Exception as e:
            #print(f'Load from path: {path}')
            resp = await self.__get(path)
            body = await resp.json()
            if (date != self.VarCurrentDate):
                with open(f'inverterData/{req_type}-{date}.json', 'w') as file:
                    json.dump(body, file, indent=2)  # 'indent' for pretty-printing 
                    print(f'Write to file: {req_type}-{date}.json')
        return body

    async def __get(self, path: str, attempts: int = 1):
        resp = await self.session.get(self.__url(path), headers=self.__headers(), timeout=20)
        if resp.status == 401 and attempts == 1:
            await self.login()
            return await self.__get(path, attempts=attempts + 1)
        return resp

    def __headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json"
        }
        if self.access_token:
            headers['Authorization'] = f"Bearer {self.access_token}"
        return headers
        
    async def get_public_key(self, nonce):
        
        urlstring = 'nonce=' + nonce + '&source=' + self.source
        hashstring = urlstring + "POWER_VIEW"
        md5_hash = hashlib.md5(hashstring.encode('utf-8')).hexdigest()
        url = 'anonymous/publicKey?' + urlstring + '&sign=' + md5_hash
        
        resp = await self.session.get(self.__url(url), 
                                      headers={"Content-Type": "application/json"}, 
                                      timeout=20)
        if resp.status == 200:
            resp_body = await resp.json()
            pubKey = resp_body['data']
            return "-----BEGIN PUBLIC KEY-----\n" + pubKey + "\n-----END PUBLIC KEY-----"
        raise FailedPubKeyException()

    async def login(self):
        
        nonce = str(int(datetime.today().timestamp() * 1000))
        public_key = await self.get_public_key(nonce)
        signdata = "nonce=" + nonce + "&source=" + self.source + public_key[27:37]
        sign = hashlib.md5(signdata.encode('utf-8')).hexdigest()
        enc_password = rsa_encrypt(public_key, self.password)
        
        payload = {
            'sign': sign,
            'nonce':int(nonce),
            'username': self.username,
            'password': enc_password,
            'grant_type': 'password',
            'client_id': 'csp-web',
            'source': self.source
        }
        
        #resp = await self.session.post(self.__url('oauth/token'),
        resp = await self.session.post(self.__url('oauth/token/new'),
                                       headers={"Content-Type": "application/json"},
                                       timeout=20,
                                       json=payload)
        if resp.status == 200:
            resp_body = await resp.json()
            if resp_body['success']:
                self.access_token = resp_body['data']['access_token']
                self.refresh_token = resp_body['data']['refresh_token']
                return self
        raise InvalidCredentialsException()

    def __url(self, path: str) -> str:
        return f'{self.base_url}/{path}'
