// Port of ios/WhichWay/WhichWay/Services/TripRecorder.swift: runs a route in progress. The motion seconds, the
// location fixes and the clock go into the trip tracker; the feeds' boards into the departure log, so that when
// the sensors feel the train pull away the phone can tell which train that was. Pure: the sensors, the stores
// and the notification are the app's (app/…/trip/TripSession.kt).
package com.whichway.core

import kotlin.math.abs

/** One leg of the plan, as the line inference needs it. */
data class LegPlan(
    val keys: List<String>,
    val idx: Map<String, Span>,
    var chosenKey: String? = null,
    var chosenTrainId: String? = null,
    val platformKeys: MutableMap<String, Int> = HashMap(),
    val origin: String = "",
    val dest: String = "",
    val walkSec: Double = 0.0,
    val otherPlatformKeys: MutableSet<String> = HashSet(),
)

data class OffPlanAlighting(val leg: Int, val key: String, val stopIdx: Int, val ts: Double)
data class StayedOn(val leg: Int, val key: String, val trainId: String, val pastIdx: Int, val ts: Double)

data class BoardingPrompt(val leg: Int, val reason: Reason, val candidates: List<BoardingCandidate>, val bestTrainId: String?, val bestKey: String?, val chosenKey: String?) {
    enum class Reason { unsure, switched, assumed }
}

class TripRecorder {
    var phase: TripPhase? = null; private set
    var endReason: String? = null; private set
    var motionState: MotionState = MotionState.unknown; private set
    var motionSeconds = 0; private set
    var walkMeters = 0.0; private set
    var rideAssumed = false; private set
    var beliefs: List<LineBelief> = emptyList(); private set
    var offPlanAlighting: OffPlanAlighting? = null; private set
    var stayedOn: StayedOn? = null; private set
    private val stayedOnChecked = HashSet<Int>()
    var tracker: TripTracker? = null; private set
    private var legs: List<LegPlan> = emptyList()
    private var log = DepartureLog()
    private var transferLog = DepartureLog()
    private var logLeg = -1
    private val candidates = HashMap<Int, List<BoardingCandidate>>()
    private val departedTs = HashMap<Int, Double>()
    private val departureBeliefs = HashMap<Int, LineBelief>()
    private var lastAlight: Pair<Int, Double>? = null
    private val locationApplied = HashSet<Int>()
    val inference = LineInference()
    private var seenEvents = 0
    private val handSet = HashSet<Int>()
    private val dismissed = HashSet<Int>()
    private val departureChecked = HashSet<Int>()
    var departureCheckSec = 100.0
    var samePlatformStandSec = 90.0
    var quickChangeWalkSec = 8
    var quickChangeBeforeSec = 45.0
    var quickChangeAfterSec = 75.0
    var stayedOnPastSec = 60.0
    /** Position of a stop on a line (line key, stop index), from the published geometry; set by the planner. */
    var stopCoordinate: ((String, Int) -> LatLon?)? = null
    /** How often this rider has ridden the plan's line on this trip before (the personal model); null until known. */
    var learnedShare: ((origin: String, dest: String, leg: Int, chosenKey: String) -> Double?)? = null
    /** Bumps on every change the screens should see. */
    var version = 0; private set

    val currentBelief: LineBelief? get() = beliefs.lastOrNull()
    fun belief(leg: Int): LineBelief? = beliefs.firstOrNull { it.leg == leg }

    val prompt: BoardingPrompt?
        get() {
            val t = tracker ?: return null
            if (t.phase != TripPhase.riding) return null
            val leg = t.timeline.legsRidden
            if (leg >= legs.size || leg in handSet || leg in dismissed) return null
            val b = belief(leg) ?: return null
            val reason = when {
                b.assumed -> BoardingPrompt.Reason.assumed
                !b.settled -> BoardingPrompt.Reason.unsure
                b.verdict == LineBelief.Verdict.switched -> BoardingPrompt.Reason.switched
                else -> return null
            }
            return BoardingPrompt(leg, reason, candidates[leg] ?: emptyList(), b.bestTrain, b.bestKey, legs[leg].chosenKey)
        }

    val needsTrainPick: Boolean
        get() {
            val t = tracker ?: return false
            if (t.phase != TripPhase.riding) return false
            val leg = t.timeline.legsRidden
            return belief(leg) == null && (candidates[leg] ?: emptyList()).isEmpty() && leg !in dismissed && leg !in handSet
        }

