package com.richw.sunsynk.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.richw.sunsynk.TariffsUiState
import com.richw.sunsynk.TariffsViewModel
import com.richw.sunsynk.analysis.EvSplit
import com.richw.sunsynk.analysis.ExportRow
import com.richw.sunsynk.analysis.ImportRow
import com.richw.sunsynk.analysis.Pair3
import com.richw.sunsynk.analysis.TariffCompare
import com.richw.sunsynk.analysis.TariffProfile

private val good = Color(0xFF2E7D32)
private val bad = Color(0xFFC62828)
private val MONTHS = listOf("J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D")

private fun gbp(v: Double) = "£%,.0f".format(v)
private fun signed(v: Double) = (if (v >= 0) "+" else "−") + "£%,.0f".format(kotlin.math.abs(v))

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TariffsScreen(vm: TariffsViewModel) {
    val state by vm.state.collectAsState()
    LaunchedEffect(Unit) { vm.reloadProfile() }
    Scaffold(topBar = { TopAppBar(title = { Text("Tariff comparison") }) }) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
        ) {
            item { ControlsCard(state, vm) }
            val profile = state.profile
            if (profile == null) {
                item {
                    Text("Run the analysis first: its projected year of half-hourly import and export " +
                        "is what the tariffs are priced on.", style = MaterialTheme.typography.bodyMedium)
                }
                return@LazyColumn
            }
            item { ProfileCard(profile) }
            if (state.pairs.isNotEmpty()) {
                item { SectionTitle("Best import + export pairs", "net £/year = import − export; tap for detail") }
                items(state.pairs) { PairCard(it, profile) }
            }
            if (state.imports.isNotEmpty()) {
                item { SectionTitle("Import tariffs", "house + EV (re-timed) + standing charge; tap for split") }
                items(state.imports) { ImportCard(it) }
            }
            if (state.exports.isNotEmpty()) {
                item { SectionTitle("Export tariffs", "projected export priced per half-hour") }
                items(state.exports) { ExportCard(it) }
                item {
                    Text("Supplier requirements (e.g. export only with that supplier's import) are assumed " +
                        "from the product - check before switching. Dynamic rates (Agile) are recent " +
                        "averages, not a forecast; eligibility and exit fees aren't checked.",
                        style = MaterialTheme.typography.bodySmall)
                }
            }
            if (state.log.isNotEmpty()) item { LogCard(state.log) }
        }
    }
}

