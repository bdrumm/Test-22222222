// Port of ios/WhichWay/WhichWay/Core/LineInference.swift: which train did the rider actually board? The plan's
// line is the prior, the felt departure against each train's time at the stop is the first evidence, the stops
// felt and the walk-off corroborate it. Pure; runs in the core tests.
package com.whichway.core

import kotlinx.serialization.Serializable
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/** A train the rider could have boarded on one leg of the route. */
data class BoardingCandidate(
    val trainId: String,
    val key: String,
    val route: String,
    val boardTs: Double,
    val alightTs: Double? = null,
    val stopsToAlight: Int? = null,
    val chosen: Boolean = false,
    val onLeg: Boolean = true,
    val boardIdx: Int = 0,
    val alightIdx: Int? = null,
    val stoppedAtBoardTs: Double? = null,
    val stopTs: Map<Int, Double> = emptyMap(),
    val progressIdx: Int? = null,
    val samePlatform: Boolean = true,
    val atOrigin: Boolean = false,
)

/** The train and line a leg was ridden on, as kept in the trip's record. */
@Serializable
data class BoardedLeg(
    val leg: Int,
    val key: String,
    val trainId: String? = null,
    val chosenKey: String? = null,
    val confidence: Double,
    val verdict: LineBelief.Verdict,
    val evidence: List<String> = emptyList(),
)

/** What the phone believes about the train the rider boarded on a leg. */
@Serializable
data class LineBelief(
    val leg: Int,
    val byTrain: Map<String, Double>,
    val byLine: Map<String, Double>,
    var chosenKey: String? = null,
    val evidence: List<String> = emptyList(),
) {
    @Serializable enum class Verdict { onPlan, switched, unsure }

    companion object {
        const val NONE_KEY = "?"
        fun fromPlan(leg: Int, chosenKey: String?, chosenTrainId: String?, evidence: String): LineBelief? {
            val k = chosenKey ?: return null
            return LineBelief(leg, chosenTrainId?.let { mapOf(it to 1.0) } ?: emptyMap(), mapOf(k to 1.0), k, listOf(evidence))
        }
        fun byHand(leg: Int, key: String, chosenKey: String?, candidates: List<BoardingCandidate>, departedTs: Double?): LineBelief {
            val onLine = candidates.filter { it.key == key }
            val train = departedTs?.let { ts -> onLine.minByOrNull { abs(it.boardTs - ts) } } ?: onLine.firstOrNull()
            return LineBelief(leg, train?.let { mapOf(it.trainId to 1.0) } ?: emptyMap(), mapOf(key to 1.0), chosenKey, listOf("hand"))
        }
        private fun best(m: Map<String, Double>): String? = m.entries.maxWithOrNull(compareBy<Map.Entry<String, Double>> { it.value }.thenByDescending { it.key })?.key
    }

    val noneShare: Double get() = byLine[NONE_KEY] ?: 0.0
    private val lines: Map<String, Double> get() = byLine.filterKeys { it != NONE_KEY }
    val bestKey: String? get() = best(lines)
    val bestTrain: String? get() { val k = bestKey ?: return null; return best(byTrain.filterKeys { it.startsWith("$k|") }) }
    val confidence: Double get() = lines.values.maxOrNull() ?: 0.0
    val route: String? get() = bestKey?.substringBefore("_")
    val chosenRoute: String? get() = chosenKey?.substringBefore("_")

    /** The best line holds 60% and leads the runner-up, or "none of these", by 20 points. */
    val settled: Boolean
        get() {
            val sorted = lines.values.sortedDescending()
            val top = sorted.firstOrNull() ?: return false
            if (top < 0.6) return false
            val runnerUp = max(sorted.getOrElse(1) { 0.0 }, noneShare)
            return top - runnerUp >= 0.2
        }

    val verdict: Verdict get() { if (!settled) return Verdict.unsure; val b = bestKey ?: return Verdict.unsure; return if (b == chosenKey) Verdict.onPlan else Verdict.switched }

    val boarded: BoardedLeg? get() { val k = bestKey ?: return null; return BoardedLeg(leg, k, bestTrain, chosenKey, confidence, verdict, evidence) }

    val assumed: Boolean get() = "schedule" in evidence || "plan" in evidence
    val byHand: Boolean get() = "hand" in evidence
}

