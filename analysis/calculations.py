import asyncio
import os
import json
import sys
import re

from enum import Enum

from sunsynk.resource import Resource
from datetime import datetime

class QueryType(Enum):
    PEAK = 1
    OFFPEAK = 2
    BOTH = 3
    
exportStart = datetime.strptime("17:00", "%H:%M")
exportStop = datetime.strptime("19:00", "%H:%M")


class PriceData:
    def __init__(self):
        self.currentOffPeak = 0.067
        self.currentPeak = 28.26
        self.currentOffPeakStart = "00:00"
        self.currentOffPeakStop = "00:07"
        self.currentStart = datetime.strptime("2025-07-10", "%Y-%m-%d")
        self.currentStop = datetime.strptime("2026-09-10", "%Y-%m-%d")
        self.offPeakAverage = 0.96
        self.currentExport = 0.165
        self.standingCharge = 0.6
        self.ComparestandingCharge = 0.53
        self.CompareRate = 0.25
        self.originalPrice = 6210 - 3.53 # £3.53 - adjusting for solar used to charge car June '25 - May '26 (32 kWh * .2767-.0165)
        self.InterestRate = 3.7

class VirtualBattery:
    def __init__(self, priceData: PriceData, batterySize = 5000, UsePV = 0, startCharging = 1000, stopCharging = 2000 ):
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
        
        #print(f"Battery Size: {self.batterySize/1000}kWh, Is PV Enabled? {self.PVEnabled}. Start PV Charging: {self.startCharging}wH Stop PV Charging: {self.stopCharging}wH ")
        
    def recharge(self):
        self.chargeAmount += self.batterySize - self.batteryStatus
        self.batteryStatus = self.batterySize
        
    def discharge(self, time: datetime):
        runDownAmount = 1000 #self.startCharging
        if self.export == 1:
            if time > exportStart and time < exportStop:
                if self.batteryStatus >(runDownAmount):
                    fiveminexportamount = min(217, ((self.batterySize - runDownAmount))/(6*60/5)) # 217 = max 200w per 5 minute (2400 per hour) / 0.92 efficiency
                    self.batteryStatus -= fiveminexportamount
                    self.exported += (fiveminexportamount * 0.92)
                    #print(f"Exporting {fiveminexportamount}W at {time}")
    
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
            #print(f"RANOUT: {temp/12}kWh - {time}")
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
            
            postEfficiencyValue = value * 0.96 # PV charging efficiency = 96%
            
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
        self.costFromGrid = round((self.chargeAmount/1000)*self.priceData.currentOffPeak,2)
        self.nominalCost = round((self.drawn*self.priceData.currentPeak)/1000,2)
        self.lostExportCost = round((self.PVInput*self.priceData.currentExport)/1000,2)
        self.exportCost = round((self.exported/1000)*self.priceData.currentExport,2)
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
            #Actual cost 6210, with battery quote: 8905
            
            print(f"Potential savings - per day: £{round(self.savings/days,2)}, Average per year: £{round(365*self.savings/days,2)}") 
            


        
class EnergySummary:
    def __init__(self, priceData: PriceData, battery: VirtualBattery, currentCumulativeSavings = 0, currentAltInvestmentValue = 0, remainderInput = 0):
        
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
        
        
        #print(f"Initial: {currentCumulativeSavings}. Initial2: {currentAltInvestmentValue}")
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
        
        #print(f"ToAdd: {toAdd}, Remaining Remainder: {self.totalSavedInPeriod} - {self.addedToCumulative} = {self.totalSavedInPeriod - self.addedToCumulative}")
        return toAdd
        
    def newMonth(self):
        
        remainder = self.getRemainder()
        interest = self.cumulativeSavings * (self.priceData.InterestRate/1200)
        toAdd = remainder + interest
        
        #print(f"CumulativeInterest: {interest}")
        self.cumulativeSavings += toAdd
        
        interest = self.altInvestValue * (self.priceData.InterestRate/1200)
        #print(f"Alternative Investment: {self.altInvestValue}. Added Interest: {interest}")
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
        
        #print(f"£{self.TotalCost} = ({self.totalCalcImportPeak}/1000 * {self.priceData.currentPeak}) + ({self.totalCalcImportOffPeak}/1000 * {self.priceData.currentOffPeak}) + {standingCharge}")
        
        CompareSavings = (self.CompareCost - self.TotalCost)
        self.TotalCostWithoutSolar = (self.totalCalcLoadPeak/1000 * self.priceData.currentPeak) + (self.totalCalcLoadOffpeak/1000 * self.priceData.currentOffPeak) + standingCharge
        CompareSavings = (self.CompareCost - self.TotalCost)
        
        #print(f"Total Cost: ({self.totalCalcImportPeak/1000}kWh * £{self.priceData.currentPeak}) + ({self.totalCalcImportOffPeak/1000}kWh * £{self.priceData.currentOffPeak}) + £{standingCharge} = £{self.TotalCost}")
        #print(f"Total Cost without Solar: ({self.totalCalcLoadPeak/1000}kWh * £{self.priceData.currentPeak}) + ({self.totalCalcLoadOffpeak/1000}kWh * £{self.priceData.currentOffPeak}) + £{standingCharge} = £{self.TotalCostWithoutSolar}")
        #print(f"Compare Rate Cost: ({self.totalCalcImportPeak/1000}kWh * £{self.priceData.CompareRate}) + ({self.totalCalcImportOffPeak/1000}kWh * £{self.priceData.CompareRate}) + £{ComparestandingCharge} = £{self.CompareCost}")
        #print(f"Compare Cost without Solar: ({self.totalCalcLoad/1000}kWh * £{self.priceData.CompareRate}) + £{ComparestandingCharge} = £{self.CompareCostWithoutSolar}")
        
         

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
            #print(f"Period Savings amount: {summary.totalSavingsCalc}")
            totals["totalSavingsSupplied"] += summary.totalSavingsSupplied
            totals["TotalCost"] += summary.TotalCost
            totals["TotalCostWithoutSolar"] += summary.TotalCostWithoutSolar
            totals["CompareCost"] += summary.CompareCost
            totals["CompareCostWithoutSolar"] += summary.CompareCostWithoutSolar
            summary.battery.getSavings()
            #summary.battery.print(summary.totalCalcExport/1000, summary.days)
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

        last_summary = self.summaries[-1]  
        last_summary.addData(energyday)
        
    def get_last_summary(self):
        if not self.summaries:
            raise IndexError("No EnergySummary objects to update.")

        return self.summaries[-1]  
        


