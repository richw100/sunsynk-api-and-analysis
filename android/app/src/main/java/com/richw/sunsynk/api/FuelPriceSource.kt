package com.richw.sunsynk.api

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.time.Instant
import java.util.concurrent.TimeUnit

data class FuelStation(val id: String, val name: String, val postcode: String)

/**
 * Petrol prices for the EV-vs-petrol comparison, cached in [dir] (private filesDir/fuelData):
 *  - DESNZ weekly road fuel prices (UK average ULSP pump price, 2018 on): the gov.uk content API
 *    names the current CSV; refetched when the cache is over [MAX_AGE_HOURS] old.
 *  - fuelcosts.co.uk (the government Fuel Finder open data, from Jan 2026): supermarket stations
 *    near a point, and each station's E10 price changes. The API serves at most 365 days, so
 *    each station's changes are merged into its cache file and older ones are kept.
 */
class FuelPriceSource(private val dir: File) {
    companion object {
        const val GOVUK = "https://www.gov.uk/api/content/government/statistics/weekly-road-fuel-prices"
        const val FUELCOSTS = "https://fuelcosts.co.uk/api"
        const val POSTCODES = "https://api.postcodes.io/postcodes/"
        const val MAX_AGE_HOURS = 12L
        /** Big-four supermarket forecourts (Asda Express sites carry other brands, so match names). */
        val SUPERMARKETS = Regex("TESCO|ASDA|SAINSBURY|MORRISON", RegexOption.IGNORE_CASE)
        val NOT_SUPERMARKET = Regex("DAILY", RegexOption.IGNORE_CASE)     // Morrisons Daily: franchised garages

        fun parseStations(json: JsonObject): List<FuelStation> =
            (json.getAsJsonArray("stations") ?: JsonArray()).mapNotNull { e ->
                val o = e.asJsonObject
                val name = listOf("trading_name", "brand_name").joinToString(" ") { o.get(it)?.takeUnless { v -> v.isJsonNull }?.asString ?: "" }
                if (!SUPERMARKETS.containsMatchIn(name) || NOT_SUPERMARKET.containsMatchIn(name)) return@mapNotNull null
                if (o.get("is_permanently_closed")?.takeUnless { it.isJsonNull }?.asBoolean == true) return@mapNotNull null
                FuelStation(o.get("node_id").asString,
                    o.get("trading_name")?.takeUnless { it.isJsonNull }?.asString ?: name.trim(),
                    o.getAsJsonObject("location")?.get("postcode")?.takeUnless { it.isJsonNull }?.asString ?: "")
            }

        /** [(instant, p/litre)] from a station history response. */
        fun parseHistory(json: JsonObject): List<Pair<Instant, Double>> =
            (json.getAsJsonArray("history") ?: JsonArray()).mapNotNull { e ->
                val o = e.asJsonObject
                val t = o.get("timestamp")?.asString ?: return@mapNotNull null
                Pair(Instant.parse(t), o.get("pricePence")?.asDouble ?: return@mapNotNull null)
            }

        /** Union of two change lists by timestamp (newer fetch wins on a clash). */
        fun merge(old: List<Pair<Instant, Double>>, new: List<Pair<Instant, Double>>) =
            (old.associate { it.first to it.second } + new.associate { it.first to it.second })
                .entries.sortedBy { it.key }.map { Pair(it.key, it.value) }
    }

    private val http = OkHttpClient.Builder().connectTimeout(20, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()
    var calls = 0
        private set

    init { dir.mkdirs() }

    private suspend fun get(url: String): String = withContext(Dispatchers.IO) {
        calls++
        http.newCall(Request.Builder().url(url).header("User-Agent", TariffSources.USER_AGENT).build()).execute().use { r ->
            if (!r.isSuccessful) throw Exception("${url.substringBefore('?')}: HTTP ${r.code}")
            r.body?.string() ?: ""
        }
    }

    private fun fresh(f: File) = f.exists() && System.currentTimeMillis() - f.lastModified() < MAX_AGE_HOURS * 3600_000

    /** The DESNZ weekly CSV text (cached; refetched when stale, the old copy kept if that fails). */
    suspend fun nationalCsv(force: Boolean = false): String {
        val f = File(dir, "national.csv")
        if (!force && fresh(f)) return f.readText()
        return try {
            val content = JsonParser.parseString(get(GOVUK)).asJsonObject
            val url = content.getAsJsonObject("details").getAsJsonArray("attachments").map { it.asJsonObject }
                .firstOrNull { a ->
                    val title = a.get("title")?.asString ?: ""
                    a.get("url")?.asString?.endsWith(".csv") == true && "CSV" in title && "2018" in title
                }?.get("url")?.asString ?: throw Exception("No weekly fuel price CSV on gov.uk")
            get(url).also { f.writeText(it) }
        } catch (e: Exception) {
            if (f.exists()) f.readText() else throw e
        }
    }

    /** Postcode → (lat, lon) via postcodes.io; the postcode isn't stored. */
    suspend fun locate(postcode: String): Pair<Double, Double> {
        val r = JsonParser.parseString(get(POSTCODES + postcode.replace(" ", ""))).asJsonObject.getAsJsonObject("result")
            ?: throw Exception("Postcode not found")
        return Pair(r.get("latitude").asDouble, r.get("longitude").asDouble)
    }

    /** Big-four supermarket stations selling E10 within [radiusMiles] of a point. */
    suspend fun findStations(lat: Double, lon: Double, radiusMiles: Double): List<FuelStation> {
        val url = "$FUELCOSTS/stations".toHttpUrl().newBuilder()
            .addQueryParameter("lat", lat.toString()).addQueryParameter("lon", lon.toString())
            .addQueryParameter("radius", radiusMiles.toString()).addQueryParameter("fuel", "E10")
            .addQueryParameter("limit", "1000").build().toString()
        return parseStations(JsonParser.parseString(get(url)).asJsonObject)
    }

    private fun stationFile(id: String) = File(dir, "station-$id.json")

    fun cachedHistory(id: String): List<Pair<Instant, Double>> = runCatching {
        JsonParser.parseString(stationFile(id).readText()).asJsonArray.map {
            val a = it.asJsonArray; Pair(Instant.parse(a[0].asString), a[1].asDouble)
        }
    }.getOrDefault(emptyList())

    /** A station's E10 changes: the cache, topped up from the API when stale. */
    suspend fun history(id: String, force: Boolean = false): List<Pair<Instant, Double>> {
        val f = stationFile(id)
        val old = cachedHistory(id)
        if (!force && fresh(f)) return old
        val new = parseHistory(JsonParser.parseString(get("$FUELCOSTS/stations/$id/history?fuel=E10&days=365")).asJsonObject)
        val merged = merge(old, new)
        f.writeText(JsonArray().apply {
            merged.forEach { (t, p) -> add(JsonArray().apply { add(t.toString()); add(p) }) }
        }.toString())
        return merged
    }

    fun lastUpdated(): Long = dir.listFiles()?.maxOfOrNull { it.lastModified() } ?: 0L
}
