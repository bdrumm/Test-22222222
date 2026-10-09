// Port of the read side of ios/WhichWay/WhichWay/Core/PersonalModel.swift: what this phone has learned about
// its rider. `learn(TripTimeline)` comes with the trip tracker (android/PORTING.md).
package com.whichway.core

import kotlinx.serialization.Serializable
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/** A running estimate that leans toward recent trips. */
@Serializable
data class PaceStat(var mean: Double = 0.0, var n: Int = 0) {
    fun add(v: Double) {
        mean = if (n == 0) v else mean + (v - mean) * max(0.25, 1.0 / (n + 1))
        n += 1
    }
}

@Serializable
data class PersonalModel(
    val walkSpeed: PaceStat = PaceStat(),                              // metres per minute toward the station
    val access: MutableMap<String, PaceStat> = HashMap(),              // origin station id -> seconds from the radius to the platform
    val accessDefault: PaceStat = PaceStat(),
    val transfer: MutableMap<String, PaceStat> = HashMap(),            // transfer station name -> seconds walking between trains
    val transferDefault: PaceStat = PaceStat(),
    val placeToStation: MutableMap<String, PaceStat> = HashMap(),      // "placeId|stationId" -> seconds from leaving the place to the station
    var lineChoices: MutableMap<String, MutableMap<String, Int>>? = null,
    var trips: Int = 0,
    var lastLearnedTs: Double? = null,
) {
    companion object { const val DEFAULT_WALK_SPEED = 80.0; const val TRANSFER_FLOOR_SEC = 15.0 }

    val walkSpeedMPerMin: Double get() = if (walkSpeed.n > 0) min(130.0, max(45.0, walkSpeed.mean)) else DEFAULT_WALK_SPEED

    /** Seconds from the street to the platform at a station: its own figure, else the rider's average anywhere. */
    fun accessSec(station: String): Double? {
        access[station]?.takeIf { it.n > 0 }?.let { return it.mean }
        return if (accessDefault.n > 0) accessDefault.mean else null
    }

    fun transferSec(station: String): Double? {
        transfer[station]?.takeIf { it.n >= 2 }?.let { return it.mean }
        return if (transferDefault.n >= 3) transferDefault.mean else null
    }

    /** The walk the planner allows for a change at a station: the rider's own once known there, never under the floor. */
    fun plannedTransferSec(station: String, scheduled: Int): Int {
        val s = transfer[station]?.takeIf { it.n >= 2 } ?: return scheduled
        return max(TRANSFER_FLOOR_SEC, s.mean).roundToInt()
    }

    fun placeToStationSec(place: String, station: String): Double? = placeToStation["$place|$station"]?.takeIf { it.n > 0 }?.mean

    fun chosenLineShare(origin: String, dest: String, leg: Int, chosenKey: String): Double? {
        val m = lineChoices?.get("$origin|$dest|$leg") ?: return null
        val n = m.values.sum()
        if (n < 3) return null
        return (m[chosenKey] ?: 0).toDouble() / n
    }

    /** Minutes from a point this far away to standing on the platform at the station. */
    fun walkMinutes(meters: Double, station: String?): Int {
        val sec = meters / walkSpeedMPerMin * 60 + (station?.let { accessSec(it) } ?: 0.0)
        return max(1, (sec / 60).roundToInt())
    }

    /** Learns what a finished trip measured. Returns whether anything was learned. */
    fun learn(t: TripTimeline): Boolean {
        var any = false
        t.walkSpeedMPerMin?.let { walkSpeed.add(min(150.0, max(30.0, it))); any = true }
        val a = t.accessSec
        if (a != null && a >= 15 && a <= 900) {
            access.getOrPut(t.originStation) { PaceStat() }.add(a)
            accessDefault.add(a)
            any = true
        }
        // a change across the platform is a quarter of a minute of walking; anything shorter is a false alighting
        val x = t.transferStation
        val s = t.transferWalkSec
        if (x != null && s != null && s >= 8 && s <= 900) {
            transfer.getOrPut(x) { PaceStat() }.add(s)
            transferDefault.add(s)
            any = true
        }
        val pid = t.placeId
        val arr = t.arrivedStationTs
        if (pid != null && arr != null && arr - t.startTs >= 30 && arr - t.startTs <= 3600) {
            placeToStation.getOrPut("$pid|${t.originStation}") { PaceStat() }.add(arr - t.startTs)
            any = true
        }
        for (b in t.boarded) {
            if (b.verdict == LineBelief.Verdict.unsure) continue
            val m = lineChoices ?: HashMap<String, MutableMap<String, Int>>().also { lineChoices = it }
            val k = "${t.originStation}|${t.destStation}|${b.leg}"
            val line = m.getOrPut(k) { HashMap() }
            line[b.key] = (line[b.key] ?: 0) + 1
            any = true
        }
        if (any) { trips += 1; lastLearnedTs = t.endedTs }
        return any
    }

    /** "3.1 mph · 4 stations · 2 changes · 12 trips" */
    val summary: String
        get() {
            val bits = ArrayList<String>()
            if (walkSpeed.n > 0) bits.add(String.format(java.util.Locale.US, "%.1f mph", walkSpeedMPerMin * 60 / 1609.344))
            if (access.isNotEmpty()) bits.add("${access.size} station${if (access.size == 1) "" else "s"}")
            if (transfer.isNotEmpty()) bits.add("${transfer.size} change${if (transfer.size == 1) "" else "s"}")
            bits.add("$trips trip${if (trips == 1) "" else "s"}")
            return bits.joinToString(" · ")
        }
}
