from datetime import datetime

import pytest

from analysis.collectdata import (
    _export_correction_for,
    _describe_export_correction,
    _parse_cli_args,
    _load_settings,
    _normalize_pv_boosts,
    _pv_boost_metrics,
    _iter_energy_days,
    _make_battery,
)
from analysis.energyprices import EnergyPrices
from analysis.pricedata import PriceData
from tests.mock_api_server import MockApiServer

MISSING_CONFIG = 'config:/nonexistent/does-not-exist.json'


# ─── _normalize_pv_boosts ────────────────────────────────────────────────────

def test_normalize_absent_is_single_baseline():
    boosts = _normalize_pv_boosts(None, 3115, {})
    assert len(boosts) == 1
    b = boosts[0]
    assert b['addWatts'] == 0.0
    assert b['cost'] == 0.0
    assert b['inverterClipW'] is None
    assert b['pv_extra_ratio'] == 0.0
    assert b['label'] == 'Baseline'


def test_normalize_list_prepends_baseline():
    boosts = _normalize_pv_boosts(
        [{'label': '+1260W', 'addWatts': 1260, 'cost': 989}], 3115, {})
    assert len(boosts) == 2
    assert boosts[0]['addWatts'] == 0.0            # baseline injected first
    assert boosts[1]['addWatts'] == 1260.0
    assert boosts[1]['pv_extra_ratio'] == pytest.approx(1260 / 3115)


def test_normalize_single_object_is_wrapped():
    boosts = _normalize_pv_boosts({'addWatts': 900, 'cost': 700}, 3115, {})
    assert [b['addWatts'] for b in boosts] == [0.0, 900.0]
    assert boosts[1]['label'] == '+900W'


def test_normalize_explicit_zero_not_duplicated():
    boosts = _normalize_pv_boosts(
        [{'addWatts': 0, 'label': 'Now'}, {'addWatts': 1260, 'cost': 989}], 3115, {})
    assert len(boosts) == 2
    assert boosts[0]['label'] == 'Now'


def test_normalize_clip_coercion():
    assert _normalize_pv_boosts({'addWatts': 1}, 3115, {})[1]['inverterClipW'] is None
    assert _normalize_pv_boosts({'addWatts': 1, 'inverterClipW': 0}, 3115, {})[1]['inverterClipW'] is None
    assert _normalize_pv_boosts({'addWatts': 1, 'inverterClipW': -5}, 3115, {})[1]['inverterClipW'] is None
    assert _normalize_pv_boosts({'addWatts': 1, 'inverterClipW': 800}, 3115, {})[1]['inverterClipW'] == 800.0


def test_normalize_cli_replaces_config():
    cli = {'addWatts': 1260.0, 'cost': 989.0, 'inverterClipW': 800.0}
    boosts = _normalize_pv_boosts([{'addWatts': 5000, 'cost': 1}], 3115, cli)
    assert [b['addWatts'] for b in boosts] == [0.0, 1260.0]
    assert boosts[1]['cost'] == 989.0
    assert boosts[1]['inverterClipW'] == 800.0


def test_normalize_zero_existing_watts_is_safe():
    boosts = _normalize_pv_boosts({'addWatts': 1260, 'cost': 989}, 0, {})
    assert all(b['pv_extra_ratio'] == 0.0 for b in boosts)


# ─── _parse_cli_args / _load_settings ────────────────────────────────────────

def test_parse_cli_pv_boost_keys():
    _, overrides = _parse_cli_args(
        ['pvBoostWatts:1260', 'pvBoostCost:989', 'pvBoostClipW:800', 'existingPvWatts:3115'])
    assert overrides['existingPvWatts'] == 3115.0
    assert overrides['pvBoostCli'] == {'addWatts': 1260.0, 'cost': 989.0, 'inverterClipW': 800.0}


def test_load_settings_defaults_single_baseline():
    settings = _load_settings([MISSING_CONFIG])
    assert settings['existingPvWatts'] == 3115
    assert len(settings['pvBoost']) == 1
    assert settings['pvBoost'][0]['pv_extra_ratio'] == 0.0


