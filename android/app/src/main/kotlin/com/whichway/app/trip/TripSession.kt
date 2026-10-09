// The route in progress, as ios/WhichWay/WhichWay/Views/PlannerView.swift runs it: starting and ending the route,
// the plan's trains per leg, the ride's own itinerary, following the train the rider actually boarded (or the
// stop they got off at, or staying on past the change), the rider's own word, and the notification. The
// recorder (core) does the reasoning; this feeds it the sensors, the fixes, the feeds and the clock.
package com.whichway.app.trip

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.whichway.app.store.AppData
import com.whichway.app.store.AppState
import com.whichway.app.store.Fix
import com.whichway.app.store.LocationService
import com.whichway.app.store.Stores
import com.whichway.core.BoardingCandidate
import com.whichway.core.BoardingPrompt
import com.whichway.core.ClientSchedule
import com.whichway.core.Fmt
import com.whichway.core.Itinerary
import com.whichway.core.LatLon
import com.whichway.core.LegPlan
import com.whichway.core.LineBelief
import com.whichway.core.MotionSecond
import com.whichway.core.OffPlanAlighting
import com.whichway.core.PathOption
import com.whichway.core.PlatformTiming
import com.whichway.core.StationIndex
import com.whichway.core.StayedOn
import com.whichway.core.TripPhase
import com.whichway.core.TripRecorder
import com.whichway.core.TripTimeline
import com.whichway.core.connectionItinerary
import com.whichway.core.enumeratePaths
import com.whichway.core.evaluate
import com.whichway.core.haversineM
import com.whichway.core.pathTrips
import com.whichway.core.plannedCandidate
import com.whichway.core.ridingItinerary
import com.whichway.core.stationCoordinates
import com.whichway.core.trainsAhead
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlin.math.abs

/** What the Go tab reads: a snapshot after every change. */
data class TripUi(
    val phase: TripPhase? = null,
    val onTrain: Boolean = false,
    val currentLeg: Int = 0,
    val currentLegKeys: List<String> = emptyList(),
    val belief: LineBelief? = null,
    val prompt: BoardingPrompt? = null,
    val rideItinerary: Itinerary? = null,
    val ridingRoute: String? = null,
    val ridePresumed: Boolean = false,
    val rideAssumed: Boolean = false,
    val lastEndNote: String? = null,
    val needsTrainPick: Boolean = false,
    val motionState: String = "",
    val version: Int = 0,
    /** For Developer settings: what the sensors felt last, and the tracker's record. */
    val lastMotion: MotionSecond? = null,
    val motionSeconds: Int = 0,
    val platformTs: Double? = null,
    val forecastBoardTs: Double? = null,
    val events: Int = 0,
)

/** The Go tab's planner, as the session needs it: read on demand, so the composable's state is the one source. */
interface PlannerAccess {
    val originId: String
    val destId: String
    val paths: List<PathOption>
    val headline: PathOption?
    fun select(id: String)
    fun addExtra(p: PathOption)
}

class TripSession private constructor(context: Context) {
    private val app = context.applicationContext
    private val data: () -> AppData? = { AppData.current }
    private val stores = Stores.get(app)
    private val loc = LocationService.get(app)
    private val sampler = MotionSampler(app)
    val recorder = TripRecorder()
    private val main = Handler(Looper.getMainLooper())
    private val _ui = MutableStateFlow(TripUi())
    val ui: StateFlow<TripUi> = _ui
    /** True while the notification permission should be asked for; the activity watches it. */
    val needsNotificationPermission = MutableStateFlow(false)
    var planner: PlannerAccess? = null

    private var routeEndedByHand = false
    private var lastEndNote: String? = null
    private var rideItinerary: Itinerary? = null
    private val plannedTrains = HashMap<Int, BoardingCandidate>()
    private val preferredKeys = HashMap<Int, String>()
    private var lastSwitchTs = 0.0
    private val autoSwitchConfidence = 0.75
    private val approachFixes = ArrayList<Pair<Double, Double>>()
    private var serviceRunning = false
    private var ticker: Runnable? = null
    private var lastBeliefVersion = -1
    private var lastMotion: MotionSecond? = null
    /** The train the tracker's forecast was last taken from. */
    private var forecastTrainId: String? = null
    /** Legs whose plan's train is kept as it stood when the forecast froze. */
    private val frozenLegs = HashSet<Int>()

