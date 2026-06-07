from enum import Enum
from datetime import datetime

from analysis.pricedata import PriceData
from analysis.virtualbattery import VirtualBattery


class QueryType(Enum):
    PEAK = 1
    OFFPEAK = 2
    BOTH = 3


class EnergySummary:
    def __init__(self, priceData: PriceData, battery: VirtualBattery, currentCumulativeSavings=0, currentAltInvestmentValue=0, remainderInput=0):

        self.battery = battery

        self.days = 0
        self.totalCalcExport = 0
        self.totalCalcImport = 0
        self.totalCalcPV = 0
        self.totalCalcLoad = 0
        self.totalCalcLoadOffpeak = 0
        self.totalCalcLoadPeak = 0

        self.totalSuppliedExport = 0
        self.totalSuppliedImport = 0
        self.totalSuppliedLoad = 0
        self.totalSuppliedPV = 0

        self.totalCalcImportOffPeak = 0
        self.totalCalcExportOffPeak = 0
        self.totalCalcImportPeak = 0
        self.totalCalcExportPeak = 0

        self.offPeakExcess = 0
        self.offPeakExcessSavings = 0

        self.totalSavedCalc = 0
        self.totalSavedSupplied = 0
        self.totalSavedAmountCalc = 0
        self.totalSavedAmountSupplied = 0
        self.totalExportAmountCalc = 0
        self.totalExportAmountSupplied = 0
        self.totalSavingsCalc = 0
        self.totalSavingsSupplied = 0
        self.TotalCost = 0
        self.TotalCostWithoutSolar = 0
        self.CompareCost = 0
        self.CompareCostWithoutSolar = 0
        self.remainderInput = remainderInput

        self.totalSavedInPeriod = remainderInput
        self.cumulativeSavings = currentCumulativeSavings
        self.altInvestValue = currentAltInvestmentValue
        self.addedToCumulative = 0

        time_difference = datetime.strptime(priceData.currentOffPeakStop, "%H:%M") - datetime.strptime(priceData.currentOffPeakStart, "%H:%M")
        priceData.offPeakAverage = 0.96 * ( time_difference.total_seconds() / 3600 ) / 7

        self.priceData = priceData

    def getRemainder(self):
        self.recalculate()
        self.totalSavedInPeriod = self.totalSavingsCalc + self.remainderInput
        toAdd = self.totalSavedInPeriod - self.addedToCumulative
        self.addedToCumulative += toAdd
        return toAdd

    def newMonth(self):
        remainder = self.getRemainder()
        interest = self.cumulativeSavings * (self.priceData.InterestRate/1200)
        toAdd = remainder + interest
        self.cumulativeSavings += toAdd

        interest = self.altInvestValue * (self.priceData.InterestRate/1200)
        self.altInvestValue += interest

    def addData(self, energyday):
        self.days += 1
        self.totalCalcExport += energyday.getCalcExport(QueryType.BOTH)
        self.totalCalcImport += energyday.getCalcImport()
        self.totalCalcPV += energyday.getCalcPV()
        self.totalCalcLoad += energyday.getCalcLoad()
        self.totalCalcLoadOffpeak += energyday.getCalcLoadOffPeak()
        self.totalCalcLoadPeak += energyday.getCalcLoadPeak()

        self.totalSuppliedExport += energyday.getSuppliedExport()
        self.totalSuppliedImport += energyday.getSuppliedImport()
        self.totalSuppliedLoad += energyday.getSuppliedLoad()
        self.totalSuppliedPV += energyday.getSuppliedPV()

        self.totalCalcImportOffPeak += energyday.getCalcImport(QueryType.OFFPEAK)
        self.totalCalcExportOffPeak += energyday.getCalcExport(QueryType.OFFPEAK)
        self.totalCalcImportPeak += energyday.getCalcImport(QueryType.PEAK)
        self.totalCalcExportPeak += energyday.getCalcExport(QueryType.PEAK)

        self.totalSavedCalc += energyday.getCalcLoad() - energyday.getCalcImport()
        self.totalSavedSupplied += energyday.getSuppliedLoad() - energyday.getSuppliedImport()

    def recalculate(self):
        self.totalSavedAmountCalc = ((self.totalCalcLoadPeak/1000 - self.totalCalcImportPeak/1000) * self.priceData.currentPeak) + ((self.totalCalcLoadOffpeak/1000 - self.totalCalcImportOffPeak/1000) * self.priceData.currentOffPeak)

        self.totalSavedAmountSupplied = self.totalSavedSupplied * self.priceData.currentPeak

        self.totalExportAmountCalc = (self.totalCalcExport/1000) * self.priceData.currentExport
        self.totalExportAmountSupplied = self.totalSuppliedExport * self.priceData.currentExport

        self.totalSavingsCalc = self.totalSavedAmountCalc + self.totalExportAmountCalc
        self.totalSavingsSupplied = self.totalSavedAmountSupplied + self.totalExportAmountSupplied

        self.offPeakExcess = (self.totalCalcImportOffPeak/1000) - (self.priceData.offPeakAverage*self.days)
        self.offPeakExcessSavings = (self.offPeakExcess) * (self.priceData.currentPeak - self.priceData.currentOffPeak)

        standingCharge = self.days * self.priceData.standingCharge
        ComparestandingCharge = self.days * self.priceData.ComparestandingCharge

        self.CompareCost = (self.totalCalcImport/1000 * self.priceData.CompareRate) + ComparestandingCharge
        self.CompareCostWithoutSolar = (self.totalCalcLoad/1000 * self.priceData.CompareRate) + ComparestandingCharge
        self.TotalCost = (self.totalCalcImportPeak/1000 * self.priceData.currentPeak) + (self.totalCalcImportOffPeak/1000 * self.priceData.currentOffPeak) + standingCharge

        self.TotalCostWithoutSolar = (self.totalCalcLoadPeak/1000 * self.priceData.currentPeak) + (self.totalCalcLoadOffpeak/1000 * self.priceData.currentOffPeak) + standingCharge


