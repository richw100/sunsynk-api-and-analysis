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
import com.richw.sunsynk.analysis.HistPair
import com.richw.sunsynk.analysis.HistoryResult
import com.richw.sunsynk.analysis.TariffHistory
import com.richw.sunsynk.analysis.ExportRow
import com.richw.sunsynk.analysis.GasRow
import com.richw.sunsynk.analysis.GasYear
import com.richw.sunsynk.analysis.OverallRow
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
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = state.mode == "today", onClick = { vm.setMode("today") },
                        label = { Text("Today's rates") })
                    FilterChip(selected = state.mode == "history", onClick = { vm.setMode("history") },
                        label = { Text("History at actual rates") })
                }
            }
            if (state.mode == "history") {
                historyItems(state, vm)
                if (state.log.isNotEmpty()) item { LogCard(state.log) }
                return@LazyColumn
            }
            val profile = state.profile
            if (profile == null) {
                item {
                    Text("Run the analysis first: its projected year of half-hourly import and export " +
                        "is what the tariffs are priced on.", style = MaterialTheme.typography.bodyMedium)
                }
                return@LazyColumn
            }
            item { ProfileCard(profile, state.gasYear) }
            if (state.overall.isNotEmpty()) {
                val base = state.overall.firstOrNull { it.pair.isCurrent }?.total
                item { SectionTitle("Overall: electricity + gas − export",
                    "£/year; gas = the tariff's own (or supplier's) gas, or the cheapest gas anywhere if cheaper") }
                items(state.overall.take(10) + state.overall.drop(10).filter { it.pair.isCurrent }) { OverallCard(it, base, profile) }
            }
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
            state.gasYear?.let { g ->
                item { GasYearCard(g) }
                item { SectionTitle("Gas tariffs", "£/year = kWh × unit rate + 365 × standing charge") }
                items(state.gasRows.take(15) + state.gasRows.drop(15).filter { it.isCurrent }) { GasCard(it) }
            } ?: item {
                Text("Gas: sign in to E.ON Next under Data sources and download, to add gas to the comparison.",
                    style = MaterialTheme.typography.bodySmall)
            }
            if (state.log.isNotEmpty()) item { LogCard(state.log) }
        }
    }
}

