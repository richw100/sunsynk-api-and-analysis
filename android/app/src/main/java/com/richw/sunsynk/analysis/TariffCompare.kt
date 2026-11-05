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
    val gasRate: Double? = null,          // direction GAS: p/kWh inc VAT
    val gasStanding: Double? = null,      // direction GAS: p/day
    val gas: Tariff? = null,              // import: its own / matching gas tariff (direction GAS)
    val ratesUrl: String? = null,         // Kraken unit-rate timeline (dynamic tariffs' history)
    // Dynamic (half-hourly) tariffs: the average price per calendar month (0-11) and local
    // half-hour over the last 12 complete months, priced against the profile's month-slot kWh.
    val monthSlotRates: List<List<Double>>? = null,
)

/** A seasonally weighted gas year: each calendar month's average day x its length. */
data class GasYear(val kwh: Double, val monthly: Map<Int, Double>, val seasonal: Boolean, val days: Int)

data class GasRow(val tariff: Tariff, val cost: Double, val vsCurrent: Double) {
    val isCurrent get() = tariff.code == "CURRENT_GAS"
}

/** Import + export pair with gas: [own] = its own/supplier's gas (current gas for the current
 *  pair), [cheapest] = cheapest gas anywhere; [total] = net + the cheaper of the two. */
/** What the panels are worth a year at one import + export pair's rates. */
data class SolarRoi(
    val selfUseKwh: Double, val selfUse: Double,     // PV the house used, at the import rates it displaced
    val evKwh: Double, val ev: Double,               // PV the car took, at the overnight / smart-EV rate
    val exportKwh: Double, val export: Double,       // export income
    val cost: Double,                                // what the panels cost (config originalPrice)
) {
    val saving get() = selfUse + ev + export
    val roiPct get() = if (cost > 0) 100 * saving / cost else 0.0
    val paybackYears get() = if (saving > 0) cost / saving else Double.POSITIVE_INFINITY
}

data class OverallRow(val pair: Pair3, val own: GasRow?, val cheapest: GasRow?, val total: Double)

/** One column of the Compare tab: an import + export pair with a gas tariff (null = no gas data). */
data class CompareOption(val pair: Pair3, val gas: GasRow?) {
    val total get() = pair.net + (gas?.cost ?: 0.0)
}

