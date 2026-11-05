package com.richw.sunsynk.api

import com.google.gson.JsonParser
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * Opt-in network check of [EonClient]: runs only with LIVE_EON_SESSION set to a desktop
 * eonData/.session.json (prints month totals only, never tokens or identifiers). The refresh
 * token rotates, so any token update is written back to that file — otherwise running this
 * would leave the desktop session holding a spent refresh token.
 */
class LiveEonClientTest {
    @Test
    fun downloadsMonthsAndBills() = runBlocking {
        val path = System.getenv("LIVE_EON_SESSION") ?: ""
        assumeTrue(path.isNotBlank())
        val a = JsonParser.parseString(File(path).readText()).asJsonObject.getAsJsonObject("auth0")
        val dir = Files.createTempDirectory("eon").toFile()
        val client = EonClient(dir, EonTokens(a.get("id_token").asString, a.get("refresh_token").asString), onTokens = { t ->
            a.addProperty("id_token", t.idToken); a.addProperty("refresh_token", t.refreshToken)
            File(path).writeText(com.google.gson.JsonObject().apply { add("auth0", a) }.toString())
        })
        client.ensureToken()
        println("PROPERTIES ${client.properties().keys}")
        val months = client.collect()
        months.forEach { (ym, t) -> println("MONTH $ym ${t.mapValues { "%.1f".format(it.value) }}") }
        val charges = client.tariffs()
        println("BILLS ${charges.size}, latest gas ${charges.filter { it.fuel == "Gas" }.maxByOrNull { it.start }?.let { "%.3f p/kWh %.2f p/day".format(it.rate, it.standing) }}")
        println("GAS DAYS ${EonClient.loadDailyKwh(dir, "gas").size}, requests ${client.fetched}")
        assertTrue(months.values.any { (it["gas"] ?: 0.0) > 0 })
    }
}
