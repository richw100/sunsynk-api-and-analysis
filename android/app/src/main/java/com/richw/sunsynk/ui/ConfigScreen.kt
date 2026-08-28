package com.richw.sunsynk.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import androidx.compose.material3.AlertDialog
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.richw.sunsynk.AppSettings
import com.richw.sunsynk.BatteryConfig
import com.richw.sunsynk.ThemeMode

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConfigScreen(
    initial: AppSettings,
    availablePriceFiles: List<String>,
    editedTariffFiles: Set<String>,
    themeMode: ThemeMode,
    onThemeChange: (ThemeMode) -> Unit,
    onSave: (AppSettings) -> Unit,
    onBack: () -> Unit,
    onEditTariff: (String) -> Unit = {},
    onDeleteTariff: (String) -> Unit = {},
) {
    var settings by remember { mutableStateOf(initial) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Configuration") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                },
                actions = {
                    TextButton(onClick = { onSave(settings); onBack() }) {
                        Text("Save")
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
            verticalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
        ) {

            // ── GENERAL ──────────────────────────────────────────────
            item { SectionHeader("General") }

            item {
                Column {
                    Text("Theme", style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.height(6.dp))
                    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                        val options = listOf(ThemeMode.SYSTEM to "System", ThemeMode.LIGHT to "Light", ThemeMode.DARK to "Dark")
                        options.forEachIndexed { idx, (mode, label) ->
                            SegmentedButton(
                                selected = themeMode == mode,
                                onClick = { onThemeChange(mode) },
                                shape = SegmentedButtonDefaults.itemShape(idx, options.size),
                            ) { Text(label) }
                        }
                    }
                }
            }

            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    NumberField(
                        label = "Scan from year",
                        value = settings.scanFromYear.toString(),
                        modifier = Modifier.weight(1f),
                        onValueChange = { v -> v.toIntOrNull()?.let { settings = settings.copy(scanFromYear = it) } },
                    )
                    Spacer(Modifier.width(8.dp))
                    NumberField(
                        label = "Install cost £",
                        value = settings.originalPrice.toString(),
                        modifier = Modifier.weight(1f),
                        decimal = true,
                        onValueChange = { v -> v.toDoubleOrNull()?.let { settings = settings.copy(originalPrice = it) } },
                    )
                }
            }

            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    NumberField(
                        label = "Off-peak baseline (kWh/day)",
                        value = settings.offPeakBaseline.toString(),
                        modifier = Modifier.weight(1f),
                        decimal = true,
                        onValueChange = { v -> v.toDoubleOrNull()?.let { settings = settings.copy(offPeakBaseline = it) } },
                    )
                    Spacer(Modifier.width(16.dp))
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("Off-peak shift", style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Switch(
                            checked = settings.offPeakShift.equals("ON", ignoreCase = true),
                            onCheckedChange = { settings = settings.copy(offPeakShift = if (it) "ON" else "OFF") },
                        )
                    }
                }
            }

            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    DateField(
                        label = "Start date",
                        value = settings.startDate,
                        modifier = Modifier.weight(1f),
                        onChange = { settings = settings.copy(startDate = it) },
                    )
                    Spacer(Modifier.width(8.dp))
                    DateField(
                        label = "Stop date",
                        value = settings.stopDate,
                        modifier = Modifier.weight(1f),
                        onChange = { settings = settings.copy(stopDate = it) },
                    )
                }
            }

            // ── TARIFF FILES ──────────────────────────────────────────
            item { SectionHeader("Tariff Files") }

            item {
                var confirmDelete by rememberSaveable { mutableStateOf<String?>(null) }

                if (confirmDelete != null) {
                    AlertDialog(
                        onDismissRequest = { confirmDelete = null },
                        title = { Text("Revert tariff?") },
                        text = {
                            Text(
                                "Delete your edits to \"${confirmDelete!!.removePrefix("_EnergyPrices-").removeSuffix(".json").ifBlank { "Default" }}\" and revert to the bundled version?"
                            )
                        },
                        confirmButton = {
                            TextButton(onClick = {
                                onDeleteTariff(confirmDelete!!)
                                confirmDelete = null
                            }) { Text("Revert", color = MaterialTheme.colorScheme.error) }
                        },
                        dismissButton = {
                            TextButton(onClick = { confirmDelete = null }) { Text("Cancel") }
                        },
                    )
                }

                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        availablePriceFiles.forEach { filename ->
                            val selected = filename in settings.energyPrices
                            val isEdited = filename in editedTariffFiles
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Checkbox(
                                    checked = selected,
                                    onCheckedChange = { checked ->
                                        val updated = if (checked) {
                                            settings.energyPrices + filename
                                        } else {
                                            settings.energyPrices - filename
                                        }
                                        if (updated.isNotEmpty()) {
                                            settings = settings.copy(energyPrices = updated)
                                        }
                                    },
                                )
                                Text(
                                    filename.removePrefix("_EnergyPrices-").removeSuffix(".json")
                                        .ifBlank { "Default" },
                                    style = MaterialTheme.typography.bodyMedium,
                                    modifier = Modifier.weight(1f),
                                )
                                IconButton(onClick = { onEditTariff(filename) }) {
                                    Icon(Icons.Default.Edit, "Edit tariff",
                                        tint = MaterialTheme.colorScheme.primary,
                                        modifier = Modifier.size(20.dp))
                                }
                                if (isEdited) {
                                    IconButton(onClick = { confirmDelete = filename }) {
                                        Icon(Icons.Default.Delete, "Revert tariff",
                                            tint = MaterialTheme.colorScheme.error,
                                            modifier = Modifier.size(20.dp))
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ── BATTERY CONFIGS ───────────────────────────────────────
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SectionHeader("Virtual Battery", modifier = Modifier.weight(1f))
                    if (settings.batteryConfigs.size < 4) {
                        IconButton(onClick = {
                            settings = settings.copy(batteryConfigs = settings.batteryConfigs + BatteryConfig())
                        }) {
                            Icon(Icons.Default.Add, "Add battery config")
                        }
                    }
                }
            }

            itemsIndexed(settings.batteryConfigs) { idx, bc ->
                BatteryConfigCard(
                    config = bc,
                    showDelete = settings.batteryConfigs.size > 1,
                    onDelete = {
                        settings = settings.copy(batteryConfigs = settings.batteryConfigs.toMutableList().also { it.removeAt(idx) })
                    },
                    onChange = { updated ->
                        settings = settings.copy(batteryConfigs = settings.batteryConfigs.toMutableList().also { it[idx] = updated })
                    },
                )
            }

            item { Spacer(Modifier.height(8.dp)) }
        }
    }
}

