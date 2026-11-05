package com.richw.sunsynk.analysis

import com.google.gson.JsonParser
import com.richw.sunsynk.api.FuelPriceSource
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.time.Instant
import java.time.LocalDate

class EvSavingsTest {
    @get:Rule val tmp = TemporaryFolder()

    private val periods = listOf(
        ActualPeriod("2025-06-10", "2025-06-30", 7.0, 26.0, "00:00-04:00", 16.5, 53.0),
        ActualPeriod("2025-07-01", "2025-12-31", 6.0, 28.0, "23:30-05:30", 15.0, 53.0),
    )

    private fun day(evWh: Map<Int, Int>, solarEvWh: Int = 0) = IntArray(240) { k ->
        when {
            k in 48 until 96 -> evWh[k - 48] ?: 0
            k == 192 + 26 -> solarEvWh
            else -> 0
        }
    }

    @Test
    fun evDaysPriceEachHalfHourAtItsPeriodAndAddIndraDaysBefore() {
        val days = mapOf(
            "2025-06-20" to day(mapOf(0 to 2000, 10 to 1000), solarEvWh = 3000),   // 00:00 off-peak, 05:00 peak
            "2025-07-05" to day(mapOf(47 to 1000, 5 to 1000, 11 to 1000)),          // 23:30 + 02:30 in window, 05:30 not
        )
        val ev = EvSavings.evDays(HistoryDays("t", periods, days), mapOf("2025-06-01" to 10.0, "2025-06-25" to 99.0))
        assertEquals(listOf("2025-06-01", "2025-06-20", "2025-07-05"), ev.map { it.date })
        assertEquals(10 * 0.07, ev[0].cost, 1e-9)                       // before the first day: first off-peak
        assertEquals(6.0, ev[1].kwh, 1e-9)
        assertEquals((2 * 7.0 + 1 * 26.0 + 3 * 16.5) / 100, ev[1].cost, 1e-9)
        assertEquals((2 * 6.0 + 28.0) / 100, ev[2].cost, 1e-9)
    }

    @Test
    fun localDailyHoldsEachStationsLastPriceInLocalTime() {
        val s = mapOf(
            "a" to listOf(Instant.parse("2026-07-01T23:30:00Z") to 150.0, Instant.parse("2026-07-03T10:00:00Z") to 152.0),
            "b" to listOf(Instant.parse("2026-07-02T09:00:00Z") to 148.0, Instant.parse("2026-07-02T10:00:00Z") to 999.0),
        )
        val d = EvSavings.localDaily(s, LocalDate.of(2026, 7, 1), LocalDate.of(2026, 7, 3))
        assertNull(d["2026-07-01"])                                      // 23:30Z = 00:30 BST on the 2nd
        assertEquals(Pair(2, 149.0), d["2026-07-02"])                   // 999 is out of range
        assertEquals(Pair(2, 150.0), d["2026-07-03"])
    }

    @Test
    fun parseNationalAndOffset() {
        val csv = "﻿Date,ULSP (Ultra low sulphur unleaded petrol) Pump price in pence/litre,ULSD\n" +
            "29/06/2026,151.02,160\n06/07/2026,149.80,161\n13/07/2026,150.53,162\n"
        val n = EvSavings.parseNational(csv)
        assertEquals(3, n.size)
        assertEquals(LocalDate.of(2026, 7, 6) to 149.80, n[1])
        val local = mapOf("2026-07-06" to Pair(15, 146.80), "2026-07-13" to Pair(16, 147.53), "2026-06-29" to Pair(3, 100.0))
        val (off, weeks) = EvSavings.offset(n, local, EvSavings.minStations(22))!!
        assertEquals(15, EvSavings.minStations(22))
        assertEquals(-3.0, off, 1e-9); assertEquals(2, weeks)
        // a thinly covered day falls back to that week's national price + offset
        val p = EvSavings.petrolPrice("2026-07-01", n, local, off, 15)!!
        assertEquals(151.02 - 3.0, p.pence, 1e-9); assertEquals("national-adjusted", p.source)
        assertEquals("local", EvSavings.petrolPrice("2026-07-13", n, local, off, 15)!!.source)
    }

