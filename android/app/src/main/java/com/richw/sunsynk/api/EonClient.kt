package com.richw.sunsynk.api

import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit
import java.util.Base64
import java.util.concurrent.TimeUnit

class EonAuthException(message: String) : Exception(message)

/** The saved Auth0 session (persisted encrypted by the caller). */
data class EonTokens(val idToken: String = "", val refreshToken: String = "")

/** One billed charge (VAT-inclusive p/kWh and p/day, as billed). */
data class EonCharge(val fuel: String, val export: Boolean, val start: String, val end: String, val days: Int,
                     val kwh: Double, val rate: Double?, val standing: Double?, val grossP: Int?)

/**
 * E.ON Next smart-meter client, mirroring Python analysis/eon_client.py. E.ON's Kraken API no
 * longer takes email/password; like the E.ON Next app we sign in with Auth0 (authorization code
 * + PKCE at auth.eonnext.com, the app's client and redirect) and send the Auth0 **ID token** as
 * the Kraken Authorization header, renewing it with the refresh token. The sign-in page is shown
 * in a WebView that catches the redirect to the app's scheme ([authorizeUrl], [parseCallback],
 * [exchangeCode]). Half-hourly readings are cached as {kind}-YYYY-MM.json in [dataDir] (rewritten
 * until the month ended FINAL_GRACE_DAYS ago); bills go to tariffs.json. Nothing identifying
 * (account number, MPAN/MPRN, address) is stored.
 */
