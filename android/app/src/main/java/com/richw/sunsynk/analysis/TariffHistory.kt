package com.richw.sunsynk.analysis

import com.google.gson.JsonObject
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * Port of Python analysis/tariffhistory.py: every day of real half-hourly import/export priced
 * at the rates each tariff actually charged on that date (Octopus Kraken rate history), against
 * the actual energyPrices tariff. See the Python module docstring for the rules.
 */

/** One Octopus variable product whose rate history is compared. */
data class HistorySpec(val name: String, val product: String, val direction: String = "IMPORT",
                       val smartEv: Boolean = false, val requires: String? = null)

/** Kraken rate periods for lookup by instant: Direct Debit / unspecified rows only, the
 *  latest-starting covering period wins. */
class RateTimeline(rows: List<JsonObject>) {
    private val periods: List<Triple<Instant, Instant, Double>>
    private val starts: List<Instant>

    init {
        fun str(o: JsonObject, k: String) = o.get(k)?.takeUnless { it.isJsonNull }?.asString
        periods = rows.filter { str(it, "payment_method") in listOf(null, "DIRECT_DEBIT") }.map {
            Triple(str(it, "valid_from")?.let(Instant::parse) ?: Instant.MIN,
                str(it, "valid_to")?.let(Instant::parse) ?: Instant.MAX,
                it.get("value_inc_vat").asDouble)
        }.sortedBy { it.first }
        starts = periods.map { it.first }
    }

    val size get() = periods.size

    fun at(t: Instant): Double? {
        var i = starts.binarySearch(t).let { if (it >= 0) it else -it - 2 }
        // binarySearch hits any equal start; step to the last period starting at or before t
        while (i + 1 < starts.size && !starts[i + 1].isAfter(t)) i++
        while (i >= 0) {
            val (a, b, v) = periods[i]
            if (!t.isBefore(a) && t.isBefore(b)) return v
            i--
        }
        return null
    }
}

data class HistTariff(
    val name: String, val code: String, val direction: String, val smartEv: Boolean,
    val requires: String?, val unit: RateTimeline, val standing: RateTimeline,
    val supplier: String = "Octopus",
)

/** One energyPrices period, rates in p (standing p/day). */
data class ActualPeriod(val dateFrom: String, val dateTo: String, val offPeak: Double, val peak: Double,
                        val window: String, val export: Double, val standing: Double)

/** The analysis run's days for the Tariffs tab's history view: per date 144 Wh values
 *  (house import 0-47, EV import 48-95, export 96-143, local half-hours). */
data class HistoryDays(val savedAt: String, val periods: List<ActualPeriod>, val days: Map<String, IntArray>)

class ImportAcc {
    var house = 0.0; var evNow = 0.0; var ev = 0.0; var standing = 0.0; var kwh = 0.0; var days = 0
    val total get() = house + ev + standing
    fun add(o: ImportAcc) { house += o.house; evNow += o.evNow; ev += o.ev; standing += o.standing; kwh += o.kwh; days += o.days }
}

class ExportAcc {
    var income = 0.0; var kwh = 0.0; var days = 0
    val rate get() = if (kwh > 0) 100 * income / kwh else 0.0
    fun add(o: ExportAcc) { income += o.income; kwh += o.kwh; days += o.days }
}

class KwhAcc { var house = 0.0; var ev = 0.0; var export = 0.0; var days = 0 }

data class HistoryResult(
    val imports: Map<String, Map<String, ImportAcc>>,   // tariff -> month -> £
    val exports: Map<String, Map<String, ExportAcc>>,
    val kwh: Map<String, KwhAcc>,                        // month -> kWh
    val skipped: Map<String, Int>,
    val suppliers: Map<String, String>,
    val requires: Map<String, String?>,
) { val months get() = kwh.keys.sorted() }

data class HistPair(val net: Double, val imp: String, val exp: String, val importTotal: Double, val income: Double) {
    val isActual get() = imp == TariffHistory.ACTUAL && exp == TariffHistory.ACTUAL_EXPORT
}

object TariffHistory {
    const val ACTUAL = "Actual"
    const val ACTUAL_EXPORT = "Actual export"
    const val DEFAULT_CHARGER_KW = 7.4
    private val LOCAL: ZoneId = ZoneId.of("Europe/London")
    val NIGHT_SLOTS = (44 until 48).toList() + (0 until 14).toList()   // 22:00-07:00

