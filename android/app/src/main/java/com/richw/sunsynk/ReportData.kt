package com.richw.sunsynk

data class EnergyTotals(
    val days: Int,
    val calcExportKwh: Double, val suppliedExportKwh: Double,
    val calcExportPeakKwh: Double, val calcExportOffPeakKwh: Double,
    val calcImportKwh: Double, val suppliedImportKwh: Double,
    val calcImportPeakKwh: Double, val calcImportOffPeakKwh: Double,
    val calcPvKwh: Double, val suppliedPvKwh: Double,
    val calcLoadKwh: Double, val suppliedLoadKwh: Double,
)

data class CostData(
    val totalCost: Double,
    val totalCostWithoutSolar: Double,
    val segIncome: Double,
    val billSavingsIncSeg: Double,
)

data class SavingsData(
    val calcSavingsExcSeg: Double,
    val calcSavings: Double,
    val calcRoiPct: Double,
    val cumulativeSavings: Double,
    val altInvestValue: Double,
    val netRoiPct: Double,
    val offPeakShiftEnabled: Boolean,
    val offPeakImportKwh: Double,
    val offPeakExcessKwh: Double,
    val offPeakSavings: Double,
    val solarPaybackYears: Double?,
)

data class BatteryData(
    val enabled: Boolean,
    val chargeAmountKwh: Double,
    val chargeCost: Double,
    val nominalCost: Double,
    val lostExportCost: Double,
    val exportCost: Double,
    val exportedKwh: Double,
    val daysRunOut: Int,
    val shortfallKwh: Double,
    val netSaving: Double,
    val perDay: Double,
    val annualised: Double,
    val batteryPrice: Double,
    val batterySizeWh: Int,
    val paybackYears: Double?,
    val warnings: List<String>,
)

data class PriceResult(
    val label: String,
    val totals: EnergyTotals,
    val costs: CostData,
    val savings: SavingsData,
    val battery: BatteryData,
)

data class BatteryScenario(
    val label: String,
    val priceResults: List<PriceResult>,
)

data class AnalysisResult(
    val scenarios: List<BatteryScenario>,
)
