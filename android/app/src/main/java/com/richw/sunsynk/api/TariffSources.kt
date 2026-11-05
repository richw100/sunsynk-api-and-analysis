package com.richw.sunsynk.api

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.richw.sunsynk.analysis.Tariff
import com.richw.sunsynk.analysis.TariffCompare
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.concurrent.TimeUnit

/**
 * Current UK electricity import/export tariffs as 48 local half-hour rates, mirroring Python
 * analysis/tariff_sources.py (see its docstring for the sources): the Kraken REST APIs of
 * Octopus (+ Co-op, London Power), E.ON Next and EDF; energyplus.co.uk tariff pages for
 * British Gas / OVO / Scottish Power; Ofgem's regional price cap (everviz widget); OVO's
 * Charge Anytime add-on rate. Responses are cached under [cacheRoot]/{date}/ for the day.
 */
class TariffSources(
    cacheRoot: File,
    private val today: LocalDate = LocalDate.now(),
    private val log: (String) -> Unit = {},
    cacheDirName: String? = null,          // default: today's date (a cache for the day)
) {
    companion object {
        val LOCAL: ZoneId = ZoneId.of("Europe/London")
        const val USER_AGENT = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/124.0 Mobile Safari/537.36"
        const val RATE_DAYS = 14L
        val KRAKEN = linkedMapOf(
            "Octopus" to "https://api.octopus.energy",
            "E.ON Next" to "https://api.eonnext-kraken.energy",
            "EDF" to "https://api.edfgb-kraken.energy",
        )
        val BRANDS = mapOf("OCTOPUS_ENERGY" to "Octopus", "COOP_ENERGY" to "Co-op", "LONDON_POWER" to "London Power",
            "EON_NEXT" to "E.ON Next", "SMART_NEW_CO" to "E.ON Next", "SAINSBURYS" to "Sainsbury's (E.ON)",
            "EDF" to "EDF")
        const val ENERGYPLUS = "https://www.energyplus.co.uk"
        val ENERGYPLUS_SUPPLIERS = listOf("british-gas", "ovo-energy", "scottish-power")
        const val OFGEM_CAP_URL = "https://www.ofgem.gov.uk/your-energy-supply/your-energy-bill/" +
            "energy-price-cap-unit-rates-and-standing-charges"
        const val OVO_CHARGE_ANYTIME_URL = "https://www.ovoenergy.com/electric-cars/charge-anytime"
        const val GSP_URL = "https://api.octopus.energy/v1/industry/grid-supply-points/"

        val EXCLUDE = Regex("EXPORT|OUTGOING|SEG|ZERO|TEST|EMPLOYEE|ENGAGE|POWER.PACK|PODPOINT|POD.POINT|" +
            "HEAT.PUMP|COSY|SNUG|FLUX|_GAS|PREPAY|PARTNER|SAMSUNG|-OEV-", RegexOption.IGNORE_CASE)
        val EXPORT_EXCLUDE = Regex("ZERO|POWER.PACK|FLUX|TEST|NEXT_EXPORT_PREMIUM", RegexOption.IGNORE_CASE)
        val EXPORT_FLAT_IMPORT_ONLY = Regex("NEXT_EXPORT_EXCLUSIVE", RegexOption.IGNORE_CASE)
        val EXPORT_REQUIRES = listOf(
            Regex("OUTGOING-VAR|AGILE-OUTGOING|OUTGOING-PRIME", RegexOption.IGNORE_CASE) to "Octopus",
            Regex("NEXT_EXPORT|NEXT_FLEX_EXPORT", RegexOption.IGNORE_CASE) to "E.ON Next",
            Regex("COOP-SEG-FIX", RegexOption.IGNORE_CASE) to "Co-op",
            Regex("LP-SEG-FIX", RegexOption.IGNORE_CASE) to "London Power",
        )
        val SMART_EV = Regex("INTELLI|IOG", RegexOption.IGNORE_CASE)
        val WINDOWS = listOf(Regex("EV Power", RegexOption.IGNORE_CASE) to "00:00-05:00",
            Regex("EV Saver", RegexOption.IGNORE_CASE) to "00:00-05:00")

        val REGION_NAMES = mapOf(
            "A" to listOf("Eastern England", "Eastern"),
            "B" to listOf("East Midlands"),
            "C" to listOf("London"),
            "D" to listOf("Merseyside & North Wales", "North Wales and Mersey", "Merseyside and North Wales",
                "North Wales & Mersey"),
            "E" to listOf("West Midlands"),
            "F" to listOf("North East England", "Northern England", "North East"),
            "G" to listOf("North West England", "North Western England", "North West"),
            "H" to listOf("South England", "Southern England", "Southern"),
            "J" to listOf("South East England", "South Eastern England", "South East"),
            "K" to listOf("South Wales", "Southern Wales"),
            "L" to listOf("South West England", "South Western England", "South West"),
            "M" to listOf("Yorkshire"),
            "N" to listOf("South Scotland", "Southern Scotland"),
            "P" to listOf("North Scotland", "Northern Scotland"),
        )

        fun regionNames(region: String) = REGION_NAMES[region.uppercase().trimStart('_')]?.map { it.lowercase() } ?: emptyList()

        /** Product code without its version/date suffix, so only the newest version is kept. */
        fun familyKey(code: String): String {
            var key = code.replace(Regex("_HH$"), "")
            key = key.replace(Regex("(-\\d{2}){3}$"), "")
            key = key.replace(Regex("[_-]V\\d+(_\\d+)*$", RegexOption.IGNORE_CASE), "")
            return key
        }

        fun windowFromText(text: String): String? {
            val m = Regex("(\\d{1,2}:\\d{2})\\s*(?:-|–|to|and)\\s*(\\d{1,2}:\\d{2})").find(text) ?: return null
            return m.groupValues.drop(1).joinToString("-") { it.padStart(5, '0') }
        }

        private fun parseUtc(s: String): Instant = ZonedDateTime.parse(s.replace("Z", "+00:00")).toInstant()

        /** Average Kraken unit-rate periods (UTC) over each local half-hour of [dayFrom, dayTo);
         *  null for slots no period covered. */
        fun ratesToSlots(rates: List<JsonObject>, dayFrom: LocalDate, dayTo: LocalDate): List<Double?> {
            val periods = rates.map { r ->
                Triple(
                    r.get("valid_from")?.takeUnless { it.isJsonNull }?.asString?.let(::parseUtc) ?: Instant.MIN,
                    r.get("valid_to")?.takeUnless { it.isJsonNull }?.asString?.let(::parseUtc) ?: Instant.MAX,
                    r.get("value_inc_vat").asDouble,
                )
            }
            val sums = DoubleArray(48)
            val counts = IntArray(48)
            var day = dayFrom
            while (day < dayTo) {
                for (k in 0 until 48) {
                    val instant = day.atTime(k / 2, 30 * (k % 2)).atZone(LOCAL).toInstant()
                    periods.firstOrNull { (a, b, _) -> !instant.isBefore(a) && instant.isBefore(b) }?.let {
                        sums[k] += it.third; counts[k]++
                    }
                }
                day = day.plusDays(1)
            }
            return List(48) { if (counts[it] > 0) sums[it] / counts[it] else null }
        }

        private fun text(fragment: String): String {
            val noTags = fragment.replace(Regex("<[^>]+>"), " ")
            return unescape(noTags).replace(Regex("\\s+"), " ").trim()
        }

        private fun unescape(s: String) = s.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#39;", "'")
            .replace("&quot;", "\"").replace("&lt;", "<").replace("&gt;", ">").replace("&pound;", "£")
            .replace(Regex("&#[xX]([0-9a-fA-F]+);")) { it.groupValues[1].toInt(16).toChar().toString() }
            .replace(Regex("&#(\\d+);")) { it.groupValues[1].toInt().toChar().toString() }

        private fun num(s: String?): Double? =
            s?.replace(",", "")?.let { Regex("-?\\d+(?:\\.\\d+)?").find(it)?.value?.toDouble() }

        /** {table title: rows} for every table on an energyplus tariff page. */
        fun parseEnergyplusTables(page: String): Map<String, List<List<String>>> {
            val tables = linkedMapOf<String, List<List<String>>>()
            for (m in Regex("<table.*?</table>", RegexOption.DOT_MATCHES_ALL).findAll(page)) {
                val before = page.substring(maxOf(0, m.range.first - 1500), m.range.first)
                val heads = Regex("<h[2-4][^>]*>(.*?)</h[2-4]>", RegexOption.DOT_MATCHES_ALL).findAll(before).toList()
                if (heads.isEmpty()) continue
                val title = text(heads.last().groupValues[1])
                val rows = Regex("<tr.*?</tr>", RegexOption.DOT_MATCHES_ALL).findAll(m.value).map { row ->
                    Regex("<t[hd][^>]*>(.*?)</t[hd]>", RegexOption.DOT_MATCHES_ALL).findAll(row.value)
                        .map { text(it.groupValues[1]) }.toList()
                }.toList()
                tables.putIfAbsent(title, rows)
            }
            return tables
        }

        /** One tariff from an energyplus page, or (null, reason). */
        fun energyplusTariff(page: String, slug: String, supplierSlug: String, region: String): Pair<Tariff?, String?> {
            val h1 = Regex("<h1[^>]*>(.*?)</h1>", RegexOption.DOT_MATCHES_ALL).find(page)?.let { text(it.groupValues[1]) } ?: slug
            val m = Regex("(.*?) by (.*?):").find(h1)
            val name = m?.groupValues?.get(1) ?: h1
            val supplier = m?.groupValues?.get(2) ?: supplierSlug
            val tables = parseEnergyplusTables(page)
            val names = regionNames(region)
            fun find(kind: String): Map<String, String>? {
                for ((title, rows) in tables) {
                    if (title.startsWith("Electricity, Monthly Direct Debit") && kind in title && rows.isNotEmpty()) {
                        val row = rows.drop(1).firstOrNull { it.isNotEmpty() && it[0].trim().lowercase() in names }
                        if (row != null) return rows[0].zip(row).toMap()
                    }
                }
                return null
            }
            val std = find("standard-rate")
            val e7 = if (std == null) find("Economy 7") else null
            val row = std ?: e7 ?: return Pair(null, "no Monthly Direct Debit electricity row for this region")
            val months = num(row["Contract"])
            val lname = name.lowercase()
            val kind = when {
                "tracker" in lname -> "tracker"
                "variable" in lname -> "variable"
                months != null -> "fixed ${months.toInt()}m"
                else -> "fixed"
            }
            val standing = num(row["Elec standing (p/day)"])
            val peak = num(row["Elec rate (p/kWh)"])
            if (peak == null || standing == null) return Pair(null, "unreadable row")
            if (std != null) return Pair(Tariff(supplier, name, slug, "energyplus.co.uk", kind, List(48) { peak }, standing), null)
            val window = WINDOWS.firstOrNull { it.first.containsMatchIn(name) }?.second
                ?: return Pair(null, "Economy 7 only and no off-peak window known")
            val off = num(row["E7 off-peak (p/kWh)"]) ?: return Pair(null, "no off-peak rate")
            return Pair(Tariff(supplier, name, slug, "energyplus.co.uk", kind,
                TariffCompare.touSlotRates(peak, off, window), standing, note = "off-peak window $window assumed"), null)
        }

        /** (title, rows) from an everviz table 'inject' script. */
        fun parseEvervizTable(js: String): Pair<String, List<List<String>>> {
            val title = Regex("<h3>(.*?)</h3>").find(js)?.let { text(it.groupValues[1]) } ?: ""
            val start = js.indexOf("[[\"")
            if (start < 0) return Pair(title, emptyList())
            var depth = 0
            var end = start
            for (i in start until js.length) {
                if (js[i] == '[') depth++
                else if (js[i] == ']') { depth--; if (depth == 0) { end = i + 1; break } }
            }
            return try {
                val arr = JsonParser.parseString(js.substring(start, end)).asJsonArray
                Pair(title, arr.map { r -> r.asJsonArray.map { it.asString } })
            } catch (e: Exception) { Pair(title, emptyList()) }
        }

        fun priceCapTariff(rows: List<List<String>>, region: String): Tariff? {
            if (rows.isEmpty()) return null
            val header = rows[0]
            val names = regionNames(region)
            val row = rows.drop(1).firstOrNull { it.isNotEmpty() && it[0].trim().lowercase() in names } ?: return null
            val sc = header.indices.filter { "standing" in header[it].lowercase() }
            val unit = header.indices.filter { "unit rate" in header[it].lowercase() }
            if (sc.isEmpty() || unit.isEmpty()) return null
            val period = header[unit.last()].replace(Regex("(?i)unit rate\\s*"), "").trim()
            val u = num(row[unit.last()]) ?: return null
            val s = num(row[sc.last()]) ?: return null
            return Tariff("Price cap", "Standard variable ($period)", "OFGEM_CAP", "ofgem.gov.uk", "variable",
                List(48) { u }, s, note = "standard variable tariff of British Gas, OVO, Scottish Power, E.ON Next, EDF ...")
        }

        fun parseChargeAnytimeRate(page: String): Double? {
            val t = text(page.replace(Regex("<script.*?</script>|<style.*?</style>", RegexOption.DOT_MATCHES_ALL), ""))
            val m = Regex("Charge Anytime[^.]{0,80}?rate[^.]{0,40}? is (\\d+(?:\\.\\d+)?)p per kWh").find(t)
                ?: Regex("discounted to (\\d+(?:\\.\\d+)?)p per kWh for smart charging").find(t)
            return m?.groupValues?.get(1)?.toDouble()
        }
    }

    private val http = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()
    private val cacheDir = File(cacheRoot, cacheDirName ?: today.toString()).also { it.mkdirs() }
    private val sem = Semaphore(6)
    private val callCount = java.util.concurrent.atomic.AtomicInteger()
    private val hitCount = java.util.concurrent.atomic.AtomicInteger()
    val calls get() = callCount.get()
    val hits get() = hitCount.get()

    private fun cacheFile(url: String) =
        File(cacheDir, url.substringAfter("://").replace(Regex("[^\\w.-]+"), "_").take(200))

    suspend fun getText(url: String): String {
        val f = cacheFile(url)
        if (f.exists()) { hitCount.incrementAndGet(); return f.readText() }
        val body = sem.withPermit {
            withContext(Dispatchers.IO) {
                callCount.incrementAndGet()
                http.newCall(Request.Builder().url(url).header("User-Agent", USER_AGENT).build()).execute().use { resp ->
                    if (!resp.isSuccessful) throw Exception("$url: HTTP ${resp.code}")
                    resp.body?.string() ?: ""
                }
            }
        }
        f.writeText(body)
        return body
    }

    suspend fun getJson(url: String): JsonObject = JsonParser.parseString(getText(url)).asJsonObject

    suspend fun pages(first: String): List<JsonObject> {
        val out = mutableListOf<JsonObject>()
        var url: String? = first
        while (url != null) {
            val page = getJson(url)
            page.getAsJsonArray("results").forEach { out.add(it.asJsonObject) }
            url = page.get("next")?.takeUnless { it.isJsonNull }?.asString
        }
        return out
    }

    /** The region's DNO letter for a postcode (Octopus GSP lookup; the postcode isn't stored). */
    suspend fun regionForPostcode(postcode: String): String? {
        val url = GSP_URL.toHttpUrl().newBuilder().addQueryParameter("postcode", postcode.replace(" ", "")).build()
        val body = withContext(Dispatchers.IO) {
            http.newCall(Request.Builder().url(url).build()).execute().use { it.body?.string() ?: "" }
        }
        val results = JsonParser.parseString(body).asJsonObject.getAsJsonArray("results") ?: return null
        return results.firstOrNull()?.asJsonObject?.get("group_id")?.asString?.trimStart('_')
    }

    private fun isExport(p: JsonObject): Boolean {
        val dir = p.get("direction")?.takeUnless { it.isJsonNull }?.asString
        return if (dir != null) dir == "EXPORT" else Regex("EXPORT|OUTGOING|SEG", RegexOption.IGNORE_CASE)
            .containsMatchIn(p.get("code").asString)
    }

    private fun str(o: JsonObject, k: String) = o.get(k)?.takeUnless { it.isJsonNull }?.asString

    suspend fun krakenTariffs(label: String, host: String, region: String, export: Boolean): List<Tariff> = coroutineScope {
        val products = pages("$host/v1/products/?page_size=500")
        val exclude = if (export) EXPORT_EXCLUDE else EXCLUDE
        val latest = linkedMapOf<String, JsonObject>()
        for (p in products) {
            if (p.get("is_business")?.asBoolean == true || p.get("is_prepay")?.asBoolean == true) continue
            if (isExport(p) != export) continue
            val code = p.get("code").asString
            if (exclude.containsMatchIn(code) || exclude.containsMatchIn(str(p, "full_name") ?: "")) continue
            val fam = "${str(p, "brand")}|${familyKey(code)}"
            val prev = latest[fam]
            if (prev == null || (str(p, "available_from") ?: "") > (str(prev, "available_from") ?: "")) latest[fam] = p
        }
        latest.values.map { p ->
            async {
                try { krakenProduct(label, host, p, region, export) }
                catch (e: Exception) { log("skip ${p.get("code").asString}: ${e.message}"); null }
            }
        }.awaitAll().filterNotNull()
    }

    private suspend fun krakenProduct(label: String, host: String, product: JsonObject, region: String,
                                      export: Boolean): Tariff? {
        val code = product.get("code").asString
        val detail = getJson("$host/v1/products/$code/")
        val name = str(detail, "display_name") ?: str(product, "display_name") ?: code
        val term = product.get("term")?.takeUnless { it.isJsonNull }?.asInt
        val variable = product.get("is_variable")?.asBoolean == true
        val description = (str(detail, "description") ?: "").replace(Regex("\\s+"), " ").trim()
        val base = Tariff(
            supplier = BRANDS[str(product, "brand") ?: ""] ?: label, name = name, code = code,
            source = host.substringAfter("//"),
            kind = if (variable) "variable" else if (term != null) "fixed ${term}m" else "fixed",
            slotRates = emptyList(), standing = 0.0, smartEv = SMART_EV.containsMatchIn(code),
        )
        fun dd(key: String) = detail.getAsJsonObject(key)?.getAsJsonObject("_$region")?.getAsJsonObject("direct_debit_monthly")
        val single = (detail.get("single_register_electricity_tariffs") as? JsonObject)?.let { dd("single_register_electricity_tariffs") }
        if (single != null) {
            val slots = unitRateSlots(single, product) ?: return null
            val t = base.copy(slotRates = slots, standing = single.get("standing_charge_inc_vat").asDouble)
            if (!export) return t
            val requires = EXPORT_REQUIRES.firstOrNull { it.first.containsMatchIn(code) }?.second
            val flat = EXPORT_FLAT_IMPORT_ONLY.containsMatchIn(code)
            var first = description.split(Regex("(?<=\\.)\\s")).first().take(90)
            if (flat) first = "$first Not with time-of-use import tariffs.".trim()
            return t.copy(direction = "EXPORT", standing = 0.0, smartEv = false, requires = requires,
                flatImportOnly = flat, note = first)
        }
        if (export) return null
        val ev4 = (detail.get("four_rate_ev_electricity_tariffs") as? JsonObject)?.let { dd("four_rate_ev_electricity_tariffs") }
            ?: return null
        val day = ev4.get("day_unit_rate_inc_vat")?.takeUnless { it.isJsonNull }?.asDouble ?: return null
        val night = ev4.get("night_unit_rate_inc_vat").asDouble
        val window = windowFromText(description) ?: WINDOWS.firstOrNull { it.first.containsMatchIn(name) }?.second
            ?: return null
        val evOff = ev4.get("ev_device_off_peak_unit_rate_inc_vat")?.takeUnless { it.isJsonNull }?.asDouble
        return base.copy(slotRates = TariffCompare.touSlotRates(day, night, window),
            standing = ev4.get("standing_charge_inc_vat").asDouble, smartEv = evOff != null || base.smartEv,
            evRate = evOff, note = "off-peak $window (from description); smart-charged EV at its EV rate")
    }

    private suspend fun unitRateSlots(tariff: JsonObject, product: JsonObject): List<Double>? {
        val ratesUrl = tariff.getAsJsonArray("links")?.map { it.asJsonObject }
            ?.firstOrNull { it.get("rel").asString == "standard_unit_rates" }?.get("href")?.asString
        val available = str(product, "available_from")?.take(10)
        val from = maxOf(today.minusDays(RATE_DAYS), if (available != null) LocalDate.parse(available) else today)
        val to = minOf(from.plusDays(RATE_DAYS), today.plusDays(2))
        var slots: List<Double?> = List(48) { null }
        if (ratesUrl != null) {
            val rates = pages("$ratesUrl?period_from=${from}T00:00:00Z&period_to=${to}T00:00:00Z&page_size=1500")
            slots = ratesToSlots(rates, from, to)
        }
        val flat = tariff.get("standard_unit_rate_inc_vat")?.takeUnless { it.isJsonNull }?.asDouble
        val known = slots.filterNotNull()
        if (known.isEmpty()) return flat?.let { f -> List(48) { f } }
        val fill = flat ?: known.max()
        return slots.map { it ?: fill }
    }

    suspend fun energyplusTariffs(region: String): List<Tariff> = coroutineScope {
        val exclude = Regex(EXCLUDE.pattern.replace("_", "[-_ ]"), RegexOption.IGNORE_CASE)
        ENERGYPLUS_SUPPLIERS.flatMap { supplier ->
            val index = getText("$ENERGYPLUS/suppliers/$supplier/tariffs")
            val slugs = Regex("href=\"/suppliers/${Regex.escape(supplier)}/tariffs/([^\"/?#]+)\"").findAll(index)
                .map { it.groupValues[1] }.toSortedSet().filterNot { exclude.containsMatchIn(it) }
            slugs.map { slug ->
                async {
                    try {
                        val (t, reason) = energyplusTariff(getText("$ENERGYPLUS/suppliers/$supplier/tariffs/$slug"),
                            slug, supplier, region)
                        if (t == null) log("skip $supplier/$slug: $reason")
                        t
                    } catch (e: Exception) { log("skip $supplier/$slug: ${e.message}"); null }
                }
            }.awaitAll().filterNotNull()
        }
    }

    suspend fun ofgemCap(region: String): Tariff? {
        val page = getText(OFGEM_CAP_URL)
        val ids = Regex("everviz\\.com/inject/([A-Za-z0-9_-]+)").findAll(page).map { it.groupValues[1] }.toSortedSet()
        for (id in ids) {
            val (title, rows) = parseEvervizTable(getText("https://app.everviz.com/inject/$id/"))
            val t = title.lowercase()
            if (t.startsWith("electricity") && "direct debit" in t && "single rate" in t && rows.isNotEmpty())
                return priceCapTariff(rows, region)
        }
        throw Exception("Direct Debit single-rate table not found on the Ofgem page")
    }

    /** Every source; a failing one is logged and skipped. */
    suspend fun fetchAll(region: String): List<Tariff> {
        val r = region.uppercase().trimStart('_')
        require(r in REGION_NAMES) { "Region must be one of ${REGION_NAMES.keys.joinToString("")}" }
        val out = mutableListOf<Tariff>()
        for ((label, host) in KRAKEN) {
            try {
                out += krakenTariffs(label, host, r, export = false)
                out += krakenTariffs(label, host, r, export = true)
            } catch (e: Exception) { log("$label failed: ${e.message}") }
        }
        try { out += energyplusTariffs(r) } catch (e: Exception) { log("energyplus failed: ${e.message}") }
        var cap: Tariff? = null
        try { cap = ofgemCap(r)?.also { out += it } } catch (e: Exception) { log("Ofgem failed: ${e.message}") }
        try {
            val rate = parseChargeAnytimeRate(getText(OVO_CHARGE_ANYTIME_URL)) ?: throw Exception("rate not found")
            val ovo = out.filter { it.direction == "IMPORT" && it.supplier.lowercase().startsWith("ovo") }
            val bases = listOfNotNull(ovo.minByOrNull { it.slotRates[0] * 3000 + it.standing * 365 },
                cap?.copy(supplier = "OVO", name = "Standard variable (cap)", code = "OVO_SVT", note = ""))
            bases.forEach { t ->
                out += t.copy(name = "${t.name} + Charge Anytime", code = t.code + "+CA", evRate = rate, smartEv = true,
                    note = "EV smart-charged at ${rate}p (compatible car/charger needed)")
            }
        } catch (e: Exception) { log("OVO Charge Anytime failed: ${e.message}") }
        return out
    }
}
