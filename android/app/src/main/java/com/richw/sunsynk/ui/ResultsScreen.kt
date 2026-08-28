package com.richw.sunsynk.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.richw.sunsynk.*

private val green = Color(0xFF2E7D32)
private val amber = Color(0xFFE65100)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ResultsScreen(result: AnalysisResult, onBack: () -> Unit) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Results") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        }
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
        ) {
            val scenarios = result.scenarios
            val multi = scenarios.size > 1 || (scenarios.firstOrNull()?.priceResults?.size ?: 0) > 1

            scenarios.forEach { scenario ->
                if (multi && scenario.label.isNotBlank()) {
                    item { ScenarioHeader(scenario.label) }
                }
                scenario.priceResults.forEach { pr ->
                    if (multi && pr.label.isNotBlank() && pr.label != scenario.label) {
                        item {
                            Text(
                                pr.label,
                                style = MaterialTheme.typography.titleSmall,
                                color = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.padding(start = 4.dp, top = 4.dp),
                            )
                        }
                    }
                    item { SummaryCard(pr) }
                    item { EnergyCard(pr.totals) }
                    item { CostsCard(pr.costs, pr.savings) }
                    item { RoiCard(pr.savings, pr.costs) }
                    if (pr.battery.enabled) {
                        item { BatteryCard(pr.battery) }
                    }
                }
            }

            item { Spacer(Modifier.height(8.dp)) }
        }
    }
}

@Composable
private fun ScenarioHeader(label: String) {
    Surface(
        color = MaterialTheme.colorScheme.primaryContainer,
        shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onPrimaryContainer,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
        )
    }
}

