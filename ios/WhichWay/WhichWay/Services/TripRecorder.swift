import Foundation
import Observation

/// One leg of the plan, as the line inference needs it: the lines the leg can use and where it boards and alights
/// on each, the train and line the plan picked, and the other lines at the boarding platform in the same
/// direction (a train the rider could board by mistake, or on purpose).
struct LegPlan {
    var keys: [String]
    var idx: [String: (from: Int, to: Int)]
    var chosenKey: String?
    var chosenTrainId: String?
    var platformKeys: [String: Int] = [:]     // other same-direction lines at the platform -> their boarding stop index
    var origin: String = ""                   // the trip, for the rider's own history of lines taken on it
    var dest: String = ""
    var walkSec: Double = 0                   // the walk before boarding this leg (0: the first leg, or a same-platform change)
    var otherPlatformKeys: Set<String> = []   // of platformKeys, those boarding at another platform of the station (the 8 Av L beside the A/C/E)
}

/// What the Go tab asks the rider when the phone is not sure which train they boarded, or believes they boarded a
/// train off the plan, or has only assumed the ride from the schedule.
/// The rider walked off the train somewhere else than the plan's change or destination: the stop on the line they
/// were on, by that train's own times. The planner re-routes from there.
struct OffPlanAlighting: Equatable {
    var leg: Int
    var key: String
    var stopIdx: Int
    var ts: Double
}

/// The rider stayed on past the stop where the route had them change (or get off): the train the phone believes
/// they are on has gone on beyond it, by the feed, and no walk-off was felt. The planner re-routes along that line.
struct StayedOn: Equatable {
    var leg: Int
    var key: String
    var trainId: String
    var pastIdx: Int          // the stop they were to leave the train at, on `key`
    var ts: Double
}

struct BoardingPrompt: Equatable {
    enum Reason: String { case unsure, switched, assumed }
    var leg: Int
    var reason: Reason
    var candidates: [BoardingCandidate]   // the trains that were at the platform, by time
    var bestTrainId: String?
    var bestKey: String?
    var chosenKey: String?
}

/// Runs a route in progress: the motion sensors (when either opt-in allows them), the location fixes and the
/// clock go into the trip tracker; the feeds' boards go into the departure log, so that when the sensors feel
/// the train pull away the phone can tell which train that was; when the trip ends, what it measured goes to the
/// rider's own pace model and, if shared, to the anonymous telemetry.
@MainActor
@Observable
final class TripRecorder {
    static let shared = TripRecorder()

