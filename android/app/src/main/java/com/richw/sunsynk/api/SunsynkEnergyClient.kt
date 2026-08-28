package com.richw.sunsynk.api

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.time.LocalDate
import java.time.format.DateTimeFormatter

class SunsynkEnergyClient(
    username: String,
    password: String,
    baseUrl: String = "https://api.sunsynk.net",
    private val cacheDir: File,
) : SunsynkClient(username, password, baseUrl) {

    private val today = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE)

    suspend fun getEnergyMonthRaw(plantId: String, date: String): JsonObject {
        val monthKey = date.take(7)
        return getCached(
            "api/v1/plant/energy/$plantId/month?lan=en&date=$date&id=$plantId",
            "month", monthKey
        )
    }

    suspend fun getEnergyDayRaw(plantId: String, date: String): JsonObject {
        return getCached(
            "api/v1/plant/energy/$plantId/day?lan=en&date=$date&id=$plantId",
            "day", date
        )
    }

    private suspend fun getCached(path: String, reqType: String, date: String): JsonObject =
        withContext(Dispatchers.IO) {
            val file = File(cacheDir, "$reqType-$date.json")
            if (file.exists()) {
                return@withContext JsonParser.parseString(file.readText()).asJsonObject
            }
            val body = get(path)
            if (date != today.take(date.length)) {
                file.writeText(body.toString())
            }
            body
        }
}