@Composable
private fun SummaryCard(pr: PriceResult) {
    ElevatedCard(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Summary", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                Text(
                    "${pr.totals.days} days",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(12.dp))

            Row(modifier = Modifier.fillMaxWidth()) {
                BigMetric(
                    label = "Bill saving (inc. SEG)",
                    value = "£${"%.2f".format(pr.costs.billSavingsIncSeg)}",
                    color = green,
                    modifier = Modifier.weight(1f),
                )
                if (pr.battery.enabled && pr.battery.netSaving > 0) {
                    BigMetric(
                        label = "Battery saving",
                        value = "£${"%.2f".format(pr.battery.netSaving)}",
                        color = green,
                        modifier = Modifier.weight(1f),
                    )
                }
            }

            Spacer(Modifier.height(12.dp))
            HorizontalDivider()
            Spacer(Modifier.height(10.dp))

            Row(modifier = Modifier.fillMaxWidth()) {
                SmallMetric("Net ROI", "${"%.2f".format(pr.savings.netRoiPct)}%", Modifier.weight(1f))
                SmallMetric("SEG income", "£${"%.2f".format(pr.costs.segIncome)}", Modifier.weight(1f))
                if (pr.savings.offPeakShiftEnabled) {
                    SmallMetric("Off-peak shift", "£${"%.2f".format(pr.savings.offPeakSavings)}", Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable
private fun EnergyCard(t: EnergyTotals) {
    ResultCard(title = "Energy Totals") {
        val perDay: (Double) -> String = { v -> "${"%.2f".format(v / t.days)} kWh/day" }

        EnergyRow("Export",
            calc = "${"%.1f".format(t.calcExportKwh)} kWh",
            reported = "${"%.1f".format(t.suppliedExportKwh)} kWh",
            detail = "Peak ${"%.1f".format(t.calcExportPeakKwh)}  Off-pk ${"%.1f".format(t.calcExportOffPeakKwh)}",
        )
        EnergyRow("Import",
            calc = "${"%.1f".format(t.calcImportKwh)} kWh",
            reported = "${"%.1f".format(t.suppliedImportKwh)} kWh",
            detail = "Peak ${"%.1f".format(t.calcImportPeakKwh)}  Off-pk ${"%.1f".format(t.calcImportOffPeakKwh)}",
        )
        EnergyRow("PV generation",
            calc = "${"%.1f".format(t.calcPvKwh)} kWh",
            reported = "${"%.1f".format(t.suppliedPvKwh)} kWh",
        )
        EnergyRow("Load",
            calc = "${"%.1f".format(t.calcLoadKwh)} kWh",
            reported = "${"%.1f".format(t.suppliedLoadKwh)} kWh",
        )
        if (t.days > 0) {
            HorizontalDivider(modifier = Modifier.padding(vertical = 6.dp))
            Row(modifier = Modifier.fillMaxWidth()) {
                SmallMetric("Export/day", perDay(t.calcExportKwh), Modifier.weight(1f))
                SmallMetric("Import/day", perDay(t.calcImportKwh), Modifier.weight(1f))
                SmallMetric("PV/day", perDay(t.calcPvKwh), Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun CostsCard(c: CostData, s: SavingsData) {
    ResultCard(title = "Energy Costs") {
        MetricRow("Paid to energy company", "£${"%.2f".format(c.totalCost)}")
        MetricRow("Estimated without solar", "£${"%.2f".format(c.totalCostWithoutSolar)}")
        HorizontalDivider(modifier = Modifier.padding(vertical = 6.dp))
        MetricRow("SEG income", "+ £${"%.2f".format(c.segIncome)}", valueColor = green)
        MetricRow("Bill saving (inc. SEG)", "£${"%.2f".format(c.billSavingsIncSeg)}", valueColor = green)
        if (s.offPeakShiftEnabled && s.offPeakExcessKwh > 0) {
            HorizontalDivider(modifier = Modifier.padding(vertical = 6.dp))
            MetricRow("Off-peak import", "${"%.2f".format(s.offPeakImportKwh)} kWh")
            MetricRow("Shifted to off-peak", "${"%.2f".format(s.offPeakExcessKwh)} kWh excess")
            MetricRow("Shifting saving", "£${"%.2f".format(s.offPeakSavings)}", valueColor = green)
        }
    }
}

@Composable
private fun RoiCard(s: SavingsData, c: CostData) {
    ResultCard(title = "Return on Investment") {
        MetricRow("Solar savings (excl. SEG)", "£${"%.2f".format(s.calcSavingsExcSeg)}")
        MetricRow("Solar savings (incl. SEG)", "£${"%.2f".format(c.billSavingsIncSeg)}", valueColor = green)
        MetricRow("Total savings (with shifting)", "£${"%.2f".format(s.calcSavings)}", valueColor = green)
        HorizontalDivider(modifier = Modifier.padding(vertical = 6.dp))
        MetricRow("Cumulative savings (+ interest)", "£${"%.2f".format(s.cumulativeSavings)}", valueColor = green)
        MetricRow("Alt. investment value", "£${"%.2f".format(s.altInvestValue)}")
        MetricRow("Net ROI", "${"%.2f".format(s.netRoiPct)}%",
            valueColor = if (s.netRoiPct >= 0) green else MaterialTheme.colorScheme.error)
        if (s.solarPaybackYears != null) {
            MetricRow("Payback period", "${"%.1f".format(s.solarPaybackYears)} years")
        }
    }
}

@Composable
private fun BatteryCard(b: BatteryData) {
    ResultCard(title = "Virtual Battery  ${b.batterySizeWh / 1000.0}kWh") {
        b.warnings.forEach { w ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 6.dp),
                verticalAlignment = Alignment.Top,
            ) {
                Text("⚠ ", color = amber, fontWeight = FontWeight.Bold)
                Text(w, style = MaterialTheme.typography.bodySmall, color = amber, modifier = Modifier.weight(1f))
            }
        }
        if (b.warnings.isNotEmpty()) HorizontalDivider(modifier = Modifier.padding(bottom = 8.dp))

        Row(modifier = Modifier.fillMaxWidth()) {
            BigMetric("Net saving", "£${"%.2f".format(b.netSaving)}", green, Modifier.weight(1f))
            BigMetric("Annualised", "£${"%.2f".format(b.annualised)}", green, Modifier.weight(1f))
        }
        Spacer(Modifier.height(8.dp))
        HorizontalDivider(modifier = Modifier.padding(bottom = 8.dp))

        MetricRow("Charged from grid", "${"%.2f".format(b.chargeAmountKwh)} kWh")
        MetricRow("Charging cost", "£${"%.2f".format(b.chargeCost)}")
        MetricRow("Peak-rate value delivered", "£${"%.2f".format(b.nominalCost)}")
        if (b.lostExportCost > 0) {
            MetricRow("Foregone export (PV→battery)", "− £${"%.2f".format(b.lostExportCost)}")
        }
        if (b.exportedKwh > 0) {
            MetricRow("Re-exported", "${"%.2f".format(b.exportedKwh)} kWh = £${"%.2f".format(b.exportCost)}")
        }
        MetricRow("Per day", "£${"%.2f".format(b.perDay)}")
        if (b.daysRunOut > 0) {
            MetricRow("Days ran short", "${b.daysRunOut}  (${b.shortfallKwh} kWh total)",
                valueColor = MaterialTheme.colorScheme.error)
        }
        if (b.batteryPrice > 0) {
            HorizontalDivider(modifier = Modifier.padding(vertical = 6.dp))
            MetricRow("Battery cost", "£${"%.2f".format(b.batteryPrice)}")
            if (b.paybackYears != null) {
                MetricRow("Payback period", "${"%.1f".format(b.paybackYears)} years")
            }
        }
    }
}

// ── Shared primitives ─────────────────────────────────────────────────────────

@Composable
private fun ResultCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.height(10.dp))
            content()
        }
    }
}

@Composable
private fun MetricRow(label: String, value: String, valueColor: Color = MaterialTheme.colorScheme.onSurface) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
        Text(value, style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium, color = valueColor)
    }
}

@Composable
private fun EnergyRow(label: String, calc: String, reported: String, detail: String? = null) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(label, style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
            Text(calc, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            Text("  |  $reported", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (detail != null) {
            Text(detail, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 8.dp), fontSize = 11.sp)
        }
    }
}

@Composable
private fun BigMetric(label: String, value: String, color: Color, modifier: Modifier = Modifier) {
    Column(modifier = modifier) {
        Text(label, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold, color = color)
    }
}

@Composable
private fun SmallMetric(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier = modifier) {
        Text(label, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 11.sp)
        Text(value, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
    }
}
