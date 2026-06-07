from datetime import datetime

from analysis.pricedata import PriceData


class VirtualBattery:
    def __init__(self, price_data: PriceData, battery_size=5000, use_pv=0,
                 start_charging=1000, stop_charging=2000,
                 export_window_start="17:00", export_window_stop="19:00",
                 discharge_efficiency=0.92, pv_charge_efficiency=0.96, max_output_w=2400):
        self.battery_size = battery_size
        self.battery_status = battery_size
        self.charge_amount = 0
        self.should_pv_charge = 0
        self.pv_input = 0
        self.drawn = 0
        self.pv_enabled = use_pv
        self.start_charging = start_charging
        self.stop_charging = stop_charging
        self.price_data = price_data
        self.lost_export_cost = 0
        self.export = 0
        self.export_cost = 0

        self.savings = 0
        self.cost_from_grid = 0
        self.nominal_cost = 0
        self.exported = 0
        self.extra_required = 0
        self.days_run_out = 0

        self.export_window_start = export_window_start
        self.export_window_stop = export_window_stop
        self.export_start = datetime.strptime(export_window_start, "%H:%M")
        self.export_stop = datetime.strptime(export_window_stop, "%H:%M")
        self.discharge_efficiency = discharge_efficiency
        self.pv_charge_efficiency = pv_charge_efficiency
        self.max_output_w = max_output_w

    def recharge(self):
        self.charge_amount += self.battery_size - self.battery_status
        self.battery_status = self.battery_size

    def discharge(self, time: datetime):
        run_down_amount = 1000
        if self.export == 1:
            if time > self.export_start and time < self.export_stop:
                if self.battery_status > run_down_amount:
                    max_5min_wh = (self.max_output_w / 12) / self.discharge_efficiency
                    five_min_export = min(max_5min_wh, (self.battery_size - run_down_amount) / (6*60/5))
                    self.battery_status -= five_min_export
                    self.exported += five_min_export * self.discharge_efficiency

    def set_ran_out(self):
        self.days_run_out += 1

    def utilise(self, value, time: datetime):
        self.discharge(time)

        max_5min_wh = (self.max_output_w / 12) / self.discharge_efficiency
        maxvalue = min(max_5min_wh, value)

        if maxvalue > (self.battery_status / self.discharge_efficiency):
            self.drawn += self.battery_status * self.discharge_efficiency
            temp = (maxvalue - self.battery_status) / self.discharge_efficiency
            self.battery_status = 0
            self.extra_required += temp
            return temp
        self.battery_status -= value
        self.drawn += value * self.discharge_efficiency
        return 0

    def pv_charge(self, value, time: datetime):
        self.discharge(time)

        if self.pv_enabled == 1:
            if self.battery_status < self.start_charging:
                self.should_pv_charge = 1
            if self.battery_status >= self.stop_charging:
                self.should_pv_charge = 0

        if self.should_pv_charge == 1:
            post_efficiency_value = value * self.pv_charge_efficiency

            if self.battery_status + post_efficiency_value <= self.battery_size:
                self.battery_status += post_efficiency_value
                self.pv_input += post_efficiency_value

    def total_drawn_kwh(self):
        return round(self.drawn/1000, 2)

    def get_charge_amount_kwh(self):
        return round(self.charge_amount/1000, 2)

    def get_pv_charge_kwh(self):
        return round(self.pv_input/1000, 2)

    def get_savings(self):
        self.cost_from_grid = round((self.charge_amount/1000)*self.price_data.current_off_peak, 2)
        self.nominal_cost = round((self.drawn*self.price_data.current_peak)/1000, 2)
        self.lost_export_cost = round((self.pv_input*self.price_data.current_export)/1000, 2)
        self.export_cost = round((self.exported/1000)*self.price_data.current_export, 2)
        self.savings = self.nominal_cost + self.export_cost - self.cost_from_grid - self.lost_export_cost