@Composable
private fun OverallCard(r: OverallRow, base: Double?, profile: TariffProfile) {
    val p = r.pair
    Expandable(highlight = p.isCurrent, header = {
        Column(Modifier.weight(1f)) {
            Text("${p.imp.tariff.supplier} ${p.imp.tariff.name}", style = MaterialTheme.typography.bodyMedium,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("+ ${p.exp.tariff.supplier} ${p.exp.tariff.name}", style = MaterialTheme.typography.bodySmall,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(gbp(r.total), fontWeight = FontWeight.Bold)
            if (base != null) Vs(r.total - base, p.isCurrent) else Text("total/yr", style = MaterialTheme.typography.labelSmall)
        }
    }) {
        PairLines(p, profile)
        HorizontalDivider(Modifier.padding(vertical = 2.dp))
        // Each gas option with the total it gives; the card's total is the cheaper one.
        val options = listOfNotNull(r.own?.let { it to (if (p.isCurrent) "current gas" else "own gas") },
            r.cheapest?.takeIf { it.tariff.code != r.own?.tariff?.code }?.let { it to "cheapest gas" })
        val best = options.minByOrNull { it.first.cost }?.first
        options.forEach { (g, what) ->
            val used = g === best
            Text("${if (used) "✓" else "  "} $what: ${g.tariff.supplier} ${g.tariff.name}",
                style = MaterialTheme.typography.bodySmall, fontWeight = if (used) FontWeight.Bold else FontWeight.Normal,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Line("     %.2fp/kWh, %.2fp/day: gas %s".format(g.tariff.gasRate, g.tariff.gasStanding, gbp(g.cost)),
                "total ${gbp(p.net + g.cost)}")
        }
        if (options.size > 1) Text("✓ = the cheaper option, used for the total above. Gas can be with a different " +
            "supplier from your electricity.", style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun GasYearCard(g: GasYear) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Projected gas year", style = MaterialTheme.typography.titleSmall)
            Text("${"%,.0f".format(g.kwh)} kWh from ${g.days} days of E.ON meter data", style = MaterialTheme.typography.bodyMedium)
            if (!g.seasonal) Text("Provisional: not all 12 months seen yet", color = bad, style = MaterialTheme.typography.bodySmall)
            MonthRow("", MONTHS, bold = true)
            MonthRow("Gas", (0 until 12).map { m -> g.monthly[m]?.let { "%.0f".format(it) } ?: "-" })
        }
    }
}

@Composable
private fun GasCard(r: GasRow) {
    Card(Modifier.fillMaxWidth(), colors = if (r.isCurrent)
        CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer) else CardDefaults.cardColors()) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("${r.tariff.supplier} ${r.tariff.name}", style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("%.2fp/kWh · %.2fp/day · ${r.tariff.kind}".format(r.tariff.gasRate, r.tariff.gasStanding),
                    style = MaterialTheme.typography.bodySmall)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(gbp(r.cost), fontWeight = FontWeight.Bold)
                Vs(r.vsCurrent, r.isCurrent)
            }
        }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.historyItems(state: TariffsUiState, vm: TariffsViewModel) {
    item {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Every half-hour of your actual import and export from the last analysis run, priced " +
                    "at the rate each Octopus tariff charged at that moment, against your actual tariff.",
                    style = MaterialTheme.typography.bodySmall)
                Button(onClick = { vm.priceHistory() }, enabled = state.historyBusy == null) {
                    Text(if (state.history == null) "Price my history" else "Re-price")
                }
                state.historyBusy?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall)
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                state.historyError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                state.historyNote?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
            }
        }
    }
    val r = state.history ?: return
    val all = r.months
    val last12 = TariffHistory.last12Months(all)
    val span = if (state.historyAll || last12.isEmpty()) all else last12
    if (span.isEmpty()) return
    item {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            FilterChip(selected = span == last12, onClick = { vm.setHistoryAll(false) }, label = { Text("Last 12 full months") },
                enabled = last12.isNotEmpty())
            FilterChip(selected = span == all && span != last12, onClick = { vm.setHistoryAll(true) }, label = { Text("All data") })
        }
    }
    val pairs = TariffHistory.bestPairs(r, span)
    val actualNet = pairs.firstOrNull { it.isActual }?.net
    item { SectionTitle("Import + export pairs, ${span.first()} to ${span.last()}", "net £ = import − export; tap for detail") }
    items(pairs) { HistPairCard(it, r, span, actualNet) }
    item { SectionTitle("Import tariffs", "house + EV + standing; EV re-timed into the cheapest night half-hours") }
    items(r.imports.keys.sortedBy { TariffHistory.importTotals(r.imports.getValue(it), span).total }.toList()) { name ->
        val t = TariffHistory.importTotals(r.imports.getValue(name), span)
        val actual = TariffHistory.importTotals(r.imports.getValue(TariffHistory.ACTUAL), span).total
        Expandable(highlight = name == TariffHistory.ACTUAL, header = {
            Text(name, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
            Column(horizontalAlignment = Alignment.End) {
                Text(gbp(t.total), fontWeight = FontWeight.Bold)
                Vs(t.total - actual, name == TariffHistory.ACTUAL)
            }
        }) {
            Line("House", gbp(t.house))
            Line("EV (as charged ${gbp(t.evNow)})", gbp(t.ev))
            Line("Standing", gbp(t.standing))
            Line("Average", "%.2fp/kWh over %,.0f kWh".format(if (t.kwh > 0) 100 * (t.house + t.ev) / t.kwh else 0.0, t.kwh))
        }
    }
    item { SectionTitle("Export tariffs", "achieved p/kWh at the times you exported") }
    items(r.exports.keys.sortedByDescending { TariffHistory.exportTotals(r.exports.getValue(it), span).income }.toList()) { name ->
        val t = TariffHistory.exportTotals(r.exports.getValue(name), span)
        Card(Modifier.fillMaxWidth(), colors = if (name == TariffHistory.ACTUAL_EXPORT)
            CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer) else CardDefaults.cardColors()) {
            Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(name, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                Text("%,.0f kWh  %.2fp  ".format(t.kwh, t.rate), style = MaterialTheme.typography.bodySmall)
                Text(gbp(t.income), fontWeight = FontWeight.Bold)
            }
        }
    }
    item { HistMonthsCard(r, span) }
}

