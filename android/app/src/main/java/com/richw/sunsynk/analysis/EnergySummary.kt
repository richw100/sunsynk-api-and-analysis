package com.richw.sunsynk.analysis

import java.time.Duration
import java.time.LocalTime
import java.time.format.DateTimeFormatter

class EnergySummary(
    val priceData: PriceData,
    val battery: VirtualBattery,
    currentCumulativeSavings: Double = 0.0,
    currentAltInvestmentValue: Double = 0.0,
    remainderInput: Double = 0.0,
) {
    var days = 0
    var totalCalcExport = 0.0
    var totalCalcImport = 0.0
    var totalCalcPv = 0.0
    var totalCalcLoad = 0.0
    var totalCalcLoadOffPeak = 0.0
    var totalCalcLoadPeak = 0.0
    var totalSuppliedExport = 0.0
    var totalSuppliedImport = 0.0
    var totalSuppliedLoad = 0.0
    var totalSuppliedPv = 0.0
    var totalCalcImportOffPeak = 0.0
    var totalCalcExportOffPeak = 0.0
    var totalCalcImportPeak = 0.0
    var totalCalcExportPeak = 0.0
    var offPeakExcess = 0.0
    var offPeakExcessSavings = 0.0
    var totalSavedCalc = 0.0
    var totalSavedSupplied = 0.0
    var totalSavedAmountCalc = 0.0
    var totalSavedAmountSupplied = 0.0
    var totalExportAmountCalc = 0.0
    var totalExportAmountSupplied = 0.0
    var totalSavingsCalc = 0.0
    var totalSavingsSupplied = 0.0
    var totalCost = 0.0
    var totalCostWithoutSolar = 0.0
    var remainderInput = remainderInput
    var totalSavedInPeriod = remainderInput
    var cumulativeSavings = currentCumulativeSavings
    var altInvestValue = currentAltInvestmentValue
    var addedToCumulative = 0.0

    init {
        val fmt = DateTimeFormatter.ofPattern("H:mm")
        val start = LocalTime.parse(priceData.currentOffPeakStart, fmt)
        val stop = LocalTime.parse(priceData.currentOffPeakStop, fmt)
        val timeDiff = Duration.between(start, stop).let {
            if (it.isNegative) it.plusHours(24) else it
        }
        priceData.offPeakAverage = priceData.offPeakBaselineKwh * (timeDiff.toMinutes() / 60.0) / 7.0
    }

    fun getRemainder(): Double {
        recalculate()
        totalSavedInPeriod = totalSavingsCalc + remainderInput
        val toAdd = totalSavedInPeriod - addedToCumulative
        addedToCumulative += toAdd
        return toAdd
    }

    fun newMonth() {
        val remainder = getRemainder()
        val interest = cumulativeSavings * (priceData.interestRate / 1200)
        cumulativeSavings += remainder + interest
        val altInterest = altInvestValue * (priceData.interestRate / 1200)
        altInvestValue += altInterest
    }

    fun addData(day: EnergyDay) {
        days++
        totalCalcExport += day.getCalcExport()
        totalCalcImport += day.getCalcImport()
        totalCalcPv += day.getCalcPv()
        totalCalcLoad += day.getCalcLoad()
        totalCalcLoadOffPeak += day.getCalcLoadOffPeak()
        totalCalcLoadPeak += day.getCalcLoadPeak()
        totalSuppliedExport += day.suppliedExport
        totalSuppliedImport += day.suppliedImport
        totalSuppliedLoad += day.suppliedLoad
        totalSuppliedPv += day.suppliedPv
        totalCalcImportOffPeak += day.getCalcImportOffPeak()
        totalCalcExportOffPeak += day.getCalcExportOffPeak()
        totalCalcImportPeak += day.getCalcImportPeak()
        totalCalcExportPeak += day.getCalcExportPeak()
        totalSavedCalc += day.getCalcLoad() - day.getCalcImport()
        totalSavedSupplied += day.suppliedLoad - day.suppliedImport
    }

    fun recalculate() {
        totalSavedAmountCalc = (
            (totalCalcLoadPeak / 1000 - totalCalcImportPeak / 1000) * priceData.currentPeak
            + (totalCalcLoadOffPeak / 1000 - totalCalcImportOffPeak / 1000) * priceData.currentOffPeak
        )
        totalSavedAmountSupplied = totalSavedSupplied * priceData.currentPeak
        totalExportAmountCalc = (totalCalcExport / 1000) * priceData.currentExport
        totalExportAmountSupplied = totalSuppliedExport * priceData.currentExport
        totalSavingsCalc = totalSavedAmountCalc + totalExportAmountCalc
        totalSavingsSupplied = totalSavedAmountSupplied + totalExportAmountSupplied
        offPeakExcess = (totalCalcImportOffPeak / 1000) - (priceData.offPeakAverage * days)
        offPeakExcessSavings = offPeakExcess * (priceData.currentPeak - priceData.currentOffPeak)
        val standing = days * priceData.standingCharge
        totalCost = (
            totalCalcImportPeak / 1000 * priceData.currentPeak
            + totalCalcImportOffPeak / 1000 * priceData.currentOffPeak
            + standing
        )
        totalCostWithoutSolar = (
            totalCalcLoadPeak / 1000 * priceData.currentPeak
            + totalCalcLoadOffPeak / 1000 * priceData.currentOffPeak
            + standing
        )
    }
}

class EnergySummaryAggregator {
    val summaries = mutableListOf<EnergySummary>()