    val started: Boolean get() = recorder.phase != null
    val notificationTitle: String
        get() {
            val u = _ui.value
            val ride = u.rideItinerary
            val p = planner?.headline
            return when {
                u.onTrain -> {
                    val route = u.ridingRoute ?: ride?.legs?.firstOrNull()?.train?.route ?: p?.legs?.getOrNull(u.currentLeg)?.primaryRoute ?: ""
                    val word = if (u.ridePresumed) "On the $route, presumably" else "On the $route"
                    ride?.let { "$word · off ${Fmt.hhmm(it.legs[0].arriveTs)}" } ?: word
                }
                ride != null -> "Change: ${ride.legs[0].train.route} at ${Fmt.hhmm(ride.boardTs)}"
                p?.live != null -> "Take the ${p.live!!.legs[0].train.route} at ${Fmt.hhmm(p.live!!.boardTs)}"
                else -> "Route in progress"
            }
        }
    val notificationText: String get() {
        val p = planner?.headline ?: return ""
        val it = rideItinerary ?: p.live
        val arrive = it?.let { "Arrive ${Fmt.hhmm(it.arriveTs)}" } ?: "Expected ${Fmt.minTxt(p.expectedSec)}"
        val pos = it?.legs?.firstOrNull()?.train?.position?.text?.let { " · train now $it" } ?: ""
        return "$arrive · ${p.label}$pos"
    }

    private fun state(): AppState? = data()?.state?.value
    private fun sched(): ClientSchedule? = state()?.schedule
    private fun index(): StationIndex? = state()?.index
    private fun now(): Double = state()?.now ?: System.currentTimeMillis() / 1000.0

    // the service and the sensors

    fun serviceStarted() {
        serviceRunning = true
        if (stores.learnPace) sampler.start { m -> main.post { onMotion(m) } }
        loc.startTracking()
    }

    fun serviceStopped() { serviceRunning = false; sampler.stop() }

    private fun onMotion(m: MotionSecond) {
        if (!started) return
        lastMotion = m
        recorder.motion(m)
        afterRecorderChange()
    }

    /** Every fix while the app or the service is up. */
    fun onFix(f: Fix) {
        if (!started) { checkArrival(); checkApproach(f); return }
        recorder.location(f.ts, distanceM(originIdOf(), f), distanceM(planner?.destId ?: "", f), now(), f.latLon, f.accuracyM)
        afterRecorderChange()
    }

    /** Every feed poll (and every scenario change) while a route is on screen. */
    fun onPoll() {
        val p = planner ?: return
        val s = state() ?: return
        if (!started) { pollLocation(); return }
        val n = s.now
        val h = p.headline
        // the planner moved on from the train the forecast named while the rider stood at the platform: it left,
        // so the forecast freezes and the plan's train for the leg is kept as it was
        val liveId = h?.live?.legs?.firstOrNull()?.train?.id
        if (forecastTrainId != null && liveId != forecastTrainId && recorder.phase == TripPhase.atStation) {
            val before = recorder.tracker?.timeline?.forecastBoardTs
            recorder.tracker?.forecastTrainDeparted(n)
            if (recorder.tracker?.timeline?.forecastBoardTs != before) frozenLegs.add(recorder.currentLeg)
        }
        followPlan(n)
        recorder.observeBoards(s.boards, n)
        refreshRideArrival()
        recorder.updateForecast(h?.live?.boardTs, h?.live?.arriveTs, n)
        if (recorder.tracker?.timeline?.forecastBoardTs == h?.live?.boardTs) forecastTrainId = liveId
        recorder.tick(n)
        if (com.whichway.app.BuildConfig.DEBUG) android.util.Log.d("WhichWay", "poll ${Fmt.hhmmss(n)} phase=${recorder.phase} onTrain=${recorder.onTrain} assumed=${recorder.rideAssumed} live=${h?.live?.boardTs?.let { Fmt.hhmmss(it) }} forecast=${recorder.tracker?.timeline?.forecastBoardTs?.let { Fmt.hhmmss(it) }} belief=${recorder.currentBelief?.let { "${it.bestKey} ${it.evidence}" }}")
        afterRecorderChange()
    }