    private(set) var phase: TripPhase?
    private(set) var endReason: String?
    private(set) var motionState: MotionState = .unknown
    private(set) var motionSeconds = 0
    private(set) var walkMeters = 0.0
    private(set) var rideAssumed = false
    /// Per leg, what the phone believes about the train the rider boarded, as far as the evidence has got.
    private(set) var beliefs: [LineBelief] = []
    /// Set when the rider walked off at a stop the route did not plan to leave the train at.
    private(set) var offPlanAlighting: OffPlanAlighting?
    /// Set when the rider rode on past the stop the route had them leave the train at.
    private(set) var stayedOn: StayedOn?
    @ObservationIgnored private var stayedOnChecked: Set<Int> = []
    @ObservationIgnored private var tracker: TripTracker?
    @ObservationIgnored private let sampler = MotionSampler()
    @ObservationIgnored private var legs: [LegPlan] = []
    @ObservationIgnored private var log = DepartureLog()
    @ObservationIgnored private var transferLog = DepartureLog()     // the next leg's lines at the transfer platform, followed during this leg
    @ObservationIgnored private var logLeg = -1
    @ObservationIgnored private var candidates: [Int: [BoardingCandidate]] = [:]
    @ObservationIgnored private var departedTs: [Int: Double] = [:]
    @ObservationIgnored private var departureBeliefs: [Int: LineBelief] = [:]   // per leg, the belief at the departure, before the ride's evidence
    @ObservationIgnored private var lastAlight: (leg: Int, ts: Double)?
    @ObservationIgnored private var locationApplied: Set<Int> = []
    @ObservationIgnored private let inference = LineInference()
    @ObservationIgnored private var seenEvents = 0
    @ObservationIgnored private var handSet: Set<Int> = []
    @ObservationIgnored private var dismissed: Set<Int> = []
    @ObservationIgnored private var departureChecked: Set<Int> = []
    /// A felt pull-away is checked against the feed this long after: time for a train that left then to show as gone.
    var departureCheckSec = 100.0
    /// Standing this long at a station, with the believed train gone past it, on a same-platform change: off the train.
    var samePlatformStandSec = 90.0
    /// Walking this long, with the steps begun while the believed train stood at a station by the feed: off the train.
    var quickChangeWalkSec = 8
    /// How far from the train's moment at the platform the steps may begin and still be the rider stepping off: a
    /// little before (the feed's time is an estimate), and up to the doors closing after.
    var quickChangeBeforeSec = 45.0
    var quickChangeAfterSec = 75.0
    /// The believed train must have gone this far past the alighting stop (the feed's time at the stop after it,
    /// at the platform) before the rider is taken to have stayed on.
    var stayedOnPastSec = 60.0
    /// Position of a stop on a line (line key, stop index), from the published geometry; set by the planner.
    @ObservationIgnored var stopCoordinate: ((String, Int) -> (lat: Double, lon: Double)?)?

    var sensing: Bool { sampler.isRunning }

    /// The belief about the ride in progress, or the last one.
    var currentBelief: LineBelief? { beliefs.last }
    func belief(leg: Int) -> LineBelief? { beliefs.first { $0.leg == leg } }
    /// The question for the rider, if there is one: the phone is unsure which train they boarded, believes they
    /// boarded one off the plan, or has only assumed the ride. Gone once they answer or wave it away.
    var prompt: BoardingPrompt? {
        guard let t = tracker, t.phase == .riding else { return nil }
        let leg = t.timeline.legsRidden
        guard leg < legs.count, !handSet.contains(leg), !dismissed.contains(leg), let b = belief(leg: leg) else { return nil }
        let reason: BoardingPrompt.Reason
        if b.assumed { reason = .assumed }
        else if !b.settled { reason = .unsure }
        else if b.verdict == .switched { reason = .switched }
        else { return nil }
        let cands = candidates[leg] ?? []
        return BoardingPrompt(leg: leg, reason: reason, candidates: cands, bestTrainId: b.bestTrain, bestKey: b.bestKey, chosenKey: legs[leg].chosenKey)
    }

    /// Riding with no idea which train (the tracker put the rider on a train from their movement, with no
    /// departure the feeds could match): the rider should be asked, once.
    var needsTrainPick: Bool {
        guard let t = tracker, t.phase == .riding else { return false }
        let leg = t.timeline.legsRidden
        return belief(leg: leg) == nil && (candidates[leg] ?? []).isEmpty && !dismissed.contains(leg) && !handSet.contains(leg)
    }

    /// On a train right now (not on the way, at the platform, or between trains at the change).
    var onTrain: Bool { tracker?.onTrain ?? false }

    /// The leg in hand (legs ridden so far) and the lines the route allows on it, for the rider's own say.
    var currentLeg: Int { tracker?.timeline.legsRidden ?? 0 }
    var currentLegKeys: [String] { currentLeg < legs.count ? legs[currentLeg].keys : [] }
    var currentLegChosenKey: String? { currentLeg < legs.count ? legs[currentLeg].chosenKey : nil }