class EonClient(
    private val dataDir: File,
    tokens: EonTokens = EonTokens(),
    private val onTokens: (EonTokens) -> Unit = {},
    private val today: LocalDate = LocalDate.now(),
    private val authUrl: String = AUTH0_DOMAIN,
    private val graphqlUrl: String = GRAPHQL_URL,
) {
    companion object {
        const val AUTH0_DOMAIN = "https://auth.eonnext.com"
        const val CLIENT_ID = "kN3SN5h3H30hxpOjlhgmGeMfELKgn5Xg"
        const val CONNECTION = "prod-eon-next-uk-customers"
        const val REDIRECT_SCHEME = "com.eonnextmobile.app.auth0"
        const val REDIRECT_URI = "$REDIRECT_SCHEME://auth.eonnext.com/android/com.eonnextmobile.app/callback"
        const val SCOPE = "openid profile email offline_access"
        const val GRAPHQL_URL = "https://api.eonnext-kraken.energy/v1/graphql/"
        const val FINAL_GRACE_DAYS = 5L
        const val REFRESH_WITHIN_SECONDS = 600L
        const val EMPTY_MONTHS_STOP = 3
        const val PAGE_SIZE = 500
        val KINDS = listOf("import", "export", "gas")
        val MAX_KWH_PER_HALF_HOUR = mapOf("import" to 12.0, "export" to 12.0, "gas" to 20.0)
        private val LOCAL: ZoneId = ZoneId.of("Europe/London")
        private val JSON = "application/json".toMediaType()

        private const val ACCOUNTS_QUERY = """{ viewer { accounts { ... on AccountType { properties { id
  electricityMeterPoints { direction supplyEndDate } gasMeterPoints { supplyEndDate } } } } } }"""
        private val MEASUREMENTS_QUERY = """query(${'$'}id: ID!, ${'$'}f: [UtilityFiltersInput!], ${'$'}s: DateTime, ${'$'}e: DateTime, ${'$'}after: String) {
  property(id: ${'$'}id) { measurements(first: $PAGE_SIZE, after: ${'$'}after, startAt: ${'$'}s, endAt: ${'$'}e,
      timezone: "Europe/London", utilityFilters: ${'$'}f) {
    pageInfo { hasNextPage endCursor }
    edges { node { value ... on IntervalMeasurementType { startAt durationInSeconds } } } } } }"""
        private const val CHARGES_QUERY = """query(${'$'}after: String) { viewer { accounts { ... on AccountType {
  transactions(first: 100, after: ${'$'}after, transactionTypes: [ENERGY_CHARGES]) {
    pageInfo { hasNextPage endCursor }
    edges { node { title isReversed amounts { gross }
      ... on Charge { isExport consumption { startDate endDate quantity usageCost supplyCharge } } } } } } } } }"""

        fun filters(kind: String): JsonArray = JsonParser.parseString(when (kind) {
            "import" -> """[{"electricityFilters":{"readingFrequencyType":"RAW_INTERVAL","readingDirection":"CONSUMPTION"}}]"""
            "export" -> """[{"electricityFilters":{"readingFrequencyType":"RAW_INTERVAL","readingDirection":"GENERATION"}}]"""
            else -> """[{"gasFilters":{"readingFrequencyType":"RAW_INTERVAL"}}]"""
        }).asJsonArray

        /** (code_verifier, S256 code_challenge). */
        fun pkcePair(random: SecureRandom = SecureRandom()): Pair<String, String> {
            val bytes = ByteArray(48).also { random.nextBytes(it) }
            val enc = Base64.getUrlEncoder().withoutPadding()
            val verifier = enc.encodeToString(bytes)
            val challenge = enc.encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray()))
            return Pair(verifier, challenge)
        }

        private fun q(v: String) = URLEncoder.encode(v, "UTF-8")

        fun authorizeUrl(challenge: String, state: String, domain: String = AUTH0_DOMAIN): String =
            "$domain/authorize?" + listOf(
                "response_type" to "code", "client_id" to CLIENT_ID, "connection" to CONNECTION,
                "redirect_uri" to REDIRECT_URI, "scope" to SCOPE, "code_challenge" to challenge,
                "code_challenge_method" to "S256", "state" to state,
            ).joinToString("&") { "${it.first}=${q(it.second)}" }

        /** The authorization code from a ``...callback?code=...&state=...`` URL. */
        fun parseCallback(url: String, state: String): String {
            val query = url.trim().substringAfter('?', "").substringBefore('#')
            val params = query.split('&').filter { '=' in it }.associate {
                java.net.URLDecoder.decode(it.substringBefore('='), "UTF-8") to
                    java.net.URLDecoder.decode(it.substringAfter('='), "UTF-8")
            }
            params["error"]?.let { throw EonAuthException("Sign-in failed: ${params["error_description"] ?: it}") }
            if (params["state"] != state) throw EonAuthException("That URL is from a different sign-in attempt")
            return params["code"] ?: throw EonAuthException("No ?code= in the URL")
        }

        /** ``exp`` of a JWT (epoch seconds), or 0 if unreadable. */
        fun jwtExpiry(token: String): Long = try {
            val seg = token.split(".")[1]
            JsonParser.parseString(String(Base64.getUrlDecoder().decode(seg.padEnd((seg.length + 3) / 4 * 4, '='))))
                .asJsonObject.get("exp").asLong
        } catch (e: Exception) { 0L }

        /** Local month start and the next month's start, ISO with offsets (as Python month_bounds). */
        fun monthBounds(ym: YearMonth): Pair<String, String> {
            val fmt = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx")
            fun iso(d: LocalDate) = d.atStartOfDay(LOCAL).format(fmt)
            return Pair(iso(ym.atDay(1)), iso(ym.plusMonths(1).atDay(1)))
        }

        fun monthIsFinal(ym: YearMonth, today: LocalDate, graceDays: Long = FINAL_GRACE_DAYS) =
            ChronoUnit.DAYS.between(ym.plusMonths(1).atDay(1), today) >= graceDays

        /** ``[[startAt, kWh, seconds], ...]`` -> local date -> 48 kWh (long readings spread). */
        fun readingsToSlots(readings: List<Triple<String, Double, Int>>): Map<String, DoubleArray> {
            val out = sortedMapOf<String, DoubleArray>()
            for ((startAt, kwh, seconds) in readings) {
                val parts = maxOf(1, Math.round(seconds / 1800.0).toInt())
                val start = ZonedDateTime.parse(startAt).withZoneSameInstant(LOCAL)
                for (i in 0 until parts) {
                    val t = start.plusMinutes(30L * i).withZoneSameInstant(LOCAL)
                    val slot = t.hour * 2 + (if (t.minute >= 30) 1 else 0)
                    out.getOrPut(t.toLocalDate().toString()) { DoubleArray(48) }[slot] += kwh / parts
                }
            }
            return out
        }

        /** Network-free: plausible readings of one kind from the cached month files. */
        fun loadReadings(dataDir: File, kind: String): List<Triple<String, Double, Int>> {
            val cap = MAX_KWH_PER_HALF_HOUR.getValue(kind)
            val files = dataDir.listFiles()?.filter { Regex("$kind-\\d{4}-\\d{2}\\.json").matches(it.name) }
                ?.sortedBy { it.name } ?: return emptyList()
            return files.flatMap { f ->
                runCatching {
                    JsonParser.parseString(f.readText()).asJsonObject.getAsJsonArray("readings").map { r ->
                        val a = r.asJsonArray
                        Triple(a[0].asString, a[1].asDouble, if (a.size() > 2 && !a[2].isJsonNull) a[2].asInt else 1800)
                    }
                }.getOrDefault(emptyList())
            }.filter { it.second <= cap * maxOf(1.0, it.third / 1800.0) }
        }

        fun loadDailyKwh(dataDir: File, kind: String): Map<String, Double> =
            readingsToSlots(loadReadings(dataDir, kind)).mapValues { it.value.sum() }

        /** Billed charges saved by [tariffs] (oldest first), or empty. */
        fun loadCharges(dataDir: File): List<EonCharge> = runCatching {
            Gson().fromJson(File(dataDir, "tariffs.json").readText(), Array<EonCharge>::class.java).toList()
        }.getOrDefault(emptyList())

        /** Billed charge nodes -> [EonCharge] (reversed and consumption-less ones dropped). */
        fun chargesFrom(nodes: List<JsonObject>): List<EonCharge> = nodes.mapNotNull { n ->
            val c = n.getAsJsonObject("consumption") ?: return@mapNotNull null
            if (n.get("isReversed")?.asBoolean == true || c.get("startDate")?.isJsonNull != false) return@mapNotNull null
            val start = c.get("startDate").asString
            val end = c.get("endDate").asString
            val days = ChronoUnit.DAYS.between(LocalDate.parse(start), LocalDate.parse(end)).toInt() + 1
            val kwh = c.get("quantity")?.takeUnless { it.isJsonNull }?.asDouble ?: 0.0
            val usage = c.get("usageCost")?.takeUnless { it.isJsonNull }?.asInt ?: 0
            val supply = c.get("supplyCharge")?.takeUnless { it.isJsonNull }?.asInt ?: 0
            EonCharge(n.get("title")?.asString ?: "", n.get("isExport")?.takeUnless { it.isJsonNull }?.asBoolean == true || usage < 0,
                start, end, days, kwh, if (kwh > 0) usage / kwh else null, if (days > 0) supply.toDouble() / days else null,
                n.getAsJsonObject("amounts")?.get("gross")?.takeUnless { it.isJsonNull }?.asInt)
        }.sortedWith(compareBy({ it.start }, { it.fuel }))
    }

    private var tokens = tokens
    val fetched get() = fetchedCount
    val cached get() = cachedCount
    private var fetchedCount = 0
    private var cachedCount = 0
    private var properties: Map<String, String>? = null
    private val http = OkHttpClient.Builder().connectTimeout(30, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()

    private suspend fun post(url: String, body: JsonObject, auth: String? = null): Pair<Int, JsonObject> =
        withContext(Dispatchers.IO) {
            // OkHttp's default User-Agent gets a bare 403 from E.ON's firewall.
            val req = Request.Builder().url(url).post(body.toString().toRequestBody(JSON))
                .header("User-Agent", TariffSources.USER_AGENT).header("Accept", "application/json")
                .apply { if (auth != null) header("Authorization", auth) }.build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body?.string() ?: "{}"
                Pair(resp.code, runCatching { JsonParser.parseString(text).asJsonObject }.getOrDefault(JsonObject()))
            }
        }

    private suspend fun tokenRequest(fields: Map<String, String>): JsonObject {
        val body = JsonObject().apply { fields.forEach { (k, v) -> addProperty(k, v) } }
        val (code, data) = post("$authUrl/oauth/token", body)
        if (code != 200) throw EonAuthException("${data.get("error")?.asString ?: code}: ${data.get("error_description")?.asString ?: ""}")
        return data
    }

    private fun adopt(data: JsonObject) {
        tokens = EonTokens(data.get("id_token")?.asString ?: tokens.idToken,
            data.get("refresh_token")?.takeUnless { it.isJsonNull }?.asString ?: tokens.refreshToken)
        onTokens(tokens)
    }

    /** Finish sign-in: swap the code from the WebView redirect for tokens. */
    suspend fun exchangeCode(code: String, verifier: String) {
        adopt(tokenRequest(mapOf("grant_type" to "authorization_code", "client_id" to CLIENT_ID, "code" to code,
            "code_verifier" to verifier, "redirect_uri" to REDIRECT_URI)))
    }

    /** A usable ID token: renewed with the refresh token when under REFRESH_WITHIN_SECONDS remain. */
    suspend fun ensureToken() {
        if (jwtExpiry(tokens.idToken) - Instant.now().epochSecond > REFRESH_WITHIN_SECONDS) return
        refresh()
    }

    private suspend fun refresh() {
        if (tokens.refreshToken.isEmpty()) throw EonAuthException("Not signed in to E.ON Next")
        // The refresh token rotates: the new one must be kept (adopt -> onTokens) or the session dies.
        adopt(tokenRequest(mapOf("grant_type" to "refresh_token", "client_id" to CLIENT_ID,
            "refresh_token" to tokens.refreshToken, "scope" to SCOPE)))
    }

    suspend fun graphql(query: String, variables: JsonObject = JsonObject(), retryAuth: Boolean = true): JsonObject {
        fetchedCount++
        val body = JsonObject().apply { addProperty("query", query); add("variables", variables) }
        val (status, data) = post(graphqlUrl, body, tokens.idToken)
        val errors = data.getAsJsonArray("errors")
        if (errors != null && errors.size() > 0) {
            val text = errors.toString()
            val auth = "KT-CT-11" in text || "signature" in text.lowercase() || "auth" in text.lowercase()
            if (auth && retryAuth) {
                refresh()
                return graphql(query, variables, retryAuth = false)
            }
            val msg = errors[0].asJsonObject.get("message")?.asString ?: text
            throw if (auth) EonAuthException(msg) else Exception(msg)
        }
        return data.getAsJsonObject("data")
            ?: throw Exception("E.ON API HTTP $status: ${data.toString().take(200)}")
    }

    /** {import|export|gas: property id} for the meters this login can see. */
    suspend fun properties(): Map<String, String> = properties ?: run {
        val out = linkedMapOf<String, String>()
        for (a in graphql(ACCOUNTS_QUERY).getAsJsonObject("viewer").getAsJsonArray("accounts")) {
            for (p in a.asJsonObject.getAsJsonArray("properties") ?: JsonArray()) {
                val prop = p.asJsonObject
                for (mp in prop.getAsJsonArray("electricityMeterPoints") ?: JsonArray()) {
                    val dir = mp.asJsonObject.get("direction")?.takeUnless { it.isJsonNull }?.asString
                    out.putIfAbsent(if (dir == "EXPORT") "export" else "import", prop.get("id").asString)
                }
                if ((prop.getAsJsonArray("gasMeterPoints")?.size() ?: 0) > 0) out.putIfAbsent("gas", prop.get("id").asString)
            }
        }
        out.also { properties = it }
    }

    private suspend fun measurements(propertyId: String, kind: String, start: String, end: String): JsonArray {
        val out = JsonArray()
        var after: String? = null
        while (true) {
            val vars = JsonObject().apply {
                addProperty("id", propertyId); add("f", filters(kind)); addProperty("s", start); addProperty("e", end)
                addProperty("after", after)
            }
            val conn = graphql(MEASUREMENTS_QUERY, vars).getAsJsonObject("property").getAsJsonObject("measurements")
            for (e in conn.getAsJsonArray("edges")) {
                val n = e.asJsonObject.getAsJsonObject("node")
                val startAt = n.get("startAt")?.takeUnless { it.isJsonNull }?.asString ?: continue
                out.add(JsonArray().apply {
                    add(startAt); add(n.get("value").asDouble)
                    add(n.get("durationInSeconds")?.takeUnless { it.isJsonNull }?.asInt ?: 1800)
                })
            }
            val page = conn.getAsJsonObject("pageInfo")
            if (!page.get("hasNextPage").asBoolean) return out
            after = page.get("endCursor").asString
        }
    }

    /** One kind's month: from the cache when final, else downloaded (and rewritten). Returns the
     *  plausible kWh total, or null when this login has no meter of that kind. */
    suspend fun getMonth(kind: String, ym: YearMonth): Double? {
        dataDir.mkdirs()
        val file = File(dataDir, "$kind-$ym.json")
        val final = monthIsFinal(ym, today)
        val readings: JsonArray = if (final && file.exists()) {
            cachedCount++
            JsonParser.parseString(file.readText()).asJsonObject.getAsJsonArray("readings")
        } else {
            val prop = properties()[kind] ?: return null
            val (s, e) = monthBounds(ym)
            measurements(prop, kind, s, e).also { r ->
                file.writeText(JsonObject().apply {
                    addProperty("kind", kind); addProperty("month", ym.toString()); addProperty("final", final); add("readings", r)
                }.toString())
            }
        }
        val cap = MAX_KWH_PER_HALF_HOUR.getValue(kind)
        return readings.sumOf { val a = it.asJsonArray; a[1].asDouble.takeIf { v -> v <= cap } ?: 0.0 }
    }

    /** Walk months back from this one until EMPTY_MONTHS_STOP empty months; {month: {kind: kWh}}. */
    suspend fun collect(progress: (String) -> Unit = {}): Map<YearMonth, Map<String, Double>> {
        val out = sortedMapOf<YearMonth, Map<String, Double>>()
        var ym = YearMonth.from(today)
        var empty = 0
        while (empty < EMPTY_MONTHS_STOP && ym.year >= 2015) {
            progress("E.ON $ym")
            val totals = KINDS.mapNotNull { k -> getMonth(k, ym)?.let { k to it } }.toMap()
            out[ym] = totals
            empty = if (totals.values.any { it > 0 }) 0 else empty + 1
            ym = ym.minusMonths(1)
        }
        return out
    }

    /** Every billed energy charge (all accounts) saved to tariffs.json. */
    suspend fun tariffs(): List<EonCharge> {
        val nodes = mutableListOf<JsonObject>()
        var accountIndex = 0
        var accounts = 1
        while (accountIndex < accounts) {
            var after: String? = null
            while (true) {
                val data = graphql(CHARGES_QUERY, JsonObject().apply { addProperty("after", after) })
                val list = data.getAsJsonObject("viewer").getAsJsonArray("accounts")
                accounts = list.size()
                if (accountIndex >= accounts) break
                val conn = list[accountIndex].asJsonObject.getAsJsonObject("transactions")
                conn.getAsJsonArray("edges").forEach { nodes += it.asJsonObject.getAsJsonObject("node") }
                if (!conn.getAsJsonObject("pageInfo").get("hasNextPage").asBoolean) break
                after = conn.getAsJsonObject("pageInfo").get("endCursor").asString
            }
            accountIndex++
        }
        val charges = chargesFrom(nodes)
        dataDir.mkdirs()
        File(dataDir, "tariffs.json").writeText(Gson().toJson(charges))
        return charges
    }
}
