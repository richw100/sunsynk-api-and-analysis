package com.richw.sunsynk

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.richw.sunsynk.analysis.ExportRow
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
                cacheRoot.listFiles()?.filter { it.name < java.time.LocalDate.now().minusDays(7).toString() }
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

    private fun reprice() {
        val p = _state.value.profile ?: return
        if (tariffs.isEmpty()) return
        val imports = TariffCompare.compare(tariffs, p.currentImport, p.house, p.ev)
        val exports = TariffCompare.compareExports(tariffs, p.currentExport, p.export)
        _state.value = _state.value.copy(imports = imports, exports = exports,
            pairs = TariffCompare.bestPairs(imports, exports, 8))
    }

    class Factory(private val context: Context) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            TariffsViewModel(context.applicationContext) as T
    }
}