    func begin(_ start: TripTimeline, distanceToOriginM: Double?, observation: TripObservation?, legs: [LegPlan] = [], now: Double) {
        if tracker != nil { end(by: "changed", api: nil, now: now) }
        tracker = TripTracker(start, distanceToOriginM: distanceToOriginM)
        endReason = nil
        self.legs = legs
        log.reset(); transferLog.reset(); logLeg = -1; candidates = [:]; beliefs = []; seenEvents = 0; handSet = []; dismissed = []
        departureBeliefs = [:]; departedTs = [:]; lastAlight = nil; locationApplied = []; departureChecked = []; offPlanAlighting = nil
        stayedOn = nil; stayedOnChecked = []
        publish()
        if let o = observation { Telemetry.shared.beginTrip(o) }
        MotionTrace.shared.begin(ts: start.startTs)
        if PersonalModelStore.shared.optIn || Telemetry.shared.optIn {
            sampler.start { [weak self] second in
                Task { @MainActor in self?.motion(second) }
            }
        }
    }

    private func motion(_ m: MotionSecond) {
        MotionTrace.shared.add(m)
        tracker?.motion(m)
        checkQuickChange()
        handleEvents()
        publish()
    }

    /// A walk that began while the train the phone believes the rider is on stood at a station, by the feed's
    /// times for its stops: the rider stepping off, even though the detector did not see the train come to rest
    /// first (people boarding keep a standing train shaking; a rider heading for the doors keeps the phone moving).
    /// Oct 8: both changes across the platform at Jay St were 11 to 12 s walks the detector let pass.
    private func checkQuickChange() {
        guard let t = tracker, t.phase == .riding, let walk = t.walking, walk.seconds >= quickChangeWalkSec,
              t.timeline.events.last?.kind == .departed, let c = trainInHand else { return }
        let lag = PlatformTiming.recordedLag(route: c.route)
        let atStop = c.stopTs.contains { idx, feedTs in
            idx > c.boardIdx && walk.startTs >= feedTs - lag - quickChangeBeforeSec && walk.startTs <= feedTs - lag + quickChangeAfterSec
        }
        guard atStop else { return }
        tracker?.alightWalking(at: walk.startTs)
    }

    /// The believed train has gone on past the stop the route had the rider leave it at (the feed has it at the
    /// stop after, a minute ago or more) and no walk-off was felt: the rider stayed on. Told once per leg; the
    /// planner re-routes along the line (the A on to Jay St for the F rather than the F from W 4 St).
    private func checkStayedOn(now: Double) {
        guard let t = tracker, t.phase == .riding else { return }
        let leg = t.timeline.legsRidden
        guard !stayedOnChecked.contains(leg), t.timeline.events.last?.kind == .departed, let c = trainInHand, let past = c.alightIdx,
              let progress = c.progressIdx, progress > past else { return }
        let lag = PlatformTiming.recordedLag(route: c.route)
        // the stop after the alighting stop, reached by the feed's reckoning a while ago
        guard let nextTs = c.stopTs.filter({ $0.key > past }).min(by: { $0.key < $1.key })?.value, now - (nextTs - lag) >= stayedOnPastSec else { return }
        // and the phone has felt the train move since the alighting stop went by (not standing on its platform)
        guard let moving = t.lastMovingTs, moving > (c.stopTs[past].map { $0 - lag } ?? 0) else { return }
        stayedOnChecked.insert(leg)
        stayedOn = StayedOn(leg: leg, key: c.key, trainId: c.trainId, pastIdx: past, ts: now)
    }

