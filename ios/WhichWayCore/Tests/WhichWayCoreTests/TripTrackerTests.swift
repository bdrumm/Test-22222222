import XCTest
@testable import WhichWayCore

final class TripTrackerTests: XCTestCase {
    private func start(_ ts: Double = 1000, legs: Int = 1, transfer: String? = nil) -> TripTimeline {
        TripTimeline(startTs: ts, startedBy: "hand", startDistanceM: 900, placeId: "home", originStation: "S1", destStation: "S9",
                     transferStation: transfer, legs: legs)
    }
    private func sec(_ ts: Double, walking: Bool, push: Double = 0.005, shake: Double = 0.005) -> MotionSecond {
        MotionSecond(ts: ts, stepEnergy: walking ? 0.05 : 0.002, pushG: push, shakeG: shake)
    }

    func testApproachLearnsStreetPaceAndArrivalStartsTheStationClock() {
        var t = TripTracker(start(), distanceToOriginM: 900)
        XCTAssertEqual(t.phase, .approaching)
        // 900 m to 100 m in fixes 30 s apart at 1.3 m/s, then within the radius
        var d = 900.0, ts = 1000.0
        while d > 100 { t.location(ts: ts, toOriginM: d, toDestM: nil, now: ts); d -= 39; ts += 30 }
        XCTAssertEqual(t.phase, .atStation)
        XCTAssertEqual(t.timeline.arrivedStationTs, ts - 30)
        XCTAssertGreaterThan(t.timeline.walkMeters, 600)
        XCTAssertEqual(t.timeline.walkSpeedMPerMin!, 78, accuracy: 1)   // 1.3 m/s
        // stairs down, then standing on the platform
        for i in 0..<40 { t.motion(sec(ts + Double(i), walking: true)) }
        for i in 40..<70 { t.motion(sec(ts + Double(i), walking: false)) }
        XCTAssertTrue(t.timeline.platformObserved)
        XCTAssertEqual(t.timeline.accessSec!, 70, accuracy: 25)
    }