class EnergySummaryAggregator:
    def __init__(self):
        self.summaries = []

    def add_summary(self, summary: EnergySummary):
        self.summaries.append(summary)

    def get_grand_totals(self):
        totals = {
            "totalCalcExport": 0,
            "totalCalcImport": 0,
            "totalCalcPV": 0,
            "totalCalcLoad": 0,
            "totalSuppliedExport": 0,
            "totalSuppliedImport": 0,
            "totalSuppliedLoad": 0,
            "totalSuppliedPV": 0,
            "totalCalcImportOffPeak": 0,
            "totalCalcExportOffPeak": 0,
            "totalCalcImportPeak": 0,
            "totalCalcExportPeak": 0,
            "totalOffPeakExcess": 0,
            "totalOffPeakExcessSavings": 0,
            "days": 0,
            "totalSavedCalc": 0,
            "totalSavedSupplied": 0,
            "totalSavedAmountCalc": 0,
            "totalSavedAmountSupplied": 0,
            "totalExportAmountCalc": 0,
            "totalExportAmountSupplied": 0,
            "totalSavingsCalc": 0,
            "totalSavingsSupplied": 0,
            "TotalCost": 0,
            "TotalCostWithoutSolar": 0,
            "CompareCost": 0,
            "CompareCostWithoutSolar": 0,
            "batteryCostFromGrid": 0,
            "batteryNominalCost": 0,
            "batteryLostExportCost": 0,
            "batteryChargeAmount": 0,
            "batteryExportCost": 0,
            "batteryExported": 0,
            "batteryDaysRunOut": 0,
            "batteryExtraRequired": 0
        }

        for summary in self.summaries:
            summary.recalculate()
            totals["totalCalcExport"] += summary.totalCalcExport/1000
            totals["totalCalcImport"] += summary.totalCalcImport/1000
            totals["totalCalcPV"] += summary.totalCalcPV/1000
            totals["totalCalcLoad"] += summary.totalCalcLoad/1000
            totals["totalSuppliedExport"] += summary.totalSuppliedExport
            totals["totalSuppliedImport"] += summary.totalSuppliedImport
            totals["totalSuppliedLoad"] += summary.totalSuppliedLoad
            totals["totalSuppliedPV"] += summary.totalSuppliedPV
            totals["totalCalcImportOffPeak"] += summary.totalCalcImportOffPeak/1000
            totals["totalCalcExportOffPeak"] += summary.totalCalcExportOffPeak/1000
            totals["totalCalcImportPeak"] += summary.totalCalcImportPeak/1000
            totals["totalCalcExportPeak"] += summary.totalCalcExportPeak/1000
            totals["totalOffPeakExcess"] += summary.offPeakExcess
            totals["totalOffPeakExcessSavings"] += summary.offPeakExcessSavings
            totals["days"] += summary.days
            totals["totalSavedCalc"] += summary.totalSavedCalc/1000
            totals["totalSavedSupplied"] += summary.totalSavedSupplied
            totals["totalSavedAmountCalc"] += summary.totalSavedAmountCalc
            totals["totalSavedAmountSupplied"] += summary.totalSavedAmountSupplied
            totals["totalExportAmountCalc"] += summary.totalExportAmountCalc
            totals["totalExportAmountSupplied"] += summary.totalExportAmountSupplied
            totals["totalSavingsCalc"] += summary.totalSavingsCalc
            totals["totalSavingsSupplied"] += summary.totalSavingsSupplied
            totals["TotalCost"] += summary.TotalCost
            totals["TotalCostWithoutSolar"] += summary.TotalCostWithoutSolar
            totals["CompareCost"] += summary.CompareCost
            totals["CompareCostWithoutSolar"] += summary.CompareCostWithoutSolar
            summary.battery.getSavings()
            totals["batteryCostFromGrid"] += summary.battery.costFromGrid
            totals["batteryNominalCost"] += summary.battery.nominalCost
            totals["batteryLostExportCost"] += summary.battery.lostExportCost
            totals["batteryChargeAmount"] += summary.battery.getChargeAmountkWh()
            totals["batteryExportCost"] += summary.battery.exportCost
            totals["batteryExported"] += summary.battery.exported/1000
            totals["batteryDaysRunOut"] += summary.battery.daysRunOut
            totals["batteryExtraRequired"] += summary.battery.extraRequired/1000

        for key, value in totals.items():
            if isinstance(value, float):
                totals[key] = round(value, 2)

        return totals

    def update_last_summary(self, energyday):
        if not self.summaries:
            raise IndexError("No EnergySummary objects to update.")
        self.summaries[-1].addData(energyday)

    def get_last_summary(self):
        if not self.summaries:
            raise IndexError("No EnergySummary objects to update.")
        return self.summaries[-1]