    private fun startTicker() {
        stopTicker()
        val r = object : Runnable { override fun run() { if (!started) return; recorder.tick(now()); afterRecorderChange(); main.postDelayed(this, 15_000) } }
        ticker = r
        main.postDelayed(r, 15_000)
    }
    private fun stopTicker() { ticker?.let { main.removeCallbacks(it) }; ticker = null }

    /** The recorder moved: the belief, the alighting and the stayed-on checks, the arrival, the end. */
    private fun afterRecorderChange() {
        if (recorder.phase == TripPhase.arrived) { endTrip(recorder.endReason ?: "arrived"); return }
        if (recorder.version != lastBeliefVersion) {
            lastBeliefVersion = recorder.version
            recorder.offPlanAlighting?.let { recorder.clearOffPlanAlighting(); followAlighting(it) }
            recorder.stayedOn?.let { recorder.clearStayedOn(); followStayingOn(it) }
            followBoarded()
        }
        publish()
    }

    private fun publish() {
        val b = recorder.currentBelief
        val onTrain = recorder.onTrain
        val ridingRoute = if (!onTrain) null else if (b != null && (b.settled || b.byHand) && b.route != null) b.route else plannedTrains[recorder.currentLeg]?.route ?: rideItinerary?.legs?.firstOrNull()?.train?.route
        val presumed = onTrain && (if (b != null && (b.settled || b.byHand)) b.assumed else true)
        _ui.value = TripUi(recorder.phase, onTrain, recorder.currentLeg, recorder.currentLegKeys, b, recorder.prompt, rideItinerary, ridingRoute, presumed,
            recorder.rideAssumed, lastEndNote, recorder.needsTrainPick, recorder.motionState.name, recorder.version,
            lastMotion, recorder.motionSeconds, recorder.tracker?.timeline?.platformTs, recorder.tracker?.timeline?.forecastBoardTs, recorder.tracker?.timeline?.events?.size ?: 0)
        if (started && serviceRunning) TripService.update(app, notificationTitle, notificationText)
    }

    // starting and ending

    private fun originIdOf() = planner?.originId ?: ""

    private fun distanceM(stationId: String, f: Fix?): Double? {
        if (stationId.isEmpty() || f == null) return null
        val s = state() ?: return null
        val sched = s.schedule ?: return null; val index = s.index ?: return null; val geo = s.geometry ?: return null
        val c = stationCoordinates(sched, index, geo)[stationId] ?: return null
        return haversineM(f.latLon, c)
    }

    /** The distance to a station from a fix fresh and sure enough to act on. */
    private fun tripDistanceM(stationId: String): Double? = distanceM(stationId, loc.fresh())

