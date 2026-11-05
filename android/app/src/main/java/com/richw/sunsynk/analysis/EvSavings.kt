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
    /** One-off EV credits (e.g. a supplier's free-miles bonus), taken off that month's charging cost. */
    val credits: List<EvCredit> = emptyList(),
    /** £ per € for public charging receipts in euros. */
    val eurToGbp: Double = 0.85,
    /** Public charging's own comparison (as ElectroFind's Trip tab): the petrol car's mpg (null =
     *  [mpg]) and a fixed fuel price per litre in [publicFuelCurrency] (null = local supermarket prices). */
    val publicMpg: Double? = null,
    /** Public charging miles per kWh at the charger (ElectroFind's evMilesPerKwh; null = milesPerKwh × chargeEfficiency). */
    val publicMilesPerKwh: Double? = null,
    val publicFuelPrice: Double? = null,
    val publicFuelCurrency: String = "GBP",
    /** Public charges without an Electroverse receipt (other networks), added by hand or from ElectroFind. */
    val customCharges: List<CustomCharge> = emptyList(),
    /** Money repaid for EV miles (e.g. per-mile expenses): covers public charging; the rest is profit. */
    val reimbursements: List<Reimbursement> = emptyList(),
    /** Edits laid over downloaded Electroverse receipts (by receipt pk), so they survive a re-download. */
    val receiptEdits: List<ReceiptEdit> = emptyList(),
)

/** Changes to one Electroverse receipt: null fields keep the receipt's own value. */
data class ReceiptEdit(val pk: Int, val date: String? = null, val name: String? = null, val kwh: Double? = null,
                       val cost: Double? = null, val currency: String? = null, val excluded: Boolean = false,
                       override val mpg: Double? = null, override val milesPerKwh: Double? = null,
                       override val fuelPrice: Double? = null, override val fuelCurrency: String? = null,
                       override val fuelNote: String? = null) : ChargeComparison

/** One public charge for the editable list: an Electroverse receipt (with any edit applied, [pk]
 *  set) or a custom charge ([customId] set). Cost in [currency]. */
data class PublicRow(val pk: Int?, val customId: String?, val date: String, val name: String, val kwh: Double,
                     val cost: Double, val currency: String, val excluded: Boolean, val edited: Boolean = false,
                     override val mpg: Double? = null, override val milesPerKwh: Double? = null,
                     override val fuelPrice: Double? = null, override val fuelCurrency: String? = null,
                     override val fuelNote: String? = null) : ChargeComparison {
    val source get() = if (pk != null) "Electroverse" else "Other"
}

/** A public charge with no Electroverse receipt; cost in [currency] ("GBP"/"EUR"), idle fees included. */
data class CustomCharge(val id: String, val date: String, val name: String, val kwh: Double, val cost: Double,
                        val currency: String = "GBP", val excluded: Boolean = false,
                        override val mpg: Double? = null, override val milesPerKwh: Double? = null,
                        override val fuelPrice: Double? = null, override val fuelCurrency: String? = null,
                        override val fuelNote: String? = null) : ChargeComparison

/** One charge's own comparison: petrol mpg, EV miles per kWh at the charger, and fuel price per litre
 *  in [fuelCurrency] (with [fuelNote] saying where it came from); null = the public defaults. */
interface ChargeComparison {
    val mpg: Double?; val milesPerKwh: Double?; val fuelPrice: Double?; val fuelCurrency: String?; val fuelNote: String?
}

/** ElectroFind Trip tab settings: £ per €, petrol mpg, petrol £ per litre. */
data class ElectroFindSettings(val eurToGbp: Double?, val mpg: Double?, val fuelPrice: Double?, val milesPerKwh: Double? = null)

/** Money repaid for EV miles, £; with [miles] and [ratePence] when entered as a mileage claim (amount = miles × rate). */
data class Reimbursement(val id: String, val date: String, val amount: Double, val note: String = "",
                         val miles: Double? = null, val ratePence: Double? = null)

