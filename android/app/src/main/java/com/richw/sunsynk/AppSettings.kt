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
import com.richw.sunsynk.analysis.ExportCorrection
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

/**
 * Calibration of inverter export to the supplier's smart meter (mirrors the Python
 * "exportCorrection" config): [gain] scales export readings, [chargerStandbyW] is the
 * EV charger standby draw between the inverter CT and the meter, which is 0 on dates
 * inside any inclusive [chargerOff] (from, to) range. Defaults reproduce the original
 * fixed 2.3 Wh per 5-minute deduction.
 */
data class ExportCorrectionConfig(
    val gain: Double = 1.0,
    val chargerStandbyW: Double = 27.6,
    val chargerOff: List<Pair<String, String>> = emptyList(),
) {
    fun forDate(date: String): ExportCorrection {
        val off = chargerOff.any { (from, to) -> date >= from && date <= to }
        return ExportCorrection(gain = gain, standbyW = if (off) 0.0 else chargerStandbyW)
    }
}

data class AppSettings(
    val energyPrices: List<String> = listOf("_EnergyPrices.json"),
    val startDate: String = "",
    val stopDate: String = "",
    val scanFromYear: Int = 2025,
    val originalPrice: Double = 6210.0,
    val offPeakShift: String = "ON",
    val offPeakBaseline: Double = 0.96,
    val batteryConfigs: List<BatteryConfig> = listOf(BatteryConfig()),
    val exportCorrection: ExportCorrectionConfig = ExportCorrectionConfig(),
    /** Add the Indra EV charger (beyond the inverter CT) to load/import, when downloaded. */
    val evCharging: String = "ON",
    /** Project EV charging at the level since this date (e.g. a second car); "" = seasonal. */
    val evBasisFrom: String = "",
    /** The EV tab's petrol comparison: car figures and charging the charger never saw. */
    val evSavings: com.richw.sunsynk.analysis.EvSavingsConfig = com.richw.sunsynk.analysis.EvSavingsConfig(),
)

fun AppSettings.isOffPeakShiftOn() = offPeakShift.equals("ON", ignoreCase = true)
fun AppSettings.isEvChargingOn() = evCharging.equals("ON", ignoreCase = true)

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
        originalPrice = json.get("originalPrice")?.asDouble ?: 6210.0,
        offPeakShift = json.get("offPeakShift")?.asString ?: "ON",
        offPeakBaseline = json.get("offPeakBaseline")?.asDouble ?: 0.96,
        batteryConfigs = batteryConfigs,
        exportCorrection = parseExportCorrection(json.getAsJsonObject("exportCorrection")),
        evCharging = json.get("evCharging")?.asString ?: "ON",
        evBasisFrom = json.get("evBasisFrom")?.asString ?: "",
        evSavings = parseEvSavings(json.getAsJsonObject("evSavings")),
    )
}

fun parseEvSavings(obj: JsonObject?): com.richw.sunsynk.analysis.EvSavingsConfig {
    val d = com.richw.sunsynk.analysis.EvSavingsConfig()
    if (obj == null) return d
    return com.richw.sunsynk.analysis.EvSavingsConfig(
        mpg = obj.get("mpg")?.asDouble ?: d.mpg,
        milesPerKwh = obj.get("milesPerKwh")?.asDouble ?: d.milesPerKwh,
        chargeEfficiency = obj.get("chargeEfficiency")?.asDouble ?: d.chargeEfficiency,
        beforeCharger = obj.getAsJsonArray("beforeCharger")?.map {
            val o = it.asJsonObject
            com.richw.sunsynk.analysis.ExtraCharge(o.get("month").asString, o.get("kwh").asDouble, o.get("rate").asDouble)
        } ?: d.beforeCharger,
        credits = obj.getAsJsonArray("credits")?.map {
            val o = it.asJsonObject
            com.richw.sunsynk.analysis.EvCredit(o.get("month").asString, o.get("amount").asDouble,
                o.get("note")?.asString ?: "")
        } ?: d.credits,
    )
}

fun evSavingsToJson(c: com.richw.sunsynk.analysis.EvSavingsConfig) = JsonObject().apply {
    addProperty("mpg", c.mpg)
    addProperty("milesPerKwh", c.milesPerKwh)
    addProperty("chargeEfficiency", c.chargeEfficiency)
    add("beforeCharger", JsonArray().apply {
        c.beforeCharger.forEach { e -> add(JsonObject().apply {
            addProperty("month", e.month); addProperty("kwh", e.kwh); addProperty("rate", e.rate) }) }
    })
    add("credits", JsonArray().apply {
        c.credits.forEach { e -> add(JsonObject().apply {
            addProperty("month", e.month); addProperty("amount", e.amount); addProperty("note", e.note) }) }
    })
}

