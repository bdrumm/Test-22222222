// Port of ios/WhichWay/WhichWay/Models/Schedule.swift and Geometry.swift: the published site's data files.
package com.whichway.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.math.roundToInt

/** The decoder for every published file: unknown keys ignored, nulls and absent keys take the defaults (as the Swift decoders do). */
val WWJson = Json { ignoreUnknownKeys = true; coerceInputValues = true; explicitNulls = false; isLenient = true }

private fun rounded(v: Double?): Int? = v?.let { if (it >= 0) (it + 0.5).toLong().toInt() else -((-it + 0.5).toLong().toInt()) }

@Serializable
data class LineTopology(
    val stops: List<String>,
    val names: List<String> = emptyList(),
    @SerialName("run_sec") private val runSecRaw: List<Double?> = emptyList(),
    @SerialName("dist_m") private val distMRaw: List<Double?> = emptyList(),
) {
    @Transient val runSec: List<Int?> = runSecRaw.map(::rounded)
    @Transient val distM: List<Int?> = distMRaw.map(::rounded)

    /** Scheduled running time between stop indices a <= b along this line (null when a segment is unknown). */
    fun runBetween(a: Int, b: Int): Int? {
        if (a < 0 || b > stops.size - 1 || a > b) return null
        var total = 0
        for (i in a until b) total += runSec.getOrNull(i) ?: return null
        return total
    }

    fun name(i: Int): String = names.getOrNull(i) ?: stops.getOrElse(i) { "" }
}

@Serializable
data class TransferOption(val line: String, val stop: String, @SerialName("min_sec") val minSec: Int = 0)

@Serializable
data class JourneyLeg(
    @SerialName("from_stop") val fromStop: String,
    @SerialName("to_stop") val toStop: String,
    val routes: List<String> = emptyList(),
    @SerialName("from_name") val fromName: String? = null,
    @SerialName("to_name") val toName: String? = null,
    @SerialName("transfer_min") val transferMin: Double? = null,
)

@Serializable
data class Journey(val id: String, val label: String = "", val legs: List<JourneyLeg> = emptyList())

@Serializable
data class ScheduleConstants(
    @SerialName("hold_sec") val holdSec: Double = 150.0,
    @SerialName("stall_slack_sec") val stallSlackSec: Double = 120.0,
    @SerialName("past_slack_sec") val pastSlackSec: Double = 90.0,
    @SerialName("gap_ratio") val gapRatio: Double = 1.5,
    @SerialName("bunching_ratio") val bunchingRatio: Double = 0.5,
    @SerialName("hold_extra_sec") val holdExtraSec: Double = 600.0,
    @SerialName("min_headway_sec") val minHeadwaySec: Double = 90.0,
)

/** client_schedule.json */
@Serializable
data class ClientSchedule(
    @SerialName("generated_at") val generatedAt: String? = null,
    @SerialName("service_date") val serviceDate: String? = null,
    val lines: Map<String, LineTopology>,
    val feeds: Map<String, String> = emptyMap(),
    @SerialName("target_feeds") val targetFeeds: List<String> = emptyList(),
    @SerialName("route_feeds") val routeFeeds: Map<String, String> = emptyMap(),
    val transfers: Map<String, List<TransferOption>> = emptyMap(),
    val journeys: List<Journey> = emptyList(),
    val constants: ScheduleConstants = ScheduleConstants(),
    @SerialName("demo_now") val demoNow: Double? = null,
    @SerialName("alerts_url") val alertsUrl: String? = null,
) {
    companion object { fun parse(text: String): ClientSchedule = WWJson.decodeFromString(serializer(), text) }
}

/** client_lines.json: per line, each trip's scheduled time at the last canonical stop it serves ([stem, lastIdx, ts]). */
data class LineSchedEntry(val stem: String, val lastIdx: Int, val ts: Double)

object ClientLines {
    fun parse(text: String): Map<String, List<LineSchedEntry>> {
        val root = WWJson.parseToJsonElement(text).jsonObject
        val lines = root["lines"] as? JsonObject ?: return emptyMap()
        return lines.mapValues { (_, v) ->
            (v as? JsonArray ?: JsonArray(emptyList())).mapNotNull { e ->
                val a = e as? JsonArray ?: return@mapNotNull null
                if (a.size < 3) return@mapNotNull null
                runCatching { LineSchedEntry(a[0].jsonPrimitive.content, a[1].jsonPrimitive.int, a[2].jsonPrimitive.double) }.getOrNull()
            }
        }
    }
}

// holds.json / segments.json (the subsets the app uses)

@Serializable
data class HoldStop(
    @SerialName("stop_id") val stopId: String = "",
    val name: String = "",
    @SerialName("per_day") val perDay: Double = 0.0,
    @SerialName("median_sec") val medianSec: Double = 0.0,
)

@Serializable
data class HoldsSummary(val n: Int = 0, @SerialName("by_stop") val byStop: List<HoldStop> = emptyList())

@Serializable
data class SegmentStat(
    @SerialName("from_name") val fromName: String = "",
    @SerialName("to_name") val toName: String = "",
    val n: Int = 0,
    @SerialName("median_run_sec") val medianRunSec: Double = 0.0,
    @SerialName("sched_run_sec") val schedRunSec: Double? = null,
    val ratio: Double? = null,
    @SerialName("dist_m") val distM: Double? = null,
    @SerialName("speed_kmh") val speedKmh: Double? = null,
    @SerialName("sched_speed_kmh") val schedSpeedKmh: Double? = null,
    @SerialName("by_hour_run_sec") val byHourRunSec: List<Double?> = emptyList(),
)

@Serializable
data class SegmentsSummary(val n: Int = 0, @SerialName("by_key") val byKey: Map<String, SegmentStat> = emptyMap())

// lines/<key>.json deviation grid (typical time lost per stop by hour)

@Serializable
data class DeviationStop(@SerialName("stop_id") val stopId: String = "", val name: String = "")

@Serializable
data class LineDeviation(
    val stops: List<DeviationStop> = emptyList(),
    val grid: List<List<Double?>> = emptyList(),
    @SerialName("n_trips") val nTrips: Int = 0,
) {
    /** Mean lateness change (s) arriving at a stop at an hour of day, if known. */
    fun typical(stopId: String, hour: Int): Double? {
        val r = stops.indexOfFirst { it.stopId == stopId }
        if (r < 0 || r >= grid.size || hour < 0 || hour >= grid[r].size) return null
        return grid[r][hour]
    }
}

@Serializable
data class LineHistory(val deviation: LineDeviation? = null)

// client_geometry.json: [lat, lon] per canonical stop and the simplified track of each exported line

@Serializable
data class LineGeometry(val coords: List<List<Double>?> = emptyList(), val shape: List<List<Double>> = emptyList()) {
    fun coord(i: Int): LatLon? = coords.getOrNull(i)?.takeIf { it.size >= 2 }?.let { LatLon(it[0], it[1]) }
}

@Serializable
data class ClientGeometry(@SerialName("generated_at") val generatedAt: String? = null, val lines: Map<String, LineGeometry> = emptyMap())

data class LatLon(val lat: Double, val lon: Double)
