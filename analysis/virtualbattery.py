from datetime import datetime

from analysis.pricedata import PriceData


class VirtualBattery:
    def __init__(self, price_data: PriceData, battery_size=5000, use_pv=0,
                 start_charging=1000, stop_charging=2000,
                 export_window_start="17:00", export_window_stop="19:00",
                 discharge_efficiency=0.92, pv_charge_efficiency=0.96, max_output_w=2400,
                 use_export=False, charge_efficiency=1.0, grid_charge=True,
                 discharge_reserve_wh=0, discharge_reserve_until="17:00"):
        self.battery_size = battery_size
        self.battery_status = battery_size
        self.charge_amount = 0
        self.should_pv_charge = 0
        self.pv_input = 0
        self.pv_diverted = 0      # raw PV surplus redirected to battery (pre-efficiency)
        self.drawn = 0
        self.pv_enabled = use_pv
        self.start_charging = start_charging
        self.stop_charging = stop_charging
        self.price_data = price_data
        self.charge_efficiency = charge_efficiency
        self.grid_charge = grid_charge
        self.lost_export_cost = 0
        self.export = 1 if use_export else 0
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
        self.discharge_reserve_wh = discharge_reserve_wh
        self.discharge_reserve_until_str = discharge_reserve_until
        self.discharge_reserve_until = datetime.strptime(discharge_reserve_until, "%H:%M")

        # Number of 5-minute intervals in the export window
        window_seconds = (self.export_stop - self.export_start).total_seconds()
        self._export_intervals = max(1, round(window_seconds / 300))

    def recharge(self):
        if self.grid_charge:
            self.charge_amount += self.battery_size - self.battery_status
            self.battery_status = self.battery_size

    def discharge(self, time: datetime):
        run_down_amount = 1000
        if self.export == 1:
            if self.export_start < time < self.export_stop:
                if self.battery_status > run_down_amount:
                    max_5min_drain = (self.max_output_w / 12) / self.discharge_efficiency
                    five_min_drain = min(
                        max_5min_drain,
                        (self.battery_size - run_down_amount) / self._export_intervals
                    )
                    self.battery_status -= five_min_drain
                    self.exported += five_min_drain * self.discharge_efficiency

    def set_ran_out(self):
        self.days_run_out += 1

    def utilise(self, value, time: datetime):
        self.discharge(time)

        # Cap demand by max inverter output per 5-minute interval
        actual_output = min(value, self.max_output_w / 12)
        # Hold back reserve until cutover time
        if self.discharge_reserve_wh > 0 and time < self.discharge_reserve_until:
            available_charge = max(0, self.battery_status - self.discharge_reserve_wh)
        else:
            available_charge = self.battery_status
        deliverable = available_charge * self.discharge_efficiency

        if actual_output > deliverable:
            # Battery exhausted (or reserve blocking) before meeting demand
            self.drawn += deliverable
            shortfall = actual_output - deliverable
            self.battery_status -= available_charge
            self.extra_required += shortfall
            return shortfall
        # Battery drains by output / efficiency (more stored energy consumed than delivered)
        self.battery_status -= actual_output / self.discharge_efficiency
        self.drawn += actual_output
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
                self.pv_diverted += value      # raw surplus foregone from export

    def total_drawn_kwh(self):
        return round(self.drawn/1000, 2)

    def get_charge_amount_kwh(self):
        return round(self.charge_amount/1000, 2)

    def get_pv_charge_kwh(self):
        return round(self.pv_input/1000, 2)

    def get_savings(self):
        # cost_from_grid uses charge_amount (stored Wh) adjusted for grid charging losses
        self.cost_from_grid = round(
            (self.charge_amount / self.charge_efficiency / 1000) * self.price_data.current_off_peak, 2
        )
        self.nominal_cost = round((self.drawn * self.price_data.current_peak) / 1000, 2)
        # lost export is costed on the raw PV surplus diverted, not what was stored
        self.lost_export_cost = round((self.pv_diverted * self.price_data.current_export) / 1000, 2)
        self.export_cost = round((self.exported / 1000) * self.price_data.current_export, 2)
        self.savings = self.nominal_cost + self.export_cost - self.cost_from_grid - self.lost_export_cost
