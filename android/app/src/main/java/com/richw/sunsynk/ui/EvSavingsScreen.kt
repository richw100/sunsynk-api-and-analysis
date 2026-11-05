package com.richw.sunsynk.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.richw.sunsynk.EvSavingsUiState
import com.richw.sunsynk.EvSavingsViewModel
import com.richw.sunsynk.analysis.EvMonth
import com.richw.sunsynk.analysis.EvSavingsConfig
import com.richw.sunsynk.analysis.CustomCharge
import com.richw.sunsynk.analysis.EvCredit
import com.richw.sunsynk.analysis.Reimbursement
import com.richw.sunsynk.analysis.ExtraCharge
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val saving = Color(0xFF2E7D32)
private fun money(v: Double) = "£%,.0f".format(v)
private fun pence(v: Double) = "%.1fp".format(v)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EvSavingsScreen(vm: EvSavingsViewModel) {
    val state by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.onShown() }
    Scaffold(topBar = {
        TopAppBar(title = { Text("EV vs petrol") }, actions = {
            TextButton(onClick = { vm.refresh(force = true) }, enabled = state.busy == null) { Text("Refresh") }
        })
    }) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
        ) {
            state.busy?.let { b -> item { Column { LinearProgressIndicator(Modifier.fillMaxWidth()); Text(b, style = MaterialTheme.typography.bodySmall) } } }
            state.error?.let { e -> item { Text(e, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium) } }
            val r = state.result
            if (r != null && r.months.isNotEmpty()) {
                item { SummaryCard(state) }
                item { MonthHeader() }
                items(r.months.reversed()) { MonthRow(it) }
            }
            state.notice?.let { n -> item { Text(n, style = MaterialTheme.typography.bodyMedium) } }
            item { PublicCard(state, vm) }
            item { SettingsCard(state, vm) }
        }
    }
}

