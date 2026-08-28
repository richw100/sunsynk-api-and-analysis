package com.richw.sunsynk.analysis

import com.google.gson.JsonObject
import java.time.LocalTime
import java.time.format.DateTimeFormatter

class IntervalSummary(
    data: JsonObject,
    battery: VirtualBattery?,
    offPeakStart: String,
    offPeakStop: String,
    private val isLoad: Boolean = false,
) {
    val label: String = data.get("label").asString
    var peak: Double = 0.0
    var peakExport: Double = 0.0
    var offPeak: Double = 0.0
    var offPeakExport: Double = 0.0
    var offPeakPercentage: Double = 0.0

    val parsed: MutableList<Pair<LocalTime, Double>> = if (isLoad) mutableListOf() else mutableListOf()

    init {
        val fmt = DateTimeFormatter.ofPattern("H:mm")
        val start = LocalTime.parse(offPeakStart, fmt)
        val stop = LocalTime.parse(offPeakStop, fmt)
        var recharged = false
        var batteryRanOut = false

        val records = data.getAsJsonArray("records")
        for (r in records) {
            val rec = r.asJsonObject
            val time = LocalTime.parse(rec.get("time").asString, fmt)
            val value = rec.get("value").asString.toDouble() / 12.0
            if (isLoad) parsed.add(Pair(time, value))

            if (!time.isBefore(start) && time.isBefore(stop)) {
                recharged = processOffPeak(value, battery, isLoad, recharged)
            } else {
                if (processPeak(value, time, battery, isLoad)) batteryRanOut = true
            }
        }

        if (battery != null && batteryRanOut) battery.setRanOut()

        val total = offPeak + peak
        if (total > 0) offPeakPercentage = offPeak / total
    }

    private fun processOffPeak(value: Double, battery: VirtualBattery?, isLoad: Boolean, recharged: Boolean): Boolean {
        if (!recharged) {
            if (battery != null && isLoad) battery.recharge()
        }
        if (value > 0) {
            offPeak += value
        } else {
            val extraLoad = minOf(-value, 2.3)
            offPeakExport += -value - extraLoad
            offPeak -= extraLoad
        }
        return true
    }

    private fun processPeak(value: Double, time: LocalTime, battery: VirtualBattery?, isLoad: Boolean): Boolean {
        return if (value > 0) {
            peak += value
            if (battery != null && isLoad) {
                battery.utilise(value, time) > 0
            } else false
        } else {
            val extraLoad = minOf(-value, 2.3)
            val export = -value - extraLoad
            peakExport += export
            peak -= extraLoad
            if (battery != null && isLoad) {
                battery.pvCharge(export, time)
            }
            false
        }
    }
}

class EnergyDay(
    data: JsonObject,
    date: String,
    month: EnergyMonth,
    battery: VirtualBattery?,
    offPeakStart: String,
    offPeakStop: String,
) {
    val pv: IntervalSummary
    val grid: IntervalSummary
    val load: IntervalSummary
    val suppliedLoad: Double
    val suppliedImport: Double
    val suppliedPv: Double
    val suppliedExport: Double

    private val startDt: LocalTime
    private val stopDt: LocalTime

    init {
        val fmt = DateTimeFormatter.ofPattern("H:mm")
        startDt = LocalTime.parse(offPeakStart, fmt)
        stopDt = LocalTime.parse(offPeakStop, fmt)

        val infos = data.getAsJsonArray("infos")
        var pvTmp: IntervalSummary? = null
        var gridTmp: IntervalSummary? = null
        var loadTmp: IntervalSummary? = null
        for (item in infos) {
            val obj = item.asJsonObject
            when (obj.get("label").asString) {
                "PV" -> pvTmp = IntervalSummary(obj, battery, offPeakStart, offPeakStop)
                "Grid" -> gridTmp = IntervalSummary(obj, battery, offPeakStart, offPeakStop, isLoad = true)
                "Load" -> loadTmp = IntervalSummary(obj, battery, offPeakStart, offPeakStop)
            }
        }
        pv = pvTmp ?: IntervalSummary(emptyInfoObj("PV"), null, offPeakStart, offPeakStop)
        grid = gridTmp ?: IntervalSummary(emptyInfoObj("Grid"), null, offPeakStart, offPeakStop, isLoad = true)
        load = loadTmp ?: IntervalSummary(emptyInfoObj("Load"), null, offPeakStart, offPeakStop)

        suppliedLoad = month.getLoad()?.records?.firstOrNull { it.time == date }?.value?.toDoubleOrNull() ?: 0.0
        suppliedImport = month.getImport()?.records?.firstOrNull { it.time == date }?.value?.toDoubleOrNull() ?: 0.0
        suppliedPv = month.getPv()?.records?.firstOrNull { it.time == date }?.value?.toDoubleOrNull() ?: 0.0
        suppliedExport = month.getExport()?.records?.firstOrNull { it.time == date }?.value?.toDoubleOrNull() ?: 0.0
    }

    fun runBattery(battery: VirtualBattery) {
        var recharged = false
        var batteryRanOut = false
        for ((timeDt, wh) in grid.parsed) {
            if (!timeDt.isBefore(startDt) && timeDt.isBefore(stopDt)) {
                if (!recharged) {
                    battery.recharge()
                    recharged = true
                }
            } else {
                if (wh > 0) {
                    if (battery.utilise(wh, timeDt) > 0) batteryRanOut = true
                } else {
                    val extraLoad = minOf(-wh, 2.3)
                    battery.pvCharge(-wh - extraLoad, timeDt)
                }
            }
        }
        if (batteryRanOut) battery.setRanOut()
    }

    fun getCalcExport() = grid.peakExport + grid.offPeakExport
    fun getCalcImport() = grid.peak + grid.offPeak
    fun getCalcExportPeak() = grid.peakExport
    fun getCalcImportPeak() = grid.peak
    fun getCalcExportOffPeak() = grid.offPeakExport
    fun getCalcImportOffPeak() = grid.offPeak
    fun getCalcPv() = pv.peak + pv.offPeak
    fun getCalcLoad() = load.peak + load.offPeak
    fun getCalcLoadPeak() = load.peak
    fun getCalcLoadOffPeak() = load.offPeak
}

private fun emptyInfoObj(label: String): JsonObject {
    val obj = JsonObject()
    obj.addProperty("label", label)
    obj.add("records", com.google.gson.JsonArray())
    return obj
}
