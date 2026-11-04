package com.richw.sunsynk.api

import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.net.URLEncoder
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.time.temporal.ChronoUnit
import java.util.UUID
import java.util.concurrent.TimeUnit

class IndraAuthException(message: String) : Exception(message)

/** What the Indra login needs to remember (persisted by the caller). */
data class IndraSession(
    val email: String = "",
    val deviceKey: String = "",
    val checkHash: String = "",
    val token: String = "",
)

/**
 * Indra EV-charger client (api.indra.co.uk), mirroring Python analysis/indra_client.py: the
 * endpoints the Indra app uses. Login is an emailed magic link: [startSignIn] (check) makes
 * Indra email a link; [finishSignIn] verifies a pasted link (or, with none, assumes it was
 * opened on the phone) and polls for the 30-day JWT; [ensureToken] renews it via
 * api/authorize/refresh when it has under a week left. We sign in as our own random
 * device key, so the Indra app's session is untouched. Session changes go to [onSession].
 */
class IndraClient(
    private val dataDir: File,
    session: IndraSession = IndraSession(),
    private val onSession: (IndraSession) -> Unit = {},
    private val baseUrl: String = "https://api.indra.co.uk",
    private val today: LocalDate = LocalDate.now(),
    private val pollDelayMs: Long = 3000,
) {
    companion object {
        const val OS_ANDROID = 2           // the app's `os` path value: 1 = iOS, 2 = Android
        const val FINAL_GRACE_DAYS = 3L    // overnight sessions + late uploads keep changing a month
        const val REFRESH_WITHIN_DAYS = 7L
        const val TOKEN_POLL_ATTEMPTS = 10
        const val NETWORK_RETRIES = 3
        const val EMPTY_MONTHS_STOP = 3
        private val VERIFY_LINK_RE = Regex("/mobile/verify/(?:[^/]+/[^/]+/)?([0-9A-Fa-f]+)/([0-9A-Fa-f]+)/?$")

        /** (mobileHash, hash) from an emailed sign-in link. */
        fun parseVerifyLink(link: String): Pair<String, String> {
            val m = VERIFY_LINK_RE.find(link.trim()) ?: throw IllegalArgumentException("Not an Indra sign-in link")
            return Pair(m.groupValues[1], m.groupValues[2])
        }

        /** Expiry of a JWT from its unverified payload, or null if unreadable. */
        fun tokenExpiry(token: String): Instant? = try {
            val payload = token.split(".")[1]
            val json = String(java.util.Base64.getUrlDecoder().decode(payload))
            Instant.ofEpochSecond(JsonParser.parseString(json).asJsonObject.get("exp").asLong)
        } catch (e: Exception) {
            null
        }

        fun monthIsFinal(ym: YearMonth, today: LocalDate, graceDays: Long = FINAL_GRACE_DAYS) =
            ChronoUnit.DAYS.between(ym.atEndOfMonth(), today) > graceDays

        private fun q(v: String) = URLEncoder.encode(v, "UTF-8").replace("+", "%20")
    }

    private var state = session
    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()
    var fetched = 0
        private set
    var cached = 0
        private set

    val hasToken get() = state.token.isNotEmpty()

    private fun save(s: IndraSession) {
        state = s
        onSession(s)
    }

    /** GET [path]: decoded JSON, null for 404. 400/401/403 raise IndraAuthException;
     *  dropped connections / timeouts are retried with a short backoff. */
    private suspend fun get(path: String, auth: Boolean = true): JsonElement? = withContext(Dispatchers.IO) {
        for (attempt in 1..NETWORK_RETRIES) {
            try {
                return@withContext getOnce(path, auth)
            } catch (e: IOException) {
                if (attempt == NETWORK_RETRIES) throw e
                delay(2000L * attempt)
            }
        }
        null
    }

    private fun getOnce(path: String, auth: Boolean): JsonElement? {
        val req = Request.Builder().url("$baseUrl/$path").header("Accept", "application/json")
            .apply { if (auth) header("Authorization", "Bearer ${state.token}") }.build()
        http.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            return when {
                resp.code == 404 -> null
                resp.code in setOf(400, 401, 403) -> throw IndraAuthException("HTTP ${resp.code}: ${text.take(200)}")
                resp.code != 200 -> throw RuntimeException("HTTP ${resp.code} from /$path")
                else -> JsonParser.parseString(text)
            }
        }
    }

    private fun JsonElement?.stringOrNull() =
        this?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString?.takeIf { it.isNotEmpty() }

    /** Ask Indra to email a sign-in link to [email]. */
    suspend fun startSignIn(email: String) {
        val deviceKey = state.deviceKey.ifEmpty { UUID.randomUUID().toString() }
        val hash = get("api/user/check/${q(email)}/${q(deviceKey)}/$OS_ANDROID", auth = false).stringOrNull()
        if (hash == null || hash.contains("no account", ignoreCase = true)) {
            throw IndraAuthException("Indra has no account for $email")
        }
        save(IndraSession(email = email, deviceKey = deviceKey, checkHash = hash))
    }

    /** Verify the pasted [link] (blank = it was opened on the phone) and poll for the token. */
    suspend fun finishSignIn(link: String) {
        val s = state
        if (s.checkHash.isEmpty()) throw IndraAuthException("Request a sign-in email first")
        if (link.isNotBlank()) {
            val (mobileHash, hash) = parseVerifyLink(link)
            get("api/user/verify/${q(s.email)}/${q(mobileHash)}/${q(hash)}", auth = false)
        }
        repeat(TOKEN_POLL_ATTEMPTS) {
            val token = try {
                get("api/user/token/${q(s.email)}/${q(s.deviceKey)}/${q(s.checkHash)}/$OS_ANDROID", auth = false)
                    .stringOrNull()
            } catch (e: IndraAuthException) {
                null    // 400 "User Not Activated" until the link has been opened
            }
            if (token != null) {
                save(s.copy(token = token))
                return
            }
            delay(pollDelayMs)
        }
        throw IndraAuthException("The sign-in link hasn't been opened yet — open it, then try again")
    }

    /** Make sure the saved token works, renewing it if it expires within a week. */
    suspend fun ensureToken() {
        if (!hasToken) throw IndraAuthException("Not signed in to Indra")
        val expiry = tokenExpiry(state.token)
        if (expiry == null || expiry.isBefore(Instant.now().plus(REFRESH_WITHIN_DAYS, ChronoUnit.DAYS))) {
            val token = get("api/authorize/refresh").stringOrNull() ?: throw IndraAuthException("Token refresh failed")
            save(state.copy(token = token))
        } else {
            get("api/devices")
        }
    }

    /** Cached ``{name}.json`` if [final], else fetched and (re)written. */
    private suspend fun cachedGet(path: String, name: String, final: (JsonElement) -> Boolean): JsonElement? {
        val file = File(dataDir, "$name.json")
        if (file.exists()) {
            val body = runCatching { JsonParser.parseString(file.readText()) }.getOrNull()
            if (body != null && final(body)) {
                cached++
                return body
            }
        }
        val body = get(path)
        fetched++
        if (body != null) {
            dataDir.mkdirs()
            file.writeText(body.toString())
        }
        return body
    }

    suspend fun getMonthRaw(ym: YearMonth): JsonObject? =
        cachedGet("api/reports/monthly/${ym.year}/${ym.monthValue}", "month-$ym") { monthIsFinal(ym, today) }
            ?.takeIf { it.isJsonObject }?.asJsonObject

    suspend fun getSessionIndex(ym: YearMonth): List<String> =
        cachedGet("api/reports/transactions/index/${ym.year}/${ym.monthValue}", "sessions-$ym") { monthIsFinal(ym, today) }
            ?.takeIf { it.isJsonArray }?.asJsonArray?.map { it.asJsonObject.get("id").asString } ?: emptyList()

    suspend fun getSession(id: String): JsonObject? =
        cachedGet("api/reports/transactions/${q(id)}", "session-$id") { b ->
            b.isJsonObject && b.asJsonObject.get("end")?.isJsonNull == false
        }?.takeIf { it.isJsonObject }?.asJsonObject

    suspend fun getTelemetry(id: String, ended: Boolean) {
        cachedGet("api/reports/transactions/${q(id)}/telemetry", "telemetry-$id") { ended }
    }

    /** Walk months back from [stop] until EMPTY_MONTHS_STOP empty months in a row, fetching
     *  each monthly report plus every session and its per-minute telemetry. Returns
     *  {month: total kWh} for the months with charging. */
    suspend fun collect(stop: LocalDate = today, progress: (String) -> Unit = {}): Map<YearMonth, Double> {
        val months = sortedMapOf<YearMonth, Double>()
        var ym = YearMonth.from(stop)
        var emptyRun = 0
        while (ym.year >= 2015) {
            progress("Indra: $ym…")
            val reports = getMonthRaw(ym)?.getAsJsonArray("dailyReports")
            if (reports == null || reports.size() == 0) {
                if (++emptyRun >= EMPTY_MONTHS_STOP) break
            } else {
                emptyRun = 0
                months[ym] = reports.sumOf { r ->
                    r.asJsonObject.getAsJsonObject("totals")?.get("energyImportedKwh")?.asDouble ?: 0.0
                }
                for (id in getSessionIndex(ym)) {
                    val session = getSession(id) ?: continue
                    getTelemetry(id, ended = session.get("end")?.isJsonNull == false)
                }
            }
            ym = ym.minusMonths(1)
        }
        return months
    }
}