    val onTrain: Boolean get() = tracker?.onTrain ?: false
    val currentLeg: Int get() = tracker?.timeline?.legsRidden ?: 0
    val currentLegKeys: List<String> get() = legs.getOrNull(currentLeg)?.keys ?: emptyList()
    val currentLegChosenKey: String? get() = legs.getOrNull(currentLeg)?.chosenKey

    fun begin(start: TripTimeline, distanceToOriginM: Double?, legs: List<LegPlan>, now: Double) {
        if (tracker != null) end("changed", now)
        tracker = TripTracker(start, distanceToOriginM)
        endReason = null
        this.legs = legs
        log.reset(); transferLog.reset(); logLeg = -1; candidates.clear(); beliefs = emptyList(); seenEvents = 0; handSet.clear(); dismissed.clear()
        departureBeliefs.clear(); departedTs.clear(); lastAlight = null; locationApplied.clear(); departureChecked.clear(); offPlanAlighting = null
        stayedOn = null; stayedOnChecked.clear()
        publish()
    }

    fun motion(m: MotionSecond) {
        tracker?.motion(m)
        checkQuickChange()
        handleEvents()
        publish()
    }

    private fun checkQuickChange() {
        val t = tracker ?: return
        if (t.phase != TripPhase.riding) return
        val walk = t.walking ?: return
        if (walk.seconds < quickChangeWalkSec || t.timeline.events.lastOrNull()?.kind != MotionEvent.Kind.departed) return
        val c = trainInHand ?: return
        val lag = PlatformTiming.recordedLag(c.route)
        val atStop = c.stopTs.any { (idx, feedTs) -> idx > c.boardIdx && walk.startTs >= feedTs - lag - quickChangeBeforeSec && walk.startTs <= feedTs - lag + quickChangeAfterSec }
        if (!atStop) return
        tracker?.alightWalking(walk.startTs)
    }

    private fun checkStayedOn(now: Double) {
        val t = tracker ?: return
        if (t.phase != TripPhase.riding) return
        val leg = t.timeline.legsRidden
        if (leg in stayedOnChecked || t.timeline.events.lastOrNull()?.kind != MotionEvent.Kind.departed) return
        val c = trainInHand ?: return
        val past = c.alightIdx ?: return
        val progress = c.progressIdx ?: return
        if (progress <= past) return
        val lag = PlatformTiming.recordedLag(c.route)
        val nextTs = c.stopTs.filterKeys { it > past }.minByOrNull { it.key }?.value ?: return
        if (now - (nextTs - lag) < stayedOnPastSec) return
        val moving = t.lastMovingTs ?: return
        if (moving <= (c.stopTs[past]?.let { it - lag } ?: 0.0)) return
        stayedOnChecked.add(leg)
        stayedOn = StayedOn(leg, c.key, c.trainId, past, now)
    }

    fun location(ts: Double, toOriginM: Double?, toDestM: Double?, now: Double, coordinate: LatLon? = null, accuracyM: Double? = null) {
        tracker?.location(ts, toOriginM, toDestM, now, accuracyM)
        if ((accuracyM ?: 0.0) > 100) { publish(); return }
        val la = lastAlight
        val sc = stopCoordinate
        if (coordinate != null && la != null && ts >= la.second && now - la.second <= 300 && la.first !in locationApplied && la.first !in handSet && sc != null) {
            val b = belief(la.first)
            val cands = candidates[la.first]
            if (b != null && !b.assumed && cands != null) {
                val dist = HashMap<String, Double>()
                for (cd in cands) {
                    val ai = inference.alightingStop(cd, la.second) ?: continue
                    val p = sc(cd.key, ai) ?: continue
                    dist[cd.trainId] = haversineM(coordinate, p)
                }
                if (dist.isNotEmpty()) { locationApplied.add(la.first); store(inference.withLocation(b, cands, dist)) }
            }
        }
        publish()
    }

    fun updateForecast(boardTs: Double?, arriveTs: Double?, now: Double) { tracker?.updateForecast(boardTs, arriveTs, now) }

    /** Every feed poll while the route is on: the boards of the leg in hand go into the departure log. */
    fun observeBoards(boards: Map<String, LineBoard>, now: Double) {
        val t = tracker ?: return
        if (t.phase == TripPhase.arrived) return
        val leg = t.timeline.legsRidden
        if (leg >= legs.size) return
        if (leg != logLeg) {
            log = if (logLeg >= 0 && leg == logLeg + 1) transferLog else DepartureLog()
            transferLog = DepartureLog()
            logLeg = leg
        }
        observe(legs[leg], boards, log, now)
        if (leg + 1 < legs.size) observe(legs[leg + 1], boards, transferLog, now)
        rideEvidence(now)
        checkDepartureMade(now)
        checkSamePlatformChange(now)
        checkStayedOn(now)
        publish()
    }