@Composable
private fun SummaryCard(state: EvSavingsUiState) {
    val r = state.result ?: return
    val t = r.total
    val y = r.last12()
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Saved vs petrol since ${r.months.first().month}", style = MaterialTheme.typography.titleSmall)
            Text(money(t.saved), style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.Bold, color = saving)
            Text("%,.0f miles: %s of electricity%s instead of %s of petrol".format(t.miles, money(t.netCost),
                if (t.credit + t.reimbursed > 0) " (after ${money(t.credit + t.reimbursed)} of credits and repayments)" else "",
                money(t.petrolCost)),
                style = MaterialTheme.typography.bodyMedium)
            if (t.publicMiles > 0) {
                val homeMiles = t.miles - t.publicMiles
                Text("Home: %,.0f miles for %s vs %s petrol".format(homeMiles, money(t.evCost - t.publicCost - t.credit),
                    money(t.petrolCost - t.publicPetrolCost)), style = MaterialTheme.typography.bodySmall)
                Text("Public: %,.0f miles for %s vs %s petrol — %s/kWh".format(t.publicMiles,
                    money(t.publicCost), money(t.publicPetrolCost), pence(t.publicCost * 100 / t.publicKwh)),
                    style = MaterialTheme.typography.bodySmall)
            }
            if (t.reimbursed > 0) {
                Text("Mileage repaid %s: %s in the total (paid − the petrol those miles would have needed)".format(
                    money(r.repayments.sumOf { it.repayment.amount }), money(t.reimbursed)), style = MaterialTheme.typography.bodySmall)
            }
            if (y.days > 0) Text("Last 12 full months: ${money(y.saved)} saved over %,.0f miles".format(y.miles),
                style = MaterialTheme.typography.bodyMedium)
            HorizontalDivider(Modifier.padding(vertical = 6.dp))
            val last = r.months.last()
            if (last.miles > 0) Text("${last.month}: ${pence(last.evCost * 100 / last.miles)}/mile electric vs " +
                "${pence(last.petrolCost * 100 / last.miles)}/mile petrol — ${money(last.saved)} saved so far",
                style = MaterialTheme.typography.bodyMedium)
            r.latest?.let { p ->
                val src = when (p.source) {
                    "local" -> "${state.stations.size} local supermarkets"
                    "national-adjusted" -> "UK average %+.1fp local adjustment".format(r.offset ?: 0.0)
                    else -> "UK average"
                }
                Text("Petrol now: ${pence(p.pence)}/litre ($src)", style = MaterialTheme.typography.bodySmall)
            }
            val updated = if (state.petrolUpdated > 0) DateTimeFormatter.ofPattern("d MMM HH:mm")
                .format(Instant.ofEpochMilli(state.petrolUpdated).atZone(ZoneId.systemDefault())) else "never"
            Text("Charging from the analysis run ${state.daysSavedAt ?: "—"}; petrol prices updated $updated" +
                (r.offset?.let { "; local supermarkets average %+.1fp/litre vs UK over %d weeks".format(it, r.offsetWeeks) } ?: ""),
                style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun MonthHeader() {
    Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp)) {
        listOf("Month" to 1.2f, "Miles" to 1f, "EV" to 1f, "Petrol" to 1f, "p/L" to 0.9f, "Saved" to 1f).forEach { (h, w) ->
            Text(h, Modifier.weight(w), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun MonthRow(m: EvMonth) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp)) {
        val s = MaterialTheme.typography.bodySmall
        Text(m.month, Modifier.weight(1.2f), style = s)
        Text("%,.0f".format(m.miles) + if (m.publicMiles > 0) "‡" else "", Modifier.weight(1f), style = s)
        Text(money(m.netCost) + if (m.credit > 0) "†" else "", Modifier.weight(1f), style = s)
        Text(money(m.petrolCost), Modifier.weight(1f), style = s)
        Text("%.1f%s".format(m.pencePerLitre, if (m.localDays == m.days) "" else "*"), Modifier.weight(0.9f), style = s)
        Text(money(m.saved), Modifier.weight(1f), style = s, color = saving)
    }
}

@Composable
private fun SettingsCard(state: EvSavingsUiState, vm: EvSavingsViewModel) {
    var open by rememberSaveable { mutableStateOf(state.stations.isEmpty()) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth().clickable { open = !open }, verticalAlignment = Alignment.CenterVertically) {
                Text("Settings", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                Icon(if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore, contentDescription = null)
            }
            Text("* = some days priced from the UK weekly average adjusted to your local supermarkets " +
                "(local prices start Jan 2026). † = after one-off credits. ‡ = includes public charging.", style = MaterialTheme.typography.bodySmall)
            if (!open) return@Column
            CarFields(state.config, vm::saveConfig)
            HorizontalDivider()
            CreditFields(state.config, vm::saveConfig)
            HorizontalDivider()
            StationFields(state, vm)
        }
    }
}

@Composable
private fun CarFields(cfg: EvSavingsConfig, onSave: (EvSavingsConfig) -> Unit) {
    var mpg by remember(cfg) { mutableStateOf(cfg.mpg.toString()) }
    var mpk by remember(cfg) { mutableStateOf(cfg.milesPerKwh.toString()) }
    var eff by remember(cfg) { mutableStateOf((cfg.chargeEfficiency * 100).toString()) }
    var extra by remember(cfg) { mutableStateOf(cfg.beforeCharger.joinToString("\n") { "${it.month} ${it.kwh} ${it.rate}" }) }
    val num = KeyboardOptions(keyboardType = KeyboardType.Decimal)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(mpg, { mpg = it }, Modifier.weight(1f), label = { Text("Petrol mpg") }, singleLine = true, keyboardOptions = num)
        OutlinedTextField(mpk, { mpk = it }, Modifier.weight(1f), label = { Text("EV mi/kWh") }, singleLine = true, keyboardOptions = num)
        OutlinedTextField(eff, { eff = it }, Modifier.weight(1f), label = { Text("Charging %") }, singleLine = true, keyboardOptions = num)
    }
    OutlinedTextField(extra, { extra = it }, Modifier.fillMaxWidth(), label = { Text("Charging before the charger: month kWh p/kWh") },
        minLines = 2)
    val parsed = runCatching {
        cfg.copy(mpg = mpg.toDouble(), milesPerKwh = mpk.toDouble(), chargeEfficiency = eff.toDouble() / 100,
            beforeCharger = extra.lines().filter { it.isNotBlank() }.map { l ->
                val (m, k, p) = l.trim().split(Regex("\\s+"))
                require(Regex("\\d{4}-\\d{2}").matches(m))
                ExtraCharge(m, k.toDouble(), p.toDouble())
            })
    }.getOrNull()
    Button(onClick = { parsed?.let(onSave) }, enabled = parsed != null && parsed != cfg) { Text("Save") }
}