@Composable
private fun HistPairCard(p: HistPair, r: HistoryResult, span: List<String>, actualNet: Double?) {
    Expandable(highlight = p.isActual, header = {
        Column(Modifier.weight(1f)) {
            Text(p.imp, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("+ ${p.exp}", style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(gbp(p.net), fontWeight = FontWeight.Bold)
            if (actualNet != null) Vs(p.net - actualNet, p.isActual) else Text("net", style = MaterialTheme.typography.labelSmall)
        }
    }) {
        val i = TariffHistory.importTotals(r.imports.getValue(p.imp), span)
        val e = TariffHistory.exportTotals(r.exports.getValue(p.exp), span)
        Line("House", gbp(i.house))
        Line("EV", gbp(i.ev))
        Line("Standing", gbp(i.standing))
        Line("Import total", gbp(i.total))
        Line("Export", "%,.0f kWh  −%s  (%.2fp)".format(e.kwh, gbp(e.income), e.rate))
        Line("Net", gbp(p.net))
        r.requires[p.exp]?.let { Line("Export needs import from", it) }
    }
}

@Composable
private fun HistMonthsCard(r: HistoryResult, span: List<String>) {
    val imports = r.imports.keys.toList()
    val exports = r.exports.keys.toList()
    fun short(n: String) = when {
        n == TariffHistory.ACTUAL || n == TariffHistory.ACTUAL_EXPORT -> "Act"
        n.startsWith("Intelligent") -> "IOG"
        n.startsWith("Agile Outgoing") -> "AgOut"
        n.startsWith("Agile") -> "Agile"
        n.startsWith("Octopus Go") -> "Go"
        n.startsWith("Flexible") -> "Flex"
        n.startsWith("Outgoing") -> "Out"
        else -> n.take(5)
    }
    Expandable(header = { Text("By month", Modifier.weight(1f), style = MaterialTheme.typography.titleSmall) }) {
        Text("Import £ (house + EV + standing)", style = MaterialTheme.typography.labelMedium)
        MonthTableRow("Month", imports.map(::short), bold = true)
        span.forEach { m -> MonthTableRow(m.substring(2), imports.map { n -> r.imports.getValue(n)[m]?.let { "%.0f".format(it.total) } ?: "-" }) }
        Spacer(Modifier.height(6.dp))
        Text("Export £ (achieved p/kWh)", style = MaterialTheme.typography.labelMedium)
        MonthTableRow("Month", listOf("kWh") + exports.map(::short), bold = true)
        span.forEach { m ->
            MonthTableRow(m.substring(2), listOf("%.0f".format(r.kwh[m]?.export ?: 0.0)) +
                exports.map { n -> r.exports.getValue(n)[m]?.let { "%.0f %.1fp".format(it.income, it.rate) } ?: "-" })
        }
    }
}

@Composable
private fun MonthTableRow(label: String, cells: List<String>, bold: Boolean = false) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, Modifier.width(48.dp), style = MaterialTheme.typography.labelSmall,
            fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal)
        cells.forEach {
            Text(it, Modifier.weight(1f), style = MaterialTheme.typography.labelSmall, maxLines = 1,
                fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal)
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
                Text("Rates fetched $it (kept until you fetch again; today's downloads are cached)",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun ProfileCard(p: TariffProfile, gas: GasYear?) {
    val house = p.house.sum()
    val ev = p.ev.sum()
    val export = p.export.sum()
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Projected year (analysis run ${p.savedAt})", style = MaterialTheme.typography.titleSmall)
            Text("Import ${"%,.0f".format(house + ev)} kWh = house ${"%,.0f".format(house)} + EV ${"%,.0f".format(ev)}" +
                "   Export ${"%,.0f".format(export)} kWh" + (gas?.let { "   Gas ${"%,.0f".format(it.kwh)} kWh" } ?: ""),
                style = MaterialTheme.typography.bodyMedium)
            if (!p.seasonal) Text("Provisional: not all 12 months seen yet", color = bad,
                style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(4.dp))
            MonthRow("", MONTHS, bold = true)
            MonthRow("Exp", (0 until 12).map { m -> p.monthlyExport[m]?.let { "%.0f".format(it) } ?: "-" })
            MonthRow("House", (0 until 12).map { m -> p.monthlyHouse[m]?.let { "%.0f".format(it) } ?: "-" })
            p.monthlyEv?.let { ev -> MonthRow("EV", (0 until 12).map { m -> ev[m]?.let { "%.0f".format(it) } ?: "-" }) }
            gas?.let { g -> MonthRow("Gas", (0 until 12).map { m -> g.monthly[m]?.let { "%.0f".format(it) } ?: "-" }) }
            Text("kWh, average month (house import excludes the EV" +
                (if (p.monthlyEv == null) "; re-run the analysis for the EV row" else "") +
                (if (gas == null) "; download E.ON data for gas" else "") + ")", style = MaterialTheme.typography.bodySmall)
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
private fun PairLines(pair: Pair3, profile: TariffProfile) {
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
    Line("Net (electricity)", gbp(b.net))
    if (pair.exp.tariff.requires != null) Line("Export needs import from", pair.exp.tariff.requires!!)
    val notes = listOfNotNull(pair.imp.tariff.note.ifBlank { null }, pair.exp.tariff.note.ifBlank { null })
    notes.forEach { Text(it, style = MaterialTheme.typography.labelSmall) }
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
        PairLines(pair, profile)
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