class EnergyPrices:
    def __init__ (self, prices, battery: VirtualBattery):
        self.prices = prices
        self.aggregator = EnergySummaryAggregator()
        self.priceData = PriceData()
        self.origbattery = battery
        self.battery = VirtualBattery(self.priceData, self.origbattery.batterySize, self.origbattery.PVEnabled, self.origbattery.startCharging, self.origbattery.stopCharging)
        self.aggregator.add_summary(EnergySummary(self.priceData, self.battery, 0, self.priceData.originalPrice))
        self.grandTotals = self.aggregator.get_grand_totals()
        self.changed = 1
        
        
    def checkDate(self, dateIn: str):
        
        if re.match("[0-9]{4}-[0-9]{2}-01$", dateIn) :
            #print(f"New month: {dateIn}")
            lastSummary = self.aggregator.get_last_summary()
            lastSummary.newMonth()
        
        checkDate = datetime.strptime(dateIn, "%Y-%m-%d")
        if checkDate < self.priceData.currentStart or checkDate > self.priceData.currentStop: 
            #print(dateIn + " : Outside Date Range")
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
                    self.battery = VirtualBattery(self.priceData, self.origbattery.batterySize, self.origbattery.PVEnabled, self.origbattery.startCharging, self.origbattery.stopCharging)
                    
                    lastSummary = self.aggregator.get_last_summary()
                    remainder = lastSummary.getRemainder()
                    #print(f"New period. Cumulative: {lastSummary.cumulativeSavings}, remainder: {remainder}")
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
        
        TotalCalculatedSavings = round(totals["totalSavingsCalc"]+totals["totalOffPeakExcessSavings"],2) # Savings on usage at peak rate; plus export; plus savings
        TotalSuppliedSavings = round(totals["totalSavingsSupplied"]+totals["totalOffPeakExcessSavings"],2)
        TotalCalculatedSavingsExcExport = round(TotalCalculatedSavings - totals["totalExportAmountCalc"],2)
        TotalSuppliedSavingsExcExport = round(TotalSuppliedSavings - totals["totalExportAmountSupplied"],2)
        print("")
        print(f"Total Savings on bill (excluding export): Calculated : £{TotalCalculatedSavingsExcExport}. Supplied: £{TotalSuppliedSavingsExcExport}")
        print("")
        CompareSavings = round(totals["CompareCost"] - totals["TotalCost"],2)
        print(f"Savings compared to Compare rate: £{CompareSavings}")
        TotalSavingsfromSolarWithSEG = round(CompareSavings + totals["totalExportAmountCalc"],2)
        TotalSavingsfromSolarWithSEGPercent = round(TotalSavingsfromSolarWithSEG*100/self.priceData.originalPrice,2)
        
        print(f"Savings compared to Compare rate Solar: £{CompareSavings}. With SEG: £{TotalSavingsfromSolarWithSEG} ({TotalSavingsfromSolarWithSEGPercent}%)") 
        
        CompareSavingsNoSolar = round(totals["CompareCostWithoutSolar"] - totals["TotalCost"],2)
        TotalSavingsNoSolarWithSEG = round(CompareSavingsNoSolar + totals["totalExportAmountCalc"],2)
        TotalSavingsNoSolarWithSEGPercent = round(TotalSavingsNoSolarWithSEG*100/self.priceData.originalPrice,2)
        print(f"Savings compared to Compare rate without Solar: £{CompareSavingsNoSolar}. With SEG: £{TotalSavingsNoSolarWithSEG} ({TotalSavingsNoSolarWithSEGPercent}%)")   
   
    def print_return_on_investment(self):
        totals = self.get_grand_totals()
        print(f"\r\nRETURN ON INVESTMENT")
            
        calcPercentageReturn = round(totals["totalSavingsCalc"]*100/self.priceData.originalPrice,2)
        suppliedPercentageReturn = round(totals["totalSavingsSupplied"]*100/self.priceData.originalPrice,2)
        
        print(f"Calculated Return: £{totals["totalSavingsCalc"]} ({calcPercentageReturn}%). Supplied Return: £{totals["totalSavingsSupplied"]} ({suppliedPercentageReturn}%)")
        
        TotalCalculatedSavings = round(totals["totalSavingsCalc"]+totals["totalOffPeakExcessSavings"],2) # Savings on usage at peak rate; plus export; plus savings
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
        
      
       
