package com.richw.sunsynk

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.google.gson.JsonParser
import com.richw.sunsynk.analysis.EnergyDay
import com.richw.sunsynk.analysis.EnergyMonth
import com.richw.sunsynk.analysis.EnergyPrices
import com.richw.sunsynk.analysis.EvCharging
import com.richw.sunsynk.api.FuseClient
import com.richw.sunsynk.analysis.PriceData
import com.richw.sunsynk.analysis.SlotProjection
import com.richw.sunsynk.analysis.TariffCompare
import com.richw.sunsynk.analysis.TariffProfile
import com.richw.sunsynk.analysis.batteryWarnings
import com.richw.sunsynk.analysis.round2
import com.richw.sunsynk.api.SunsynkEnergyClient
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.time.LocalDate
import java.time.format.DateTimeFormatter

sealed class AnalysisState {
    object Idle : AnalysisState()
    data class Running(val progress: String) : AnalysisState()
    data class Done(val result: AnalysisResult) : AnalysisState()
    data class Error(val message: String) : AnalysisState()
}

class AnalysisViewModel(private val context: Context) : ViewModel() {

    private val _state = MutableStateFlow<AnalysisState>(AnalysisState.Idle)
    val state = _state.asStateFlow()

    private var job: Job? = null

    fun runAnalysis(settings: AppSettings, username: String, password: String) {
        job?.cancel()
        job = viewModelScope.launch {
            _state.value = AnalysisState.Running("Starting…")
            try {
                val result = doAnalysis(settings, username, password)
                _state.value = AnalysisState.Done(result)
            } catch (e: CancellationException) {
                _state.value = AnalysisState.Idle
            } catch (e: Exception) {
                _state.value = AnalysisState.Error(e.message ?: "Unknown error")
            }
        }
    }

    fun cancel() {
        job?.cancel()
        _state.value = AnalysisState.Idle
    }

    fun clearResult() {
        _state.value = AnalysisState.Idle
    }

