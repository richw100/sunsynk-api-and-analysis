import pytest
from datetime import datetime

from analysis.calculations import (
    PriceData, VirtualBattery, EnergySummary, EnergySummaryAggregator, QueryType
)


def make_price_data(peak=0.30, offpeak=0.10, export=0.15, compare=0.25,
                    standing=0.60, compare_standing=0.53,
                    offpeak_start="00:00", offpeak_stop="06:00",
                    interest_rate=3.7):
    pd = PriceData()
    pd.currentPeak = peak
    pd.currentOffPeak = offpeak
    pd.currentExport = export
    pd.CompareRate = compare
    pd.standingCharge = standing
    pd.ComparestandingCharge = compare_standing
    pd.currentOffPeakStart = offpeak_start
    pd.currentOffPeakStop = offpeak_stop
    pd.InterestRate = interest_rate
    return pd


class MockEnergyDay:
    """Minimal EnergyDay stub for testing EnergySummary without API calls."""
    def __init__(self, calc_import=0, calc_import_peak=0, calc_import_offpeak=0,
                 calc_export=0, calc_export_peak=0, calc_export_offpeak=0,
                 calc_pv=0, calc_load=0, calc_load_peak=0, calc_load_offpeak=0,
                 supplied_load=0.0, supplied_import=0.0, supplied_pv=0.0, supplied_export=0.0):
        self._import = calc_import
        self._import_peak = calc_import_peak
        self._import_offpeak = calc_import_offpeak
        self._export = calc_export
        self._export_peak = calc_export_peak
        self._export_offpeak = calc_export_offpeak
        self._pv = calc_pv
        self._load = calc_load
        self._load_peak = calc_load_peak
        self._load_offpeak = calc_load_offpeak
        self._supplied_load = supplied_load
        self._supplied_import = supplied_import
        self._supplied_pv = supplied_pv
        self._supplied_export = supplied_export

    def getCalcImport(self, qtype=QueryType.BOTH):
        if qtype == QueryType.PEAK:    return self._import_peak
        if qtype == QueryType.OFFPEAK: return self._import_offpeak
        return self._import

    def getCalcExport(self, qtype=QueryType.BOTH):
        if qtype == QueryType.PEAK:    return self._export_peak
        if qtype == QueryType.OFFPEAK: return self._export_offpeak
        return self._export

    def getCalcPV(self):          return self._pv
    def getCalcLoad(self):        return self._load
    def getCalcLoadPeak(self):    return self._load_peak
    def getCalcLoadOffPeak(self): return self._load_offpeak
    def getSuppliedLoad(self):    return self._supplied_load
    def getSuppliedImport(self):  return self._supplied_import
    def getSuppliedPV(self):      return self._supplied_pv
    def getSuppliedExport(self):  return self._supplied_export


# ─── VirtualBattery ──────────────────────────────────────────────────────────

def test_virtual_battery_recharge_fills_and_tracks_charge_amount():
    battery = VirtualBattery(PriceData(), batterySize=5000)
    battery.batteryStatus = 3000
    battery.recharge()
    assert battery.batteryStatus == 5000
    assert battery.chargeAmount == 2000
    assert battery.getChargeAmountkWh() == 2.0


def test_virtual_battery_utilise_draws_with_92_percent_efficiency():
    battery = VirtualBattery(PriceData(), batterySize=5000)
    t = datetime.strptime("12:00", "%H:%M")
    result = battery.utilise(100, t)
    assert result == 0
    assert battery.batteryStatus == 4900
    assert battery.drawn == pytest.approx(92.0)
    assert battery.totalDrawnkWh() == 0.09


def test_virtual_battery_utilise_returns_shortfall_when_exhausted():
    battery = VirtualBattery(PriceData(), batterySize=1000)
    battery.batteryStatus = 100
    t = datetime.strptime("12:00", "%H:%M")
    result = battery.utilise(200, t)
    assert battery.batteryStatus == 0
    assert result > 0
    assert battery.drawn == pytest.approx(100 * 0.92)
    assert battery.extraRequired == pytest.approx(result)


def test_virtual_battery_set_ran_out_increments_days():
    battery = VirtualBattery(PriceData())
    battery.setRanOut()
    battery.setRanOut()
    assert battery.daysRunOut == 2


def test_virtual_battery_pv_charge_does_nothing_when_disabled():
    battery = VirtualBattery(PriceData(), batterySize=5000, UsePV=0)
    battery.batteryStatus = 500
    t = datetime.strptime("12:00", "%H:%M")
    battery.PVCharge(100, t)
    assert battery.batteryStatus == 500
    assert battery.PVInput == 0