fun parseExportCorrection(obj: JsonObject?): ExportCorrectionConfig {
    val defaults = ExportCorrectionConfig()
    if (obj == null) return defaults
    return ExportCorrectionConfig(
        gain = obj.get("gain")?.asDouble ?: defaults.gain,
        chargerStandbyW = obj.get("chargerStandbyW")?.asDouble ?: defaults.chargerStandbyW,
        chargerOff = obj.getAsJsonArray("chargerOff")?.map { r ->
            val range = r.asJsonArray
            Pair(range[0].asString, range[1].asString)
        } ?: defaults.chargerOff,
    )
}

fun exportCorrectionToJson(ec: ExportCorrectionConfig) = JsonObject().apply {
    addProperty("gain", ec.gain)
    addProperty("chargerStandbyW", ec.chargerStandbyW)
    add("chargerOff", JsonArray().apply {
        ec.chargerOff.forEach { (from, to) -> add(JsonArray().apply { add(from); add(to) }) }
    })
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
    obj.add("exportCorrection", exportCorrectionToJson(settings.exportCorrection))
    obj.addProperty("evCharging", settings.evCharging)
    obj.addProperty("evBasisFrom", settings.evBasisFrom)
    obj.add("evSavings", evSavingsToJson(settings.evSavings))
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
            val json = JsonParser.parseString(configFile.readText()).asJsonObject
            // Configs saved before exportCorrection existed: adopt the bundled calibration.
            if (!json.has("exportCorrection")) bundledExportCorrection()?.let { json.add("exportCorrection", it) }
            // Configs saved before EV charging was modelled: adopt the bundled EV settings, and
            // drop the old -£3.53 "solar used to charge the car" install-cost adjustment, which
            // the EV model now counts directly.
            if (!json.has("evCharging")) bundled()?.let { b ->
                for (key in listOf("evCharging", "evBasisFrom")) b.get(key)?.let { json.add(key, it) }
                if (json.get("originalPrice")?.asDouble == 6206.47) b.get("originalPrice")?.let { json.add("originalPrice", it) }
            }
            // Configs saved before the EV tab existed: adopt the bundled petrol comparison settings.
            if (!json.has("evSavings")) bundled()?.get("evSavings")?.let { json.add("evSavings", it) }
            json.getAsJsonObject("evSavings")?.let { ev ->
                if (!ev.has("credits")) bundled()?.getAsJsonObject("evSavings")?.get("credits")?.let { ev.add("credits", it) }
            }
            loadSettingsFromJson(json)
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

    private fun bundled(): JsonObject? = try {
        context.assets.open("config.json").bufferedReader().use { JsonParser.parseReader(it).asJsonObject }
    } catch (e: Exception) {
        null
    }

    private fun bundledExportCorrection(): JsonObject? = bundled()?.getAsJsonObject("exportCorrection")

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
    /** DNO region letter (A-P) for the Tariffs tab. */
    var tariffRegion: String
        get() = prefs.getString("tariff_region", "") ?: ""
        set(v) { prefs.edit().putString("tariff_region", v).apply() }
    /** The EV tab's supermarket petrol stations (found from a postcode, which isn't stored). */
    var fuelStations: List<com.richw.sunsynk.api.FuelStation>
        get() = runCatching {
            com.google.gson.Gson().fromJson(prefs.getString("fuel_stations", "[]"),
                Array<com.richw.sunsynk.api.FuelStation>::class.java).toList()
        }.getOrDefault(emptyList())
        set(v) { prefs.edit().putString("fuel_stations", com.google.gson.Gson().toJson(v)).apply() }
}

/** The latest analysis run's half-hourly year (TariffProfile), kept in private app storage. */
class TariffProfileStore(context: Context) {
    private val file = java.io.File(context.filesDir, "tariffProfile.json")
    private val gson = com.google.gson.Gson()
    fun save(profile: com.richw.sunsynk.analysis.TariffProfile) = file.writeText(gson.toJson(profile))
    fun load(): com.richw.sunsynk.analysis.TariffProfile? = try {
        if (file.exists()) gson.fromJson(file.readText(), com.richw.sunsynk.analysis.TariffProfile::class.java) else null
    } catch (e: Exception) { null }
}

/** Each day's half-hourly house/EV import and export from the latest analysis run, plus the
 *  energyPrices periods, for the Tariffs tab's history view (private filesDir). */