    /// A location fix. Soon after a walk-off, the fix says which station the rider is at: its distance to each
    /// candidate train's alighting stop is weighed into the leg's belief, once.
    func location(ts: Double, toOriginM: Double?, toDestM: Double?, now: Double, coordinate: (lat: Double, lon: Double)? = nil, accuracyM: Double? = nil) {
        tracker?.location(ts: ts, toOriginM: toOriginM, toDestM: toDestM, now: now, accuracyM: accuracyM)
        if (accuracyM ?? 0) > 100 { publish(); return }        // too unsure to say which stop the rider walked off at
        if let c = coordinate, let la = lastAlight, ts >= la.ts, now - la.ts <= 300, !locationApplied.contains(la.leg), !handSet.contains(la.leg),
           let b = belief(leg: la.leg), !b.assumed, let cands = candidates[la.leg], let sc = stopCoordinate {
            var dist: [String: Double] = [:]
            for cd in cands {
                // each train against the stop a rider on it would have walked off at (its own nearest stop, off the plan)
                if let ai = inference.alightingStop(cd, at: la.ts), let p = sc(cd.key, ai) { dist[cd.trainId] = haversineM((c.lat, c.lon), p) }
            }
            if !dist.isEmpty {
                locationApplied.insert(la.leg)
                store(inference.withLocation(b, candidates: cands, distanceM: dist))
            }
        }
        publish()
    }

    func updateForecast(boardTs: Double?, arriveTs: Double?, now: Double) {
        tracker?.updateForecast(boardTs: boardTs, arriveTs: arriveTs, now: now)
    }

    /// Every feed poll while the route is on: the boards of the leg in hand (the feed's own times, not the
    /// engine's) go into the departure log, which keeps each train's time at the platform after the feed has
    /// moved the train on.
    func observeBoards(_ boards: [String: LineBoard], now: Double) {
        guard let t = tracker, t.phase != .arrived else { return }
        let leg = t.timeline.legsRidden
        guard leg < legs.count else { return }
        if leg != logLeg {
            // on to the next leg: the log that followed its lines at the transfer platform during the last leg takes over
            log = (logLeg >= 0 && leg == logLeg + 1) ? transferLog : DepartureLog()
            transferLog = DepartureLog()
            logLeg = leg
        }
        observe(legs[leg], boards: boards, into: &log, now: now)
        if leg + 1 < legs.count { observe(legs[leg + 1], boards: boards, into: &transferLog, now: now) }
        rideEvidence(now: now)
        checkDepartureMade(now: now)
        checkSamePlatformChange(now: now)
        checkStayedOn(now: now)
    }

    /// The train the plan has the rider on for each leg still to board, as the planner's itinerary stands now:
    /// kept up until the leg's boarding time passes (the planner then moves on to the next train, but the rider
    /// presumably took this one), so a ride assumed from the schedule, the candidates' `chosen` mark and the
    /// plan's fallback all name the train actually in question.
    func updatePlannedTrains(_ trains: [Int: (key: String, trainId: String)], now: Double) {
        guard let t = tracker, t.phase != .arrived else { return }
        let riding = t.phase == .riding ? t.timeline.legsRidden : -1
        for (leg, tr) in trains where leg < legs.count && leg >= t.timeline.legsRidden && leg != riding && departedTs[leg] == nil {
            guard legs[leg].keys.contains(tr.key) else { continue }
            legs[leg].chosenKey = tr.key
            legs[leg].chosenTrainId = tr.trainId
        }
    }

    /// A felt pull-away that no train made. About a minute and a half on, the feed has had time to show a train that
    /// left then as gone past the platform. If none did, while other trains the log follows are still to come (the
    /// feed is alive here), it was the platform shaking (a train on the next track, the stairs), not the rider's
    /// train: withdrawn once per leg, and the next pull-away counts afresh. Seen twice on Oct 7: at 4 Av-9 St three
    /// minutes before the G the rider took, and at Hoyt as the G they had just left pulled out. Not for a ride the
    /// rider named or the phone's movement began.
    private func checkDepartureMade(now: Double) {
        guard let t = tracker, t.phase == .riding, !t.departedByLocation else { return }
        let leg = t.timeline.legsRidden
        guard leg < legs.count, !handSet.contains(leg), !departureChecked.contains(leg), let dep = departedTs[leg], now - dep >= departureCheckSec else { return }
        guard now - dep <= departureCheckSec + 240 else { departureChecked.insert(leg); return }   // too long ago to say
        guard log.lastPollTs >= dep + 75 else { return }                                            // the feed has not been read since
        departureChecked.insert(leg)
        let check = log.departureCheck(at: dep)
        guard !check.left, check.waiting else { return }
        tracker?.withdrawDeparture()
        beliefs.removeAll { $0.leg == leg }
        departureBeliefs[leg] = nil; candidates[leg] = nil; departedTs[leg] = nil
        departureChecked.remove(leg)
        seenEvents = tracker?.timeline.events.count ?? 0
        publish()
    }

