package com.richw.sunsynk.analysis

import com.google.gson.JsonObject
import java.time.LocalDate
import java.time.format.DateTimeFormatter

fun batteryWarnings(battery: VirtualBattery, pd: PriceData): List<String> {
    val warnings = mutableListOf<String>()
    val de = battery.dischargeEfficiency
    val ce = battery.chargeEfficiency
    val pce = battery.pvChargeEfficiency

    if (battery.gridCharge) {
        val chargeCost = pd.currentOffPeak / ce
        val loadValue = pd.currentPeak * de
        if (chargeCost > loadValue) {
            warnings.add(
                "grid charge to load makes a loss: costs ${"%.2f".format(chargeCost * 100)}p/kWh stored " +
                "(${"%.2f".format(pd.currentOffPeak * 100)}p off-peak ÷ $ce charge eff) " +
                "but saves only ${"%.2f".format(loadValue * 100)}p/kWh " +
                "(${"%.2f".format(pd.currentPeak * 100)}p peak × $de discharge eff)"
            )
        }
        if (battery.export == 1) {
            val exportValue = pd.currentExport * de
            if (chargeCost > exportValue) {
                warnings.add(
                    "grid charge to re-export makes a loss: costs ${"%.2f".format(chargeCost * 100)}p/kWh stored " +
                    "but re-exports at only ${"%.2f".format(exportValue * 100)}p/kWh"
                )
            }
        }
    }
    if (battery.pvEnabled == 1) {
        val pvLoadValue = pd.currentPeak * pce * de
        if (pd.currentExport > pvLoadValue) {
            warnings.add(
                "PV diversion to battery makes a loss: foregoes ${"%.2f".format(pd.currentExport * 100)}p/kWh export " +
                "but saves only ${"%.2f".format(pvLoadValue * 100)}p/kWh at peak"
            )
        }
    }
    return warnings
}

