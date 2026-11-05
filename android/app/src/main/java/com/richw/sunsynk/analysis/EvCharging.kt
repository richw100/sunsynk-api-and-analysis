package com.richw.sunsynk.analysis

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.io.File
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.temporal.ChronoUnit

/**
 * 5-minute EV charging profiles from the cached Indra charger data (mirrors Python
 * analysis/evcharging.py). The charger sits between the inverter CT and the supply meter,
 * so Sunsynk never sees it; EnergyDay adds these profiles to Load and Grid.
 *
 * Indra keeps per-minute telemetry for only the last few months. Older sessions are
 * reconstructed from start/end, the off-peak/standard kWh split and the scheduled window(s)
 * (`events`): the car draws a flat charger power, so off-peak energy goes from the start of
 * the scheduled window and standard-rate energy from plug-in, minute by minute; any energy
 * that doesn't fit is spread evenly over the session.
 *
 * Until Indra is told the tariff it labels every kWh "Standard", so for sessions before the
 * first one with any off-peak energy (`splitKnown = false`) the split means nothing: the E.ON
 * meter shows those were the car's own overnight charges, so their energy goes into the
 * scheduled window (windows under [MIN_WINDOW_MINUTES] ignored), else from the first local
 * midnight in the session, else from plug-in.
 */
object EvCharging {
    val LOCAL_TZ: ZoneId = ZoneId.of("Europe/London")
    const val DEFAULT_CHARGER_KW = 7.4
    private const val SAMPLE_CAP_S = 300L
    const val MIN_WINDOW_MINUTES = 30L      // shorter scheduled events (e.g. 23:05-23:10) aren't charging windows

    private fun utc(stamp: String): Instant = Instant.parse(if (stamp.endsWith("Z")) stamp else "${stamp}Z")

    /** (local date "yyyy-MM-dd", 5-minute slot "HH:mm") for an instant. */
    fun slot(at: Instant): Pair<String, String> {
        val local = at.atZone(LOCAL_TZ)
        return Pair(local.toLocalDate().toString(), "%02d:%02d".format(local.hour, local.minute - local.minute % 5))
    }

    private fun subtotals(session: JsonObject): Pair<Double, Double> {
        var off = 0.0
        var std = 0.0
        val subs = session.getAsJsonArray("subTotals") ?: session.getAsJsonArray("subtotals") ?: JsonArray()
        for (s in subs) {
            val o = s.asJsonObject
            val kwh = o.get("energyImportedKwh")?.takeUnless { it.isJsonNull }?.asDouble ?: 0.0
            if (o.get("name").asString.lowercase().replace("-", "") == "offpeak") off += kwh else std += kwh
        }
        return Pair(off, std)
    }

    /** [(instant, Wh)] for per-minute telemetry: each sample's power held until the next
     *  (capped at 5 minutes; the last sample counts for one minute). */
    fun telemetryWh(rows: List<JsonObject>): List<Pair<Instant, Double>> {
        val times = rows.map { utc(it.get("time").asString) }
        return rows.indices.map { i ->
            val span = if (i + 1 < rows.size) Duration.between(times[i], times[i + 1]).seconds else 60L
            Pair(times[i], rows[i].get("powerKw").asDouble * 1000 * minOf(span, SAMPLE_CAP_S) / 3600.0)
        }
    }

    /** The first local midnight after [start], or null if the session ends first. */
    private fun firstLocalMidnight(start: Instant, end: Instant): Instant? {
        val midnight = start.atZone(LOCAL_TZ).toLocalDate().plusDays(1).atStartOfDay(LOCAL_TZ).toInstant()
        return midnight.takeIf { it.isBefore(end) }
    }