/** Remembers, poll by poll, the feed's time at the boarding stop for every train of the lines at the platform. */
class DepartureLog {
    data class Entry(
        val trainId: String, val key: String, val route: String, var boardTs: Double, var alightTs: Double? = null, var stops: Int? = null,
        val onLeg: Boolean, var lastSeenTs: Double, val boardIdx: Int = 0, var alightIdx: Int? = null, var stoppedAtBoardTs: Double? = null,
        val stopTs: MutableMap<Int, Double> = HashMap(), var progressIdx: Int? = null, val samePlatform: Boolean = true, val atOrigin: Boolean = false,
    )

    var entries: MutableMap<String, Entry> = HashMap(); private set
    var lastPollTs = 0.0; private set
    var keepSec = 3600.0

    /** One poll of one line's board. `span` is the leg's stop indices on this line (null for a line at the platform but not on the leg). */
    fun observe(trains: List<LiveTrain>, key: String, boardIdx: Int, span: Span?, onLeg: Boolean, now: Double, samePlatform: Boolean = true) {
        val route = key.substringBefore("_")
        lastPollTs = max(lastPollTs, now)
        for (t in trains) {
            val pts = t.feedPoints ?: t.points
            val atBoard = pts.firstOrNull { it.idx == boardIdx }?.ts
            val e = entries[t.id] ?: run {
                val b = atBoard ?: return@run null
                Entry(t.id, key, route, b, onLeg = onLeg, lastSeenTs = now, boardIdx = boardIdx, samePlatform = samePlatform, atOrigin = boardIdx == 0)
            } ?: continue
            if (atBoard != null) e.boardTs = atBoard
            e.lastSeenTs = now
            e.progressIdx = t.nextIdx
            for (pt in pts) if (pt.idx > boardIdx) e.stopTs[pt.idx] = pt.ts
            if (span != null) {
                e.alightTs = pts.firstOrNull { it.idx == span.to }?.ts ?: e.alightTs
                e.stops = span.to - span.from
                e.alightIdx = span.to
            }
            val pos = t.position
            if (pos != null && pos.status == "STOPPED_AT" && pos.stopIdx == boardIdx && !pos.atTerminal && boardIdx > 0) e.stoppedAtBoardTs = now
            entries[t.id] = e
        }
        entries = entries.filterValues { now - it.lastSeenTs <= keepSec }.toMutableMap()
    }

    private fun candidate(e: Entry, chosenTrainId: String?) = BoardingCandidate(e.trainId, e.key, e.route, e.boardTs, e.alightTs, e.stops, e.trainId == chosenTrainId, e.onLeg,
        e.boardIdx, e.alightIdx, e.stoppedAtBoardTs, HashMap(e.stopTs), e.progressIdx, e.samePlatform, e.atOrigin)

    private val order = compareBy<BoardingCandidate> { it.boardTs }.thenBy { it.trainId }

    /** The trains whose time at the boarding stop fell within `windowSec` of a departure felt at `ts`. */
    fun candidates(departedTs: Double, windowSec: Double, chosenTrainId: String?): List<BoardingCandidate> =
        entries.values.filter { abs(it.boardTs - departedTs) <= windowSec }.map { candidate(it, chosenTrainId) }.sortedWith(order)

    /** The same candidates with what the log has learned about them since. */
    fun refreshed(cands: List<BoardingCandidate>): List<BoardingCandidate> = cands.map { c -> entries[c.trainId]?.let { candidate(it, null).copy(chosen = c.chosen) } ?: c }

    fun progressIdx(of: String): Int? = entries[of]?.progressIdx

    fun entry(trainId: String, chosenTrainId: String? = null): BoardingCandidate? = entries[trainId]?.let { candidate(it, chosenTrainId) }

    /** A train the rider says they are on, taken up after it has left the platform. */
    fun seed(c: BoardingCandidate, now: Double) {
        entries[c.trainId]?.let { it.lastSeenTs = now; return }
        entries[c.trainId] = Entry(c.trainId, c.key, c.route, c.boardTs, c.alightTs, c.stopsToAlight, c.onLeg, now, c.boardIdx, c.alightIdx, c.stoppedAtBoardTs,
            HashMap(c.stopTs), c.progressIdx, c.samePlatform, c.atOrigin)
    }

