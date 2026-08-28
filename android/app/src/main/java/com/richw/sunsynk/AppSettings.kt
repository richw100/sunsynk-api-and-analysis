package com.richw.sunsynk

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import androidx.core.content.FileProvider
import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.richw.sunsynk.analysis.VirtualBattery
import com.richw.sunsynk.analysis.PriceData
import java.io.BufferedOutputStream
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

data class BatteryConfig(
    val label: String = "",
    val enabled: String = "ON",
    val batterySize: Int = 5000,
    val batteryPrice: Double = 0.0,
    val usePV: String = "OFF",
    val startCharge: Int = 1000,
    val stopCharge: Int = 2000,
    val exportWindowStart: String = "17:00",
    val exportWindowStop: String = "19:00",
    val useExport: String = "OFF",
    val dischargeEfficiency: Double = 0.92,
    val pvChargeEfficiency: Double = 0.96,
    val maxOutputW: Double = 2400.0,
    val chargeEfficiency: Double = 1.0,
    val gridCharge: String = "ON",
    val dischargeReserveWh: Int = 0,
    val dischargeReserveUntil: String = "17:00",
)

data class AppSettings(
    val energyPrices: List<String> = listOf("_EnergyPrices.json"),
    val startDate: String = "",
    val stopDate: String = "",
    val scanFromYear: Int = 2025,
    val originalPrice: Double = 6206.47,
    val offPeakShift: String = "ON",
    val offPeakBaseline: Double = 0.96,
    val batteryConfigs: List<BatteryConfig> = listOf(BatteryConfig()),
)

fun AppSettings.isOffPeakShiftOn() = offPeakShift.equals("ON", ignoreCase = true)

fun BatteryConfig.isEnabled() = enabled.equals("ON", ignoreCase = true)
fun BatteryConfig.isPvEnabled() = usePV.equals("ON", ignoreCase = true)
fun BatteryConfig.isExportEnabled() = useExport.equals("ON", ignoreCase = true)
fun BatteryConfig.isGridCharge() = gridCharge.equals("ON", ignoreCase = true)

fun BatteryConfig.toVirtualBattery(priceData: PriceData) = VirtualBattery(
    priceData = priceData,
    batterySize = batterySize,
    pvEnabled = if (isPvEnabled()) 1 else 0,
    startCharging = startCharge,
    stopCharging = stopCharge,
    exportWindowStart = exportWindowStart,
    exportWindowStop = exportWindowStop,
    dischargeEfficiency = dischargeEfficiency,
    pvChargeEfficiency = pvChargeEfficiency,
    maxOutputW = maxOutputW,
    useExport = isExportEnabled(),
    chargeEfficiency = chargeEfficiency,
    gridCharge = isGridCharge(),
    dischargeReserveWh = dischargeReserveWh,
    dischargeReserveUntilStr = dischargeReserveUntil,
)

fun loadSettingsFromJson(json: JsonObject): AppSettings {
    val gson = Gson()

    val energyPricesRaw = json.get("energyPrices")
    val energyPrices = when {
        energyPricesRaw == null -> listOf("_EnergyPrices.json")
        energyPricesRaw.isJsonArray -> energyPricesRaw.asJsonArray.map { it.asString }
        else -> listOf(energyPricesRaw.asString)
    }

    val defaults = BatteryConfig()
    val vbRaw = json.get("virtualBattery")
    val batteryConfigs: List<BatteryConfig> = when {
        vbRaw == null -> listOf(defaults)
        vbRaw.isJsonArray -> vbRaw.asJsonArray.map { parseBatteryConfig(it.asJsonObject, defaults) }
        else -> listOf(parseBatteryConfig(vbRaw.asJsonObject, defaults))
    }

    return AppSettings(
        energyPrices = energyPrices,
        startDate = json.get("startDate")?.asString ?: "",
        stopDate = json.get("stopDate")?.asString ?: "",
        scanFromYear = json.get("scanFromYear")?.asInt ?: 2025,
        originalPrice = json.get("originalPrice")?.asDouble ?: 6206.47,
        offPeakShift = json.get("offPeakShift")?.asString ?: "ON",
        offPeakBaseline = json.get("offPeakBaseline")?.asDouble ?: 0.96,
        batteryConfigs = batteryConfigs,
    )
}

