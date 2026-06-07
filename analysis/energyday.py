from datetime import datetime

from sunsynk.resource import Resource
from analysis.energysummary import QueryType
from analysis.virtualbattery import VirtualBattery
from analysis.energymonth import EnergyMonth


class IntervalSummary(Resource):
    """Accumulates peak/offpeak Wh totals from 5-minute interval records for one label (PV, Grid, Load)."""

    def __init__(self, data, month: EnergyMonth, battery: VirtualBattery,
                 offpeakstart="00:00", offpeakstop="00:07", date="", isLoad: bool = False):
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
                    if isLoad:
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
                    if isLoad:
                        temp = battery.utilise(value, time)
                        if temp > 0:
                            battery_ran_out = True
                else:
                    extra_load = min(value * -1, 2.3)
                    export = (value * -1) - extra_load
                    self.peakexport = self.peakexport + export
                    self.peak = self.peak - extra_load
                    if isLoad:
                        battery.PVCharge(export, time)

        if battery_ran_out:
            battery.setRanOut()

        if self.offpeak + self.peak > 0:
            self.offpeakpercentage = self.offpeak / (self.offpeak + self.peak)


class EnergyDay(Resource):
    def __init__(self, data, date: str, month: EnergyMonth, battery: VirtualBattery,
                 offpeakstart: str, offpeakstop: str):
        self.data = data
        energy = self.data['infos']
        for item in energy:
            if item['label'] == "PV":
                self.PV = IntervalSummary(item, month, battery, offpeakstart, offpeakstop, date)
            elif item['label'] == "Grid":
                self.Grid = IntervalSummary(item, month, battery, offpeakstart, offpeakstop, date, isLoad=True)
            elif item['label'] == "Load":
                self.Load = IntervalSummary(item, month, battery, offpeakstart, offpeakstop, date)

        self.suppliedLoad = next((float(r['value']) for r in month.get_Load()['records'] if r['time'] == date), 0.0)
        self.suppliedImport = next((float(r['value']) for r in month.get_Import()['records'] if r['time'] == date), 0.0)
        self.suppliedPV = next((float(r['value']) for r in month.get_PV()['records'] if r['time'] == date), 0.0)
        self.suppliedExport = next((float(r['value']) for r in month.get_Export()['records'] if r['time'] == date), 0.0)

    def getCalcExport(self, qtype: QueryType = QueryType.BOTH):
        if qtype == QueryType.BOTH:
            return self.Grid.peakexport + self.Grid.offpeakexport
        elif qtype == QueryType.PEAK:
            return self.Grid.peakexport
        elif qtype == QueryType.OFFPEAK:
            return self.Grid.offpeakexport
        else:
            return 0

    def getCalcImport(self, qtype: QueryType = QueryType.BOTH):
        if qtype == QueryType.BOTH:
            return self.Grid.peak + self.Grid.offpeak
        elif qtype == QueryType.PEAK:
            return self.Grid.peak
        elif qtype == QueryType.OFFPEAK:
            return self.Grid.offpeak
        else:
            return 0

    def getCalcExportPeak(self):
        return self.Grid.peakexport

    def getCalcImportPeak(self):
        return self.Grid.peak

    def getCalcExportOffPeak(self):
        return self.Grid.offpeakexport

    def getCalcImportOffPeak(self):
        return self.Grid.offpeak

    def getCalcPV(self):
        return self.PV.peak + self.PV.offpeak

    def getCalcPVPeak(self):
        return self.PV.peak

    def getCalcPVOffPeak(self):
        return self.PV.offpeak

    def getCalcLoad(self):
        return self.Load.peak + self.Load.offpeak

    def getCalcLoadPeak(self):
        return self.Load.peak

    def getCalcLoadOffPeak(self):
        return self.Load.offpeak

    def getSuppliedLoad(self):
        return self.suppliedLoad

    def getSuppliedExport(self):
        return self.suppliedExport

    def getSuppliedImport(self):
        return self.suppliedImport

    def getSuppliedPV(self):
        return self.suppliedPV

    def print(self):
        if self.getCalcExport(QueryType.BOTH) > 0:
            print(f"Export: {round(self.getCalcExport() / 1000, 2)}kWh (vs {round(self.getSuppliedExport(), 1)}kWh), "
                  f"Diff = {round(self.getSuppliedExport() - self.getCalcExport() / 1000, 2)}kWh  "
                  f"%age {round(self.getSuppliedExport() / (self.getCalcExport() / 1000), 2)}")
        print(f"Import {round(self.getCalcImport() / 1000, 2)}kWh (vs {round(self.getSuppliedImport(), 1)}kWh), "
              f"Diff = {round(self.getSuppliedImport() - self.getCalcImport() / 1000, 2)}kWh")
        print(f"PV {round(self.getCalcPV() / 1000, 2)}kWh (vs {round(self.getSuppliedPV(), 1)}kWh), "
              f"Diff = {round(self.getSuppliedPV() - self.getCalcPV() / 1000, 2)}kWh   "
              f"%age {round(self.getSuppliedPV() / (self.getCalcPV() / 1000), 2)}")