    val TARIFFS = listOf(
        HistorySpec("Agile Octopus", "AGILE-24-10-01"),
        HistorySpec("Intelligent Octopus Go", "INTELLI-VAR-24-10-29", smartEv = true),
        HistorySpec("Octopus Go", "GO-VAR-22-10-14"),
        HistorySpec("Flexible Octopus (~price cap)", "VAR-22-11-01"),
        HistorySpec("Agile Outgoing", "AGILE-OUTGOING-19-05-13", "EXPORT", requires = "Octopus"),
        HistorySpec("Outgoing Octopus", "OUTGOING-VAR-24-10-26", "EXPORT", requires = "Octopus"),
    )

    fun slotInstants(day: LocalDate): List<Instant> =
        List(48) { LocalDateTime.of(day.year, day.month, day.dayOfMonth, it / 2, 30 * (it % 2)).atZone(LOCAL).toInstant() }

    /** 48 p/kWh for a day, gaps filled with the day's mean; null if the day has no rates. */
    fun dayRates(t: RateTimeline, instants: List<Instant>): List<Double>? {
        val rates = instants.map { t.at(it) }
        val present = rates.filterNotNull()
        if (present.isEmpty()) return null
        val mean = present.average()
        return rates.map { it ?: mean }
    }

    fun monthsBetween(start: LocalDate, stop: LocalDate): List<LocalDate> {
        val out = mutableListOf<LocalDate>()
        var m = start.withDayOfMonth(1)
        while (!m.isAfter(stop)) { out.add(m); m = m.plusMonths(1) }
        return out
    }

    fun actualPeriods(pricesJson: JsonObject): List<ActualPeriod> =
        pricesJson.getAsJsonObject("data").getAsJsonArray("prices").map { it.asJsonObject }.map {
            ActualPeriod(it.get("datefrom").asString, it.get("dateto").asString,
                it.get("offpeakRate").asDouble * 100, it.get("peakRate").asDouble * 100,
                "${it.get("offpeakStart").asString}-${it.get("offpeakStop").asString}",
                it.get("exportRate").asDouble * 100, (it.get("standingCharge")?.asDouble ?: 0.0) * 100)
        }

    /** The first period covering [day] (as EnergyPrices.checkDate), or null. */
    fun actualPeriod(periods: List<ActualPeriod>, day: String) =
        periods.firstOrNull { it.dateFrom <= day && day <= it.dateTo }

    /** p for the day's EV kWh with what was charged in [off] moved into the cheapest NIGHT_SLOTS
     *  (at most [slotCapKwh] each); the rest stays at its own rate. */
    fun retimeEv(ev: List<Double>, rates: List<Double>, off: Set<Int>, slotCapKwh: Double): Double {
        var cost = (0 until 48).filter { it !in off }.sumOf { ev[it] * rates[it] }
        var remaining = off.sumOf { ev[it] }
        val night = NIGHT_SLOTS.sortedBy { rates[it] }
        for (k in night) {
            if (remaining <= 0) break
            val take = minOf(slotCapKwh, remaining)
            cost += take * rates[k]
            remaining -= take
        }
        if (remaining > 0) cost += remaining * rates[night.last()]
        return cost
    }

    /** £ for one day's house + EV kWh per slot at 48 rates (p/kWh), as an accumulator. */
    fun priceImportDay(rates: List<Double>, house: List<Double>, ev: List<Double>, off: Set<Int>,
                       smartEv: Boolean, slotCapKwh: Double, actual: Boolean = false): ImportAcc {
        val evNow = ev.indices.sumOf { ev[it] * rates[it] }
        val best = when {
            actual -> evNow
            smartEv -> ev.sum() * rates.min()
            else -> retimeEv(ev, rates, off, slotCapKwh)
        }
        return ImportAcc().apply {
            this.house = house.indices.sumOf { house[it] * rates[it] } / 100
            this.evNow = evNow / 100
            this.ev = minOf(evNow, best) / 100
        }
    }

