// Port of ios/WhichWay/WhichWay/Core/LineBoard.swift (lineBoard() in site/rt-client.js): every started train of
// one line right now, with lateness against the timetable extract, position fusion, holds and stalls.
package com.whichway.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

fun tripSuffix(id: String): String {
    val parts = id.split("_").filter { it.isNotEmpty() }
    return if (parts.size >= 3) parts.takeLast(2).joinToString("_") else id
}

/** The suffix without its path code: 020300_L..N01R -> 020300_L..N (some feeds publish ids without the code). */
fun tripStem(id: String): String {
    val s = tripSuffix(id)
    val dots = s.indexOf("..")
    if (dots < 0) return s
    val first = s.getOrNull(dots + 2) ?: return s
    if (first != 'N' && first != 'S') return s
    return s.substring(0, dots + 2) + first
}

data class TrainPoint(val idx: Int, val ts: Double)

data class TrainPosition(
    val status: String,
    val stopId: String,
    val stopIdx: Int?,
    val stopName: String,
    val sinceSec: Double,
    val holding: Boolean,
    val stalled: Boolean,
    val atTerminal: Boolean,
    val expectedRunSec: Double?,
    val positionLatenessSec: Double?,
) {
    val text: String
        get() {
            val verb = when (status) { "STOPPED_AT" -> "at"; "INCOMING_AT" -> "arriving"; else -> "→" }
            return "$verb $stopName" + if (sinceSec >= 60) " · ${(sinceSec / 60).toInt()} min" else ""
        }
}

data class SegmentInfo(val fromIdx: Int, val toIdx: Int, val distM: Double, val schedRunSec: Double?, val schedSpeedKmh: Double?, val elapsedSec: Double, val coveredM: Double?)

data class LastRun(val fromStop: String, val toStop: String, val runSec: Double, val distM: Double?, val speedKmh: Double?, val schedSpeedKmh: Double?, val assumedFrom: Boolean)

data class LiveTrain(
    val key: String,
    val tripId: String,
    val trainId: String?,
    val route: String,
    val points: List<TrainPoint>,
    val nextIdx: Int,
    val nextName: String,
    val etaTs: Double,
    val schedTs: Double?,
    val schedMethod: String?,
    val latenessSec: Double?,
    val effectiveLatenessSec: Double?,
    val position: TrainPosition?,
    val corroboration: String,
    val trackChanged: Boolean,
    val started: Boolean,
    val segment: SegmentInfo?,
    val lastRun: LastRun?,
    /** Set on the predicted boards: the feed's own points and the engine's projection for the chosen scenario. */
    val feedPoints: List<TrainPoint>? = null,
    val pred: PredictedTrain? = null,
) {
    val id: String get() = "$key|$tripId"
    val label: String get() = (trainId ?: tripId).trim()
    val isHeld: Boolean get() = position?.holding == true || position?.stalled == true
}

data class LineBoard(
    val key: String,
    val route: String,
    val direction: String,
    val now: Double,
    val trains: List<LiveTrain>,
    val nHolding: Int,
    val nStalled: Int,
    val nFeedOptimistic: Int,
)

/** Observations across polls: the feed timestamp is when a state began, so "in transit to X" then "stopped at X" times the segment exactly. */
class VehicleHistory {
    private data class Obs(val status: String, val stopId: String, val ts: Double, val lastStopped: String?, val lastRun: LastRun?)
    private val obs = HashMap<String, Obs>()

    fun reset() = obs.clear()

