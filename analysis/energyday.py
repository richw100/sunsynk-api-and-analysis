from datetime import datetime

from sunsynk.resource import Resource
from analysis.energysummary import QueryType
from analysis.virtualbattery import VirtualBattery
from analysis.energymonth import EnergyMonth


class IntervalSummary(Resource):
    """Accumulates peak/offpeak Wh totals from 5-minute interval records for one label."""

    def __init__(self, data, battery: VirtualBattery,
                 offpeakstart="00:00", offpeakstop="00:07", is_load: bool = False):
        self.label = data['label']
        self.records = data['records']
        self.peak = 0
        self.peakexport = 0
        self.offpeak = 0
        self.offpeakexport = 0
        self.offpeakpercentage = 0
        is_off_peak = False
        recharged = False
        battery_ran_out = False

        start = datetime.strptime(offpeakstart, "%H:%M")
        stop = datetime.strptime(offpeakstop, "%H:%M")

        for record in self.records:
            time = datetime.strptime(record['time'], "%H:%M")
            value = float(record['value']) / 12

            if time >= start:
                is_off_peak = True
            if is_off_peak:
                if time >= stop:
                    is_off_peak = False

            if is_off_peak:
                if not recharged:
                    recharged = True
                    if is_load:
                        battery.recharge()

                if value > 0:
                    self.offpeak = self.offpeak + value
                else:
                    extra_load = min(value * -1, 2.3)
                    export = (value * -1) - extra_load
                    self.offpeakexport = self.offpeakexport + export
                    self.offpeak = self.offpeak - extra_load
            else:
                if value > 0:
                    self.peak = self.peak + value
                    if is_load:
                        temp = battery.utilise(value, time)
                        if temp > 0:
                            battery_ran_out = True
                else:
                    extra_load = min(value * -1, 2.3)
                    export = (value * -1) - extra_load
                    self.peakexport = self.peakexport + export
                    self.peak = self.peak - extra_load
                    if is_load:
                        battery.pv_charge(export, time)

        if battery_ran_out:
            battery.set_ran_out()

        if self.offpeak + self.peak > 0:
            self.offpeakpercentage = self.offpeak / (self.offpeak + self.peak)


class EnergyDay(Resource):
    def __init__(self, data, date: str, month: EnergyMonth, battery: VirtualBattery,
                 offpeakstart: str, offpeakstop: str):
        self.data = data
        energy = self.data['infos']
        for item in energy:
            if item['label'] == "PV":
                self.pv = IntervalSummary(item, battery, offpeakstart, offpeakstop)
            elif item['label'] == "Grid":
                self.grid = IntervalSummary(item, battery, offpeakstart, offpeakstop, is_load=True)
            elif item['label'] == "Load":
                self.load = IntervalSummary(item, battery, offpeakstart, offpeakstop)

        self.supplied_load = next((float(r['value']) for r in month.get_load()['records'] if r['time'] == date), 0.0)
        self.supplied_import = next((float(r['value']) for r in month.get_import()['records'] if r['time'] == date), 0.0)
        self.supplied_pv = next((float(r['value']) for r in month.get_pv()['records'] if r['time'] == date), 0.0)
        self.supplied_export = next((float(r['value']) for r in month.get_export()['records'] if r['time'] == date), 0.0)

    def get_calc_export(self, qtype: QueryType = QueryType.BOTH):
        if qtype == QueryType.BOTH:
            return self.grid.peakexport + self.grid.offpeakexport
        if qtype == QueryType.PEAK:
            return self.grid.peakexport
        if qtype == QueryType.OFFPEAK:
            return self.grid.offpeakexport
        return 0

    def get_calc_import(self, qtype: QueryType = QueryType.BOTH):
        if qtype == QueryType.BOTH:
            return self.grid.peak + self.grid.offpeak
        if qtype == QueryType.PEAK:
            return self.grid.peak
        if qtype == QueryType.OFFPEAK:
            return self.grid.offpeak
        return 0

    def get_calc_export_peak(self):
        return self.grid.peakexport

    def get_calc_import_peak(self):
        return self.grid.peak

    def get_calc_export_off_peak(self):
        return self.grid.offpeakexport

    def get_calc_import_off_peak(self):
        return self.grid.offpeak

    def get_calc_pv(self):
        return self.pv.peak + self.pv.offpeak

    def get_calc_pv_peak(self):
        return self.pv.peak

    def get_calc_pv_off_peak(self):
        return self.pv.offpeak

    def get_calc_load(self):
        return self.load.peak + self.load.offpeak

    def get_calc_load_peak(self):
        return self.load.peak

    def get_calc_load_off_peak(self):
        return self.load.offpeak

    def get_supplied_load(self):
        return self.supplied_load

    def get_supplied_export(self):
        return self.supplied_export

    def get_supplied_import(self):
        return self.supplied_import

    def get_supplied_pv(self):
        return self.supplied_pv

    def print(self):
        if self.get_calc_export(QueryType.BOTH) > 0:
            print(f"Export: {round(self.get_calc_export() / 1000, 2)}kWh (vs {round(self.get_supplied_export(), 1)}kWh), "
                  f"Diff = {round(self.get_supplied_export() - self.get_calc_export() / 1000, 2)}kWh  "
                  f"%age {round(self.get_supplied_export() / (self.get_calc_export() / 1000), 2)}")
        print(f"Import {round(self.get_calc_import() / 1000, 2)}kWh (vs {round(self.get_supplied_import(), 1)}kWh), "
              f"Diff = {round(self.get_supplied_import() - self.get_calc_import() / 1000, 2)}kWh")
        print(f"PV {round(self.get_calc_pv() / 1000, 2)}kWh (vs {round(self.get_supplied_pv(), 1)}kWh), "
              f"Diff = {round(self.get_supplied_pv() - self.get_calc_pv() / 1000, 2)}kWh   "
              f"%age {round(self.get_supplied_pv() / (self.get_calc_pv() / 1000), 2)}")
