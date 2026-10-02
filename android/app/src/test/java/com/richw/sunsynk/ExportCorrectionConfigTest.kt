package com.richw.sunsynk

import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Test

class ExportCorrectionConfigTest {

    private val json = JsonParser.parseString(
        """{"exportCorrection": {"gain": 0.953, "chargerStandbyW": 11, "chargerOff": [["2026-08-06", "2026-08-25"]]}}"""
    ).asJsonObject

    @Test
    fun parsesAndResolvesChargerOffRangesInclusively() {
        val ec = loadSettingsFromJson(json).exportCorrection
        assertEquals(0.953, ec.gain, 0.0)
        assertEquals(11.0, ec.forDate("2026-08-05").standbyW, 0.0)
        assertEquals(0.0, ec.forDate("2026-08-06").standbyW, 0.0)
        assertEquals(0.0, ec.forDate("2026-08-25").standbyW, 0.0)
        assertEquals(11.0, ec.forDate("2026-08-26").standbyW, 0.0)
        assertEquals(0.953, ec.forDate("2026-08-26").gain, 0.0)
    }

    @Test
    fun missingBlockGivesOriginalRuleDefaults() {
        val ec = loadSettingsFromJson(JsonParser.parseString("{}").asJsonObject).exportCorrection
        assertEquals(ExportCorrectionConfig(1.0, 27.6, emptyList()), ec)
    }

    @Test
    fun roundTripsThroughSettingsJson() {
        val settings = loadSettingsFromJson(json)
        assertEquals(settings.exportCorrection, loadSettingsFromJson(settingsToJson(settings)).exportCorrection)
    }
}
