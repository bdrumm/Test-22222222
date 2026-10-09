import Foundation

/// Where a route in progress stands: heading to the origin station, at it, on the train, or done.
enum TripPhase: String, Codable { case approaching, atStation, riding, arrived }

/// What one trip looked like, as the phone saw it: when the rider reached the station, stopped walking (the
/// platform), the train left and the rider walked off; how far and how long they walked on the street; the
/// forecast they acted on. Feeds the personal model and, if shared, the anonymous telemetry.
struct TripTimeline: Codable, Equatable {
    var startTs: Double
    var startedBy: String                 // "gps" | "hand"
    var startDistanceM: Double?           // to the origin station when the route started
    var placeId: String?                  // a pinned place the rider started from, if any
    var originStation: String
    var destStation: String
    var transferStation: String?
    var legs: Int
    var arrivedStationTs: Double?         // within the station radius, or the first departure the sensors saw
    var platformTs: Double?               // walking stopped after arriving: on the platform
    var platformObserved = false          // platformTs came from the sensors (else from the departure itself)
    var walkMeters = 0.0                  // on the street, toward the station
    var walkSeconds = 0.0
    var events: [MotionEvent] = []
    var motionSeconds = 0
    var walkingSecondsBetweenTrains = 0.0
    var rideAssumed = false               // no sensors: riding assumed once the boarding time had passed
    var forecastBoardTs: Double?          // frozen when the sensors see the train leave
    var forecastArriveTs: Double?
    var liveArriveTs: Double?             // keeps following the planner, for ending the trip
    var endedTs: Double?
    var endedBy: String?                  // arrived | alighted | walked | timeout | hand | changed | off
    var rideStops: [Int] = []             // station stops felt on each ride, the alighting stop included
    var boarded: [BoardedLeg] = []        // the train and line each leg was ridden on, as far as the phone could tell
    var withdrawnDepartures: [Double]? = nil   // pull-aways felt that no train made (the platform shaking), withdrawn

    /// Legs ridden so far: the number of alightings the sensors saw.
    var legsRidden: Int { events.filter { $0.kind == .alighted }.count }

    /// Seconds from the station radius to standing on the platform, when the sensors saw both.
    var accessSec: Double? {
        guard platformObserved, let a = arrivedStationTs, let p = platformTs, p >= a else { return nil }
        return p - a
    }

    /// Seconds walking between leaving the first train and the second one leaving, on a two-leg trip.
    var transferWalkSec: Double? {
        guard legs > 1, let off = events.firstIndex(where: { $0.kind == .alighted }),
              events[(off + 1)...].contains(where: { $0.kind == .departed }) else { return nil }
        return walkingSecondsBetweenTrains
    }

    /// The first departure within five minutes of the forecast boarding time.
    var corroboratedDepartureTs: Double? {
        guard let b = forecastBoardTs else { return nil }
        return events.first { $0.kind == .departed && abs($0.ts - b) <= 300 }?.ts
    }

    var walkSpeedMPerMin: Double? { walkMeters >= 150 && walkSeconds >= 60 ? walkMeters / walkSeconds * 60 : nil }
}

/// Follows a route in progress from location fixes, summarised motion and the clock, and decides when it is
/// over so the rider need not say so. A route may start anywhere: the rider walks up to the station
/// (approaching), reaches it (at the station), the train leaves (riding), and they walk off at the end.
struct TripTracker {
    var stationRadiusM = 150.0
    var destRadiusM = 250.0
    var platformStillSec = 20             // standing still this long after arriving: on the platform
    var walkOffSec = 180                  // walking this long near the end of the ride: off the train
    var lateGraceSec = 600.0              // this long past the expected arrival with no sign: over
    var assumeRideAfterSec = 120.0        // riding once the boarding time is this far past with no departure felt
    var stopQuietSec = 12                 // riding, no push and no shake this long: the train is standing at a station
    var startGraceSec = 15.0              // a "departure" this soon after sensing began is the phone being put away
    var destFarRadiusM = 800.0            // this close to the destination once the expected arrival is well past: over
    var awaySpeedMps = 4.0                // on the way to the station yet moving away from it this fast, for this long: on a train already
    var awaySeconds = 30.0
    var usableAccuracyM = 100.0           // a fix less sure than this says nothing about where the rider is
    var preciseAccuracyM = 65.0           // speeds (the walk, a train pulling away) only from fixes this sure