    fun reset() { entries = HashMap(); lastPollTs = 0.0 }

    data class DepartureCheck(val left: Boolean, val waiting: Boolean)

    /** Did a train leave the boarding platform about when a pull-away was felt at `ts`? */
    fun departureCheck(at: Double, windowSec: Double = 120.0): DepartureCheck {
        var left = false; var waiting = false
        for (e in entries.values) {
            val passed = (e.progressIdx ?: e.boardIdx) > e.boardIdx
            if (passed && abs(PlatformTiming.pullsAway(e.boardTs, e.route) - at) <= windowSec) left = true
            if (!passed) waiting = true
        }
        return DepartureCheck(left, waiting)
    }
}

/** The estimator. Likelihoods are a Gaussian on a floor, so one odd reading never rules a train out for good. */
class LineInference {
    var priorChosenFirstLeg = 0.5
    var priorChosenLaterLeg = 0.7
    var otherLineAtPlatformWeight = 0.8
    var otherPlatformWeight = 0.2
    var priorNone = 0.1
    var noneLike = 0.2
    var dwellSec = PlatformTiming.DWELL_SEC
    var recordedLag: (String) -> Double = { PlatformTiming.recordedLag(it) }
    var departureSigmaSec = 40.0
    var terminalDepartureSigmaSec = 120.0
    var departureWindowSec = 300.0
    var alightLagSec = 20.0
    var alightSigmaSec = 75.0
    var stopMiscountFactor = 0.35
    var floor = 0.04
    var stoppedAtSigmaSec = 60.0
    var stoppedAtFloor = 0.3
    var rideSigmaSec = 90.0
    var stopLagSec = 0.0
    var locationSigmaM = 150.0

    fun priorChosen(leg: Int, learnedShare: Double?): Double {
        val base = if (leg == 0) priorChosenFirstLeg else priorChosenLaterLeg
        val s = learnedShare ?: return base
        return min(0.95, max(0.3, 0.5 * base + 0.5 * s))
    }

    fun prior(cands: List<BoardingCandidate>, leg: Int, chosenKey: String?, learnedShare: Double? = null): Map<String, Double> {
        if (cands.isEmpty()) return emptyMap()
        val pc = priorChosen(leg, learnedShare)
        val lineWeight = LinkedHashMap<String, Double>()
        for (c in cands) if (c.key != chosenKey) lineWeight[c.key] = if (c.onLeg) 1.0 else if (c.samePlatform) otherLineAtPlatformWeight else otherPlatformWeight
        val otherTotal = lineWeight.values.sum()
        val lineMass = HashMap<String, Double>()
        if (chosenKey != null && cands.any { it.key == chosenKey }) {
            lineMass[chosenKey] = if (otherTotal > 0) pc else 1.0
            for ((k, w) in lineWeight) lineMass[k] = (1 - pc) * w / max(otherTotal, 1.0)
        } else {
            for ((k, w) in lineWeight) lineMass[k] = if (otherTotal > 0) w / otherTotal else 0.0
        }
        val countByLine = cands.groupingBy { it.key }.eachCount()
        val out = HashMap<String, Double>()
        for (c in cands) out[c.trainId] = (1 - priorNone) * (lineMass[c.key] ?: 0.0) / (countByLine[c.key] ?: 1)
        out[LineBelief.NONE_KEY] = priorNone
        return out
    }

    private fun gaussian(delta: Double, sigma: Double) = floor + (1 - floor) * exp(-0.5 * (delta / sigma) * (delta / sigma))

    private fun belief(leg: Int, chosenKey: String?, weights: Map<String, Double>, cands: List<BoardingCandidate>, evidence: List<String>): LineBelief {
        val total = weights.values.sum()
        val byTrain = HashMap<String, Double>()
        val byLine = HashMap<String, Double>()
        if (total > 0) {
            for ((id, w) in weights) byTrain[id] = w / total
            for (c in cands) byLine[c.key] = (byLine[c.key] ?: 0.0) + (byTrain[c.trainId] ?: 0.0)
            byTrain[LineBelief.NONE_KEY]?.let { byLine[LineBelief.NONE_KEY] = it }
        }
        return LineBelief(leg, byTrain, byLine, chosenKey, evidence)
    }