    private suspend fun doAnalysis(settings: AppSettings, username: String, password: String): AnalysisResult =
        withContext(Dispatchers.IO) {
            val cacheDir = File(context.getExternalFilesDir(null), "inverterData").also { it.mkdirs() }
            val client = SunsynkEnergyClient(username, password, cacheDir = cacheDir)

            emit("Logging in…")
            client.login()

            emit("Fetching inverter list…")
            val inverters = client.getInverters()
            if (inverters.isEmpty()) throw Exception("No inverters found on account.")

            val inverter = inverters.first()
            val plantId = inverter.getAsJsonObject("plant").get("id").asString

            val currentMonth = LocalDate.now().format(DateTimeFormatter.ofPattern("yyyy-MM"))
            val dateFmt = DateTimeFormatter.ofPattern("yyyy-MM-dd")

            // Only months are cached in memory (a handful, cheap). Day JSON trees are
            // large (~1 MB parsed each); caching every day across the full history
            // blew the phone heap (OutOfMemoryError surfacing as a Gson parse failure).
            // Day data is already cached on disk by SunsynkEnergyClient, so we re-read
            // and rebuild each EnergyDay on demand and let it be garbage-collected.
            val monthCache = mutableMapOf<String, EnergyMonth>()

            // EV charging beyond the CT (Indra), and the Fuse meter's daily export, if downloaded.
            val dataSources = DataSourceStore(context)
            val evProfiles = if (settings.isEvChargingOn()) {
                emit("Loading EV charging…")
                EvCharging.loadProfiles(dataSources.indraDir)
            } else emptyMap()
            val meterExport = FuseClient.loadDailyExportKwh(dataSources.fuseDir)
            val calcExportByDate = sortedMapOf<String, Double>()   // first pass only
            val slots = SlotProjection(settings.evBasisFrom)          // first pass only, for the Tariffs tab
            val historyDays = HashMap<String, IntArray>()             // first pass: each day's half-hours

            val priceFiles = settings.energyPrices
            val batteryConfigs = settings.batteryConfigs
            val multiBattery = batteryConfigs.size > 1

            val allBatteryResults = mutableListOf<Pair<String, List<EnergyPrices>>>()

            for (batteryConfig in batteryConfigs) {
                val bcLabel = batteryConfig.label.ifBlank {
                    if (multiBattery) "${batteryConfig.batterySize}Wh" else ""
                }
                val batteryEnabled = batteryConfig.isEnabled()
                val allPrices = mutableListOf<EnergyPrices>()

                for (priceFile in priceFiles) {
                    emit("Loading $priceFile…")
                    val priceJson = loadAssetJson(priceFile)
                    val pfLabel = run {
                        val base = priceFile.removeSuffix(".json")
                        base.removePrefix("_EnergyPrices-").removePrefix("_EnergyPrices").ifBlank { base }
                    }
                    val label = when {
                        multiBattery && priceFiles.size > 1 -> "$bcLabel / $pfLabel"
                        multiBattery -> bcLabel
                        else -> pfLabel
                    }

                    val tmpPrice = PriceData()
                    val battery = batteryConfig.toVirtualBattery(tmpPrice)
                    val prices = EnergyPrices(
                        pricesJson = priceJson,
                        origBattery = battery,
                        originalPrice = settings.originalPrice,
                        label = label,
                        offPeakBaselineKwh = settings.offPeakBaseline,
                        offPeakShiftEnabled = settings.isOffPeakShiftOn(),
                        batteryEnabled = batteryEnabled,
                        batteryPrice = batteryConfig.batteryPrice,
                        evBasisFrom = settings.evBasisFrom,
                    )
                    val firstPass = allBatteryResults.isEmpty() && allPrices.isEmpty()

                    var yearCount = settings.scanFromYear
                    var hasYear = true
                    val startDate = settings.startDate
                    val stopDate = settings.stopDate
                    var processDate = startDate.isEmpty()

                    while (hasYear && yearCount < 2040) {
                        hasYear = false
                        for (month in 1..12) {
                            val monthToCheck = "%04d-%02d".format(yearCount, month)
                            if (monthToCheck > currentMonth) break

                            emit("Processing $monthToCheck…")

                            val energyMonth = monthCache.getOrPut(monthToCheck) {
                                val raw = client.getEnergyMonthRaw(plantId, monthToCheck)
                                EnergyMonth(raw.getAsJsonObject("data"))
                            }

                            val loadItems = energyMonth.getLoad() ?: continue
                            if (loadItems.records.isEmpty()) continue
                            hasYear = true

                            for (dayRecord in loadItems.records) {
                                val dayDate = dayRecord.time
                                prices.checkDate(dayDate)

                                val checkDate = LocalDate.parse(dayDate, dateFmt)
                                if (startDate.isNotEmpty()) {
                                    if (checkDate.isAfter(LocalDate.parse(startDate, dateFmt))) processDate = true
                                }
                                if (stopDate.isNotEmpty()) {
                                    if (checkDate.isAfter(LocalDate.parse(stopDate, dateFmt))) processDate = false
                                }

                                if (processDate) {
                                    val opStart = prices.priceData.currentOffPeakStart
                                    val opStop = prices.priceData.currentOffPeakStop

                                    val raw = client.getEnergyDayRaw(plantId, dayDate)
                                    val energyDay = EnergyDay(
                                        raw.getAsJsonObject("data"),
                                        dayDate, energyMonth,
                                        null, opStart, opStop,
                                        settings.exportCorrection.forDate(dayDate),
                                        evProfiles[dayDate],
                                    )
                                    energyDay.runBattery(prices.battery)
                                    prices.addData(energyDay)
                                    if (firstPass) {
                                        calcExportByDate[dayDate] = energyDay.getCalcExport() / 1000
                                        val (total, ev) = energyDay.importSlots()
                                        val exportSlots = energyDay.exportSlots()
                                        slots.addDay(dayDate, total, ev, exportSlots)
                                        historyDays[dayDate] = IntArray(144) { k ->
                                            Math.round(when {
                                                k < 48 -> total[k] - ev[k]
                                                k < 96 -> ev[k - 48]
                                                else -> exportSlots[k - 96]
                                            }).toInt()
                                        }
                                    }
                                }
                            }
                        }
                        yearCount++
                    }

                    prices.getGrandTotals()
                    if (firstPass && slots.days > 0) {
                        saveTariffProfile(prices, priceJson, slots)
                        TariffHistoryDaysStore(context).save(com.richw.sunsynk.analysis.HistoryDays(
                            java.time.LocalDateTime.now().withNano(0).toString().replace('T', ' '),
                            com.richw.sunsynk.analysis.TariffHistory.actualPeriods(priceJson), historyDays))
                    }
                    allPrices.add(prices)
                }

                allBatteryResults.add(Pair(bcLabel, allPrices))
            }

            emit("Building report…")

            val shared = calcExportByDate.keys.filter { it in meterExport }
            AnalysisResult(
                scenarios = allBatteryResults.map { (bcLabel, prices) ->
                    BatteryScenario(
                        label = bcLabel,
                        priceResults = prices.map { it.toResult() },
                    )
                },
                meter = if (shared.isEmpty()) null else MeterComparison(
                    days = shared.size, from = shared.first(), to = shared.last(),
                    inverterKwh = shared.sumOf { calcExportByDate[it]!! }.round2(),
                    meterKwh = shared.sumOf { meterExport[it]!! }.round2(),
                ),
                evDays = evProfiles.size,
            )
        }