def test_virtual_battery_pv_charge_charges_at_96_percent_efficiency():
    battery = VirtualBattery(PriceData(), batterySize=5000, UsePV=1,
                             startCharging=1000, stopCharging=2000)
    battery.batteryStatus = 500  # below startCharging → triggers shouldPVCharge=1
    t = datetime.strptime("12:00", "%H:%M")
    battery.PVCharge(100, t)
    assert battery.batteryStatus == pytest.approx(500 + 100 * 0.96)
    assert battery.PVInput == pytest.approx(100 * 0.96)


def test_virtual_battery_pv_charge_stops_at_stop_threshold():
    battery = VirtualBattery(PriceData(), batterySize=5000, UsePV=1,
                             startCharging=1000, stopCharging=2000)
    battery.batteryStatus = 2000  # at stopCharging → clears shouldPVCharge
    battery.shouldPVCharge = 1
    t = datetime.strptime("12:00", "%H:%M")
    battery.PVCharge(100, t)
    assert battery.batteryStatus == 2000
    assert battery.PVInput == 0


def test_virtual_battery_get_savings_computes_correctly():
    pd = make_price_data(peak=0.30, offpeak=0.10, export=0.15)
    battery = VirtualBattery(pd, batterySize=10000)
    battery.chargeAmount = 10000  # 10 kWh charged off-peak
    battery.drawn = 5000          # 5 kWh drawn at peak
    battery.PVInput = 2000        # 2 kWh from PV (foregone export)
    battery.exported = 1000       # 1 kWh re-exported

    battery.getSavings()

    assert battery.costFromGrid   == pytest.approx(10000 / 1000 * 0.10, rel=1e-6)  # 1.00
    assert battery.nominalCost    == pytest.approx(5000 * 0.30 / 1000,  rel=1e-6)  # 1.50
    assert battery.lostExportCost == pytest.approx(2000 * 0.15 / 1000,  rel=1e-6)  # 0.30
    assert battery.exportCost     == pytest.approx(1000 / 1000 * 0.15,  rel=1e-6)  # 0.15
    assert battery.savings        == pytest.approx(1.50 + 0.15 - 1.00 - 0.30)      # 0.35


# ─── EnergySummary ───────────────────────────────────────────────────────────

def make_summary(interest_rate=3.7, cumulative=0.0, alt_invest=0.0):
    pd = make_price_data(interest_rate=interest_rate)
    battery = VirtualBattery(pd)
    return EnergySummary(pd, battery, currentCumulativeSavings=cumulative,
                         currentAltInvestmentValue=alt_invest), pd, battery


def make_day(calc_import=95, calc_import_peak=50, calc_import_offpeak=45,
             calc_export=10, calc_export_peak=10, calc_export_offpeak=0,
             calc_pv=200, calc_load=200, calc_load_peak=150, calc_load_offpeak=50,
             supplied_load=5.2, supplied_import=1.4, supplied_pv=8.3, supplied_export=2.1):
    return MockEnergyDay(
        calc_import=calc_import, calc_import_peak=calc_import_peak,
        calc_import_offpeak=calc_import_offpeak,
        calc_export=calc_export, calc_export_peak=calc_export_peak,
        calc_export_offpeak=calc_export_offpeak,
        calc_pv=calc_pv, calc_load=calc_load,
        calc_load_peak=calc_load_peak, calc_load_offpeak=calc_load_offpeak,
        supplied_load=supplied_load, supplied_import=supplied_import,
        supplied_pv=supplied_pv, supplied_export=supplied_export,
    )


def test_energy_summary_add_data_accumulates_totals():
    summary, _, _ = make_summary()
    summary.addData(make_day())

    assert summary.days == 1
    assert summary.totalCalcImport == 95
    assert summary.totalCalcImportPeak == 50
    assert summary.totalCalcImportOffPeak == 45
    assert summary.totalCalcExport == 10
    assert summary.totalCalcPV == 200
    assert summary.totalCalcLoad == 200
    assert summary.totalSuppliedLoad == pytest.approx(5.2)
    assert summary.totalSuppliedImport == pytest.approx(1.4)
    assert summary.totalSavedCalc == 200 - 95  # load minus import (Wh)


def test_energy_summary_add_data_accumulates_across_multiple_days():
    summary, _, _ = make_summary()
    summary.addData(make_day(calc_import=95, calc_pv=200))
    summary.addData(make_day(calc_import=80, calc_pv=150))

    assert summary.days == 2
    assert summary.totalCalcImport == 175
    assert summary.totalCalcPV == 350