    private func observe(_ plan: LegPlan, boards: [String: LineBoard], into log: inout DepartureLog, now: Double) {
        for k in plan.keys {
            guard let b = boards[k], let span = plan.idx[k] else { continue }
            log.observe(trains: b.trains, key: k, boardIdx: span.from, span: span, onLeg: true, now: now)
        }
        for (k, bi) in plan.platformKeys where !plan.keys.contains(k) {
            guard let b = boards[k] else { continue }
            log.observe(trains: b.trains, key: k, boardIdx: bi, span: nil, onLeg: false, now: now, samePlatform: !plan.otherPlatformKeys.contains(k))
        }
    }

    /// Every poll while riding: the stops felt so far, and when, against each candidate train's actual progress
    /// and stop times from the feed, recomputed from the departure belief.
    private func rideEvidence(now: Double) {
        guard let t = tracker, t.phase == .riding else { return }
        let leg = t.timeline.legsRidden
        guard let cands = candidates[leg], !cands.isEmpty else { return }
        let fresh = log.refreshed(cands)
        candidates[leg] = fresh
        guard !handSet.contains(leg), let base = departureBeliefs[leg] else { return }
        let b = inference.withRide(base, candidates: fresh, stopTimes: t.stopTimes, now: now)
        if b != belief(leg: leg) { store(b) }
    }

    /// A same-platform change the sensors cannot see (no walk): the phone has stood still at a station for a
    /// while and the feed has moved the believed train on past the transfer stop, so the rider is on the platform.
    private func checkSamePlatformChange(now: Double) {
        guard let t = tracker, t.phase == .riding else { return }
        let leg = t.timeline.legsRidden
        guard leg + 1 < legs.count, legs[leg + 1].walkSec <= 0, let since = t.standingSince, now - since >= samePlatformStandSec,
              let b = belief(leg: leg), let best = b.bestTrain, let cand = candidates[leg]?.first(where: { $0.trainId == best }),
              let alightIdx = cand.alightIdx, let progress = log.progressIdx(of: best), progress > alightIdx else { return }
        tracker?.alightStanding(at: since)
        handleEvents()
        publish()
    }

