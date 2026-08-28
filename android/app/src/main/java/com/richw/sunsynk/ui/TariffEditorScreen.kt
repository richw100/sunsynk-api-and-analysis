package com.richw.sunsynk.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.richw.sunsynk.PricePeriod

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TariffEditorScreen(
    filename: String,
    initialPeriods: List<PricePeriod>,
    onSave: (List<PricePeriod>) -> Unit,
    onBack: () -> Unit,
) {
    var periods by remember { mutableStateOf(initialPeriods) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        filename.removePrefix("_EnergyPrices-").removeSuffix(".json")
                            .ifBlank { "Default tariff" }
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                },
                actions = {
                    TextButton(onClick = { onSave(periods); onBack() }) {
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
            verticalArrangement = Arrangement.spacedBy(10.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
        ) {
            itemsIndexed(periods) { idx, period ->
                PeriodCard(
                    period = period,
                    index = idx,
                    showDelete = periods.size > 1,
                    onDelete = {
                        periods = periods.toMutableList().also { it.removeAt(idx) }
                    },
                    onChange = { updated ->
                        periods = periods.toMutableList().also { it[idx] = updated }
                    },
                )
            }

            item {
                OutlinedButton(
                    onClick = {
                        val last = periods.lastOrNull()
                        val newPeriod = PricePeriod(
                            dateFrom = last?.dateTo ?: "",
                            dateTo = "2040-10-10",
                            offpeakRate = last?.offpeakRate ?: "0.075",
                            offpeakStart = last?.offpeakStart ?: "00:00",
                            offpeakStop = last?.offpeakStop ?: "07:00",
                            peakRate = last?.peakRate ?: "0.30",
                            exportRate = last?.exportRate ?: "0.165",
                            standingCharge = last?.standingCharge ?: "0.60",
                            interestRate = last?.interestRate ?: "3.7",
                        )
                        periods = periods + newPeriod
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(Icons.Default.Add, null)
                    Spacer(Modifier.width(8.dp))
                    Text("Add period")
                }
            }

            item { Spacer(Modifier.height(8.dp)) }
        }
    }
}

@Composable
private fun PeriodCard(
    period: PricePeriod,
    index: Int,
    showDelete: Boolean,
    onDelete: () -> Unit,
    onChange: (PricePeriod) -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Period ${index + 1}",
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.weight(1f),
                )
                if (showDelete) {
                    IconButton(onClick = onDelete, modifier = Modifier.size(32.dp)) {
                        Icon(Icons.Default.Delete, "Remove", tint = MaterialTheme.colorScheme.error)
                    }
                }
            }

            // Date range
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                DateField("From", period.dateFrom, Modifier.weight(1f)) {
                    onChange(period.copy(dateFrom = it))
                }
                DateField("To", period.dateTo, Modifier.weight(1f)) {
                    onChange(period.copy(dateTo = it))
                }
            }

            // Off-peak window
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TariffField("Off-peak start", period.offpeakStart, Modifier.weight(1f), placeholder = "HH:MM") {
                    onChange(period.copy(offpeakStart = it))
                }
                TariffField("Off-peak stop", period.offpeakStop, Modifier.weight(1f), placeholder = "HH:MM") {
                    onChange(period.copy(offpeakStop = it))
                }
            }

            // Rates
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TariffField("Off-peak rate £/kWh", period.offpeakRate, Modifier.weight(1f), KeyboardType.Decimal) {
                    onChange(period.copy(offpeakRate = it))
                }
                TariffField("Peak rate £/kWh", period.peakRate, Modifier.weight(1f), KeyboardType.Decimal) {
                    onChange(period.copy(peakRate = it))
                }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TariffField("Export rate £/kWh", period.exportRate, Modifier.weight(1f), KeyboardType.Decimal) {
                    onChange(period.copy(exportRate = it))
                }
                TariffField("Standing charge £/day", period.standingCharge, Modifier.weight(1f), KeyboardType.Decimal) {
                    onChange(period.copy(standingCharge = it))
                }
            }

            TariffField("Interest rate %/year", period.interestRate, Modifier.fillMaxWidth(), KeyboardType.Decimal) {
                onChange(period.copy(interestRate = it))
            }
        }
    }
}

@Composable
private fun TariffField(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    keyboardType: KeyboardType = KeyboardType.Text,
    placeholder: String? = null,
    onChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        placeholder = placeholder?.let { { Text(it) } },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
        modifier = modifier,
    )
}