    /** The train the plan has the rider on for each leg still to board. */
    fun updatePlannedTrains(trains: Map<Int, Pair<String, String>>, now: Double) {
        val t = tracker ?: return
        if (t.phase == TripPhase.arrived) return
        val riding = if (t.phase == TripPhase.riding) t.timeline.legsRidden else -1
        for ((leg, tr) in trains) {
            if (leg >= legs.size || leg < t.timeline.legsRidden || leg == riding || departedTs[leg] != null) continue
            if (tr.first !in legs[leg].keys) continue
            legs[leg].chosenKey = tr.first
            legs[leg].chosenTrainId = tr.second
        }
    }

    private fun checkDepartureMade(now: Double) {
        val t = tracker ?: return
        if (t.phase != TripPhase.riding || t.departedByLocation) return
        val leg = t.timeline.legsRidden
        if (leg >= legs.size || leg in handSet || leg in departureChecked) return
        val dep = departedTs[leg] ?: return
        if (now - dep < departureCheckSec) return
        if (now - dep > departureCheckSec + 240) { departureChecked.add(leg); return }
        if (log.lastPollTs < dep + 75) return
        departureChecked.add(leg)
        val check = log.departureCheck(dep)
        if (check.left || !check.waiting) return
        tracker?.withdrawDeparture()
        beliefs = beliefs.filter { it.leg != leg }
        departureBeliefs.remove(leg); candidates.remove(leg); departedTs.remove(leg)
        departureChecked.remove(leg)
        seenEvents = tracker?.timeline?.events?.size ?: 0
    }

    private fun observe(plan: LegPlan, boards: Map<String, LineBoard>, into: DepartureLog, now: Double) {
        for (k in plan.keys) {
            val b = boards[k] ?: continue
            val span = plan.idx[k] ?: continue
            into.observe(b.trains, k, span.from, span, true, now)
        }
        for ((k, bi) in plan.platformKeys) {
            if (k in plan.keys) continue
            val b = boards[k] ?: continue
            into.observe(b.trains, k, bi, null, false, now, samePlatform = k !in plan.otherPlatformKeys)
        }
    }

    private fun rideEvidence(now: Double) {
        val t = tracker ?: return
        if (t.phase != TripPhase.riding) return
        val leg = t.timeline.legsRidden
        val cands = candidates[leg] ?: return
        if (cands.isEmpty()) return
        val fresh = log.refreshed(cands)
        candidates[leg] = fresh
        if (leg in handSet) return
        val base = departureBeliefs[leg] ?: return
        val b = inference.withRide(base, fresh, t.stopTimes, now)
        if (b != belief(leg)) store(b)
    }

    private fun checkSamePlatformChange(now: Double) {
        val t = tracker ?: return
        if (t.phase != TripPhase.riding) return
        val leg = t.timeline.legsRidden
        if (leg + 1 >= legs.size || legs[leg + 1].walkSec > 0) return
        val since = t.standingSince ?: return
        if (now - since < samePlatformStandSec) return
        val b = belief(leg) ?: return
        val best = b.bestTrain ?: return
        val cand = candidates[leg]?.firstOrNull { it.trainId == best } ?: return
        val alightIdx = cand.alightIdx ?: return
        val progress = log.progressIdx(best) ?: return
        if (progress <= alightIdx) return
        tracker?.alightStanding(since)
        handleEvents()
    }

