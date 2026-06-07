import base64
import json

from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from sunsynk.client import SunsynkClient
from analysis.energy_client import SunsynkEnergyClient


class MockApiServer:
    def __init__(self, aiohttp_client):
        self.aiohttp_client = aiohttp_client
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_der = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._public_key_b64 = base64.b64encode(public_der).decode()
        self.app = web.Application()
        self.app.router.add_get('/anonymous/publicKey', self.get_public_key)
        self.app.router.add_post('/oauth/token/new', self.login)
        self.app.router.add_get('/api/v1/inverters', self.get_inverters)
        self.app.router.add_get('/api/v1/plants', self.get_plants)
        self.app.router.add_get('/api/v1/inverter/grid/1029384756/realtime', self.get_inverter_realtime_grid)
        self.app.router.add_get('/api/v1/inverter/battery/1029384756/realtime', self.get_inverter_realtime_battery)
        self.app.router.add_get('/api/v1/inverter/1029384756/realtime/input', self.get_inverter_realtime_input)
        self.app.router.add_get('/api/v1/inverter/1029384756/realtime/output', self.get_inverter_realtime_output)
        self.app.router.add_get('/api/v1/plant/energy/{plant_id}/month', self.get_energy_month)
        self.app.router.add_get('/api/v1/plant/energy/{plant_id}/day', self.get_energy_day)

    async def client(self, username='myuser'):
        client = await self.aiohttp_client(self.app)
        return await SunsynkClient.create(username, 'letmein', base_url=f'http://{client.host}:{client.port}')

    async def energy_client(self, username='myuser'):
        client = await self.aiohttp_client(self.app)
        base_url = f'http://{client.host}:{client.port}'
        ec = SunsynkEnergyClient(username, 'letmein', base_url)
        await ec.login()
        return ec

    async def get_public_key(self, request):
        payload = {
            'code': 0,
            'msg': 'Success',
            'data': self._public_key_b64,
            'success': True,
        }
        return web.Response(text=json.dumps(payload),
                            headers={'Content-Type': 'application/json'})

    async def login(self, request):
        request_body = await request.json()
        try:
            ciphertext = base64.b64decode(request_body['password'])
            decrypted = self._private_key.decrypt(ciphertext, padding.PKCS1v15()).decode()
        except Exception:
            decrypted = None
        success = request_body['username'] == 'myuser' and decrypted == 'letmein'
        payload = {
            'success': success,
            'data': {
                'access_token': 'AT123',
                'refresh_token': 'RT456'
            }
        }
        headers = {
            'Content-Type': 'application/json'
        }
        return web.Response(text=json.dumps(payload), headers=headers)

    async def get_inverters(self, request):
        payload = {
            "code": 0,
            "msg": "Success",
            "data": {
                "pageSize": 10,
                "pageNumber": 1,
                "total": 1,
                "infos": [
                    {
                        "sn": "1029384756",
                        "alias": "1029384756",
                        "gsn": "E0192837465",
                        "status": 1,
                        "type": 2,
                        "commTypeName": "RS485",
                        "custCode": 29,
                        "version": {
                            "masterVer": "2.3.7.4",
                            "softVer": "1.5.1.5",
                            "hardVer": "",
                            "hmiVer": "E.4.2.4",
                            "bmsVer": ""
                        },
                        "model": "",
                        "equipMode": None,
                        "pac": 61,
                        "etoday": 1.7,
                        "etotal": 375.1,
                        "updateAt": "2023-01-07T15:40:02Z", "opened": 1,
                        "plant": {
                            "id": 12345,
                            "name": "John Smith",
                            "type": 2,
                            "master": None,
                            "installer": None,
                            "email": None,
                            "phone": None
                        },
                        "gatewayVO": {
                            "gsn": "E0192837465",
                            "status": 2
                        },
                        "sunsynkEquip": True,
                        "protocolIdentifier": "2"
                    }
                ]
            },
            "success": True
        }
        headers = {
            'Content-Type': 'application/json'
        }
        return web.Response(text=json.dumps(payload), headers=headers)

    async def get_plants(self, request):
        payload = {
            "code": 0,
            "msg": "Success",
            "data": {
                "pageSize": 10,
                "pageNumber": 1,
                "total": 1,
                "infos": [
                    {
                        "id": 12345,
                        "name": "John Smith",
                        "thumbUrl": "https://",
                        "status": 1,
                        "address": "123 Fake Street",
                        "pac": 38,
                        "efficiency": 0.011,
                        "etoday": 1.7,
                        "etotal": 370.5,
                        "updateAt": "2023-01-07T15:55:06Z",
                        "createAt": "2022-10-03T15:39:21.000+00:00",
                        "type": 2,
                        "masterId": 54321,
                        "share": False,
                        "plantPermission": [
                            "station.share.cancle"
                        ],
                        "existCamera": False
                    }
                ]
            },
            "success": True
        }
        headers = {
            'Content-Type': 'application/json'
        }
        return web.Response(text=json.dumps(payload), headers=headers)

    async def get_inverter_realtime_grid(self, request):
        payload = {
            "code": 0,
            "msg": "Success",
            "data": {
                "vip":
                    [
                        {"volt": "233.6",
                         "current": "0.8",
                         "power": 610
                         }
                    ],
                "pac": 610,
                "qac": 0,
                "fac": 50.08,
                "pf": 1.0,
                "status": 1,
                "etodayFrom": "12.2",
                "etodayTo": "0.0",
                "etotalFrom": "998.5",
                "etotalTo": "48.2",
                "limiterPowerArr": [610, 0],
                "limiterTotalPower": 610
            },
            "success": True
        }
        headers = {
            'Content-Type': 'application/json'
        }
        return web.Response(text=json.dumps(payload), headers=headers)

    async def get_inverter_realtime_battery(self, request):
        payload = {
            'code': 0,
            'msg': 'Success',
            'data': {
                'time': None,
                'etodayChg': '1.1',
                'etodayDischg': '0.6',
                'emonthChg': '7.5',
                'emonthDischg': '6.2',
                'eyearChg': '7.5',
                'eyearDischg': '6.2',
                'etotalChg': '188.5',
                'etotalDischg': '147.9',
                'type': 1,
                'power': -18,
                'capacity': '100.0',
                'correctCap': 100,
                'current': '-0.4',
                'voltage': '53.3',
                'temp': '18.7',
                'soc': '20.0',
                'chargeVolt': 56.1,
                'dischargeVolt': 0.0,
                'chargeCurrentLimit': 50.0,
                'dischargeCurrentLimit': 50.0,
                'maxChargeCurrentLimit': 0.0,
                'maxDischargeCurrentLimit': 0.0,
                'current2': None,
                'voltage2': None,
                'temp2': None,
                'soc2': None,
                'chargeVolt2': None,
                'dischargeVolt2': None,
                'chargeCurrentLimit2': None,
                'dischargeCurrentLimit2': None,
                'maxChargeCurrentLimit2': None,
                'maxDischargeCurrentLimit2': None,
                'status': 1,
                'batterySoc1': 0.0,
                'batteryCurrent1': 0.0,
                'batteryVolt1': 0.0,
                'batteryPower1': 0.0,
                'batteryTemp1': 0.0,
                'batteryStatus2': 0,
                'batterySoc2': None,
                'batteryCurrent2': None,
                'batteryVolt2': None,
                'batteryPower2': None,
                'batteryTemp2': None,
                'numberOfBatteries': None,
                'batt1Factory': None,
                'batt2Factory': None
            },
            'success': True
        }
        headers = {
            'Content-Type': 'application/json'
        }
        return web.Response(text=json.dumps(payload), headers=headers)

    async def get_inverter_realtime_input(self, request):
        payload = {
            "code": 0,
            "msg": "Success",
            "data": {
                "pac": 9, "pvIV":
                    [
                        {
                            "id": None,
                            "pvNo": 1,
                            "vpv": "91.5",
                            "ipv": "0.1",
                            "ppv": "9.0",
                            "todayPv": "0.0",
                            "sn": "1029384756",
                            "time": "2023-01-07 16:50:17"
                        },
                        {
                            "id": None,
                            "pvNo": 2,
                            "vpv": "2.4",
                            "ipv": "0.1",
                            "ppv": "0.0",
                            "todayPv": "0.0",
                            "sn": "1029384756",
                            "time": "2023-01-07 16:50:17"
                        }
                    ],
                "mpptIV": [],
                "etoday": 1.8,
                "etotal": 375.2
            },
            "success": True
        }
        headers = {
            'Content-Type': 'application/json'
        }
        return web.Response(text=json.dumps(payload), headers=headers)

    async def get_inverter_realtime_output(self, request):
        payload = {
            "code": 0,
            "msg": "Success",
            "data": {
                "vip":
                    [
                        {
                            "volt": "230.8",
                            "current": "0.3",
                            "power": -50
                        }
                    ],
                "pInv": 9,
                "pac": -50,
                "fac": 50.0
            },
            "success": True
        }
        headers = {
            'Content-Type': 'application/json'
        }
        return web.Response(text=json.dumps(payload), headers=headers)

    async def get_energy_month(self, request):
        # Daily totals (kWh) from the real API (labels match real Sunsynk API format).
        # 2025-06-10: sunny day (based on 2026-05-27 real data)
        # 2025-06-11: cloudy day with export (based on 2026-05-04 real data)
        payload = {
            "code": 0,
            "msg": "Success",
            "data": {
                "infos": [
                    {
                        "label": "Load Power Consumption",
                        "records": [
                            {"time": "2025-06-10", "value": "22.9"},
                            {"time": "2025-06-11", "value": "6.0"}
                        ]
                    },
                    {
                        "label": "PV Generation",
                        "records": [
                            {"time": "2025-06-10", "value": "19.6"},
                            {"time": "2025-06-11", "value": "4.4"}
                        ]
                    },
                    {
                        "label": "Sold electricity",
                        "records": [
                            {"time": "2025-06-10", "value": "10.1"},
                            {"time": "2025-06-11", "value": "1.4"}
                        ]
                    },
                    {
                        "label": "Purchased electricity",
                        "records": [
                            {"time": "2025-06-10", "value": "1.9"},
                            {"time": "2025-06-11", "value": "2.9"}
                        ]
                    }
                ]
            },
            "success": True
        }
        headers = {'Content-Type': 'application/json'}
        return web.Response(text=json.dumps(payload), headers=headers)

    async def get_energy_day(self, request):
        date = request.rel_url.query.get('date', '')
        if date == '2025-06-11':
            infos = self._day_infos_cloudy()
        else:
            infos = self._day_infos_sunny()
        payload = {"code": 0, "msg": "Success", "data": {"infos": infos}, "success": True}
        return web.Response(text=json.dumps(payload), headers={'Content-Type': 'application/json'})

    @staticmethod
    def _r(time, value):
        return {"time": time, "value": str(float(value)), "updateTime": None}

    def _day_infos_sunny(self):
        # Hourly records for 2025-06-10 (sunny day, from 2026-05-27 real data).
        # offpeak=00:00-06:00 (records at 00:00-05:00 are offpeak; 06:00+ is peak).
        # Expected results (Wh):
        #   Grid offpeak = 7344/12 = 612.0,  peak = 749.75 - 9*2.3 ≈ 729.05 → import 1341.1
        #   Grid peakexport = 721.2
        #   PV   peak     = 18880/12 = 1573.3
        #   Load offpeak  = 612.0,  peak = 19025/12 = 1585.4 → load 2197.4
        r = self._r
        return [
            {"label": "PV",      "unit": "W", "records": [
                r("06:00", 110),  r("07:00", 790),  r("08:00", 1473), r("09:00", 968),
                r("10:00", 2147), r("11:00", 2512), r("12:00", 2575), r("13:00", 2433),
                r("14:00", 2119), r("15:00", 1672), r("16:00", 1157), r("17:00", 512),
                r("18:00", 196),  r("19:00", 148),  r("20:00", 68)]},
            {"label": "Battery", "unit": "W", "records": [r("12:00", 0)]},
            {"label": "SOC",     "unit": "%", "records": [r("12:00", 50)]},
            {"label": "Load",    "unit": "W", "records": [
                r("00:00", 787),  r("01:00", 930),  r("02:00", 3148), r("03:00", 876),
                r("04:00", 805),  r("05:00", 798),  r("06:00", 875),  r("07:00", 781),
                r("08:00", 786),  r("09:00", 863),  r("10:00", 1066), r("11:00", 902),
                r("12:00", 945),  r("13:00", 983),  r("14:00", 935),  r("15:00", 863),
                r("16:00", 877),  r("17:00", 777),  r("18:00", 772),  r("19:00", 1014),
                r("20:00", 2290), r("21:00", 3536), r("23:00", 760)]},
            {"label": "Grid",    "unit": "W", "records": [
                r("00:00", 787),  r("01:00", 930),  r("02:00", 3148), r("03:00", 876),
                r("04:00", 805),  r("05:00", 798),  r("06:00", 765),  r("07:00", 0),
                r("08:00", -697), r("09:00", -100), r("10:00", -1095),r("11:00", -1619),
                r("12:00", -1632),r("13:00", -1461),r("14:00", -1196),r("15:00", -818),
                r("16:00", -285), r("17:00", 272),  r("18:00", 576),  r("19:00", 868),
                r("20:00", 2220), r("21:00", 3536), r("23:00", 760)]},
        ]

    def _day_infos_cloudy(self):
        # Hourly records for 2025-06-11 (cloudy day with export, from 2026-05-04 real data).
        # offpeak=00:00-06:00 (records at 00:00-05:00 are offpeak; 06:00+ is peak).
        # Expected results (Wh):
        #   Grid offpeak = 968/12 = 80.7,  peak = 2799/12 - 9*2.3 ≈ 212.6 → import 293.2
        #   Grid peakexport = 135.8 (exact)
        #   PV   peak     = 5312/12 = 442.7
        #   Load offpeak  = 80.7,   peak = 6270/12 = 522.5 → load 603.2
        r = self._r
        return [
            {"label": "PV",      "unit": "W", "records": [
                r("07:00", 409), r("08:00", 399), r("09:00", 369), r("10:00", 666),
                r("11:00", 887), r("12:00", 765), r("13:00", 646), r("14:00", 393),
                r("15:00", 298), r("16:00", 313), r("17:00", 154), r("18:00", 13)]},
            {"label": "Battery", "unit": "W", "records": [r("12:00", 0)]},
            {"label": "SOC",     "unit": "%", "records": [r("12:00", 50)]},
            {"label": "Load",    "unit": "W", "records": [
                r("00:00", 134), r("01:00", 134), r("02:00", 130), r("03:00", 133),
                r("04:00", 180), r("05:00", 257), r("06:00", 251), r("07:00", 328),
                r("08:00", 317), r("09:00", 288), r("10:00", 372), r("11:00", 415),
                r("12:00", 388), r("13:00", 350), r("14:00", 315), r("15:00", 219),
                r("16:00", 376), r("17:00", 492), r("18:00", 358), r("19:00", 347),
                r("20:00", 357), r("21:00", 368), r("22:00", 364), r("23:00", 365)]},
            {"label": "Grid",    "unit": "W", "records": [
                r("00:00", 134), r("01:00", 134), r("02:00", 130), r("03:00", 133),
                r("04:00", 180), r("05:00", 257), r("06:00", 251), r("07:00", -82),
                r("08:00", -82), r("09:00", -82), r("10:00", -300),r("11:00", -480),
                r("12:00", -384),r("13:00", -304),r("14:00", -82), r("15:00", -82),
                r("16:00", 62),  r("17:00", 338), r("18:00", 347), r("19:00", 347),
                r("20:00", 357), r("21:00", 368), r("22:00", 364), r("23:00", 365)]},
        ]
