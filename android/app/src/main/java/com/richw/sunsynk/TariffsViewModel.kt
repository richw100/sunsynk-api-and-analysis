package com.richw.sunsynk

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.richw.sunsynk.analysis.ExportRow
import com.richw.sunsynk.analysis.GasRow
import com.richw.sunsynk.analysis.GasYear
import com.richw.sunsynk.analysis.OverallRow
import com.richw.sunsynk.api.EonClient
import com.richw.sunsynk.analysis.HistoryResult
import com.richw.sunsynk.analysis.TariffHistory
import com.richw.sunsynk.api.TariffHistorySource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import com.richw.sunsynk.analysis.ImportRow
import com.richw.sunsynk.analysis.Pair3
import com.richw.sunsynk.analysis.Tariff
import com.richw.sunsynk.analysis.TariffCompare
import com.richw.sunsynk.analysis.TariffProfile
import com.richw.sunsynk.api.TariffSources
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.File

data class TariffsUiState(
    val region: String = "",
    val profile: TariffProfile? = null,
    val busy: String? = null,             // progress text while fetching
    val error: String? = null,
    val fetchedAt: String? = null,
    val imports: List<ImportRow> = emptyList(),
    val exports: List<ExportRow> = emptyList(),
    val pairs: List<Pair3> = emptyList(),
    val log: List<String> = emptyList(),
    val mode: String = "today",           // "today" (latest rates, projected year) or "history"
    val history: HistoryResult? = null,   // actual days priced at historic rates
    val historyBusy: String? = null,
    val historyError: String? = null,
    val historyNote: String? = null,      // days/period/request counts
    val historyAll: Boolean = false,      // all data vs the last 12 full months
    val gasYear: GasYear? = null,         // from the E.ON gas meter (Data sources)
    val gasRows: List<GasRow> = emptyList(),
    val overall: List<OverallRow> = emptyList(),
)

/**
 * Tariffs tab: prices the latest analysis run's half-hourly year ([TariffProfileStore]) against
 * the latest rates scraped by [TariffSources] (cached per day in filesDir/tariffData).
 */
class TariffsViewModel(private val context: Context) : ViewModel() {
    private val prefs = PrefsStore(context)
    private val profiles = TariffProfileStore(context)
    private val cacheRoot = File(context.filesDir, "tariffData")
    private var tariffs: List<Tariff> = emptyList()
    private var job: Job? = null
    private var historyJob: Job? = null

    private val _state = MutableStateFlow(TariffsUiState(region = prefs.tariffRegion, profile = profiles.load()))
    val state = _state.asStateFlow()

    /** Re-read the saved profile (after a new analysis run) and re-price what we have. */
    fun reloadProfile() {
        _state.value = _state.value.copy(profile = profiles.load())
        reprice()
    }

    fun setRegion(region: String) {
        val r = region.trim().uppercase().trimStart('_')
        prefs.tariffRegion = r
        _state.value = _state.value.copy(region = r)
    }

    fun lookupPostcode(postcode: String) {
        job?.cancel()
        job = viewModelScope.launch {
            _state.value = _state.value.copy(busy = "Looking up region…", error = null)
            try {
                val r = TariffSources(cacheRoot).regionForPostcode(postcode)
                    ?: throw Exception("No region found for that postcode")
                setRegion(r)
                _state.value = _state.value.copy(busy = null)
            } catch (e: Exception) {
                _state.value = _state.value.copy(busy = null, error = e.message)
            }
        }
    }

    /** Scrape the latest rates (today's cache is reused unless [force]) and price them. */
    fun refresh(force: Boolean = false) {
        val region = _state.value.region
        if (region !in TariffSources.REGION_NAMES) {
            _state.value = _state.value.copy(error = "Set your region letter (A-P) first")
            return
        }
        job?.cancel()
        job = viewModelScope.launch {
            val log = mutableListOf<String>()
            _state.value = _state.value.copy(busy = "Fetching latest rates…", error = null)
            try {
                if (force) File(cacheRoot, java.time.LocalDate.now().toString()).deleteRecursively()
                cacheRoot.listFiles()?.filter { it.name != TariffHistorySource.HISTORY_DIR &&
                    it.name < java.time.LocalDate.now().minusDays(7).toString() }
                    ?.forEach { it.deleteRecursively() }
                val src = TariffSources(cacheRoot, log = { synchronized(log) { log.add(it) } })
                tariffs = src.fetchAll(region)
                log.add("${tariffs.size} tariffs: ${src.calls} requests, ${src.hits} from today's cache")
                _state.value = _state.value.copy(
                    busy = null, log = log.toList(),
                    fetchedAt = java.time.LocalTime.now().withNano(0).withSecond(0).toString(),
                )
                reprice()
            } catch (e: Exception) {
                _state.value = _state.value.copy(busy = null, error = e.message ?: "Fetch failed", log = log.toList())
            }
        }
    }

