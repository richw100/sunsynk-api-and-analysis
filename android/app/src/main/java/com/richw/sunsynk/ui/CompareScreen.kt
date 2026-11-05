package com.richw.sunsynk.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.richw.sunsynk.TariffsViewModel
import com.richw.sunsynk.analysis.TariffCompare

private fun gbp(v: Double) = "£%,.0f".format(v)

/** Two ticked options from the Tariffs tab side by side: rates, the electricity breakdown,
 *  gas, totals and the solar ROI at each option's rates. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CompareScreen(vm: TariffsViewModel) {
    val state by vm.state.collectAsState()
    val profile = state.profile
    val picks = state.compare
    Scaffold(topBar = { TopAppBar(title = { Text("Compare") }) }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 12.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(2.dp)) {
            if (profile == null || picks.size < 2) {
                Text("On the Tariffs tab, tick two import + export pairs, or a pair and a gas tariff, or two gas " +
                    "tariffs (with or without pairs) to compare them here.",
                    style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 12.dp))
                return@Column
            }
            val cols = picks.map { opt ->
                val pair = opt.pair
                val b = TariffCompare.pairBreakdown(pair, profile.house, profile.ev, profile.export,
                    profile.monthSlotHouse, profile.monthSlotEv)
                val solar = TariffCompare.solarRoi(pair, profile)
                val gas = opt.gas
                listOf(
                    "Import" to "${pair.imp.tariff.supplier} ${pair.imp.tariff.name}",
                    "Export" to "${pair.exp.tariff.supplier} ${pair.exp.tariff.name}",
                    "Import rates (p)" to pair.imp.ratesText,
                    "Standing (p/day)" to "%.2f".format(pair.imp.tariff.standing),
                    "Export rates (p)" to pair.exp.ratesText,
                    "House peak" to "%,.0f kWh %s".format(b.housePeakKwh, gbp(b.housePeak)),
                    "House off-peak" to "%,.0f kWh %s".format(b.houseOffKwh, gbp(b.houseOff)),
                    "EV (overnight)" to "%,.0f kWh %s (%.2fp)".format(b.evKwh, gbp(b.ev), b.evRate),
                    "Standing" to gbp(b.standing),
                    "Import total" to gbp(b.importTotal),
                    "Export income" to "%,.0f kWh −%s (%.2fp)".format(b.exportKwh, gbp(b.export), b.exportRate),
                    "Electricity net" to gbp(b.net),
                    "Gas" to (gas?.let { "${it.tariff.supplier} ${it.tariff.name}: %.2fp, %.2fp/day".format(it.tariff.gasRate, it.tariff.gasStanding) } ?: "-"),
                    "Gas £/yr" to (gas?.let { gbp(it.cost) } ?: "-"),
                    "TOTAL £/yr" to gbp(opt.total),
                    "Solar: house use" to (solar?.let { "%,.0f kWh %s".format(it.selfUseKwh, gbp(it.selfUse)) } ?: "-"),
                    "Solar: into car" to (solar?.let { "%,.0f kWh %s".format(it.evKwh, gbp(it.ev)) } ?: "-"),
                    "Solar: exported" to (solar?.let { "%,.0f kWh %s".format(it.exportKwh, gbp(it.export)) } ?: "-"),
                    "Solar saving £/yr" to (solar?.let { gbp(it.saving) } ?: "re-run analysis"),
                    "Solar ROI" to (solar?.takeIf { it.cost > 0 }?.let { "%.1f%%/yr".format(it.roiPct) } ?: "-"),
                    "Payback" to (solar?.takeIf { it.cost > 0 }?.let { "%.1f years".format(it.paybackYears) } ?: "-"),
                )
            }
            val (a, bCol) = cols
            val totals = picks.map { it.total }
            val diff = totals[1] - totals[0]
            Text("Option B costs ${if (diff >= 0) "£%,.0f more".format(diff) else "£%,.0f less".format(-diff)} a year than A",
                style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(vertical = 8.dp))
            Row(Modifier.fillMaxWidth()) {
                Text("", Modifier.weight(0.9f))
                Text("A", Modifier.weight(1f), fontWeight = FontWeight.Bold)
                Text("B", Modifier.weight(1f), fontWeight = FontWeight.Bold)
            }
            HorizontalDivider()
            a.zip(bCol).forEach { (ea, eb) ->
                val bold = ea.first.startsWith("TOTAL") || ea.first.startsWith("Solar saving") || ea.first == "Electricity net"
                Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
                    Text(ea.first, Modifier.weight(0.9f), style = MaterialTheme.typography.labelSmall)
                    Text(ea.second, Modifier.weight(1f).padding(end = 4.dp), style = MaterialTheme.typography.bodySmall,
                        fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal)
                    Text(eb.second, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall,
                        fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal)
                }
                HorizontalDivider(thickness = 0.5.dp)
            }
            Text("EV charging is assumed to be all at each import tariff's overnight (or smart-EV) rate. Solar " +
                "saving = PV the house used at the import rates it displaced + PV into the car at the overnight rate " +
                "+ export income; ROI and payback are on the panels' cost (config originalPrice).",
                style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 8.dp))
            TextButton(onClick = vm::clearSelection) { Text("Clear selection") }
        }
    }
}
