package com.richw.sunsynk.api

import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.File
import java.time.LocalDate
import java.time.YearMonth
import java.time.temporal.ChronoUnit
import java.util.UUID
import java.util.concurrent.TimeUnit

class FuseAuthException(message: String) : Exception(message)

/**
 * Fuse Energy (SEG export supplier) web-app client, mirroring Python analysis/fuse_client.py.
 * Fuse has no public API; this talks to the tRPC endpoints the web app uses. Login is phone
 * number + SMS code ([startSignIn] then [verifyOtp]); the year-long `app-auth` + `session_id`
 * cookies are handed to [onCookies] for the caller to persist, and passed back in as [cookies].
 * Month views (daily export bars) are cached in [dataDir]; a cached file is reused only once
 * [isFinal], otherwise it is refetched and rewritten.
 */
class FuseClient(
    private val dataDir: File,
    cookies: Map<String, String> = emptyMap(),
    private val onCookies: (Map<String, String>) -> Unit = {},
    private val baseUrl: String = "https://www.fuseenergy.com",
    private val today: LocalDate = LocalDate.now(),
) {
    companion object {
        const val APP_VERSION = "0.5.416"
        const val USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        const val FINAL_GRACE_DAYS = 14L
        const val EARLIEST_YEAR = 2015
        private val PREMISES_RE = Regex(
            "\"premises\":\\{\"id\":\"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\"")
        val PERSISTED = setOf("session_id", "app-auth")

        /** A UK number in the E.164 form Fuse expects: "07700 900123" / "447700900123" ->
         *  "+447700900123". Anything else is passed through with spaces removed. */
        fun normalisePhone(phone: String): String {
            val p = phone.filterNot { it.isWhitespace() || it == '-' || it == '(' || it == ')' }
            return when {
                p.startsWith("+") -> p
                p.startsWith("00") -> "+" + p.drop(2)
                p.startsWith("44") -> "+$p"
                p.startsWith("0") -> "+44" + p.drop(1)
                else -> p
            }
        }

        /** Every `bar` (index, money, kWh, type) in a display-data response body. */
        fun bars(body: JsonObject): List<JsonObject> {
            val chart = body.getAsJsonObject("result").getAsJsonObject("data")
                .getAsJsonObject("data").getAsJsonObject("chart")
            val supplies = chart.get("supplies")?.takeIf { it.isJsonArray }?.asJsonArray ?: return emptyList()
            return supplies.flatMap { s ->
                (s.asJsonObject.get("bars")?.takeIf { it.isJsonArray }?.asJsonArray ?: return@flatMap emptyList())
                    .map { it.asJsonObject.getAsJsonObject("bar") }
            }
        }

        /** Safe to reuse from the cache: the period has ended and every bar is REALISED
         *  (smart-meter data lags), or it ended more than [graceDays] ago. */
        fun isFinal(body: JsonObject, end: LocalDate, today: LocalDate, graceDays: Long = FINAL_GRACE_DAYS): Boolean {
            if (!end.isBefore(today)) return false
            if (ChronoUnit.DAYS.between(end, today) > graceDays) return true
            val b = bars(body)
            return b.isNotEmpty() && b.all { it.get("type")?.asString == "REALISED" }
        }

        /** {"yyyy-MM-dd": export kWh} from REALISED daily bars in the cached month files. */
        fun loadDailyExportKwh(dataDir: File): Map<String, Double> {
            val out = sortedMapOf<String, Double>()
            dataDir.listFiles()?.filter { Regex("month-\\d{4}-\\d{2}\\.json").matches(it.name) }?.forEach { f ->
                for (bar in bars(JsonParser.parseString(f.readText()).asJsonObject)) {
                    if (bar.get("type")?.asString != "REALISED") continue
                    val i = bar.getAsJsonObject("index")
                    out["%04d-%02d-%02d".format(i.get("year").asInt, i.get("month").asInt, i.get("day").asInt)] =
                        bar.get("kWh").asString.toDouble()
                }
            }
            return out
        }
    }

    private val jar = cookies.toMutableMap()
    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()
    var fetched = 0
        private set
    var cached = 0
        private set

    val hasSession get() = jar.containsKey("app-auth")

    private fun headers(builder: Request.Builder, referer: String = "/app") = builder
        .header("X-Fuse-App-Version", APP_VERSION)
        .header("X-Request-Id", UUID.randomUUID().toString())
        .header("Timezone", "Europe/London")
        .header("User-Agent", USER_AGENT)
        .header("Accept", "*/*")
        .header("Accept-Language", "en-GB,en;q=0.9")
        .header("Content-Type", "application/json")
        .header("Origin", baseUrl)
        .header("Referer", baseUrl + referer)
        .apply { if (jar.isNotEmpty()) header("Cookie", jar.entries.joinToString("; ") { "${it.key}=${it.value}" }) }

    private fun keepCookies(resp: Response) {
        for (h in resp.headers("Set-Cookie")) {
            val parts = h.split(";").map { it.trim() }
            val (name, value) = parts[0].split("=", limit = 2).let { it[0] to it.getOrElse(1) { "" } }
            val expired = parts.any { it.equals("Max-Age=0", ignoreCase = true) } || value.isEmpty()
            if (expired) jar.remove(name) else jar[name] = value
        }
    }

    private fun parse(resp: Response): JsonElement {
        keepCookies(resp)
        val path = resp.request.url.encodedPath
        if (resp.code == 401 || resp.code == 403) throw FuseAuthException("HTTP ${resp.code} from $path")
        val body = try {
            JsonParser.parseString(resp.body!!.string()).asJsonObject
        } catch (e: Exception) {
            throw RuntimeException("Non-JSON response (HTTP ${resp.code}) from $path")
        }
        body.getAsJsonObject("error")?.let { e ->
            val err = e.getAsJsonObject("json") ?: e
            val data = err.getAsJsonObject("data")
            val code = data?.get("code")?.asString
            if (code == "UNAUTHORIZED" || code == "FORBIDDEN" || data?.get("httpStatus")?.asInt in setOf(401, 403)) {
                throw FuseAuthException(err.get("message")?.asString ?: "unauthorized")
            }
            throw RuntimeException("Fuse API error from $path: ${err.get("message")?.asString ?: err}" +
                (code?.let { " ($it, HTTP ${resp.code})" } ?: " (HTTP ${resp.code})"))
        }
        if (resp.code != 200) throw RuntimeException("HTTP ${resp.code} from $path")
        return body.getAsJsonObject("result").get("data")
    }

    private suspend fun post(procedure: String, payload: JsonObject): JsonObject = withContext(Dispatchers.IO) {
        val req = headers(Request.Builder(), "/")
            .url("$baseUrl/api/trpc/$procedure")
            .post(payload.toString().toByteArray().toRequestBody(null))
            .build()
        http.newCall(req).execute().use { parse(it).asJsonObject }
    }

    private suspend fun get(procedure: String, input: JsonObject? = null): JsonElement = withContext(Dispatchers.IO) {
        val url = "$baseUrl/api/trpc/$procedure".toHttpUrl().newBuilder()
            .apply { input?.let { addQueryParameter("input", it.toString()) } }
            .build()
        http.newCall(headers(Request.Builder()).url(url).build()).execute().use { parse(it) }
    }

    /** True if the saved cookies still work. */
    suspend fun checkSession(): Boolean {
        if (!hasSession) return false
        return try {
            get("getUnreadNotificationsCount"); true
        } catch (e: FuseAuthException) {
            false
        }
    }

    /** Ask Fuse to text a sign-in code to [phone] (e.g. +447...). */
    suspend fun startSignIn(phone: String) {
        jar.clear()
        // The web app sends supplyCountry (null); without it phoneSignIn is an HTTP 500 (Nov 2026).
        val body = post("phoneSignIn", JsonObject().apply {
            addProperty("phone", normalisePhone(phone)); add("supplyCountry", com.google.gson.JsonNull.INSTANCE)
        })
        fuseError(body)?.let { throw FuseAuthException("Fuse rejected the sign-in: $it") }
        val challenge = body.get("challenge_type")?.asString
        if (challenge != "PHONE_OTP") throw FuseAuthException("Unexpected phoneSignIn challenge: $challenge")
    }

    /** "incorrect_phone_number (+ incorrect_email_address)" from a tRPC result's error object, or null. */
    private fun fuseError(body: JsonObject): String? {
        val err = body.getAsJsonObject("error") ?: return null
        val codes = listOfNotNull(err.get("errorCode")?.asString) +
            (err.getAsJsonArray("__additionalErrors")?.mapNotNull { it.asJsonObject.get("errorCode")?.asString } ?: emptyList())
        return if (codes.isEmpty()) err.toString().take(200)
        else codes.first() + (if (codes.size > 1) " (+ ${codes.drop(1).joinToString()})" else "")
    }

    /** The cookies so far (after [startSignIn]: session_id + otp), to resume a sign-in later. */
    fun currentCookies(): Map<String, String> = jar.toMap()

    /** Complete sign-in with the SMS [code]; persists the session cookies via onCookies. */
    suspend fun verifyOtp(code: String) {
        val body = post("verifyOtp", JsonObject().apply { addProperty("code", code) })
        fuseError(body)?.let { throw FuseAuthException("Code not accepted: $it") }
        val challenge = body.get("challenge_type")?.asString
        if (challenge != "AUTHORIZED") throw FuseAuthException("Code not accepted (challenge_type=$challenge)")
        onCookies(jar.filterKeys { it in PERSISTED })
    }

    /** The first premises ID, scraped from the /app React Server Components payload. */
    suspend fun premisesFid(): String = withContext(Dispatchers.IO) {
        val req = headers(Request.Builder()).header("RSC", "1").url("$baseUrl/app").build()
        val text = http.newCall(req).execute().use { keepCookies(it); it.body!!.string() }
        PREMISES_RE.find(text)?.groupValues?.get(1) ?: throw RuntimeException("Could not find a Fuse premises ID")
    }

    private suspend fun displayData(premisesFid: String, index: JsonObject, name: String, end: LocalDate): JsonObject {
        val file = File(dataDir, "$name.json")
        if (file.exists()) {
            val body = runCatching { JsonParser.parseString(file.readText()).asJsonObject }.getOrNull()
            if (body != null && isFinal(body, end, today)) {
                cached++
                return body
            }
        }
        val input = JsonObject().apply { addProperty("premisesFid", premisesFid); add("index", index) }
        val body = JsonObject().apply {
            add("result", JsonObject().apply { add("data", get("premisesDisplayData", input)) })
        }
        fetched++
        dataDir.mkdirs()
        file.writeText(body.toString())
        return body
    }

    suspend fun getYearRaw(premisesFid: String, year: Int) =
        displayData(premisesFid, JsonObject().apply { addProperty("year", year) }, "year-%04d".format(year),
            LocalDate.of(year, 12, 31))

    suspend fun getMonthRaw(premisesFid: String, year: Int, month: Int) =
        displayData(premisesFid, JsonObject().apply { addProperty("year", year); addProperty("month", month) },
            "month-%04d-%02d".format(year, month), YearMonth.of(year, month).atEndOfMonth())

    /** Walk years back from [stop] until one has no export, fetching every month with data.
     *  Returns the months fetched/cached, oldest first. */
    suspend fun collect(premisesFid: String, stop: LocalDate = today, progress: (String) -> Unit = {}): List<YearMonth> {
        val months = mutableListOf<YearMonth>()
        for (year in stop.year downTo EARLIEST_YEAR) {
            progress("Fuse: $year…")
            val idx = bars(getYearRaw(premisesFid, year)).map { b ->
                val i = b.getAsJsonObject("index"); YearMonth.of(i.get("year").asInt, i.get("month").asInt)
            }.distinct().sorted()
            if (idx.isEmpty()) break
            for (ym in idx) {
                if (ym.atDay(1).isAfter(stop)) continue
                progress("Fuse: $ym…")
                getMonthRaw(premisesFid, ym.year, ym.monthValue)
                months.add(ym)
            }
        }
        return months.sorted()
    }
}
