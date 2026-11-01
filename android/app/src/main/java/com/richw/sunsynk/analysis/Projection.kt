package com.richw.sunsynk.analysis

import com.google.gson.JsonObject
import java.time.LocalDate

/**
 * Mirrors analysis/projection.py: a seasonally weighted annual projection, priced at the
 * latest tariff rates. kWh are kept per calendar month; each month is averaged over every
 * year it was seen and weighted by its length, so neither the season the data happens to
 * cover nor a month recorded twice skews the year. Until all 12 months have been seen it
 * falls back to the plain ×365/days average ([seasonal] is false: provisional).
 */
data class ProjectionRates(val peak: Double, val offPeak: Double, val export: Double, val dateFrom: String)

data class AnnualMoney(val selfConsumption: Double, val export: Double, val battery: Double) {
    val solar get() = selfConsumption + export
}

/** The newest tariff period that has started by [today]; if none has, the newest in the file. */
fun latestRates(pricesJson: JsonObject, today: String = LocalDate.now().toString()): ProjectionRates {
    val items = pricesJson.getAsJsonObject("data").getAsJsonArray("prices").map { it.asJsonObject }
    val started = items.filter { it.get("datefrom").asString <= today }
    val item = (started.ifEmpty { items }).maxBy { it.get("datefrom").asString }
    return ProjectionRates(
        peak = item.get("peakRate").asDouble,
        offPeak = item.get("offpeakRate").asDouble,
        export = item.get("exportRate").asDouble,
        dateFrom = item.get("datefrom").asString,
    )
}

class SeasonalProjection {
    companion object {
        val DAYS_IN_MONTH = intArrayOf(31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    }

    private val kwh = List(12) { mutableMapOf<String, Double>() }
    private val monthDays = IntArray(12)

    fun addDay(date: String, values: Map<String, Double>) {
        val m = date.substring(5, 7).toInt() - 1
        monthDays[m]++
        for ((key, value) in values) kwh[m][key] = (kwh[m][key] ?: 0.0) + value
    }

    val days get() = monthDays.sum()
    val monthsCovered get() = monthDays.count { it > 0 }
    val seasonal get() = monthsCovered == 12

    fun annualKwh(key: String): Double {
        if (seasonal) return (0 until 12).sumOf { m -> (kwh[m][key] ?: 0.0) / monthDays[m] * DAYS_IN_MONTH[m] }
        if (days == 0) return 0.0
        return 365 * kwh.sumOf { it[key] ?: 0.0 } / days
    }

    /** Same formulas as EnergySummary (solar) and VirtualBattery.getSavings (battery). */
    fun annualMoney(r: ProjectionRates): AnnualMoney {
        val a = ::annualKwh
        return AnnualMoney(
            selfConsumption = (a("load_peak") - a("import_peak")) * r.peak +
                (a("load_off_peak") - a("import_off_peak")) * r.offPeak,
            export = a("export") * r.export,
            battery = a("batt_drawn") * r.peak + a("batt_exported") * r.export -
                a("batt_grid_charge") * r.offPeak - a("batt_pv_diverted") * r.export,
        )
    }
}
