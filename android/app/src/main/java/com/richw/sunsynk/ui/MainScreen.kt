package com.richw.sunsynk.ui

import androidx.compose.foundation.layout.*
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.richw.sunsynk.AppSettings
import com.richw.sunsynk.AnalysisState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    settings: AppSettings,
    username: String,
    state: AnalysisState,
    isExporting: Boolean = false,
    onRunAnalysis: () -> Unit,
    onCancel: () -> Unit,
    onEditCredentials: () -> Unit,
    onViewResult: () -> Unit,
    onEditConfig: () -> Unit,
    onExport: () -> Unit = {},
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Sunsynk Analysis") },
                actions = {
                    IconButton(onClick = onExport, enabled = !isExporting) {
                        if (isExporting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Icon(Icons.Default.Share, contentDescription = "Export data")
                        }
                    }
                    IconButton(onClick = onEditConfig) {
                        Icon(Icons.Default.Edit, contentDescription = "Edit config")
                    }
                    IconButton(onClick = onEditCredentials) {
                        Icon(Icons.Default.Settings, contentDescription = "Credentials")
                    }
                },
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
        ) {
            if (username.isNotBlank()) {
                Text("Account: $username", style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(8.dp))
            }

            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    Text("Configuration", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(4.dp))
                    Text("Scan from: ${settings.scanFromYear}", style = MaterialTheme.typography.bodySmall)
                    Text("Install cost: £${"%.2f".format(settings.originalPrice)}", style = MaterialTheme.typography.bodySmall)
                    Text("Off-peak shift: ${settings.offPeakShift}", style = MaterialTheme.typography.bodySmall)
                    Text("Tariff files: ${settings.energyPrices.joinToString()}", style = MaterialTheme.typography.bodySmall)
                    Spacer(Modifier.height(4.dp))
                    if (settings.batteryConfigs.size > 1) {
                        Text("Battery configs: ${settings.batteryConfigs.size}", style = MaterialTheme.typography.bodySmall)
                        settings.batteryConfigs.forEach { bc ->
                            val label = bc.label.ifBlank { "${bc.batterySize}Wh" }
                            Text("  • $label: ${bc.batterySize}Wh, enabled=${bc.enabled}", style = MaterialTheme.typography.bodySmall)
                        }
                    } else {
                        val bc = settings.batteryConfigs.first()
                        Text("Battery: ${bc.batterySize}Wh, enabled=${bc.enabled}, PV=${bc.usePV}", style = MaterialTheme.typography.bodySmall)
                        if (bc.enabled.equals("ON", ignoreCase = true)) {
                            Text("  Export window: ${bc.exportWindowStart}–${bc.exportWindowStop}, export=${bc.useExport}", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }

            Spacer(Modifier.height(16.dp))

            when (state) {
                is AnalysisState.Idle -> {
                    Button(
                        onClick = onRunAnalysis,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Run Analysis")
                    }
                }
                is AnalysisState.Running -> {
                    Text(state.progress, style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(8.dp))
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = onCancel, modifier = Modifier.fillMaxWidth()) {
                        Text("Cancel")
                    }
                }
                is AnalysisState.Done -> {
                    Text("Analysis complete.", style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(8.dp))
                    Button(onClick = onViewResult, modifier = Modifier.fillMaxWidth()) {
                        Text("View Results")
                    }
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = onRunAnalysis, modifier = Modifier.fillMaxWidth()) {
                        Text("Run Again")
                    }
                }
                is AnalysisState.Error -> {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            "Error: ${state.message}",
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            modifier = Modifier.padding(12.dp),
                        )
                    }
                    Spacer(Modifier.height(8.dp))
                    Button(onClick = onRunAnalysis, modifier = Modifier.fillMaxWidth()) {
                        Text("Retry")
                    }
                }
            }
        }
    }
}