    /** The route starts: anywhere by hand, at the station by GPS. */
    fun startTrip(by: String) {
        val p = planner ?: return
        if (p.originId.isEmpty() || p.destId.isEmpty() || started) return
        val s = state() ?: return
        routeEndedByHand = false
        lastEndNote = null
        val d = tripDistanceM(p.originId)
        val h = p.headline
        val f = loc.fix.value
        val here = f?.takeIf { it.ageSec < 300 }?.let { com.whichway.core.Place.nearest(stores.places.value, it.latLon, 150.0) }
        val tl = TripTimeline(startTs = s.now, startedBy = by, startDistanceM = d, placeId = here?.id, originStation = p.originId, destStation = p.destId,
            transferStation = h?.transfer?.station, legs = h?.legs?.size ?: 1)
        tl.forecastBoardTs = h?.live?.boardTs; tl.forecastArriveTs = h?.live?.arriveTs; tl.liveArriveTs = h?.live?.arriveTs
        val plans = legPlans(h)
        data()?.setWanted(plans.flatMap { it.platformKeys.keys }.toSet(), "trip")
        lastSwitchTs = 0.0; rideItinerary = null; plannedTrains.clear(); preferredKeys.clear(); lastBeliefVersion = -1; forecastTrainId = null; frozenLegs.clear()
        recorder.stopCoordinate = { key, idx -> state()?.geometry?.lines?.get(key)?.coord(idx) }
        recorder.learnedShare = { o, dd, leg, ck -> stores.pace.value.chosenLineShare(o, dd, leg, ck) }
        recorder.begin(tl, d, plans, s.now)
        data()?.requestGeometry()
        if (!loc.authorized) loc.request()
        if (android.os.Build.VERSION.SDK_INT >= 33 && androidx.core.content.ContextCompat.checkSelfPermission(app, android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) needsNotificationPermission.value = true
        TripService.start(app)
        startTicker()
        publish()
    }

    /** The plan's legs for the line inference: each leg's lines and stop spans, the train and line the itinerary boards, and the other same-direction lines at the boarding platform. */
    private fun legPlans(p: PathOption?): List<LegPlan> {
        if (p == null) return emptyList()
        val index = index() ?: return emptyList()
        val origin = originIdOf(); val dest = planner?.destId ?: ""
        return p.legs.mapIndexed { i, leg ->
            val tc = p.live?.legs?.getOrNull(i)?.takeIf { p.live?.legs?.size == p.legs.size }
            val preferred = preferredKeys[i]?.takeIf { it in leg.keys }
            val plan = LegPlan(leg.keys.toList(), leg.idx.toMap(), preferred ?: tc?.key ?: leg.primaryKey,
                if (preferred == null || preferred == tc?.key) tc?.train?.id else null, origin = origin, dest = dest,
                walkSec = if (i == 0) 0.0 else (p.live?.walkSec ?: (p.transfer?.walkSec ?: 0).toDouble()))
            val dir = (plan.chosenKey ?: "").substringAfter("_")
            index.stations[index.stationOf(leg.from)]?.let { st ->
                for (m in st.members) if (m.dir == dir && m.key !in leg.keys) {
                    plan.platformKeys[m.key] = m.idx
                    if (m.stop != leg.from) plan.otherPlatformKeys.add(m.key)
                }
            }
            plan
        }
    }

    fun endTrip(by: String) {
        if (!started && recorder.endReason == null) return
        if (by == "hand") routeEndedByHand = true
        stopTicker()
        data()?.setWanted(emptySet(), "trip")
        data()?.setWanted(emptySet(), "onTrain")
        rideItinerary = null; plannedTrains.clear()
        val tl = recorder.end(by, now())
        TripService.stop(app)
        sampler.stop()
        tl?.let { if (stores.learnPace) stores.learnPace(it) }
        val destName = index()?.stations?.get(planner?.destId ?: "")?.name ?: "your stop"
        val at = Fmt.hhmm(tl?.endedTs ?: now())
        lastEndNote = when (by) {
            "arrived" -> "Ended $at: reached $destName"
            "alighted", "walked" -> "Ended $at: you got off the train"
            "timeout" -> "Ended $at: long past the expected arrival"
            else -> null
        }
        publish()
    }

    /** The stations changed under the route. */
    fun resetTrip() { if (started) endTrip("changed"); routeEndedByHand = false; lastEndNote = null; publish() }

    /** Within 150 m of the origin station with no route in progress: it starts by itself. */
    private fun checkArrival() {
        val p = planner ?: return
        if (started || routeEndedByHand || p.destId.isEmpty()) return
        val d = tripDistanceM(p.originId) ?: return
        if (d <= 150) startTrip("gps")
    }

    /** A walk closing on the origin station at a walking pace, over the last minute or so, starts the route on the way. */
    private fun checkApproach(f: Fix) {
        val p = planner ?: return
        if (started || routeEndedByHand || p.destId.isEmpty()) return
        val l = loc.fresh(30.0, 65.0) ?: return
        val d = distanceM(p.originId, l) ?: return
        val ts = l.ts
        if (approachFixes.lastOrNull()?.let { ts <= it.first } == true) return
        approachFixes.add(Pair(ts, d))
        approachFixes.removeAll { ts - it.first > 180 }
        if (d <= 150 || d > 1500) return
        val first = approachFixes.firstOrNull() ?: return
        if (ts - first.first < 45) return
        val closed = first.second - d; val dt = ts - first.first
        if (closed >= 40 && closed / dt >= 0.5 && closed / dt <= 2.5) startTrip("gps")
    }

    /** A fresh fix every poll while a trip is on screen and not started yet (once location is allowed). */
    private fun pollLocation() {
        val p = planner ?: return
        if (p.originId.isEmpty() || p.destId.isEmpty() || !loc.authorized) return
        data()?.requestGeometry()
        loc.request()
        checkArrival()
    }

    // the plan's trains and the ride's itinerary

    private fun followPlan(now: Double) {
        val p = planner?.headline ?: return
        val it = p.live ?: return
        val leg = recorder.currentLeg
        for (i in p.legs.indices) {
            if (i < leg) continue
            if (i == leg && recorder.onTrain) continue
            val atPlatform = i == leg && (recorder.phase == TripPhase.atStation || (i > 0 && recorder.phase == TripPhase.riding))
            val kept = plannedTrains[i]
            if (atPlatform && kept != null && (i in frozenLegs || now >= PlatformTiming.atPlatform(kept.boardTs, kept.route))) continue
            plannedCandidate(it, p, i)?.let { c -> plannedTrains[i] = c }
        }
        recorder.updatePlannedTrains(plannedTrains.mapValues { Pair(it.value.key, it.value.trainId) }, now)
    }

    private fun refreshRideArrival() {
        val sel = planner?.headline; val s = state(); val sched = s?.schedule
        if (recorder.phase != TripPhase.riding || sel == null || s == null || sched == null) { rideItinerary = null; return }
        val leg = recorder.currentLeg
        if (recorder.onTrain) {
            val c = recorder.boardedCandidate ?: plannedTrains[leg] ?: return
            val it = ridingItinerary(s.predictedBoards, sched, sel, leg, c, s.now) ?: return
            rideItinerary = it
            recorder.setLiveArrival(it.arriveTs)
        } else if (leg > 0) {
            val it = connectionItinerary(s.predictedBoards, sched, sel, leg, s.now) ?: return
            rideItinerary = it
            recorder.setLiveArrival(it.arriveTs)
        } else rideItinerary = null
    }

    // following the train actually boarded

    private fun evaluated(ps: List<PathOption>): List<PathOption> {
        val s = state() ?: return ps; val sched = s.schedule ?: return ps
        val now = s.now; val hour = Fmt.nyHour(now)
        for (p in ps) {
            evaluate(p, sched, s.lineSched, now, s.holds, s.deviations, hour)
            p.transfer?.let { tr -> val sec = stores.pace.value.plannedTransferSec(tr.station, tr.walkSec); if (sec != tr.walkSec) { p.schedSec += sec - tr.walkSec; tr.walkSec = sec } }
            p.live = pathTrips(s.predictedBoards, sched, p, now, 1).firstOrNull()
        }
        return ps
    }

    private fun morePaths(): List<PathOption> {
        val p = planner ?: return emptyList(); val sched = sched() ?: return emptyList(); val index = index() ?: return emptyList()
        val listed = p.paths.map { it.id }.toSet()
        return enumeratePaths(sched, index, p.originId, p.destId, 24).filter { it.id !in listed }
    }

    /** The route that rides `key` on leg `leg` and matches the route in hand up to there. */
    private fun routeRiding(key: String, leg: Int, sel: PathOption, adopt: Boolean = true): PathOption? {
        val p = planner ?: return null; val index = index() ?: return null
        fun fits(o: PathOption): Boolean {
            val l = o.legs.getOrNull(leg) ?: return false
            if (key !in l.keys) return false
            if (leg > 0 && index.stationOf(l.from) != index.stationOf(sel.legs[leg].from)) return false
            for (i in 0 until leg) {
                if (index.stationOf(o.legs[i].from) != index.stationOf(sel.legs[i].from) || index.stationOf(o.legs[i].to) != index.stationOf(sel.legs[i].to)) return false
                val r = recorder.belief(i)?.takeIf { it.settled || it.byHand }?.bestKey
                if (r != null) { if (r !in o.legs[i].keys) return false } else if (o.legs[i].keys.none { it in sel.legs[i].keys }) return false
            }
            return true
        }
        p.paths.firstOrNull(::fits)?.let { return it }
        val more = evaluated(morePaths().filter(::fits)).sortedBy { it.expectedSec }
        val best = more.firstOrNull() ?: return null
        if (adopt) p.addExtra(best)
        return best
    }

    private fun switchRoute(alt: PathOption) {
        val p = planner ?: return
        if (p.headline?.id != alt.id) p.select(alt.id)
        adoptSelectedRoute(alt)
    }

    /** The trip follows the selected route from here. */
    fun adoptSelectedRoute(sel: PathOption? = planner?.headline) {
        val p = sel ?: return
        if (!started) return
        val plans = legPlans(p)
        data()?.setWanted(plans.flatMap { it.platformKeys.keys }.toSet(), "trip")
        recorder.replan(plans, p.transfer?.station)
        refreshRideArrival()
        publish()
    }

    private fun followBoarded() {
        val sel = planner?.headline ?: return
        if (recorder.phase != TripPhase.riding) return
        val leg = recorder.currentLeg
        val b = recorder.belief(leg) ?: return
        val key = b.bestKey ?: return
        val l = sel.legs.getOrNull(leg) ?: return
        if (key in l.keys) return
        if (!(b.byHand || (b.settled && b.confidence >= autoSwitchConfidence))) return
        if (!b.byHand && now() - lastSwitchTs < 60) return
        val alt = routeRiding(key, leg, sel) ?: return
        lastSwitchTs = now()
        switchRoute(alt)
    }

    private fun followAlighting(a: OffPlanAlighting) {
        val p = planner ?: return; val sched = sched() ?: return; val index = index() ?: return; val sel = p.headline ?: return
        val line = sched.lines[a.key] ?: return
        if (a.stopIdx >= line.stops.size) return
        val here = index.stationOf(line.stops[a.stopIdx])
        if (sel.legs.size > a.leg + 1 && index.stationOf(sel.legs[a.leg + 1].from) == here) return
        if (index.stationOf(sel.legs[a.leg].to) == here) return
        val origin = index.stationOf(sel.legs[0].from)
        fun fits(o: PathOption) = o.legs.size == a.leg + 2 && a.key in o.legs[a.leg].keys && index.stationOf(o.legs[a.leg].to) == here && index.stationOf(o.legs[0].from) == origin
        var alt = p.paths.firstOrNull(::fits)
        if (alt == null) { alt = evaluated(morePaths().filter(::fits)).minByOrNull { it.expectedSec }?.also { p.addExtra(it) } }
        val chosen = alt ?: return
        switchRoute(chosen)
        val next = chosen.legs[a.leg + 1]
        val boardIdx = next.idx.mapValues { it.value.from }; val alightIdx = next.idx.mapValues { it.value.to }
        val s = state() ?: return
        recorder.seedCurrentLeg(trainsAhead(s.boards, sched, boardIdx, alightIdx, next.keys.toSet(), s.now, 300.0), s.now)
    }

    private fun followStayingOn(st: StayedOn) {
        val p = planner ?: return; val s = state() ?: return; val sched = s.schedule ?: return; val index = s.index ?: return; val sel = p.headline ?: return
        if (st.leg >= sel.legs.size) return
        val line = sched.lines[st.key] ?: return
        val c = recorder.boardedCandidate ?: return
        if (c.trainId != st.trainId) return
        val progress = c.progressIdx ?: (st.pastIdx + 1)
        fun fits(o: PathOption): Boolean {
            val ix = o.legs.getOrNull(st.leg)?.idx?.get(st.key) ?: return false
            if (!(ix.to > st.pastIdx && ix.to >= progress && ix.to < line.stops.size)) return false
            if (index.stationOf(o.legs[st.leg].from) != index.stationOf(sel.legs[st.leg].from)) return false
            for (i in 0 until st.leg) {
                if (index.stationOf(o.legs[i].from) != index.stationOf(sel.legs[i].from) || index.stationOf(o.legs[i].to) != index.stationOf(sel.legs[i].to) || o.legs[i].keys.none { it in sel.legs[i].keys }) return false
            }
            return true
        }
        val options = p.paths.filter(::fits) + evaluated(morePaths().filter(::fits))
        fun arrival(o: PathOption) = ridingItinerary(s.predictedBoards, sched, o, st.leg, c, s.now)?.arriveTs ?: (s.now + o.expectedSec)
        val alt = options.minByOrNull { arrival(it) } ?: return
        if (p.paths.none { it.id == alt.id }) p.addExtra(alt)
        lastSwitchTs = s.now
        switchRoute(alt)
    }

    // the rider's own word

    val onTrainLeg: Int get() = if (started) recorder.currentLeg else 0

    /** Every route whose leg in hand starts where the rider boards it. */
    fun onTrainOptions(): List<PathOption> {
        val p = planner ?: return emptyList(); val sched = sched() ?: return emptyList(); val index = index() ?: return emptyList(); val sel = p.headline ?: return emptyList()
        val leg = onTrainLeg
        val l = sel.legs.getOrNull(leg) ?: return emptyList()
        val here = index.stationOf(l.from)
        val dir = l.primaryKey.substringAfter("_")
        val out = p.paths.filter { o -> o.legs.getOrNull(leg)?.let { index.stationOf(it.from) == here } == true }.toMutableList()
        val seen = out.map { it.id }.toSet()
        for (o in enumeratePaths(sched, index, p.originId, p.destId, 24)) {
            val ol = o.legs.getOrNull(leg) ?: continue
            if (o.id !in seen && index.stationOf(ol.from) == here && ol.keys.all { it.endsWith("_$dir") }) out.add(o)
        }
        return out
    }

    /** Opens the sheet: the lines leaving the boarding station are asked of the feeds. */
    fun prepareOnTrain() {
        data()?.setWanted(onTrainOptions().flatMap { o -> o.legs.flatMap { it.keys } }.toSet(), "onTrain")
    }

    fun trainsAheadNow(): List<BoardingCandidate> {
        val sched = sched() ?: return emptyList(); val s = state() ?: return emptyList(); val sel = planner?.headline ?: return emptyList()
        val leg = onTrainLeg
        if (leg >= sel.legs.size) return emptyList()
        val boardIdx = HashMap<String, Int>(); val alightIdx = HashMap<String, Int>()
        for (o in onTrainOptions()) for ((k, ix) in o.legs[leg].idx) if (k !in boardIdx) { boardIdx[k] = ix.from; alightIdx[k] = ix.to }
        return trainsAhead(s.boards, sched, boardIdx, alightIdx, sel.legs[leg].keys.toSet(), s.now)
    }

    data class TransferChoice(val key: String, val label: String)
    data class TransferChoices(val station: String, val choices: List<TransferChoice>)

    fun transferChoices(): TransferChoices? {
        val p = planner ?: return null; val sched = sched() ?: return null; val index = index() ?: return null; val sel = p.headline ?: return null
        if (onTrainLeg != 0 || sel.legs.size != 2) return null
        val tr = sel.transfer ?: return null
        val choices = ArrayList<TransferChoice>(); val seen = HashSet<String>()
        val firstKeys = sel.legs[0].keys.toSet()
        val options = p.paths + morePaths()
        for (o in options) {
            if (o.legs.size != 2 || index.stationOf(o.legs[1].from) != index.stationOf(sel.legs[1].from) || o.legs[0].keys.none { it in firstKeys }) continue
            for (k in o.legs[1].keys) if (seen.add(k)) choices.add(TransferChoice(k, o.label))
        }
        return TransferChoices(tr.station, choices)
    }

    /** The route a line leads to from the boarding station, for the sheet. */
    fun routeFor(key: String): String? {
        val sel = planner?.headline ?: return null
        val leg = onTrainLeg
        if (sel.legs.getOrNull(leg)?.keys?.contains(key) == true) return sel.label
        return routeRiding(key, leg, sel, adopt = false)?.label
    }

    /** The rider is on this train. */
    fun boardTrain(c: BoardingCandidate) {
        if (!started) startTrip("onboard")
        val sel = planner?.headline ?: return
        if (!started) return
        val leg = recorder.currentLeg
        if (sel.legs.getOrNull(leg)?.keys?.contains(c.key) == false) routeRiding(c.key, leg, sel)?.let { lastSwitchTs = now(); switchRoute(it) }
        recorder.setOnTrain(c, now())
        refreshRideArrival()
        publish()
    }

    fun onTransfer(key: String) {
        val sel = planner?.headline ?: return
        val alt = routeRiding(key, 1, sel) ?: return
        preferredKeys[1] = key
        switchRoute(alt)
    }

    fun setBoardedByHand(key: String) { recorder.setBoardedByHand(key); afterRecorderChange() }
    fun confirm(trainId: String) { recorder.confirm(trainId); afterRecorderChange() }
    fun dismissPrompt() { recorder.dismissPrompt(); publish() }
    fun notOnTrain() { recorder.notOnTrain(); refreshRideArrival(); publish() }

    /** The headline route changed by hand while the route is on: the trip follows it. */
    fun selectionChanged() { if (started) adoptSelectedRoute() }

    companion object {
        @Volatile private var instance: TripSession? = null
        fun get(context: Context): TripSession = instance ?: synchronized(this) { instance ?: TripSession(context).also { instance = it } }
    }
}
