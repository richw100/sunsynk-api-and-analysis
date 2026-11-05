package com.richw.sunsynk.api

import com.google.gson.Gson
import com.richw.sunsynk.analysis.HistoryDays
import com.richw.sunsynk.analysis.TariffHistory
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.LocalDate

/**
 * Opt-in network check of the history view against Python tariffhistory.py: runs only with
 * LIVE_TARIFF_REGION and LIVE_HISTORY_DAYS (a HistoryDays JSON, e.g. dumped from the Python run).
 */
class LiveTariffHistoryTest {
    @Test
    fun pricesSavedDaysAtLiveHistory() = runBlocking {
        val region = System.getenv("LIVE_TARIFF_REGION") ?: ""
        val path = System.getenv("LIVE_HISTORY_DAYS") ?: ""
        assumeTrue(region.isNotBlank() && path.isNotBlank())
        val saved = Gson().fromJson(File(path).readText(), HistoryDays::class.java)
        val src = TariffHistorySource(Files.createTempDirectory("hist").toFile(), log = { println("LOG $it") })
        val tariffs = src.fetch(region, LocalDate.parse(saved.days.keys.min()), LocalDate.parse(saved.days.keys.max()))
        val r = TariffHistory.priceHistory(saved.days, saved.periods, tariffs)
        for ((label, span) in listOf("ALL" to r.months, "LAST12" to TariffHistory.last12Months(r.months))) {
            TariffHistory.bestPairs(r, span).forEach {
                println("$label PAIR ${it.imp} + ${it.exp}: import %.2f export %.2f net %.2f".format(it.importTotal, it.income, it.net))
            }
        }
        println("SUMMARY ${tariffs.size} tariffs, ${src.calls} requests, days ${r.kwh.values.sumOf { it.days }}, skipped ${r.skipped}")
        assertTrue(tariffs.size == TariffHistory.TARIFFS.size)
    }
}
