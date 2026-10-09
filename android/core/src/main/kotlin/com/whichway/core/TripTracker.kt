// Port of ios/WhichWay/WhichWay/Core/BoardingDetector.swift and TripTracker.swift: one summarised second of
// motion, the detector that finds the pull-away and the walk-off, the trip's record, and the tracker that follows
// a route in progress from fixes, motion and the clock.
package com.whichway.core

import kotlinx.serialization.Serializable
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/** One second of the phone's motion, summarised on the phone. Raw sensor samples never leave the sampler. */
data class MotionSecond(val ts: Double, val stepEnergy: Double, val pushG: Double, val shakeG: Double)

enum class MotionState { unknown, walking, still, riding }

@Serializable
data class MotionEvent(val kind: Kind, val ts: Double) {
    @Serializable enum class Kind { departed, alighted }
}

/** Finds the moment a rider's train pulls away and the moment they walk off it, from one MotionSecond per second. */
class BoardingDetector {
    var stepThreshold = 0.012
    var pushThreshold = 0.05
    var shakeThreshold = 0.02
    var pushSeconds = 3
    var shakeSeconds = 8
    var alightWalkSeconds = 8
    var minRideSeconds = 20
    var stoppedBeforeWalkSeconds = 3
    var longWalkSeconds = 30

    var state: MotionState = MotionState.unknown; private set
    var seconds = 0; private set
    private var walkRun = 0; private var pushRun = 0; private var shakeRun = 0; private var walkAfterRide = 0; private var stoppedRun = 0
    private var walkFromStop = false
    private var rideStart: Double? = null

    fun feed(m: MotionSecond): MotionEvent? {
        seconds += 1
        val walking = m.stepEnergy > stepThreshold
        if (walking) {
            if (walkRun == 0) walkFromStop = stoppedRun >= stoppedBeforeWalkSeconds
            walkRun += 1; pushRun = 0; shakeRun = 0; stoppedRun = 0
            if (state == MotionState.riding) {
                walkAfterRide += 1
                val start = rideStart
                if (walkAfterRide >= alightWalkSeconds && (walkFromStop || walkAfterRide >= longWalkSeconds) && start != null && m.ts - start >= minRideSeconds) {
                    state = MotionState.walking; rideStart = null; walkAfterRide = 0
                    return MotionEvent(MotionEvent.Kind.alighted, m.ts - (alightWalkSeconds - 1))
                }
                return null
            }
            if (walkRun >= 2) state = MotionState.walking
            return null
        }
        walkRun = 0; walkAfterRide = 0
        pushRun = if (m.pushG >= pushThreshold) pushRun + 1 else 0
        shakeRun = if (m.shakeG >= shakeThreshold) shakeRun + 1 else 0
        stoppedRun = if (m.shakeG < shakeThreshold && m.pushG < pushThreshold) stoppedRun + 1 else 0
        if (state == MotionState.riding) return null
        if (pushRun >= pushSeconds || shakeRun >= shakeSeconds) {
            val run = if (pushRun >= pushSeconds) pushRun else shakeRun
            val start = m.ts - (run - 1)
            state = MotionState.riding; rideStart = start
            return MotionEvent(MotionEvent.Kind.departed, start)
        }
        state = MotionState.still
        return null
    }

    /** The rider says they are on a train already: the ride counts from `ts`. */
    fun assumeRiding(since: Double) { state = MotionState.riding; rideStart = since; walkAfterRide = 0; pushRun = 0; shakeRun = 0; stoppedRun = 0; walkFromStop = false }

    /** The ride is over without a walk: the next sustained push or vibration is a new departure. */
    fun standDown() { state = MotionState.still; rideStart = null; walkAfterRide = 0; pushRun = 0; shakeRun = 0; stoppedRun = 0; walkFromStop = false }
}

enum class TripPhase { approaching, atStation, riding, arrived }

