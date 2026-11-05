package com.richw.sunsynk.api

import com.google.gson.JsonObject
import com.richw.sunsynk.analysis.HistTariff
import com.richw.sunsynk.analysis.HistorySpec
import com.richw.sunsynk.analysis.RateTimeline
import com.richw.sunsynk.analysis.TariffHistory
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import java.io.File
import java.time.LocalDate

/**
 * Rate history for [TariffHistory] (port of fetch_history in Python tariffhistory.py): each
 * product's single-register Direct Debit unit rates for the region, one calendar month per
 * request. Months that ended before today go through a permanent cache ([cacheRoot]/history),
 * the current month and the standing-charge timeline through today's cache.
 */
class TariffHistorySource(cacheRoot: File, private val today: LocalDate = LocalDate.now(),
                          private val log: (String) -> Unit = {}) {
    private val hist = TariffSources(cacheRoot, today, log, cacheDirName = HISTORY_DIR)
    private val daily = TariffSources(cacheRoot, today, log)
    val calls get() = hist.calls + daily.calls
    val hits get() = hist.hits + daily.hits

    companion object {
        const val HISTORY_DIR = "history"
        private const val HOST = "https://api.octopus.energy"
    }

    suspend fun fetch(region: String, start: LocalDate, stop: LocalDate,
                      specs: List<HistorySpec> = TariffHistory.TARIFFS): List<HistTariff> = coroutineScope {
        specs.map { spec -> async { runCatching { one(spec, region, start, stop) }
            .onFailure { log("[skip] ${spec.product}: ${it.message}") }.getOrNull() } }
            .awaitAll().filterNotNull()
    }

    private suspend fun one(spec: HistorySpec, region: String, start: LocalDate, stop: LocalDate): HistTariff {
        val product = hist.getJson("$HOST/v1/products/${spec.product}/")
        val byPayment = product.getAsJsonObject("single_register_electricity_tariffs")?.getAsJsonObject("_$region")
            ?: throw Exception("no single-register tariff for region $region")
        val tariff: JsonObject = byPayment.getAsJsonObject("direct_debit_monthly")
            ?: byPayment.getAsJsonObject("varying")
            ?: byPayment.entrySet().first().value.asJsonObject
        val base = "$HOST/v1/products/${spec.product}/electricity-tariffs/${tariff.get("code").asString}"
        val unitRows = mutableListOf<JsonObject>()
        TariffHistory.monthsBetween(start, stop).forEachIndexed { i, m ->
            val from = if (i == 0) m.minusDays(1) else m      // local midnight in BST is 23:00 UTC
            val to = m.plusMonths(1)
            val src = if (!to.isAfter(today)) hist else daily
            unitRows += src.pages("$base/standard-unit-rates/?page_size=1500&period_from=${from}T00:00:00Z" +
                "&period_to=${to}T00:00:00Z")
        }
        val standingRows = daily.pages("$base/standing-charges/?page_size=1500&period_from=${start.minusDays(1)}T00:00:00Z")
        log("${spec.name}: ${unitRows.size} rates")
        return HistTariff(spec.name, spec.product, spec.direction, spec.smartEv, spec.requires,
            RateTimeline(unitRows), RateTimeline(standingRows))
    }
}
