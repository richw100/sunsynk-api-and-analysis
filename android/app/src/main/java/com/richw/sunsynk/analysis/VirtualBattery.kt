package com.richw.sunsynk.analysis

import java.time.LocalTime
import java.time.format.DateTimeFormatter

class VirtualBattery(
    val priceData: PriceData,
    val batterySize: Int = 5000,
    val pvEnabled: Int = 0,
    val startCharging: Int = 1000,
    val stopCharging: Int = 2000,
    val exportWindowStart: String = "17:00",
    val exportWindowStop: String = "19:00",
    val dischargeEfficiency: Double = 0.92,
    val pvChargeEfficiency: Double = 0.96,
    val maxOutputW: Double = 2400.0,
    val useExport: Boolean = false,
    val chargeEfficiency: Double = 1.0,
    val gridCharge: Boolean = true,
    val dischargeReserveWh: Int = 0,
    val dischargeReserveUntilStr: String = "17:00",
) {
    private val fmt = DateTimeFormatter.ofPattern("HH:mm")

    val export: Int = if (useExport) 1 else 0
    private val exportStart: LocalTime = LocalTime.parse(exportWindowStart, fmt)
    private val exportStop: LocalTime = LocalTime.parse(exportWindowStop, fmt)
    private val reserveUntil: LocalTime = LocalTime.parse(dischargeReserveUntilStr, fmt)

    private val exportIntervals: Int = run {
        val startSec = exportStart.toSecondOfDay().toLong()
        val stopSec = exportStop.toSecondOfDay().toLong()
        val windowSec = (stopSec - startSec).coerceAtLeast(300)
        maxOf(1, (windowSec / 300).toInt())
    }

    var batteryStatus: Double = batterySize.toDouble()
    var chargeAmount: Double = 0.0
    var shouldPvCharge: Int = 0
    var pvInput: Double = 0.0
    var pvDiverted: Double = 0.0
    var drawn: Double = 0.0
    var lostExportCost: Double = 0.0
    var exportCost: Double = 0.0
    var savings: Double = 0.0
    var costFromGrid: Double = 0.0
    var nominalCost: Double = 0.0
    var exported: Double = 0.0
    var extraRequired: Double = 0.0
    var daysRunOut: Int = 0

    fun recharge() {
        if (gridCharge) {
            chargeAmount += batterySize - batteryStatus
            batteryStatus = batterySize.toDouble()
        }
    }

    fun discharge(time: LocalTime) {
        if (export == 1) {
            if (time.isAfter(exportStart) && time.isBefore(exportStop)) {
                val runDownAmount = 1000.0
                if (batteryStatus > runDownAmount) {
                    val max5MinDrain = (maxOutputW / 12) / dischargeEfficiency
                    val fiveMinDrain = minOf(
                        max5MinDrain,
                        (batterySize - runDownAmount) / exportIntervals
                    )
                    batteryStatus -= fiveMinDrain
                    exported += fiveMinDrain * dischargeEfficiency
                }
            }
        }
    }

    fun setRanOut() {
        daysRunOut++
    }

    fun utilise(value: Double, time: LocalTime): Double {
        discharge(time)
        val actualOutput = minOf(value, maxOutputW / 12)
        val availableCharge = if (dischargeReserveWh > 0 && time.isBefore(reserveUntil)) {
            maxOf(0.0, batteryStatus - dischargeReserveWh)
        } else {
            batteryStatus
        }
        val deliverable = availableCharge * dischargeEfficiency

        return if (actualOutput > deliverable) {
            drawn += deliverable
            val shortfall = actualOutput - deliverable
            batteryStatus -= availableCharge
            extraRequired += shortfall
            shortfall
        } else {
            batteryStatus -= actualOutput / dischargeEfficiency
            drawn += actualOutput
            0.0
        }
    }

    fun pvCharge(value: Double, time: LocalTime) {
        discharge(time)
        if (pvEnabled == 1) {
            if (batteryStatus < startCharging) shouldPvCharge = 1
            if (batteryStatus >= stopCharging) shouldPvCharge = 0
        }
        if (shouldPvCharge == 1) {
            val postEfficiency = value * pvChargeEfficiency
            if (batteryStatus + postEfficiency <= batterySize) {
                batteryStatus += postEfficiency
                pvInput += postEfficiency
                pvDiverted += value
            }
        }
    }

    fun totalDrawnKwh() = (drawn / 1000).round2()
    fun getChargeAmountKwh() = (chargeAmount / 1000).round2()
    fun getPvChargeKwh() = (pvInput / 1000).round2()

    fun getSavings() {
        costFromGrid = ((chargeAmount / chargeEfficiency / 1000) * priceData.currentOffPeak).round2()
        nominalCost = ((drawn * priceData.currentPeak) / 1000).round2()
        lostExportCost = ((pvDiverted * priceData.currentExport) / 1000).round2()
        exportCost = ((exported / 1000) * priceData.currentExport).round2()
        savings = nominalCost + exportCost - costFromGrid - lostExportCost
    }
}

fun Double.round2() = (Math.round(this * 100.0) / 100.0)