    func testStartAtTheStationThenRideAndAlightEndsTheTrip() {
        var t = TripTracker(start(), distanceToOriginM: 40)
        XCTAssertEqual(t.phase, .atStation)
        t.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        var ts = 1000.0
        for _ in 0..<60 { t.motion(sec(ts, walking: false)); ts += 1 }            // waiting
        for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 }   // pulls away at 1060
        XCTAssertEqual(t.phase, .riding)
        XCTAssertEqual(t.timeline.corroboratedDepartureTs, 1060)
        t.updateForecast(boardTs: 1300, arriveTs: 1750, now: 1070)   // the planner's next train: the rider is on this one
        XCTAssertEqual(t.timeline.forecastBoardTs, 1100)
        XCTAssertEqual(t.timeline.liveArriveTs, 1700)
        for _ in 0..<580 { t.motion(sec(ts, walking: false, shake: 0.04)); ts += 1 }   // rolling to 1646
        for _ in 0..<20 { t.motion(sec(ts, walking: false)); ts += 1 }             // standing at the destination
        for _ in 0..<20 { t.motion(sec(ts, walking: true)); ts += 1 }              // walks off at 1666
        XCTAssertEqual(t.timeline.events.last?.kind, .alighted)
        t.tick(now: ts)                                                            // 1686 >= 1750 - 120
        XCTAssertEqual(t.phase, .arrived)
        XCTAssertEqual(t.timeline.endedBy, "alighted")
    }

    func testTwoLegsMeasureTheChangeAndOnlyTheSecondAlightingEnds() {
        var t = TripTracker(start(legs: 2, transfer: "X"), distanceToOriginM: 20)
        t.updateForecast(boardTs: 1030, arriveTs: 2100, now: 1000)
        var ts = 1000.0
        func ride(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: false, shake: 0.04)); ts += 1 } }
        func depart() { for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 } }
        func walk(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: true)); ts += 1 } }
        func stand(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: false)); ts += 1 } }
        stand(30); depart(); ride(300); stand(15)
        walk(45); stand(60)                      // off the first train, 45 s to the other platform, wait
        depart()
        XCTAssertEqual(t.phase, .riding)
        XCTAssertEqual(t.timeline.transferWalkSec!, 45, accuracy: 2)
        t.tick(now: ts)
        XCTAssertNotEqual(t.phase, .arrived, "one alighting on a two-leg trip is the change, not the end")
        ride(600); stand(15); walk(20)
        t.tick(now: ts)
        XCTAssertEqual(t.phase, .arrived)
    }

    func testAPushInTheFirstSecondsOfSensingIsNotADeparture() {
        var t = TripTracker(start(), distanceToOriginM: 20)
        var ts = 1000.0
        for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 }    // tapping Start, pocketing the phone
        XCTAssertTrue(t.timeline.events.isEmpty)
        XCTAssertEqual(t.phase, .atStation)
        for _ in 0..<30 { t.motion(sec(ts, walking: false)); ts += 1 }
        for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 }    // the train, half a minute later
        XCTAssertEqual(t.timeline.events.map(\.kind), [.departed])
        XCTAssertEqual(t.phase, .riding)
    }

    func testWellPastTheArrivalAndNearTheDestinationEndsTheTrip() {
        var t = TripTracker(start(), distanceToOriginM: 20)
        t.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        var ts = 1000.0
        for _ in 0..<30 { t.motion(sec(ts, walking: false)); ts += 1 }
        for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 }
        XCTAssertEqual(t.phase, .riding)
        t.location(ts: 1800, toOriginM: 6000, toDestM: 540, now: 1800)   // the rider walked on past the station, 540 m out
        XCTAssertNotEqual(t.phase, .arrived, "two minutes past the arrival only")
        t.location(ts: 1830, toOriginM: 6000, toDestM: 560, now: 1830)
        XCTAssertEqual(t.phase, .arrived)
        XCTAssertEqual(t.timeline.endedBy, "arrived")
    }

    func testNoSensorsAssumesTheRideAndEndsNearTheDestinationOrOnTimeout() {
        var t = TripTracker(start(), distanceToOriginM: 30)
        t.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        t.tick(now: 1150)
        XCTAssertEqual(t.phase, .atStation)
        t.tick(now: 1230)
        XCTAssertEqual(t.phase, .riding)
        XCTAssertTrue(t.timeline.rideAssumed)
        t.location(ts: 1350, toOriginM: 5000, toDestM: 200, now: 1350)   // near the destination, but 350 s early
        XCTAssertEqual(t.phase, .riding)
        t.location(ts: 1450, toOriginM: 5200, toDestM: 120, now: 1450)   // within five minutes of the forecast
        XCTAssertEqual(t.phase, .arrived)
        XCTAssertEqual(t.timeline.endedBy, "arrived")

        var u = TripTracker(start(), distanceToOriginM: 30)
        u.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        u.tick(now: 2299); XCTAssertNotEqual(u.phase, .arrived)
        u.tick(now: 2300); XCTAssertEqual(u.phase, .arrived)
        XCTAssertEqual(u.timeline.endedBy, "timeout")
    }

    func testForecastFreezesOnceTheBoardingTimePassesAtTheStation() {
        var t = TripTracker(start(), distanceToOriginM: 30)
        t.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        t.updateForecast(boardTs: 1110, arriveTs: 1710, now: 1050)          // still before boarding: follows
        XCTAssertEqual(t.timeline.liveArriveTs, 1710)
        t.updateForecast(boardTs: 1900, arriveTs: 2500, now: 1111)          // the planner moved to the next train: frozen
        XCTAssertEqual(t.timeline.liveArriveTs, 1710)
        XCTAssertEqual(t.timeline.forecastBoardTs, 1110)
        t.tick(now: 1240)
        XCTAssertEqual(t.phase, .riding)                                      // assumed, no sensors
        t.location(ts: 1420, toOriginM: 4000, toDestM: 80, now: 1420)
        XCTAssertEqual(t.phase, .arrived, "at the destination within five minutes of the frozen forecast")
        // on the way to the station the forecast keeps following, missed trains and all
        var a = TripTracker(start(), distanceToOriginM: 900)
        a.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        a.updateForecast(boardTs: 1900, arriveTs: 2500, now: 1111)
        XCTAssertEqual(a.timeline.liveArriveTs, 2500)
    }

    func testStandingStillAtTheDestinationEndsOnTheClock() {
        var t = TripTracker(start(), distanceToOriginM: 30)
        t.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        t.location(ts: 1380, toOriginM: 5000, toDestM: 90, now: 1380)   // the last fix: at the destination, early
        XCTAssertNotEqual(t.phase, .arrived)
        t.tick(now: 1399); XCTAssertNotEqual(t.phase, .arrived)
        t.tick(now: 1400); XCTAssertEqual(t.phase, .arrived)            // no new fix needed
        XCTAssertEqual(t.timeline.endedBy, "arrived")
        // a stale fix does not count
        var u = TripTracker(start(), distanceToOriginM: 30)
        u.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        u.location(ts: 700, toOriginM: 5000, toDestM: 90, now: 700)
        u.tick(now: 1500); XCTAssertNotEqual(u.phase, .arrived)
        // a route that never reached its origin but shows up at the destination near the time is over too
        var v = TripTracker(start(), distanceToOriginM: 900)
        v.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        v.location(ts: 1450, toOriginM: 4000, toDestM: 100, now: 1450)
        XCTAssertEqual(v.phase, .arrived)
    }

    func testReplanToATwoLegRouteWaitsForTheSecondAlighting() {
        // planned direct; on the train the phone finds the rider on a line with a change ahead, and the route switches
        var t = TripTracker(start(), distanceToOriginM: 20)
        t.updateForecast(boardTs: 1030, arriveTs: 1700, now: 1000)
        var ts = 1000.0
        func ride(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: false, shake: 0.04)); ts += 1 } }
        func depart() { for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 } }
        func walk(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: true)); ts += 1 } }
        func stand(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: false)); ts += 1 } }
        stand(30); depart()
        XCTAssertEqual(t.phase, .riding)
        t.replan(legs: 2, transferStation: "X")
        t.setLiveArrival(2400)
        XCTAssertEqual(t.timeline.legs, 2)
        XCTAssertEqual(t.timeline.transferStation, "X")
        ride(300); stand(15); walk(45)                     // off at the change
        XCTAssertEqual(t.timeline.events.last?.kind, .alighted)
        t.tick(now: ts + 200)                              // past the old route's arrival less two minutes: still riding
        XCTAssertEqual(t.phase, .riding)
        stand(60); depart(); ride(600); stand(15); walk(45)   // the second train, off at the destination
        XCTAssertEqual(t.timeline.events.filter { $0.kind == .alighted }.count, 2)
        t.tick(now: ts)                                    // 2122: the ride ended early against the 2400 clock, not over yet
        XCTAssertEqual(t.phase, .riding)
        t.tick(now: ts + 200)                              // within two minutes of the live arrival: over
        XCTAssertEqual(t.phase, .arrived)
        XCTAssertEqual(t.timeline.endedBy, "alighted")
        XCTAssertNotNil(t.timeline.transferWalkSec)
    }

    func testTheLiveArrivalOfTheBoardedTrainMovesTheClockTheTripEndsBy() {
        var t = TripTracker(start(), distanceToOriginM: 20)
        t.updateForecast(boardTs: 1030, arriveTs: 1700, now: 1000)
        var ts = 1000.0
        for _ in 0..<30 { t.motion(sec(ts, walking: false)); ts += 1 }
        for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 }
        XCTAssertEqual(t.phase, .riding)
        t.setLiveArrival(2400)                             // the train actually boarded reaches the destination later
        XCTAssertEqual(t.timeline.forecastArriveTs, 1700)  // the forecast the rider acted on is kept
        XCTAssertEqual(t.timeline.liveArriveTs, 2400)
        t.tick(now: 2310)                                  // ten minutes past the old arrival: no timeout
        XCTAssertEqual(t.phase, .riding)
        t.tick(now: 3001)
        XCTAssertEqual(t.timeline.endedBy, "timeout")
    }

    func testAFixTooUnsureToActOnChangesNothingAndTheStreetGuardNeedsASureOne() {
        // this afternoon: the route started at the entrance on a fix 500 m off; an unsure fix there must neither keep the
        // rider "on the way" when they are not, nor drop the departure as a push on the street
        var t = TripTracker(start(), distanceToOriginM: nil)
        XCTAssertEqual(t.phase, .approaching)
        t.location(ts: 1000, toOriginM: 480, toDestM: nil, now: 1000, accuracyM: 350)    // coarse: ignored
        var ts = 1001.0
        for _ in 0..<30 { t.motion(sec(ts, walking: false)); ts += 1 }
        for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 }
        XCTAssertEqual(t.phase, .riding, "an unsure far fix does not hold back the departure")
        // a sure far fix does: a push on the street is not a train
        var s = TripTracker(start(), distanceToOriginM: nil)
        s.location(ts: 1000, toOriginM: 480, toDestM: nil, now: 1000, accuracyM: 20)
        ts = 1001
        for _ in 0..<30 { s.motion(sec(ts, walking: false)); ts += 1 }
        for _ in 0..<6 { s.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 }
        XCTAssertEqual(s.phase, .approaching)
        // a 160 m fix give or take 40 m counts as at the station
        var a = TripTracker(start(), distanceToOriginM: nil)
        a.location(ts: 1000, toOriginM: 160, toDestM: nil, now: 1000, accuracyM: 40)
        XCTAssertEqual(a.phase, .atStation)
    }

    func testPersonalModelLearnsAndPredicts() {
        var m = PersonalModel()
        XCTAssertEqual(m.walkSpeedMPerMin, 80)
        XCTAssertNil(m.accessSec(station: "S1"))
        var tl = start()
        tl.walkMeters = 700; tl.walkSeconds = 600                   // 70 m/min
        tl.arrivedStationTs = 1600; tl.platformTs = 1720; tl.platformObserved = true
        tl.endedTs = 2500
        XCTAssertTrue(m.learn(tl))
        XCTAssertEqual(m.walkSpeedMPerMin, 70, accuracy: 0.1)
        XCTAssertEqual(m.accessSec(station: "S1")!, 120)
        XCTAssertEqual(m.accessSec(station: "S2")!, 120, "another station falls back to the rider's average")
        XCTAssertEqual(m.placeToStationSec(place: "home", station: "S1")!, 600)
        XCTAssertEqual(m.walkMinutes(meters: 700, station: "S1"), 12)   // 10 min walk + 2 min to the platform
        var tl2 = tl; tl2.walkMeters = 900; tl2.walkSeconds = 600       // 90 m/min: blends in
        m.learn(tl2)
        XCTAssertEqual(m.walkSpeedMPerMin, 80, accuracy: 0.1)
        XCTAssertEqual(m.trips, 2)
        // a change is only trusted at a station after two trips there
        var x = start(legs: 2, transfer: "X")
        x.events = [MotionEvent(kind: .departed, ts: 1), MotionEvent(kind: .alighted, ts: 2), MotionEvent(kind: .departed, ts: 3)]
        x.walkingSecondsBetweenTrains = 50
        m.learn(x); XCTAssertNil(m.transferSec(station: "X"))
        m.learn(x); XCTAssertEqual(m.transferSec(station: "X")!, 50)
        // the data is small: a trip that measured nothing teaches nothing
        XCTAssertFalse(m.learn(TripTimeline(startTs: 1, startedBy: "gps", originStation: "S", destStation: "D", legs: 1)))
        // a change across the platform (Jay St, 17 s then 12 s) is learned, and the planner's allowance follows it down
        // from the timetable's 3 minutes; a 5 s "change" is a false alighting and is not
        var jay = start(legs: 2, transfer: "Jay St-MetroTech")
        jay.events = x.events
        XCTAssertEqual(m.plannedTransferSec(station: "Jay St-MetroTech", scheduled: 180), 180, "nothing learned there yet")
        jay.walkingSecondsBetweenTrains = 17; m.learn(jay)
        jay.walkingSecondsBetweenTrains = 12; m.learn(jay)
        XCTAssertEqual(m.transferSec(station: "Jay St-MetroTech")!, 14.5, accuracy: 0.01)
        XCTAssertEqual(m.plannedTransferSec(station: "Jay St-MetroTech", scheduled: 180), 15, "not under the floor")
        XCTAssertEqual(m.plannedTransferSec(station: "X", scheduled: 30), 50, "and up where the rider is slower")
        var fake = start(legs: 2, transfer: "Y")
        fake.events = x.events; fake.walkingSecondsBetweenTrains = 5
        let before = m.transferDefault.n
        m.learn(fake)
        XCTAssertEqual(m.transferDefault.n, before)
    }


    func testAWalkTheFeedVouchesForClosesTheRideWithoutTheTrainSeenAtRest() {
        // Oct 8 at Jay St, twice: the rider crossed the platform in 11 to 12 s of walking that began while the train still
        // read as moving (people boarding, the phone in a hand heading for the doors), so the detector let it pass
        var t = TripTracker(start(legs: 2, transfer: "S4"), distanceToOriginM: 20)
        var ts = 1000.0
        func feed(_ n: Int, walking: Bool, push: Double = 0.005, shake: Double = 0.005) { for _ in 0..<n { t.motion(sec(ts, walking: walking, push: push, shake: shake)); ts += 1 } }
        feed(30, walking: false)
        feed(6, walking: false, push: 0.08, shake: 0.03)          // pulls away at 1030
        XCTAssertEqual(t.phase, .riding)
        XCTAssertTrue(t.onTrain)
        feed(200, walking: false, shake: 0.04)                     // rolling
        XCTAssertEqual(t.lastMovingTs!, ts - 1, accuracy: 0.5)
        XCTAssertNil(t.walking)
        feed(10, walking: true, shake: 0.03)                       // steps with the car still shaking
        XCTAssertEqual(t.timeline.events.map(\.kind), [.departed], "no rest seen before the steps: the detector does not call it")
        XCTAssertEqual(t.walking?.seconds, 10)
        XCTAssertEqual(t.walking!.startTs, ts - 10, accuracy: 0.5)
        // the recorder: the believed train stood at a station then, by the feed
        let off = t.walking!.startTs
        t.alightWalking(at: off)
        XCTAssertEqual(t.timeline.events.map(\.kind), [.departed, .alighted])
        XCTAssertEqual(t.timeline.events.last!.ts, off)
        XCTAssertEqual(t.timeline.rideStops, [0])
        XCTAssertEqual(t.phase, .riding, "the trip goes on to the next train")
        XCTAssertFalse(t.onTrain, "but between trains now")
        XCTAssertEqual(t.timeline.legsRidden, 1)
        feed(20, walking: true)                                    // the rest of the walk across
        XCTAssertGreaterThanOrEqual(t.timeline.walkingSecondsBetweenTrains, 30)
        feed(40, walking: false)
        feed(6, walking: false, push: 0.08, shake: 0.03)          // the next train pulls away
        XCTAssertEqual(t.timeline.events.map(\.kind), [.departed, .alighted, .departed])
        XCTAssertTrue(t.onTrain)
        XCTAssertEqual(t.timeline.transferWalkSec!, 30, accuracy: 1)
    }
}