    private fun handleEvents() {
        val t = tracker ?: return
        val events = t.timeline.events
        while (seenEvents < events.size) {
            val e = events[seenEvents]
            seenEvents += 1
            when (e.kind) {
                MotionEvent.Kind.departed -> {
                    val leg = t.timeline.legsRidden
                    if (leg >= legs.size) continue
                    val plan = legs[leg]
                    val cands = log.candidates(e.ts, inference.departureWindowSec, plan.chosenTrainId)
                    candidates[leg] = cands
                    departedTs[leg] = e.ts
                    if (leg in handSet) continue
                    val share = plan.chosenKey?.let { ck -> learnedShare?.invoke(plan.origin, plan.dest, leg, ck) }
                    val b = inference.fromDeparture(cands, e.ts, leg, plan.chosenKey, share)
                    val fallback = if (b.byTrain.isEmpty()) LineBelief.fromPlan(leg, plan.chosenKey, plan.chosenTrainId, "plan") else null
                    if (fallback != null) store(fallback) else { departureBeliefs[leg] = b; store(b) }
                }
                MotionEvent.Kind.alighted -> {
                    val leg = t.timeline.legsRidden - 1
                    lastAlight = Pair(leg, e.ts)
                    if (leg >= 0 && leg in handSet) checkAlightingStop(leg, e.ts)
                    if (leg < 0 || leg in handSet) continue
                    val current = belief(leg) ?: continue
                    if (current.assumed) continue
                    val known = candidates[leg] ?: continue
                    val cands = log.refreshed(known)
                    candidates[leg] = cands
                    var b = departureBeliefs[leg]?.let { inference.withRide(it, cands, t.lastRideStopTimes, e.ts) } ?: current
                    t.timeline.rideStops.lastOrNull()?.let { stops -> b = inference.withStops(b, cands, stops, departedTs[leg], e.ts) }
                    store(inference.withAlighting(b, cands, e.ts))
                    checkAlightingStop(leg, e.ts)
                }
            }
        }
    }

    private fun checkAlightingStop(leg: Int, ts: Double) {
        val b = belief(leg) ?: return
        if (!(b.settled || b.byHand)) return
        val id = b.bestTrain ?: return
        val c = log.refreshed((candidates[leg] ?: emptyList()).filter { it.trainId == id }).firstOrNull() ?: return
        fun walkOff(feedTs: Double) = PlatformTiming.atPlatform(feedTs, c.route) + inference.alightLagSec
        val near = c.stopTs.filterKeys { it > c.boardIdx }.minByOrNull { abs(walkOff(it.value) - ts) } ?: return
        if (abs(walkOff(near.value) - ts) > 150) return
        if (legs[leg].idx[c.key]?.to == near.key) return
        offPlanAlighting = OffPlanAlighting(leg, c.key, near.key, ts)
    }

    private fun store(b: LineBelief) {
        beliefs = if (beliefs.any { it.leg == b.leg }) beliefs.map { if (it.leg == b.leg) b else it } else beliefs + b
        b.boarded?.let { tracker?.setBoarded(it) }
    }

    private fun checkAssumed() {
        val t = tracker ?: return
        if (t.phase != TripPhase.riding || !t.timeline.rideAssumed) return
        val leg = t.timeline.legsRidden
        if (leg >= legs.size || belief(leg) != null) return
        LineBelief.fromPlan(leg, legs[leg].chosenKey, legs[leg].chosenTrainId, "schedule")?.let { store(it) }
    }

    fun setBoardedByHand(key: String) {
        val leg = currentLeg
        if (leg >= legs.size) return
        handSet.add(leg)
        store(LineBelief.byHand(leg, key, legs[leg].chosenKey, candidates[leg] ?: emptyList(), departedTs[leg]))
        publish()
    }

    fun confirm(trainId: String) {
        val leg = currentLeg
        if (leg >= legs.size) return
        val c = (candidates[leg] ?: emptyList()).firstOrNull { it.trainId == trainId } ?: return
        handSet.add(leg)
        store(LineBelief(leg, mapOf(c.trainId to 1.0), mapOf(c.key to 1.0), legs[leg].chosenKey, listOf("hand")))
        publish()
    }

    fun dismissPrompt() { dismissed.add(currentLeg); publish() }

    /** The rider says which train they are on, from the trains that have left the platform. */
    fun setOnTrain(c: BoardingCandidate, now: Double) {
        val t = tracker ?: return
        if (t.phase == TripPhase.arrived) return
        val leg = t.timeline.legsRidden
        if (leg >= legs.size) return
        if (t.phase != TripPhase.riding) {
            t.beginRiding(now, PlatformTiming.pullsAway(c.boardTs, c.route))
            seenEvents = t.timeline.events.size
        }
        if (logLeg != leg) { log = if (leg == logLeg + 1) transferLog else DepartureLog(); transferLog = DepartureLog(); logLeg = leg }
        log.seed(c, now)
        val cands = ((candidates[leg] ?: emptyList()).filter { it.trainId != c.trainId } + c).sortedWith(compareBy<BoardingCandidate> { it.boardTs }.thenBy { it.trainId })
        candidates[leg] = cands
        departedTs[leg] = PlatformTiming.pullsAway(c.boardTs, c.route)
        handSet.add(leg); dismissed.remove(leg)
        store(LineBelief(leg, mapOf(c.trainId to 1.0), mapOf(c.key to 1.0), legs[leg].chosenKey, listOf("hand")))
        publish()
    }