/** The projected year from the latest analysis run, saved for the Tariffs tab. */
data class TariffProfile(
    val savedAt: String,
    val house: List<Double>,              // kWh per half-hour slot over a year
    val ev: List<Double>,
    val export: List<Double>,
    val monthlyHouse: Map<Int, Double>,   // average month, kWh (months seen only)
    val monthlyExport: Map<Int, Double>,
    val monthlyEv: Map<Int, Double>? = null, // average month, kWh (null in profiles saved before it)
    // kWh per calendar month (0-11) x local half-hour for an average such month (each sums to the
    // annual per-slot list); null in profiles saved before they existed.
    val monthSlotHouse: List<List<Double>>? = null,
    val monthSlotEv: List<List<Double>>? = null,
    val monthSlotExport: List<List<Double>>? = null,
    // Where the PV goes (kWh per calendar month x half-hour) and what the panels cost, for the
    // solar ROI at each tariff's rates; null in profiles saved before them.
    val monthSlotSolarHouse: List<List<Double>>? = null,
    val monthSlotSolarEv: List<List<Double>>? = null,
    val solarCost: Double? = null,
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
    private val DAYS_IN_MONTH = intArrayOf(31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

    fun gasOffer(supplier: String, name: String, code: String, source: String, kind: String,
                 rate: Double?, standing: Double?, note: String = ""): Tariff? =
        if (rate == null || standing == null) null
        else Tariff(supplier, name, code, source, kind, emptyList(), 0.0, note = note, direction = "GAS",
            gasRate = rate, gasStanding = standing)

    fun annualGas(daily: Map<String, Double>): GasYear {
        val byMonth = daily.entries.groupBy({ it.key.substring(5, 7).toInt() - 1 }, { it.value })
        val monthly = byMonth.mapValues { (m, v) -> v.average() * DAYS_IN_MONTH[m] }
        val seasonal = monthly.size == 12
        val days = daily.size
        val kwh = if (seasonal) monthly.values.sum() else if (days > 0) 365 * daily.values.sum() / days else 0.0
        return GasYear(kwh, monthly, seasonal, days)
    }

    fun priceGas(g: Tariff, kwh: Double) = (kwh * (g.gasRate ?: 0.0) + 365 * (g.gasStanding ?: 0.0)) / 100

    fun compareGas(tariffs: List<Tariff>, current: Tariff?, kwh: Double): List<GasRow> {
        val base = current?.let { priceGas(it, kwh) }
        return (listOfNotNull(current) + tariffs.filter { it.direction == "GAS" }).map {
            val c = priceGas(it, kwh)
            GasRow(it, c, if (base != null) c - base else 0.0)
        }.sortedBy { it.cost }
    }

    fun overall(pairs: List<Pair3>, gasRows: List<GasRow>, kwh: Double): List<OverallRow> {
        val market = gasRows.filterNot { it.isCurrent }
        val cheapest = market.firstOrNull()
        val current = gasRows.firstOrNull { it.isCurrent }
        return pairs.mapNotNull { p ->
            val own = when {
                p.imp.isCurrent -> current
                p.imp.tariff.gas != null -> GasRow(p.imp.tariff.gas, priceGas(p.imp.tariff.gas, kwh), 0.0)
                else -> market.firstOrNull { it.tariff.supplier == p.imp.tariff.supplier }
            }
            val any = if (p.imp.isCurrent) null else cheapest
            val best = listOfNotNull(own, any).minOfOrNull { it.cost } ?: return@mapNotNull null
            OverallRow(p, own, any, p.net + best)
        }.sortedBy { it.total }
    }

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

    /** One stretch of the year priced at one set of 48 slot rates: the whole year (a tariff's
     *  slotRates), or a calendar month (a dynamic tariff's [Tariff.monthSlotRates] against the
     *  profile's kWh for an average such month). */
    private class Segment(val house: List<Double>, val ev: List<Double>, val rates: List<Double>)

    private fun segments(t: Tariff, house: List<Double>, ev: List<Double>,
                         monthHouse: List<List<Double>>?, monthEv: List<List<Double>>?): List<Segment> {
        val ms = t.monthSlotRates
        return if (ms != null && ms.size == 12 && monthHouse != null && monthEv != null)
            (0 until 12).map { Segment(monthHouse[it], monthEv[it], ms[it]) }
        else listOf(Segment(house, ev, t.slotRates))
    }

    private fun price(t: Tariff, house: List<Double>, ev: List<Double>, currentOff: Set<Int>,
                      monthHouse: List<List<Double>>? = null, monthEv: List<List<Double>>? = null): Price {
        val standing = 365 * t.standing / 100
        var houseCost = 0.0; var evNow = 0.0; var evBest = 0.0
        var offKwh = 0.0; var offCost = 0.0; var peakKwh = 0.0; var peakCost = 0.0
        for (seg in segments(t, house, ev, monthHouse, monthEv)) {
            val rates = seg.rates
            val cheapest = rates.min()
            houseCost += seg.house.indices.sumOf { seg.house[it] * rates[it] } / 100
            evNow += seg.ev.indices.sumOf { seg.ev[it] * rates[it] } / 100
            val evOffRate = if (t.smartEv) minOf(t.evRate ?: cheapest, cheapest) else cheapest
            // Projection rule: all EV charging is scheduled into the overnight (cheapest) rate, or
            // the smart-EV rate on smart-charging tariffs. ([currentOff] is kept for callers.)
            for (k in seg.ev.indices) {
                evBest += seg.ev[k] * evOffRate / 100
                offKwh += seg.ev[k]; offCost += seg.ev[k] * evOffRate
            }
        }
        val evRetimed = minOf(evNow, evBest)
        val evSplit = EvSplit(offKwh, if (offKwh > 0) offCost / offKwh else 0.0, peakKwh,
            if (peakKwh > 0) peakCost / peakKwh else 0.0)
        val rates = t.slotRates
        val cheapest = rates.min()
        val cheapSlots = (0 until 48).filter { rates[it] <= cheapest + 0.005 }
        val evKwh = ev.sum()
        return Price(houseCost, evNow, evRetimed, if (evKwh > 0) 100 * evRetimed / evKwh else 0.0,
            standing, houseCost + evNow + standing, houseCost + evRetimed + standing,
            if (cheapSlots.size < 48) cheapSlots.sumOf { house[it] + ev[it] } else 0.0, evSplit)
    }

    fun pairKey(p: Pair3) = "${p.imp.tariff.code}|${p.exp.tariff.code}"

    fun gasKey(t: Tariff) = "${t.supplier}|${t.name}|${t.code}"

    /**
     * The two columns of the Compare tab from what's ticked: up to two import + export pairs
     * ([pairKeys]) and up to two gas tariffs ([gasKeys]), combined as
     *  - 2 pairs: each pair with the matching ticked gas (2 gases), the one ticked gas (1), or its
     *    own Overall gas (the cheaper of its supplier's / the cheapest anywhere; current gas for the current pair);
     *  - 1 pair + 1 or 2 gases: that pair with each gas, or, with one gas, against today's setup;
     *  - gases only: today's electricity with each gas, or with one gas, against today's setup.
     * Fewer than two columns means more needs ticking.
     */
    fun compareOptions(allPairs: List<Pair3>, overall: List<OverallRow>, gasRows: List<GasRow>,
                       pairKeys: List<String>, gasKeys: List<String>): List<CompareOption> {
        val pairs = pairKeys.mapNotNull { k -> allPairs.firstOrNull { pairKey(it) == k } }
        val gases = gasKeys.mapNotNull { k -> gasRows.firstOrNull { gasKey(it.tariff) == k } }
        fun defaultGas(p: Pair3): GasRow? = overall.firstOrNull { pairKey(it.pair) == pairKey(p) }
            ?.let { o -> listOfNotNull(o.own, o.cheapest).minByOrNull { it.cost } }
        val currentPair = allPairs.firstOrNull { it.isCurrent }
        val current = currentPair?.let { CompareOption(it, gasRows.firstOrNull { g -> g.isCurrent }) }
        return when (pairs.size) {
            2 -> pairs.mapIndexed { i, p ->
                CompareOption(p, when (gases.size) { 2 -> gases[i]; 1 -> gases[0]; else -> defaultGas(p) })
            }
            1 -> when (gases.size) {
                2 -> gases.map { CompareOption(pairs[0], it) }
                1 -> listOfNotNull(current, CompareOption(pairs[0], gases[0]))
                else -> emptyList()
            }
            else -> when {
                currentPair == null -> emptyList()
                gases.size == 2 -> gases.map { CompareOption(currentPair, it) }
                gases.size == 1 -> listOfNotNull(current, CompareOption(currentPair, gases[0]))
                else -> emptyList()
            }
        }.takeIf { it.size == 2 } ?: emptyList()
    }

    /** The panels' yearly value at [pair]'s rates, or null when the profile has no PV split. */
    fun solarRoi(pair: Pair3, profile: TariffProfile): SolarRoi? {
        val sh = profile.monthSlotSolarHouse ?: return null
        val se = profile.monthSlotSolarEv ?: return null
        val t = pair.imp.tariff
        val ms = t.monthSlotRates?.takeIf { it.size == 12 }
        var selfUse = 0.0; var ev = 0.0
        for (m in 0 until 12) {
            val rates = ms?.get(m) ?: t.slotRates
            val cheapest = rates.min()
            val overnight = if (t.smartEv) minOf(t.evRate ?: cheapest, cheapest) else cheapest
            for (k in 0 until 48) { selfUse += sh[m][k] * rates[k] / 100; ev += se[m][k] * overnight / 100 }
        }
        return SolarRoi(sh.sumOf { it.sum() }, selfUse, se.sumOf { it.sum() }, ev,
            profile.export.sum(), pair.exp.income, profile.solarCost ?: 0.0)
    }

    fun priceExport(t: Tariff, export: List<Double>, monthExport: List<List<Double>>? = null): Double {
        val ms = t.monthSlotRates
        if (ms != null && ms.size == 12 && monthExport != null)
            return (0 until 12).sumOf { m -> monthExport[m].indices.sumOf { monthExport[m][it] * ms[m][it] } } / 100
        return export.indices.sumOf { export[it] * t.slotRates[it] } / 100
    }

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
    fun compare(tariffs: List<Tariff>, current: Tariff, house: List<Double>, ev: List<Double>,
                monthHouse: List<List<Double>>? = null, monthEv: List<List<Double>>? = null): List<ImportRow> {
        val off = windowSlots(current.window)
        val base = price(current, house, ev, off, monthHouse, monthEv).total
        return (listOf(current) + tariffs.filter { it.direction == "IMPORT" }).map { t ->
            val p = price(t, house, ev, off, monthHouse, monthEv)
            val (kind, text) = describeRates(t.slotRates)
            ImportRow(t, p.houseCost, p.evNow, p.evRetimed, p.evPPerKwh, p.standingYear, p.asNow, p.total,
                p.cheapKwh, kind, text, p.total - base, p.evSplit)
        }.sortedBy { it.total }
    }

    /** Priced export rows, highest income first. */
    fun compareExports(tariffs: List<Tariff>, current: Tariff, export: List<Double>,
                       monthExport: List<List<Double>>? = null): List<ExportRow> {
        val base = priceExport(current, export, monthExport)
        return (listOf(current) + tariffs.filter { it.direction == "EXPORT" }).map { t ->
            val income = priceExport(t, export, monthExport)
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
    fun pairBreakdown(pair: Pair3, house: List<Double>, ev: List<Double>, export: List<Double>,
                      monthHouse: List<List<Double>>? = null, monthEv: List<List<Double>>? = null): PairBreakdown {
        var offKwh = 0.0; var peakKwh = 0.0; var offCost = 0.0; var peakCost = 0.0
        for (seg in segments(pair.imp.tariff, house, ev, monthHouse, monthEv)) {
            val rates = seg.rates
            val cheapest = rates.min()
            val tou = rates.map { Math.round(it * 100) }.toSet().size > 1
            for (k in 0 until 48) {
                if (tou && rates[k] <= cheapest + 0.005) { offKwh += seg.house[k]; offCost += seg.house[k] * rates[k] / 100 }
                else { peakKwh += seg.house[k]; peakCost += seg.house[k] * rates[k] / 100 }
            }
        }
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