@Composable
private fun BatteryConfigCard(
    config: BatteryConfig,
    showDelete: Boolean,
    onDelete: () -> Unit,
    onChange: (BatteryConfig) -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {

            Row(verticalAlignment = Alignment.CenterVertically) {
                TextField(
                    value = config.label,
                    onValueChange = { onChange(config.copy(label = it)) },
                    label = { Text("Label") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Enabled", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Switch(
                        checked = config.enabled.equals("ON", ignoreCase = true),
                        onCheckedChange = { onChange(config.copy(enabled = if (it) "ON" else "OFF")) },
                    )
                }
                if (showDelete) {
                    IconButton(onClick = onDelete) {
                        Icon(Icons.Default.Delete, "Remove", tint = MaterialTheme.colorScheme.error)
                    }
                }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumberField("Size (Wh)", config.batterySize.toString(), Modifier.weight(1f),
                    onValueChange = { v -> v.toIntOrNull()?.let { onChange(config.copy(batterySize = it)) } })
                NumberField("Price £", config.batteryPrice.toString(), Modifier.weight(1f), decimal = true,
                    onValueChange = { v -> v.toDoubleOrNull()?.let { onChange(config.copy(batteryPrice = it)) } })
                NumberField("Max W", config.maxOutputW.toInt().toString(), Modifier.weight(1f),
                    onValueChange = { v -> v.toIntOrNull()?.let { onChange(config.copy(maxOutputW = it.toDouble())) } })
            }

            // Charging
            Text("Charging", style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Grid charge", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Switch(
                        checked = config.gridCharge.equals("ON", ignoreCase = true),
                        onCheckedChange = { onChange(config.copy(gridCharge = if (it) "ON" else "OFF")) },
                    )
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("PV charge", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Switch(
                        checked = config.usePV.equals("ON", ignoreCase = true),
                        onCheckedChange = { onChange(config.copy(usePV = if (it) "ON" else "OFF")) },
                    )
                }
                if (config.usePV.equals("ON", ignoreCase = true)) {
                    NumberField("Start (Wh)", config.startCharge.toString(), Modifier.weight(1f),
                        onValueChange = { v -> v.toIntOrNull()?.let { onChange(config.copy(startCharge = it)) } })
                    NumberField("Stop (Wh)", config.stopCharge.toString(), Modifier.weight(1f),
                        onValueChange = { v -> v.toIntOrNull()?.let { onChange(config.copy(stopCharge = it)) } })
                }
            }

            // Export window
            Text("Export / Discharge", style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Export", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Switch(
                        checked = config.useExport.equals("ON", ignoreCase = true),
                        onCheckedChange = { onChange(config.copy(useExport = if (it) "ON" else "OFF")) },
                    )
                }
                TimeField("Window start", config.exportWindowStart, Modifier.weight(1f),
                    onChange = { onChange(config.copy(exportWindowStart = it)) })
                TimeField("Window stop", config.exportWindowStop, Modifier.weight(1f),
                    onChange = { onChange(config.copy(exportWindowStop = it)) })
            }

            // Reserve
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumberField("Reserve (Wh)", config.dischargeReserveWh.toString(), Modifier.weight(1f),
                    onValueChange = { v -> v.toIntOrNull()?.let { onChange(config.copy(dischargeReserveWh = it)) } })
                TimeField("Reserve until", config.dischargeReserveUntil, Modifier.weight(1f),
                    onChange = { onChange(config.copy(dischargeReserveUntil = it)) })
            }

            // Efficiencies
            Text("Efficiencies", style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumberField("Discharge", config.dischargeEfficiency.toString(), Modifier.weight(1f), decimal = true,
                    onValueChange = { v -> v.toDoubleOrNull()?.let { onChange(config.copy(dischargeEfficiency = it)) } })
                NumberField("Charge", config.chargeEfficiency.toString(), Modifier.weight(1f), decimal = true,
                    onValueChange = { v -> v.toDoubleOrNull()?.let { onChange(config.copy(chargeEfficiency = it)) } })
                NumberField("PV charge", config.pvChargeEfficiency.toString(), Modifier.weight(1f), decimal = true,
                    onValueChange = { v -> v.toDoubleOrNull()?.let { onChange(config.copy(pvChargeEfficiency = it)) } })
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String, modifier: Modifier = Modifier) {
    Text(title, style = MaterialTheme.typography.titleMedium,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier.padding(top = 8.dp, bottom = 2.dp))
}