    fun notOnTrain() {
        val leg = currentLeg
        tracker?.notOnTrain()
        beliefs = beliefs.filter { it.leg != leg }
        departureBeliefs.remove(leg); candidates.remove(leg); departedTs.remove(leg)
        handSet.remove(leg); dismissed.remove(leg)
        seenEvents = tracker?.timeline?.events?.size ?: 0
        publish()
    }

    /** The route changed under the rider: the legs from here on follow the new plan. */
    fun replan(newLegs: List<LegPlan>, transferStation: String? = null) {
        val leg = currentLeg
        val old = legs
        legs = newLegs
        transferLog = DepartureLog()
        if (leg < newLegs.size && leg < old.size && boardingStops(old[leg]) != boardingStops(newLegs[leg])) {
            log = DepartureLog(); logLeg = leg
            candidates.remove(leg); departureBeliefs.remove(leg); departureChecked.remove(leg)
            if (leg !in handSet) beliefs = beliefs.filter { it.leg != leg }
        }
        if (leg < legs.size) belief(leg)?.let { b ->
            store(b.copy(chosenKey = legs[leg].chosenKey))
            departureBeliefs[leg]?.let { d -> departureBeliefs[leg] = d.copy(chosenKey = legs[leg].chosenKey) }
        }
        dismissed.remove(leg)
        stayedOn = null
        tracker?.replan(maxOf(1, newLegs.size), transferStation)
        publish()
    }

    private fun boardingStops(p: LegPlan): Map<String, Int> = p.idx.mapValues { it.value.from }

    /** Trains that already left the leg's boarding platform, taken up by the log as if it had seen them there. */
    fun seedCurrentLeg(cands: List<BoardingCandidate>, now: Double) {
        val t = tracker ?: return
        val leg = t.timeline.legsRidden
        if (leg >= legs.size) return
        if (logLeg != leg) { log = DepartureLog(); transferLog = DepartureLog(); logLeg = leg }
        for (c in cands) log.seed(c, now)
        if (leg in handSet || t.phase != TripPhase.riding) return
        val dep = departedTs[leg] ?: return
        val plan = legs[leg]
        val cs = log.candidates(dep, inference.departureWindowSec, plan.chosenTrainId)
        candidates[leg] = cs
        val b = inference.fromDeparture(cs, dep, leg, plan.chosenKey)
        if (b.byTrain.isNotEmpty()) { departureBeliefs[leg] = b; store(b) }
        publish()
    }

    fun setLiveArrival(ts: Double) { tracker?.setLiveArrival(ts) }

    val boardedCandidate: BoardingCandidate?
        get() {
            val t = tracker ?: return null
            if (t.phase != TripPhase.riding) return null
            val leg = t.timeline.legsRidden
            val b = belief(leg) ?: return null
            if (!(b.settled || b.byHand)) return null
            val id = b.bestTrain ?: return null
            return (candidates[leg] ?: emptyList()).firstOrNull { it.trainId == id } ?: log.entry(id, legs.getOrNull(leg)?.chosenTrainId)
        }

    private val trainInHand: BoardingCandidate?
        get() {
            boardedCandidate?.let { return it }
            val t = tracker ?: return null
            if (t.phase != TripPhase.riding) return null
            val leg = t.timeline.legsRidden
            val id = legs.getOrNull(leg)?.chosenTrainId ?: return null
            return log.entry(id, id)
        }

    fun tick(now: Double) {
        tracker?.tick(now)
        checkAssumed()
        publish()
    }

    private fun publish() {
        val t = tracker ?: return
        phase = t.phase
        motionState = t.motionState
        motionSeconds = t.timeline.motionSeconds
        walkMeters = t.timeline.walkMeters
        rideAssumed = t.timeline.rideAssumed
        endReason = t.timeline.endedBy
        version += 1
    }

    /** Ends the trip and hands back what it measured. */
    fun end(by: String, now: Double): TripTimeline? {
        val t = tracker ?: return null
        val tl = t.end(now, by)
        tracker = null
        legs = emptyList()
        log.reset(); transferLog.reset(); candidates.clear(); departedTs.clear(); handSet.clear()
        departureBeliefs.clear(); lastAlight = null; locationApplied.clear(); departureChecked.clear(); offPlanAlighting = null
        stayedOn = null; stayedOnChecked.clear()
        phase = null
        motionState = MotionState.unknown
        endReason = tl.endedBy
        version += 1
        return tl
    }

    /** The planner has acted on them. */
    fun clearOffPlanAlighting() { offPlanAlighting = null }
    fun clearStayedOn() { stayedOn = null }
}