    fun observe(key: String, v: RTVehicle, now: Double, line: LineTopology): LastRun? {
        val stop = v.stopId ?: return null
        val ts = v.timestamp ?: return null
        if (ts > now + 60) return null
        val status = v.status ?: "IN_TRANSIT_TO"
        val prev = obs[key]
        var lastRun = prev?.lastRun
        var lastStopped = prev?.lastStopped
        if (prev != null && status == "STOPPED_AT" && prev.status != "STOPPED_AT" && prev.stopId == stop && ts > prev.ts) {
            var from: String? = if (prev.lastStopped != null && prev.lastStopped != stop) prev.lastStopped else null
            var assumed = false
            if (from == null) {
                val j = line.stops.indexOf(stop)
                if (j > 0) { from = line.stops[j - 1]; assumed = true }
            }
            if (from != null) {
                var lr = LastRun(from, stop, ts - prev.ts, null, null, null, assumed)
                val a = line.stops.indexOf(from)
                val b = line.stops.indexOf(stop)
                val d = line.distM.getOrNull(a)
                if (a >= 0 && b == a + 1 && d != null) {
                    val r = line.runSec.getOrNull(a)
                    lr = lr.copy(distM = d.toDouble(), speedKmh = d / lr.runSec * 3.6, schedSpeedKmh = if (r != null && r > 0) d.toDouble() / r * 3.6 else null)
                }
                lastRun = lr
            }
        }
        if (status == "STOPPED_AT") lastStopped = stop
        else if (prev != null && prev.status == "STOPPED_AT" && prev.stopId != stop) lastStopped = prev.stopId
        obs[key] = Obs(status, stop, ts, lastStopped, lastRun)
        return lastRun
    }
}