class EnergyPrices(
    private val pricesJson: JsonObject,
    val origBattery: VirtualBattery,
    val originalPrice: Double = 6206.47,
    val label: String = "",
    private val offPeakBaselineKwh: Double = 0.96,
    val offPeakShiftEnabled: Boolean = true,
    val batteryEnabled: Boolean = true,
    val batteryPrice: Double = 0.0,
) {
    val aggregator = EnergySummaryAggregator()
    var priceData = PriceData()
    var battery: VirtualBattery
    private var grandTotals: Map<String, Double> = emptyMap()
    private var changed = true

    init {
        priceData.offPeakBaselineKwh = offPeakBaselineKwh
        battery = makeBattery(priceData)
        aggregator.addSummary(EnergySummary(priceData, battery, 0.0, originalPrice))
        grandTotals = aggregator.getGrandTotals()
    }

    private fun makeBattery(pd: PriceData) = VirtualBattery(
        priceData = pd,
        batterySize = origBattery.batterySize,
        pvEnabled = origBattery.pvEnabled,
        startCharging = origBattery.startCharging,
        stopCharging = origBattery.stopCharging,
        exportWindowStart = origBattery.exportWindowStart,
        exportWindowStop = origBattery.exportWindowStop,
        dischargeEfficiency = origBattery.dischargeEfficiency,
        pvChargeEfficiency = origBattery.pvChargeEfficiency,
        maxOutputW = origBattery.maxOutputW,
        useExport = origBattery.export == 1,
        chargeEfficiency = origBattery.chargeEfficiency,
        gridCharge = origBattery.gridCharge,
        dischargeReserveWh = origBattery.dischargeReserveWh,
        dischargeReserveUntilStr = origBattery.dischargeReserveUntilStr,
    )

    fun checkDate(dateIn: String) {
        val dateFmt = DateTimeFormatter.ofPattern("yyyy-MM-dd")
        if (dateIn.endsWith("-01")) {
            aggregator.getLastSummary().newMonth()
        }
        val dateDt = LocalDate.parse(dateIn, dateFmt)
        if (dateDt.isBefore(priceData.currentStart) || dateDt.isAfter(priceData.currentStop)) {
            val pricesArr = pricesJson.getAsJsonObject("data").getAsJsonArray("prices")
            for (item in pricesArr) {
                val obj = item.asJsonObject
                val newStart = LocalDate.parse(obj.get("datefrom").asString, dateFmt)
                val newStop = LocalDate.parse(obj.get("dateto").asString, dateFmt)
                if (!dateDt.isBefore(newStart) && !dateDt.isAfter(newStop)) {
                    priceData = PriceData()
                    priceData.offPeakBaselineKwh = offPeakBaselineKwh
                    priceData.currentOffPeak = obj.get("offpeakRate").asDouble
                    priceData.currentPeak = obj.get("peakRate").asDouble
                    priceData.currentExport = obj.get("exportRate").asDouble
                    priceData.currentOffPeakStart = obj.get("offpeakStart").asString
                    priceData.currentOffPeakStop = obj.get("offpeakStop").asString
                    priceData.standingCharge = obj.get("standingCharge").asDouble
                    if (obj.has("InterestRate")) priceData.interestRate = obj.get("InterestRate").asDouble
                    priceData.currentStart = newStart
                    priceData.currentStop = newStop
                    battery = makeBattery(priceData)

                    val last = aggregator.getLastSummary()
                    val remainder = last.getRemainder()
                    aggregator.addSummary(
                        EnergySummary(priceData, battery, last.cumulativeSavings, last.altInvestValue, remainder)
                    )
                    break
                }
            }
        }
    }

    fun addData(day: EnergyDay) {
        changed = true
        aggregator.updateLastSummary(day)
    }

    fun getGrandTotals(): Map<String, Double> {
        if (changed) {
            changed = false
            grandTotals = aggregator.getGrandTotals()
        }
        return grandTotals
    }

    fun getDerived(): Map<String, Any?> {
        val t = getGrandTotals()
        val seg = t["total_export_amount_calc"]!!
        val opExcessSavings = if (offPeakShiftEnabled) t["total_off_peak_excess_savings"]!! else 0.0

        fun pct(amount: Double) = (amount * 100 / originalPrice).round2()

        val billSavings = (t["total_cost_without_solar"]!! - t["total_cost"]!!).round2()
        val billSavingsIncSeg = (billSavings + seg).round2()
        val calcSavings = (t["total_savings_calc"]!! + opExcessSavings).round2()
        val calcSavingsExcSeg = (calcSavings - seg).round2()
        val suppliedSavings = (t["total_savings_supplied"]!! + opExcessSavings).round2()
        val suppliedSavingsExcSeg = (suppliedSavings - t["total_export_amount_supplied"]!!).round2()
        val batterySavings = if (batteryEnabled) {
            (t["battery_nominal_cost"]!! + t["battery_export_cost"]!! -
                t["battery_cost_from_grid"]!! - t["battery_lost_export_cost"]!!).round2()
        } else 0.0

        val days = t["days"]!!
        val annualisedBattery = if (days > 0) (365 * batterySavings / days).round2() else 0.0
        val paybackYears: Double? = if (batteryPrice > 0 && annualisedBattery > 0) {
            (batteryPrice / annualisedBattery).round2()
        } else null

        return mapOf(
            "t" to t,
            "seg" to seg,
            "bill_savings" to billSavings,
            "bill_savings_inc_seg" to billSavingsIncSeg,
            "calc_savings" to calcSavings,
            "calc_savings_exc_seg" to calcSavingsExcSeg,
            "supplied_savings" to suppliedSavings,
            "supplied_savings_exc_seg" to suppliedSavingsExcSeg,
            "calc_roi_pct" to pct(t["total_savings_calc"]!!),
            "supplied_roi_pct" to pct(t["total_savings_supplied"]!!),
            "battery_savings" to batterySavings,
            "annualised_battery" to annualisedBattery,
            "payback_years" to paybackYears,
        )
    }

    fun buildReport(showBattery: Boolean = true): String {
        val sb = StringBuilder()
        val d = getDerived()
        @Suppress("UNCHECKED_CAST")
        val t = d["t"] as Map<String, Double>
        val days = t["days"]!!.toInt()

        sb.appendLine()
        sb.appendLine("ENERGY TOTALS")
        sb.appendLine("  (Two values: interval-calculated | inverter-reported daily kWh)")
        sb.appendLine("  Export:  ${"%.2f".format(t["total_calc_export"])}kWh | ${"%.2f".format(t["total_supplied_export"])}kWh" +
            "    Peak: ${"%.2f".format(t["total_calc_export_peak"])}kWh   Off-peak: ${"%.2f".format(t["total_calc_export_off_peak"])}kWh")
        sb.appendLine("  Import:  ${"%.2f".format(t["total_calc_import"])}kWh | ${"%.2f".format(t["total_supplied_import"])}kWh" +
            "    Peak: ${"%.2f".format(t["total_calc_import_peak"])}kWh   Off-peak: ${"%.2f".format(t["total_calc_import_off_peak"])}kWh")
        sb.appendLine("  PV gen:  ${"%.2f".format(t["total_calc_pv"])}kWh | ${"%.2f".format(t["total_supplied_pv"])}kWh")
        sb.appendLine("  Load:    ${"%.2f".format(t["total_calc_load"])}kWh | ${"%.2f".format(t["total_supplied_load"])}kWh")
        sb.appendLine("  Days:    $days")

        if (days > 0) {
            sb.appendLine()
            sb.appendLine("DAILY AVERAGES  (interval-calculated | inverter-reported)")
            sb.appendLine("  Export: ${"%.2f".format(t["total_calc_export"]!! / days)}kWh | ${"%.2f".format(t["total_supplied_export"]!! / days)}kWh" +
                "    Peak: ${"%.2f".format(t["total_calc_export_peak"]!! / days)}kWh   Off-peak: ${"%.2f".format(t["total_calc_export_off_peak"]!! / days)}kWh")
            sb.appendLine("  Import: ${"%.2f".format(t["total_calc_import"]!! / days)}kWh | ${"%.2f".format(t["total_supplied_import"]!! / days)}kWh" +
                "    Peak: ${"%.2f".format(t["total_calc_import_peak"]!! / days)}kWh   Off-peak: ${"%.2f".format(t["total_calc_import_off_peak"]!! / days)}kWh")
        }

        sb.appendLine()
        sb.appendLine("ENERGY COSTS")
        sb.appendLine("  Total paid to energy company:                   £${"%.2f".format(t["total_cost"])}")
        sb.appendLine("  Total paid after deducting SEG income:          £${"%.2f".format(t["total_cost"]!! - (d["seg"] as Double))}")
        sb.appendLine("  Smart Export Guarantee (SEG) income:            £${"%.2f".format(d["seg"])}")
        sb.appendLine("  Estimated cost without solar or battery:        £${"%.2f".format(t["total_cost_without_solar"])}")

        sb.appendLine()
        sb.appendLine("SOLAR SAVINGS  (interval-calculated | inverter-reported)")
        sb.appendLine("  Savings excl. export income:   £${d["calc_savings_exc_seg"]} | £${d["supplied_savings_exc_seg"]}")
        sb.appendLine("  Savings incl. SEG income:      £${d["bill_savings_inc_seg"]}")

        sb.appendLine()
        sb.appendLine("RETURN ON INVESTMENT")
        sb.appendLine("  Solar savings from generation (excl. off-peak shifting):")
        sb.appendLine("    Interval-calculated: £${t["total_savings_calc"]} (${d["calc_roi_pct"]}% of install cost)")
        sb.appendLine("    Inverter-reported:   £${t["total_savings_supplied"]} (${d["supplied_roi_pct"]}% of install cost)")
        val shiftLabel = if (offPeakShiftEnabled) "incl. off-peak load shifting" else "(off-peak shifting disabled)"
        sb.appendLine("  Total savings $shiftLabel:")
        sb.appendLine("    Interval-calculated: £${d["calc_savings"]}    Inverter-reported: £${d["supplied_savings"]}")
        sb.appendLine()
        val lastSummary = aggregator.getLastSummary()
        val finalRemainder = lastSummary.getRemainder()
        val cumulative = (lastSummary.cumulativeSavings + finalRemainder).round2()
        val alt = lastSummary.altInvestValue.round2()
        val percentage = if (alt > 0) (100 * cumulative / alt).round2() else 0.0
        sb.appendLine("  Cumulative savings (with compound interest):    £$cumulative")
        sb.appendLine("  Alternative investment value (same interest):   £$alt")
        sb.appendLine("  Net ROI:                                        $percentage%")

        if (offPeakShiftEnabled) {
            sb.appendLine()
            sb.appendLine("OFF-PEAK IMPORT ANALYSIS")
            sb.appendLine("  Total off-peak import:               ${"%.2f".format(t["total_calc_import_off_peak"])}kWh")
            sb.appendLine("  Excess above expected baseline:      ${"%.2f".format(t["total_off_peak_excess"])}kWh  (load shifted to off-peak)")
            sb.appendLine("  Saving from off-peak load shifting:  £${"%.2f".format(t["total_off_peak_excess_savings"])}  (excess × (peak − off-peak rate))")
        }

        if (batteryEnabled && showBattery && days > 0) {
            sb.appendLine()
            sb.appendLine("VIRTUAL BATTERY SIMULATION  (models a battery charged at off-peak, discharged at peak)")
            val seen = mutableSetOf<String>()
            for (summary in aggregator.summaries) {
                for (w in batteryWarnings(origBattery, summary.priceData)) {
                    if (seen.add(w)) sb.appendLine("  WARNING: $w")
                }
            }
            sb.appendLine("  Charged from grid at off-peak:              ${"%.2f".format(t["battery_charge_amount"])}kWh  (cost: £${"%.2f".format(t["battery_cost_from_grid"])})")
            sb.appendLine("  Peak-rate value of energy delivered:        £${"%.2f".format(t["battery_nominal_cost"])}  (what it would cost drawn from grid at peak)")
            sb.appendLine("  PV energy diverted to battery:              foregone export income: £${"%.2f".format(t["battery_lost_export_cost"])}")
            sb.appendLine("  Re-exported to grid during export window:   ${"%.2f".format(t["battery_exported"])}kWh = £${"%.2f".format(t["battery_export_cost"])}")
            sb.appendLine("  Days battery ran out before peak demand met: ${t["battery_days_run_out"]!!.toInt()}  (shortfall: ${"%.2f".format(t["battery_extra_required"])}kWh)")
            sb.appendLine("  Net potential saving:                       £${d["battery_savings"]}")
            sb.appendLine("    = peak saving £${t["battery_nominal_cost"]} + export £${t["battery_export_cost"]}" +
                " − charging cost £${t["battery_cost_from_grid"]} − foregone export £${t["battery_lost_export_cost"]}")
            val perDay = if (days > 0) ((d["battery_savings"] as Double) / days).round2() else 0.0
            sb.appendLine("  Per day: £$perDay   Annualised: £${d["annualised_battery"]}")
            if (batteryPrice > 0) {
                val py = d["payback_years"] as? Double
                if (py != null) {
                    sb.appendLine("  Battery cost: £${"%.2f".format(batteryPrice)}   Payback period: ${"%.1f".format(py)} years")
                } else {
                    sb.appendLine("  Battery cost: £${"%.2f".format(batteryPrice)}   Payback period: N/A")
                }
            }
        }

        return sb.toString()
    }
}