    private fun none(b: LineBelief, informative: Boolean): MutableMap<String, Double> {
        val n = b.byTrain[LineBelief.NONE_KEY] ?: return HashMap()
        return hashMapOf(LineBelief.NONE_KEY to n * (if (informative) noneLike else 1.0))
    }

    /** The belief right after the sensors felt the train leave at `departedTs`. */
    fun fromDeparture(cands: List<BoardingCandidate>, departedTs: Double, leg: Int, chosenKey: String?, learnedShare: Double? = null): LineBelief {
        val p = prior(cands, leg, chosenKey, learnedShare)
        val positions = cands.any { it.stoppedAtBoardTs != null }
        val w = HashMap<String, Double>()
        for (c in cands) {
            val delta = departedTs - (c.boardTs - recordedLag(c.route) + dwellSec)
            val sigma = if (c.atOrigin) terminalDepartureSigmaSec else departureSigmaSec
            var like = gaussian(delta, sigma) * (departureSigmaSec / sigma)
            if (positions) {
                val s = c.stoppedAtBoardTs
                like *= if (s != null) stoppedAtFloor + (1 - stoppedAtFloor) * exp(-0.5 * (max(0.0, departedTs - s - 15) / stoppedAtSigmaSec).pow(2)) else stoppedAtFloor
            }
            w[c.trainId] = (p[c.trainId] ?: 0.0) * like
        }
        p[LineBelief.NONE_KEY]?.let { if (cands.isNotEmpty()) w[LineBelief.NONE_KEY] = it * noneLike * (if (positions) stoppedAtFloor else 1.0) }
        val evidence = ArrayList<String>()
        if (cands.isNotEmpty()) evidence.add("departure")
        if (positions) evidence.add("position")
        return belief(leg, chosenKey, w, cands, evidence)
    }

    /** The belief during the ride: the stops felt so far against each train's actual stop times. */
    fun withRide(b: LineBelief, candidates: List<BoardingCandidate>, stopTimes: List<Double>, now: Double): LineBelief {
        if (b.byTrain.isEmpty() || stopTimes.isEmpty() || candidates.none { it.stopTs.isNotEmpty() }) return b
        val w = none(b, informative = true)
        w[LineBelief.NONE_KEY]?.let { w[LineBelief.NONE_KEY] = it * noneLike.pow(max(0, stopTimes.size - 1).toDouble()) }
        for (c in candidates) {
            val lag = recordedLag(c.route)
            val expected = c.stopTs.filterKeys { it > c.boardIdx }.toSortedMap().values.map { it - lag + stopLagSec }
            if (expected.isEmpty()) { w[c.trainId] = b.byTrain[c.trainId] ?: 0.0; continue }
            val reached = expected.count { it <= now }
            var like = floor + (1 - floor) * stopMiscountFactor.pow(abs(stopTimes.size - reached).toDouble())
            for (felt in stopTimes) {
                val nearest = expected.minOf { abs(felt - it) }
                like *= gaussian(nearest, rideSigmaSec)
            }
            w[c.trainId] = (b.byTrain[c.trainId] ?: 0.0) * like
        }
        return belief(b.leg, b.chosenKey, w, candidates, b.evidence + "ride")
    }

    /** The belief after a location fix following the walk-off. */
    fun withLocation(b: LineBelief, candidates: List<BoardingCandidate>, distanceM: Map<String, Double>): LineBelief {
        if (b.byTrain.isEmpty() || distanceM.isEmpty()) return b
        val w = none(b, informative = true)
        for (c in candidates) {
            val like = distanceM[c.trainId]?.let { gaussian(it, locationSigmaM) } ?: 1.0
            w[c.trainId] = (b.byTrain[c.trainId] ?: 0.0) * like
        }
        return belief(b.leg, b.chosenKey, w, candidates, b.evidence + "location")
    }

    private fun atPlatform(c: BoardingCandidate, idx: Int): Double? = c.stopTs[idx]?.let { it - recordedLag(c.route) }

