from enum import Enum
from datetime import datetime

from analysis.pricedata import PriceData
from analysis.virtualbattery import VirtualBattery


class QueryType(Enum):
    PEAK = 1
    OFFPEAK = 2
    BOTH = 3


class EnergySummary:
    def __init__(self, price_data: PriceData, battery: VirtualBattery,
                 current_cumulative_savings=0, current_alt_investment_value=0, remainder_input=0):

        self.battery = battery

        self.days = 0
        self.total_calc_export = 0
        self.total_calc_import = 0
        self.total_calc_pv = 0
        self.total_calc_load = 0
        self.total_calc_load_off_peak = 0
        self.total_calc_load_peak = 0

        self.total_supplied_export = 0
        self.total_supplied_import = 0
        self.total_supplied_load = 0
        self.total_supplied_pv = 0

        self.total_calc_import_off_peak = 0
        self.total_calc_export_off_peak = 0
        self.total_calc_import_peak = 0
        self.total_calc_export_peak = 0

        self.off_peak_excess = 0
        self.off_peak_excess_savings = 0

        self.total_saved_calc = 0
        self.total_saved_supplied = 0
        self.total_saved_amount_calc = 0
        self.total_saved_amount_supplied = 0
        self.total_export_amount_calc = 0
        self.total_export_amount_supplied = 0
        self.total_savings_calc = 0
        self.total_savings_supplied = 0
        self.total_cost = 0
        self.total_cost_without_solar = 0
        self.remainder_input = remainder_input

        self.total_saved_in_period = remainder_input
        self.cumulative_savings = current_cumulative_savings
        self.alt_invest_value = current_alt_investment_value
        self.added_to_cumulative = 0

        time_diff = (datetime.strptime(price_data.current_off_peak_stop, "%H:%M")
                     - datetime.strptime(price_data.current_off_peak_start, "%H:%M"))
        price_data.off_peak_average = 0.96 * (time_diff.total_seconds() / 3600) / 7

        self.price_data = price_data

    def get_remainder(self):
        self.recalculate()
        self.total_saved_in_period = self.total_savings_calc + self.remainder_input
        to_add = self.total_saved_in_period - self.added_to_cumulative
        self.added_to_cumulative += to_add
        return to_add

    def new_month(self):
        remainder = self.get_remainder()
        interest = self.cumulative_savings * (self.price_data.interest_rate/1200)
        self.cumulative_savings += remainder + interest

        interest = self.alt_invest_value * (self.price_data.interest_rate/1200)
        self.alt_invest_value += interest

    def add_data(self, energyday):
        self.days += 1
        self.total_calc_export += energyday.get_calc_export(QueryType.BOTH)
        self.total_calc_import += energyday.get_calc_import()
        self.total_calc_pv += energyday.get_calc_pv()
        self.total_calc_load += energyday.get_calc_load()
        self.total_calc_load_off_peak += energyday.get_calc_load_off_peak()
        self.total_calc_load_peak += energyday.get_calc_load_peak()

        self.total_supplied_export += energyday.get_supplied_export()
        self.total_supplied_import += energyday.get_supplied_import()
        self.total_supplied_load += energyday.get_supplied_load()
        self.total_supplied_pv += energyday.get_supplied_pv()

        self.total_calc_import_off_peak += energyday.get_calc_import(QueryType.OFFPEAK)
        self.total_calc_export_off_peak += energyday.get_calc_export(QueryType.OFFPEAK)
        self.total_calc_import_peak += energyday.get_calc_import(QueryType.PEAK)
        self.total_calc_export_peak += energyday.get_calc_export(QueryType.PEAK)

        self.total_saved_calc += energyday.get_calc_load() - energyday.get_calc_import()
        self.total_saved_supplied += energyday.get_supplied_load() - energyday.get_supplied_import()

    def recalculate(self):
        self.total_saved_amount_calc = (
            (self.total_calc_load_peak/1000 - self.total_calc_import_peak/1000) * self.price_data.current_peak
            + (self.total_calc_load_off_peak/1000 - self.total_calc_import_off_peak/1000) * self.price_data.current_off_peak
        )

        self.total_saved_amount_supplied = self.total_saved_supplied * self.price_data.current_peak

        self.total_export_amount_calc = (self.total_calc_export/1000) * self.price_data.current_export
        self.total_export_amount_supplied = self.total_supplied_export * self.price_data.current_export

        self.total_savings_calc = self.total_saved_amount_calc + self.total_export_amount_calc
        self.total_savings_supplied = self.total_saved_amount_supplied + self.total_export_amount_supplied

        self.off_peak_excess = (self.total_calc_import_off_peak/1000) - (self.price_data.off_peak_average*self.days)
        self.off_peak_excess_savings = self.off_peak_excess * (self.price_data.current_peak - self.price_data.current_off_peak)

        standing_charge = self.days * self.price_data.standing_charge

        self.total_cost = (
            (self.total_calc_import_peak/1000 * self.price_data.current_peak)
            + (self.total_calc_import_off_peak/1000 * self.price_data.current_off_peak)
            + standing_charge
        )
        self.total_cost_without_solar = (
            (self.total_calc_load_peak/1000 * self.price_data.current_peak)
            + (self.total_calc_load_off_peak/1000 * self.price_data.current_off_peak)
            + standing_charge
        )