    /// The tracker's new events: a departure starts a belief for the leg from the trains that were at the
    /// platform; an alighting corroborates it with the stops felt and the arrival time.
    private func handleEvents() {
        guard let t = tracker else { return }
        let events = t.timeline.events
        while seenEvents < events.count {
            let e = events[seenEvents]
            seenEvents += 1
            switch e.kind {
            case .departed:
                let leg = t.timeline.legsRidden
                guard leg < legs.count else { continue }
                let plan = legs[leg]
                let cands = log.candidates(departedTs: e.ts, windowSec: inference.departureWindowSec, chosenTrainId: plan.chosenTrainId)
                candidates[leg] = cands
                departedTs[leg] = e.ts
                guard !handSet.contains(leg) else { continue }
                let share = plan.chosenKey.flatMap {
                    PersonalModelStore.shared.model.chosenLineShare(origin: plan.origin, dest: plan.dest, leg: leg, chosenKey: $0)
                }
                let b = inference.fromDeparture(cands, departedTs: e.ts, leg: leg, chosenKey: plan.chosenKey, learnedShare: share)
                // a departure felt with no train in the feed to match it: the plan stands, as a guess
                if b.byTrain.isEmpty, let fallback = LineBelief.fromPlan(leg: leg, chosenKey: plan.chosenKey, chosenTrainId: plan.chosenTrainId, evidence: "plan") {
                    store(fallback)
                } else {
                    departureBeliefs[leg] = b
                    store(b)
                }
            case .alighted:
                let leg = t.timeline.legsRidden - 1
                lastAlight = (leg, e.ts)
                if leg >= 0, handSet.contains(leg) { checkAlightingStop(leg: leg, ts: e.ts) }
                guard leg >= 0, !handSet.contains(leg), let current = belief(leg: leg), !current.assumed, let known = candidates[leg] else { continue }
                let cands = log.refreshed(known)
                candidates[leg] = cands
                // the ride's evidence first (from the departure belief), then the stop count and the arrival time
                var b = departureBeliefs[leg].map { inference.withRide($0, candidates: cands, stopTimes: t.lastRideStopTimes, now: e.ts) } ?? current
                if let stops = t.timeline.rideStops.last { b = inference.withStops(b, candidates: cands, stopsFelt: stops, departedTs: departedTs[leg], alightedTs: e.ts) }
                store(inference.withAlighting(b, candidates: cands, alightedTs: e.ts))
                checkAlightingStop(leg: leg, ts: e.ts)
            }
        }
    }

    /// Where the rider walked off, by the train the phone settled on (or the rider named): the stop whose time, put
    /// back to the platform moment, is nearest the walk-off. Off the plan's own stop, the planner hears of it.
    private func checkAlightingStop(leg: Int, ts: Double) {
        guard let b = belief(leg: leg), b.settled || b.byHand, let id = b.bestTrain,
              let c = log.refreshed((candidates[leg] ?? []).filter { $0.trainId == id }).first else { return }
        let walkOff = { (feedTs: Double) in PlatformTiming.atPlatform(feedTs, route: c.route) + self.inference.alightLagSec }
        guard let near = c.stopTs.filter({ $0.key > c.boardIdx }).min(by: { abs(walkOff($0.value) - ts) < abs(walkOff($1.value) - ts) }),
              abs(walkOff(near.value) - ts) <= 150 else { return }
        if let planned = legs[leg].idx[c.key]?.to, planned == near.key { return }
        offPlanAlighting = OffPlanAlighting(leg: leg, key: c.key, stopIdx: near.key, ts: ts)
    }

    private func store(_ b: LineBelief) {
        if let i = beliefs.firstIndex(where: { $0.leg == b.leg }) { beliefs[i] = b } else { beliefs.append(b) }
        if let bl = b.boarded { tracker?.setBoarded(bl) }
    }

    /// The ride the tracker assumed from the schedule (boarding time past, no departure felt) is the plan's
    /// train until the sensors or the rider say otherwise.
    private func checkAssumed() {
        guard let t = tracker, t.phase == .riding, t.timeline.rideAssumed else { return }
        let leg = t.timeline.legsRidden
        guard leg < legs.count, belief(leg: leg) == nil,
              let b = LineBelief.fromPlan(leg: leg, chosenKey: legs[leg].chosenKey, chosenTrainId: legs[leg].chosenTrainId, evidence: "schedule") else { return }
        store(b)
    }

    /// The rider's own word on the line they boarded for the leg in hand; nothing measured later overrides it.
    func setBoardedByHand(key: String) {
        let leg = currentLeg
        guard leg < legs.count else { return }
        handSet.insert(leg)
        store(LineBelief.byHand(leg: leg, key: key, chosenKey: legs[leg].chosenKey, candidates: candidates[leg] ?? [], departedTs: departedTs[leg]))
    }

    /// The rider confirms one particular train (from the prompt's candidates).
    func confirm(trainId: String) {
        let leg = currentLeg
        guard leg < legs.count, let c = (candidates[leg] ?? []).first(where: { $0.trainId == trainId }) else { return }
        handSet.insert(leg)
        store(LineBelief(leg: leg, byTrain: [c.trainId: 1.0], byLine: [c.key: 1.0], chosenKey: legs[leg].chosenKey, evidence: ["hand"]))
    }

