package com.richw.sunsynk.analysis

import com.google.gson.JsonObject

class EnergyMonth(private val data: JsonObject) {

    data class LabelData(val label: String, val records: List<Record>)
    data class Record(val time: String, val value: String)

    private var load: LabelData? = null
    private var pv: LabelData? = null
    private var export: LabelData? = null
    private var import: LabelData? = null

    init {
        val infos = data.getAsJsonArray("infos")
        for (item in infos) {
            val obj = item.asJsonObject
            val label = obj.get("label").asString
            val records = obj.getAsJsonArray("records").map { r ->
                val rec = r.asJsonObject
                Record(rec.get("time").asString, rec.get("value").asString)
            }
            val ld = LabelData(label, records)
            when (label) {
                "Load", "Load Power Consumption" -> load = ld
                "PV", "PV Generation" -> pv = ld
                "Export", "Sold electricity" -> export = ld
                "Import", "Purchased electricity" -> import = ld
            }
        }
    }

    fun getLoad() = load
    fun getPv() = pv
    fun getExport() = export
    fun getImport() = import
}
