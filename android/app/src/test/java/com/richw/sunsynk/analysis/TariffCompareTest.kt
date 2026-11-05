package com.richw.sunsynk.analysis

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.richw.sunsynk.api.TariffSources
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/** Parity with tests/test_tariffcompare.py (same fixtures and expected values). */
class TariffCompareTest {

    // ── helpers ──────────────────────────────────────────────────────────────

    @Test
    fun familyKeyStripsVersions() {
        mapOf("OE-FIX-12M-26-11-03" to "OE-FIX-12M", "GO-VAR-22-10-14" to "GO-VAR",
            "NEXT_DRIVE_FIXED_V37" to "NEXT_DRIVE_FIXED",
            "NEXT_SMART_SAVER_FIXED_12M_GAS_V47_1" to "NEXT_SMART_SAVER_FIXED_12M_GAS",
            "EDF_EV_FIX_GOELEC_18M_V5_HH" to "EDF_EV_FIX_GOELEC_18M", "NEXT_FLEX" to "NEXT_FLEX",
        ).forEach { (code, fam) -> assertEquals(fam, TariffSources.familyKey(code)) }
    }

    @Test
    fun windowSlotsPlainAndWrapping() {
        assertEquals((1 until 11).toSet(), TariffCompare.windowSlots("00:30-05:30"))
        assertEquals(setOf(47) + (0 until 11).toSet(), TariffCompare.windowSlots("23:30-05:30"))
        assertTrue(TariffCompare.windowSlots("00:00-00:00").isEmpty())
    }

    private fun rate(from: String?, to: String?, v: Double) = JsonObject().apply {
        addProperty("valid_from", from); addProperty("valid_to", to); addProperty("value_inc_vat", v)
    }

    @Test
    fun ratesToSlotsLocalTimeInGmtAndBst() {
        val gmt = listOf(rate("2026-11-02T05:30:00Z", "2026-11-03T00:30:00Z", 30.0),
            rate("2026-11-02T00:30:00Z", "2026-11-02T05:30:00Z", 9.0),
            rate("2026-11-01T05:30:00Z", "2026-11-02T00:30:00Z", 30.0))
        var slots = TariffSources.ratesToSlots(gmt, LocalDate.of(2026, 11, 2), LocalDate.of(2026, 11, 3))
        assertEquals((1 until 11).toList(), (0 until 48).filter { slots[it] == 9.0 })
        val bst = listOf(rate("2026-07-01T23:30:00Z", "2026-07-02T04:30:00Z", 9.0),
            rate("2026-07-01T04:30:00Z", "2026-07-01T23:30:00Z", 30.0),
            rate("2026-06-30T23:30:00Z", "2026-07-01T04:30:00Z", 9.0))
        slots = TariffSources.ratesToSlots(bst, LocalDate.of(2026, 7, 1), LocalDate.of(2026, 7, 2))
        assertEquals((1 until 11).toList(), (0 until 48).filter { slots[it] == 9.0 })
    }

    @Test
    fun ratesToSlotsAveragesDaysOpenEndedAndGaps() {
        val rates = listOf(rate("2026-11-02T00:00:00Z", null, 20.0),
            rate("2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z", 10.0))
        assertEquals(List(48) { 15.0 }, TariffSources.ratesToSlots(rates, LocalDate.of(2026, 11, 1), LocalDate.of(2026, 11, 3)))
        assertEquals(List<Double?>(48) { null },
            TariffSources.ratesToSlots(rates.take(1), LocalDate.of(2026, 11, 1), LocalDate.of(2026, 11, 2)))
    }

    @Test
    fun windowFromText() {
        assertEquals("23:30-05:30", TariffSources.windowFromText("a super low rate between 23:30 - 05:30 every night"))
        assertEquals("00:30-05:30", TariffSources.windowFromText("cheap from 0:30 to 5:30"))
        assertNull(TariffSources.windowFromText("no times here"))
    }

    // ── scrapers ─────────────────────────────────────────────────────────────

    private fun epTable(title: String, header: List<String>, rows: List<List<String>>): String {
        val cells = header.joinToString("") { "<th>$it</th>" }
        val body = rows.joinToString("") { r -> "<tr>" + r.joinToString("") { "<td>$it</td>" } + "</tr>" }
        return "<h3>$title</h3><p>Unit rates...</p><table><tr>$cells</tr>$body</table>"
    }

    private val epStd = listOf("PES region", "Elec rate (p/kWh)", "Elec standing (p/day)", "Contract", "Exit fee (per fuel)")
    private val epE7 = listOf("PES region", "Elec rate (p/kWh)", "E7 off-peak (p/kWh)", "Elec standing (p/day)",
        "Contract", "Exit fee (per fuel)")