    /// The rider waves the question away; the phone's own estimate stands.
    func dismissPrompt() { dismissed.insert(currentLeg) }

    /// The rider says which train they are on, from the trains that have left the platform (a route started from
    /// the train, or one whose pull-away went unfelt, or a wrong guess put right): the ride is on from that train's
    /// departure if it was not already, the train is the leg's belief, by hand, and the departure log follows it
    /// from here on, so the arrival clock and the alighting read its progress.
    func setOnTrain(_ c: BoardingCandidate, now: Double) {
        guard var t = tracker, t.phase != .arrived else { return }
        let leg = t.timeline.legsRidden
        guard leg < legs.count else { return }
        if t.phase != .riding {
            t.beginRiding(now: now, departedTs: PlatformTiming.pullsAway(c.boardTs, route: c.route))
            tracker = t
            seenEvents = t.timeline.events.count          // the departure on the record is this one, not one to infer from
        }
        if logLeg != leg { log = leg == logLeg + 1 ? transferLog : DepartureLog(); transferLog = DepartureLog(); logLeg = leg }
        log.seed(c, now: now)
        var cands = (candidates[leg] ?? []).filter { $0.trainId != c.trainId }
        cands.append(c)
        candidates[leg] = cands.sorted { $0.boardTs != $1.boardTs ? $0.boardTs < $1.boardTs : $0.trainId < $1.trainId }
        departedTs[leg] = PlatformTiming.pullsAway(c.boardTs, route: c.route)
        handSet.insert(leg); dismissed.remove(leg)
        store(LineBelief(leg: leg, byTrain: [c.trainId: 1.0], byLine: [c.key: 1.0], chosenKey: legs[leg].chosenKey, evidence: ["hand"]))
        publish()
    }

    /// The rider says they are not on a train: the felt departure is withdrawn and the leg starts over at the platform.
    func notOnTrain() {
        let leg = currentLeg
        tracker?.notOnTrain()
        if let i = beliefs.firstIndex(where: { $0.leg == leg }) { beliefs.remove(at: i) }
        departureBeliefs[leg] = nil; candidates[leg] = nil; departedTs[leg] = nil
        handSet.remove(leg); dismissed.remove(leg)
        seenEvents = tracker?.timeline.events.count ?? 0
        publish()
    }

    /// The route changed under the rider (they boarded a line off the plan and the planner found the route that
    /// rides it): the legs from here on follow the new plan; the belief about the leg in hand is kept and now
    /// counts as on-plan.
    func replan(legs newLegs: [LegPlan], transferStation: String? = nil) {
        let leg = currentLeg
        let old = legs
        legs = newLegs
        transferLog = DepartureLog()
        // the leg in hand now boards somewhere else (the rider changed at another station): its trains are followed
        // afresh there, and a pull-away already felt on it is read again against them
        if leg < newLegs.count, leg < old.count, boardingStops(old[leg]) != boardingStops(newLegs[leg]) {
            log = DepartureLog(); logLeg = leg
            candidates[leg] = nil; departureBeliefs[leg] = nil; departureChecked.remove(leg)
            if !handSet.contains(leg) { beliefs.removeAll { $0.leg == leg } }
        }
        if leg < legs.count, let b = belief(leg: leg) {
            var nb = b; nb.chosenKey = legs[leg].chosenKey
            store(nb)
            if let d = departureBeliefs[leg] { var nd = d; nd.chosenKey = legs[leg].chosenKey; departureBeliefs[leg] = nd }
        }
        dismissed.remove(leg)
        stayedOn = nil
        tracker?.replan(legs: max(1, newLegs.count), transferStation: transferStation)
        publish()
    }