def test_load_settings_cli_builds_comparison():
    settings = _load_settings(
        [MISSING_CONFIG, 'pvBoostWatts:1260', 'pvBoostCost:989', 'pvBoostClipW:800'])
    boosts = settings['pvBoost']
    assert [b['addWatts'] for b in boosts] == [0.0, 1260.0]
    assert boosts[1]['cost'] == 989.0
    assert boosts[1]['inverterClipW'] == 800.0
    assert boosts[1]['pv_extra_ratio'] == pytest.approx(1260 / 3115)


# ─── _pv_boost_metrics ──────────────────────────────────────────────────────

class _FakePriceData:
    current_export = 0.15


class _FakePrices:
    def __init__(self, totals, derived, battery_enabled=False):
        self._t = totals
        self._d = derived
        self.battery_enabled = battery_enabled
        self.price_data = _FakePriceData()

    def get_grand_totals(self):
        return self._t

    def get_derived(self):
        return self._d


def _entry(boost, totals, derived, battery_enabled=False):
    return (boost, [('cfg', [_FakePrices(totals, derived, battery_enabled)])])


def test_pv_boost_metrics_math():
    days = 365  # ann factor == 1.0
    base_t = {'days': days, 'total_calc_pv': 1000.0, 'total_pv_clip_loss': 0.0,
              'total_cost': 500.0, 'total_export_amount_calc': 100.0}
    base_d = {'battery_savings': 0}
    boost_t = {'days': days, 'total_calc_pv': 1300.0, 'total_pv_clip_loss': 40.0,
               'total_cost': 460.0, 'total_export_amount_calc': 130.0}
    boost_d = {'battery_savings': 0}

    boosts = [
        {'label': 'Baseline', 'addWatts': 0.0, 'cost': 0.0, 'inverterClipW': None},
        {'label': '+1260W', 'addWatts': 1260.0, 'cost': 989.0, 'inverterClipW': 800.0},
    ]
    results = [
        _entry(boosts[0], base_t, base_d),
        _entry(boosts[1], boost_t, boost_d),
    ]
    m = _pv_boost_metrics(results)

    # baseline column is all zero
    assert m[0]['deliv'] == 0.0 and m[0]['total_extra'] == 0.0 and m[0]['payback'] is None

    row = m[1]
    assert row['deliv'] == pytest.approx(300.0)        # 1300 - 1000
    assert row['clip'] == pytest.approx(40.0)
    assert row['clip_pct'] == pytest.approx(100 * 40 / 340, abs=0.1)
    assert row['clip_value'] == pytest.approx(40 * 0.15)
    assert row['self_cons'] == pytest.approx(40.0)     # 500 - 460
    assert row['export_inc'] == pytest.approx(30.0)    # 130 - 100
    assert row['total_extra'] == pytest.approx(70.0)
    assert row['payback'] == pytest.approx(round(989.0 / 70.0, 1))
    assert row['roi'] == pytest.approx(round(100 * 70.0 / 989.0, 1))


def test_pv_boost_metrics_negative_saving_gives_na_payback():
    days = 365
    base_t = {'days': days, 'total_calc_pv': 1000.0, 'total_pv_clip_loss': 0.0,
              'total_cost': 500.0, 'total_export_amount_calc': 100.0}
    worse_t = {'days': days, 'total_calc_pv': 1000.0, 'total_pv_clip_loss': 0.0,
               'total_cost': 520.0, 'total_export_amount_calc': 100.0}
    d = {'battery_savings': 0}
    boosts = [
        {'label': 'Baseline', 'addWatts': 0.0, 'cost': 0.0, 'inverterClipW': None},
        {'label': 'Bad', 'addWatts': 500.0, 'cost': 400.0, 'inverterClipW': None},
    ]
    m = _pv_boost_metrics([_entry(boosts[0], base_t, d), _entry(boosts[1], worse_t, d)])
    assert m[1]['total_extra'] < 0
    assert m[1]['payback'] is None
    assert m[1]['clip_pct'] == 0.0


# ─── _iter_energy_days ────────────────────────────────────────────────────

