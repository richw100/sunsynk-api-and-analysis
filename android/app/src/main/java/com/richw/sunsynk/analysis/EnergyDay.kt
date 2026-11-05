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

private val HM: DateTimeFormatter = DateTimeFormatter.ofPattern("H:mm")

/** (time, W) records of one Sunsynk day series, as sent (not gap-filled). */
fun parseRecords(data: JsonObject): List<Pair<LocalTime, Double>> =
    data.getAsJsonArray("records").map { r ->
        val rec = r.asJsonObject
        Pair(LocalTime.parse(rec.get("time").asString, HM), rec.get("value").asString.toDouble())
    }

/** Accumulates peak/off-peak Wh from already gap-filled 5-minute (time, W) records. */
class IntervalSummary(
    val label: String,
    records: List<Pair<LocalTime, Double>>,
    battery: VirtualBattery?,
    offPeakStart: String,
    offPeakStop: String,
    private val isLoad: Boolean = false,
    private val correction: ExportCorrection = ExportCorrection(),
) {
    constructor(
        data: JsonObject, battery: VirtualBattery?, offPeakStart: String, offPeakStop: String,
        isLoad: Boolean = false, correction: ExportCorrection = ExportCorrection(),
    ) : this(data.get("label").asString, fillGaps(parseRecords(data)), battery, offPeakStart, offPeakStop,
        isLoad, correction)

    var peak: Double = 0.0
    var peakExport: Double = 0.0
    var offPeak: Double = 0.0
    var offPeakExport: Double = 0.0
    var offPeakPercentage: Double = 0.0

    val parsed: MutableList<Pair<LocalTime, Double>> = if (isLoad) mutableListOf() else mutableListOf()

    init {
        val start = LocalTime.parse(offPeakStart, HM)
        val stop = LocalTime.parse(offPeakStop, HM)
        var recharged = false
        var batteryRanOut = false

        for ((time, watts) in records) {
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
    evWatts: Map<String, Double>? = null,
) {
    val pv: IntervalSummary
    val grid: IntervalSummary
    val load: IntervalSummary
    /** EV charging beyond the CT ({"HH:mm": W}, see EvCharging), already in grid/load. */
    val ev: IntervalSummary
    /** Wh of EV charging met by PV the CT counted as export. */
    var evFromPv = 0.0
        private set
    private var batteryGrid: IntervalSummary? = null
    /** Gap-filled net Grid records with EV added, and the EV draw by time (for half-hour slots). */
    private var gridRecords: List<Pair<LocalTime, Double>> = emptyList()
    private var evByTime: Map<LocalTime, Double> = emptyMap()
    private var pvRecords: List<Pair<LocalTime, Double>> = emptyList()
    private var ctGridRecords: List<Pair<LocalTime, Double>> = emptyList()   // CT-side net grid, before EV
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

        // EV charging (mirrors Python EnergyDay ev_watts): added to Load and the net Grid meter
        // after gap-filling, so it soaks up CT-measured export first and the rest is import;
        // slots Sunsynk didn't sample get a record carrying only the EV draw. The battery
        // replay keeps the CT-side Grid (a battery there couldn't feed the car).
        val ev = (evWatts ?: emptyMap()).filterValues { it > 0 }
            .mapKeys { LocalTime.parse(it.key, HM) }
        fun addEv(records: List<Pair<LocalTime, Double>>): List<Pair<LocalTime, Double>> {
            if (ev.isEmpty()) return records
            val seen = records.map { it.first }.toSet()
            return (records.map { (t, w) -> Pair(t, w + (ev[t] ?: 0.0)) } +
                ev.filterKeys { it !in seen }.map { (t, w) -> Pair(t, w) }).sortedBy { it.first }
        }
        evByTime = ev
        this.ev = IntervalSummary("EV", ev.entries.sortedBy { it.key }.map { Pair(it.key, it.value) },
            null, offPeakStart, offPeakStop)

        val infos = data.getAsJsonArray("infos")
        var pvTmp: IntervalSummary? = null
        var gridTmp: IntervalSummary? = null
        var loadTmp: IntervalSummary? = null
        for (item in infos) {
            val obj = item.asJsonObject
            val records = fillGaps(parseRecords(obj))
            when (obj.get("label").asString) {
                "PV" -> { pvRecords = records; pvTmp = IntervalSummary("PV", records, battery, offPeakStart, offPeakStop) }
                "Grid" -> {
                    evFromPv = records.sumOf { (t, w) -> minOf(ev[t] ?: 0.0, maxOf(0.0, -w)) } / 12.0
                    ctGridRecords = records
                    gridRecords = addEv(records)
                    gridTmp = IntervalSummary("Grid", gridRecords, if (ev.isEmpty()) battery else null,
                        offPeakStart, offPeakStop, isLoad = true, correction = exportCorrection)
                    if (ev.isNotEmpty()) batteryGrid = IntervalSummary("Grid", records, battery,
                        offPeakStart, offPeakStop, isLoad = true, correction = exportCorrection)
                }
                "Load" -> loadTmp = IntervalSummary("Load", addEv(records), battery, offPeakStart, offPeakStop)
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
        for ((timeDt, wh) in (batteryGrid ?: grid).parsed) {
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

    /**
     * Import Wh per local half-hour (48 slots) as (total, ev), mirroring Python
     * EnergyDay.import_slots(): an import sample counts in full, an export sample credits the
     * charger standby against import (so total sums to [getCalcImport]); ev is the EV's share
     * of import, its draw less the CT-measured export it soaked up.
     */
    fun importSlots(): Pair<DoubleArray, DoubleArray> {
        val total = DoubleArray(48)
        val ev = DoubleArray(48)
        for ((t, value) in gridRecords) {
            val slot = t.hour * 2 + (if (t.minute >= 30) 1 else 0)
            if (value > 0) total[slot] += value / 12.0
            else total[slot] -= exportCorrection.split(-value / 12.0).second
            val w = evByTime[t] ?: 0.0
            if (w > 0) {
                val ct = value - w
                ev[slot] += (w - minOf(w, maxOf(0.0, -ct))) / 12.0
            }
        }
        return Pair(total, ev)
    }

    /** Corrected export Wh per local half-hour (48 slots); sums to [getCalcExport]. */
    /**
     * Where the PV went, Wh per local half-hour, as (house, ev): ``house`` = PV the house used
     * itself (generation minus what the CT saw exported), ``ev`` = PV the car took (the CT export
     * the EV charger soaked up, which never reached the meter). Used to value the panels.
     */
    fun solarSlots(): Pair<DoubleArray, DoubleArray> {
        val house = DoubleArray(48)
        val ev = DoubleArray(48)
        fun slot(t: LocalTime) = t.hour * 2 + (if (t.minute >= 30) 1 else 0)
        for ((t, w) in pvRecords) if (w > 0) house[slot(t)] += w / 12.0
        for ((t, w) in ctGridRecords) {
            if (w >= 0) continue
            val exported = -w
            house[slot(t)] -= exported / 12.0
            ev[slot(t)] += minOf(evByTime[t] ?: 0.0, exported) / 12.0
        }
        for (k in 0 until 48) house[k] = maxOf(0.0, house[k])
        return Pair(house, ev)
    }

    fun exportSlots(): DoubleArray {
        val out = DoubleArray(48)
        for ((t, value) in gridRecords) {
            if (value < 0) out[t.hour * 2 + (if (t.minute >= 30) 1 else 0)] +=
                exportCorrection.split(-value / 12.0).first
        }
        return out
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
    fun getCalcEv() = ev.peak + ev.offPeak
    fun getCalcEvPeak() = ev.peak
    fun getCalcEvOffPeak() = ev.offPeak
}

private fun emptyInfoObj(label: String): JsonObject {
    val obj = JsonObject()
    obj.addProperty("label", label)
    obj.add("records", com.google.gson.JsonArray())
    return obj
}
