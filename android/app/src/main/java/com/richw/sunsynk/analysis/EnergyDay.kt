package com.richw.sunsynk.analysis

import com.google.gson.JsonObject
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

const val INTERVAL_MIN = 5L        // Sunsynk day records are nominally 5 minutes apart
const val MAX_FILL_GAP_MIN = 30L   // gaps up to this are dropped samples; larger ones are left alone

/**
 * Records sorted by time with dropped 5-minute samples forward-filled (mirrors Python
 * energyday._fill_gaps). The API omits a few samples a day (10-20 minute gaps); since
 * each record counts as 5 minutes of energy (value/12), a missing one would otherwise
 * count as zero and under-count every total by ~2.5%. Gaps longer than maxGapMin
 * (e.g. coarse fixtures) are not filled.
 */
fun fillGaps(records: List<Pair<LocalTime, Double>>, maxGapMin: Long = MAX_FILL_GAP_MIN): List<Pair<LocalTime, Double>> {
    val ordered = records.sortedBy { it.first }
    val filled = ArrayList<Pair<LocalTime, Double>>(ordered.size + 16)
    for (i in ordered.indices) {
        val (t, v) = ordered[i]
        filled.add(ordered[i])
        if (i + 1 == ordered.size) break
        val gap = ChronoUnit.MINUTES.between(t, ordered[i + 1].first)
        if (gap in (INTERVAL_MIN + 1)..maxGapMin) {
            for (k in 1 until gap / INTERVAL_MIN) filled.add(Pair(t.plusMinutes(k * INTERVAL_MIN), v))
        }
    }
    return filled
}

/**
 * How measured export (negative Grid samples) is adjusted before use (mirrors Python
 * energyday.ExportCorrection). [gain] scales the inverter's export reading toward the
 * supplier's meter (the shortfall is simply lost, never credited). [standbyW] is a load
 * between the inverter CT and the meter (EV charger standby) that PV covers while
 * exporting: removed from export and credited as self-consumed load (subtracted from
 * import). Defaults (1.0, 27.6 W = 2.3 Wh per 5 min) reproduce the original fixed rule.
 */
data class ExportCorrection(val gain: Double = 1.0, val standbyW: Double = 27.6) {
    /** (corrected export Wh, standby Wh credited as load) for one 5-minute sample. */
    fun split(exportWh: Double): Pair<Double, Double> {
        val exported = exportWh * gain
        val standby = minOf(exported, standbyW / 12.0)
        return Pair(exported - standby, standby)
    }
}

class IntervalSummary(
    data: JsonObject,
    battery: VirtualBattery?,
    offPeakStart: String,
    offPeakStop: String,
    private val isLoad: Boolean = false,
    private val correction: ExportCorrection = ExportCorrection(),
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

        val raw = data.getAsJsonArray("records").map { r ->
            val rec = r.asJsonObject
            Pair(LocalTime.parse(rec.get("time").asString, fmt), rec.get("value").asString.toDouble())
        }
        for ((time, watts) in fillGaps(raw)) {
            val value = watts / 12.0
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
            val (export, standby) = correction.split(-value)
            offPeakExport += export
            offPeak -= standby
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
            val (export, standby) = correction.split(-value)
            peakExport += export
            peak -= standby
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
    private val exportCorrection: ExportCorrection = ExportCorrection(),
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
                "Grid" -> gridTmp = IntervalSummary(obj, battery, offPeakStart, offPeakStop, isLoad = true,
                    correction = exportCorrection)
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
                    battery.pvCharge(exportCorrection.split(-wh).first, timeDt)
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
