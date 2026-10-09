package com.whichway.core

/** Shared helpers for the ride tests: the fixtures the Swift tests build by hand. */
object Ride {
    fun sec(ts: Double, walking: Boolean, push: Double = 0.005, shake: Double = 0.005) = MotionSecond(ts, if (walking) 0.05 else 0.002, push, shake)

    fun train(id: String, key: String, points: List<Pair<Int, Double>>, at: Int? = null, terminal: Boolean = false): LiveTrain {
        val pos = at?.let { TrainPosition("STOPPED_AT", "s$it", it, "", if (terminal) 240.0 else 10.0, false, false, terminal, null, null) }
        return LiveTrain(key, id, null, key.substringBefore("_"), points.map { TrainPoint(it.first, it.second) }, points.firstOrNull()?.first ?: 0, "",
            points.firstOrNull()?.second ?: 0.0, null, null, null, null, pos, "position_unknown", false, true, null, null)
    }

    fun board(key: String, trains: List<LiveTrain>) = LineBoard(key, key.substringBefore("_"), "N", 0.0, trains, 0, 0, 0)

    fun start(ts: Double = 1000.0, legs: Int = 1, transfer: String? = null, distance: Double? = 900.0, by: String = "hand", place: String? = "home") =
        TripTimeline(startTs = ts, startedBy = by, startDistanceM = distance, placeId = place, originStation = "S1", destStation = "S9", transferStation = transfer, legs = legs)

    /** The inference as the older tests reason: in feed time, with the old dwell. */
    fun feedTimeInference() = LineInference().apply { recordedLag = { 0.0 }; dwellSec = 25.0; stopLagSec = 10.0 }

    fun cand(id: String, key: String, board: Double, alight: Double? = null, stops: Int? = null, chosen: Boolean = false, onLeg: Boolean = true) =
        BoardingCandidate("$key|$id", key, key.substringBefore("_"), board, alight, stops, chosen, onLeg)

    fun candIdx(id: String, key: String, board: Double, boardIdx: Int = 0, alightIdx: Int? = null, stoppedAt: Double? = null, stopTs: Map<Int, Double> = emptyMap(), chosen: Boolean = false) =
        BoardingCandidate("$key|$id", key, key.substringBefore("_"), board, alightIdx?.let { stopTs[it] }, alightIdx?.let { it - boardIdx }, chosen, true, boardIdx, alightIdx, stoppedAt, stopTs)
}

/** A tiny driver over a tracker: feeds n seconds of one kind of motion. */
class Driver(val t: TripTracker, var ts: Double = 1000.0) {
    fun feed(n: Int, walking: Boolean, push: Double = 0.005, shake: Double = 0.005) { repeat(n) { t.motion(Ride.sec(ts, walking, push, shake)); ts += 1 } }
    fun ride(n: Int) = feed(n, false, shake = 0.04)
    fun stand(n: Int) = feed(n, false)
    fun depart() = feed(6, false, push = 0.08, shake = 0.03)
    fun walk(n: Int) = feed(n, true)
}