_ITER_TEST_PRICES = {
    'data': {
        'prices': [
            {
                'datefrom': '2020-01-01',
                'dateto': '2030-12-31',
                'offpeakRate': '0.10',
                'offpeakStart': '00:00',
                'offpeakStop': '06:00',
                'peakRate': '0.30',
                'exportRate': '0.15',
                'standingCharge': '0.60',
                'InterestRate': '3.7',
            },
        ]
    }
}

_ITER_TEST_BATTERY_CONFIG = {
    'enabled': 'ON', 'batterySize': 5000, 'usePV': 'OFF',
    'startCharge': 1000, 'stopCharge': 2000,
    'exportWindowStart': '17:00', 'exportWindowStop': '19:00',
    'useExport': 'OFF', 'dischargeEfficiency': 0.92, 'pvChargeEfficiency': 0.96,
    'maxOutputW': 2400, 'chargeEfficiency': 1.0, 'gridCharge': 'ON',
    'dischargeReserveWh': 0, 'dischargeReserveUntil': '17:00',
}


@pytest.mark.asyncio
async def test_iter_energy_days_yields_match_add_data_calls(aiohttp_client, tmp_path, monkeypatch):
    # Regression guard for the collectdata.py refactor that extracted _iter_energy_days
    # out of main()'s inline day-processing loop: every yielded day must correspond to
    # exactly one prices.add_data() call (grand_totals['days'] tracks add_data calls),
    # and each yield must carry a battery_delta dict for that single day.
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inverterData').mkdir()

    mock_api_server = MockApiServer(aiohttp_client)
    client = await mock_api_server.energy_client()
    inverter = (await client.get_inverters())[0]

    battery = _make_battery(_ITER_TEST_BATTERY_CONFIG, PriceData())
    prices = EnergyPrices(_ITER_TEST_PRICES, battery, original_price=6000)

    current_year = datetime.now().year
    month_cache, day_raw_cache, energyday_cache = {}, {}, {}
    cache_stats = {'hits': 0, 'misses': 0}

    yielded = []
    async for date_str, energyday, battery_delta in _iter_energy_days(
            client, inverter, prices, 0.0, None,
            '', '', current_year, True,
            month_cache, day_raw_cache, energyday_cache, cache_stats):
        yielded.append((date_str, energyday, battery_delta))

    assert len(yielded) > 0
    assert all(d in ('2025-06-10', '2025-06-11') for d, _, _ in yielded)
    for _, _, delta in yielded:
        assert set(delta.keys()) == {'charge_kwh', 'discharge_kwh', 'pv_charge_kwh', 'export_kwh'}

    assert prices.get_grand_totals()['days'] == len(yielded)


# ─── exportCorrection ────────────────────────────────────────────────────────

def test_export_correction_defaults_reproduce_original_rule():
    ec = _load_settings([MISSING_CONFIG])['exportCorrection']
    assert ec == {'gain': 1.0, 'chargerStandbyW': 27.6, 'chargerOff': []}
    corr = _export_correction_for('2026-08-10', ec)
    assert (corr.gain, corr.standby_w) == (1.0, 27.6)


def test_export_correction_config_and_cli_overrides(tmp_path):
    cfg = tmp_path / 'c.json'
    cfg.write_text('{"exportCorrection": {"gain": 0.95, "chargerOff": [["2026-08-06", "2026-08-25"]]}}')
    ec = _load_settings([f'config:{cfg}', 'chargerStandbyW:11'])['exportCorrection']
    assert ec['gain'] == 0.95 and ec['chargerStandbyW'] == 11.0
    assert _load_settings([f'config:{cfg}', 'exportGain:0.9'])['exportCorrection']['gain'] == 0.9
    assert 'off 2026-08-06..2026-08-25' in _describe_export_correction(ec)


def test_export_correction_for_charger_off_ranges_inclusive():
    ec = {'gain': 0.953, 'chargerStandbyW': 11, 'chargerOff': [['2026-08-06', '2026-08-25']]}
    assert _export_correction_for('2026-08-05', ec).standby_w == 11
    assert _export_correction_for('2026-08-06', ec).standby_w == 0
    assert _export_correction_for('2026-08-25', ec).standby_w == 0
    assert _export_correction_for('2026-08-26', ec).standby_w == 11
    assert _export_correction_for('2026-08-26', ec).gain == 0.953
