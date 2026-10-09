// Port of the itinerary half of ios/WhichWay/WhichWay/Core/Planner.swift (segmentTrips / legTrips / pathTrips /
// schedHeadwayAt / evaluate). The ride-in-progress functions (ridingItinerary, plannedCandidate,
// connectionItinerary) depend on the trip recorder and are not ported yet; see android/PORTING.md.
package com.whichway.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

data class TripCandidate(
    val train: LiveTrain,
    val key: String,
    val boardTs: Double,
    val arriveTs: Double,
    val rideSec: Double,
    val schedRideSec: Double?,
    val stopsToOrigin: Int,
    val arriveLoTs: Double? = null,
    val arriveHiTs: Double? = null,
    val feedArriveTs: Double? = null,
    val arriveSource: String? = null,
) {
    val id: String get() = train.id
    val rideVsSchedSec: Double? get() = schedRideSec?.let { rideSec - it }

    /** "10:49–10:54 · feed says 10:49" when the engine's window and the feed's own time are known. */
    val rangeText: String?
        get() {
            val lo = arriveLoTs ?: return null
            val hi = arriveHiTs ?: return null
            var s = "${Fmt.hhmm(lo)}–${Fmt.hhmm(hi)}"
            val f = feedArriveTs
            if (f != null && abs(f - arriveTs) >= 60) s += " · feed says ${Fmt.hhmm(f)}"
            return s
        }
}

data class Itinerary(
    val legs: List<TripCandidate>,
    val boardTs: Double,
    val arriveTs: Double,
    val totalSec: Double,
    val walkSec: Double,
    val waitAtTransferSec: Double?,
    val connectionMarginSec: Double?,
    val nextIfMissedSec: Double?,
    val schedRideSec: Double,
    val rideVsSchedSec: Double,
) {
    val id: String get() = legs.joinToString("+") { it.train.tripId }
}

/** Trains of a line board that carry a rider from fromIdx to toIdx, timed at the platform (PlatformTiming). */
fun segmentTrips(lb: LineBoard, line: LineTopology, fromIdx: Int, toIdx: Int, now: Double, maxN: Int = 6): List<TripCandidate> {
    val sched = line.runBetween(fromIdx, toIdx)?.toDouble()
    val out = ArrayList<TripCandidate>()
    for (t in lb.trains) {
        val boardF = t.points.firstOrNull { it.idx == fromIdx }?.ts ?: continue
        val arriveF = t.points.firstOrNull { it.idx == toIdx }?.ts ?: continue
        val board = PlatformTiming.atPlatform(boardF, t.route)
        val arrive = PlatformTiming.atPlatform(arriveF, t.route)
        // a train that has pulled away is gone: the countdown reaching zero keeps it for the dwell
        if (board + PlatformTiming.DWELL_SEC < now || arrive <= board) continue
        val pt = t.pred?.point(toIdx)
        out.add(TripCandidate(t, lb.key, board, arrive, arrive - board, sched, max(1, fromIdx - t.nextIdx + 1),
            arriveLoTs = pt?.let { PlatformTiming.atPlatform(it.loTs, t.route) },
            arriveHiTs = pt?.let { PlatformTiming.atPlatform(it.hiTs, t.route) },
            feedArriveTs = t.feedPoints?.firstOrNull { it.idx == toIdx }?.let { PlatformTiming.atPlatform(it.ts, t.route) },
            arriveSource = pt?.source))
    }
    return out.sortedBy { it.boardTs }.take(maxN)
}

/** Candidates for a merged leg (parallel routes), merged by boarding time. */
fun legTrips(boards: Map<String, LineBoard>, schedule: ClientSchedule, leg: PathLeg, now: Double, maxN: Int = 6): List<TripCandidate> {
    val out = ArrayList<TripCandidate>()
    for (k in leg.keys) {
        val lb = boards[k] ?: continue
        val line = schedule.lines[k] ?: continue
        val ix = leg.idx[k] ?: continue
        out.addAll(segmentTrips(lb, line, ix.from, ix.to, now, maxN))
    }
    return out.sortedBy { it.boardTs }.take(maxN)
}