    private func boardingStops(_ p: LegPlan) -> [String: Int] {
        var out: [String: Int] = [:]
        for (k, v) in p.idx { out[k] = v.from }
        return out
    }

    /// Trains that already left the leg's (new) boarding platform, taken up by the log as if it had seen them there:
    /// the rider may have stepped straight onto one. A pull-away already felt on the leg is read against them.
    func seedCurrentLeg(_ cands: [BoardingCandidate], now: Double) {
        guard let t = tracker else { return }
        let leg = t.timeline.legsRidden
        guard leg < legs.count else { return }
        if logLeg != leg { log = DepartureLog(); transferLog = DepartureLog(); logLeg = leg }
        for c in cands { log.seed(c, now: now) }
        guard !handSet.contains(leg), let dep = departedTs[leg], t.phase == .riding else { return }
        let plan = legs[leg]
        let cs = log.candidates(departedTs: dep, windowSec: inference.departureWindowSec, chosenTrainId: plan.chosenTrainId)
        candidates[leg] = cs
        let b = inference.fromDeparture(cs, departedTs: dep, leg: leg, chosenKey: plan.chosenKey)
        if !b.byTrain.isEmpty { departureBeliefs[leg] = b; store(b) }
        publish()
    }

    /// The arrival as the train the rider is on makes it, from the planner: the clock the trip ends by.
    func setLiveArrival(_ ts: Double) { tracker?.setLiveArrival(ts) }

    /// The train the phone believes the rider is on for the leg in hand (a settled belief or the rider's word),
    /// with the feed's times the departure log has kept for it; nil at the platform or while the phone is unsure.
    var boardedCandidate: BoardingCandidate? {
        guard let t = tracker, t.phase == .riding else { return nil }
        let leg = t.timeline.legsRidden
        guard let b = belief(leg: leg), b.settled || b.byHand, let id = b.bestTrain else { return nil }
        return (candidates[leg] ?? []).first { $0.trainId == id } ?? log.entry(id, chosenTrainId: legs[leg].chosenTrainId)
    }

    /// The train the rider is on as far as the phone can say: the one it settled on or the rider named, else the
    /// plan's train for the leg as the log has followed it (the ride felt but no train matched, or none settled).
    private var trainInHand: BoardingCandidate? {
        if let c = boardedCandidate { return c }
        guard let t = tracker, t.phase == .riding else { return nil }
        let leg = t.timeline.legsRidden
        guard leg < legs.count, let id = legs[leg].chosenTrainId else { return nil }
        return log.entry(id, chosenTrainId: id)
    }

    func tick(now: Double) {
        tracker?.tick(now: now)
        checkAssumed()
        publish()
    }

    private func publish() {
        guard let t = tracker else { return }
        if phase != t.phase { phase = t.phase }
        if motionState != t.motionState { motionState = t.motionState }
        motionSeconds = t.timeline.motionSeconds
        walkMeters = t.timeline.walkMeters
        rideAssumed = t.timeline.rideAssumed
        endReason = t.timeline.endedBy
    }

    /// Ends the trip and hands what it measured on: to the pace model when learning is on, to the telemetry
    /// when sharing is on.
    @discardableResult
    func end(by: String, api: URL?, now: Double) -> TripTimeline? {
        sampler.stop()
        guard var t = tracker else { return nil }
        let tl = t.end(now: now, by: by)
        tracker = nil
        legs = []
        log.reset(); transferLog.reset(); candidates = [:]; departedTs = [:]; handSet = []
        departureBeliefs = [:]; lastAlight = nil; locationApplied = []; departureChecked = []; offPlanAlighting = nil
        stayedOn = nil; stayedOnChecked = []
        phase = nil
        motionState = .unknown
        endReason = tl.endedBy
        PersonalModelStore.shared.learn(tl)
        Telemetry.shared.endTrip(timeline: tl, api: api)
        MotionTrace.shared.end(tl)
        return tl
    }
}

