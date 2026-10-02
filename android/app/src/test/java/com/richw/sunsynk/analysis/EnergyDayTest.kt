package com.richw.sunsynk.analysis

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalTime

/**
 * Parity with the Python engine (analysis/energyday.py): expected values below were
 * produced by running the Python EnergyDay on the same fixture.
 */
class EnergyDayTest {

    private fun records(vararg pairs: Pair<String, Int>) = JsonArray().apply {
        pairs.forEach { (t, v) -> add(JsonObject().apply { addProperty("time", t); addProperty("value", v.toDouble().toString()) }) }
    }

    private fun info(label: String, recs: JsonArray) = JsonObject().apply {
        addProperty("label", label); add("records", recs)
    }

    // 10:05 -> 10:25 is a 20-minute gap (3 dropped samples, forward-filled); 05:05 -> 10:00
    // and 10:35 -> 14:00 are long gaps and are left alone. 10:30 exports only 40 W, so the
    // 27.6 W default standby is capped at that sample's export.
    private fun dayData() = JsonObject().apply {
        add("infos", JsonArray().apply {
            add(info("PV", records("10:00" to 1500, "10:05" to 1400, "10:25" to 1100, "10:30" to 300, "10:35" to 900)))
            add(info("Grid", records("05:00" to 300, "05:05" to 250, "10:00" to -1200, "10:05" to -1000,
                "10:25" to -800, "10:30" to -40, "10:35" to -600, "14:00" to 400)))
            add(info("Load", records("05:00" to 300, "05:05" to 250, "10:00" to 300, "10:05" to 400,
                "10:25" to 300, "10:30" to 340, "10:35" to 300, "14:00" to 400)))
        })
    }

    private val emptyMonth = EnergyMonth(JsonObject().apply { add("infos", JsonArray()) })

    private fun day(correction: ExportCorrection) =
        EnergyDay(dayData(), "2026-09-01", emptyMonth, null, "00:00", "06:00", correction)

    @Test
    fun defaultCorrectionMatchesPython() {
        val d = day(ExportCorrection())
        assertEquals(534.9333333333333, d.getCalcExport(), 1e-9)
        assertEquals(14.933333333333334, d.getCalcImportPeak(), 1e-9)
        assertEquals(45.83333333333333, d.getCalcImportOffPeak(), 1e-9)
        assertEquals(783.3333333333334, d.getCalcPv(), 1e-9)
        assertEquals(315.8333333333333, d.getCalcLoad(), 1e-9)
    }

    @Test
    fun calibratedCorrectionMatchesPython() {
        val d = day(ExportCorrection(gain = 0.953, standbyW = 11.0))
        assertEquals(519.9933333333333, d.getCalcExport(), 1e-9)
        assertEquals(26.0, d.getCalcImportPeak(), 1e-9)
    }

    @Test
    fun gainIsNotCreditedToImport() {
        val d = day(ExportCorrection(gain = 0.953, standbyW = 0.0))
        assertEquals(527.3266666666666, d.getCalcExport(), 1e-9)
        assertEquals(33.333333333333336, d.getCalcImportPeak(), 1e-9)   // raw peak import, untouched
    }

    @Test
    fun splitDefaultIsOriginal2point3WhRule() {
        val (export, standby) = ExportCorrection().split(10.0)
        assertEquals(7.7, export, 1e-9)
        assertEquals(2.3, standby, 1e-9)
        assertEquals(Pair(0.0, 1.0), ExportCorrection().split(1.0))
    }

    @Test
    fun fillGapsForwardFillsOnlyShortGaps() {
        val t = { s: String -> LocalTime.parse(s) }
        val filled = fillGaps(listOf(t("10:20") to 3.0, t("10:00") to 1.0, t("10:05") to 2.0, t("11:00") to 4.0))
        assertEquals(listOf("10:00", "10:05", "10:10", "10:15", "10:20", "11:00"), filled.map { it.first.toString() })
        assertEquals(listOf(1.0, 2.0, 2.0, 2.0, 3.0, 4.0), filled.map { it.second })
    }
}