    /** The stop a rider on this train walked off at, at `ts`. */
    fun alightingStop(c: BoardingCandidate, at: Double): Int? {
        c.alightIdx?.let { return it }
        return c.stopTs.keys.filter { it > c.boardIdx }.minByOrNull { abs((atPlatform(c, it) ?: Double.POSITIVE_INFINITY) - at) }
    }

    /** The station stops this train made between the departure and the walk-off, when the leg's own count does not apply. */
    fun stopsMade(c: BoardingCandidate, departedTs: Double, alightedTs: Double): Int? {
        c.stopsToAlight?.let { return it }
        if (c.stopTs.isEmpty()) return null
        return c.stopTs.keys.filter { it > c.boardIdx }.mapNotNull { atPlatform(c, it) }.count { it > departedTs && it <= alightedTs + alightLagSec }
    }

    /** The belief after the ride: `stopsFelt` station stops were felt between the departure and walking off. */
    fun withStops(b: LineBelief, candidates: List<BoardingCandidate>, stopsFelt: Int, departedTs: Double? = null, alightedTs: Double? = null): LineBelief {
        if (b.byTrain.isEmpty()) return b
        fun expected(c: BoardingCandidate): Int? = if (departedTs != null && alightedTs != null) stopsMade(c, departedTs, alightedTs) else c.stopsToAlight
        if (candidates.none { expected(it) != null }) return b
        val w = none(b, informative = true)
        for (c in candidates) {
            val like = expected(c)?.let { floor + (1 - floor) * stopMiscountFactor.pow(abs(stopsFelt - it).toDouble()) } ?: 1.0
            w[c.trainId] = (b.byTrain[c.trainId] ?: 0.0) * like
        }
        return belief(b.leg, b.chosenKey, w, candidates, b.evidence + "stops")
    }

    /** The belief after the rider walked off at `alightedTs`, against each train's arrival at the alighting stop. */
    fun withAlighting(b: LineBelief, candidates: List<BoardingCandidate>, alightedTs: Double): LineBelief {
        if (b.byTrain.isEmpty() || candidates.none { it.alightTs != null || it.stopTs.isNotEmpty() }) return b
        val w = none(b, informative = true)
        for (c in candidates) {
            val arrival = c.alightTs ?: alightingStop(c, alightedTs)?.let { c.stopTs[it] }
            val like = arrival?.let { gaussian(alightedTs - (it - recordedLag(c.route) + alightLagSec), alightSigmaSec) } ?: floor
            w[c.trainId] = (b.byTrain[c.trainId] ?: 0.0) * like
        }
        return belief(b.leg, b.chosenKey, w, candidates, b.evidence + "alighting")
    }
}

/**
 * Trains that have already left a station in the direction of travel (ios/WhichWay/WhichWay/Core/OnTrain.swift):
 * the look-back for a rider who starts a route, or puts it right, from the train they are already on. Most recent first.
 */
fun trainsAhead(boards: Map<String, LineBoard>, schedule: ClientSchedule, boardIdx: Map<String, Int>, alightIdx: Map<String, Int>,
                onLegKeys: Set<String>, now: Double, maxAgoSec: Double = 1500.0): List<BoardingCandidate> {
    val out = ArrayList<BoardingCandidate>()
    for ((key, bi) in boardIdx) {
        val lb = boards[key] ?: continue
        val line = schedule.lines[key] ?: continue
        val ai = alightIdx[key]
        for (t in lb.trains) {
            if (t.nextIdx <= bi) continue
            if (ai != null && t.nextIdx > ai) continue
            val next = t.points.firstOrNull { it.idx >= t.nextIdx } ?: continue
            val run = line.runBetween(bi, next.idx) ?: continue
            val board = next.ts - run
            if (board < now - maxAgoSec || board > now + 60) continue
            val stopTs = HashMap<Int, Double>()
            for (p in t.points) if (p.idx > bi) stopTs[p.idx] = p.ts
            out.add(BoardingCandidate(t.id, key, t.route, board, ai?.let { stopTs[it] }, ai?.let { it - bi }, false, key in onLegKeys, bi, ai, null, stopTs, t.nextIdx))
        }
    }
    return out.sortedWith(compareByDescending<BoardingCandidate> { it.boardTs }.thenBy { it.trainId })
}