@Composable
private fun NumberField(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    decimal: Boolean = false,
    onValueChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(
            keyboardType = if (decimal) KeyboardType.Decimal else KeyboardType.Number
        ),
        modifier = modifier,
    )
}

@Composable
private fun TimeField(label: String, value: String, modifier: Modifier = Modifier, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        singleLine = true,
        placeholder = { Text("HH:MM") },
        modifier = modifier,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DateField(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    onChange: (String) -> Unit,
) {
    var showPicker by remember { mutableStateOf(false) }
    val initialMillis = remember(value) {
        runCatching {
            LocalDate.parse(value).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
        }.getOrNull()
    }

    if (showPicker) {
        val state = rememberDatePickerState(initialSelectedDateMillis = initialMillis)
        DatePickerDialog(
            onDismissRequest = { showPicker = false },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let { millis ->
                        onChange(
                            Instant.ofEpochMilli(millis)
                                .atZone(ZoneOffset.UTC)
                                .toLocalDate()
                                .format(DateTimeFormatter.ISO_LOCAL_DATE)
                        )
                    }
                    showPicker = false
                }) { Text("OK") }
            },
            dismissButton = {
                TextButton(onClick = { showPicker = false }) { Text("Cancel") }
            },
        ) {
            DatePicker(state = state)
        }
    }

    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        trailingIcon = {
            IconButton(onClick = { showPicker = true }) {
                Icon(Icons.Default.DateRange, contentDescription = "Pick date")
            }
        },
        singleLine = true,
        modifier = modifier,
    )
}