/** One-off credits (e.g. a supplier's free-miles bonus): a row per credit with a delete button,
 *  and month / £ / note boxes to add another. Adding or deleting saves straight away. */
@Composable
private fun CreditFields(cfg: EvSavingsConfig, onSave: (EvSavingsConfig) -> Unit) {
    Text("One-off credits", style = MaterialTheme.typography.titleSmall)
    Text("Taken off that month's charging cost, e.g. a supplier's free-miles bonus (£ including VAT).",
        style = MaterialTheme.typography.bodySmall)
    cfg.credits.forEachIndexed { i, c ->
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(c.month, Modifier.weight(0.9f), style = MaterialTheme.typography.bodyMedium)
            Text("£%,.2f".format(c.amount), Modifier.weight(0.8f), style = MaterialTheme.typography.bodyMedium)
            Text(c.note, Modifier.weight(2f), style = MaterialTheme.typography.bodySmall)
            IconButton(onClick = { onSave(cfg.copy(credits = cfg.credits.filterIndexed { j, _ -> j != i })) }) {
                Icon(Icons.Default.Delete, contentDescription = "Delete credit")
            }
        }
    }
    var month by rememberSaveable { mutableStateOf(java.time.YearMonth.now().toString()) }
    var amount by rememberSaveable { mutableStateOf("") }
    var note by rememberSaveable { mutableStateOf("") }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(month, { month = it }, Modifier.weight(1.1f), label = { Text("Month") }, singleLine = true,
            placeholder = { Text("YYYY-MM") }, isError = !Regex("\\d{4}-(0[1-9]|1[0-2])").matches(month),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
        OutlinedTextField(amount, { amount = it }, Modifier.weight(1f), label = { Text("£") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
    }
    OutlinedTextField(note, { note = it }, Modifier.fillMaxWidth(), label = { Text("Note (optional)") }, singleLine = true)
    val value = amount.removePrefix("£").toDoubleOrNull()
    Button(onClick = {
        onSave(cfg.copy(credits = (cfg.credits + EvCredit(month, value!!, note.trim())).sortedBy { it.month }))
        amount = ""; note = ""
    }, enabled = Regex("\\d{4}-(0[1-9]|1[0-2])").matches(month) && value != null && value > 0) { Text("Add credit") }
}

@Composable
private fun StationFields(state: EvSavingsUiState, vm: EvSavingsViewModel) {
    var postcode by remember { mutableStateOf("") }
    var radius by rememberSaveable { mutableStateOf("20") }
    Text(if (state.stations.isEmpty()) "No petrol stations chosen: the UK weekly average is used."
         else "${state.stations.size} supermarket forecourts: " + state.stations.joinToString { it.name.lowercase()
            .split(" ").joinToString(" ") { w -> w.replaceFirstChar(Char::uppercase) } }.take(300),
        style = MaterialTheme.typography.bodySmall)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(postcode, { postcode = it }, Modifier.weight(1.4f), label = { Text("Postcode (not saved)") }, singleLine = true,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters))
        OutlinedTextField(radius, { radius = it }, Modifier.weight(1f), label = { Text("Miles") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
    }
    Button(onClick = { vm.findStations(postcode, radius.toDoubleOrNull() ?: 20.0) },
        enabled = state.busy == null && postcode.isNotBlank() && radius.toDoubleOrNull() != null) {
        Text("Find Tesco / Asda / Sainsbury's / Morrisons forecourts")
    }
}

/** Public charging, as ElectroFind's Trip tab: its own € rate, mpg and fuel price; charges with no
 *  Electroverse receipt (typed in, or imported from ElectroFind); and money repaid for EV miles. */
@Composable
private fun PublicCard(state: EvSavingsUiState, vm: EvSavingsViewModel) {
    val cfg = state.config
    var open by rememberSaveable { mutableStateOf(false) }
    val num = KeyboardOptions(keyboardType = KeyboardType.Decimal)
    val picker = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.OpenDocument()) { uri -> uri?.let(vm::importElectroFind) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth().clickable { open = !open }, verticalAlignment = Alignment.CenterVertically) {
                Text("Public charging", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                Icon(if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore, contentDescription = null)
            }
            val t = state.result?.total
            if (t != null && t.publicKwh > 0) Text("%,.0f kWh, %,.0f miles: %s vs %s petrol".format(t.publicKwh, t.publicMiles,
                money(t.publicCost), money(t.publicPetrolCost)), style = MaterialTheme.typography.bodySmall)
            Text("Electroverse receipts (Data sources) + ${cfg.customCharges.count { !it.excluded }} other charges; " +
                "${cfg.reimbursements.size} repayments", style = MaterialTheme.typography.bodySmall)
            if (!open) return@Column

            // ── comparison settings
            var eur by remember(cfg) { mutableStateOf(cfg.eurToGbp.toString()) }
            var mpg by remember(cfg) { mutableStateOf(cfg.publicMpg?.toString() ?: "") }
            var price by remember(cfg) { mutableStateOf(cfg.publicFuelPrice?.toString() ?: "") }
            var cur by remember(cfg) { mutableStateOf(cfg.publicFuelCurrency) }
            Text("Comparison", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(eur, { eur = it }, Modifier.weight(1f), label = { Text("£ per €") }, singleLine = true, keyboardOptions = num)
                OutlinedTextField(mpg, { mpg = it }, Modifier.weight(1f), label = { Text("mpg") }, placeholder = { Text("%g".format(cfg.mpg)) },
                    singleLine = true, keyboardOptions = num)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(price, { price = it }, Modifier.weight(1f), label = { Text("Fuel per litre") },
                    placeholder = { Text("blank = local prices") }, singleLine = true, keyboardOptions = num)
                FilterChip(selected = cur == "GBP", onClick = { cur = "GBP" }, label = { Text("£") })
                FilterChip(selected = cur == "EUR", onClick = { cur = "EUR" }, label = { Text("€") })
            }
            val parsed = runCatching {
                cfg.copy(eurToGbp = eur.toDouble(), publicMpg = mpg.takeIf { it.isNotBlank() }?.toDouble(),
                    publicFuelPrice = price.takeIf { it.isNotBlank() }?.toDouble(), publicFuelCurrency = cur)
            }.getOrNull()
            Button(onClick = { parsed?.let(vm::saveConfig) }, enabled = parsed != null && parsed != cfg) { Text("Save") }

            // ── every public charge: Electroverse receipts and other charges, tap to edit
            HorizontalDivider()
            val allRows = com.richw.sunsynk.analysis.EvSavings.publicRows(state.receipts, cfg.customCharges, cfg.receiptEdits)
            var listOpen by rememberSaveable { mutableStateOf(true) }
            var monthsBack by rememberSaveable { mutableIntStateOf(0) }          // 0 = all
            val since = if (monthsBack == 0) "" else java.time.LocalDate.now().minusMonths(monthsBack.toLong()).toString()
            val rows = allRows.filter { it.date >= since }
            Row(Modifier.fillMaxWidth().clickable { listOpen = !listOpen }, verticalAlignment = Alignment.CenterVertically) {
                Text("All public charges (${rows.size}${if (rows.size != allRows.size) " of ${allRows.size}" else ""})",
                    Modifier.weight(1f), style = MaterialTheme.typography.titleSmall)
                Icon(if (listOpen) Icons.Default.ExpandLess else Icons.Default.ExpandMore, contentDescription = null)
            }
            var editing by remember { mutableStateOf<com.richw.sunsynk.analysis.PublicRow?>(null) }
            val defMpg = cfg.publicMpg ?: cfg.mpg
            val defMpk = cfg.publicMilesPerKwh ?: (cfg.chargeEfficiency * cfg.milesPerKwh)
            fun fuelText(price: Double?, cur: String?) = price?.let { "%s%.3f/L".format(if (cur == "EUR") "€" else "£", it) }
                ?: cfg.publicFuelPrice?.let { "%s%.3f/L".format(if (cfg.publicFuelCurrency == "EUR") "€" else "£", it) }
                ?: "local £/L"
            if (listOpen) {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                listOf(1 to "1 m", 3 to "3 m", 6 to "6 m", 12 to "12 m", 0 to "All").forEach { (m, label) ->
                    FilterChip(selected = monthsBack == m, onClick = { monthsBack = m }, label = { Text(label) })
                }
            }
            Text("Untick to leave one out; tap to edit. Receipt edits are kept apart from the download, so they " +
                "survive a re-download.", style = MaterialTheme.typography.bodySmall)
            val shownEv = rows.filter { !it.excluded }.sumOf { it.cost * (if (it.currency == "EUR") cfg.eurToGbp else 1.0) }
            val shownPetrol = rows.filter { !it.excluded }.sumOf {
                com.richw.sunsynk.analysis.EvSavings.publicRowPetrol(it, cfg, state.localPence[it.date]) ?: 0.0 }
            Text("These: %s charging vs ≈ %s petrol — %s saved".format(money(shownEv), money(shownPetrol), money(shownPetrol - shownEv)),
                style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold)
            rows.forEach { r ->
                Row(Modifier.fillMaxWidth().clickable { editing = r }, verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = !r.excluded, onCheckedChange = { on -> vm.savePublicRow(r.copy(excluded = !on)) })
                    Column(Modifier.weight(1f)) {
                        Text("${r.date}  ${r.name.ifBlank { r.source }}", style = MaterialTheme.typography.bodyMedium, maxLines = 1,
                            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                        val sym = if (r.currency == "EUR") "€" else "£"
                        Text("%.1f kWh, %s%.2f (%s) — %s%s".format(r.kwh, sym, r.cost,
                            if (r.kwh > 0) (if (r.currency == "EUR") "%.1fc/kWh" else "%.1fp/kWh").format(r.cost * 100 / r.kwh) else "—",
                            r.source, if (r.edited) " (edited)" else ""), style = MaterialTheme.typography.bodySmall)
                        val petrol = com.richw.sunsynk.analysis.EvSavings.publicRowPetrol(r, cfg, state.localPence[r.date])
                        val evGbp = r.cost * (if (r.currency == "EUR") cfg.eurToGbp else 1.0)
                        Text(petrol?.let { "Petrol ≈ %s vs %s charging — %s %s".format(money(it), money(evGbp),
                            money(kotlin.math.abs(it - evGbp)), if (it >= evGbp) "saved" else "dearer") } ?: "Petrol: no price for this date",
                            style = MaterialTheme.typography.bodySmall, color = if ((petrol ?: 0.0) >= evGbp) saving else MaterialTheme.colorScheme.error)
                        Text("%.1f mpg, %.1f mi/kWh, %s%s".format(r.mpg ?: defMpg, r.milesPerKwh ?: defMpk, fuelText(r.fuelPrice, r.fuelCurrency),
                            r.fuelNote?.let { " — $it" } ?: ""), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            }
            editing?.let { r -> PublicChargeEditor(r, defMpg, defMpk, onDismiss = { editing = null },
                onSave = { vm.savePublicRow(it); editing = null },
                onReset = r.pk?.takeIf { r.edited }?.let { pk -> { vm.resetReceipt(pk); editing = null } },
                onDelete = r.customId?.let { id -> { vm.deleteCustom(id); editing = null } }) }
            Text("Add another charge", style = MaterialTheme.typography.titleSmall)
            var date by rememberSaveable { mutableStateOf(java.time.LocalDate.now().toString()) }
            var name by rememberSaveable { mutableStateOf("") }
            var kwh by rememberSaveable { mutableStateOf("") }
            var cost by rememberSaveable { mutableStateOf("") }
            var ccur by rememberSaveable { mutableStateOf("GBP") }
            val dateOk = runCatching { java.time.LocalDate.parse(date) }.isSuccess
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(date, { date = it }, Modifier.weight(1.2f), label = { Text("Date") }, placeholder = { Text("YYYY-MM-DD") },
                    singleLine = true, isError = !dateOk)
                OutlinedTextField(name, { name = it }, Modifier.weight(1f), label = { Text("Where") }, singleLine = true)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(kwh, { kwh = it }, Modifier.weight(1f), label = { Text("kWh") }, singleLine = true, keyboardOptions = num)
                OutlinedTextField(cost, { cost = it }, Modifier.weight(1f), label = { Text("Cost") }, singleLine = true, keyboardOptions = num)
                FilterChip(selected = ccur == "GBP", onClick = { ccur = "GBP" }, label = { Text("£") })
                FilterChip(selected = ccur == "EUR", onClick = { ccur = "EUR" }, label = { Text("€") })
            }
            val k = kwh.toDoubleOrNull(); val c = cost.removePrefix("£").removePrefix("€").toDoubleOrNull()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    vm.saveConfig(cfg.copy(customCharges = (cfg.customCharges + CustomCharge(java.util.UUID.randomUUID().toString(),
                        date, name.trim(), k!!, c!!, ccur)).sortedBy { it.date }))
                    name = ""; kwh = ""; cost = ""
                }, enabled = dateOk && k != null && k > 0 && c != null && c >= 0) { Text("Add charge") }
                OutlinedButton(onClick = { picker.launch(arrayOf("application/json", "*/*")) }, enabled = state.busy == null) {
                    Text("Import from ElectroFind")
                }
            }
            Text("Import reads ElectroFind's auto-backup (Download/ElectroFind/electrofind-auto-backup.json): its custom " +
                "charges and Trip tab € rate, mpg and fuel price.", style = MaterialTheme.typography.bodySmall)

            // ── repayments
            HorizontalDivider()
            Text("Repaid for EV miles", style = MaterialTheme.typography.titleSmall)
            Text("Mileage you've been paid for EV miles. A petrol car would have been paid too, so each claim adds " +
                "(paid − the petrol those miles would have needed) to the total; the charging is already counted in your " +
                "charges. Petrol price from a public charge that day, else home.", style = MaterialTheme.typography.bodySmall)
            val calcs = state.result?.repayments?.associateBy { it.repayment.id } ?: emptyMap()
            // EV £/mile per month (home + public), to estimate what a claim's miles cost to charge
            val perMile = state.result?.months?.associate { it.month to (if (it.miles > 0) it.evCost / it.miles else null) } ?: emptyMap()
            val overall = state.result?.total?.let { if (it.miles > 0) it.evCost / it.miles else null }
            cfg.reimbursements.forEach { r ->
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        val claim = if (r.miles != null && r.ratePence != null) "%,.0f mi × %gp = %s".format(r.miles, r.ratePence, money(r.amount))
                                    else money(r.amount)
                        Text("${r.date}  $claim", style = MaterialTheme.typography.bodyMedium)
                        calcs[r.id]?.let { c ->
                            Text(if (c.petrolCost != null) "Petrol for those miles ≈ %s (%.1fp/L, %.1f mpg, %s) → adds %s to the total".format(
                                money(c.petrolCost), c.pence, c.mpg, c.priceFrom, money(c.adjustment))
                                else "Adds %s to the total (%s)".format(money(c.adjustment), c.priceFrom),
                                style = MaterialTheme.typography.bodySmall)
                        }
                        val pm = perMile[r.date.take(7)] ?: overall
                        if (r.miles != null && pm != null) {
                            val charge = r.miles * pm
                            Text("Approx. profit: %s − charging ≈ %s (%.1fp/mi) = %s".format(money(r.amount), money(charge), pm * 100,
                                money(r.amount - charge)), style = MaterialTheme.typography.bodySmall, color = saving)
                        }
                        if (r.note.isNotBlank()) Text(r.note, style = MaterialTheme.typography.bodySmall)
                    }
                    IconButton(onClick = { vm.saveConfig(cfg.copy(reimbursements = cfg.reimbursements.filter { it.id != r.id })) }) {
                        Icon(Icons.Default.Delete, contentDescription = "Delete repayment")
                    }
                }
            }
            var rdate by rememberSaveable { mutableStateOf(java.time.LocalDate.now().toString()) }
            var ramount by rememberSaveable { mutableStateOf("") }
            var rmiles by rememberSaveable { mutableStateOf("") }
            var rrate by rememberSaveable { mutableStateOf("45") }
            var rnote by rememberSaveable { mutableStateOf("") }
            val rdateOk = runCatching { java.time.LocalDate.parse(rdate) }.isSuccess
            val rm = rmiles.toDoubleOrNull(); val rr = rrate.removeSuffix("p").toDoubleOrNull()
            val ra = if (rm != null && rr != null) rm * rr / 100 else ramount.removePrefix("£").toDoubleOrNull()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(rdate, { rdate = it }, Modifier.weight(1.2f), label = { Text("Date") }, singleLine = true, isError = !rdateOk)
                OutlinedTextField(rmiles, { rmiles = it }, Modifier.weight(1f), label = { Text("Miles") }, singleLine = true, keyboardOptions = num)
                OutlinedTextField(rrate, { rrate = it }, Modifier.weight(1f), label = { Text("p/mile") }, singleLine = true, keyboardOptions = num)
            }
            if (rm == null) OutlinedTextField(ramount, { ramount = it }, Modifier.fillMaxWidth(),
                label = { Text("…or an amount paid (£)") }, singleLine = true, keyboardOptions = num)
            else Text("Paid: ${money(ra ?: 0.0)}", style = MaterialTheme.typography.bodySmall)
            OutlinedTextField(rnote, { rnote = it }, Modifier.fillMaxWidth(), label = { Text("Note (e.g. 600 miles at 45p)") }, singleLine = true)
            Button(onClick = {
                vm.saveConfig(cfg.copy(reimbursements = (cfg.reimbursements + Reimbursement(java.util.UUID.randomUUID().toString(),
                    rdate, ra!!, rnote.trim(), miles = rm, ratePence = if (rm != null) rr else null)).sortedBy { it.date }))
                ramount = ""; rnote = ""; rmiles = ""
            }, enabled = rdateOk && ra != null && ra > 0) { Text("Add repayment") }
        }
    }
}

