package com.richw.sunsynk.analysis

import com.google.gson.JsonParser
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/** Settings for the EV-vs-petrol comparison (config.json "evSavings"). */
data class EvSavingsConfig(
    val mpg: Double = 50.0,                 // the petrol car it replaces (UK gallons)
    val milesPerKwh: Double = 3.9,          // the EVs, as the car reports it (battery side)
    val chargeEfficiency: Double = 0.9,     // AC charging: kWh into the battery per kWh at the charger
    /** Charging the charger never saw (e.g. before it was fitted), from bills: one entry per month. */
    val beforeCharger: List<ExtraCharge> = emptyList(),
)

data class ExtraCharge(val month: String, val kwh: Double, val rate: Double)   // rate p/kWh inc VAT

/** One day of home charging: kWh at the charger and what it cost (£, incl. export given up). */
data class EvDay(val date: String, val kwh: Double, val cost: Double)

data class EvMonth(
    val month: String, val kwh: Double, val miles: Double, val evCost: Double,
    val litres: Double, val petrolCost: Double, val localDays: Int, val days: Int,
) {
    val saved get() = petrolCost - evCost
    val pencePerLitre get() = if (litres > 0) petrolCost * 100 / litres else 0.0
}

data class PetrolPrice(val date: String, val pence: Double, val source: String)

data class EvSavingsResult(
    val months: List<EvMonth>,
    val offset: Double?,            // local supermarkets minus the national average, p/litre
    val offsetWeeks: Int,
    val latest: PetrolPrice?,
) {
    private fun sum(ms: List<EvMonth>) = EvMonth("", ms.sumOf { it.kwh }, ms.sumOf { it.miles }, ms.sumOf { it.evCost },
        ms.sumOf { it.litres }, ms.sumOf { it.petrolCost }, ms.sumOf { it.localDays }, ms.sumOf { it.days })
    val total get() = sum(months)
    /** The last 12 complete months before [today]'s month. */
    fun last12(today: LocalDate = LocalDate.now()): EvMonth {
        val end = today.withDayOfMonth(1)
        val from = end.minusMonths(12).toString().substring(0, 7)
        return sum(months.filter { it.month >= from && it.month < end.toString().substring(0, 7) })
    }
}

/**
 * Home EV charging vs a petrol car doing the same miles. Miles = charger kWh × charging
 * efficiency × miles/kWh; petrol = miles / mpg × 4.54609 litres at the day's local supermarket
 * price: the mean of the configured stations' E10 prices (each held from its last change) on
 * days at least [minStations] of them have a price, else the national weekly average (DESNZ)
 * plus the mean local − national gap over the weeks both exist.
 */
object EvSavings {
    const val LITRES_PER_GALLON = 4.54609
    val ZONE: ZoneId = ZoneId.of("Europe/London")

    /** Each saved analysis day's charging: EV import half-hours at the period's off-peak (inside
     *  its window) or peak rate, plus solar into the car at the export rate (the export given
     *  up), as the analysis's EV cost. Charger days before the first analysis day (from Indra's
     *  daily totals) are priced at the first period's off-peak rate. */
    fun evDays(history: HistoryDays, indraDaily: Map<String, Double> = emptyMap()): List<EvDay> {
        val out = mutableListOf<EvDay>()
        for ((date, v) in history.days) {
            val p = history.periods.firstOrNull { date >= it.dateFrom && date <= it.dateTo } ?: continue
            val (a, b) = windowSlots(p.window)
            var kwh = 0.0
            var cost = 0.0
            for (k in 0 until 48) {
                val ev = v[48 + k] / 1000.0
                kwh += ev
                cost += ev * (if (inWindow(k, a, b)) p.offPeak else p.peak) / 100
            }
            if (v.size >= 240) {
                val solar = (192 until 240).sumOf { v[it] } / 1000.0
                kwh += solar
                cost += solar * p.export / 100
            }
            if (kwh > 0) out.add(EvDay(date, kwh, cost))
        }
        val first = history.days.keys.minOrNull()
        val firstPeriod = history.periods.minByOrNull { it.dateFrom }
        if (first != null && firstPeriod != null) {
            for ((date, kwh) in indraDaily) if (date < first && kwh > 0)
                out.add(EvDay(date, kwh, kwh * firstPeriod.offPeak / 100))
        }
        return out.sortedBy { it.date }
    }

    private fun windowSlots(window: String): Pair<Int, Int> {
        fun slot(t: String) = t.substring(0, 2).toInt() * 2 + (if (t.substring(3, 5).toInt() >= 30) 1 else 0)
        val (a, b) = window.split("-")
        return Pair(slot(a), slot(b))
    }

    private fun inWindow(k: Int, a: Int, b: Int) = if (a <= b) k in a until b else (k >= a || k < b)

