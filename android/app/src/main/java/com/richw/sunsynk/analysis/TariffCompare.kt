package com.richw.sunsynk.analysis

import com.google.gson.JsonObject
import kotlin.math.abs

/**
 * Mirrors Python analysis/tariffcompare.py: prices the projected year of half-hourly import
 * (house + EV) and export against tariffs given as 48 local half-hour rates (p/kWh).
 */
data class Tariff(
    val supplier: String,
    val name: String,
    val code: String,
    val source: String,
    val kind: String,
    val slotRates: List<Double>,
    val standing: Double,                 // p/day
    val smartEv: Boolean = false,
    val evRate: Double? = null,           // p/kWh for smart-charged EV energy
    val note: String = "",
    val direction: String = "IMPORT",     // or EXPORT
    val requires: String? = null,         // export: supplier whose import tariff it needs
    val flatImportOnly: Boolean = false,  // export: not with time-of-use import tariffs
    val window: String = "",              // current tariff only: its off-peak window
)

/** The projected year from the latest analysis run, saved for the Tariffs tab. */
data class TariffProfile(
    val savedAt: String,
    val house: List<Double>,              // kWh per half-hour slot over a year
    val ev: List<Double>,
    val export: List<Double>,
    val monthlyHouse: Map<Int, Double>,   // average month, kWh (months seen only)
    val monthlyExport: Map<Int, Double>,
    val projectedImportKwh: Double,       // PROJECTED YEARLY IMPORT, for the cross-check
    val seasonal: Boolean,
    val note: String,
    val currentImport: Tariff,
    val currentExport: Tariff,
)

data class ImportRow(
    val tariff: Tariff,
    val houseCost: Double, val evNow: Double, val evRetimed: Double, val evPPerKwh: Double,
    val standingYear: Double, val asNow: Double, val total: Double, val cheapKwh: Double,
    val rateType: String, val ratesText: String, val vsCurrent: Double,
    val evSplit: EvSplit = EvSplit(0.0, 0.0, 0.0, 0.0),
) { val isCurrent get() = tariff.code == "CURRENT" }

/** Re-timed EV import split into kWh at the tariff's cheapest (or smart-EV) rate and the rest
 *  at their average rate, so a blended p/kWh can be read as e.g. "1,970 @ 3.80p + 46 @ 25.63p". */
data class EvSplit(val offKwh: Double, val offRate: Double, val peakKwh: Double, val peakRate: Double)

data class ExportRow(
    val tariff: Tariff, val income: Double, val rateType: String, val ratesText: String,
    val vsCurrent: Double,
) { val isCurrent get() = tariff.code == "CURRENT_EXPORT" }

data class Pair3(val imp: ImportRow, val exp: ExportRow) {
    val net get() = imp.total - exp.income
    val isCurrent get() = imp.isCurrent && exp.isCurrent
}

data class PairBreakdown(
    val housePeakKwh: Double, val housePeak: Double, val housePeakRate: Double,
    val houseOffKwh: Double, val houseOff: Double, val houseOffRate: Double,
    val evKwh: Double, val ev: Double, val evRate: Double, val evSplit: EvSplit,
    val standing: Double, val importTotal: Double,
    val exportKwh: Double, val export: Double, val exportRate: Double, val net: Double,
)

object TariffCompare {

    /** "HH:MM-HH:MM" (may wrap midnight) -> half-hour slot indices it covers. */
    fun windowSlots(window: String): Set<Int> {
        val (a, b) = window.split("-").map { it.trim() }
        fun slot(t: String) = t.substring(0, 2).toInt() * 2 + (if (t.substring(3, 5).toInt() >= 30) 1 else 0)
        val start = slot(a.padStart(5, '0'))
        val stop = slot(b.padStart(5, '0'))
        return when {
            start == stop -> emptySet()
            start < stop -> (start until stop).toSet()
            else -> (start until 48).toSet() + (0 until stop).toSet()
        }
    }

    fun touSlotRates(peak: Double, offPeak: Double, window: String): List<Double> {
        val cheap = windowSlots(window)
        return List(48) { if (it in cheap) offPeak else peak }
    }