    private(set) var phase: TripPhase
    private(set) var timeline: TripTimeline
    private(set) var motionState: MotionState = .unknown
    private var detector = BoardingDetector()
    private var stillRun = 0
    private var walkRun = 0
    private var afterAlight = false
    private var alightedAll = false
    private var lastFix: (ts: Double, toOrigin: Double, acc: Double)?
    private var awayRun = 0.0
    private var lastDest: (ts: Double, toDest: Double)?
    private var hasMotion = false
    private var firstMotionTs: Double?
    private var quietRun = 0
    /// Station stops felt on the ride in progress (the train standing still, then moving again).
    private(set) var stopsFelt = 0
    /// When each station stop of the ride in progress began (the stop in hand included), for the line inference.
    private(set) var stopTimes: [Double] = []
    /// The stop times of the ride that just ended, kept until the next departure.
    private(set) var lastRideStopTimes: [Double] = []
    private var quietStartTs: Double?
    private var walkStartTs: Double?

    /// The ride in hand began from the phone's movement (moving away from the station at a train's pace), not
    /// from a felt pull-away at the platform.
    private(set) var departedByLocation = false

    /// Riding and standing at a station since this moment (nil while moving).
    var standingSince: Double? { phase == .riding && quietRun >= stopQuietSec ? quietStartTs : nil }

    /// On a train right now: the ride's pull-away was felt (or named, or assumed from the timetable) and no walk-off
    /// since. False between trains at the change, where the phase is still `.riding`.
    var onTrain: Bool { phase == .riding && (timeline.rideAssumed || timeline.events.last?.kind == .departed) }

    /// Riding and walking right now: when the steps began and for how many seconds they have gone on. The
    /// recorder reads it against the feed: steps that begin while the believed train stands at a station are
    /// the rider getting off, even when the detector could not see the train come to rest first.
    var walking: (startTs: Double, seconds: Int)? {
        guard phase == .riding, walkRun > 0, let s = walkStartTs else { return nil }
        return (s, walkRun)
    }

    /// The last second the phone felt the train moving (a push or vibration) on the ride in hand.
    private(set) var lastMovingTs: Double?

    init(_ start: TripTimeline, distanceToOriginM: Double?) {
        timeline = start
        if let d = distanceToOriginM, d <= stationRadiusM {
            phase = .atStation
            timeline.arrivedStationTs = start.startTs
        } else {
            phase = .approaching
        }
    }

    /// A location fix: on the way, the distance closed toward the station at a walking pace is the rider's
    /// street speed; within the radius the rider is at the station; near the destination the trip is over.
    /// `accuracyM` is the fix's own uncertainty: one worse than `usableAccuracyM` (a fix from cell towers, or
    /// one under a station roof) changes nothing, and speeds come only from `preciseAccuracyM` fixes. Unknown
    /// accuracy counts as precise (the package tests, a simulator).
    mutating func location(ts: Double, toOriginM: Double?, toDestM: Double?, now: Double, accuracyM: Double? = nil) {
        guard phase != .arrived else { return }
        let acc = accuracyM.map { $0 < 0 ? Double.infinity : $0 } ?? 0
        guard acc <= usableAccuracyM else { checkDestination(now: now); return }
        if phase == .approaching, let d = toOriginM {
            if let last = lastFix, ts > last.ts, max(acc, last.acc) <= preciseAccuracyM {
                let dt = ts - last.ts, gained = last.toOrigin - d
                if dt >= 5, dt <= 120, gained > 0, gained / dt >= 0.4, gained / dt <= 2.5 {
                    timeline.walkMeters += gained
                    timeline.walkSeconds += dt
                }
                // past the station and pulling away from it at a train's pace: the rider is on a train already (a route
                // started on the way); which train is for the rider or the feeds to say
                if dt >= 5, dt <= 180, d > stationRadiusM * 2, -gained / dt >= awaySpeedMps {
                    awayRun += dt
                    if awayRun >= awaySeconds {
                        lastFix = (ts, d, acc)
                        beginRiding(now: now, departedTs: ts - awayRun)
                        departedByLocation = true
                        checkDestination(now: now)
                        return
                    }
                } else {
                    awayRun = 0
                }
            }
            lastFix = (ts, d, acc)
            // within the radius, give or take half the fix's own uncertainty
            if d <= stationRadiusM + acc / 2 {
                phase = .atStation
                timeline.arrivedStationTs = ts
                stillRun = 0
            }
        }
        if let dd = toDestM { lastDest = (ts, max(0, dd - acc / 2)) }
        checkDestination(now: now)
    }