/** Build the board for one line key ("6_N") from the parsed feeds. */
fun lineBoard(schedule: ClientSchedule, lineSched: List<LineSchedEntry>, feeds: Map<String, RTFeed>, key: String, now: Double, history: VehicleHistory): LineBoard? {
    val line = schedule.lines[key] ?: return null
    val parts = key.split("_")
    if (parts.size != 2) return null
    val route = parts[0]
    val direction = parts[1]
    val c = schedule.constants
    val idx = HashMap<String, Int>()
    line.stops.forEachIndexed { i, s -> idx[s] = i }
    val vehicles = HashMap<String, RTVehicle>()
    for (fd in feeds.values) for (v in fd.vehicles) if (v.trip.tripId.isNotEmpty()) vehicles[v.trip.key] = v
    val trains = ArrayList<LiveTrain>()
    for (fd in feeds.values) for (tu in fd.trips) {
        val first = tu.stops.firstOrNull() ?: continue
        if (tu.trip.routeId != route || !first.stopId.endsWith(direction)) continue
        val points = tu.stops.mapNotNull { s -> val i = idx[s.stopId]; val t = s.eta; if (i != null && t != null) TrainPoint(i, t) else null }
        val firstPoint = points.firstOrNull() ?: continue
        val veh = vehicles[tu.trip.key]
        val hasPos = veh != null && veh.stopId != null && veh.timestamp != null && veh.timestamp <= now + 60
        val lastRun = if (hasPos) history.observe(tu.trip.key, veh, now, line) else null
        val started = hasPos || tu.trip.isAssigned == true
        if (!started) continue
        val j = firstPoint.idx
        val eta = firstPoint.ts
        // schedule at the next stop: this trip's time at its last canonical stop minus the canonical running time,
        // by stem, else the nearest scheduled trip within 15 min
        var sched: Double? = null
        var schedMethod: String? = null
        val stem = tripStem(tu.trip.tripId)
        var bestStem: Pair<Int, Double>? = null
        var near: Double? = null
        for (e in lineSched) {
            if (e.lastIdx < j || abs(e.ts - eta) > 4 * 3600) continue
            if (e.stem == stem) {
                if (bestStem == null || abs(e.ts - eta) < abs(bestStem.second - eta)) bestStem = Pair(e.lastIdx, e.ts)
                continue
            }
            val run = line.runBetween(j, e.lastIdx)
            if (run != null) {
                val at = e.ts - run
                if (abs(at - eta) <= 900 && (near == null || abs(at - eta) < abs(near - eta))) near = at
            }
        }
        val bs = bestStem
        val stemRun = bs?.let { line.runBetween(j, it.first) }
        if (bs != null && stemRun != null) { sched = bs.second - stemRun; schedMethod = "trip_stem" }
        if (sched == null && near != null) { sched = near; schedMethod = "nearest" }
        val lateness = sched?.let { eta - it }
        var pos: TrainPosition? = null
        var corroboration = "position_unknown"
        var effective = lateness
        if (hasPos) {
            val v = veh
            val vstop = v.stopId
            val vts = v.timestamp
            val status = v.status ?: "IN_TRANSIT_TO"
            val pj = idx[vstop]
            val since = max(0.0, now - vts)
            var holding = false
            var stalled = false
            var expectedRun: Double? = null
            var plate: Double? = null
            val atTerminal = pj == 0 || (pj != null && pj == line.stops.size - 1)
            if (status == "STOPPED_AT") {
                holding = since >= c.holdSec && !atTerminal
            } else if (pj != null && pj > 0) {
                line.runSec.getOrNull(pj - 1)?.let { r -> expectedRun = r.toDouble(); stalled = since > r + c.stallSlackSec }
            }
            val s = sched
            if (s != null && pj != null && pj <= j) {
                val run = line.runBetween(pj, j)
                if (run != null) {
                    val remaining = if (status == "STOPPED_AT") 0.0 else (expectedRun?.let { max(0.0, it - since) } ?: 0.0)
                    plate = now + remaining - (s - run)
                }
            }
            pos = TrainPosition(status, vstop, pj, if (pj != null && pj < line.names.size) line.names[pj] else vstop, since, holding, stalled, atTerminal, expectedRun, plate)
            val pl = plate
            if (pl != null && lateness != null) {
                corroboration = if (pl - lateness > 60) "feed_optimistic" else "agree"
                effective = max(lateness, pl)
            }
        }
        var segment: SegmentInfo? = null
        val p = pos
        val pj = p?.stopIdx
        if (p != null && pj != null && p.status != "STOPPED_AT" && pj > 0) {
            val d = line.distM.getOrNull(pj - 1)
            if (d != null) {
                val run = line.runSec.getOrNull(pj - 1)?.toDouble()
                val frac = if (run != null && run > 0) min(0.96, p.sinceSec / run) else null
                segment = SegmentInfo(pj - 1, pj, d.toDouble(), run, run?.let { d / it * 3.6 }, p.sinceSec, frac?.let { d * it })
            }
        }
        val trackChanged = first.actualTrack != null && first.schedTrack != null && first.actualTrack != first.schedTrack
        trains.add(LiveTrain(key, tu.trip.tripId, tu.trip.trainId, route, points, j, if (j < line.names.size) line.names[j] else line.stops[j], eta, sched, schedMethod,
            lateness, effective, pos, corroboration, trackChanged, started, segment, lastRun))
    }
    trains.sortWith(compareByDescending<LiveTrain> { it.nextIdx }.thenBy { it.etaTs })
    return LineBoard(key, route, direction, now, trains, trains.count { it.position?.holding == true }, trains.count { it.position?.stalled == true },
        trains.count { it.corroboration == "feed_optimistic" })
}

/** Progress of a train along its line as a fractional stop index, dead-reckoned `age` seconds after the board. */
data class TrainProgress(val idx: Double, val state: String, val since: Double?)   // state: moving | stopped | holding | stalled | terminal | unknown

fun trainProgress(t: LiveTrain, age: Double, line: LineTopology): TrainProgress {
    val p = t.position
    val j = p?.stopIdx
    if (p == null || j == null) return TrainProgress(max(0.0, t.nextIdx - 0.5), "unknown", null)
    val since = p.sinceSec + max(0.0, age)
    if (p.status == "STOPPED_AT") return TrainProgress(j.toDouble(), if (p.holding) "holding" else if (p.atTerminal) "terminal" else "stopped", since)
    if (j <= 0) return TrainProgress(0.0, "moving", since)
    val run = line.runSec.getOrNull(j - 1)?.toDouble()
    var frac = if (run != null && run > 0) min(0.96, since / run) else 0.5
    if (p.status == "INCOMING_AT") frac = max(frac, 0.85)
    return TrainProgress((j - 1) + frac, if (p.stalled) "stalled" else "moving", since)
}