@Composable
private fun PublicChargeEditor(row: com.richw.sunsynk.analysis.PublicRow, defMpg: Double, defMpk: Double, onDismiss: () -> Unit,
                               onSave: (com.richw.sunsynk.analysis.PublicRow) -> Unit,
                               onReset: (() -> Unit)?, onDelete: (() -> Unit)?) {
    var date by remember { mutableStateOf(row.date) }
    var name by remember { mutableStateOf(row.name) }
    var kwh by remember { mutableStateOf(row.kwh.toString()) }
    var cost by remember { mutableStateOf("%.2f".format(row.cost)) }
    var cur by remember { mutableStateOf(row.currency) }
    var included by remember { mutableStateOf(!row.excluded) }
    var mpg by remember { mutableStateOf(row.mpg?.toString() ?: "") }
    var mpk by remember { mutableStateOf(row.milesPerKwh?.toString() ?: "") }
    var fuel by remember { mutableStateOf(row.fuelPrice?.toString() ?: "") }
    var fcur by remember { mutableStateOf(row.fuelCurrency ?: "GBP") }
    val num = KeyboardOptions(keyboardType = KeyboardType.Decimal)
    val dateOk = runCatching { java.time.LocalDate.parse(date) }.isSuccess
    val k = kwh.toDoubleOrNull(); val c = cost.removePrefix("£").removePrefix("€").toDoubleOrNull()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("${row.source} charge") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(date, { date = it }, label = { Text("Date") }, singleLine = true, isError = !dateOk)
                OutlinedTextField(name, { name = it }, label = { Text("Where") }, singleLine = true)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(kwh, { kwh = it }, Modifier.weight(1f), label = { Text("kWh") }, singleLine = true, keyboardOptions = num)
                    OutlinedTextField(cost, { cost = it }, Modifier.weight(1f), label = { Text("Cost") }, singleLine = true, keyboardOptions = num)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    FilterChip(selected = cur == "GBP", onClick = { cur = "GBP" }, label = { Text("£") })
                    FilterChip(selected = cur == "EUR", onClick = { cur = "EUR" }, label = { Text("€") })
                    Spacer(Modifier.weight(1f))
                    Text("Include")
                    Switch(checked = included, onCheckedChange = { included = it })
                }
                Text("Comparison (blank = the public defaults)", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(mpg, { mpg = it }, Modifier.weight(1f), label = { Text("mpg") },
                        placeholder = { Text("%.1f".format(defMpg)) }, singleLine = true, keyboardOptions = num)
                    OutlinedTextField(mpk, { mpk = it }, Modifier.weight(1f), label = { Text("mi/kWh") },
                        placeholder = { Text("%.1f".format(defMpk)) }, singleLine = true, keyboardOptions = num)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(fuel, { fuel = it }, Modifier.weight(1f), label = { Text("Fuel per litre") }, singleLine = true,
                        keyboardOptions = num)
                    FilterChip(selected = fcur == "GBP", onClick = { fcur = "GBP" }, label = { Text("£") })
                    FilterChip(selected = fcur == "EUR", onClick = { fcur = "EUR" }, label = { Text("€") })
                }
                row.fuelNote?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    onReset?.let { TextButton(onClick = it) { Text("Reset to receipt") } }
                    onDelete?.let { TextButton(onClick = it) { Text("Delete", color = MaterialTheme.colorScheme.error) } }
                }
            }
        },
        confirmButton = {
            val fuelV = fuel.takeIf { it.isNotBlank() }?.removePrefix("£")?.removePrefix("€")?.toDoubleOrNull()
            val okOpt = (mpg.isBlank() || mpg.toDoubleOrNull() != null) && (mpk.isBlank() || mpk.toDoubleOrNull() != null) &&
                (fuel.isBlank() || fuelV != null)
            TextButton(onClick = { onSave(row.copy(date = date, name = name.trim(), kwh = k!!, cost = c!!, currency = cur,
                excluded = !included, mpg = mpg.toDoubleOrNull(), milesPerKwh = mpk.toDoubleOrNull(),
                fuelPrice = fuelV, fuelCurrency = if (fuelV != null) fcur else null,
                // a typed price replaces an automatic one's note
                fuelNote = if (fuelV != null && fuelV == row.fuelPrice && fcur == row.fuelCurrency) row.fuelNote else null)) },
                enabled = dateOk && k != null && k >= 0 && c != null && c >= 0 && okOpt) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
