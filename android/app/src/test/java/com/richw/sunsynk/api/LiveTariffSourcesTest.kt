package com.richw.sunsynk.api

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.nio.file.Files

/**
 * Opt-in network smoke test of the real tariff sources: runs only with LIVE_TARIFF_REGION set
 * (e.g. `LIVE_TARIFF_REGION=A ./gradlew testDebugUnitTest --tests '*LiveTariffSourcesTest*'`).
 */
class LiveTariffSourcesTest {
    @Test
    fun fetchesEverySource() = runBlocking {
        val region = System.getenv("LIVE_TARIFF_REGION") ?: ""
        assumeTrue(region.isNotBlank())
        val log = mutableListOf<String>()
        val src = TariffSources(Files.createTempDirectory("tariffs").toFile(), log = { synchronized(log) { log.add(it) } })
        val tariffs = src.fetchAll(region)
        log.forEach { println("LOG $it") }
        tariffs.filter { it.direction != "GAS" }.sortedBy { it.direction + it.supplier + it.name }.forEach {
            println("TARIFF ${it.direction} | ${it.supplier} | ${it.name} | ${it.kind} | min %.2f max %.2f | sc %.2f | %s%s"
                .format(it.slotRates.min(), it.slotRates.max(), it.standing,
                    if (it.smartEv) "smart " else "", it.requires ?: ""))
        }
        tariffs.filter { it.monthSlotRates != null }.forEach { t ->
            val ms = t.monthSlotRates!!
            println("SEASONAL ${t.direction} ${t.supplier} ${t.name}: yearly avg %.2f, Jan midday %.2f, Jul midday %.2f, Jan 4-7pm %.2f"
                .format(t.slotRates.average(), ms[0].subList(20, 30).average(), ms[6].subList(20, 30).average(), ms[0].subList(32, 38).average()))
        }
        val sources = tariffs.map { it.source }.toSet()
        println("SUMMARY ${tariffs.size} tariffs, ${src.calls} requests, sources $sources")
        assertTrue("api.octopus.energy" in sources && "api.eonnext-kraken.energy" in sources &&
            "api.edfgb-kraken.energy" in sources && "energyplus.co.uk" in sources && "ofgem.gov.uk" in sources)
        assertTrue(tariffs.any { it.name.endsWith("+ Charge Anytime") })
        assertTrue(tariffs.any { it.direction == "EXPORT" })
        assertTrue(tariffs.any { it.code.startsWith("IOG") && it.smartEv && it.evRate != null })
    }
}