/** What one trip looked like, as the phone saw it. Feeds the personal model and, if shared, the telemetry. */
@Serializable
data class TripTimeline(
    val startTs: Double,
    val startedBy: String,                 // "gps" | "hand" | "onboard"
    val startDistanceM: Double? = null,
    val placeId: String? = null,
    val originStation: String,
    val destStation: String,
    var transferStation: String? = null,
    var legs: Int,
    var arrivedStationTs: Double? = null,
    var platformTs: Double? = null,
    var platformObserved: Boolean = false,
    var walkMeters: Double = 0.0,
    var walkSeconds: Double = 0.0,
    val events: MutableList<MotionEvent> = ArrayList(),
    var motionSeconds: Int = 0,
    var walkingSecondsBetweenTrains: Double = 0.0,
    var rideAssumed: Boolean = false,
    var forecastBoardTs: Double? = null,
    var forecastArriveTs: Double? = null,
    var liveArriveTs: Double? = null,
    var endedTs: Double? = null,
    var endedBy: String? = null,
    val rideStops: MutableList<Int> = ArrayList(),
    val boarded: MutableList<BoardedLeg> = ArrayList(),
    var withdrawnDepartures: MutableList<Double>? = null,
) {
    /** Legs ridden so far: the number of alightings the sensors saw. */
    val legsRidden: Int get() = events.count { it.kind == MotionEvent.Kind.alighted }

    val accessSec: Double?
        get() { if (!platformObserved) return null; val a = arrivedStationTs ?: return null; val p = platformTs ?: return null; return if (p >= a) p - a else null }

    /** Seconds walking between leaving the first train and the second one leaving, on a two-leg trip. */
    val transferWalkSec: Double?
        get() {
            if (legs <= 1) return null
            val off = events.indexOfFirst { it.kind == MotionEvent.Kind.alighted }
            if (off < 0 || events.drop(off + 1).none { it.kind == MotionEvent.Kind.departed }) return null
            return walkingSecondsBetweenTrains
        }

    /** The first departure within five minutes of the forecast boarding time. */
    val corroboratedDepartureTs: Double?
        get() { val b = forecastBoardTs ?: return null; return events.firstOrNull { it.kind == MotionEvent.Kind.departed && abs(it.ts - b) <= 300 }?.ts }

    val walkSpeedMPerMin: Double? get() = if (walkMeters >= 150 && walkSeconds >= 60) walkMeters / walkSeconds * 60 else null
}

/** Follows a route in progress from location fixes, summarised motion and the clock, and decides when it is over. */
class TripTracker(start: TripTimeline, distanceToOriginM: Double?) {
    var stationRadiusM = 150.0
    var destRadiusM = 250.0
    var platformStillSec = 20
    var walkOffSec = 180
    var lateGraceSec = 600.0
    var assumeRideAfterSec = 120.0
    var stopQuietSec = 12
    var startGraceSec = 15.0
    var destFarRadiusM = 800.0
    var awaySpeedMps = 4.0
    var awaySeconds = 30.0
    var usableAccuracyM = 100.0
    var preciseAccuracyM = 65.0

    var phase: TripPhase; private set
    val timeline: TripTimeline = start
    var motionState: MotionState = MotionState.unknown; private set
    private val detector = BoardingDetector()
    private var stillRun = 0
    private var walkRun = 0
    private var afterAlight = false
    private var alightedAll = false
    private class Fix(val ts: Double, val toOrigin: Double, val acc: Double)
    private var lastFix: Fix? = null
    private var awayRun = 0.0
    private class DestFix(val ts: Double, val toDest: Double)
    private var lastDest: DestFix? = null
    private var hasMotion = false
    private var firstMotionTs: Double? = null
    private var quietRun = 0
    /** Station stops felt on the ride in progress. */
    var stopsFelt = 0; private set
    /** When each station stop of the ride in progress began (the stop in hand included). */
    var stopTimes: List<Double> = emptyList(); private set
    /** The stop times of the ride that just ended, kept until the next departure. */
    var lastRideStopTimes: List<Double> = emptyList(); private set
    private var quietStartTs: Double? = null
    private var walkStartTs: Double? = null
    /** The ride began from the phone's movement, not a felt pull-away. */
    var departedByLocation = false; private set
    /** The last second the phone felt the train moving on the ride in hand. */
    var lastMovingTs: Double? = null; private set