    @Test
    fun energyplusStandardRateRowForRegion() {
        val page = "<h1>Fix and Fall Oct28 v5 by British Gas: regional prices</h1>" +
            epTable("Electricity, Monthly Direct Debit, Economy 7: regional prices", epE7,
                listOf(listOf("South West England", "30.0", "15.0", "50.00", "24 months", "£50"))) +
            epTable("Electricity, Monthly Direct Debit, standard-rate: regional prices", epStd,
                listOf(listOf("East Midlands", "26.0", "55.00", "24 months", "£50"),
                    listOf("South West England", "27.38", "56.24", "24 months", "£50")))
        val (t, reason) = TariffSources.energyplusTariff(page.replace("Fix and Fall", "Fix &#x2B; Fall"),
            "fix-and-fall", "british-gas", "L")
        assertNull(reason)
        assertEquals("British Gas", t!!.supplier)
        assertEquals("Fix + Fall Oct28 v5", t.name)
        assertEquals("fixed 24m", t.kind)
        assertEquals(List(48) { 27.38 }, t.slotRates)
        assertEquals(56.24, t.standing, 1e-9)
    }

    @Test
    fun energyplusE7OnlyNeedsAWindow() {
        val page = "<h1>EV Power Apr28 v2 by British Gas: regional prices</h1>" +
            epTable("Electricity, Monthly Direct Debit, Economy 7: regional prices", epE7,
                listOf(listOf("South West England", "35.49", "9.50", "56.51", "18 months", "£50")))
        val t = TariffSources.energyplusTariff(page, "ev-power", "british-gas", "L").first!!
        assertEquals(List(10) { 9.5 }, t.slotRates.take(10))
        assertEquals(List(38) { 35.49 }, t.slotRates.drop(10))
        val other = TariffSources.energyplusTariff(page.replace("EV Power Apr28 v2", "Night Saver"), "n", "british-gas", "L")
        assertNull(other.first); assertTrue("window" in other.second!!)
        val wrongRegion = TariffSources.energyplusTariff(page, "ev-power", "british-gas", "A")
        assertNull(wrongRegion.first); assertTrue("region" in wrongRegion.second!!)
    }

    @Test
    fun evervizPriceCapUsesNewestPeriod() {
        val rows = """[["Region","Daily standing charge July to September 2026","Daily standing charge October to December 2026","Unit rate July to September 2026","Unit rate  October to December 2026"],
            ["East Midlands","50.00 pence per day","51.00 pence per day","25.00 pence per kWh","25.10 pence per kWh"],
            ["South Western England","59.71 pence per day","57.23 pence per day","25.33 pence per kWh","25.60 pence per kWh"]]"""
        val js = "var x = {\"title\":{\"text\":\"<h3>Electricity standing charges and unit rates paid by Direct " +
            "Debit, single rate</h3>\"}, \"data\": $rows};"
        val (title, parsed) = TariffSources.parseEvervizTable(js)
        assertTrue(title.startsWith("Electricity standing charges"))
        assertEquals(3, parsed.size)
        val cap = TariffSources.priceCapTariff(parsed, "L")!!
        assertEquals(List(48) { 25.6 }, cap.slotRates)
        assertEquals(57.23, cap.standing, 1e-9)
        assertTrue("October to December 2026" in cap.name)
        assertNull(TariffSources.priceCapTariff(parsed, "P"))
    }

    @Test
    fun chargeAnytimeRate() {
        assertEquals(13.33, TariffSources.parseChargeAnytimeRate(
            "<p>The Charge Anytime pay as you go add-on rate on 1/10/2026 is 13.33p per kWh. Actual sum per mile is 3.33p.</p>")!!, 1e-9)
        assertNull(TariffSources.parseChargeAnytimeRate("<p>nothing</p>"))
    }

