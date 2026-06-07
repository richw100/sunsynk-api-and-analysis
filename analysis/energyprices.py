import re
from datetime import datetime

from analysis.pricedata import PriceData
from analysis.virtualbattery import VirtualBattery
from analysis.energysummary import EnergySummary, EnergySummaryAggregator


class EnergyPrices:
    def __init__(self, prices, battery: VirtualBattery, originalPrice: float = None):
        self.prices = prices
        self.aggregator = EnergySummaryAggregator()
        self.priceData = PriceData()
        self.originalPrice = originalPrice if originalPrice is not None else self.priceData.originalPrice
        self.origbattery = battery
        self.battery = self._make_battery(self.priceData)
        self.aggregator.add_summary(EnergySummary(self.priceData, self.battery, 0, self.originalPrice))
        self.grandTotals = self.aggregator.get_grand_totals()
        self.changed = 1

    def _make_battery(self, priceData: PriceData) -> VirtualBattery:
        return VirtualBattery(
            priceData,
            self.origbattery.batterySize,
            self.origbattery.PVEnabled,
            self.origbattery.startCharging,
            self.origbattery.stopCharging,
            exportWindowStart=self.origbattery.exportWindowStart,
            exportWindowStop=self.origbattery.exportWindowStop,
            dischargeEfficiency=self.origbattery.dischargeEfficiency,
            pvChargeEfficiency=self.origbattery.pvChargeEfficiency,
        )

    def checkDate(self, dateIn: str):
        if re.match("[0-9]{4}-[0-9]{2}-01$", dateIn):
            lastSummary = self.aggregator.get_last_summary()
            lastSummary.newMonth()

        checkDate = datetime.strptime(dateIn, "%Y-%m-%d")
        if checkDate < self.priceData.currentStart or checkDate > self.priceData.currentStop:
            for item in self.prices['data']['prices']:
                newStart = datetime.strptime(item['datefrom'], "%Y-%m-%d")
                newStop = datetime.strptime(item['dateto'], "%Y-%m-%d")
                if checkDate >= newStart and checkDate <= newStop:
                    self.priceData = PriceData()
                    self.priceData.currentOffPeak = float(item['offpeakRate'])
                    self.priceData.currentPeak = float(item['peakRate'])
                    self.priceData.currentExport = float(item['exportRate'])
                    self.priceData.currentOffPeakStart = item['offpeakStart']
                    self.priceData.currentOffPeakStop = item['offpeakStop']
                    self.priceData.standingCharge = float(item['standingCharge'])
                    self.priceData.ComparestandingCharge = float(item['CompareStandingCharge'])
                    self.priceData.CompareRate = float(item['CompareRate'])
                    if 'InterestRate' in item:
                        self.priceData.InterestRate = float(item['InterestRate'])
                    self.priceData.currentStart = newStart
                    self.priceData.currentStop = newStop
                    self.battery = self._make_battery(self.priceData)

                    lastSummary = self.aggregator.get_last_summary()
                    remainder = lastSummary.getRemainder()
                    self.aggregator.add_summary(EnergySummary(self.priceData, self.battery, lastSummary.cumulativeSavings, lastSummary.altInvestValue, remainder))
                    break

    def addData(self, energyday):
        self.changed = 1
        self.aggregator.update_last_summary(energyday)

    def get_grand_totals(self):
        if self.changed == 1:
            self.changed = 0
            self.grandTotals = self.aggregator.get_grand_totals()
        return self.grandTotals

    def print_costs(self):
        totals = self.get_grand_totals()
        print("")
        print("ENERGY COSTS")
        print(f"Total Cost of Energy:  £{totals["TotalCost"]}. With SEG: £{round(totals["TotalCost"] - totals["totalExportAmountCalc"],2)}")
        print(f"SEG income: £{totals["totalExportAmountCalc"]}")
        print(f"Total Cost of Energy without Solar:  £{totals["TotalCostWithoutSolar"]}")
        print(f"Compare Rate Cost of Energy:  £{totals["CompareCost"]}. ")
        print(f"Compare Rate Cost of Energy without Solar:  £{totals["CompareCostWithoutSolar"]}")

        TotalCalculatedSavings = round(totals["TotalCostWithoutSolar"]+totals["totalExportAmountCalc"]-totals["TotalCost"],2)
        TotalCalculatedSavingsExcExport = round(totals["TotalCostWithoutSolar"]-totals["TotalCost"],2)
        print("")
        print(f"Total Savings on bill (excluding export): Calculated : £{TotalCalculatedSavingsExcExport}. With SEG: £{TotalCalculatedSavings}")

        TotalCalculatedSavings = round(totals["CompareCostWithoutSolar"]+totals["totalExportAmountCalc"]-totals["TotalCost"],2)
        TotalCalculatedSavingsExcExport = round(totals["CompareCostWithoutSolar"]-totals["TotalCost"],2)
        print(f"Total Savings compared to alternative provider (excluding export): Calculated : £{TotalCalculatedSavingsExcExport}. With SEG: £{TotalCalculatedSavings}")

    def print_savings(self):
        totals = self.get_grand_totals()

        TotalCalculatedSavings = round(totals["totalSavingsCalc"]+totals["totalOffPeakExcessSavings"],2)
        TotalSuppliedSavings = round(totals["totalSavingsSupplied"]+totals["totalOffPeakExcessSavings"],2)
        TotalCalculatedSavingsExcExport = round(TotalCalculatedSavings - totals["totalExportAmountCalc"],2)
        TotalSuppliedSavingsExcExport = round(TotalSuppliedSavings - totals["totalExportAmountSupplied"],2)
        print("")
        print(f"Total Savings on bill (excluding export): Calculated : £{TotalCalculatedSavingsExcExport}. Supplied: £{TotalSuppliedSavingsExcExport}")
        print("")
        CompareSavings = round(totals["CompareCost"] - totals["TotalCost"],2)
        print(f"Savings compared to Compare rate: £{CompareSavings}")
        TotalSavingsfromSolarWithSEG = round(CompareSavings + totals["totalExportAmountCalc"],2)
        TotalSavingsfromSolarWithSEGPercent = round(TotalSavingsfromSolarWithSEG*100/self.originalPrice,2)

        print(f"Savings compared to Compare rate Solar: £{CompareSavings}. With SEG: £{TotalSavingsfromSolarWithSEG} ({TotalSavingsfromSolarWithSEGPercent}%)")

        CompareSavingsNoSolar = round(totals["CompareCostWithoutSolar"] - totals["TotalCost"],2)
        TotalSavingsNoSolarWithSEG = round(CompareSavingsNoSolar + totals["totalExportAmountCalc"],2)
        TotalSavingsNoSolarWithSEGPercent = round(TotalSavingsNoSolarWithSEG*100/self.originalPrice,2)
        print(f"Savings compared to Compare rate without Solar: £{CompareSavingsNoSolar}. With SEG: £{TotalSavingsNoSolarWithSEG} ({TotalSavingsNoSolarWithSEGPercent}%)")

    def print_return_on_investment(self):
        totals = self.get_grand_totals()
        print(f"\r\nRETURN ON INVESTMENT")

        calcPercentageReturn = round(totals["totalSavingsCalc"]*100/self.originalPrice,2)
        suppliedPercentageReturn = round(totals["totalSavingsSupplied"]*100/self.originalPrice,2)

        print(f"Calculated Return: £{totals["totalSavingsCalc"]} ({calcPercentageReturn}%). Supplied Return: £{totals["totalSavingsSupplied"]} ({suppliedPercentageReturn}%)")

        TotalCalculatedSavings = round(totals["totalSavingsCalc"]+totals["totalOffPeakExcessSavings"],2)
        TotalSuppliedSavings = round(totals["totalSavingsSupplied"]+totals["totalOffPeakExcessSavings"],2)
        print("")
        print(f"Total Savings inc offpeak shift savings. Calculated : £{TotalCalculatedSavings}. Supplied: £{TotalSuppliedSavings}")

        lastSummary = self.aggregator.get_last_summary()
        finalRemainder = lastSummary.getRemainder()
        print("")
        percentage = round(100*(lastSummary.cumulativeSavings + finalRemainder)/lastSummary.altInvestValue,2)
        print(f"Return on investment with cumulative interest = £{round(lastSummary.cumulativeSavings + finalRemainder,2)}/£{round(lastSummary.altInvestValue, 2)} = {percentage}%")

    def print_energy_summary(self):
        totals = self.get_grand_totals()
        print("")
        print(f"Total Days: {totals["days"]}")
        print(f"Export: {totals["totalCalcExport"]}kWh (vs {totals["totalSuppliedExport"]}kWh). Peak: {totals["totalCalcExportPeak"]}kWh. OffPeak: {totals["totalCalcExportOffPeak"]}kWh")
        print(f"Import {totals["totalCalcImport"]}kWh (vs {totals["totalSuppliedImport"]}kWh). Peak: {totals["totalCalcImportPeak"]}kWh. OffPeak: {totals["totalCalcImportOffPeak"]}kWh")
        print(f"PV: {totals["totalCalcPV"]}kWh (vs {totals["totalSuppliedPV"]}kWh)")
        print(f"Load: {totals["totalCalcLoad"]}kWh (vs {totals["totalSuppliedLoad"]}kWh)")

    def print_averages(self):
        totals = self.get_grand_totals()
        days = totals["days"]
        if days > 0:
            print("")
            print(f"Export Average Per Day: {round(totals["totalCalcExport"]/days, 2)}kWh (vs {round(totals["totalSuppliedExport"]/days, 2)}kWh). Peak: {round(totals["totalCalcExportPeak"]/(days), 2)}kWh. OffPeak: {round(totals["totalCalcExportOffPeak"]/days, 2)}kWh")
            print(f"Import Average Per Day {round(totals["totalCalcImport"]/days, 2)}kWh (vs {round(totals["totalSuppliedImport"]/days, 2)}kWh). Peak: {round(totals["totalCalcImportPeak"]/days, 2)}kWh. OffPeak: {round(totals["totalCalcImportOffPeak"]/days, 2)}kWh")

    def print_totals(self):
        totals = self.get_grand_totals()
        print("")
        print(f"Offpeak Total: {totals["totalCalcImportOffPeak"]}kWh. OffPeak Expected Excess: {totals["totalOffPeakExcess"]}kWh. Savings: £{totals["totalOffPeakExcessSavings"]}")
        print("")
        print(f"Calculated savings. Saved from grid = {totals["totalSavedCalc"]}kWh = £{totals["totalSavedAmountCalc"]}, Exported: {totals["totalCalcExport"]}kWh = £{totals["totalExportAmountCalc"]}")
        print(f"Supplied savings. Saved from grid = {totals["totalSavedSupplied"]}kWh = £{totals["totalSavedAmountSupplied"]}, Exported: {totals["totalSuppliedExport"]}kWh = £{totals["totalExportAmountSupplied"]}")

    def print_battery(self):
        totals = self.get_grand_totals()
        days = totals["days"]
        if days > 0:
            print("")
            print(f"\r\nBATTERY")
            print(f"Potential battery usage {totals["batteryChargeAmount"]}kWh (vs {totals["totalCalcImportPeak"]}kWh actually imported). (Average: {round(totals["totalCalcImportPeak"]/days, 2)}kWh/day)")
            print(f"Cost from grid: £{totals["batteryCostFromGrid"]} vs Peak Price cost: £{totals["batteryNominalCost"]}. (Missed Export payments: £{totals["batteryLostExportCost"]}")
            print(f"Re-exported: £{totals["batteryExportCost"]} ({totals["batteryExported"]}kWh")
            print(f"Days Ran Out of Battery: {totals["batteryDaysRunOut"]} ({totals["batteryExtraRequired"]}kWh)")

            savings = totals["batteryNominalCost"] + totals["batteryExportCost"] - totals["batteryCostFromGrid"] - totals["batteryLostExportCost"]
            print(f"Total Potential Saving: £{savings}")
            print(f"Potential savings - per day: £{round(savings/days,2)}, Average per year: £{round(365*savings/days,2)}")
