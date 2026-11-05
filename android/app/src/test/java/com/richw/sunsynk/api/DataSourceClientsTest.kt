package com.richw.sunsynk.api

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.util.Base64

/** Pure helpers of the Fuse/Indra clients (parity with tests/test_fuse_client.py and
 *  tests/test_indra_client.py); the HTTP flows are exercised against the real services. */
class DataSourceClientsTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun displayBody(vararg bars: Triple<Triple<Int, Int, Int>, Double, String>) = JsonObject().apply {
        val entries = JsonArray().apply {
            bars.forEach { (idx, kwh, type) ->
                add(JsonObject().apply {
                    add("bar", JsonObject().apply {
                        add("index", JsonObject().apply {
                            addProperty("year", idx.first); addProperty("month", idx.second); addProperty("day", idx.third)
                        })
                        addProperty("kWh", kwh.toString()); addProperty("type", type)
                    })
                })
            }
        }
        add("result", JsonObject().apply {
            add("data", JsonObject().apply {
                add("data", JsonObject().apply {
                    add("chart", JsonObject().apply {
                        add("supplies", JsonArray().apply { if (bars.isNotEmpty()) add(JsonObject().apply { add("bars", entries) }) })
                    })
                })
            })
        })
    }

    private fun d(day: Int) = Triple(2026, 9, day)

    @Test
    fun fuseIsFinalRules() {
        val today = LocalDate.of(2026, 10, 2)
        assertFalse(FuseClient.isFinal(displayBody(Triple(d(1), 1.0, "REALISED")), today, today))
        assertTrue(FuseClient.isFinal(displayBody(Triple(d(1), 1.0, "REALISED"), Triple(d(2), 1.0, "REALISED")),
            LocalDate.of(2026, 10, 1), today))
        assertFalse(FuseClient.isFinal(displayBody(Triple(d(1), 1.0, "REALISED"), Triple(d(2), 1.0, "FORECASTED")),
            LocalDate.of(2026, 10, 1), today))
        assertFalse(FuseClient.isFinal(displayBody(), LocalDate.of(2026, 10, 1), today))
        assertTrue(FuseClient.isFinal(displayBody(Triple(d(1), 1.0, "FORECASTED")), LocalDate.of(2026, 9, 1), today))
    }

    @Test
    fun fuseLoadDailyExportKwhUsesRealisedBarsOnly() {
        tmp.root.resolve("month-2026-09.json").writeText(
            displayBody(Triple(d(29), 10.5, "REALISED"), Triple(d(30), 9.0, "FORECASTED")).toString())
        tmp.root.resolve("year-2026.json").writeText("{}")
        assertEquals(mapOf("2026-09-29" to 10.5), FuseClient.loadDailyExportKwh(tmp.root))
        assertTrue(FuseClient.loadDailyExportKwh(tmp.root.resolve("nope")).isEmpty())
    }

    @Test
    fun indraParseVerifyLink() {
        val link = "https://indrawebapp.azurewebsites.net/mobile/verify/production/driver%40example.com/DEF456/ABC123"
        assertEquals("DEF456" to "ABC123", IndraClient.parseVerifyLink(link))
        assertEquals("DEF456" to "ABC123", IndraClient.parseVerifyLink("https://x/mobile/verify/DEF456/ABC123/"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun indraRejectsOtherLinks() {
        IndraClient.parseVerifyLink("https://example.com/nope")
    }

    @Test
    fun indraTokenExpiryAndMonthFinality() {
        fun b64(s: String) = Base64.getUrlEncoder().withoutPadding().encodeToString(s.toByteArray())
        val token = "${b64("{\"alg\":\"HS256\"}")}.${b64("{\"exp\":1796401467}")}.sig"
        assertEquals(Instant.ofEpochSecond(1796401467), IndraClient.tokenExpiry(token))
        assertNull(IndraClient.tokenExpiry("not-a-jwt"))
        assertFalse(IndraClient.monthIsFinal(YearMonth.of(2026, 9), LocalDate.of(2026, 10, 2)))
        assertTrue(IndraClient.monthIsFinal(YearMonth.of(2026, 9), LocalDate.of(2026, 10, 4)))
    }

    @Test
    fun fuseNormalisesUkPhoneNumbers() {
        assertEquals("+447700900123", FuseClient.normalisePhone("07700 900123"))
        assertEquals("+447700900123", FuseClient.normalisePhone("447700900123"))
        assertEquals("+447700900123", FuseClient.normalisePhone("+44 7700-900123"))
        assertEquals("+447700900123", FuseClient.normalisePhone("00447700900123"))
    }

    @Test
    fun phoneSignInPayloadCarriesANullSupplyCountry() {
        // Fuse answers HTTP 500 unless supplyCountry is present (as null), so it must survive toString().
        val body = com.google.gson.JsonObject().apply {
            addProperty("phone", "+447700900000"); add("supplyCountry", com.google.gson.JsonNull.INSTANCE)
        }
        assertEquals("{\"phone\":\"+447700900000\",\"supplyCountry\":null}", body.toString())
    }
}