private fun parseBatteryConfig(obj: JsonObject, defaults: BatteryConfig) = BatteryConfig(
    label = obj.get("label")?.asString ?: defaults.label,
    enabled = obj.get("enabled")?.asString ?: defaults.enabled,
    batterySize = obj.get("batterySize")?.asInt ?: defaults.batterySize,
    batteryPrice = obj.get("batteryPrice")?.asDouble ?: defaults.batteryPrice,
    usePV = obj.get("usePV")?.asString ?: defaults.usePV,
    startCharge = obj.get("startCharge")?.asInt ?: defaults.startCharge,
    stopCharge = obj.get("stopCharge")?.asInt ?: defaults.stopCharge,
    exportWindowStart = obj.get("exportWindowStart")?.asString ?: defaults.exportWindowStart,
    exportWindowStop = obj.get("exportWindowStop")?.asString ?: defaults.exportWindowStop,
    useExport = obj.get("useExport")?.asString ?: defaults.useExport,
    dischargeEfficiency = obj.get("dischargeEfficiency")?.asDouble ?: defaults.dischargeEfficiency,
    pvChargeEfficiency = obj.get("pvChargeEfficiency")?.asDouble ?: defaults.pvChargeEfficiency,
    maxOutputW = obj.get("maxOutputW")?.asDouble ?: defaults.maxOutputW,
    chargeEfficiency = obj.get("chargeEfficiency")?.asDouble ?: defaults.chargeEfficiency,
    gridCharge = obj.get("gridCharge")?.asString ?: defaults.gridCharge,
    dischargeReserveWh = obj.get("dischargeReserveWh")?.asInt ?: defaults.dischargeReserveWh,
    dischargeReserveUntil = obj.get("dischargeReserveUntil")?.asString ?: defaults.dischargeReserveUntil,
)

fun settingsToJson(settings: AppSettings): JsonObject {
    val obj = JsonObject()
    if (settings.energyPrices.size == 1) {
        obj.addProperty("energyPrices", settings.energyPrices[0])
    } else {
        val arr = JsonArray()
        settings.energyPrices.forEach { arr.add(it) }
        obj.add("energyPrices", arr)
    }
    obj.addProperty("startDate", settings.startDate)
    obj.addProperty("stopDate", settings.stopDate)
    obj.addProperty("scanFromYear", settings.scanFromYear)
    obj.addProperty("originalPrice", settings.originalPrice)
    obj.addProperty("offPeakShift", settings.offPeakShift)
    obj.addProperty("offPeakBaseline", settings.offPeakBaseline)
    val vb = settings.batteryConfigs
    if (vb.size == 1) {
        obj.add("virtualBattery", batteryConfigToJson(vb[0]))
    } else {
        val arr = JsonArray()
        vb.forEach { arr.add(batteryConfigToJson(it)) }
        obj.add("virtualBattery", arr)
    }
    return obj
}

private fun batteryConfigToJson(bc: BatteryConfig): JsonObject {
    val obj = JsonObject()
    if (bc.label.isNotBlank()) obj.addProperty("label", bc.label)
    obj.addProperty("enabled", bc.enabled)
    obj.addProperty("batterySize", bc.batterySize)
    obj.addProperty("batteryPrice", bc.batteryPrice)
    obj.addProperty("usePV", bc.usePV)
    obj.addProperty("startCharge", bc.startCharge)
    obj.addProperty("stopCharge", bc.stopCharge)
    obj.addProperty("exportWindowStart", bc.exportWindowStart)
    obj.addProperty("exportWindowStop", bc.exportWindowStop)
    obj.addProperty("useExport", bc.useExport)
    obj.addProperty("dischargeEfficiency", bc.dischargeEfficiency)
    obj.addProperty("pvChargeEfficiency", bc.pvChargeEfficiency)
    obj.addProperty("maxOutputW", bc.maxOutputW)
    obj.addProperty("chargeEfficiency", bc.chargeEfficiency)
    obj.addProperty("gridCharge", bc.gridCharge)
    obj.addProperty("dischargeReserveWh", bc.dischargeReserveWh)
    obj.addProperty("dischargeReserveUntil", bc.dischargeReserveUntil)
    return obj
}

class ConfigStore(private val context: Context) {
    private val configFile = File(context.getExternalFilesDir(null), "config.json")

    fun load(): AppSettings {
        if (!configFile.exists()) copyFromAssets()
        return try {
            loadSettingsFromJson(JsonParser.parseString(configFile.readText()).asJsonObject)
        } catch (e: Exception) {
            AppSettings()
        }
    }

    fun save(settings: AppSettings) {
        val json = settingsToJson(settings)
        configFile.writeText(GsonBuilder().setPrettyPrinting().create().toJson(json))
    }

    fun availablePriceFiles(): List<String> =
        context.assets.list("inverterData")
            ?.filter { it.startsWith("_EnergyPrices") && it.endsWith(".json") }
            ?.sorted() ?: emptyList()

    private fun copyFromAssets() {
        context.assets.open("config.json").use { input ->
            configFile.outputStream().use { input.copyTo(it) }
        }
    }
}

data class PricePeriod(
    val dateFrom: String = "",
    val dateTo: String = "",
    val offpeakRate: String = "0.075",
    val offpeakStart: String = "00:00",
    val offpeakStop: String = "07:00",
    val peakRate: String = "0.30",
    val exportRate: String = "0.165",
    val standingCharge: String = "0.60",
    val interestRate: String = "3.7",
)