@Composable
private fun ControlsCard(state: TariffsUiState, vm: TariffsViewModel) {
    var region by remember(state.region) { mutableStateOf(state.region) }
    var postcode by remember { mutableStateOf("") }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = region, onValueChange = { region = it.take(1).uppercase(); vm.setRegion(region) },
                    label = { Text("Region") }, singleLine = true, modifier = Modifier.width(96.dp),
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters),
                )
                OutlinedTextField(
                    value = postcode, onValueChange = { postcode = it }, label = { Text("or postcode") },
                    singleLine = true, modifier = Modifier.weight(1f),
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters),
                )
                TextButton(onClick = { vm.lookupPostcode(postcode); postcode = "" },
                    enabled = postcode.isNotBlank() && state.busy == null) { Text("Find") }
            }
            Text("The postcode is only sent to Octopus's public region lookup; just the letter is kept.",
                style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { vm.refresh() }, enabled = state.busy == null) { Text("Get latest rates") }
                OutlinedButton(onClick = { vm.refresh(force = true) }, enabled = state.busy == null) { Text("Re-fetch") }
            }
            state.busy?.let {
                Text(it, style = MaterialTheme.typography.bodySmall)
                LinearProgressIndicator(Modifier.fillMaxWidth())
            }
            state.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            state.fetchedAt?.let {
                Text("Rates fetched at $it (cached for today)", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun ProfileCard(p: TariffProfile) {
    val house = p.house.sum()
    val ev = p.ev.sum()
    val export = p.export.sum()
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Projected year (analysis run ${p.savedAt})", style = MaterialTheme.typography.titleSmall)
            Text("Import ${"%,.0f".format(house + ev)} kWh = house ${"%,.0f".format(house)} + EV ${"%,.0f".format(ev)}" +
                "   Export ${"%,.0f".format(export)} kWh", style = MaterialTheme.typography.bodyMedium)
            if (!p.seasonal) Text("Provisional: not all 12 months seen yet", color = bad,
                style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(4.dp))
            MonthRow("", MONTHS, bold = true)
            MonthRow("Exp", (0 until 12).map { m -> p.monthlyExport[m]?.let { "%.0f".format(it) } ?: "-" })
            MonthRow("House", (0 until 12).map { m -> p.monthlyHouse[m]?.let { "%.0f".format(it) } ?: "-" })
            Text("kWh, average month (house import excludes the EV)", style = MaterialTheme.typography.bodySmall)
            val cur = p.currentImport
            Text("Current: ${TariffCompare.describeRates(cur.slotRates).second} p/kWh, " +
                "standing ${"%.2f".format(cur.standing)}p/day; export ${"%.2f".format(p.currentExport.slotRates[0])}p",
                style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun MonthRow(label: String, cells: List<String>, bold: Boolean = false) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, Modifier.width(44.dp), style = MaterialTheme.typography.labelSmall)
        cells.forEach {
            Text(it, Modifier.weight(1f), style = MaterialTheme.typography.labelSmall,
                fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal, maxLines = 1)
        }
    }
}

@Composable
private fun SectionTitle(title: String, subtitle: String) {
    Column(Modifier.padding(top = 4.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
        Text(subtitle, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun Expandable(header: @Composable RowScope.() -> Unit, highlight: Boolean = false,
                       body: @Composable ColumnScope.() -> Unit) {
    var open by remember { mutableStateOf(false) }
    Card(
        Modifier.fillMaxWidth().clickable { open = !open },
        colors = if (highlight) CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer)
        else CardDefaults.cardColors(),
    ) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                header()
                Icon(if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore, contentDescription = null)
            }
            if (open) Column(Modifier.padding(top = 6.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) { body() }
        }
    }
}

@Composable
private fun Vs(v: Double, isCurrent: Boolean, higherIsBetter: Boolean = false) {
    if (isCurrent) Text("current", style = MaterialTheme.typography.labelSmall)
    else {
        val better = if (higherIsBetter) v > 0.005 else v < -0.005
        val worse = if (higherIsBetter) v < -0.005 else v > 0.005
        Text(signed(v), style = MaterialTheme.typography.labelMedium,
            color = if (better) good else if (worse) bad else MaterialTheme.colorScheme.onSurface)
    }
}

@Composable
private fun Line(label: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun PairCard(pair: Pair3, profile: TariffProfile) {
    Expandable(highlight = pair.isCurrent, header = {
        Column(Modifier.weight(1f)) {
            Text("${pair.imp.tariff.supplier} ${pair.imp.tariff.name}", style = MaterialTheme.typography.bodyMedium,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("+ ${pair.exp.tariff.supplier} ${pair.exp.tariff.name}", style = MaterialTheme.typography.bodySmall,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(gbp(pair.net), fontWeight = FontWeight.Bold)
            Text("net/yr", style = MaterialTheme.typography.labelSmall)
        }
    }) {
        val b = TariffCompare.pairBreakdown(pair, profile.house, profile.ev, profile.export)
        Line("Import rates", "${pair.imp.ratesText} p")
        Line("Standing charge", "%.2fp/day".format(pair.imp.tariff.standing))
        Line("Export rates", "${pair.exp.ratesText} p")
        HorizontalDivider(Modifier.padding(vertical = 2.dp))
        Line("House peak", "%,.0f kWh  %s  (%.2fp)".format(b.housePeakKwh, gbp(b.housePeak), b.housePeakRate))
        if (b.houseOffKwh > 0) Line("House off-peak", "%,.0f kWh  %s  (%.2fp)".format(b.houseOffKwh, gbp(b.houseOff), b.houseOffRate))
        Line("EV", "%,.0f kWh  %s  (%.2fp)".format(b.evKwh, gbp(b.ev), b.evRate))
        evSplitText(b.evSplit)?.let { Line("", it) }
        Line("Standing", gbp(b.standing))
        Line("Import total", gbp(b.importTotal))
        Line("Export", "%,.0f kWh  −%s  (%.2fp)".format(b.exportKwh, gbp(b.export), b.exportRate))
        Line("Net", gbp(b.net))
        if (pair.exp.tariff.requires != null) Line("Export needs import from", pair.exp.tariff.requires!!)
        val notes = listOfNotNull(pair.imp.tariff.note.ifBlank { null }, pair.exp.tariff.note.ifBlank { null })
        notes.forEach { Text(it, style = MaterialTheme.typography.labelSmall) }
    }
}

/** "1,970 kWh @ 3.80p + 46 kWh @ 25.63p"; null when there's no EV energy. */
private fun evSplitText(s: EvSplit): String? = when {
    s.offKwh + s.peakKwh < 0.5 -> null
    s.peakKwh < 0.5 -> "%,.0f kWh @ %.2fp".format(s.offKwh, s.offRate)
    else -> "%,.0f kWh @ %.2fp + %,.0f kWh @ %.2fp".format(s.offKwh, s.offRate, s.peakKwh, s.peakRate)
}

@Composable
private fun ImportCard(r: ImportRow) {
    Expandable(highlight = r.isCurrent, header = {
        Column(Modifier.weight(1f)) {
            Text("${r.tariff.supplier} ${r.tariff.name}", style = MaterialTheme.typography.bodyMedium,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("${r.ratesText} p · SC %.2fp · ${r.tariff.kind.ifBlank { r.rateType }}".format(r.tariff.standing) +
                (if (r.tariff.smartEv) " · smart EV" else ""), style = MaterialTheme.typography.bodySmall,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(gbp(r.total), fontWeight = FontWeight.Bold)
            Vs(r.vsCurrent, r.isCurrent)
        }
    }) {
        Line("House", gbp(r.houseCost))
        Line("EV as now / re-timed", "${gbp(r.evNow)} / ${gbp(r.evRetimed)}  (%.2fp)".format(r.evPPerKwh))
        evSplitText(r.evSplit)?.let { Line("", it) }
        Line("Standing charge", gbp(r.standingYear))
        Line("Total (EV as now)", gbp(r.asNow))
        if (r.tariff.note.isNotBlank()) Text(r.tariff.note, style = MaterialTheme.typography.labelSmall)
        Text("Source: ${r.tariff.source}", style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun ExportCard(r: ExportRow) {
    Expandable(highlight = r.isCurrent, header = {
        Column(Modifier.weight(1f)) {
            Text("${r.tariff.supplier} ${r.tariff.name}", style = MaterialTheme.typography.bodyMedium,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("${r.ratesText} p" + (r.tariff.requires?.let { " · needs $it import" } ?: ""),
                style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(gbp(r.income), fontWeight = FontWeight.Bold)
            Vs(r.vsCurrent, r.isCurrent, higherIsBetter = true)
        }
    }) {
        if (r.tariff.note.isNotBlank()) Text(r.tariff.note, style = MaterialTheme.typography.labelSmall)
        Text("Source: ${r.tariff.source}", style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun LogCard(log: List<String>) {
    Expandable(header = { Text("Fetch log (${log.size})", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall) }) {
        log.forEach { Text(it, style = MaterialTheme.typography.labelSmall) }
    }
}