    /** [(minute, Wh)] for a session without telemetry (see class docs). */
    fun synthesiseWh(session: JsonObject, chargerKw: Double, splitKnown: Boolean = true): List<Pair<Instant, Double>> {
        val start = utc(session.get("start").asString)
        val end = utc(session.get("end").asString)
        val minutes = mutableListOf<Instant>()
        var t = start.truncatedTo(ChronoUnit.MINUTES)
        while (t.isBefore(end)) {
            minutes.add(t)
            t = t.plusSeconds(60)
        }
        if (minutes.isEmpty()) return emptyList()
        var windows = (session.getAsJsonArray("events") ?: JsonArray()).map {
            val e = it.asJsonObject
            Pair(utc(e.get("start").asString), utc(e.get("end").asString))
        }
        var (offKwh, stdKwh) = subtotals(session)
        if (!splitKnown) {
            // Indra didn't know the tariff: it was all scheduled (overnight) charging.
            windows = windows.filter { (a, b) -> Duration.between(a, b).toMinutes() >= MIN_WINDOW_MINUTES }
            offKwh += stdKwh
            stdKwh = 0.0
            if (windows.isEmpty()) windows = listOf(Pair(firstLocalMidnight(start, end) ?: start, end))
        }
        fun inWindow(m: Instant) = windows.any { (a, b) -> !m.isBefore(a) && m.isBefore(b) }

        val perMin = chargerKw * 1000 / 60
        val energy = LinkedHashMap<Instant, Double>()
        val passes = listOf(
            Pair(offKwh * 1000, if (windows.isNotEmpty()) minutes.filter { inWindow(it) } else minutes),
            Pair(stdKwh * 1000, minutes.filter { !inWindow(it) }),
        )
        for ((total, pool) in passes) {
            var wh = total
            for (m in pool) {
                if (wh <= 0) break
                val room = perMin - (energy[m] ?: 0.0)
                if (room <= 0) continue
                val take = minOf(room, wh)
                energy[m] = (energy[m] ?: 0.0) + take
                wh -= take
            }
            if (wh > 0) for (m in minutes) energy[m] = (energy[m] ?: 0.0) + wh / minutes.size
        }
        return energy.entries.sortedBy { it.key }.map { Pair(it.key, it.value) }
    }

    private fun readJson(f: File) = JsonParser.parseString(f.readText())

    /** {"yyyy-MM-dd": {"HH:mm": W}} (local time, 5-minute average power) for every cached
     *  session; telemetry when cached, else reconstructed. Missing dir -> empty. */
    fun loadProfiles(dataDir: File, chargerKw: Double? = null): Map<String, Map<String, Double>> {
        val files = dataDir.listFiles() ?: return emptyMap()
        val sessions = files.filter { it.name.startsWith("session-") && it.name.endsWith(".json") }
            .mapNotNull { f -> readJson(f).takeIf { it.isJsonObject }?.asJsonObject }
            .filter { s -> listOf("start", "end").all { s.get(it)?.isJsonNull == false } }
            .associateBy { it.get("id").asString }
        val telemetry = sessions.keys.mapNotNull { id ->
            val f = File(dataDir, "telemetry-$id.json")
            val rows = if (f.exists()) readJson(f).takeIf { it.isJsonArray }?.asJsonArray else null
            if (rows != null && rows.size() > 0) id to rows.map { it.asJsonObject } else null
        }.toMap()
        val kw = chargerKw ?: run {
            val samples = telemetry.values.flatten().map { it.get("powerKw").asDouble }.filter { it > 1 }.sorted()
            if (samples.isEmpty()) DEFAULT_CHARGER_KW
            else if (samples.size % 2 == 1) samples[samples.size / 2]
            else (samples[samples.size / 2 - 1] + samples[samples.size / 2]) / 2
        }
        // Indra only splits off-peak/standard once it knows the tariff (see class docs).
        val splitFrom = sessions.values.filter { subtotals(it).first > 0 }
            .minOfOrNull { utc(it.get("start").asString) }
        val whBySlot = HashMap<String, HashMap<String, Double>>()
        for ((id, session) in sessions) {
            val known = splitFrom != null && !utc(session.get("start").asString).isBefore(splitFrom)
            val samples = telemetry[id]?.let { telemetryWh(it) } ?: synthesiseWh(session, kw, known)
            for ((at, wh) in samples) {
                val (day, slot) = slot(at)
                val slots = whBySlot.getOrPut(day) { HashMap() }
                slots[slot] = (slots[slot] ?: 0.0) + wh
            }
        }
        return whBySlot.mapValues { (_, slots) -> slots.mapValues { it.value * 12 } }
    }
}