    /** Indra's daily kWh from the cached monthly reports (indraData/month-YYYY-MM.json). */
    fun indraDailyKwh(dir: File): Map<String, Double> {
        val out = sortedMapOf<String, Double>()
        val files = dir.listFiles()?.filter { Regex("month-\\d{4}-\\d{2}\\.json").matches(it.name) } ?: return out
        for (f in files) {
            val reports = runCatching { JsonParser.parseString(f.readText()).asJsonObject.getAsJsonArray("dailyReports") }
                .getOrNull() ?: continue
            for (r in reports) {
                val o = r.asJsonObject
                val date = o.get("date")?.asString?.take(10) ?: continue
                out[date] = o.getAsJsonObject("totals")?.get("energyImportedKwh")?.asDouble ?: 0.0
            }
        }
        return out
    }

    /** {date: (stations priced, mean p/litre)} from each station's price changes, each held
     *  from its change until the next, for [from]..[to] (local dates). */
    fun localDaily(stations: Map<String, List<Pair<Instant, Double>>>, from: LocalDate, to: LocalDate,
                   zone: ZoneId = ZONE): Map<String, Pair<Int, Double>> {
        val byDay = sortedMapOf<String, MutableList<Double>>()
        for (changes in stations.values) {
            val sorted = changes.filter { it.second in 100.0..250.0 }.sortedBy { it.first }
            var i = 0
            var cur: Double? = null
            var d = from
            while (!d.isAfter(to)) {
                while (i < sorted.size && !sorted[i].first.atZone(zone).toLocalDate().isAfter(d)) cur = sorted[i++].second
                cur?.let { byDay.getOrPut(d.toString()) { mutableListOf() }.add(it) }
                d = d.plusDays(1)
            }
        }
        return byDay.mapValues { (_, v) -> Pair(v.size, v.average()) }
    }

    /** DESNZ weekly road fuel CSV → [(week date, ULSP pump p/litre)], oldest first. */
    fun parseNational(csv: String): List<Pair<LocalDate, Double>> {
        val fmt = java.time.format.DateTimeFormatter.ofPattern("dd/MM/yyyy")
        return csv.lineSequence().drop(1).mapNotNull { line ->
            val cols = line.trim().trimStart('﻿').split(",")
            if (cols.size < 2) return@mapNotNull null
            val date = runCatching { LocalDate.parse(cols[0].trim(), fmt) }.getOrNull() ?: return@mapNotNull null
            val p = cols[1].trim().toDoubleOrNull() ?: return@mapNotNull null
            Pair(date, p)
        }.sortedBy { it.first }.toList()
    }

    /** Mean (local − national) over national weeks with a well-covered local price. */
    fun offset(national: List<Pair<LocalDate, Double>>, local: Map<String, Pair<Int, Double>>,
               minStations: Int): Pair<Double, Int>? {
        val diffs = national.mapNotNull { (d, p) -> local[d.toString()]?.takeIf { it.first >= minStations }?.let { it.second - p } }
        return if (diffs.isEmpty()) null else Pair(diffs.average(), diffs.size)
    }

    fun minStations(stationCount: Int) = maxOf(1, (2 * stationCount + 2) / 3)

    fun petrolPrice(date: String, national: List<Pair<LocalDate, Double>>, local: Map<String, Pair<Int, Double>>,
                    offset: Double?, minStations: Int): PetrolPrice? {
        local[date]?.takeIf { it.first >= minStations }?.let { return PetrolPrice(date, it.second, "local") }
        val d = LocalDate.parse(date)
        val week = national.lastOrNull { !it.first.isAfter(d) } ?: return null
        return PetrolPrice(date, week.second + (offset ?: 0.0), if (offset != null) "national-adjusted" else "national")
    }

    fun calculate(days: List<EvDay>, cfg: EvSavingsConfig, national: List<Pair<LocalDate, Double>>,
                  local: Map<String, Pair<Int, Double>>, stationCount: Int,
                  today: LocalDate = LocalDate.now()): EvSavingsResult {
        val min = minStations(stationCount)
        val off = offset(national, local, min)
        val all = days + cfg.beforeCharger.map { EvDay("${it.month}-15", it.kwh, it.kwh * it.rate / 100) }
        val months = sortedMapOf<String, MutableList<Pair<EvDay, PetrolPrice>>>()
        for (d in all) {
            val p = petrolPrice(d.date, national, local, off?.first, min) ?: continue
            months.getOrPut(d.date.substring(0, 7)) { mutableListOf() }.add(Pair(d, p))
        }
        val rows = months.map { (m, list) ->
            var miles = 0.0; var litres = 0.0; var petrol = 0.0
            for ((d, p) in list) {
                val mi = d.kwh * cfg.chargeEfficiency * cfg.milesPerKwh
                val l = mi / cfg.mpg * LITRES_PER_GALLON
                miles += mi; litres += l; petrol += l * p.pence / 100
            }
            EvMonth(m, list.sumOf { it.first.kwh }, miles, list.sumOf { it.first.cost }, litres, petrol,
                list.count { it.second.source == "local" }, list.size)
        }
        val latest = petrolPrice(today.toString(), national, local, off?.first, min)
            ?: local.entries.lastOrNull { it.value.first >= min }?.let { PetrolPrice(it.key, it.value.second, "local") }
        return EvSavingsResult(rows, off?.first, off?.second ?: 0, latest)
    }
}