    @Test
    fun calculateMilesLitresAndSavings() {
        val n = listOf(LocalDate.of(2025, 1, 6) to 140.0)
        val cfg = EvSavingsConfig(mpg = 50.0, milesPerKwh = 4.0, chargeEfficiency = 0.9,
            beforeCharger = listOf(ExtraCharge("2025-02", 10.0, 7.0)))
        val r = EvSavings.calculate(listOf(EvDay("2025-03-02", 20.0, 1.5)), cfg, n, emptyMap(), 0, LocalDate.of(2025, 4, 1))
        assertEquals(listOf("2025-02", "2025-03"), r.months.map { it.month })
        val mar = r.months[1]
        assertEquals(20 * 0.9 * 4.0, mar.miles, 1e-9)
        val litres = 72.0 / 50 * EvSavings.LITRES_PER_GALLON
        assertEquals(litres * 1.40, mar.petrolCost, 1e-9)
        assertEquals(litres * 1.40 - 1.5, mar.saved, 1e-9)
        assertEquals(0.7, r.months[0].evCost, 1e-9)
        assertEquals(r.months.sumOf { it.saved }, r.total.saved, 1e-9)
        assertEquals(r.total.saved, r.last12(LocalDate.of(2025, 4, 1)).saved, 1e-9)
        assertEquals("national", r.latest!!.source)
    }

    @Test
    fun creditsComeOffTheirMonthsChargingCost() {
        val n = listOf(LocalDate.of(2025, 1, 6) to 140.0)
        val cfg = EvSavingsConfig(credits = listOf(EvCredit("2025-03", 30.0, "bonus"), EvCredit("2025-03", 5.0),
            EvCredit("2025-05", 10.0)))
        val r = EvSavings.calculate(listOf(EvDay("2025-03-02", 20.0, 1.5)), cfg, n, emptyMap(), 0, LocalDate.of(2025, 7, 1))
        assertEquals(listOf("2025-03", "2025-05"), r.months.map { it.month })
        val mar = r.months[0]
        assertEquals(35.0, mar.credit, 1e-9)
        assertEquals(1.5 - 35.0, mar.netCost, 1e-9)
        assertEquals(mar.petrolCost - 1.5 + 35.0, mar.saved, 1e-9)
        assertEquals(10.0, r.months[1].saved, 1e-9)                    // a credit-only month
        assertEquals(45.0, r.total.credit, 1e-9)
    }

    @Test
    fun indraDailyKwhReadsMonthReports() {
        tmp.root.resolve("month-2025-04.json").writeText(
            """{"dailyReports":[{"date":"2025-04-02T00:00:00","totals":{"energyImportedKwh":10.27}},{"date":"2025-04-03","totals":{}}]}""")
        tmp.root.resolve("sessions-2025-04.json").writeText("[]")
        assertEquals(mapOf("2025-04-02" to 10.27, "2025-04-03" to 0.0), EvSavings.indraDailyKwh(tmp.root))
    }

    @Test
    fun stationsKeepTheBigFourAndHistoryMerges() {
        val json = JsonParser.parseString("""{"stations":[
            {"node_id":"1","trading_name":"TOWN SUPERSTORE","brand_name":"TESCO","location":{"postcode":"X1"}},
            {"node_id":"2","trading_name":"ASDA RING ROAD EXPRESS PETROL","brand_name":"Esso","location":{"postcode":"X2"}},
            {"node_id":"3","trading_name":"Village Garage","brand_name":"Morrisons Daily"},
            {"node_id":"4","trading_name":"Some Garage","brand_name":"BP"},
            {"node_id":"5","trading_name":"SAINSBURYS OLD","brand_name":"Sainsburys","is_permanently_closed":true}]}""").asJsonObject
        assertEquals(listOf("1", "2"), FuelPriceSource.parseStations(json).map { it.id })
        val h = FuelPriceSource.parseHistory(JsonParser.parseString(
            """{"history":[{"timestamp":"2026-02-07T21:40:02.554Z","pricePence":128},{"timestamp":"2026-02-17T14:40:38Z","pricePence":127}]}""").asJsonObject)
        val old = listOf(Instant.parse("2025-12-01T00:00:00Z") to 130.0, Instant.parse("2026-02-07T21:40:02.554Z") to 1.0)
        assertEquals(listOf(130.0, 128.0, 127.0), FuelPriceSource.merge(old, h).map { it.second })
    }
}
