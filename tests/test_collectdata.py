import pytest

from analysis.collectdata import (
    _parse_cli_args,
    _load_settings,
    _normalize_pv_boosts,
    _pv_boost_metrics,
)

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