/** Live itineraries for a path option, earliest arrival first. */
fun pathTrips(boards: Map<String, LineBoard>, schedule: ClientSchedule, option: PathOption, now: Double, maxN: Int = 5): List<Itinerary> {
    val sched = option.schedSec.toDouble()
    if (option.legs.size == 1) {
        return legTrips(boards, schedule, option.legs[0], now, maxN + 4).map { t ->
            Itinerary(listOf(t), t.boardTs, t.arriveTs, t.arriveTs - now, 0.0, null, null, null, sched, t.rideSec - sched)
        }.sortedBy { it.arriveTs }.take(maxN)
    }
    val transfer = option.transfer
    if (transfer == null || option.legs.size != 2) return emptyList()
    val walk = transfer.walkSec.toDouble()
    val firsts = legTrips(boards, schedule, option.legs[0], now, maxN + 2)
    val seconds = legTrips(boards, schedule, option.legs[1], now, 40)
    val out = ArrayList<Itinerary>()
    val seen = HashSet<String>()
    for (a in firsts) {
        val b = seconds.firstOrNull { it.boardTs >= a.arriveTs + walk } ?: continue
        if (b.train.tripId in seen) continue
        seen.add(b.train.tripId)
        val next = seconds.firstOrNull { it.boardTs > b.boardTs }
        out.add(Itinerary(listOf(a, b), a.boardTs, b.arriveTs, b.arriveTs - now, walk, b.boardTs - a.arriveTs, b.boardTs - a.arriveTs - walk,
            next?.let { it.boardTs - b.boardTs }, sched, (b.arriveTs - a.boardTs) - sched))
    }
    return out.sortedBy { it.arriveTs }.take(maxN)
}

/** Scheduled headway (s) of a leg's routes at a stop around `now`, from the per-line schedules. */
fun schedHeadwayAt(schedule: ClientSchedule, lineSched: Map<String, List<LineSchedEntry>>, keys: List<String>, stop: String, now: Double, windowSec: Double = 1800.0): Double? {
    var rate = 0.0
    for (k in keys) {
        val line = schedule.lines[k] ?: continue
        val i = line.stops.indexOf(stop)
        val entries = lineSched[k]
        if (i < 0 || entries.isNullOrEmpty()) continue
        var n = 0
        for (e in entries) {
            if (e.lastIdx < i) continue
            val run = line.runBetween(i, e.lastIdx) ?: continue
            if (abs((e.ts - run) - now) <= windowSec) n++
        }
        if (n >= 1) rate += n / (2 * windowSec)
    }
    return if (rate > 0) 1 / rate else null
}

/** Expected time: half the scheduled headway as each wait, scheduled rides, the typical time lost at this hour, and a hold risk. */
fun evaluate(option: PathOption, schedule: ClientSchedule, lineSched: Map<String, List<LineSchedEntry>>, now: Double,
             holds: HoldsSummary?, deviations: Map<String, LineDeviation>, hour: Int) {
    val hw1 = schedHeadwayAt(schedule, lineSched, option.legs[0].keys, option.legs[0].from, now)
    option.wait1Sec = hw1?.let { min(it / 2, 900.0) } ?: 300.0
    option.wait2Sec = if (option.legs.size > 1) {
        schedHeadwayAt(schedule, lineSched, option.legs[1].keys, option.legs[1].from, now)?.let { min(it / 2, 900.0) } ?: 300.0
    } else 0.0
    val holdMap = (holds?.byStop ?: emptyList()).associateBy { it.stopId }
    for (leg in option.legs) {
        val line = schedule.lines[leg.primaryKey] ?: continue
        val ix = leg.idx[leg.primaryKey] ?: continue
        var typical: Double? = null
        deviations[leg.primaryKey]?.let { dev ->
            var sum = 0.0
            var any = false
            for (s in ix.from + 1..ix.to) dev.typical(line.stops[s], hour)?.let { sum += it; any = true }
            typical = if (any) sum else null
        }
        val trips = max(60, (lineSched[leg.primaryKey] ?: emptyList()).size)
        var risk = 0.0
        for (s in ix.from + 1..ix.to) holdMap[line.stops[s]]?.let { risk += it.perDay * it.medianSec / trips }
        leg.typicalSec = typical
        leg.holdRiskSec = risk
    }
    option.expectedSec = option.wait1Sec + option.schedSec + option.typicalSec + option.holdRiskSec + option.wait2Sec
}