    // ── profile + pricing ────────────────────────────────────────────────────

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
        return EnergyDay(data, "2025-06-10", month, null, "00:00", "06:00", ExportCorrection(0.95, 11.0), ev)
    }

    @Test
    fun importAndExportSlotsMatchIntervalTotals() {
        val d = day(mapOf("00:00" to 7000.0, "12:00" to 1000.0, "03:00" to 2400.0))
        val (total, ev) = d.importSlots()
        assertEquals(d.getCalcImport(), total.sum(), 1e-9)
        assertEquals(d.getCalcEv() - d.evFromPv, ev.sum(), 1e-9)
        assertEquals(7000 / 12.0, ev[0], 1e-9)
        assertEquals(2400 / 12.0, ev[6], 1e-9)
        assertEquals(0.0, ev[24], 1e-9)
        val exp = d.exportSlots()
        assertEquals(d.getCalcExport(), exp.sum(), 1e-9)
        assertTrue(exp[16] > 0 && exp[24] > 0)
    }

    @Test
    fun slotProjectionSeasonalWeightingExportAndEvShape() {
        val proj = SlotProjection("2026-05-01")
        val zero = DoubleArray(48)
        for (m in 1..12) {
            val total = DoubleArray(48).also { it[0] = 1000.0 * m }
            proj.addDay("2025-%02d-15".format(m), total, zero, total)
        }
        assertTrue(proj.seasonal)
        val expected = (1..12).sumOf { it * SeasonalProjection.DAYS_IN_MONTH[it - 1] }.toDouble()
        assertEquals(expected, proj.annualHouse()[0], 1e-9)
        assertEquals(expected, proj.annualExport()[0], 1e-9)
        assertEquals(6.0 * 30, proj.monthly()[5]!!.second, 1e-9)
        val before = DoubleArray(48).also { it[2] = 5000.0 }
        val after = DoubleArray(48).also { it[4] = 3000.0 }
        proj.addDay("2026-04-01", before, before)
        proj.addDay("2026-06-01", after, after)
        assertEquals(1.0, proj.evShape()[4], 1e-9)
        assertEquals(0.0, proj.evShape()[2], 1e-9)
    }

    @Test
    fun annualProfileSpreadsRebasedEv() {
        val proj = SlotProjection()
        val total = DoubleArray(48).also { it[10] = 2000.0; it[2] = 4000.0 }
        val ev = DoubleArray(48).also { it[2] = 4000.0 }
        proj.addDay("2026-06-01", total, ev)
        val (house, evYear) = TariffCompare.annualProfile(proj, 365 * 2.0 + 3000)
        assertEquals(365 * 2.0, house.sum(), 1e-9)
        assertEquals(3000.0, evYear[2], 1e-9)
    }

    private fun goLike(cheap: Double, peak: Double, window: String, smart: Boolean = false, evRate: Double? = null) =
        Tariff("S", "T", "C", "x", "variable", TariffCompare.touSlotRates(peak, cheap, window), 50.0, smart, evRate)

    @Test
    fun priceAsNowEvRetimedSmartAndBreakdown() {
        val house = MutableList(48) { 0.0 }.also { it[20] = 1000.0 }
        val ev = MutableList(48) { 0.0 }.also { it[0] = 2000.0; it[30] = 100.0 }
        val cur = goLike(3.8, 25.63, "00:00-06:00").copy(code = "CURRENT", window = "00:00-06:00")
        val rows = TariffCompare.compare(listOf(goLike(9.0, 30.0, "00:30-05:30").copy(code = "GO"),
            goLike(7.0, 35.0, "23:30-05:30", smart = true, evRate = 7.0).copy(code = "IOG")), cur, house, ev)
        val go = rows.first { it.tariff.code == "GO" }
        assertEquals((1000 * 30 + 2000 * 30 + 100 * 30) / 100.0 + 365 * 0.5, go.asNow, 1e-9)
        assertEquals((1000 * 30 + 2000 * 9 + 100 * 30) / 100.0 + 365 * 0.5, go.total, 1e-9)
        assertEquals(0.0, go.cheapKwh, 1e-9)
        val iog = rows.first { it.tariff.code == "IOG" }
        assertEquals((1000 * 35 + 2100 * 7) / 100.0 + 365 * 0.5, iog.total, 1e-9)
        assertEquals(rows.map { it.total }.sorted(), rows.map { it.total })
        assertEquals(0.0, rows.first { it.isCurrent }.vsCurrent, 1e-9)
        assertEquals(go.houseCost + go.evRetimed + go.standingYear, go.total, 1e-9)
    }

    @Test
    fun describeRatesAndRuns() {
        assertEquals(Pair("single", "25.60"), TariffCompare.describeRates(List(48) { 25.6 }))
        assertEquals(Pair("tou", "6.50 23:30-05:30 / 36.98"),
            TariffCompare.describeRates(TariffCompare.touSlotRates(36.98, 6.5, "23:30-05:30")))
        assertEquals("16.00 16:00-19:00 / 9.00",
            TariffCompare.describeRates(TariffCompare.touSlotRates(9.0, 16.0, "16:00-19:00"), bestHigh = true).second)
        assertEquals("dynamic", TariffCompare.describeRates(List(48) { it.toDouble() }).first)
        assertEquals(listOf(Pair(47, 2)), TariffCompare.runs(listOf(0, 1, 47)))
    }

    private fun imp(code: String, supplier: String, total: Double, type: String = "tou") = ImportRow(
        Tariff(supplier, code, code, "x", "", List(48) { 1.0 }, 0.0), 0.0, 0.0, 0.0, 0.0, 0.0, total, total, 0.0,
        type, "", 0.0)

    private fun exp(code: String, income: Double, requires: String? = null, flat: Boolean = false) = ExportRow(
        Tariff("S", code, code, "x", "", List(48) { 1.0 }, 0.0, direction = "EXPORT", requires = requires,
            flatImportOnly = flat), income, "single", "", 0.0)

    @Test
    fun bestPairsRespectSupplierAndFlatImportRules() {
        val imports = listOf(imp("CURRENT", "Current", 800.0), imp("OCT", "Octopus", 1000.0),
            imp("EDF", "EDF", 1010.0), imp("FIX", "E.ON Next", 1500.0, "single"), imp("DRIVE", "E.ON Next", 1000.0))
        val exports = listOf(exp("CURRENT_EXPORT", 300.0), exp("AGILE_OUT", 400.0, "Octopus"),
            exp("SEG", 100.0), exp("EXCL", 300.0, "E.ON Next", flat = true))
        val pairs = TariffCompare.bestPairs(imports, exports, 30).map { Triple(it.imp.tariff.code, it.exp.tariff.code, it.net) }
        assertEquals(Triple("CURRENT", "CURRENT_EXPORT", 500.0), pairs[0])
        assertTrue(Triple("OCT", "AGILE_OUT", 600.0) in pairs)
        assertFalse(pairs.any { it.first == "EDF" && it.second == "AGILE_OUT" })
        assertFalse(pairs.any { it.first == "CURRENT" && it.second != "CURRENT_EXPORT" })
        assertFalse(pairs.any { it.first == "DRIVE" && it.second == "EXCL" })
        assertTrue(pairs.any { it.first == "FIX" && it.second == "EXCL" })
        assertTrue(TariffCompare.bestPairs(imports.reversed(), exports, 0).any { it.isCurrent })
    }

    @Test
    fun pairBreakdownAddsUp() {
        val house = List(48) { 10.0 }
        val ev = MutableList(48) { 0.0 }.also { it[2] = 50.0 }
        val export = MutableList(48) { 0.0 }.also { it[24] = 100.0 }
        val cur = goLike(3.8, 25.63, "00:00-06:00").copy(code = "CURRENT", window = "00:00-06:00")
        val row = TariffCompare.compare(listOf(goLike(7.0, 30.0, "00:00-05:00").copy(code = "X")), cur, house, ev)
            .first { it.tariff.code == "X" }
        val e = exp("E", 13.0)
        val b = TariffCompare.pairBreakdown(Pair3(row, e), house, ev, export)
        assertEquals(100.0, b.houseOffKwh, 1e-9)
        assertEquals(380.0, b.housePeakKwh, 1e-9)
        assertEquals(7.0, b.houseOffRate, 1e-9)
        assertEquals(30.0, b.housePeakRate, 1e-9)
        assertEquals(b.importTotal, b.houseOff + b.housePeak + b.ev + b.standing, 1e-9)
        assertEquals(13.0, b.exportRate, 1e-9)
        assertEquals(row.total - 13.0, b.net, 1e-9)
    }

    @Test
    fun currentTariffFromPricesFile() {
        val prices = JsonParser.parseString("""{"data":{"prices":[
            {"datefrom":"2025-01-01","dateto":"2040-01-01","offpeakRate":"0.07","offpeakStart":"00:00","offpeakStop":"05:00","peakRate":"0.30","exportRate":"0.15","standingCharge":"0.60"},
            {"datefrom":"2026-10-01","dateto":"2040-01-01","offpeakRate":"0.038","offpeakStart":"00:00","offpeakStop":"06:00","peakRate":"0.2563","exportRate":"0.13","standingCharge":"0.5714"}]}}""").asJsonObject
        val cur = TariffCompare.currentImport(prices, "2026-11-04")
        assertEquals("00:00-06:00", cur.window)
        assertEquals(3.8, cur.slotRates[11], 1e-9)
        assertEquals(25.63, cur.slotRates[12], 1e-9)
        assertEquals(13.0, TariffCompare.currentExport(prices, "2026-11-04").slotRates[0], 1e-9)
    }
}
