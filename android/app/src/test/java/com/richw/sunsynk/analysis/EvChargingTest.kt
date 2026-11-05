package com.richw.sunsynk.analysis

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.time.Instant
import java.time.ZoneOffset

/** Parity with Python tests/test_evcharging.py and the EV cases in tests/test_calculations.py. */
class EvChargingTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun session(id: String, start: String, end: String?, off: Double = 0.0, std: Double = 0.0,
                        events: List<Pair<String, String>> = emptyList()) = JsonObject().apply {
        addProperty("id", id); addProperty("start", start)
        if (end == null) add("end", com.google.gson.JsonNull.INSTANCE) else addProperty("end", end)
        add("subTotals", JsonArray().apply {
            add(JsonObject().apply { addProperty("name", "Off-Peak"); addProperty("energyImportedKwh", off) })
            add(JsonObject().apply { addProperty("name", "Standard"); addProperty("energyImportedKwh", std) })
        })
        add("events", JsonArray().apply {
            events.forEach { (a, b) -> add(JsonObject().apply { addProperty("start", a); addProperty("end", b) }) }
        })
    }

    private fun row(time: String, kw: Double) =
        JsonObject().apply { addProperty("time", time); addProperty("powerKw", kw); addProperty("tariffRateType", 3) }

    @Test
    fun telemetryHoldsEachSampleUntilTheNext() {
        val wh = EvCharging.telemetryWh(listOf(row("2026-10-02T23:00:00Z", 6.0), row("2026-10-02T23:01:00Z", 3.0),
            row("2026-10-02T23:31:00Z", 0.0))).map { it.second }
        assertEquals(listOf(100.0, 3000.0 * 300 / 3600, 0.0), wh)
    }

    @Test
    fun synthesiseOffPeakInWindowStandardFromPlugIn() {
        val s = session("a", "2026-10-02T16:00:00Z", "2026-10-03T07:00:00Z", off = 6.0, std = 1.0,
            events = listOf("2026-10-02T23:10:00Z" to "2026-10-03T04:55:00Z"))
        val minutes = EvCharging.synthesiseWh(s, 6.0)
        assertEquals(7000.0, minutes.sumOf { it.second }, 1e-6)
        val utc = minutes.map { it.first.atOffset(ZoneOffset.UTC) }
        assertEquals(10, utc.count { it.hour == 16 })
        val off = utc.filter { it.hour == 23 || it.hour == 0 }
        assertEquals(23, off.first().hour); assertEquals(10, off.first().minute)
        assertEquals(0, utc.max().hour)
    }

    @Test
    fun synthesiseOverflowSpreadOverSession() {
        val minutes = EvCharging.synthesiseWh(session("b", "2026-10-02T16:00:00Z", "2026-10-02T16:10:00Z", std = 2.0), 6.0)
        assertEquals(10, minutes.size)
        assertEquals(2000.0, minutes.sumOf { it.second }, 1e-6)
    }

    @Test
    fun synthesiseUnknownSplitChargesInTheScheduleWindow() {
        val s = session("u", "2025-07-01T22:03:00Z", "2025-07-02T07:58:00Z", std = 6.0,
            events = listOf("2025-07-01T22:05:00Z" to "2025-07-01T22:10:00Z",
                "2025-07-01T23:10:00Z" to "2025-07-02T05:55:00Z"))
        val minutes = EvCharging.synthesiseWh(s, 6.0, splitKnown = false)
        assertEquals(6000.0, minutes.sumOf { it.second }, 1e-6)
        assertEquals(Instant.parse("2025-07-01T23:10:00Z"), minutes.minOf { it.first })
        assertEquals(Instant.parse("2025-07-02T00:09:00Z"), minutes.maxOf { it.first })
        assertEquals(22, EvCharging.synthesiseWh(s, 6.0).minOf { it.first }.atOffset(ZoneOffset.UTC).hour)
    }

    @Test
    fun synthesiseUnknownSplitWithoutWindowStartsAtLocalMidnight() {
        val overnight = EvCharging.synthesiseWh(
            session("n", "2025-06-15T13:28:00Z", "2025-06-16T06:58:00Z", std = 9.0), 6.0, splitKnown = false)
        assertEquals(Instant.parse("2025-06-15T23:00:00Z"), overnight.minOf { it.first })
        assertEquals(90, overnight.size)
        val nightOnly = EvCharging.synthesiseWh(
            session("o", "2025-01-15T20:00:00Z", "2025-01-16T06:30:00Z", std = 3.0), 6.0, splitKnown = false)
        assertEquals(0, nightOnly.minOf { it.first }.atOffset(ZoneOffset.UTC).hour)
    }

    @Test
    fun synthesiseUnknownSplitSmallSessionIsASolarTopUp() {
        val s = session("s", "2025-06-21T14:35:00Z", "2025-06-23T06:56:00Z", std = 3.0,
            events = listOf("2025-06-21T22:05:00Z" to "2025-06-21T22:10:00Z"))
        val minutes = EvCharging.synthesiseWh(s, 6.0, splitKnown = false)
        assertEquals(3000.0, minutes.sumOf { it.second }, 1e-6)
        assertEquals((6 until 18).toSet(), minutes.map { it.first.atOffset(ZoneOffset.UTC).hour }.toSet())
        assertEquals(205 + 720 + 56, minutes.size)
        assertEquals(1, minutes.map { Math.round(it.second * 1e9) }.toSet().size)
        val daytime = EvCharging.synthesiseWh(
            session("d", "2025-06-11T08:46:00Z", "2025-06-11T10:26:00Z", std = 1.0), 6.0, splitKnown = false)
        assertEquals(100, daytime.size)
    }

    @Test
    fun loadProfilesTreatsSessionsBeforeTheFirstOffPeakOneAsScheduled() {
        val dir = tmp.root
        val window = { d: String, e: String -> listOf("${d}T23:10:00Z" to "${e}T05:55:00Z") }
        dir.resolve("session-e.json").writeText(session("e", "2025-07-01T22:03:00Z", "2025-07-02T07:58:00Z",
            std = 1.0, events = window("2025-07-01", "2025-07-02")).toString())
        dir.resolve("session-l.json").writeText(session("l", "2025-08-01T22:03:00Z", "2025-08-02T07:58:00Z",
            off = 1.0, std = 0.5, events = window("2025-08-01", "2025-08-02")).toString())
        val prof = EvCharging.loadProfiles(dir, 6.0)
        assertEquals(setOf("00:10", "00:15"), prof["2025-07-02"]!!.keys)
        assertTrue("2025-07-01" !in prof)
        assertTrue("23:00" in prof["2025-08-01"]!! && "00:10" in prof["2025-08-02"]!!)
    }

    @Test
    fun loadProfilesPrefersTelemetryAndUsesLocalTime() {
        val dir = tmp.root
        dir.resolve("session-t.json").writeText(session("t", "2026-07-01T22:58:00Z", "2026-07-01T23:10:00Z", off = 1.0).toString())
        dir.resolve("telemetry-t.json").writeText(JsonArray().apply {
            (0 until 5).forEach { add(row("2026-07-01T23:0$it:00Z", 6.0)) }
        }.toString())
        dir.resolve("session-s.json").writeText(session("s", "2026-01-10T12:00:00Z", "2026-01-10T13:00:00Z", off = 0.5).toString())
        dir.resolve("session-open.json").writeText(session("open", "2026-01-11T12:00:00Z", null).toString())
        val prof = EvCharging.loadProfiles(dir)
        assertEquals(6000.0, prof["2026-07-02"]!!["00:00"]!!, 1e-6)
        assertEquals(setOf("00:00"), prof["2026-07-02"]!!.keys)
        assertEquals(6000.0, prof["2026-01-10"]!!["12:00"]!!, 1e-6)
        assertTrue("2026-01-11" !in prof)
        assertTrue(EvCharging.loadProfiles(dir.resolve("nope")).isEmpty())
    }

    // ── EnergyDay with EV (same fixture as Python _boost_day_data) ───────────

    private fun day(ev: Map<String, Double>?): EnergyDay {
        fun recs(vararg p: Pair<String, Int>) = JsonArray().apply {
            p.forEach { (t, v) -> add(JsonObject().apply { addProperty("time", t); addProperty("value", v.toDouble().toString()) }) }
        }
        fun info(label: String, r: JsonArray) = JsonObject().apply { addProperty("label", label); add("records", r) }
        val data = JsonObject().apply {
            add("infos", JsonArray().apply {
                add(info("PV", recs("00:00" to 0, "08:00" to 1000, "09:00" to 300, "12:00" to 2000)))
                add(info("Grid", recs("00:00" to 500, "08:00" to -200, "09:00" to 100, "12:00" to -1500)))
                add(info("Load", recs("00:00" to 500, "08:00" to 800, "09:00" to 400, "12:00" to 500)))
            })
        }
        val month = EnergyMonth(JsonParser.parseString("""{"infos": []}""").asJsonObject)
        return EnergyDay(data, "2025-06-10", month, null, "00:00", "06:00", ExportCorrection(1.0, 0.0), ev)
    }

    @Test
    fun evAddsToLoadAndImportAndSoaksUpExport() {
        val base = day(null)
        val ev = day(mapOf("00:00" to 7000.0, "12:00" to 1000.0, "03:00" to 2400.0))
        assertEquals((7000 + 2400) / 12.0, ev.getCalcEvOffPeak(), 1e-9)
        assertEquals(1000 / 12.0, ev.getCalcEvPeak(), 1e-9)
        assertEquals((7000 + 1000 + 2400) / 12.0, ev.getCalcLoad() - base.getCalcLoad(), 1e-9)
        assertEquals(1000 / 12.0, base.getCalcExport() - ev.getCalcExport(), 1e-9)
        assertEquals(1000 / 12.0, ev.evFromPv, 1e-9)
        assertEquals(base.getCalcImportPeak(), ev.getCalcImportPeak(), 1e-9)
        assertEquals((7000 + 2400) / 12.0, ev.getCalcImportOffPeak() - base.getCalcImportOffPeak(), 1e-9)
    }

    @Test
    fun evNotSeenByBatterySimulation() {
        fun replay(ev: Map<String, Double>?): List<Double> {
            val b = VirtualBattery(PriceData(), batterySize = 5000, pvEnabled = 1, startCharging = 5000, stopCharging = 5000)
            day(ev).runBattery(b)
            return listOf(b.drawn, b.pvInput, b.chargeAmount, b.batteryStatus)
        }
        assertEquals(replay(null), replay(mapOf("12:00" to 1000.0, "09:00" to 5000.0)))
    }
}
