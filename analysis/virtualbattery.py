from datetime import datetime

from analysis.pricedata import PriceData


class VirtualBattery:
    def __init__(self, priceData: PriceData, batterySize=5000, UsePV=0, startCharging=1000, stopCharging=2000,
                 exportWindowStart="17:00", exportWindowStop="19:00",
                 dischargeEfficiency=0.92, pvChargeEfficiency=0.96, maxOutputW=2400):
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

        self.exportWindowStart = exportWindowStart
        self.exportWindowStop = exportWindowStop
        self.exportStart = datetime.strptime(exportWindowStart, "%H:%M")
        self.exportStop = datetime.strptime(exportWindowStop, "%H:%M")
        self.dischargeEfficiency = dischargeEfficiency
        self.pvChargeEfficiency = pvChargeEfficiency
        self.maxOutputW = maxOutputW

    def recharge(self):
        self.chargeAmount += self.batterySize - self.batteryStatus
        self.batteryStatus = self.batterySize

    def discharge(self, time: datetime):
        runDownAmount = 1000
        if self.export == 1:
            if time > self.exportStart and time < self.exportStop:
                if self.batteryStatus > runDownAmount:
                    max5minWh = (self.maxOutputW / 12) / self.dischargeEfficiency
                    fiveminexportamount = min(max5minWh, (self.batterySize - runDownAmount) / (6*60/5))
                    self.batteryStatus -= fiveminexportamount
                    self.exported += fiveminexportamount * self.dischargeEfficiency

    def setRanOut(self):
        self.daysRunOut += 1

    def utilise(self, value, time: datetime):
        self.discharge(time)

        max5minWh = (self.maxOutputW / 12) / self.dischargeEfficiency
        maxvalue = min(max5minWh, value)

        if maxvalue > (self.batteryStatus / self.dischargeEfficiency):
            self.drawn += self.batteryStatus * self.dischargeEfficiency
            temp = (maxvalue - self.batteryStatus) / self.dischargeEfficiency
            self.batteryStatus = 0
            self.extraRequired += temp
            return temp
        else:
            self.batteryStatus -= value
            self.drawn += value * self.dischargeEfficiency
            return 0

    def PVCharge(self, value, time: datetime):
        self.discharge(time)

        if self.PVEnabled == 1:
            if self.batteryStatus < self.startCharging:
                self.shouldPVCharge = 1
            if self.batteryStatus >= self.stopCharging:
                self.shouldPVCharge = 0

        if self.shouldPVCharge == 1:
            postEfficiencyValue = value * self.pvChargeEfficiency

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