class TariffHistoryDaysStore(context: Context) {
    private val file = java.io.File(context.filesDir, "tariffDays.json")
    private val gson = com.google.gson.Gson()
    fun save(days: com.richw.sunsynk.analysis.HistoryDays) =
        file.bufferedWriter().use { gson.toJson(days, it) }
    fun load(): com.richw.sunsynk.analysis.HistoryDays? = try {
        if (file.exists()) file.bufferedReader().use {
            gson.fromJson(it, com.richw.sunsynk.analysis.HistoryDays::class.java)
        } else null
    } catch (e: Exception) { null }
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

/**
 * Sign-in state for the Fuse (export meter) and Indra (EV charger) downloads, encrypted like
 * the Sunsynk credentials. Their data caches live in the app's private storage
 * ([fuseDir]/[indraDir]), not the shareable external folder: Indra's data includes the
 * home address.
 */
class DataSourceStore(private val context: Context) {
    private val prefs: SharedPreferences = try {
        val masterKey = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context, "data_sources", masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    } catch (e: Exception) {
        context.getSharedPreferences("data_sources_fallback", Context.MODE_PRIVATE)
    }

    val fuseDir get() = File(context.filesDir, "fuseenergydata")
    val indraDir get() = File(context.filesDir, "indraData")
    val eonDir get() = File(context.filesDir, "eonData")

    /** E.ON Next Auth0 session. Its refresh token is single-use (each refresh returns a new one
     *  and an old one is then rejected), so a session can't be shared with the desktop
     *  eondata.py: each device signs in on its own. */
    var eonTokens: com.richw.sunsynk.api.EonTokens
        get() = com.richw.sunsynk.api.EonTokens(prefs.getString("eon_id_token", "") ?: "",
            prefs.getString("eon_refresh_token", "") ?: "")
        set(v) = prefs.edit().putString("eon_id_token", v.idToken).putString("eon_refresh_token", v.refreshToken).apply()

    var fusePhone: String
        get() = prefs.getString("fuse_phone", "") ?: ""
        set(v) = prefs.edit().putString("fuse_phone", v).apply()

    var fusePremises: String
        get() = prefs.getString("fuse_premises", "") ?: ""
        set(v) = prefs.edit().putString("fuse_premises", v).apply()

    /** Fuse session cookies. A session saved by the desktop downloader (fuseenergydata/
     *  .session.json, {name: value}) can be copied into [fuseDir] via adb; it is adopted on
     *  first read, moved into the encrypted store, and the plain file deleted. */
    var fuseCookies: Map<String, String>
        get() {
            val imported = File(fuseDir, ".session.json")
            if (imported.exists()) {
                runCatching {
                    JsonParser.parseString(imported.readText()).asJsonObject.entrySet()
                        .associate { it.key to it.value.asString }
                }.getOrNull()?.takeIf { "app-auth" in it }?.let { fuseCookies = it }
                imported.delete()
            }
            return (prefs.getString("fuse_cookies", null) ?: "{}").let { raw ->
                JsonParser.parseString(raw).asJsonObject.entrySet().associate { it.key to it.value.asString }
            }
        }
        set(v) = prefs.edit().putString("fuse_cookies", Gson().toJson(v)).apply()

    /** A Fuse sign-in waiting for its SMS code (phoneSignIn's session_id + otp cookies and when it
     *  was sent), so the code can still be entered if Android kills the app meanwhile. */
    var fusePending: Pair<Map<String, String>, Long>?
        get() {
            val raw = prefs.getString("fuse_pending", null) ?: return null
            return runCatching {
                val o = JsonParser.parseString(raw).asJsonObject
                Pair(o.getAsJsonObject("cookies").entrySet().associate { it.key to it.value.asString }, o.get("at").asLong)
            }.getOrNull()
        }
        set(v) = prefs.edit().apply {
            if (v == null) remove("fuse_pending")
            else putString("fuse_pending", com.google.gson.JsonObject().apply {
                add("cookies", Gson().toJsonTree(v.first)); addProperty("at", v.second)
            }.toString())
        }.apply()

    var indraSession: com.richw.sunsynk.api.IndraSession
        get() = com.richw.sunsynk.api.IndraSession(
            email = prefs.getString("indra_email", "") ?: "",
            deviceKey = prefs.getString("indra_device_key", "") ?: "",
            checkHash = prefs.getString("indra_check_hash", "") ?: "",
            token = prefs.getString("indra_token", "") ?: "",
        )
        set(v) = prefs.edit()
            .putString("indra_email", v.email).putString("indra_device_key", v.deviceKey)
            .putString("indra_check_hash", v.checkHash).putString("indra_token", v.token).apply()
}
