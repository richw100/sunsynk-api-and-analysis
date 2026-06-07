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
            maxOutputW=self.origbattery.maxOutputW,
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

    def _derive(self):
        """Compute all derived financial figures from grand totals."""
        t = self.get_grand_totals()
        seg = t['totalExportAmountCalc']
        op_excess_savings = t['totalOffPeakExcessSavings']

        def pct(amount):
            return round(amount * 100 / self.originalPrice, 2)

        # How much solar saved on own bill vs own tariff without solar
        bill_savings            = round(t['TotalCostWithoutSolar'] - t['TotalCost'], 2)
        bill_savings_inc_seg    = round(bill_savings + seg, 2)

        # How much better own tariff is vs compare tariff (same solar usage)
        compare_savings             = round(t['CompareCost'] - t['TotalCost'], 2)
        compare_savings_inc_seg     = round(compare_savings + seg, 2)

        # Full value of solar vs compare tariff without any solar
        solar_vs_compare            = round(t['CompareCostWithoutSolar'] - t['TotalCost'], 2)
        solar_vs_compare_inc_seg    = round(solar_vs_compare + seg, 2)

        # Calculated and supplied solar savings including offpeak shift benefit
        calc_savings                = round(t['totalSavingsCalc'] + op_excess_savings, 2)
        calc_savings_exc_seg        = round(calc_savings - seg, 2)
        supplied_savings            = round(t['totalSavingsSupplied'] + op_excess_savings, 2)
        supplied_savings_exc_seg    = round(supplied_savings - t['totalExportAmountSupplied'], 2)

        # Battery simulation net saving
        battery_savings = round(
            t['batteryNominalCost'] + t['batteryExportCost']
            - t['batteryCostFromGrid'] - t['batteryLostExportCost'], 2
        )

        return dict(
            t=t,
            seg=seg,
            bill_savings=bill_savings,
            bill_savings_inc_seg=bill_savings_inc_seg,
            compare_savings=compare_savings,
            compare_savings_inc_seg=compare_savings_inc_seg,
            compare_savings_pct=pct(compare_savings_inc_seg),
            solar_vs_compare=solar_vs_compare,
            solar_vs_compare_inc_seg=solar_vs_compare_inc_seg,
            solar_vs_compare_pct=pct(solar_vs_compare_inc_seg),
            calc_savings=calc_savings,
            calc_savings_exc_seg=calc_savings_exc_seg,
            supplied_savings=supplied_savings,
            supplied_savings_exc_seg=supplied_savings_exc_seg,
            calc_roi_pct=pct(t['totalSavingsCalc']),
            supplied_roi_pct=pct(t['totalSavingsSupplied']),
            battery_savings=battery_savings,
        )

    def print_costs(self):
        d = self._derive()
        t = d['t']
        print("")
        print("ENERGY COSTS")
        print(f"Total Cost of Energy: £{t['TotalCost']}. With SEG: £{round(t['TotalCost'] - d['seg'], 2)}")
        print(f"SEG income: £{d['seg']}")
        print(f"Total Cost of Energy without Solar: £{t['TotalCostWithoutSolar']}")
        print(f"Compare Rate Cost of Energy: £{t['CompareCost']}")
        print(f"Compare Rate Cost of Energy without Solar: £{t['CompareCostWithoutSolar']}")
        print("")
        print(f"Savings on bill (excl. export): £{d['bill_savings']}. With SEG: £{d['bill_savings_inc_seg']}")
        print(f"Savings vs compare provider (excl. export): £{d['solar_vs_compare']}. With SEG: £{d['solar_vs_compare_inc_seg']}")

    def print_savings(self):
        d = self._derive()
        t = d['t']
        print("")
        print(f"Solar savings excl. export: Calculated £{d['calc_savings_exc_seg']}. Supplied £{d['supplied_savings_exc_seg']}")
        print("")
        print(f"Savings compared to compare rate: £{d['compare_savings']}. With SEG: £{d['compare_savings_inc_seg']} ({d['compare_savings_pct']}%)")
        print(f"Full solar value vs compare rate: £{d['solar_vs_compare']}. With SEG: £{d['solar_vs_compare_inc_seg']} ({d['solar_vs_compare_pct']}%)")

    def print_return_on_investment(self):
        d = self._derive()
        t = d['t']
        print(f"\r\nRETURN ON INVESTMENT")
        print(f"Calculated: £{t['totalSavingsCalc']} ({d['calc_roi_pct']}%). Supplied: £{t['totalSavingsSupplied']} ({d['supplied_roi_pct']}%)")
        print("")
        print(f"Total savings inc offpeak shift: Calculated £{d['calc_savings']}. Supplied £{d['supplied_savings']}")
        print("")
        lastSummary = self.aggregator.get_last_summary()
        finalRemainder = lastSummary.getRemainder()
        cumulative = round(lastSummary.cumulativeSavings + finalRemainder, 2)
        percentage = round(100 * cumulative / lastSummary.altInvestValue, 2)
        print(f"Return on investment with cumulative interest = £{cumulative}/£{round(lastSummary.altInvestValue, 2)} = {percentage}%")

    def print_energy_summary(self):
        t = self.get_grand_totals()
        print("")
        print(f"Total Days: {t['days']}")
        print(f"Export: {t['totalCalcExport']}kWh (vs {t['totalSuppliedExport']}kWh). Peak: {t['totalCalcExportPeak']}kWh. OffPeak: {t['totalCalcExportOffPeak']}kWh")
        print(f"Import: {t['totalCalcImport']}kWh (vs {t['totalSuppliedImport']}kWh). Peak: {t['totalCalcImportPeak']}kWh. OffPeak: {t['totalCalcImportOffPeak']}kWh")
        print(f"PV: {t['totalCalcPV']}kWh (vs {t['totalSuppliedPV']}kWh)")
        print(f"Load: {t['totalCalcLoad']}kWh (vs {t['totalSuppliedLoad']}kWh)")

    def print_averages(self):
        t = self.get_grand_totals()
        days = t['days']
        if days > 0:
            print("")
            print(f"Export avg/day: {round(t['totalCalcExport']/days, 2)}kWh (vs {round(t['totalSuppliedExport']/days, 2)}kWh). Peak: {round(t['totalCalcExportPeak']/days, 2)}kWh. OffPeak: {round(t['totalCalcExportOffPeak']/days, 2)}kWh")
            print(f"Import avg/day: {round(t['totalCalcImport']/days, 2)}kWh (vs {round(t['totalSuppliedImport']/days, 2)}kWh). Peak: {round(t['totalCalcImportPeak']/days, 2)}kWh. OffPeak: {round(t['totalCalcImportOffPeak']/days, 2)}kWh")

    def print_totals(self):
        t = self.get_grand_totals()
        print("")
        print(f"Offpeak Total: {t['totalCalcImportOffPeak']}kWh. Expected Excess: {t['totalOffPeakExcess']}kWh. Savings: £{t['totalOffPeakExcessSavings']}")
        print("")
        print(f"Calculated savings. Saved from grid: {t['totalSavedCalc']}kWh = £{t['totalSavedAmountCalc']}. Exported: {t['totalCalcExport']}kWh = £{t['totalExportAmountCalc']}")
        print(f"Supplied savings.   Saved from grid: {t['totalSavedSupplied']}kWh = £{t['totalSavedAmountSupplied']}. Exported: {t['totalSuppliedExport']}kWh = £{t['totalExportAmountSupplied']}")

    def print_battery(self):
        d = self._derive()
        t = d['t']
        days = t['days']
        if days > 0:
            print(f"\r\nBATTERY")
            print(f"Potential usage: {t['batteryChargeAmount']}kWh (vs {t['totalCalcImportPeak']}kWh imported at peak). Average: {round(t['totalCalcImportPeak']/days, 2)}kWh/day")
            print(f"Cost from grid: £{t['batteryCostFromGrid']}. Peak price equivalent: £{t['batteryNominalCost']}. Missed export: £{t['batteryLostExportCost']}")
            print(f"Re-exported: {t['batteryExported']}kWh = £{t['batteryExportCost']}")
            print(f"Days ran out of battery: {t['batteryDaysRunOut']} ({t['batteryExtraRequired']}kWh short)")
            print(f"Total potential saving: £{d['battery_savings']}")
            print(f"Potential savings: £{round(d['battery_savings']/days, 2)}/day. £{round(365 * d['battery_savings']/days, 2)}/year")
