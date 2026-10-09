// Port of ios/WhichWay/WhichWay/Models/Alerts.swift: service alerts from the MTA Mercury JSON feed, one entry per
// active period, unplanned delays first.
package com.whichway.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonObject

data class RouteAlert(
    val id: String,
    val kind: String,          // delay | planned | notice
    val type: String?,
    val header: String,
    val routes: List<String>,
    val start: Double?,
    val end: Double?,
    val stops: List<String> = emptyList(),
)

object Alerts {
    private const val MERCURY = "transit_realtime.mercury_alert"
    private val planned = listOf("planned", "weekend service", "buses replace trains", "no midday service", "no weekend service", "special schedule")
    private val notices = listOf("boarding change", "station notice", "extra service", "elevator", "escalator", "accessibility", "service reminder", "shuttle bus")

    fun kind(type: String?, header: String): String {
        val t = (type ?: "").lowercase()
        val h = header.lowercase()
        if (planned.any { t.startsWith(it) } || h.contains("planned work") || h.contains("scheduled maintenance")) return "planned"
        if (notices.any { t.startsWith(it) }) return "notice"
        return "delay"
    }

    private fun prim(e: Any?): JsonPrimitive? = e as? JsonPrimitive
    private fun num(e: Any?): Double? = prim(e)?.let { it.doubleOrNull ?: it.content.toDoubleOrNull() }
    private fun string(e: Any?): String? = prim(e)?.takeIf { it.isString }?.content

    private fun text(e: Any?): String {
        val tr = (e as? JsonObject)?.get("translation") as? JsonArray ?: return ""
        val en = tr.firstOrNull { string((it as? JsonObject)?.get("language")) == "en" } ?: tr.firstOrNull()
        return (string((en as? JsonObject)?.get("text")) ?: "").trim()
    }

    fun parse(text: String, now: Double): List<RouteAlert> {
        val doc = runCatching { WWJson.parseToJsonElement(text).jsonObject }.getOrNull() ?: return emptyList()
        val ents = doc["entity"] as? JsonArray ?: return emptyList()
        val out = ArrayList<RouteAlert>()
        for ((n, ent) in ents.withIndex()) {
            val eo = ent as? JsonObject ?: continue
            val a = eo["alert"] as? JsonObject ?: continue
            val merc = a[MERCURY] as? JsonObject
            val header = text(a["header_text"])
            val type = string(merc?.get("alert_type"))
            val informed = (a["informed_entity"] as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()
            val routes = informed.mapNotNull { string(it["route_id"]) }.toSortedSet().toList()
            val stops = informed.mapNotNull { string(it["stop_id"]) }.toSortedSet().toList()
            val updated = num(merc?.get("updated_at"))
            var periods = (a["active_period"] as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()
            if (periods.isEmpty()) periods = listOf(JsonObject(emptyMap()))
            val id = string(eo["id"]) ?: "alert-$n"
            for ((i, p) in periods.withIndex()) {
                val start = num(p["start"])
                val end = num(p["end"])
                val endEff = end ?: updated?.plus(3 * 3600) ?: start?.plus(3 * 3600)
                if (start != null && start > now) continue
                if (endEff != null && endEff < now) continue
                out.add(RouteAlert("$id#$i", kind(type, header), type, header, routes, start, end, stops))
            }
        }
        val rank = mapOf("delay" to 0, "planned" to 1, "notice" to 2)
        return out.sortedWith(compareBy<RouteAlert> { rank[it.kind] ?: 3 }.thenByDescending { it.start ?: 0.0 })
    }
}