def test_energy_summary_recalculate_total_cost():
    summary, pd, _ = make_summary()
    summary.addData(make_day())
    summary.recalculate()

    # TotalCost = (peak_import_kWh * peak_rate) + (offpeak_import_kWh * offpeak_rate) + standing
    expected = (50 / 1000 * 0.30) + (45 / 1000 * 0.10) + 0.60
    assert summary.TotalCost == pytest.approx(expected, rel=1e-6)


def test_energy_summary_recalculate_compare_cost():
    summary, pd, _ = make_summary()
    summary.addData(make_day())
    summary.recalculate()

    expected = (95 / 1000 * 0.25) + 0.53
    assert summary.CompareCost == pytest.approx(expected, rel=1e-6)


def test_energy_summary_recalculate_export_amount():
    summary, pd, _ = make_summary()
    summary.addData(make_day())
    summary.recalculate()

    expected = (10 / 1000) * 0.15
    assert summary.totalExportAmountCalc == pytest.approx(expected, rel=1e-6)


def test_energy_summary_new_month_adds_interest_to_cumulative():
    summary, pd, _ = make_summary(interest_rate=12.0, cumulative=1000.0, alt_invest=2000.0)
    summary.addData(make_day())

    summary.newMonth()

    # altInvestValue grows by 1% per month (12% / 1200 * month)
    assert summary.altInvestValue == pytest.approx(2000.0 * (1 + 12.0 / 1200), rel=1e-6)
    # cumulativeSavings grows by interest (10) plus any period savings
    assert summary.cumulativeSavings > 1000.0


# ─── EnergySummaryAggregator ─────────────────────────────────────────────────

def test_aggregator_grand_totals_sums_across_periods():
    pd1 = make_price_data()
    s1 = EnergySummary(pd1, VirtualBattery(pd1))
    s1.addData(make_day(calc_import=100, calc_import_peak=60, calc_import_offpeak=40,
                        calc_pv=200, calc_load=200, calc_load_peak=160, calc_load_offpeak=40,
                        supplied_load=5.0, supplied_import=2.0, supplied_pv=4.0, supplied_export=1.0,
                        calc_export=20, calc_export_peak=20, calc_export_offpeak=0))

    pd2 = make_price_data()
    s2 = EnergySummary(pd2, VirtualBattery(pd2))
    s2.addData(make_day(calc_import=80, calc_import_peak=40, calc_import_offpeak=40,
                        calc_pv=150, calc_load=150, calc_load_peak=110, calc_load_offpeak=40,
                        supplied_load=4.0, supplied_import=1.5, supplied_pv=3.0, supplied_export=0.5,
                        calc_export=10, calc_export_peak=10, calc_export_offpeak=0))

    agg = EnergySummaryAggregator()
    agg.add_summary(s1)
    agg.add_summary(s2)
    totals = agg.get_grand_totals()

    assert totals["days"] == 2
    assert totals["totalCalcImport"] == pytest.approx((100 + 80) / 1000, rel=1e-6)
    assert totals["totalCalcPV"] == pytest.approx((200 + 150) / 1000, rel=1e-6)
    assert totals["totalSuppliedLoad"] == pytest.approx(5.0 + 4.0, rel=1e-6)
    assert totals["totalCalcExport"] == pytest.approx((20 + 10) / 1000, rel=1e-6)


def test_aggregator_get_grand_totals_rounds_to_two_decimal_places():
    pd = make_price_data()
    s = EnergySummary(pd, VirtualBattery(pd))
    s.addData(make_day(calc_import=1, calc_pv=1, calc_load=1,
                       calc_import_peak=1, calc_import_offpeak=0,
                       calc_load_peak=1, calc_load_offpeak=0))
    agg = EnergySummaryAggregator()
    agg.add_summary(s)
    totals = agg.get_grand_totals()
    for val in totals.values():
        if isinstance(val, float):
            assert val == round(val, 2)


def test_aggregator_update_last_summary():
    pd = make_price_data()
    s = EnergySummary(pd, VirtualBattery(pd))
    agg = EnergySummaryAggregator()
    agg.add_summary(s)
    agg.update_last_summary(make_day(calc_import=50))
    assert agg.get_last_summary().totalCalcImport == 50


def test_aggregator_raises_when_empty():
    agg = EnergySummaryAggregator()
    with pytest.raises(IndexError):
        agg.update_last_summary(make_day())
    with pytest.raises(IndexError):
        agg.get_last_summary()