    /** The energyPrices file's newest started period as the current import tariff (p/kWh). */
    fun currentImport(pricesJson: JsonObject, today: String = java.time.LocalDate.now().toString()): Tariff {
        val r = latestRates(pricesJson, today)
        val item = pricesJson.getAsJsonObject("data").getAsJsonArray("prices").map { it.asJsonObject }
            .first { it.get("datefrom").asString == r.dateFrom }
        val window = "${item.get("offpeakStart").asString}-${item.get("offpeakStop").asString}"
        return Tariff("Current", "energyPrices from ${r.dateFrom}", "CURRENT", "config", "",
            touSlotRates(r.peak * 100, r.offPeak * 100, window), r.standing * 100, window = window)
    }

    fun currentExport(pricesJson: JsonObject, today: String = java.time.LocalDate.now().toString()): Tariff {
        val r = latestRates(pricesJson, today)
        return Tariff("Current", "export rate from ${r.dateFrom}", "CURRENT_EXPORT", "config", "",
            List(48) { r.export * 100 }, 0.0, direction = "EXPORT")
    }

    /** (house kWh[48], EV kWh[48]): seasonal house year + the projected EV import over its shape. */
    fun annualProfile(slots: SlotProjection, annualImportKwh: Double): Pair<List<Double>, List<Double>> {
        val house = slots.annualHouse().toList()
        val evYear = maxOf(0.0, annualImportKwh - house.sum())
        return Pair(house, slots.evShape().map { evYear * it })
    }

    private data class Price(
        val houseCost: Double, val evNow: Double, val evRetimed: Double, val evPPerKwh: Double,
        val standingYear: Double, val asNow: Double, val total: Double, val cheapKwh: Double,
        val evSplit: EvSplit,
    )

    private fun price(t: Tariff, house: List<Double>, ev: List<Double>, currentOff: Set<Int>): Price {
        val rates = t.slotRates
        val cheapest = rates.min()
        val standing = 365 * t.standing / 100
        val houseCost = house.indices.sumOf { house[it] * rates[it] } / 100
        val evNow = ev.indices.sumOf { ev[it] * rates[it] } / 100
        val evOffRate = if (t.smartEv) minOf(t.evRate ?: cheapest, cheapest) else cheapest
        // rate each half-hour's EV energy is charged at once re-timed
        val evRates = ev.indices.map { k ->
            if (t.smartEv || k in currentOff || rates[k] <= cheapest + 0.005) evOffRate else rates[k]
        }
        val evRetimed = minOf(evNow, ev.indices.sumOf { ev[it] * evRates[it] } / 100)
        val offIdx = ev.indices.filter { evRates[it] <= evOffRate + 0.005 }
        val peakIdx = ev.indices.filter { it !in offIdx }
        val evPeakKwh = peakIdx.sumOf { ev[it] }
        val evSplit = EvSplit(offIdx.sumOf { ev[it] }, evOffRate, evPeakKwh,
            if (evPeakKwh > 0) peakIdx.sumOf { ev[it] * evRates[it] } / evPeakKwh else 0.0)
        val cheapSlots = (0 until 48).filter { rates[it] <= cheapest + 0.005 }
        val evKwh = ev.sum()
        return Price(houseCost, evNow, evRetimed, if (evKwh > 0) 100 * evRetimed / evKwh else 0.0,
            standing, houseCost + evNow + standing, houseCost + evRetimed + standing,
            if (cheapSlots.size < 48) cheapSlots.sumOf { house[it] + ev[it] } else 0.0, evSplit)
    }

    fun priceExport(t: Tariff, export: List<Double>) = export.indices.sumOf { export[it] * t.slotRates[it] } / 100

    /** (type, text): "single" "25.60", "tou" "9.40 00:30-05:30 / 34.71", "dynamic" "avg 28.1 (..)".
     *  A time-of-use text leads with the cheapest rate, or for export ([bestHigh]) the highest. */
    fun describeRates(rates: List<Double>, bestHigh: Boolean = false): Pair<String, String> {
        val distinct = rates.map { Math.round(it * 100) / 100.0 }.toSortedSet().toList()
        if (distinct.size == 1) return Pair("single", "%.2f".format(distinct[0]))
        if (distinct.size > 4) return Pair("dynamic",
            "avg %.1f (%.1f-%.1f)".format(rates.average(), distinct.first(), distinct.last()))
        val best = if (bestHigh) rates.max() else rates.min()
        val parts = runs((0 until 48).filter { abs(rates[it] - best) <= 0.005 })
            .joinToString(",") { (a, b) -> "${slotTime(a)}-${slotTime(b)}" }
        val others = rates.filter { abs(it - best) > 0.005 }.map { Math.round(it * 100) / 100.0 }
            .toSortedSet().joinToString("/") { "%.2f".format(it) }
        return Pair("tou", "%.2f %s / %s".format(best, parts, others))
    }

