from dataclasses import dataclass, asdict

from analysis.energyday import EnergyDay
from analysis.energysummary import QueryType
from analysis.pricedata import PriceData


@dataclass
class DailyRow:
    date: str
    pv_kwh: float
    load_kwh: float
    export_kwh: float
    import_kwh: float
    spend: float
    export_income: float
    savings: float
    savings_inc_seg: float
    cumulative_savings: float
    cumulative_investment: float
    roi_pct: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    battery_pv_charge_kwh: float
    battery_export_kwh: float


class DailySeriesBuilder:
    """Accumulates one DailyRow per date processed, for charting.

    Computes spend/savings using the same per-kWh formula EnergySummary.recalculate()
    uses (peak/offpeak import & load rates + standing charge), applied to a single
    day's EnergyDay getters instead of a period total. cumulative_investment is held
    constant (originalPrice, plus batteryPrice if enabled) so a payback chart's
    crossover point reads as raw cash flow, matching energyprices.py's payback_years.
    """

    def __init__(self, cumulative_investment: float):
        self._cumulative_investment = cumulative_investment
        self._cumulative_savings = 0.0
        self._rows: list[DailyRow] = []

    def add_day(self, date: str, energyday: EnergyDay, price_data: PriceData,
                battery_delta: dict) -> DailyRow:
        import_peak_kwh = energyday.get_calc_import(QueryType.PEAK) / 1000
        import_offpeak_kwh = energyday.get_calc_import(QueryType.OFFPEAK) / 1000
        load_peak_kwh = energyday.get_calc_load_peak() / 1000
        load_offpeak_kwh = energyday.get_calc_load_off_peak() / 1000
        export_kwh = energyday.get_calc_export() / 1000

        spend = (
            import_peak_kwh * price_data.current_peak
            + import_offpeak_kwh * price_data.current_off_peak
            + price_data.standing_charge
        )
        cost_without_solar = (
            load_peak_kwh * price_data.current_peak
            + load_offpeak_kwh * price_data.current_off_peak
            + price_data.standing_charge
        )
        export_income = export_kwh * price_data.current_export
        savings = cost_without_solar - spend
        savings_inc_seg = savings + export_income

        self._cumulative_savings += savings_inc_seg
        roi_pct = (
            100 * self._cumulative_savings / self._cumulative_investment
            if self._cumulative_investment > 0 else 0.0
        )

        row = DailyRow(
            date=date,
            pv_kwh=energyday.get_calc_pv() / 1000,
            load_kwh=energyday.get_calc_load() / 1000,
            export_kwh=export_kwh,
            import_kwh=import_peak_kwh + import_offpeak_kwh,
            spend=spend,
            export_income=export_income,
            savings=savings,
            savings_inc_seg=savings_inc_seg,
            cumulative_savings=self._cumulative_savings,
            cumulative_investment=self._cumulative_investment,
            roi_pct=roi_pct,
            battery_charge_kwh=battery_delta['charge_kwh'],
            battery_discharge_kwh=battery_delta['discharge_kwh'],
            battery_pv_charge_kwh=battery_delta['pv_charge_kwh'],
            battery_export_kwh=battery_delta['export_kwh'],
        )
        self._rows.append(row)
        return row

    def rows(self) -> list[DailyRow]:
        return self._rows

    def to_dicts(self) -> list[dict]:
        return [asdict(r) for r in self._rows]