    private fun EnergyPrices.toResult(): PriceResult {
        val t = getGrandTotals()
        val d = getDerived()
        val days = t["days"]!!.toInt()
        val lastSummary = aggregator.getLastSummary()
        val finalRemainder = lastSummary.getRemainder()
        val cumulative = (lastSummary.cumulativeSavings + finalRemainder).round2()
        val alt = lastSummary.altInvestValue.round2()
        val netRoiPct = if (alt > 0) (100 * cumulative / alt).round2() else 0.0
        val batterySavings = d["battery_savings"] as Double
        val perDay = if (days > 0) (batterySavings / days).round2() else 0.0
        val annualisedSolar = d["annualised_solar"] as Double
        val solarPaybackYears = d["solar_payback_years"] as? Double

        val warnings = buildList {
            val seen = mutableSetOf<String>()
            for (summary in aggregator.summaries) {
                for (w in batteryWarnings(origBattery, summary.priceData)) {
                    if (seen.add(w)) add(w)
                }
            }
        }

        return PriceResult(
            label = label,
            totals = EnergyTotals(
                days = days,
                calcExportKwh = t["total_calc_export"]!!,
                suppliedExportKwh = t["total_supplied_export"]!!,
                calcExportPeakKwh = t["total_calc_export_peak"]!!,
                calcExportOffPeakKwh = t["total_calc_export_off_peak"]!!,
                calcImportKwh = t["total_calc_import"]!!,
                suppliedImportKwh = t["total_supplied_import"]!!,
                calcImportPeakKwh = t["total_calc_import_peak"]!!,
                calcImportOffPeakKwh = t["total_calc_import_off_peak"]!!,
                calcPvKwh = t["total_calc_pv"]!!,
                suppliedPvKwh = t["total_supplied_pv"]!!,
                calcLoadKwh = t["total_calc_load"]!!,
                suppliedLoadKwh = t["total_supplied_load"]!!,
                annualImportPeakKwh = d["annual_import_peak_kwh"] as Double,
                annualImportOffPeakKwh = d["annual_import_off_peak_kwh"] as Double,
                annualImportPeakCost = d["annual_import_peak_cost"] as Double,
                annualImportOffPeakCost = d["annual_import_off_peak_cost"] as Double,
                annualStandingCharge = d["annual_standing_charge"] as Double,
            ),
            costs = CostData(
                totalCost = t["total_cost"]!!,
                totalCostWithoutSolar = t["total_cost_without_solar"]!!,
                segIncome = t["total_export_amount_calc"]!!,
                billSavingsIncSeg = d["bill_savings_inc_seg"] as Double,
            ),
            savings = SavingsData(
                calcSavingsExcSeg = d["calc_savings_exc_seg"] as Double,
                calcSavings = d["calc_savings"] as Double,
                calcRoiPct = d["calc_roi_pct"] as Double,
                cumulativeSavings = cumulative,
                altInvestValue = alt,
                netRoiPct = netRoiPct,
                offPeakShiftEnabled = offPeakShiftEnabled,
                offPeakImportKwh = t["total_calc_import_off_peak"]!!,
                offPeakExcessKwh = t["total_off_peak_excess"]!!,
                offPeakSavings = t["total_off_peak_excess_savings"]!!,
                annualisedSolar = annualisedSolar,
                solarPaybackYears = solarPaybackYears,
                solarPaybackRemainingYears = d["solar_payback_remaining_years"] as? Double,
                projectionNote = projectionNote(),
            ),
            ev = EvData(
                chargedKwh = (t["total_ev_peak"]!! + t["total_ev_off_peak"]!!).round2(),
                peakKwh = t["total_ev_peak"]!!,
                offPeakKwh = t["total_ev_off_peak"]!!,
                fromSolarKwh = t["total_ev_from_pv"]!!,
                importCost = t["total_ev_import_cost"]!!,
                lostExport = t["total_ev_lost_export"]!!,
                annualKwh = d["annual_ev_kwh"] as Double,
                annualCost = d["annual_ev_cost"] as Double,
                basisNote = evBasisNote(d),
            ),
            battery = BatteryData(
                enabled = batteryEnabled,
                chargeAmountKwh = t["battery_charge_amount"]!!,
                chargeCost = t["battery_cost_from_grid"]!!,
                nominalCost = t["battery_nominal_cost"]!!,
                lostExportCost = t["battery_lost_export_cost"]!!,
                exportCost = t["battery_export_cost"]!!,
                exportedKwh = t["battery_exported"]!!,
                daysRunOut = t["battery_days_run_out"]!!.toInt(),
                shortfallKwh = t["battery_extra_required"]!!,
                netSaving = batterySavings,
                perDay = perDay,
                annualised = d["annualised_battery"] as Double,
                batteryPrice = batteryPrice,
                batterySizeWh = origBattery.batterySize,
                paybackYears = d["payback_years"] as? Double,
                warnings = warnings,
            ),
        )
    }

