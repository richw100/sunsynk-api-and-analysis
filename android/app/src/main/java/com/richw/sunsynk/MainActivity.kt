package com.richw.sunsynk

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.view.WindowCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.richw.sunsynk.ui.ConfigScreen
import com.richw.sunsynk.ui.LoginScreen
import com.richw.sunsynk.ui.MainScreen
import com.richw.sunsynk.ui.ResultsScreen
import com.richw.sunsynk.ui.TariffEditorScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val credStore = CredentialStore(this)
        val configStore = ConfigStore(this)
        val tariffStore = TariffStore(this)
        val prefsStore = PrefsStore(this)
        val defaultSettings = configStore.load()

        setContent {
            var themeMode by remember { mutableStateOf(prefsStore.themeMode) }
            val darkTheme = when (themeMode) {
                ThemeMode.DARK -> true
                ThemeMode.LIGHT -> false
                ThemeMode.SYSTEM -> isSystemInDarkTheme()
            }

            SideEffect {
                WindowCompat.getInsetsController(window, window.decorView)
                    .isAppearanceLightStatusBars = !darkTheme
            }

            MaterialTheme(colorScheme = if (darkTheme) darkColorScheme() else lightColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    App(
                        credStore = credStore,
                        configStore = configStore,
                        tariffStore = tariffStore,
                        defaultSettings = defaultSettings,
                        themeMode = themeMode,
                        onThemeChange = { mode ->
                            prefsStore.themeMode = mode
                            themeMode = mode
                        },
                    )
                }
            }
        }
    }
}

@Composable
fun App(
    credStore: CredentialStore,
    configStore: ConfigStore,
    tariffStore: TariffStore,
    defaultSettings: AppSettings,
    themeMode: ThemeMode,
    onThemeChange: (ThemeMode) -> Unit,
) {
    val navController = rememberNavController()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val vm: AnalysisViewModel = viewModel(factory = AnalysisViewModel.Factory(context))
    val state by vm.state.collectAsStateWithLifecycle()

    var settings by remember { mutableStateOf(defaultSettings) }
    var username by remember { mutableStateOf(credStore.username) }
    var password by remember { mutableStateOf(credStore.password) }
    var isExporting by remember { mutableStateOf(false) }

    val startDest = if (credStore.hasCredentials()) "main" else "login"

    NavHost(navController, startDestination = startDest) {
        composable("login") {
            LoginScreen(
                initialUsername = credStore.username,
                initialPassword = credStore.password,
                onSave = { u, p ->
                    credStore.username = u
                    credStore.password = p
                    username = u
                    password = p
                    navController.navigate("main") {
                        popUpTo("login") { inclusive = true }
                    }
                }
            )
        }
        composable("main") {
            MainScreen(
                settings = settings,
                username = username,
                state = state,
                isExporting = isExporting,
                onRunAnalysis = { vm.runAnalysis(settings, username, password) },
                onCancel = { vm.cancel() },
                onEditCredentials = { navController.navigate("login") },
                onViewResult = { navController.navigate("results") },
                onEditConfig = { navController.navigate("config") },
                onExport = {
                    if (!isExporting) {
                        isExporting = true
                        scope.launch {
                            try {
                                val store = ExportStore(context)
                                val zip = withContext(Dispatchers.IO) { store.createZip() }
                                store.share(zip)
                            } finally {
                                isExporting = false
                            }
                        }
                    }
                },
            )
        }
        composable("results") {
            val result = (state as? AnalysisState.Done)?.result
            if (result != null) {
                ResultsScreen(result = result, onBack = { navController.popBackStack() })
            }
        }
        composable("config") {
            val availableFiles = configStore.availablePriceFiles()
            var editedFiles by remember { mutableStateOf(availableFiles.filter { tariffStore.isEdited(it) }.toSet()) }
            ConfigScreen(
                initial = settings,
                availablePriceFiles = availableFiles,
                editedTariffFiles = editedFiles,
                themeMode = themeMode,
                onThemeChange = onThemeChange,
                onSave = { updated ->
                    configStore.save(updated)
                    settings = updated
                },
                onBack = { navController.popBackStack() },
                onEditTariff = { filename ->
                    tariffStore.ensureExternal(filename)
                    editedFiles = editedFiles + filename
                    navController.navigate("tariff/${java.net.URLEncoder.encode(filename, "UTF-8")}")
                },
                onDeleteTariff = { filename ->
                    tariffStore.delete(filename)
                    editedFiles = editedFiles - filename
                },
            )
        }
        composable("tariff/{filename}") { backStack ->
            val filename = java.net.URLDecoder.decode(
                backStack.arguments?.getString("filename") ?: "", "UTF-8"
            )
            if (filename.isNotBlank()) {
                val periods = remember(filename) { tariffStore.load(filename) }
                TariffEditorScreen(
                    filename = filename,
                    initialPeriods = periods,
                    onSave = { updated -> tariffStore.save(filename, updated) },
                    onBack = { navController.popBackStack() },
                )
            }
        }
    }
}
