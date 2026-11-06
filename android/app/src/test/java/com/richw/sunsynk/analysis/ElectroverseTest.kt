package com.richw.sunsynk.analysis

import com.google.gson.JsonParser
import com.richw.sunsynk.api.ElectroverseClient
import com.richw.sunsynk.api.PublicCharge
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

class ElectroverseTest {
    @Test
    fun sessionCookiesKeepOnlyTheSessionOnes() {
        assertEquals(mapOf("token" to "a.b.c", "refreshToken" to "r1", "csrftoken" to "x"),
            ElectroverseClient.sessionCookies("g_state={}; token=a.b.c; refreshToken=r1; csrftoken=x; other=1; empty="))
        assertEquals(emptyMap<String, String>(), ElectroverseClient.sessionCookies(null))
    }

    @Test
    fun parsePaymentsKeepsReceiptsInMajorUnitsNetOfRefunds() {
        val nodes = JsonParser.parseString("""[
          {"pk":1,"grossAmount":-2350,"currency":{"isoCode":"GBP","minorUnitConversion":100},
           "paymentSource":{"__typename":"ReceiptPaymentType","pk":11,"periodStart":"2026-08-07T18:48:00Z",
             "periodEnd":"2026-08-07T19:30:00Z","kwhUsed":"37.0700","operator":{"name":"Op A"},"location":{"address":"1 Road"}},
           "paymentRecord":{"status":"SUCCEEDED","hasRefund":true,"refundedAmount":350}},
          {"pk":2,"grossAmount":1000,"currency":{"isoCode":"EUR","minorUnitConversion":100},
           "paymentSource":{"__typename":"ReceiptPaymentType","pk":12,"periodStart":"2026-08-10T23:30:00Z","kwhUsed":20},
           "paymentRecord":{"hasRefund":false}},
          {"pk":3,"grossAmount":500,"currency":{"isoCode":"GBP"},"paymentSource":{"__typename":"RFIDCardOrderPaymentType","pk":13}}
        ]""").asJsonArray.map { it.asJsonObject }
        val c = ElectroverseClient.parsePayments(nodes)
        assertEquals(listOf(11, 12), c.map { it.pk })
        assertEquals(20.0, c[0].gross, 1e-9)
        assertEquals("Op A", c[0].operator)
        assertEquals(10.0, c[1].gross, 1e-9)
        assertEquals(null, c[1].end)
    }

    @Test
    fun publicChargesJoinTheMonthsAsTheirOwnShare() {
        val charges = listOf(
            PublicCharge(1, Instant.parse("2026-08-10T23:30:00Z"), null, 20.0, 10.0, "EUR", "", ""),   // 00:30 BST on the 11th
            PublicCharge(2, Instant.parse("2026-08-07T18:48:00Z"), null, 30.0, 15.0, "GBP", "", ""),
            PublicCharge(3, Instant.parse("2026-08-08T10:00:00Z"), null, 5.0, 3.0, "USD", "", ""))
        val pub = EvSavings.publicDays(charges, 0.8)
        assertEquals(listOf("2026-08-11", "2026-08-07"), pub.map { it.date })
        assertEquals(8.0, pub[0].cost, 1e-9)
        val n = listOf(LocalDate.of(2026, 8, 3) to 150.0)
        val cfg = EvSavingsConfig(mpg = 50.0, milesPerKwh = 4.0, chargeEfficiency = 1.0)
        val r = EvSavings.calculate(listOf(EvDay("2026-08-05", 10.0, 0.5)) + pub, cfg, n, emptyMap(), 0, LocalDate.of(2026, 9, 1))
        val aug = r.months.single()
        assertEquals(60.0, aug.kwh, 1e-9)
        assertEquals(50.0, aug.publicKwh, 1e-9)
        assertEquals(200.0, aug.publicMiles, 1e-9)
        assertEquals(23.0, aug.publicCost, 1e-9)
        assertEquals(200.0 / 50 * EvSavings.LITRES_PER_GALLON * 1.5, aug.publicPetrolCost, 1e-9)
        assertEquals(aug.petrolCost - 23.5, aug.saved, 1e-9)
    }

    @Test
    fun publicSettingsCustomChargesAndRepayments() {
        val n = listOf(LocalDate.of(2026, 8, 3) to 150.0)
        val cfg = EvSavingsConfig(mpg = 50.0, milesPerKwh = 4.0, chargeEfficiency = 1.0, eurToGbp = 0.8,
            publicMpg = 40.0, publicFuelPrice = 2.0, publicFuelCurrency = "EUR",
            customCharges = listOf(CustomCharge("a", "2026-08-10", "Paris", 10.0, 5.0, "EUR"),
                CustomCharge("b", "2026-08-11", "Excluded", 99.0, 99.0, "GBP", excluded = true)),
            reimbursements = listOf(Reimbursement("r", "2026-08-31", 30.0, "miles")))
        val pub = EvSavings.publicDays(emptyList(), cfg.eurToGbp, cfg.customCharges)
        assertEquals(1, pub.size)
        assertEquals(4.0, pub[0].cost, 1e-9)                              // €5 at 0.8
        assertEquals(160.0, EvSavings.publicPence(cfg)!!, 1e-9)            // €2.00/litre = 160p
        val home = EvDay("2026-08-05", 10.0, 0.5)
        val r = EvSavings.calculate(listOf(home) + pub, cfg, n, emptyMap(), 0, LocalDate.of(2026, 9, 1))
        val aug = r.months.single()
        val pubLitres = 40.0 / 40 * EvSavings.LITRES_PER_GALLON          // 40 miles at 40 mpg
        assertEquals(pubLitres * 1.60, aug.publicPetrolCost, 1e-9)
        assertEquals(40.0 / 50 * EvSavings.LITRES_PER_GALLON * 1.5 + pubLitres * 1.60, aug.petrolCost, 1e-9)
        assertEquals(0.0, aug.reimbursed, 1e-9)                           // repayments don't change the saving
        assertEquals(aug.petrolCost - 4.5, aug.saved, 1e-9)
    }