    /// Within 250 m of the destination station, on a fix no older than ten minutes, once the expected arrival is
    /// within five minutes (or, riding with no forecast at all, at once): the trip is over. The phone sitting
    /// still at the destination sends no new fix, so the clock asks too.
    private mutating func checkDestination(now: Double) {
        guard phase != .arrived, let d = lastDest, now - d.ts <= 600 else { return }
        if d.toDest <= destRadiusM {
            if let pa = timeline.liveArriveTs {
                if now >= pa - 300 { finish(now, by: "arrived") }
            } else if phase == .riding {
                finish(now, by: "arrived")
            }
        } else if d.toDest <= destFarRadiusM, let pa = timeline.liveArriveTs, now >= pa + 120, phase == .riding || phase == .atStation {
            // well past the expected arrival and within a short walk of the destination: the rider has come and gone
            finish(now, by: "arrived")
        }
    }

    /// One summarised second of motion.
    mutating func motion(_ m: MotionSecond) {
        guard phase != .arrived else { return }
        hasMotion = true
        if firstMotionTs == nil { firstMotionTs = m.ts }
        timeline.motionSeconds += 1
        let walking = m.stepEnergy > detector.stepThreshold
        if walking {
            if walkRun == 0 { walkStartTs = m.ts }
            walkRun += 1; stillRun = 0
        } else {
            stillRun += 1; walkRun = 0; walkStartTs = nil
        }
        if afterAlight, walking { timeline.walkingSecondsBetweenTrains += 1 }
        if phase == .riding, !walking, m.pushG >= detector.pushThreshold || m.shakeG >= detector.shakeThreshold { lastMovingTs = m.ts }
        if phase == .atStation, timeline.platformTs == nil, stillRun >= platformStillSec {
            timeline.platformTs = m.ts - Double(platformStillSec - 1)
            timeline.platformObserved = true
        }
        let riding = detector.state == .riding
        if let e = detector.feed(m) {
            // the first seconds after sensing starts are the rider tapping Start and pocketing the phone, not a train
            if e.kind == .departed, let f = firstMotionTs, m.ts - f < startGraceSec {
                detector.standDown()
                motionState = detector.state
                return
            }
            // still on the way to the station by a fresh fix: a push on the street (a bus, a car, a jog) is not a train
            if e.kind == .departed, phase == .approaching, let f = lastFix, m.ts - f.ts < 120, f.toOrigin - f.acc > 300 {
                detector.standDown()
                motionState = detector.state
                return
            }
            timeline.events.append(e)
            switch e.kind {
            case .departed:
                afterAlight = false
                departedByLocation = false
                stopsFelt = 0; quietRun = 0; stopTimes = []; quietStartTs = nil
                timeline.rideAssumed = false          // a ride assumed from the schedule is now a ride felt
                if phase != .riding {
                    if timeline.arrivedStationTs == nil { timeline.arrivedStationTs = e.ts }
                    if timeline.platformTs == nil { timeline.platformTs = e.ts }
                    phase = .riding
                }
            case .alighted:
                afterAlight = true
                if quietRun >= stopQuietSec { stopsFelt += 1 }    // the stop the rider got off at
                timeline.rideStops.append(stopsFelt)
                lastRideStopTimes = stopTimes
                stopsFelt = 0; quietRun = 0; stopTimes = []; quietStartTs = nil
                timeline.walkingSecondsBetweenTrains += Double(walkRun)   // the steps that confirmed the alighting
                if timeline.events.filter({ $0.kind == .alighted }).count >= timeline.legs { alightedAll = true }
            }
        } else if riding {
            // a station stop: a quiet spell (no push, no shake, no steps) long enough to be a dwell, then motion again
            let quiet = !walking && m.pushG < detector.pushThreshold && m.shakeG < detector.shakeThreshold
            if quiet {
                quietRun += 1
                if quietRun == stopQuietSec {
                    let start = m.ts - Double(stopQuietSec - 1)
                    quietStartTs = start
                    stopTimes.append(start)
                }
            } else {
                if quietRun >= stopQuietSec { stopsFelt += 1 }
                quietRun = 0; quietStartTs = nil
            }
        }
        motionState = detector.state
        if phase == .riding, walkRun >= walkOffSec, let pa = timeline.liveArriveTs, m.ts >= pa - 180 {
            finish(m.ts, by: "walked")
        }
    }