    init {
        if (distanceToOriginM != null && distanceToOriginM <= stationRadiusM) {
            phase = TripPhase.atStation
            timeline.arrivedStationTs = start.startTs
        } else phase = TripPhase.approaching
    }

    /** Riding and standing at a station since this moment (null while moving). */
    val standingSince: Double? get() = if (phase == TripPhase.riding && quietRun >= stopQuietSec) quietStartTs else null

    /** On a train right now; false between trains at the change. */
    val onTrain: Boolean get() = phase == TripPhase.riding && (timeline.rideAssumed || timeline.events.lastOrNull()?.kind == MotionEvent.Kind.departed)

    data class Walking(val startTs: Double, val seconds: Int)
    /** Riding and walking right now: when the steps began and for how long. */
    val walking: Walking? get() { if (phase != TripPhase.riding || walkRun <= 0) return null; val s = walkStartTs ?: return null; return Walking(s, walkRun) }

    private fun resetRide() { stopsFelt = 0; quietRun = 0; stopTimes = emptyList(); quietStartTs = null }

    fun location(ts: Double, toOriginM: Double?, toDestM: Double?, now: Double, accuracyM: Double? = null) {
        if (phase == TripPhase.arrived) return
        val acc = accuracyM?.let { if (it < 0) Double.POSITIVE_INFINITY else it } ?: 0.0
        if (acc > usableAccuracyM) { checkDestination(now); return }
        if (phase == TripPhase.approaching && toOriginM != null) {
            val d = toOriginM
            val last = lastFix
            if (last != null && ts > last.ts && max(acc, last.acc) <= preciseAccuracyM) {
                val dt = ts - last.ts
                val gained = last.toOrigin - d
                if (dt >= 5 && dt <= 120 && gained > 0 && gained / dt >= 0.4 && gained / dt <= 2.5) {
                    timeline.walkMeters += gained
                    timeline.walkSeconds += dt
                }
                if (dt >= 5 && dt <= 180 && d > stationRadiusM * 2 && -gained / dt >= awaySpeedMps) {
                    awayRun += dt
                    if (awayRun >= awaySeconds) {
                        lastFix = Fix(ts, d, acc)
                        beginRiding(now, ts - awayRun)
                        departedByLocation = true
                        checkDestination(now)
                        return
                    }
                } else awayRun = 0.0
            }
            lastFix = Fix(ts, d, acc)
            if (d <= stationRadiusM + acc / 2) {
                phase = TripPhase.atStation
                timeline.arrivedStationTs = ts
                stillRun = 0
            }
        }
        if (toDestM != null) lastDest = DestFix(ts, max(0.0, toDestM - acc / 2))
        checkDestination(now)
    }

    private fun checkDestination(now: Double) {
        if (phase == TripPhase.arrived) return
        val d = lastDest ?: return
        if (now - d.ts > 600) return
        val pa = timeline.liveArriveTs
        if (d.toDest <= destRadiusM) {
            if (pa != null) { if (now >= pa - 300) finish(now, "arrived") }
            else if (phase == TripPhase.riding) finish(now, "arrived")
        } else if (d.toDest <= destFarRadiusM && pa != null && now >= pa + 120 && (phase == TripPhase.riding || phase == TripPhase.atStation)) {
            finish(now, "arrived")
        }
    }