class EnergySummaryAggregator:
    def __init__(self):
        self.summaries = []

    def add_summary(self, summary: EnergySummary):
        self.summaries.append(summary)

    def get_grand_totals(self):
        totals = {
            "total_calc_export": 0,
            "total_calc_import": 0,
            "total_calc_pv": 0,
            "total_calc_load": 0,
            "total_supplied_export": 0,
            "total_supplied_import": 0,
            "total_supplied_load": 0,
            "total_supplied_pv": 0,
            "total_calc_import_off_peak": 0,
            "total_calc_export_off_peak": 0,
            "total_calc_import_peak": 0,
            "total_calc_export_peak": 0,
            "total_off_peak_excess": 0,
            "total_off_peak_excess_savings": 0,
            "days": 0,
            "total_saved_calc": 0,
            "total_saved_supplied": 0,
            "total_saved_amount_calc": 0,
            "total_saved_amount_supplied": 0,
            "total_export_amount_calc": 0,
            "total_export_amount_supplied": 0,
            "total_savings_calc": 0,
            "total_savings_supplied": 0,
            "total_cost": 0,
            "total_cost_without_solar": 0,
            "battery_cost_from_grid": 0,
            "battery_nominal_cost": 0,
            "battery_lost_export_cost": 0,
            "battery_charge_amount": 0,
            "battery_export_cost": 0,
            "battery_exported": 0,
            "battery_days_run_out": 0,
            "battery_extra_required": 0,
        }

        for summary in self.summaries:
            summary.recalculate()
            totals["total_calc_export"] += summary.total_calc_export/1000
            totals["total_calc_import"] += summary.total_calc_import/1000
            totals["total_calc_pv"] += summary.total_calc_pv/1000
            totals["total_calc_load"] += summary.total_calc_load/1000
            totals["total_supplied_export"] += summary.total_supplied_export
            totals["total_supplied_import"] += summary.total_supplied_import
            totals["total_supplied_load"] += summary.total_supplied_load
            totals["total_supplied_pv"] += summary.total_supplied_pv
            totals["total_calc_import_off_peak"] += summary.total_calc_import_off_peak/1000
            totals["total_calc_export_off_peak"] += summary.total_calc_export_off_peak/1000
            totals["total_calc_import_peak"] += summary.total_calc_import_peak/1000
            totals["total_calc_export_peak"] += summary.total_calc_export_peak/1000
            totals["total_off_peak_excess"] += summary.off_peak_excess
            totals["total_off_peak_excess_savings"] += summary.off_peak_excess_savings
            totals["days"] += summary.days
            totals["total_saved_calc"] += summary.total_saved_calc/1000
            totals["total_saved_supplied"] += summary.total_saved_supplied
            totals["total_saved_amount_calc"] += summary.total_saved_amount_calc
            totals["total_saved_amount_supplied"] += summary.total_saved_amount_supplied
            totals["total_export_amount_calc"] += summary.total_export_amount_calc
            totals["total_export_amount_supplied"] += summary.total_export_amount_supplied
            totals["total_savings_calc"] += summary.total_savings_calc
            totals["total_savings_supplied"] += summary.total_savings_supplied
            totals["total_cost"] += summary.total_cost
            totals["total_cost_without_solar"] += summary.total_cost_without_solar
            summary.battery.get_savings()
            totals["battery_cost_from_grid"] += summary.battery.cost_from_grid
            totals["battery_nominal_cost"] += summary.battery.nominal_cost
            totals["battery_lost_export_cost"] += summary.battery.lost_export_cost
            totals["battery_charge_amount"] += summary.battery.get_charge_amount_kwh()
            totals["battery_export_cost"] += summary.battery.export_cost
            totals["battery_exported"] += summary.battery.exported/1000
            totals["battery_days_run_out"] += summary.battery.days_run_out
            totals["battery_extra_required"] += summary.battery.extra_required/1000

        for key, value in totals.items():
            if isinstance(value, float):
                totals[key] = round(value, 2)

        return totals

    def update_last_summary(self, energyday):
        if not self.summaries:
            raise IndexError("No EnergySummary objects to update.")
        self.summaries[-1].add_data(energyday)

    def get_last_summary(self):
        if not self.summaries:
            raise IndexError("No EnergySummary objects to update.")
        return self.summaries[-1]
