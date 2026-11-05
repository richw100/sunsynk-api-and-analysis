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
            Text("%,.0f miles: %s of electricity instead of %s of petrol".format(t.miles, money(t.evCost), money(t.petrolCost)),
                style = MaterialTheme.typography.bodyMedium)
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
        Text("%,.0f".format(m.miles), Modifier.weight(1f), style = s)
        Text(money(m.evCost), Modifier.weight(1f), style = s)
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
                "(local prices start Jan 2026).", style = MaterialTheme.typography.bodySmall)
            if (!open) return@Column
            CarFields(state.config, vm::saveConfig)
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
        EvSavingsConfig(mpg.toDouble(), mpk.toDouble(), eff.toDouble() / 100,
            extra.lines().filter { it.isNotBlank() }.map { l ->
                val (m, k, p) = l.trim().split(Regex("\\s+"))
                require(Regex("\\d{4}-\\d{2}").matches(m))
                ExtraCharge(m, k.toDouble(), p.toDouble())
            })
    }.getOrNull()
    Button(onClick = { parsed?.let(onSave) }, enabled = parsed != null && parsed != cfg) { Text("Save") }
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