    fun setMode(mode: String) { _state.value = _state.value.copy(mode = mode) }
    fun setHistoryAll(all: Boolean) { _state.value = _state.value.copy(historyAll = all) }

    /** Price the latest analysis run's actual days at the rates each tariff charged on the day. */
    fun priceHistory() {
        val region = _state.value.region
        if (region !in TariffSources.REGION_NAMES) {
            _state.value = _state.value.copy(historyError = "Set your region letter (A-P) first")
            return
        }
        historyJob?.cancel()
        historyJob = viewModelScope.launch {
            val log = mutableListOf<String>()
            _state.value = _state.value.copy(historyBusy = "Loading your days…", historyError = null)
            try {
                val saved = withContext(Dispatchers.IO) { TariffHistoryDaysStore(context).load() }
                    ?: throw Exception("Run the analysis first: it saves the days this prices")
                if (saved.days.isEmpty()) throw Exception("The last analysis run had no days")
                val start = java.time.LocalDate.parse(saved.days.keys.min())
                val stop = java.time.LocalDate.parse(saved.days.keys.max())
                _state.value = _state.value.copy(historyBusy = "Fetching rate history $start to $stop…")
                val src = TariffHistorySource(cacheRoot, log = { synchronized(log) { log.add(it) } })
                val tariffs = src.fetch(region, start, stop)
                _state.value = _state.value.copy(historyBusy = "Pricing ${saved.days.size} days…")
                val result = withContext(Dispatchers.Default) {
                    TariffHistory.priceHistory(saved.days, saved.periods, tariffs)
                }
                val days = result.kwh.values.sumOf { it.days }
                val skipped = if (result.skipped.isEmpty()) "" else
                    "; left out: " + result.skipped.entries.joinToString { "${it.key} ${it.value}" }
                log.add("${tariffs.size} tariffs: ${src.calls} requests, ${src.hits} from cache")
                _state.value = _state.value.copy(historyBusy = null, history = result, log = log.toList(),
                    historyNote = "$days days, $start to $stop (analysis run ${saved.savedAt})$skipped")
            } catch (e: Exception) {
                _state.value = _state.value.copy(historyBusy = null, historyError = e.message ?: "Failed",
                    log = log.toList())
            }
        }
    }

    private fun reprice() {
        val p = _state.value.profile ?: return
        if (tariffs.isEmpty()) return
        val imports = TariffCompare.compare(tariffs, p.currentImport, p.house, p.ev)
        val exports = TariffCompare.compareExports(tariffs, p.currentExport, p.export)
        _state.value = _state.value.copy(imports = imports, exports = exports,
            pairs = TariffCompare.bestPairs(imports, exports, 8))
        // Gas: the E.ON meter's seasonally weighted year, priced at every gas offer and the latest
        // bill (parsing the readings takes a moment, so off the main thread).
        val eonDir = File(context.filesDir, "eonData")
        val offers = tariffs
        viewModelScope.launch {
            val (gasYear, gasRows, overall) = withContext(Dispatchers.Default) {
                val gasDaily = runCatching { EonClient.loadDailyKwh(eonDir, "gas") }.getOrDefault(emptyMap())
                if (gasDaily.isEmpty()) return@withContext Triple(null, emptyList<GasRow>(), emptyList<OverallRow>())
                val year = TariffCompare.annualGas(gasDaily)
                val bill = EonClient.loadCharges(eonDir).filter { it.fuel.equals("Gas", true) && it.rate != null && it.kwh > 0 }
                    .maxByOrNull { it.start }
                val current = bill?.let { TariffCompare.gasOffer("Current", "gas billed from ${it.start}", "CURRENT_GAS",
                    "eonData", "", it.rate, it.standing) }
                val rows = TariffCompare.compareGas(offers, current, year.kwh)
                Triple(year, rows, TariffCompare.overall(TariffCompare.bestPairs(imports, exports, Int.MAX_VALUE), rows, year.kwh))
            }
            _state.value = _state.value.copy(gasYear = gasYear, gasRows = gasRows, overall = overall)
        }
    }

    class Factory(private val context: Context) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            TariffsViewModel(context.applicationContext) as T
    }
}
