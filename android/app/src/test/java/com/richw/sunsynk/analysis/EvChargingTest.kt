package com.richw.sunsynk.analysis

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
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
    fun loadProfilesPrefersTelemetryAndUsesLocalTime() {
        val dir = tmp.root
        dir.resolve("session-t.json").writeText(session("t", "2026-07-01T22:58:00Z", "2026-07-01T23:10:00Z", off = 1.0).toString())
        dir.resolve("telemetry-t.json").writeText(JsonArray().apply {
            (0 until 5).forEach { add(row("2026-07-01T23:0$it:00Z", 6.0)) }
        }.toString())
        dir.resolve("session-s.json").writeText(session("s", "2026-01-10T12:00:00Z", "2026-01-10T13:00:00Z", std = 0.5).toString())
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