    /** Price every day at the actual tariff and each history tariff; a day only counts when all
     *  of them have rates for it, so every column covers the same days. */
    fun priceHistory(days: Map<String, IntArray>, periods: List<ActualPeriod>, tariffs: List<HistTariff>,
                     chargerKw: Double = DEFAULT_CHARGER_KW): HistoryResult {
        val cap = chargerKw / 2
        val imports = linkedMapOf<String, MutableMap<String, ImportAcc>>(ACTUAL to mutableMapOf())
        val exports = linkedMapOf<String, MutableMap<String, ExportAcc>>(ACTUAL_EXPORT to mutableMapOf())
        tariffs.forEach { if (it.direction == "EXPORT") exports[it.name] = mutableMapOf() else imports[it.name] = mutableMapOf() }
        val kwh = mutableMapOf<String, KwhAcc>()
        val skipped = mutableMapOf<String, Int>()
        for (day in days.keys.sorted()) {
            val v = days.getValue(day)
            val house = List(48) { v[it] / 1000.0 }
            val ev = List(48) { v[48 + it] / 1000.0 }
            val export = List(48) { v[96 + it] / 1000.0 }
            val act = actualPeriod(periods, day)
            if (act == null) { skipped.merge(ACTUAL, 1, Int::plus); continue }
            val instants = slotInstants(LocalDate.parse(day))
            val dayTariffs = mutableListOf<Triple<HistTariff, List<Double>, Double>>()
            var missing: String? = null
            for (t in tariffs) {
                val rates = dayRates(t.unit, instants)
                val standing = if (t.standing.size > 0) t.standing.at(instants[24]) else 0.0
                if (rates == null || standing == null) { missing = t.name; break }
                dayTariffs.add(Triple(t, rates, standing))
            }
            if (missing != null) { skipped.merge(missing, 1, Int::plus); continue }
            val month = day.substring(0, 7)
            val importKwh = house.sum() + ev.sum()
            val exportKwh = export.sum()
            kwh.getOrPut(month) { KwhAcc() }.apply { this.house += house.sum(); this.ev += ev.sum(); this.export += exportKwh; this.days++ }
            val off = TariffCompare.windowSlots(act.window)
            fun addImport(name: String, a: ImportAcc, standingP: Double) {
                a.standing = standingP / 100; a.kwh = importKwh; a.days = 1
                imports.getValue(name).getOrPut(month) { ImportAcc() }.add(a)
            }
            fun addExport(name: String, income: Double) {
                exports.getValue(name).getOrPut(month) { ExportAcc() }.add(ExportAcc().apply { this.income = income; this.kwh = exportKwh; this.days = 1 })
            }
            addImport(ACTUAL, priceImportDay(TariffCompare.touSlotRates(act.peak, act.offPeak, act.window),
                house, ev, off, false, cap, actual = true), act.standing)
            addExport(ACTUAL_EXPORT, exportKwh * act.export / 100)
            for ((t, rates, standing) in dayTariffs) {
                if (t.direction == "EXPORT") addExport(t.name, export.indices.sumOf { export[it] * rates[it] } / 100)
                else addImport(t.name, priceImportDay(rates, house, ev, off, t.smartEv, cap), standing)
            }
        }
        return HistoryResult(imports, exports, kwh, skipped,
            mapOf(ACTUAL to ACTUAL, ACTUAL_EXPORT to ACTUAL) + tariffs.associate { it.name to it.supplier },
            mapOf<String, String?>(ACTUAL to null, ACTUAL_EXPORT to null) + tariffs.associate { it.name to it.requires })
    }

    fun importTotals(byMonth: Map<String, ImportAcc>, months: List<String>) =
        ImportAcc().also { acc -> months.forEach { m -> byMonth[m]?.let(acc::add) } }

    fun exportTotals(byMonth: Map<String, ExportAcc>, months: List<String>) =
        ExportAcc().also { acc -> months.forEach { m -> byMonth[m]?.let(acc::add) } }

    /** Import + export pairs, cheapest net first: an export that requires a supplier only with
     *  that supplier's import, the actual import only with the actual export. */
    fun bestPairs(r: HistoryResult, months: List<String>): List<HistPair> {
        val out = mutableListOf<HistPair>()
        for ((imp, byMonth) in r.imports) {
            val total = importTotals(byMonth, months).total
            for ((exp, exMonth) in r.exports) {
                val req = r.requires[exp]
                if (req != null && req != r.suppliers[imp]) continue
                if (imp == ACTUAL && exp != ACTUAL_EXPORT) continue
                val income = exportTotals(exMonth, months).income
                out.add(HistPair(total - income, imp, exp, total, income))
            }
        }
        return out.sortedBy { it.net }
    }

    /** The newest 12 months with data, dropping a partial current month. */
    fun last12Months(months: List<String>, today: LocalDate = LocalDate.now()): List<String> {
        val current = today.toString().substring(0, 7)
        return months.filter { it < current }.takeLast(12)
    }
}
