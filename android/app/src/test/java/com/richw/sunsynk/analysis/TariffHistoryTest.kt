package com.richw.sunsynk.analysis

import com.google.gson.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

/** Parity with tests/test_tariffhistory.py. */
class TariffHistoryTest {
    private fun row(from: String?, to: String?, v: Double, pm: String? = null) = JsonObject().apply {
        addProperty("valid_from", from); addProperty("valid_to", to); addProperty("value_inc_vat", v)
        if (pm != null) addProperty("payment_method", pm)
    }

    private val periods = listOf(
        ActualPeriod("2026-03-01", "2026-04-30", 5.0, 25.0, "00:00-06:00", 15.0, 50.0),
        ActualPeriod("2026-04-30", "2026-12-31", 4.0, 26.0, "00:00-06:00", 13.0, 60.0),
    )

    @Test
    fun timelineLatestStartWinsAndSkipsNonDirectDebit() {
        val t = RateTimeline(listOf(
            row("2026-01-01T00:00:00Z", null, 20.0),
            row("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z", 25.0, "DIRECT_DEBIT"),
            row("2026-03-01T00:00:00Z", null, 99.0, "NON_DIRECT_DEBIT"),
        ))
        assertEquals(2, t.size)
        assertNull(t.at(Instant.parse("2025-12-31T23:59:00Z")))
        assertEquals(20.0, t.at(Instant.parse("2026-02-01T00:00:00Z"))!!, 0.0)
        assertEquals(25.0, t.at(Instant.parse("2026-03-01T00:00:00Z"))!!, 0.0)
        assertEquals(25.0, t.at(Instant.parse("2026-03-15T00:00:00Z"))!!, 0.0)
        assertEquals(20.0, t.at(Instant.parse("2026-04-01T00:00:00Z"))!!, 0.0)
    }

    private fun halfHourly(day: LocalDate, value: (Int) -> Double): List<JsonObject> {
        val s = TariffHistory.slotInstants(day)
        return List(48) { k -> row(s[k].toString(), if (k == 47) null else s[k + 1].toString(), value(k)) }
    }

    @Test
    fun dayRatesFollowLocalHalfHoursInGmtAndBst() {
        for (day in listOf(LocalDate.of(2026, 1, 15), LocalDate.of(2026, 7, 15))) {
            val t = RateTimeline(halfHourly(day) { it.toDouble() })
            assertEquals(List(48) { it.toDouble() }, TariffHistory.dayRates(t, TariffHistory.slotInstants(day)))
        }
        assertEquals("2026-07-14T23:00:00Z", TariffHistory.slotInstants(LocalDate.of(2026, 7, 15))[0].toString())
    }

    @Test
    fun dayRatesFillGapsAndNullWhenEmpty() {
        val day = LocalDate.of(2026, 1, 15)
        val s = TariffHistory.slotInstants(day)
        val rows = halfHourly(day) { if (it < 24) 10.0 else 30.0 }.toMutableList()
        rows.removeAt(47)
        rows[46] = row(s[46].toString(), s[47].toString(), 30.0)
        assertEquals((24 * 10 + 23 * 30) / 47.0, TariffHistory.dayRates(RateTimeline(rows), s)!![47], 1e-9)
        assertNull(TariffHistory.dayRates(RateTimeline(emptyList()), s))
    }

    @Test
    fun monthsAndLast12() {
        assertEquals(4, TariffHistory.monthsBetween(LocalDate.of(2025, 11, 20), LocalDate.of(2026, 2, 1)).size)
        val months = (1..12).map { "2025-%02d".format(it) } + listOf("2026-01", "2099-01")
        assertEquals(months.subList(1, 13), TariffHistory.last12Months(months, LocalDate.of(2026, 11, 5)))
        assertEquals(periods[0], TariffHistory.actualPeriod(periods, "2026-04-30"))
        assertNull(TariffHistory.actualPeriod(periods, "2027-01-01"))
    }