    /** One summarised second of motion. */
    fun motion(m: MotionSecond) {
        if (phase == TripPhase.arrived) return
        hasMotion = true
        if (firstMotionTs == null) firstMotionTs = m.ts
        timeline.motionSeconds += 1
        val walking = m.stepEnergy > detector.stepThreshold
        if (walking) {
            if (walkRun == 0) walkStartTs = m.ts
            walkRun += 1; stillRun = 0
        } else {
            stillRun += 1; walkRun = 0; walkStartTs = null
        }
        if (afterAlight && walking) timeline.walkingSecondsBetweenTrains += 1
        if (phase == TripPhase.riding && !walking && (m.pushG >= detector.pushThreshold || m.shakeG >= detector.shakeThreshold)) lastMovingTs = m.ts
        if (phase == TripPhase.atStation && timeline.platformTs == null && stillRun >= platformStillSec) {
            timeline.platformTs = m.ts - (platformStillSec - 1)
            timeline.platformObserved = true
        }
        val riding = detector.state == MotionState.riding
        val e = detector.feed(m)
        if (e != null) {
            val f = firstMotionTs
            if (e.kind == MotionEvent.Kind.departed && f != null && m.ts - f < startGraceSec) {
                detector.standDown(); motionState = detector.state; return
            }
            val lf = lastFix
            if (e.kind == MotionEvent.Kind.departed && phase == TripPhase.approaching && lf != null && m.ts - lf.ts < 120 && lf.toOrigin - lf.acc > 300) {
                detector.standDown(); motionState = detector.state; return
            }
            timeline.events.add(e)
            when (e.kind) {
                MotionEvent.Kind.departed -> {
                    afterAlight = false
                    departedByLocation = false
                    resetRide()
                    timeline.rideAssumed = false
                    if (phase != TripPhase.riding) {
                        if (timeline.arrivedStationTs == null) timeline.arrivedStationTs = e.ts
                        if (timeline.platformTs == null) timeline.platformTs = e.ts
                        phase = TripPhase.riding
                    }
                }
                MotionEvent.Kind.alighted -> {
                    afterAlight = true
                    if (quietRun >= stopQuietSec) stopsFelt += 1
                    timeline.rideStops.add(stopsFelt)
                    lastRideStopTimes = stopTimes
                    resetRide()
                    timeline.walkingSecondsBetweenTrains += walkRun
                    if (timeline.legsRidden >= timeline.legs) alightedAll = true
                }
            }
        } else if (riding) {
            val quiet = !walking && m.pushG < detector.pushThreshold && m.shakeG < detector.shakeThreshold
            if (quiet) {
                quietRun += 1
                if (quietRun == stopQuietSec) {
                    val start = m.ts - (stopQuietSec - 1)
                    quietStartTs = start
                    stopTimes = stopTimes + start
                }
            } else {
                if (quietRun >= stopQuietSec) stopsFelt += 1
                quietRun = 0; quietStartTs = null
            }
        }
        motionState = detector.state
        val pa = timeline.liveArriveTs
        if (phase == TripPhase.riding && walkRun >= walkOffSec && pa != null && m.ts >= pa - 180) finish(m.ts, "walked")
    }

    /** The clock: confirms an arrival, assumes the ride from the schedule, and gives up well past the arrival. */
    fun tick(now: Double) {
        if (phase == TripPhase.arrived) return
        val pa = timeline.liveArriveTs
        if (alightedAll && (pa == null || now >= pa - 120)) { finish(now, "alighted"); return }
        val pb = timeline.forecastBoardTs
        if (phase == TripPhase.atStation && pb != null && now >= pb + assumeRideAfterSec && (!hasMotion || timeline.platformTs != null)) {
            phase = TripPhase.riding
            timeline.rideAssumed = true
            if (timeline.platformTs == null) timeline.platformTs = pb
        }
        checkDestination(now)
        if (phase != TripPhase.arrived && pa != null && now >= pa + lateGraceSec) finish(now, "timeout")
    }

    fun updateForecast(boardTs: Double?, arriveTs: Double?, now: Double) {
        if (phase == TripPhase.riding || phase == TripPhase.arrived) return
        val b = timeline.forecastBoardTs
        if (phase == TripPhase.atStation && b != null && now >= b) return
        timeline.liveArriveTs = arriveTs ?: timeline.liveArriveTs
        timeline.forecastBoardTs = boardTs
        timeline.forecastArriveTs = arriveTs
    }

    /**
     * The train the forecast named has gone from the feed a little before its predicted platform moment (the feed
     * moves a train on as it leaves, and its ETA for the stop was a few seconds optimistic): at the station, that
     * is the train leaving now, so the forecast freezes here and the assumed ride is counted from this moment.
     * Android-only; the Swift tracker waits for the predicted time itself to pass. `graceSec` bounds how early.
     */
    fun forecastTrainDeparted(now: Double, graceSec: Double = 90.0) {
        if (phase != TripPhase.atStation) return
        val b = forecastBoardTs ?: return
        if (b > now && b - now <= graceSec) timeline.forecastBoardTs = now
    }
    private val forecastBoardTs: Double? get() = timeline.forecastBoardTs

