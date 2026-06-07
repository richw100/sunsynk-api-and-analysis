import re
from datetime import datetime

from analysis.pricedata import PriceData
from analysis.virtualbattery import VirtualBattery
from analysis.energysummary import EnergySummary, EnergySummaryAggregator


class EnergyPrices:
    def __init__(self, prices, battery: VirtualBattery, original_price: float = None,
                 label: str = ""):
        self.prices = prices
        self.label = label
        self.aggregator = EnergySummaryAggregator()
        self.price_data = PriceData()
        self.original_price = original_price if original_price is not None else self.price_data.original_price
        self.orig_battery = battery
        self.battery = self._make_battery(self.price_data)
        self.aggregator.add_summary(EnergySummary(self.price_data, self.battery, 0, self.original_price))
        self.grand_totals = self.aggregator.get_grand_totals()
        self.changed = True

    def _make_battery(self, price_data: PriceData) -> VirtualBattery:
        return VirtualBattery(
            price_data,
            self.orig_battery.battery_size,
            self.orig_battery.pv_enabled,
            self.orig_battery.start_charging,
            self.orig_battery.stop_charging,
            export_window_start=self.orig_battery.export_window_start,
            export_window_stop=self.orig_battery.export_window_stop,
            discharge_efficiency=self.orig_battery.discharge_efficiency,
            pv_charge_efficiency=self.orig_battery.pv_charge_efficiency,
            max_output_w=self.orig_battery.max_output_w,
        )

    def check_date(self, date_in: str):
        if re.match("[0-9]{4}-[0-9]{2}-01$", date_in):
            last_summary = self.aggregator.get_last_summary()
            last_summary.new_month()

        date_dt = datetime.strptime(date_in, "%Y-%m-%d")
        if date_dt < self.price_data.current_start or date_dt > self.price_data.current_stop:
            for item in self.prices['data']['prices']:
                new_start = datetime.strptime(item['datefrom'], "%Y-%m-%d")
                new_stop = datetime.strptime(item['dateto'], "%Y-%m-%d")
                if new_start <= date_dt <= new_stop:
                    self.price_data = PriceData()
                    self.price_data.current_off_peak = float(item['offpeakRate'])
                    self.price_data.current_peak = float(item['peakRate'])
                    self.price_data.current_export = float(item['exportRate'])
                    self.price_data.current_off_peak_start = item['offpeakStart']
                    self.price_data.current_off_peak_stop = item['offpeakStop']
                    self.price_data.standing_charge = float(item['standingCharge'])
                    if 'InterestRate' in item:
                        self.price_data.interest_rate = float(item['InterestRate'])
                    self.price_data.current_start = new_start
                    self.price_data.current_stop = new_stop
                    self.battery = self._make_battery(self.price_data)

                    last_summary = self.aggregator.get_last_summary()
                    remainder = last_summary.get_remainder()
                    self.aggregator.add_summary(EnergySummary(
                        self.price_data, self.battery,
                        last_summary.cumulative_savings, last_summary.alt_invest_value, remainder
                    ))
                    break

    def add_data(self, energyday):
        self.changed = True
        self.aggregator.update_last_summary(energyday)

    def get_grand_totals(self):
        if self.changed:
            self.changed = False
            self.grand_totals = self.aggregator.get_grand_totals()
        return self.grand_totals

    def get_derived(self):
        t = self.get_grand_totals()
        seg = t['total_export_amount_calc']
        op_excess_savings = t['total_off_peak_excess_savings']

        def pct(amount):
            return round(amount * 100 / self.original_price, 2)

        bill_savings         = round(t['total_cost_without_solar'] - t['total_cost'], 2)
        bill_savings_inc_seg = round(bill_savings + seg, 2)

        calc_savings             = round(t['total_savings_calc'] + op_excess_savings, 2)
        calc_savings_exc_seg     = round(calc_savings - seg, 2)
        supplied_savings         = round(t['total_savings_supplied'] + op_excess_savings, 2)
        supplied_savings_exc_seg = round(supplied_savings - t['total_export_amount_supplied'], 2)

        battery_savings = round(
            t['battery_nominal_cost'] + t['battery_export_cost']
            - t['battery_cost_from_grid'] - t['battery_lost_export_cost'], 2
        )

        return {
            "t": t,
            "seg": seg,
            "bill_savings": bill_savings,
            "bill_savings_inc_seg": bill_savings_inc_seg,
            "calc_savings": calc_savings,
            "calc_savings_exc_seg": calc_savings_exc_seg,
            "supplied_savings": supplied_savings,
            "supplied_savings_exc_seg": supplied_savings_exc_seg,
            "calc_roi_pct": pct(t['total_savings_calc']),
            "supplied_roi_pct": pct(t['total_savings_supplied']),
            "battery_savings": battery_savings,
        }

    def print_costs(self):
        d = self.get_derived()
        t = d['t']
        print("")
        print("ENERGY COSTS")
        print(f"  Total paid to energy company:                   £{t['total_cost']}")
        print(f"  Total paid after deducting SEG income:          £{round(t['total_cost'] - d['seg'], 2)}")
        print(f"  Smart Export Guarantee (SEG) income:            £{d['seg']}")
        print(f"  Estimated cost without solar or battery:        £{t['total_cost_without_solar']}")

    def print_savings(self):
        d = self.get_derived()
        print("")
        print("SOLAR SAVINGS  (interval-calculated | inverter-reported)")
        print(f"  Savings excl. export income:   £{d['calc_savings_exc_seg']} | £{d['supplied_savings_exc_seg']}")
        print(f"  Savings incl. SEG income:      £{d['bill_savings_inc_seg']}")

    def print_return_on_investment(self):
        d = self.get_derived()
        t = d['t']
        print("")
        print("RETURN ON INVESTMENT")
        print(f"  Solar savings from generation (excl. off-peak shifting):")
        print(f"    Interval-calculated: £{t['total_savings_calc']} ({d['calc_roi_pct']}% of install cost)")
        print(f"    Inverter-reported:   £{t['total_savings_supplied']} ({d['supplied_roi_pct']}% of install cost)")
        print(f"  Total savings incl. off-peak load shifting:")
        print(f"    Interval-calculated: £{d['calc_savings']}    Inverter-reported: £{d['supplied_savings']}")
        print("")
        last_summary = self.aggregator.get_last_summary()
        final_remainder = last_summary.get_remainder()
        cumulative = round(last_summary.cumulative_savings + final_remainder, 2)
        alt = round(last_summary.alt_invest_value, 2)
        percentage = round(100 * cumulative / last_summary.alt_invest_value, 2)
        print(f"  Cumulative savings (with compound interest):    £{cumulative}")
        print(f"  Alternative investment value (same interest):   £{alt}")
        print(f"  Net ROI:                                        {percentage}%")

    def print_energy_summary(self):
        t = self.get_grand_totals()
        print("")
        print("ENERGY TOTALS")
        print("  (Two values: interval-calculated from 5-min API data | inverter-reported daily kWh)")
        print(f"  Export:  {t['total_calc_export']}kWh | {t['total_supplied_export']}kWh"
              f"    Peak: {t['total_calc_export_peak']}kWh   Off-peak: {t['total_calc_export_off_peak']}kWh")
        print(f"  Import:  {t['total_calc_import']}kWh | {t['total_supplied_import']}kWh"
              f"    Peak: {t['total_calc_import_peak']}kWh   Off-peak: {t['total_calc_import_off_peak']}kWh")
        print(f"  PV gen:  {t['total_calc_pv']}kWh | {t['total_supplied_pv']}kWh")
        print(f"  Load:    {t['total_calc_load']}kWh | {t['total_supplied_load']}kWh")
        print(f"  Days:    {t['days']}")

    def print_averages(self):
        t = self.get_grand_totals()
        days = t['days']
        if days > 0:
            print("")
            print("DAILY AVERAGES  (interval-calculated | inverter-reported)")
            print(f"  Export: {round(t['total_calc_export']/days, 2)}kWh | {round(t['total_supplied_export']/days, 2)}kWh"
                  f"    Peak: {round(t['total_calc_export_peak']/days, 2)}kWh   Off-peak: {round(t['total_calc_export_off_peak']/days, 2)}kWh")
            print(f"  Import: {round(t['total_calc_import']/days, 2)}kWh | {round(t['total_supplied_import']/days, 2)}kWh"
                  f"    Peak: {round(t['total_calc_import_peak']/days, 2)}kWh   Off-peak: {round(t['total_calc_import_off_peak']/days, 2)}kWh")

    def print_totals(self):
        t = self.get_grand_totals()
        print("")
        print("OFF-PEAK IMPORT ANALYSIS")
        print(f"  Total off-peak import:               {t['total_calc_import_off_peak']}kWh")
        print(f"  Excess above expected baseline:      {t['total_off_peak_excess']}kWh  (load shifted to off-peak)")
        print(f"  Saving from off-peak load shifting:  £{t['total_off_peak_excess_savings']}  (excess × (peak − off-peak rate))")

    def print_battery(self):
        d = self.get_derived()
        t = d['t']
        days = t['days']
        if days > 0:
            print("")
            print("VIRTUAL BATTERY SIMULATION  (models a battery charged at off-peak, discharged at peak)")
            print(f"  Charged from grid at off-peak:              {t['battery_charge_amount']}kWh  (cost: £{t['battery_cost_from_grid']})")
            print(f"  Peak-rate value of energy delivered:        £{t['battery_nominal_cost']}  (what it would cost drawn from grid at peak)")
            print(f"  PV energy diverted to battery:              foregone export income: £{t['battery_lost_export_cost']}")
            print(f"  Re-exported to grid during export window:   {t['battery_exported']}kWh = £{t['battery_export_cost']}")
            print(f"  Days battery ran out before peak demand met: {t['battery_days_run_out']}  (shortfall: {t['battery_extra_required']}kWh)")
            print(f"  Net potential saving:                       £{d['battery_savings']}")
            print(f"    = peak saving £{t['battery_nominal_cost']} + export £{t['battery_export_cost']}"
                  f" − charging cost £{t['battery_cost_from_grid']} − foregone export £{t['battery_lost_export_cost']}")
            print(f"  Per day: £{round(d['battery_savings']/days, 2)}   Annualised: £{round(365 * d['battery_savings']/days, 2)}")
