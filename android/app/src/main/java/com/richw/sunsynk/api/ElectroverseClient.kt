package com.richw.sunsynk.api

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
import java.time.Instant
import java.util.concurrent.TimeUnit

class ElectroverseAuthException(message: String) : Exception(message)

/** One public charging receipt (Electroverse ReceiptPaymentType), cost in its own currency. */
data class PublicCharge(
    val pk: Int, val start: Instant, val end: Instant?, val kwh: Double,
    val gross: Double, val currency: String, val operator: String, val address: String,
)

/**
 * Electroverse (Octopus) public charging receipts, read through the electroverse.com website's
 * GraphQL route (/api/proxy/graphql) with the session cookies the user's own sign-in in a WebView
 * leaves behind (token, refreshToken, csrftoken), as the website itself and ElectroFind do. The
 * website refreshes the session from refreshToken and returns new cookies, which are kept via
 * [onCookies]. Receipts come from `allPayments` (the mobile app's receipts list); every page is
 * saved to [dir]/payments.json so the EV tab works offline.
 */
class ElectroverseClient(
    private val dir: File,
    cookies: Map<String, String>,
    private val deviceId: String,
    private val onCookies: (Map<String, String>) -> Unit = {},
) {
    companion object {
        const val SITE = "https://electroverse.com"
        const val LOGIN_URL = "$SITE/log-in"
        const val GRAPHQL = "$SITE/api/proxy/graphql"
        val SESSION_COOKIES = listOf("token", "refreshToken", "csrftoken")
        const val PAGE = 50

        const val ALL_PAYMENTS = "query AllPayments(\$first: Int, \$after: String) { allPayments(first: \$first, " +
            "after: \$after) { edges { node { pk grossAmount currency { isoCode minorUnitConversion } " +
            "paymentSource { __typename ... on ReceiptPaymentType { pk periodStart periodEnd kwhUsed " +
            "operator { name } location { address city postalCode coordinates chargingLocationPk } } } paymentRecord { status hasRefund refundedAmount } } } " +
            "totalCount pageInfo { hasNextPage endCursor } } }"

        /** Session cookies from a WebView cookie string ("a=1; b=2"). */
        fun sessionCookies(raw: String?): Map<String, String> = (raw ?: "").split(";").mapNotNull {
            val (k, v) = it.trim().split("=", limit = 2).let { p -> if (p.size == 2) p[0] to p[1] else return@mapNotNull null }
            if (k in SESSION_COOKIES && v.isNotBlank()) k to v else null
        }.toMap()

        /** Receipts (charging sessions only, refunds netted off) from payment nodes. Checked Nov 2026: all 18
         *  receipts ElectroFind had parsed from PDFs match on kWh and cost to the penny. */
        fun parsePayments(nodes: List<JsonObject>): List<PublicCharge> = nodes.mapNotNull { n ->
            val src = n.getAsJsonObject("paymentSource") ?: return@mapNotNull null
            if (src.get("__typename")?.asString != "ReceiptPaymentType") return@mapNotNull null
            fun str(o: JsonObject?, k: String) = o?.get(k)?.takeUnless { it.isJsonNull }?.asString
            val start = str(src, "periodStart")?.let { Instant.parse(it) } ?: return@mapNotNull null
            val cur = n.getAsJsonObject("currency")
            val minor = cur?.get("minorUnitConversion")?.takeUnless { it.isJsonNull }?.asDouble ?: 100.0
            val record = n.getAsJsonObject("paymentRecord")
            val refunded = if (record?.get("hasRefund")?.takeUnless { it.isJsonNull }?.asBoolean == true)
                (record.get("refundedAmount")?.takeUnless { it.isJsonNull }?.asDouble ?: 0.0) else 0.0
            PublicCharge(
                pk = src.get("pk")?.asInt ?: n.get("pk").asInt,
                start = start, end = str(src, "periodEnd")?.let { Instant.parse(it) },
                kwh = src.get("kwhUsed")?.takeUnless { it.isJsonNull }?.asDouble ?: 0.0,
                // Payments come back as debits (negative minor units); refunds as positive amounts.
                gross = (kotlin.math.abs(n.get("grossAmount")?.takeUnless { it.isJsonNull }?.asDouble ?: 0.0) -
                    kotlin.math.abs(refunded)) / minor,
                currency = str(cur, "isoCode") ?: "GBP",
                operator = str(src.getAsJsonObject("operator"), "name") ?: "",
                address = str(src.getAsJsonObject("location"), "address") ?: "",
            )
        }

        /** The receipts saved by the last download (empty if none). */
        fun loadCharges(dir: File): List<PublicCharge> = runCatching {
            parsePayments(JsonParser.parseString(File(dir, "payments.json").readText()).asJsonArray.map { it.asJsonObject })
        }.getOrDefault(emptyList())
    }

    private var cookies = cookies.toMutableMap()
    private val http = OkHttpClient.Builder().connectTimeout(20, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()
    var calls = 0
        private set

    init { dir.mkdirs() }

    private suspend fun graphql(operation: String, query: String, variables: JsonObject): JsonObject = withContext(Dispatchers.IO) {
        if (cookies["token"].isNullOrBlank() && cookies["refreshToken"].isNullOrBlank())
            throw ElectroverseAuthException("Sign in to Electroverse first")
        val body = JsonObject().apply {
            addProperty("operationName", operation); addProperty("query", query); add("variables", variables)
        }.toString().toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url(GRAPHQL).post(body)
            .header("Accept", "application/json")
            .header("Origin", SITE).header("Referer", "$SITE/map")
            .header("X-Method", "POST").header("X-Path", "/api/proxy/graphql")
            .header("X-Dui", deviceId)
            .header("User-Agent", TariffSources.USER_AGENT)
            .header("Cookie", cookies.entries.joinToString("; ") { "${it.key}=${it.value}" })
            .build()
        calls++
        http.newCall(req).execute().use { resp ->
            // The website renews the JWT from refreshToken and sends it back as cookies.
            val renewed = resp.headers("Set-Cookie").mapNotNull { c ->
                val (k, v) = c.substringBefore(";").split("=", limit = 2).let { if (it.size == 2) it[0] to it[1] else return@mapNotNull null }
                if (k in SESSION_COOKIES) k to v else null
            }.toMap()
            if (renewed.isNotEmpty()) { cookies.putAll(renewed.filterValues { it.isNotBlank() }); onCookies(cookies.toMap()) }
            val text = resp.body?.string() ?: ""
            if (resp.code == 401) throw ElectroverseAuthException("Electroverse session has expired — sign in again")
            if (!resp.isSuccessful) throw Exception("Electroverse: HTTP ${resp.code}")
            val json = JsonParser.parseString(text).asJsonObject
            val errors = json.getAsJsonArray("errors")
            if (errors != null && errors.size() > 0) {
                val code = errors.firstNotNullOfOrNull { e ->
                    e.asJsonObject.get("errorCode")?.asString ?: e.asJsonObject.getAsJsonObject("extensions")?.get("errorCode")?.asString
                }
                if (code == "UNAUTHORIZED" || code?.startsWith("KT-CT-11") == true)
                    throw ElectroverseAuthException("Electroverse session has expired — sign in again")
                throw Exception("Electroverse: ${code ?: errors[0].asJsonObject.get("message")?.asString}")
            }
            json.getAsJsonObject("data")
        }
    }

    /** Every payment (all pages), saved to payments.json; returns the charging receipts. */
    suspend fun collect(progress: (String) -> Unit = {}): List<PublicCharge> {
        val nodes = mutableListOf<JsonObject>()
        var after: String? = null
        do {
            val vars = JsonObject().apply { addProperty("first", PAGE); if (after != null) addProperty("after", after) }
            val page = graphql("AllPayments", ALL_PAYMENTS, vars).getAsJsonObject("allPayments")
            page.getAsJsonArray("edges").forEach { nodes.add(it.asJsonObject.getAsJsonObject("node")) }
            progress("${nodes.size} of ${page.get("totalCount")?.asInt ?: "?"} payments")
            val info = page.getAsJsonObject("pageInfo")
            after = if (info.get("hasNextPage").asBoolean) info.get("endCursor").asString else null
        } while (after != null)
        File(dir, "payments.json").writeText(JsonArray().apply { nodes.forEach { add(it) } }.toString())
        return parsePayments(nodes)
    }
}