    private fun closeRide(ts: Double) {
        if (quietRun >= stopQuietSec) stopsFelt += 1
        timeline.rideStops.add(stopsFelt)
        lastRideStopTimes = stopTimes
        resetRide()
        afterAlight = true
        timeline.events.add(MotionEvent(MotionEvent.Kind.alighted, ts))
    }

    /** A same-platform change told by the feed: the ride closes as an alighting at `ts`. */
    fun alightStanding(at: Double) {
        if (phase != TripPhase.riding) return
        closeRide(at)
        detector.standDown()
        motionState = detector.state
        if (timeline.legsRidden >= timeline.legs) alightedAll = true
    }

    /** A walk the feed vouches for: the ride closes as an alighting at `ts`, the walk goes on as the change. */
    fun alightWalking(at: Double) {
        if (phase != TripPhase.riding) return
        closeRide(at)
        timeline.walkingSecondsBetweenTrains += walkRun
        detector.standDown()
        motionState = MotionState.walking
        if (timeline.legsRidden >= timeline.legs) alightedAll = true
    }

    /** The rider says they are not on a train: the felt departure is withdrawn. */
    fun notOnTrain() {
        if (phase != TripPhase.riding) return
        val last = timeline.events.lastOrNull() ?: return
        if (last.kind != MotionEvent.Kind.departed) return
        timeline.events.removeAt(timeline.events.size - 1)
        phase = TripPhase.atStation
        if (timeline.arrivedStationTs == null) timeline.arrivedStationTs = last.ts
        resetRide()
        timeline.rideAssumed = false
        detector.standDown()
        motionState = detector.state
        firstMotionTs = null
    }

    /** The rider is on a train already, having left the station at `departedTs`. */
    fun beginRiding(now: Double, departedTs: Double) {
        if (phase == TripPhase.arrived || phase == TripPhase.riding) return
        val dep = min(departedTs, now)
        if (timeline.arrivedStationTs == null) timeline.arrivedStationTs = dep
        if (timeline.platformTs == null) timeline.platformTs = dep
        timeline.events.add(MotionEvent(MotionEvent.Kind.departed, dep))
        timeline.rideAssumed = false
        phase = TripPhase.riding
        afterAlight = false
        resetRide()
        detector.assumeRiding(dep)
        motionState = detector.state
        if (firstMotionTs == null) firstMotionTs = now - startGraceSec
    }

    /** The route changed under the rider: the legs still to ride and the change follow the new route. */
    fun replan(legs: Int, transferStation: String?) {
        if (phase == TripPhase.arrived) return
        timeline.legs = max(1, legs)
        timeline.transferStation = transferStation
        alightedAll = timeline.legsRidden >= timeline.legs
    }

    fun setLiveArrival(ts: Double) { if (phase != TripPhase.arrived) timeline.liveArriveTs = ts }

    /** A pull-away the feed says no train made: withdrawn, the record keeps it. */
    fun withdrawDeparture() {
        if (phase != TripPhase.riding) return
        val last = timeline.events.lastOrNull() ?: return
        if (last.kind != MotionEvent.Kind.departed) return
        notOnTrain()
        timeline.withdrawnDepartures = ((timeline.withdrawnDepartures ?: ArrayList()) + last.ts).toMutableList()
    }

    fun setBoarded(b: BoardedLeg) {
        val i = timeline.boarded.indexOfFirst { it.leg == b.leg }
        if (i >= 0) timeline.boarded[i] = b else timeline.boarded.add(b)
    }

    fun end(now: Double, by: String): TripTimeline { if (phase != TripPhase.arrived) finish(now, by); return timeline }

    private fun finish(ts: Double, by: String) { phase = TripPhase.arrived; timeline.endedTs = ts; timeline.endedBy = by }
}
