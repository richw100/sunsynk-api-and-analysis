package com.richw.sunsynk.analysis

import java.time.LocalDate

data class PriceData(
    var currentOffPeak: Double = 0.067,
    var currentPeak: Double = 28.26,
    var currentOffPeakStart: String = "00:00",
    var currentOffPeakStop: String = "00:07",
    var currentStart: LocalDate = LocalDate.of(2025, 7, 10),
    var currentStop: LocalDate = LocalDate.of(2026, 9, 10),
    var offPeakAverage: Double = 0.96,
    var offPeakBaselineKwh: Double = 0.96,
    var currentExport: Double = 0.165,
    var standingCharge: Double = 0.6,
    var originalPrice: Double = 6206.47,
    var interestRate: Double = 3.7,
)
