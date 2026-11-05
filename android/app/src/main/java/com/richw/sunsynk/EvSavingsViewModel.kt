package com.richw.sunsynk

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.richw.sunsynk.analysis.EvSavings
import com.richw.sunsynk.analysis.EvSavingsConfig
import com.richw.sunsynk.analysis.EvSavingsResult
import com.richw.sunsynk.api.FuelPriceSource
import com.richw.sunsynk.api.FuelStation
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.time.LocalDate

data class EvSavingsUiState(
    val result: EvSavingsResult? = null,
    val config: EvSavingsConfig = EvSavingsConfig(),
    val stations: List<FuelStation> = emptyList(),
    val daysSavedAt: String? = null,        // the analysis run the charging comes from
    val petrolUpdated: Long = 0,            // when petrol prices were last fetched (epoch ms)
    val busy: String? = null,
    val error: String? = null,
)

/**
 * EV tab: home charging (the last analysis run's days, tariffDays.json, plus Indra days before
 * them and the configured before-charger months) vs a petrol car doing the same miles at local
 * supermarket prices ([FuelPriceSource]). Recomputed whenever a new analysis run is saved or
 * petrol prices are refreshed (automatically when over 12 hours old).
 */
class EvSavingsViewModel(private val context: Context) : ViewModel() {
    private val prefs = PrefsStore(context)
    private val configStore = ConfigStore(context)
    private val fuel = FuelPriceSource(File(context.filesDir, "fuelData"))
    private var job: Job? = null

    private val _state = MutableStateFlow(EvSavingsUiState(config = configStore.load().evSavings, stations = prefs.fuelStations))
    val state = _state.asStateFlow()

    init { reload(fetchIfStale = true) }

    /** Recompute from what's saved; with [fetchIfStale], top up petrol prices first when old. */
    fun reload(fetchIfStale: Boolean = false) {
        val stale = System.currentTimeMillis() - fuel.lastUpdated() > FuelPriceSource.MAX_AGE_HOURS * 3600_000
        if (fetchIfStale && stale) refresh() else launch(null) { compute(fetch = false) }
    }

    /** The tab became visible: pick up a newer analysis run. */
    fun onShown() {
        val saved = File(context.filesDir, "tariffDays.json").lastModified()
        val shownAt = _state.value.daysSavedAt
        if (shownAt == null || saved > lastDaysModified) reload(fetchIfStale = true)
    }

    fun refresh(force: Boolean = false) = launch("Fetching petrol prices…") { compute(fetch = true, force = force) }

    fun findStations(postcode: String, radiusMiles: Double) = launch("Finding supermarket forecourts…") {
        val (lat, lon) = fuel.locate(postcode.trim())
        val found = fuel.findStations(lat, lon, radiusMiles)
        if (found.isEmpty()) throw Exception("No Tesco, Asda, Sainsbury's or Morrisons forecourts within $radiusMiles miles")
        prefs.fuelStations = found
        _state.value = _state.value.copy(stations = found, busy = "Fetching prices for ${found.size} forecourts…")
        compute(fetch = true)
    }

    fun saveConfig(cfg: EvSavingsConfig) {
        configStore.save(configStore.load().copy(evSavings = cfg))
        _state.value = _state.value.copy(config = cfg)
        launch(null) { compute(fetch = false) }
    }

    private var lastDaysModified = 0L

    private fun launch(busy: String?, block: suspend () -> Unit) {
        job?.cancel()
        job = viewModelScope.launch {
            _state.value = _state.value.copy(busy = busy, error = null)
            try {
                block()
                _state.value = _state.value.copy(busy = null)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.value = _state.value.copy(busy = null, error = e.message ?: e.javaClass.simpleName)
            }
        }
    }

    private suspend fun compute(fetch: Boolean, force: Boolean = false) {
        val stations = _state.value.stations
        val cfg = _state.value.config
        val (national, histories) = withContext(Dispatchers.IO) {
            val csv = if (fetch) fuel.nationalCsv(force) else runCatching { File(context.filesDir, "fuelData/national.csv").readText() }.getOrDefault("")
            val h = stations.associate { s ->
                s.id to (if (fetch) runCatching { fuel.history(s.id, force) }.getOrElse { fuel.cachedHistory(s.id) }
                         else fuel.cachedHistory(s.id))
            }
            Pair(EvSavings.parseNational(csv), h)
        }
        val daysFile = File(context.filesDir, "tariffDays.json")
        val (result, savedAt) = withContext(Dispatchers.Default) {
            val history = TariffHistoryDaysStore(context).load()
            val days = if (history == null) emptyList()
                else EvSavings.evDays(history, EvSavings.indraDailyKwh(File(context.filesDir, "indraData")))
            val from = (histories.values.flatten().minOfOrNull { it.first }?.atZone(EvSavings.ZONE)?.toLocalDate())
                ?: LocalDate.now()
            val local = EvSavings.localDaily(histories, from, LocalDate.now())
            Pair(EvSavings.calculate(days, cfg, national, local, stations.size), history?.savedAt)
        }
        lastDaysModified = daysFile.lastModified()
        _state.value = _state.value.copy(result = result, daysSavedAt = savedAt ?: "", petrolUpdated = fuel.lastUpdated(),
            error = if (savedAt == null) "Run the analysis first: it saves the charging days this uses" else null)
    }

    class Factory(private val context: Context) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            EvSavingsViewModel(context.applicationContext) as T
    }
}