    /// The clock: confirms an arrival the sensors saw, assumes the ride from the schedule when no departure was
    /// felt, and gives up well past the expected arrival.
    ///
    /// The assumption: a chosen route is taken as planned. At the station, once the boarding time of the train
    /// the rider was waiting for is two minutes past, they are on it, whether the sensors are off or just missed
    /// the pull-away (a phone deep in a bag). With sensors the rider must have reached the platform (stood still
    /// after arriving) first, so someone still on the stairs is not put on a train. A departure felt later turns
    /// the assumed ride into a measured one, and the line inference re-reads which train it was.
    mutating func tick(now: Double) {
        guard phase != .arrived else { return }
        let pa = timeline.liveArriveTs
        if alightedAll, pa == nil || now >= pa! - 120 { finish(now, by: "alighted"); return }
        if phase == .atStation, let pb = timeline.forecastBoardTs, now >= pb + assumeRideAfterSec, !hasMotion || timeline.platformTs != nil {
            phase = .riding
            timeline.rideAssumed = true
            if timeline.platformTs == nil { timeline.platformTs = pb }
        }
        checkDestination(now: now)
        if phase != .arrived, let pa = pa, now >= pa + lateGraceSec { finish(now, by: "timeout") }
    }

    /// The planner's latest forecast. It stops following once the rider is on the train, or is at the
    /// station when the boarding time passes (the planner then moves on to the next train, but the rider
    /// presumably took this one); on the way to the station it keeps following.
    mutating func updateForecast(boardTs: Double?, arriveTs: Double?, now: Double) {
        guard phase != .riding, phase != .arrived else { return }
        if phase == .atStation, let b = timeline.forecastBoardTs, now >= b { return }
        timeline.liveArriveTs = arriveTs ?? timeline.liveArriveTs
        timeline.forecastBoardTs = boardTs
        timeline.forecastArriveTs = arriveTs
    }

    /// The train the forecast named has gone from the feed a little before its predicted platform moment (the feed
    /// moves a train on as it leaves, and its ETA for the stop ran a few seconds optimistic: Oct 9, gone at 3:00:49
    /// against 3:01:05). At the station, that is the train leaving now: the forecast freezes here and the assumed
    /// ride is counted from this moment. Without this, `updateForecast` follows the planner on to the next train,
    /// the predicted time never having passed, and with no felt departure the ride is never assumed. `graceSec`
    /// bounds how early a drop may come and still be that train leaving; a train gone five minutes ahead of its
    /// time was withdrawn, not taken.
    mutating func forecastTrainDeparted(now: Double, graceSec: Double = 90) {
        guard phase == .atStation, let b = timeline.forecastBoardTs, b > now, b - now <= graceSec else { return }
        timeline.forecastBoardTs = now
    }

    /// The rider got off without walking: a same-platform change, told by the feed (the train they were on has
    /// moved past the transfer stop while the phone has stood still there since `ts`). Closes the ride as an
    /// alighting at `ts`; the next push or vibration the sensors feel is the next train leaving.
    mutating func alightStanding(at ts: Double) {
        guard phase == .riding else { return }
        if quietRun >= stopQuietSec { stopsFelt += 1 }
        timeline.rideStops.append(stopsFelt)
        lastRideStopTimes = stopTimes
        stopsFelt = 0; quietRun = 0; stopTimes = []; quietStartTs = nil
        afterAlight = true
        timeline.events.append(MotionEvent(kind: .alighted, ts: ts))
        detector.standDown()
        motionState = detector.state
        if timeline.events.filter({ $0.kind == .alighted }).count >= timeline.legs { alightedAll = true }
    }