    /** Contiguous [start, stop) runs of slot indices, joining a run across midnight. */
    fun runs(slots: List<Int>): List<Pair<Int, Int>> {
        val out = mutableListOf<IntArray>()
        for (k in slots) {
            if (out.isNotEmpty() && out.last()[1] == k) out.last()[1] = k + 1 else out.add(intArrayOf(k, k + 1))
        }
        if (out.size > 1 && out.first()[0] == 0 && out.last()[1] == 48) out.first()[0] = out.removeAt(out.size - 1)[0]
        return out.map { Pair(it[0], it[1]) }
    }

    private fun slotTime(k: Int) = "%02d:%02d".format((k % 48) / 2, 30 * (k % 2))

    /** Priced import rows, cheapest total first. */
    fun compare(tariffs: List<Tariff>, current: Tariff, house: List<Double>, ev: List<Double>): List<ImportRow> {
        val off = windowSlots(current.window)
        val base = price(current, house, ev, off).total
        return (listOf(current) + tariffs.filter { it.direction == "IMPORT" }).map { t ->
            val p = price(t, house, ev, off)
            val (kind, text) = describeRates(t.slotRates)
            ImportRow(t, p.houseCost, p.evNow, p.evRetimed, p.evPPerKwh, p.standingYear, p.asNow, p.total,
                p.cheapKwh, kind, text, p.total - base, p.evSplit)
        }.sortedBy { it.total }
    }

    /** Priced export rows, highest income first. */
    fun compareExports(tariffs: List<Tariff>, current: Tariff, export: List<Double>): List<ExportRow> {
        val base = priceExport(current, export)
        return (listOf(current) + tariffs.filter { it.direction == "EXPORT" }).map { t ->
            val income = priceExport(t, export)
            val (kind, text) = describeRates(t.slotRates, bestHigh = true)
            ExportRow(t, income, kind, text, income - base)
        }.sortedByDescending { it.income }
    }

    /** The [n] cheapest import + export pairs (plus the current pair): an export tariff only
     *  with the supplier it requires, a flat-import-only one only with a single-rate import,
     *  and the current import only with the current export. */
    fun bestPairs(imports: List<ImportRow>, exports: List<ExportRow>, n: Int = 10): List<Pair3> {
        val pairs = mutableListOf<Pair3>()
        for (imp in imports) for (exp in exports) {
            val r = exp.tariff.requires
            if (r != null && r != imp.tariff.supplier) continue
            if (exp.tariff.flatImportOnly && imp.rateType != "single") continue
            if (imp.isCurrent && !exp.isCurrent) continue
            pairs.add(Pair3(imp, exp))
        }
        pairs.sortBy { it.net }
        return pairs.take(n) + pairs.drop(n).filter { it.isCurrent }
    }

    /** House split into the import tariff's cheapest-rate half-hours ("off-peak") and the rest,
     *  EV (re-timed), standing, export and net, for one pair. */
    fun pairBreakdown(pair: Pair3, house: List<Double>, ev: List<Double>, export: List<Double>): PairBreakdown {
        val rates = pair.imp.tariff.slotRates
        val cheapest = rates.min()
        val tou = rates.map { Math.round(it * 100) }.toSet().size > 1
        val off = (0 until 48).filter { tou && rates[it] <= cheapest + 0.005 }.toSet()
        val peak = (0 until 48).filter { it !in off }
        val offKwh = off.sumOf { house[it] }
        val peakKwh = peak.sumOf { house[it] }
        val offCost = off.sumOf { house[it] * rates[it] } / 100
        val peakCost = peak.sumOf { house[it] * rates[it] } / 100
        val exportKwh = export.sum()
        return PairBreakdown(
            peakKwh, peakCost, if (peakKwh > 0) 100 * peakCost / peakKwh else 0.0,
            offKwh, offCost, if (offKwh > 0) 100 * offCost / offKwh else 0.0,
            ev.sum(), pair.imp.evRetimed, pair.imp.evPPerKwh, pair.imp.evSplit,
            pair.imp.standingYear, pair.imp.total,
            exportKwh, pair.exp.income, if (exportKwh > 0) 100 * pair.exp.income / exportKwh else 0.0,
            pair.net,
        )
    }
}