    @Test
    fun retimeMovesOffPeakEvToCheapestNightSlotsWithCap() {
        val rates = MutableList(48) { 30.0 }.also { it[46] = 5.0; it[2] = 8.0; it[3] = 9.0 }
        val ev = MutableList(48) { 0.0 }.also { it[0] = 4.0; it[1] = 3.0; it[30] = 1.0 }
        assertEquals(3.7 * 5 + 3.3 * 8 + 1.0 * 30, TariffHistory.retimeEv(ev, rates, (0 until 12).toSet(), 3.7), 1e-9)
    }

    @Test
    fun priceImportDaySmartAndActual() {
        val rates = TariffCompare.touSlotRates(30.0, 7.0, "23:30-05:30")
        val house = List(48) { 0.5 }
        val ev = MutableList(48) { 0.0 }.also { it[2] = 7.0; it[36] = 2.0 }
        val smart = TariffHistory.priceImportDay(rates, house, ev, (0 until 12).toSet(), true, 3.7)
        assertEquals(9 * 7 / 100.0, smart.ev, 1e-9)
        assertEquals((7 * 7 + 2 * 30) / 100.0, smart.evNow, 1e-9)
        val actual = TariffHistory.priceImportDay(rates, house, ev, (0 until 12).toSet(), false, 3.7, actual = true)
        assertEquals(actual.evNow, actual.ev, 0.0)
    }

    private fun flat(name: String, rate: Double, standing: Double = 40.0, dir: String = "IMPORT", requires: String? = null) =
        HistTariff(name, name, dir, false, requires,
            RateTimeline(listOf(row("2020-01-01T00:00:00Z", null, rate))),
            RateTimeline(listOf(row("2020-01-01T00:00:00Z", null, standing))))

    private fun day(): IntArray = IntArray(144) { k -> when (k) { in 0..47 -> 100; 48 -> 2000; 96 + 24 -> 3000; else -> 0 } }

    @Test
    fun priceHistoryTotalsAndPairs() {
        val days = mapOf("2026-05-01" to day(), "2026-05-02" to day(), "2027-06-01" to day())
        val r = TariffHistory.priceHistory(days, periods,
            listOf(flat("Flat", 20.0), flat("Out", 10.0, 0.0, "EXPORT", "Octopus"),
                flat("Other out", 30.0, 0.0, "EXPORT", "Someone else")))
        assertEquals(mapOf(TariffHistory.ACTUAL to 1), r.skipped)
        val may = r.imports.getValue(TariffHistory.ACTUAL).getValue("2026-05")
        assertEquals(2, may.days)
        assertEquals(2 * (3.6 * 0.26 + 1.2 * 0.04), may.house, 1e-9)
        assertEquals(2 * 2 * 0.04, may.ev, 1e-9)
        assertEquals(1.2, may.standing, 1e-9)
        val f = r.imports.getValue("Flat").getValue("2026-05")
        assertEquals(2 * 6.8 * 0.20, f.house + f.ev, 1e-9)
        assertEquals(2 * 3 * 0.13, r.exports.getValue(TariffHistory.ACTUAL_EXPORT).getValue("2026-05").income, 1e-9)
        assertEquals(2 * 3 * 0.10, r.exports.getValue("Out").getValue("2026-05").income, 1e-9)
        assertEquals(6.0, r.kwh.getValue("2026-05").export, 1e-9)
        val pairs = TariffHistory.bestPairs(r, listOf("2026-05"))
        val names = pairs.map { it.imp to it.exp }.toSet()
        assertTrue((TariffHistory.ACTUAL to TariffHistory.ACTUAL_EXPORT) in names)
        assertTrue(("Flat" to "Out") in names && ("Flat" to TariffHistory.ACTUAL_EXPORT) in names)
        assertTrue((TariffHistory.ACTUAL to "Out") !in names && ("Flat" to "Other out") !in names)
        assertEquals(pairs.map { it.net }.sorted(), pairs.map { it.net })
    }

    @Test
    fun aDayMissingAnyTariffIsLeftOutOfEveryColumn() {
        val gap = flat("Gap", 20.0).copy(unit = RateTimeline(emptyList()))
        val r = TariffHistory.priceHistory(mapOf("2026-05-01" to day()), periods, listOf(flat("Flat", 20.0), gap))
        assertEquals(mapOf("Gap" to 1), r.skipped)
        assertTrue(r.kwh.isEmpty())
    }
}