    fun addSummary(summary: EnergySummary) = summaries.add(summary)

    fun updateLastSummary(day: EnergyDay) {
        summaries.last().addData(day)
    }

    fun getLastSummary() = summaries.last()

    fun getGrandTotals(): Map<String, Double> {
        val t = mutableMapOf(
            "total_calc_export" to 0.0,
            "total_calc_import" to 0.0,
            "total_calc_pv" to 0.0,
            "total_calc_load" to 0.0,
            "total_supplied_export" to 0.0,
            "total_supplied_import" to 0.0,
            "total_supplied_load" to 0.0,
            "total_supplied_pv" to 0.0,
            "total_calc_import_off_peak" to 0.0,
            "total_calc_export_off_peak" to 0.0,
            "total_calc_import_peak" to 0.0,
            "total_calc_export_peak" to 0.0,
            "total_off_peak_excess" to 0.0,
            "total_off_peak_excess_savings" to 0.0,
            "days" to 0.0,
            "total_saved_calc" to 0.0,
            "total_saved_supplied" to 0.0,
            "total_saved_amount_calc" to 0.0,
            "total_saved_amount_supplied" to 0.0,
            "total_export_amount_calc" to 0.0,
            "total_export_amount_supplied" to 0.0,
            "total_savings_calc" to 0.0,
            "total_savings_supplied" to 0.0,
            "total_cost" to 0.0,
            "total_cost_without_solar" to 0.0,
            "battery_cost_from_grid" to 0.0,
            "battery_nominal_cost" to 0.0,
            "battery_lost_export_cost" to 0.0,
            "battery_charge_amount" to 0.0,
            "battery_export_cost" to 0.0,
            "battery_exported" to 0.0,
            "battery_days_run_out" to 0.0,
            "battery_extra_required" to 0.0,
        )

        for (s in summaries) {
            s.recalculate()
            t["total_calc_export"] = t["total_calc_export"]!! + s.totalCalcExport / 1000
            t["total_calc_import"] = t["total_calc_import"]!! + s.totalCalcImport / 1000
            t["total_calc_pv"] = t["total_calc_pv"]!! + s.totalCalcPv / 1000
            t["total_calc_load"] = t["total_calc_load"]!! + s.totalCalcLoad / 1000
            t["total_supplied_export"] = t["total_supplied_export"]!! + s.totalSuppliedExport
            t["total_supplied_import"] = t["total_supplied_import"]!! + s.totalSuppliedImport
            t["total_supplied_load"] = t["total_supplied_load"]!! + s.totalSuppliedLoad
            t["total_supplied_pv"] = t["total_supplied_pv"]!! + s.totalSuppliedPv
            t["total_calc_import_off_peak"] = t["total_calc_import_off_peak"]!! + s.totalCalcImportOffPeak / 1000
            t["total_calc_export_off_peak"] = t["total_calc_export_off_peak"]!! + s.totalCalcExportOffPeak / 1000
            t["total_calc_import_peak"] = t["total_calc_import_peak"]!! + s.totalCalcImportPeak / 1000
            t["total_calc_export_peak"] = t["total_calc_export_peak"]!! + s.totalCalcExportPeak / 1000
            t["total_off_peak_excess"] = t["total_off_peak_excess"]!! + s.offPeakExcess
            t["total_off_peak_excess_savings"] = t["total_off_peak_excess_savings"]!! + s.offPeakExcessSavings
            t["days"] = t["days"]!! + s.days
            t["total_saved_calc"] = t["total_saved_calc"]!! + s.totalSavedCalc / 1000
            t["total_saved_supplied"] = t["total_saved_supplied"]!! + s.totalSavedSupplied
            t["total_saved_amount_calc"] = t["total_saved_amount_calc"]!! + s.totalSavedAmountCalc
            t["total_saved_amount_supplied"] = t["total_saved_amount_supplied"]!! + s.totalSavedAmountSupplied
            t["total_export_amount_calc"] = t["total_export_amount_calc"]!! + s.totalExportAmountCalc
            t["total_export_amount_supplied"] = t["total_export_amount_supplied"]!! + s.totalExportAmountSupplied
            t["total_savings_calc"] = t["total_savings_calc"]!! + s.totalSavingsCalc
            t["total_savings_supplied"] = t["total_savings_supplied"]!! + s.totalSavingsSupplied
            t["total_cost"] = t["total_cost"]!! + s.totalCost
            t["total_cost_without_solar"] = t["total_cost_without_solar"]!! + s.totalCostWithoutSolar
            s.battery.getSavings()
            t["battery_cost_from_grid"] = t["battery_cost_from_grid"]!! + s.battery.costFromGrid
            t["battery_nominal_cost"] = t["battery_nominal_cost"]!! + s.battery.nominalCost
            t["battery_lost_export_cost"] = t["battery_lost_export_cost"]!! + s.battery.lostExportCost
            t["battery_charge_amount"] = t["battery_charge_amount"]!! + s.battery.getChargeAmountKwh()
            t["battery_export_cost"] = t["battery_export_cost"]!! + s.battery.exportCost
            t["battery_exported"] = t["battery_exported"]!! + s.battery.exported / 1000
            t["battery_days_run_out"] = t["battery_days_run_out"]!! + s.battery.daysRunOut
            t["battery_extra_required"] = t["battery_extra_required"]!! + s.battery.extraRequired / 1000
        }

        return t.mapValues { (_, v) -> (Math.round(v * 100.0) / 100.0) }
    }
}
