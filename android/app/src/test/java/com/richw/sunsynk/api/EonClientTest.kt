package com.richw.sunsynk.api

import com.google.gson.JsonParser
import com.richw.sunsynk.analysis.TariffCompare
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.time.LocalDate
import java.time.YearMonth
import java.util.Base64

/** Parity with tests/test_eon_client.py and the gas parts of tests/test_tariffcompare.py. */
class EonClientTest {
    @Test
    fun pkceAuthorizeUrlAndCallback() {
        val (verifier, challenge) = EonClient.pkcePair()
        assertTrue(verifier.length >= 43 && challenge.length == 43)
        val url = EonClient.authorizeUrl(challenge, "st8")
        assertTrue("code_challenge=$challenge" in url && "state=st8" in url && "offline_access" in url)
        assertTrue("redirect_uri=com.eonnextmobile.app.auth0%3A%2F%2F" in url)
        val cb = "com.eonnextmobile.app.auth0://auth.eonnext.com/android/com.eonnextmobile.app/callback?code=abc&state=st8"
        assertEquals("abc", EonClient.parseCallback(" $cb\n", "st8"))
        assertTrue(runCatching { EonClient.parseCallback(cb, "other") }.exceptionOrNull() is EonAuthException)
        val err = runCatching { EonClient.parseCallback("x://y?error=access_denied&error_description=denied&state=st8", "st8") }
        assertTrue(err.exceptionOrNull()!!.message!!.contains("denied"))
    }

    @Test
    fun jwtExpiryAndMonthRules() {
        val enc = Base64.getUrlEncoder().withoutPadding()
        val jwt = enc.encodeToString("{\"alg\":\"none\"}".toByteArray()) + "." + enc.encodeToString("{\"exp\":1234}".toByteArray()) + ".s"
        assertEquals(1234L, EonClient.jwtExpiry(jwt))
        assertEquals(0L, EonClient.jwtExpiry("nope"))
        assertEquals(Pair("2026-07-01T00:00:00+01:00", "2026-08-01T00:00:00+01:00"), EonClient.monthBounds(YearMonth.of(2026, 7)))
        assertEquals(Pair("2026-12-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"), EonClient.monthBounds(YearMonth.of(2026, 12)))
        assertFalse(EonClient.monthIsFinal(YearMonth.of(2026, 10), LocalDate.of(2026, 11, 5)))
        assertTrue(EonClient.monthIsFinal(YearMonth.of(2026, 10), LocalDate.of(2026, 11, 6)))
    }

    @Test
    fun slotsAndLoadersDropImplausible() {
        val slots = EonClient.readingsToSlots(listOf(Triple("2026-07-14T23:30:00+00:00", 1.0, 1800),
            Triple("2026-01-15T00:00:00+00:00", 2.0, 3600)))
        assertEquals(1.0, slots.getValue("2026-07-15")[1], 1e-9)
        assertEquals(1.0, slots.getValue("2026-01-15")[0], 1e-9)
        assertEquals(1.0, slots.getValue("2026-01-15")[1], 1e-9)
        val dir = Files.createTempDirectory("eon").toFile()
        dir.resolve("export-2025-07.json").writeText("""{"readings":[["2025-07-01T00:00:00+01:00",99.8,1800],["2025-07-01T12:00:00+01:00",1.5,1800]]}""")
        assertEquals(mapOf("2025-07-01" to 1.5), EonClient.loadDailyKwh(dir, "export"))
        assertTrue(EonClient.loadDailyKwh(dir, "gas").isEmpty())
    }

    @Test
    fun chargesIncludeVatAndSkipReversed() {
        val nodes = listOf(
            """{"title":"Electricity","isReversed":false,"isExport":false,"amounts":{"gross":9916},"consumption":{"startDate":"2026-01-20","endDate":"2026-02-19","quantity":"492.03","usageCost":8057,"supplyCharge":1860}}""",
            """{"title":"Electricity","isReversed":true,"amounts":{},"consumption":{"startDate":"2026-01-20","endDate":"2026-02-19","quantity":"1","usageCost":1,"supplyCharge":1}}""",
            """{"title":"Electricity","isReversed":false,"isExport":false,"amounts":{"gross":-3397},"consumption":{"startDate":"2026-06-05","endDate":"2026-06-24","quantity":"205.83","usageCost":-3397,"supplyCharge":0}}""",
            """{"title":"Fee","isReversed":false,"amounts":{}}""",
        ).map { JsonParser.parseString(it).asJsonObject }
        val out = EonClient.chargesFrom(nodes)
        assertEquals(2, out.size)
        assertEquals(31, out[0].days)
        assertEquals(60.0, out[0].standing!!, 1e-9)
        assertEquals(8057 / 492.03, out[0].rate!!, 1e-9)
        assertTrue(out[1].export)
    }

    @Test
    fun gasLinkingPricingAndOverall() {
        assertEquals("NEXT_DRIVE_FIXED_V37", TariffSources.gasCodeKey("NEXT_DRIVE_FIXED_GAS_V37"))
        val gas = TariffCompare.gasOffer("E.ON Next", "Drive gas", "NEXT_DRIVE_FIXED_GAS_V37", "x", "", 8.8, 29.7)!!
        val elec = com.richw.sunsynk.analysis.Tariff("E.ON Next", "Drive", "NEXT_DRIVE_FIXED_V37", "x", "", List(48) { 20.0 }, 50.0)
        val linked = TariffSources.linkGas(listOf(elec, gas))
        assertEquals(gas, linked.first { it.direction == "IMPORT" }.gas)
        assertEquals(1, linked.count { it.direction == "GAS" })
        assertNull(TariffCompare.gasOffer("a", "b", "c", "d", "", null, 1.0))

        val daily = (1..12).associate { "2026-%02d-01".format(it) to 10.0 * it } + ("2025-01-01" to 30.0)
        val year = TariffCompare.annualGas(daily)
        assertTrue(year.seasonal)
        assertEquals(20.0 * 31, year.monthly.getValue(0), 1e-9)
        val partial = TariffCompare.annualGas(mapOf("2026-01-01" to 10.0, "2026-07-01" to 2.0))
        assertFalse(partial.seasonal)
        assertEquals(365 * 6.0, partial.kwh, 1e-9)

        val cur = TariffCompare.gasOffer("Current", "billed", "CURRENT_GAS", "x", "", 5.0, 34.0)
        val a = TariffCompare.gasOffer("A", "A", "A_GAS", "x", "", 8.0, 30.0)!!
        val b = TariffCompare.gasOffer("B", "B", "B_GAS", "x", "", 7.0, 25.0)!!
        val rows = TariffCompare.compareGas(listOf(a, b), cur, 10000.0)
        assertEquals(listOf("CURRENT_GAS", "B_GAS", "A_GAS"), rows.map { it.tariff.code })
        assertEquals(700 + 91.25, rows[1].cost, 1e-9)
    }
}