    /// The rider walked off at a station the feed vouches for: the steps that began at `ts` started while the train
    /// the phone believes they were on stood at a stop, so they are the rider getting off even though the detector
    /// never saw the train come to rest (a quick change across the platform, Oct 8 at Jay St twice). Closes the ride
    /// as an alighting at `ts`; the walk goes on as the change, and the next pull-away is the next train.
    mutating func alightWalking(at ts: Double) {
        guard phase == .riding else { return }
        if quietRun >= stopQuietSec { stopsFelt += 1 }
        timeline.rideStops.append(stopsFelt)
        lastRideStopTimes = stopTimes
        stopsFelt = 0; quietRun = 0; stopTimes = []; quietStartTs = nil
        afterAlight = true
        timeline.events.append(MotionEvent(kind: .alighted, ts: ts))
        timeline.walkingSecondsBetweenTrains += Double(walkRun)
        detector.standDown()
        motionState = .walking
        if timeline.events.filter({ $0.kind == .alighted }).count >= timeline.legs { alightedAll = true }
    }

    /// The rider says they are not on a train: the departure the sensors felt is withdrawn, they are back at the
    /// station waiting, and the next pull-away counts afresh.
    mutating func notOnTrain() {
        guard phase == .riding, let last = timeline.events.last, last.kind == .departed else { return }
        timeline.events.removeLast()
        phase = .atStation
        if timeline.arrivedStationTs == nil { timeline.arrivedStationTs = last.ts }
        stopsFelt = 0; quietRun = 0; stopTimes = []; quietStartTs = nil
        timeline.rideAssumed = false
        detector.standDown()
        motionState = detector.state
        firstMotionTs = nil                  // the grace period runs again from here
    }

    /// The rider says they are on a train already, having left the station at `departedTs` (a route started from
    /// the train, or one that never saw the pull-away): the ride is on from here, with that departure on the
    /// record, and the sensors watch for the stops and the alighting as after a felt departure.
    mutating func beginRiding(now: Double, departedTs: Double) {
        guard phase != .arrived, phase != .riding else { return }
        let dep = min(departedTs, now)
        if timeline.arrivedStationTs == nil { timeline.arrivedStationTs = dep }
        if timeline.platformTs == nil { timeline.platformTs = dep }
        timeline.events.append(MotionEvent(kind: .departed, ts: dep))
        timeline.rideAssumed = false
        phase = .riding
        afterAlight = false
        stopsFelt = 0; quietRun = 0; stopTimes = []; quietStartTs = nil
        detector.assumeRiding(since: dep)
        motionState = detector.state
        if firstMotionTs == nil { firstMotionTs = now - startGraceSec }
    }

    /// The route changed under the rider (they boarded a line off the plan and the planner found the route that
    /// rides it): the legs still to ride and the change follow the new route, so the trip ends at the right
    /// alighting and not at a change the old route had, or never.
    mutating func replan(legs: Int, transferStation: String?) {
        guard phase != .arrived else { return }
        timeline.legs = max(1, legs)
        timeline.transferStation = transferStation
        alightedAll = timeline.legsRidden >= timeline.legs
    }

    /// The arrival as the train the rider is actually on makes it (its own progress in the feed, and the connection
    /// it makes): the clock the trip ends by. The forecast the rider acted on is kept as it was.
    mutating func setLiveArrival(_ ts: Double) {
        guard phase != .arrived else { return }
        timeline.liveArriveTs = ts
    }

    /// A pull-away the feed says no train made (none left the platform then while others are still to come): the
    /// rider is still at the station, the next sustained push or vibration counts afresh, and the record keeps it.
    mutating func withdrawDeparture() {
        guard phase == .riding, let last = timeline.events.last, last.kind == .departed else { return }
        notOnTrain()
        timeline.withdrawnDepartures = (timeline.withdrawnDepartures ?? []) + [last.ts]
    }

    /// What the line inference concluded about a leg, kept with the trip (the latest word per leg).
    mutating func setBoarded(_ b: BoardedLeg) {
        if let i = timeline.boarded.firstIndex(where: { $0.leg == b.leg }) { timeline.boarded[i] = b } else { timeline.boarded.append(b) }
    }

    mutating func end(now: Double, by: String) -> TripTimeline {
        if phase != .arrived { finish(now, by: by) }
        return timeline
    }

    private mutating func finish(_ ts: Double, by: String) {
        phase = .arrived
        timeline.endedTs = ts
        timeline.endedBy = by
    }
}
