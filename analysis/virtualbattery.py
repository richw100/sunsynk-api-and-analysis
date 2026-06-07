from datetime import datetime

from analysis.pricedata import PriceData

exportStart = datetime.strptime("17:00", "%H:%M")
exportStop = datetime.strptime("19:00", "%H:%M")


class VirtualBattery:
    def __init__(self, priceData: PriceData, batterySize=5000, UsePV=0, startCharging=1000, stopCharging=2000):
        self.batterySize = batterySize
        self.batteryStatus = batterySize
        self.chargeAmount = 0
        self.shouldPVCharge = 0
        self.PVInput = 0
        self.drawn = 0
        self.PVEnabled = UsePV
        self.startCharging = startCharging
        self.stopCharging = stopCharging
        self.priceData = priceData
        self.lostExportCost = 0
        self.export = 0
        self.exportCost = 0

        self.savings = 0
        self.costFromGrid = 0
        self.nominalCost = 0
        self.exported = 0
        self.extraRequired = 0
        self.daysRunOut = 0

    def recharge(self):
        self.chargeAmount += self.batterySize - self.batteryStatus
        self.batteryStatus = self.batterySize

    def discharge(self, time: datetime):
        runDownAmount = 1000
        if self.export == 1:
            if time > exportStart and time < exportStop:
                if self.batteryStatus > (runDownAmount):
                    fiveminexportamount = min(217, ((self.batterySize - runDownAmount))/(6*60/5))
                    self.batteryStatus -= fiveminexportamount
                    self.exported += (fiveminexportamount * 0.92)

    def setRanOut(self):
        self.daysRunOut += 1

    def utilise(self, value, time: datetime):
        #Discharge Efficiency = 92%
        self.discharge(time)

        maxvalue = min(217, value)  # 217 = max 200w per 5 minute (2400 per hour) / 0.92 efficiency

        if maxvalue > (self.batteryStatus / 0.92):
            self.drawn += (self.batteryStatus * 0.92)
            temp = (maxvalue - self.batteryStatus) / 0.92
            self.batteryStatus = 0
            self.extraRequired += temp
            return temp
        else:
            self.batteryStatus -= value
            self.drawn += value * .92
            return 0

    def PVCharge(self, value, time: datetime):
        self.discharge(time)

        if self.PVEnabled == 1:
            if self.batteryStatus < self.startCharging:
                self.shouldPVCharge = 1
            if self.batteryStatus >= self.stopCharging:
                self.shouldPVCharge = 0

        if self.shouldPVCharge == 1:
            postEfficiencyValue = value * 0.96  # PV charging efficiency = 96%

            if self.batteryStatus + postEfficiencyValue <= self.batterySize:
                self.batteryStatus += postEfficiencyValue
                self.PVInput += postEfficiencyValue

    def totalDrawnkWh(self):
        return round(self.drawn/1000, 2)

    def getChargeAmountkWh(self):
        return round(self.chargeAmount/1000, 2)

    def getPVChargekWh(self):
        return round(self.PVInput/1000, 2)

    def getSavings(self):
        self.costFromGrid = round((self.chargeAmount/1000)*self.priceData.currentOffPeak, 2)
        self.nominalCost = round((self.drawn*self.priceData.currentPeak)/1000, 2)
        self.lostExportCost = round((self.PVInput*self.priceData.currentExport)/1000, 2)
        self.exportCost = round((self.exported/1000)*self.priceData.currentExport, 2)
        self.savings = self.nominalCost + self.exportCost - self.costFromGrid - self.lostExportCost

    def print(self, totalCalcImportPeak, days):
        self.getSavings()
        if days > 0:
            print(f"\r\nBATTERY")
            print(f"Potential battery usage {self.getChargeAmountkWh()}kWh (vs {round(totalCalcImportPeak/1000, 1)}kWh actually imported). (Average: {round(self.getChargeAmountkWh()/days, 2)}kWh/day)")
            print(f"Total Drawn: {self.totalDrawnkWh()}kWh. Nominal Cost from Grid: £{self.nominalCost}")
            print(f"Total Cost from Grid: {self.getChargeAmountkWh()}kWh. Cost from Grid: £{self.costFromGrid}")
            print(f"Total Cost from PV: {self.getPVChargekWh()}kWh. Not exported: £{self.lostExportCost}")
            print(f"Potential Savings: £{round(self.nominalCost + self.exportCost - self.costFromGrid - self.lostExportCost,2)}")
            print(f"Re-exported: £{self.exportCost} ({self.exported/1000}kWh")
            print(f"Days Ran Out of Battery: {self.daysRunOut} ({self.extraRequired/1000}kWh)")
            print(f"Potential savings - per day: £{round(self.savings/days,2)}, Average per year: £{round(365*self.savings/days,2)}")