    /** The first pass's half-hourly year (house + rebased EV import, export) for the Tariffs tab. */
    private fun saveTariffProfile(prices: EnergyPrices, priceJson: com.google.gson.JsonObject, slots: SlotProjection) {
        val d = prices.getDerived()
        val annualImport = (d["annual_import_peak_kwh"] as Double) + (d["annual_import_off_peak_kwh"] as Double)
        val (house, ev) = TariffCompare.annualProfile(slots, annualImport)
        val monthly = slots.monthly()
        TariffProfileStore(context).save(TariffProfile(
            savedAt = java.time.LocalDateTime.now().withNano(0).toString().replace('T', ' '),
            house = house, ev = ev, export = slots.annualExport().toList(),
            monthlyHouse = monthly.mapValues { it.value.first },
            monthlyExport = monthly.mapValues { it.value.second },
            monthlyEv = slots.monthlyEv(ev.sum()),
            projectedImportKwh = annualImport,
            seasonal = slots.seasonal,
            note = prices.projectionNote(),
            currentImport = TariffCompare.currentImport(priceJson),
            currentExport = TariffCompare.currentExport(priceJson),
        ))
    }

    private fun emit(msg: String) {
        _state.value = AnalysisState.Running(msg)
    }

    private fun loadAssetJson(filename: String): com.google.gson.JsonObject {
        val externalFile = java.io.File(context.getExternalFilesDir(null), "inverterData/$filename")
        if (externalFile.exists()) {
            return JsonParser.parseString(externalFile.readText()).asJsonObject
        }
        return context.assets.open("inverterData/$filename").bufferedReader().use {
            JsonParser.parseReader(it).asJsonObject
        }
    }

    class Factory(private val context: Context) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            AnalysisViewModel(context.applicationContext) as T
    }
}