    @Test
    fun parseElectroFindBackup() {
        val (charges, st) = EvSavings.parseElectroFind("""{"tripLog":{"customCharges":[
            {"id":"x1","dateEpochMillis":1786000000000,"name":"Paris","kwh":12.75,"cost":8.05,"idleCost":0.5,"currency":"EUR","excluded":false},
            {"id":"x2","dateEpochMillis":1786000000000,"name":"Skip","kwh":1.0,"cost":1.0,"idleCost":0.0,"currency":"GBP","excluded":true}],
            "settings":{"eurToGbpRate":0.86,"iceMpg":53.0,"petrolPricePerLitre":1.7}}}""")
        assertEquals(listOf("ef-x1", "ef-x2"), charges.map { it.id })
        assertEquals(8.55, charges[0].cost, 1e-9)
        assertEquals(true, charges[1].excluded)
        assertEquals(ElectroFindSettings(0.86, 53.0, 1.7), st)
    }

    @Test
    fun receiptEditsOverlayTheDownloadAndTheListHoldsEverything() {
        val receipts = listOf(
            PublicCharge(1, Instant.parse("2026-08-07T18:48:00Z"), null, 37.07, 18.54, "GBP", "Op A", "1 Road"),
            PublicCharge(2, Instant.parse("2026-08-08T14:00:00Z"), null, 45.91, 25.45, "EUR", "Op B", ""))
        val custom = listOf(CustomCharge("c", "2026-08-30", "Paris", 12.75, 8.05, "EUR"))
        val edits = listOf(ReceiptEdit(1, kwh = 30.0, cost = 15.0), ReceiptEdit(2, excluded = true))
        val rows = EvSavings.publicRows(receipts, custom, edits)
        assertEquals(listOf("2026-08-30", "2026-08-08", "2026-08-07"), rows.map { it.date })
        assertEquals("Op A, 1 Road", rows[2].name)
        assertEquals(30.0, rows[2].kwh, 1e-9); assertEquals(true, rows[2].edited)
        assertEquals(listOf("Other", "Electroverse", "Electroverse"), rows.map { it.source })
        val days = EvSavings.publicDays(receipts, 0.8, custom, edits)
        assertEquals(listOf(30.0, 12.75), days.sortedBy { it.date }.map { it.kwh })   // receipt 2 left out
        assertEquals(15.0 + 8.05 * 0.8, days.sumOf { it.cost }, 1e-9)
    }

    @Test
    fun mileageRepaymentsAreInformationOnly() {
        // The user's example: paid £135, charging £15, petrol £45 -> EV profit £120, petrol £90, EV saving £30.
        val n = listOf(LocalDate.of(2026, 8, 3) to 150.0)                  // home: 150p/L at 50 mpg
        val cfg = EvSavingsConfig(mpg = 50.0, milesPerKwh = 4.0, chargeEfficiency = 1.0, publicMpg = 40.0,
            reimbursements = listOf(
                Reimbursement("a", "2026-08-10", 135.0, miles = 300.0, ratePence = 45.0),   // public charge that day
                Reimbursement("b", "2026-08-20", 45.0, miles = 100.0, ratePence = 45.0),    // none: home price
                Reimbursement("c", "2026-08-21", 10.0)))                                    // no miles
        val home = EvDay("2026-08-05", 50.0, 3.0)                                       // 200 mi
        val pub = EvDay("2026-08-10", 25.0, 12.0, public = true, pence = 160.0)          // 100 mi
        val base = EvSavings.calculate(listOf(home, pub), cfg.copy(reimbursements = emptyList()), n, emptyMap(), 0, LocalDate.of(2026, 9, 1))
        val r = EvSavings.calculate(listOf(home, pub), cfg, n, emptyMap(), 0, LocalDate.of(2026, 9, 1))
        assertEquals(base.total.saved, r.total.saved, 1e-9)                         // unchanged by repayments
        val (a, b, c) = r.repayments
        val perMile = 15.0 / 300.0                                                     // August EV £/mile
        assertEquals(300.0 / 40 * EvSavings.LITRES_PER_GALLON * 1.60, a.petrolCost!!, 1e-9)
        assertEquals(300.0 * perMile, a.chargingCost!!, 1e-9)
        assertEquals(135.0 - 15.0, a.evProfit!!, 1e-9)
        assertEquals(a.evProfit!! - a.petrolProfit!!, a.evSaving!!, 1e-9)
        assertEquals(100.0 / 50 * EvSavings.LITRES_PER_GALLON * 1.50, b.petrolCost!!, 1e-9)
        assertNull(c.petrolCost); assertNull(c.evSaving)
    }
}
