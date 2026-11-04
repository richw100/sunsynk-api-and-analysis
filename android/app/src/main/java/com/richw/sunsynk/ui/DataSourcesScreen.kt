package com.richw.sunsynk.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.richw.sunsynk.DataSourcesViewModel
import com.richw.sunsynk.SignInStep
import com.richw.sunsynk.SourceUi

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DataSourcesScreen(vm: DataSourcesViewModel, onBack: () -> Unit) {
    val fuse by vm.fuse.collectAsStateWithLifecycle()
    val indra by vm.indra.collectAsStateWithLifecycle()
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Data sources") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            FuseCard(vm, fuse)
            IndraCard(vm, indra)
        }
    }
}

@Composable
private fun SourceCard(title: String, subtitle: String, ui: SourceUi, content: @Composable ColumnScope.() -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
            Text(subtitle, style = MaterialTheme.typography.bodySmall)
            Text(ui.summary, style = MaterialTheme.typography.bodyMedium)
            content()
            if (ui.busy) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
                if (ui.progress.isNotBlank()) Text(ui.progress, style = MaterialTheme.typography.bodySmall)
            }
            if (ui.message.isNotBlank()) Text(ui.message, style = MaterialTheme.typography.bodySmall)
            ui.error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

@Composable
private fun FuseCard(vm: DataSourcesViewModel, ui: SourceUi) {
    var phone by remember { mutableStateOf(vm.fusePhone) }
    var code by remember { mutableStateOf("") }
    SourceCard(
        "Fuse Energy — export meter",
        "Your SEG supplier's smart-meter export, compared with the inverter's export estimate.",
        ui,
    ) {
        when (ui.step) {
            SignInStep.SIGNED_IN -> {
                Button(onClick = vm::fuseDownload, enabled = !ui.busy, modifier = Modifier.fillMaxWidth()) {
                    Text("Download meter data")
                }
                TextButton(onClick = { vm.fuseSendCode(phone) }, enabled = !ui.busy && phone.isNotBlank()) {
                    Text("Sign in again")
                }
            }
            else -> {
                OutlinedTextField(phone, { phone = it }, label = { Text("Phone (+447…)") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone), modifier = Modifier.fillMaxWidth())
                Button(onClick = { vm.fuseSendCode(phone) }, enabled = !ui.busy && phone.isNotBlank(),
                    modifier = Modifier.fillMaxWidth()) { Text("Text me a sign-in code") }
                if (ui.step == SignInStep.AWAITING_CONFIRMATION) {
                    OutlinedTextField(code, { code = it }, label = { Text("SMS code") }, singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                        modifier = Modifier.fillMaxWidth())
                    Button(onClick = { vm.fuseVerify(code) }, enabled = !ui.busy && code.isNotBlank(),
                        modifier = Modifier.fillMaxWidth()) { Text("Sign in") }
                }
            }
        }
    }
}

@Composable
private fun IndraCard(vm: DataSourcesViewModel, ui: SourceUi) {
    var email by remember { mutableStateOf(vm.indraEmail) }
    var link by remember { mutableStateOf("") }
    val clipboard = LocalClipboardManager.current
    SourceCard(
        "Indra — EV charger",
        "Car charging happens beyond the inverter's CT, so Sunsynk never sees it. Once downloaded, " +
            "it is added to load and import when EV charging is ON in Config.",
        ui,
    ) {
        when (ui.step) {
            SignInStep.SIGNED_IN -> {
                Button(onClick = vm::indraDownload, enabled = !ui.busy, modifier = Modifier.fillMaxWidth()) {
                    Text("Download charging history")
                }
                TextButton(onClick = { vm.indraSendLink(email) }, enabled = !ui.busy && email.isNotBlank()) {
                    Text("Sign in again")
                }
            }
            else -> {
                OutlinedTextField(email, { email = it }, label = { Text("Indra account email") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth())
                Button(onClick = { vm.indraSendLink(email) }, enabled = !ui.busy && email.isNotBlank(),
                    modifier = Modifier.fillMaxWidth()) { Text("Email me a sign-in link") }
                if (ui.step == SignInStep.AWAITING_CONFIRMATION) {
                    Text(
                        "In the email, long-press the link and copy it, then paste it here. " +
                            "(If you tapped it and the Indra app opened, just press Sign in.)",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    OutlinedTextField(link, { link = it }, label = { Text("Sign-in link") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        trailingIcon = {
                            TextButton(onClick = { link = clipboard.getText()?.text ?: link }) { Text("Paste") }
                        })
                    Button(onClick = { vm.indraFinish(link) }, enabled = !ui.busy,
                        modifier = Modifier.fillMaxWidth()) { Text("Sign in") }
                }
            }
        }
    }
}