class TariffStore(private val context: Context) {
    private val dir = File(context.getExternalFilesDir(null), "inverterData")

    fun load(filename: String): List<PricePeriod> {
        val file = File(dir, filename)
        if (!file.exists()) copyFromAssets(filename)
        return parse(JsonParser.parseString(file.readText()).asJsonObject)
    }

    fun save(filename: String, periods: List<PricePeriod>) {
        dir.mkdirs()
        val arr = JsonArray()
        periods.forEach { arr.add(periodToJson(it)) }
        val root = JsonObject().apply {
            addProperty("code", 0)
            addProperty("msg", "Success")
            add("data", JsonObject().apply { add("prices", arr) })
        }
        File(dir, filename).writeText(GsonBuilder().setPrettyPrinting().create().toJson(root))
    }

    fun ensureExternal(filename: String) {
        if (!File(dir, filename).exists()) copyFromAssets(filename)
    }

    fun isEdited(filename: String): Boolean = File(dir, filename).exists()

    fun delete(filename: String) {
        File(dir, filename).delete()
    }

    private fun copyFromAssets(filename: String) {
        dir.mkdirs()
        context.assets.open("inverterData/$filename").use { it.copyTo(File(dir, filename).outputStream()) }
    }

    private fun parse(root: JsonObject): List<PricePeriod> =
        root.getAsJsonObject("data").getAsJsonArray("prices").map { e ->
            val o = e.asJsonObject
            PricePeriod(
                dateFrom = o.get("datefrom")?.asString ?: "",
                dateTo = o.get("dateto")?.asString ?: "",
                offpeakRate = o.get("offpeakRate")?.asString ?: "0.075",
                offpeakStart = o.get("offpeakStart")?.asString ?: "00:00",
                offpeakStop = o.get("offpeakStop")?.asString ?: "07:00",
                peakRate = o.get("peakRate")?.asString ?: "0.30",
                exportRate = o.get("exportRate")?.asString ?: "0.165",
                standingCharge = o.get("standingCharge")?.asString ?: "0.60",
                interestRate = o.get("InterestRate")?.asString ?: "3.7",
            )
        }

    private fun periodToJson(p: PricePeriod) = JsonObject().apply {
        addProperty("datefrom", p.dateFrom)
        addProperty("dateto", p.dateTo)
        addProperty("offpeakRate", p.offpeakRate)
        addProperty("offpeakStart", p.offpeakStart)
        addProperty("offpeakStop", p.offpeakStop)
        addProperty("peakRate", p.peakRate)
        addProperty("exportRate", p.exportRate)
        addProperty("standingCharge", p.standingCharge)
        addProperty("InterestRate", p.interestRate)
    }
}

class ExportStore(private val context: Context) {

    fun createZip(): File {
        val zipFile = File(context.cacheDir, "sunsynk-export.zip")
        ZipOutputStream(BufferedOutputStream(zipFile.outputStream())).use { zip ->
            val root = context.getExternalFilesDir(null) ?: return@use
            addEntry(zip, File(root, "config.json"), "config.json")
            val dataDir = File(root, "inverterData")
            dataDir.listFiles()
                ?.filter { it.isFile && it.name.endsWith(".json") }
                ?.sortedBy { it.name }
                ?.forEach { addEntry(zip, it, "inverterData/${it.name}") }
        }
        return zipFile
    }

    fun share(zipFile: File) {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", zipFile)
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "application/zip"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(Intent.createChooser(intent, "Export Sunsynk data"))
    }

    private fun addEntry(zip: ZipOutputStream, file: File, name: String) {
        if (!file.exists()) return
        zip.putNextEntry(ZipEntry(name))
        file.inputStream().use { it.copyTo(zip) }
        zip.closeEntry()
    }
}

enum class ThemeMode { SYSTEM, LIGHT, DARK }

class PrefsStore(context: Context) {
    private val prefs = context.getSharedPreferences("ui_prefs", Context.MODE_PRIVATE)
    var themeMode: ThemeMode
        get() = ThemeMode.entries.getOrNull(prefs.getInt("theme_mode", 0)) ?: ThemeMode.SYSTEM
        set(v) { prefs.edit().putInt("theme_mode", v.ordinal).apply() }
}

class CredentialStore(context: Context) {
    private val prefs: SharedPreferences = try {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context, "sunsynk_creds", masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    } catch (e: Exception) {
        context.getSharedPreferences("sunsynk_creds_fallback", Context.MODE_PRIVATE)
    }

    var username: String
        get() = prefs.getString("username", "") ?: ""
        set(v) = prefs.edit().putString("username", v).apply()

    var password: String
        get() = prefs.getString("password", "") ?: ""
        set(v) = prefs.edit().putString("password", v).apply()

    fun hasCredentials() = username.isNotBlank() && password.isNotBlank()
}