data class ExtraCharge(val month: String, val kwh: Double, val rate: Double)   // rate p/kWh inc VAT

data class EvCredit(val month: String, val amount: Double, val note: String = "")   // £ inc VAT

/** One day of home charging: kWh at the charger and what it cost (£, incl. export given up). */
data class EvDay(val date: String, val kwh: Double, val cost: Double, val public: Boolean = false,
                 val mpg: Double? = null, val milesPerKwh: Double? = null, val pence: Double? = null)   // per-charge overrides

data class EvMonth(
    val month: String, val kwh: Double, val miles: Double, val evCost: Double,
    val litres: Double, val petrolCost: Double, val localDays: Int, val days: Int,
    val credit: Double = 0.0,        // one-off credits this month, £ (already taken off netCost)
    val publicKwh: Double = 0.0, val publicMiles: Double = 0.0,   // the public-charging share of the above
    val publicCost: Double = 0.0, val publicPetrolCost: Double = 0.0,
    val reimbursed: Double = 0.0,    // repaid for EV miles this month, £ (taken off netCost)
) {
    val netCost get() = evCost - credit - reimbursed
    val saved get() = petrolCost - netCost
    val pencePerLitre get() = if (litres > 0) petrolCost * 100 / litres else 0.0
}

data class PetrolPrice(val date: String, val pence: Double, val source: String)

/** A repayment as counted: [petrolCost] = its miles at [pence]/litre and [mpg] (from a public charge
 *  that day, else home); [adjustment] = what it adds to the total (amount − petrolCost, or the
 *  whole amount when no miles were given). */
data class RepaymentCalc(val repayment: Reimbursement, val petrolCost: Double?, val pence: Double?, val mpg: Double?,
                         val priceFrom: String, val adjustment: Double)

