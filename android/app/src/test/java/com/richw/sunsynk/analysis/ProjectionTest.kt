package com.richw.sunsynk.analysis

import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Parity with analysis/projection.py (same cases as tests/test_projection.py). */
class ProjectionTest {

    private val prices = JsonParser.parseString("""
        {"data": {"prices": [
          {"datefrom": "2025-01-01", "dateto": "2025-12-31", "offpeakRate": "0.07", "offpeakStart": "00:00",
           "offpeakStop": "07:00", "peakRate": "0.28", "exportRate": "0.165", "standingCharge": "0.6"},
          {"datefrom": "2026-01-01", "dateto": "2040-12-31", "offpeakRate": "0.04", "offpeakStart": "00:00",
           "offpeakStop": "06:00", "peakRate": "0.25", "exportRate": "0.13", "standingCharge": "0.57"}
        ]}}
    """).asJsonObject

    @Test
    fun latestRatesPicksNewestStartedPeriod() {
        assertEquals(ProjectionRates(0.25, 0.04, 0.13, 0.57, "2026-01-01"), latestRates(prices, "2026-10-18"))
        assertEquals(0.165, latestRates(prices, "2025-06-01").export, 1e-9)
        assertEquals("2026-01-01", latestRates(prices, "2020-01-01").dateFrom)
    }

    @Test
    fun partialYearFallsBackToSimpleAverage() {
        val p = SeasonalProjection()
        p.addDay("2026-06-01", mapOf("pv" to 20.0))
        p.addDay("2026-06-02", mapOf("pv" to 10.0))
        assertFalse(p.seasonal)
        assertEquals(365 * 15.0, p.annualKwh("pv"), 1e-9)
        assertEquals(0.0, SeasonalProjection().annualKwh("pv"), 0.0)
    }

    @Test
    fun monthSeenTwiceCountsOnce() {
        val p = SeasonalProjection()
        for (m in 1..12) p.addDay("2025-%02d-01".format(m), mapOf("pv" to 1.0))
        for (d in 1..20) p.addDay("2026-06-%02d".format(d), mapOf("pv" to if (d % 2 == 1) 1.0 else 5.0))
        assertTrue(p.seasonal)
        val juneAvg = (1.0 + 10 * 1.0 + 10 * 5.0) / 21
        assertEquals(365 - 30 + 30 * juneAvg, p.annualKwh("pv"), 1e-9)
    }

    @Test
    fun annualMoneyPricesKwhAtGivenRates() {
        val p = SeasonalProjection()
        for (m in 1..12) repeat(SeasonalProjection.DAYS_IN_MONTH[m - 1]) {
            p.addDay("2026-%02d-01".format(m), mapOf(
                "load_peak" to 8.0, "import_peak" to 3.0, "load_off_peak" to 2.0, "import_off_peak" to 2.0,
                "export" to 4.0, "batt_drawn" to 2.0, "batt_grid_charge" to 1.0,
                "batt_pv_diverted" to 1.0, "batt_exported" to 0.5))
        }
        val m = p.annualMoney(ProjectionRates(0.25, 0.04, 0.13, 0.0, ""))
        assertEquals(365 * 5.0 * 0.25, m.selfConsumption, 1e-6)
        assertEquals(365 * 4.0 * 0.13, m.export, 1e-6)
        assertEquals(365 * (2.0 * 0.25 + 0.5 * 0.13 - 1.0 * 0.04 - 1.0 * 0.13), m.battery, 1e-6)
    }

    // ── rebased EV year (parity with tests/test_projection.py) ───────────────

    private fun evProj(months: Map<Int, Double>, year: Int = 2026, split: Triple<Double, Double, Double> = Triple(0.1, 0.9, 0.0)) =
        SeasonalProjection().apply {
            for ((m, kwh) in months) addDay("%04d-%02d-01".format(year, m),
                mapOf("ev_peak" to kwh * split.first, "ev_off_peak" to kwh * split.second, "ev_from_pv" to kwh * split.third))
        }

    @Test
    fun rebasedEvScalesUnseenMonthsByOverlapRatio() {
        val before = evProj((1..12).associateWith { 5.0 }, 2025, Triple(0.5, 0.5, 0.0))
        val after = evProj((5..10).associateWith { 12.0 }, split = Triple(0.1, 0.9, 0.05))
        val r = rebasedAnnualEv(before, after)
        assertEquals(6, r.measuredMonths)
        assertEquals(2.4, r.ratio, 1e-9)
        assertEquals(365 * 12.0, r.kwh["ev_peak"]!! + r.kwh["ev_off_peak"]!!, 1e-6)
        assertEquals(365 * 12.0 * 0.1, r.kwh["ev_peak"]!!, 1e-6)
        assertEquals(365 * 12.0 * 0.05, r.kwh["ev_from_pv"]!!, 1e-6)
    }

    @Test
    fun rebasedEvWithoutAfterDataIsSeasonal() {
        val r = rebasedAnnualEv(evProj((1..12).associateWith { 5.0 }, 2025), SeasonalProjection())
        assertEquals(0, r.measuredMonths)
        assertEquals(1.0, r.ratio, 0.0)
        assertEquals(365 * 5.0, r.kwh["ev_peak"]!! + r.kwh["ev_off_peak"]!!, 1e-6)
    }

    @Test
    fun rebasedEvUnseenMonthsTakeTheAverage() {
        val r = rebasedAnnualEv(SeasonalProjection(), evProj(mapOf(6 to 10.0, 7 to 10.0)))
        assertEquals(365 * 10.0, r.kwh["ev_peak"]!! + r.kwh["ev_off_peak"]!!, 1e-6)
    }
}
