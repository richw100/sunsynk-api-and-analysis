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
data class ProjectionRates(
    val peak: Double, val offPeak: Double, val export: Double, val standing: Double, val dateFrom: String,
)

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
        standing = item.get("standingCharge")?.asDouble ?: 0.0,
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

    /** {month index 0-11: average kWh/day} for every calendar month seen. */
    fun monthlyDailyAvg(key: String): Map<Int, Double> =
        (0 until 12).filter { monthDays[it] > 0 }.associateWith { (kwh[it][key] ?: 0.0) / monthDays[it] }

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

val EV_KEYS = listOf("ev_peak", "ev_off_peak", "ev_from_pv")

data class RebasedEv(val kwh: Map<String, Double>, val ratio: Double, val measuredMonths: Int)

/**
 * A year of EV charging at the level seen since a change in use, e.g. a second car (mirrors
 * Python projection.rebased_annual_ev). Calendar months seen since the change ([after]) count
 * as measured; a month seen only before it is scaled by the after/before ratio over the
 * months both periods share, keeping last year's seasonal shape. The peak/off-peak/solar
 * split follows the after period. Without after data: the plain seasonal year, ratio 1.
 */
fun rebasedAnnualEv(before: SeasonalProjection, after: SeasonalProjection): RebasedEv {
    fun total(p: SeasonalProjection): Map<Int, Double> {
        val peak = p.monthlyDailyAvg("ev_peak")
        val off = p.monthlyDailyAvg("ev_off_peak")
        return (peak.keys + off.keys).associateWith { (peak[it] ?: 0.0) + (off[it] ?: 0.0) }
    }
    val dim = SeasonalProjection.DAYS_IN_MONTH
    val b = total(before)
    val a = total(after)
    if (a.isEmpty()) return RebasedEv(EV_KEYS.associateWith { before.annualKwh(it) }, 1.0, 0)
    val shared = a.keys.filter { it in b }
    val bShared = shared.sumOf { b[it]!! * dim[it] }
    val ratio = if (bShared > 0) shared.sumOf { a[it]!! * dim[it] } / bShared else 1.0
    var year = (0 until 12).sumOf { m -> (a[m] ?: ((b[m] ?: 0.0) * ratio)) * dim[m] }
    val seen = a.keys + b.keys
    if (seen.size < 12) year = year * 365 / seen.sumOf { dim[it] }
    val split = EV_KEYS.associateWith { k -> after.monthlyDailyAvg(k).entries.sumOf { (m, v) -> v * dim[m] } }
    val afterTotal = split["ev_peak"]!! + split["ev_off_peak"]!!
    return RebasedEv(EV_KEYS.associateWith { if (afterTotal > 0) year * split[it]!! / afterTotal else 0.0 },
        ratio, a.size)
}


/**
 * Mirrors Python projection.SlotProjection: a seasonally weighted year of grid import and
 * export per local half-hour (48 slots). Per calendar month it sums house import (total less
 * the EV's share), EV import and export per slot; [evShape] is the EV import's half-hour shape
 * from days on/after [evBasisFrom] when given (current charging habits).
 */
class SlotProjection(private val evBasisFrom: String = "") {
    private val house = List(12) { DoubleArray(48) }
    private val export = List(12) { DoubleArray(48) }
    private val monthDays = IntArray(12)
    private val evAll = DoubleArray(48)
    private val evAfter = DoubleArray(48)
    private val evMonthAll = DoubleArray(12)        // EV import kWh per calendar month, every day
    private val evMonthAfter = DoubleArray(12)      // ... and only days since evBasisFrom
    private val afterDays = IntArray(12)

    fun addDay(date: String, totalWh: DoubleArray, evWh: DoubleArray, exportWh: DoubleArray? = null) {
        val m = date.substring(5, 7).toInt() - 1
        monthDays[m]++
        val after = evBasisFrom.isNotEmpty() && date >= evBasisFrom
        val evKwh = evWh.sum() / 1000
        evMonthAll[m] += evKwh
        if (after) { evMonthAfter[m] += evKwh; afterDays[m]++ }
        for (k in 0 until 48) {
            house[m][k] += (totalWh[k] - evWh[k]) / 1000
            evAll[k] += evWh[k] / 1000
            if (after) evAfter[k] += evWh[k] / 1000
            if (exportWh != null) export[m][k] += exportWh[k] / 1000
        }
    }

    val days get() = monthDays.sum()
    val seasonal get() = monthDays.all { it > 0 }

    private fun annual(series: List<DoubleArray>): DoubleArray {
        if (seasonal) return DoubleArray(48) { k ->
            (0 until 12).sumOf { m -> series[m][k] / monthDays[m] * SeasonalProjection.DAYS_IN_MONTH[m] }
        }
        if (days == 0) return DoubleArray(48)
        return DoubleArray(48) { k -> 365.0 * series.sumOf { it[k] } / days }
    }

    fun annualHouse() = annual(house)
    fun annualExport() = annual(export)

    /** {month index: (house import kWh, export kWh)} for an average such month (seen months only). */
    fun monthly(): Map<Int, Pair<Double, Double>> = (0 until 12).filter { monthDays[it] > 0 }.associateWith { m ->
        Pair(house[m].sum() / monthDays[m] * SeasonalProjection.DAYS_IN_MONTH[m],
            export[m].sum() / monthDays[m] * SeasonalProjection.DAYS_IN_MONTH[m])
    }

    /** EV import for an average such month, scaled so the months sum to [evYear] (the rebased EV
     *  year): months seen since evBasisFrom use those days; earlier-only months use all days
     *  times the after/all ratio over months that have both (as rebasedAnnualEv). */
    fun monthlyEv(evYear: Double): Map<Int, Double> {
        val dim = SeasonalProjection.DAYS_IN_MONTH
        val shared = (0 until 12).filter { afterDays[it] > 0 && monthDays[it] > afterDays[it] }
        val allPerDay = { m: Int -> evMonthAll[m] / monthDays[m] }
        val afterPerDay = { m: Int -> evMonthAfter[m] / afterDays[m] }
        val ratio = if (shared.isEmpty()) 1.0 else shared.sumOf { afterPerDay(it) } / shared.sumOf { allPerDay(it) }
            .takeIf { it > 0 }.let { it ?: 1.0 }
        val raw = (0 until 12).filter { monthDays[it] > 0 }.associateWith { m ->
            (if (afterDays[m] > 0) afterPerDay(m) else allPerDay(m) * (if (evBasisFrom.isEmpty()) 1.0 else ratio)) * dim[m]
        }
        val total = raw.values.sum()
        return if (total > 0) raw.mapValues { it.value * evYear / total } else raw
    }

    /** Fraction of EV import in each slot (sums to 1, or all 0 without EV data). */
    fun evShape(): DoubleArray {
        val src = if (evAfter.sum() > 0) evAfter else evAll
        val total = src.sum()
        return DoubleArray(48) { if (total > 0) src[it] / total else 0.0 }
    }
}