data class EvSavingsResult(
    val months: List<EvMonth>,
    val offset: Double?,            // local supermarkets minus the national average, p/litre
    val offsetWeeks: Int,
    val latest: PetrolPrice?,
    /** How each repayment was counted (see [EvSavings.calculate]). */
    val repayments: List<RepaymentCalc> = emptyList(),
) {
    private fun sum(ms: List<EvMonth>) = EvMonth("", ms.sumOf { it.kwh }, ms.sumOf { it.miles }, ms.sumOf { it.evCost },
        ms.sumOf { it.litres }, ms.sumOf { it.petrolCost }, ms.sumOf { it.localDays }, ms.sumOf { it.days },
        ms.sumOf { it.credit }, ms.sumOf { it.publicKwh }, ms.sumOf { it.publicMiles }, ms.sumOf { it.publicCost },
        ms.sumOf { it.publicPetrolCost }, ms.sumOf { it.reimbursed })
    val total get() = sum(months)
    /** Repayments that covered public charging, and the excess over it (profit), over [ms]. */
    fun repaid(ms: List<EvMonth> = months): Pair<Double, Double> {
        val r = ms.sumOf { it.reimbursed }; val c = ms.sumOf { it.publicCost }
        return Pair(minOf(r, c), maxOf(0.0, r - c))
    }
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

    /** Public charging receipts as EV days (local start date, £: GBP as billed, EUR at [eurToGbp];
     *  other currencies are left out). */
    fun publicDays(charges: List<com.richw.sunsynk.api.PublicCharge>, eurToGbp: Double,
                   custom: List<CustomCharge> = emptyList(), edits: List<ReceiptEdit> = emptyList()): List<EvDay> {
        fun gbp(v: Double, cur: String) = when (cur) { "GBP" -> v; "EUR" -> v * eurToGbp; else -> null }
        return publicRows(charges, custom, edits).filter { !it.excluded && it.kwh > 0 }.mapNotNull { r ->
            gbp(r.cost, r.currency)?.let {
                EvDay(r.date, r.kwh, it, public = true, mpg = r.mpg, milesPerKwh = r.milesPerKwh,
                    pence = r.fuelPrice?.let { p -> p * 100 * (if (r.fuelCurrency == "EUR") eurToGbp else 1.0) })
            }
        }
    }

    /** Every public charge, receipts (edits applied) and custom, newest first. */
    fun publicRows(charges: List<com.richw.sunsynk.api.PublicCharge>, custom: List<CustomCharge>,
                   edits: List<ReceiptEdit>): List<PublicRow> {
        val byPk = edits.associateBy { it.pk }
        val receipts = charges.map { c ->
            val e = byPk[c.pk]
            PublicRow(c.pk, null, e?.date ?: c.start.atZone(ZONE).toLocalDate().toString(),
                e?.name ?: listOf(c.operator, c.address).filter { it.isNotBlank() }.joinToString(", "),
                e?.kwh ?: c.kwh, e?.cost ?: c.gross, e?.currency ?: c.currency, e?.excluded ?: false, edited = e != null,
                mpg = e?.mpg, milesPerKwh = e?.milesPerKwh, fuelPrice = e?.fuelPrice, fuelCurrency = e?.fuelCurrency,
                fuelNote = e?.fuelNote)
        }
        val others = custom.map { PublicRow(null, it.id, it.date, it.name, it.kwh, it.cost, it.currency, it.excluded,
            mpg = it.mpg, milesPerKwh = it.milesPerKwh, fuelPrice = it.fuelPrice, fuelCurrency = it.fuelCurrency,
            fuelNote = it.fuelNote) }
        return (receipts + others).sortedByDescending { it.date }
    }

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

    /** Estimated petrol cost (£) of one public charge's miles: kWh × mi/kWh, at its mpg and fuel price
     *  (per-charge, else the public defaults, else [localPence] for its date). */
    fun publicRowPetrol(r: PublicRow, cfg: EvSavingsConfig, localPence: Double?): Double? {
        val mpk = r.milesPerKwh ?: cfg.publicMilesPerKwh ?: cfg.chargeEfficiency * cfg.milesPerKwh
        val mpg = r.mpg ?: cfg.publicMpg ?: cfg.mpg
        val pence = r.fuelPrice?.let { it * 100 * (if (r.fuelCurrency == "EUR") cfg.eurToGbp else 1.0) }
            ?: publicPence(cfg) ?: localPence ?: return null
        return r.kwh * mpk / mpg * LITRES_PER_GALLON * pence / 100
    }

    /** The fixed public fuel price in pence per litre (in £), or null to use local prices. */
    fun publicPence(cfg: EvSavingsConfig): Double? = cfg.publicFuelPrice?.let {
        it * 100 * (if (cfg.publicFuelCurrency == "EUR") cfg.eurToGbp else 1.0)
    }

    /** Custom charges and trip settings from an ElectroFind backup (electrofind-auto-backup.json). */
    fun parseElectroFind(json: String): Pair<List<CustomCharge>, ElectroFindSettings?> {
        val trip = JsonParser.parseString(json).asJsonObject.getAsJsonObject("tripLog") ?: return Pair(emptyList(), null)
        val charges = trip.getAsJsonArray("customCharges")?.map { e ->
            val o = e.asJsonObject
            CustomCharge(
                id = "ef-" + o.get("id").asString,
                date = java.time.Instant.ofEpochMilli(o.get("dateEpochMillis").asLong).atZone(ZONE).toLocalDate().toString(),
                name = o.get("name")?.asString ?: "",
                kwh = o.get("kwh")?.asDouble ?: 0.0,
                cost = (o.get("cost")?.asDouble ?: 0.0) + (o.get("idleCost")?.asDouble ?: 0.0),
                currency = o.get("currency")?.asString ?: "GBP",
                excluded = o.get("excluded")?.asBoolean ?: false)
        } ?: emptyList()
        val st = trip.getAsJsonObject("settings")?.let {
            ElectroFindSettings(it.get("eurToGbpRate")?.asDouble, it.get("iceMpg")?.asDouble, it.get("petrolPricePerLitre")?.asDouble,
                it.get("evMilesPerKwh")?.asDouble)
        }
        return Pair(charges, st)
    }

    /** Local petrol price (p/litre) per date for [dates], as [calculate] prices them. */
    fun localPrices(dates: Collection<String>, national: List<Pair<LocalDate, Double>>,
                    local: Map<String, Pair<Int, Double>>, stationCount: Int): Map<String, Double> {
        val min = minStations(stationCount)
        val off = offset(national, local, min)?.first
        return dates.mapNotNull { d -> petrolPrice(d, national, local, off, min)?.let { d to it.pence } }.toMap()
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
            var pubMiles = 0.0; var pubPetrol = 0.0
            for ((d, p) in list) {
                val mi = d.kwh * (d.milesPerKwh ?: (if (d.public) cfg.publicMilesPerKwh else null)
                    ?: cfg.chargeEfficiency * cfg.milesPerKwh)
                val l = mi / (d.mpg ?: (if (d.public) cfg.publicMpg else null) ?: cfg.mpg) * LITRES_PER_GALLON
                val pence = d.pence ?: (if (d.public) publicPence(cfg) else null) ?: p.pence
                miles += mi; litres += l; petrol += l * pence / 100
                if (d.public) { pubMiles += mi; pubPetrol += l * pence / 100 }
            }
            val pub = list.filter { it.first.public }.map { it.first }
            EvMonth(m, list.sumOf { it.first.kwh }, miles, list.sumOf { it.first.cost }, litres, petrol,
                list.count { it.second.source == "local" }, list.size,
                publicKwh = pub.sumOf { it.kwh }, publicMiles = pubMiles, publicCost = pub.sumOf { it.cost },
                publicPetrolCost = pubPetrol)
        }.associateBy { it.month }.toSortedMap()
        for ((m, cs) in cfg.credits.groupBy { it.month }) {
            val row = rows[m] ?: EvMonth(m, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
            rows[m] = row.copy(credit = row.credit + cs.sumOf { it.amount })
        }
        // Repaid miles would have been paid on a petrol car too, so for them the total should hold the
        // actual profit (paid − charging), not petrol − charging. Charging is already counted, so each
        // repayment adds (paid − the petrol cost of its miles). That petrol cost uses a public charge on
        // the same date (its fuel price and mpg) if there is one, else home (local price, home mpg).
        val calcs = cfg.reimbursements.map { r ->
            val miles = r.miles ?: return@map RepaymentCalc(r, null, null, null, "no miles given: whole amount", r.amount)
            val pub = all.firstOrNull { it.public && it.date == r.date }
            val pence = pub?.let { it.pence ?: publicPence(cfg) } ?: petrolPrice(r.date, national, local, off?.first, min)?.pence
            val mpg = if (pub != null) pub.mpg ?: cfg.publicMpg ?: cfg.mpg else cfg.mpg
            val from = if (pub != null) "public charge that day" else "home price"
            if (pence == null) RepaymentCalc(r, null, null, mpg, "no petrol price: whole amount", r.amount)
            else {
                val petrol = miles / mpg * LITRES_PER_GALLON * pence / 100
                RepaymentCalc(r, petrol, pence, mpg, from, r.amount - petrol)
            }
        }
        for ((m, cs) in calcs.groupBy { it.repayment.date.take(7) }) {
            val row = rows[m] ?: EvMonth(m, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
            rows[m] = row.copy(reimbursed = row.reimbursed + cs.sumOf { it.adjustment })
        }
        val latest = petrolPrice(today.toString(), national, local, off?.first, min)
            ?: local.entries.lastOrNull { it.value.first >= min }?.let { PetrolPrice(it.key, it.value.second, "local") }
        return EvSavingsResult(rows.values.toList(), off?.first, off?.second ?: 0, latest, calcs)
    }
}
