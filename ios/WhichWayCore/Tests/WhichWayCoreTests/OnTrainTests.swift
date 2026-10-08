import XCTest
@testable import WhichWayCore

/// A route started, or put right, from the train the rider is already on: the look-back over the trains that
/// left the station, the ride that begins without a felt departure, and the log taking the train up late.
final class OnTrainTests: XCTestCase {
    private func schedule() throws -> ClientSchedule {
        let json = """
        {"lines": {"G_N": {"stops": ["g0","g1","g2","g3","g4"], "names": ["4 Av","Smith","Bergen","Hoyt","Fulton"], "run_sec": [120, 120, 120, 120]},
                   "R_N": {"stops": ["r0","r1","r2","r3"], "names": ["4 Av","Union","Atlantic","DeKalb"], "run_sec": [150, 150, 150]}}}
        """
        return try JSONDecoder().decode(ClientSchedule.self, from: Data(json.utf8))
    }
    private func train(_ id: String, key: String, points: [(Int, Double)]) -> LiveTrain {
        LiveTrain(key: key, tripId: id, trainId: nil, route: String(key.split(separator: "_")[0]), points: points.map { TrainPoint(idx: $0.0, ts: $0.1) },
                  nextIdx: points.first?.0 ?? 0, nextName: "", etaTs: points.first?.1 ?? 0, schedTs: nil, schedMethod: nil, latenessSec: nil,
                  effectiveLatenessSec: nil, position: nil, corroboration: "", trackChanged: false, started: true, segment: nil, lastRun: nil)
    }
    private func board(_ key: String, _ trains: [LiveTrain]) -> LineBoard {
        LineBoard(key: key, route: String(key.split(separator: "_")[0]), direction: "N", now: 0, trains: trains, nHolding: 0, nStalled: 0, nFeedOptimistic: 0)
    }
    private func sec(_ ts: Double, walking: Bool, push: Double = 0.005, shake: Double = 0.005) -> MotionSecond {
        MotionSecond(ts: ts, stepEnergy: walking ? 0.05 : 0.002, pushG: push, shakeG: shake)
    }

    func testTheTrainsThatLeftTheStationRecentlyMostRecentFirst() throws {
        let sched = try schedule()
        let now = 10_000.0
        let boards = ["G_N": board("G_N", [train("g-left", key: "G_N", points: [(2, now + 100), (3, now + 220), (4, now + 340)]),   // left 4 Av at now - 140
                                           train("g-here", key: "G_N", points: [(0, now + 30), (1, now + 150)]),                  // still to arrive: not ahead
                                           train("g-old", key: "G_N", points: [(4, now + 10)]),                                   // left 40 minutes ago
                                           train("g-gone", key: "G_N", points: [(4, now + 200)])]),                                // past Hoyt, where this route gets off
                      "R_N": board("R_N", [train("r-left", key: "R_N", points: [(1, now + 20), (2, now + 170)])])]                  // left 4 Av at now - 130
        let out = trainsAhead(boards: boards, schedule: sched, boardIdx: ["G_N": 0, "R_N": 0], alightIdx: ["G_N": 3, "R_N": 3], onLegKeys: ["R_N"], now: now)
        XCTAssertEqual(out.map { $0.trainId }, ["R_N|r-left", "G_N|g-left"])
        let g = out[1]
        XCTAssertEqual(g.boardTs, now - 140)
        XCTAssertEqual(g.alightTs, now + 220)
        XCTAssertEqual(g.stopsToAlight, 3)
        XCTAssertEqual(g.stopTs, [2: now + 100, 3: now + 220, 4: now + 340])
        XCTAssertEqual(g.progressIdx, 2)
        XCTAssertFalse(g.onLeg)
        XCTAssertTrue(out[0].onLeg)
    }

    func testARideBegunFromTheTrainEndsAtTheAlighting() {
        var t = TripTracker(TripTimeline(startTs: 1000, startedBy: "onboard", startDistanceM: 2000, placeId: nil, originStation: "S1", destStation: "S9",
                                         transferStation: nil, legs: 1), distanceToOriginM: 2000)
        XCTAssertEqual(t.phase, .approaching)
        t.beginRiding(now: 1000, departedTs: 820)
        XCTAssertEqual(t.phase, .riding)
        XCTAssertEqual(t.timeline.events.map { $0.kind }, [.departed])
        XCTAssertEqual(t.timeline.events[0].ts, 820)
        XCTAssertEqual(t.timeline.arrivedStationTs, 820)
        XCTAssertFalse(t.timeline.rideAssumed)
        t.setLiveArrival(1700)
        var ts = 1000.0
        for _ in 0..<300 { t.motion(sec(ts, walking: false, shake: 0.04)); ts += 1 }      // rolling: no second departure
        XCTAssertEqual(t.timeline.events.count, 1)
        for _ in 0..<20 { t.motion(sec(ts, walking: false)); ts += 1 }                      // a stop
        for _ in 0..<200 { t.motion(sec(ts, walking: false, shake: 0.04)); ts += 1 }
        for _ in 0..<20 { t.motion(sec(ts, walking: false)); ts += 1 }                      // the destination
        for _ in 0..<20 { t.motion(sec(ts, walking: true)); ts += 1 }                       // walks off at 1560
        XCTAssertEqual(t.timeline.events.last?.kind, .alighted)
        XCTAssertEqual(t.timeline.rideStops, [2])
        t.tick(now: 1600)
        XCTAssertEqual(t.phase, .arrived)
        XCTAssertEqual(t.timeline.endedBy, "alighted")
        // already riding: a second call changes nothing
        var r = TripTracker(TripTimeline(startTs: 1000, startedBy: "hand", startDistanceM: 10, placeId: nil, originStation: "S1", destStation: "S9", transferStation: nil, legs: 1), distanceToOriginM: 10)
        r.beginRiding(now: 1000, departedTs: 990); r.beginRiding(now: 1100, departedTs: 1090)
        XCTAssertEqual(r.timeline.events.count, 1)
    }

    func testPullingAwayFromTheStationAtATrainsPaceStartsTheRide() {
        var t = TripTracker(TripTimeline(startTs: 1000, startedBy: "hand", startDistanceM: 600, placeId: nil, originStation: "S1", destStation: "S9",
                                         transferStation: nil, legs: 1), distanceToOriginM: 600)
        XCTAssertEqual(t.phase, .approaching)
        // fixes 10 s apart, 80 m further from the origin each time: 8 m/s, a train; three such intervals make it sure
        var d = 600.0, ts = 1000.0
        t.location(ts: ts, toOriginM: d, toDestM: nil, now: ts)
        for _ in 0..<2 { ts += 10; d += 80; t.location(ts: ts, toOriginM: d, toDestM: nil, now: ts) }
        XCTAssertEqual(t.phase, .approaching)                           // 20 s of it is not enough
        ts += 10; d += 80; t.location(ts: ts, toOriginM: d, toDestM: nil, now: ts)
        XCTAssertEqual(t.phase, .riding)
        XCTAssertEqual(t.timeline.events.map { $0.kind }, [.departed])
        XCTAssertEqual(t.timeline.events.first?.ts ?? 0, 1000, accuracy: 1)      // the departure is put at the start of the pull-away
        // a walk toward the station, however fast, is not one
        var w = TripTracker(TripTimeline(startTs: 1000, startedBy: "hand", startDistanceM: 600, placeId: nil, originStation: "S1", destStation: "S9",
                                         transferStation: nil, legs: 1), distanceToOriginM: 600)
        d = 600; ts = 1000
        for _ in 0..<3 { ts += 10; d -= 20; w.location(ts: ts, toOriginM: d, toDestM: nil, now: ts) }
        XCTAssertEqual(w.phase, .approaching)
        // moving away slowly (a detour on foot) is not one either
        var s = TripTracker(TripTimeline(startTs: 1000, startedBy: "hand", startDistanceM: 600, placeId: nil, originStation: "S1", destStation: "S9",
                                         transferStation: nil, legs: 1), distanceToOriginM: 600)
        d = 600; ts = 1000
        for _ in 0..<6 { ts += 10; d += 15; s.location(ts: ts, toOriginM: d, toDestM: nil, now: ts) }
        XCTAssertEqual(s.phase, .approaching)
    }

    func testAPullAwayNoTrainMadeIsToldFromOneThatHappened() {
        // Oct 7, 4 Av-9 St at 09:53:13 (0 here): a pull-away was felt; the R was still to come, the last F had left
        // two and a half minutes before. A minute and a half on, the feed shows no train gone past the platform then.
        var log = DepartureLog()
        let r = train("r1", key: "R_N", points: [(4, 125), (5, 250)])               // still to reach the platform (idx 4)
        var f = train("f1", key: "F_N", points: [(4, -150), (5, -30)])               // seen at the platform before
        log.observe(trains: [r], key: "R_N", boardIdx: 4, span: (from: 4, to: 8), onLeg: true, now: -200)
        log.observe(trains: [f], key: "F_N", boardIdx: 4, span: nil, onLeg: false, now: -200)
        f = train("f1", key: "F_N", points: [(5, -30), (6, 90)])                     // gone past it
        log.observe(trains: [f], key: "F_N", boardIdx: 4, span: nil, onLeg: false, now: 100)
        log.observe(trains: [train("r1", key: "R_N", points: [(4, 125), (5, 250)])], key: "R_N", boardIdx: 4, span: (from: 4, to: 8), onLeg: true, now: 100)
        let felt = log.departureCheck(at: 0)
        XCTAssertFalse(felt.left)
        XCTAssertTrue(felt.waiting)
        // Oct 7, 14 St at 17:41:59 (0 here): the E's last feed time for 14 St was 33 s later, and the feed has moved it on
        var l2 = DepartureLog()
        l2.observe(trains: [train("e1", key: "E_S", points: [(5, 33), (6, 190)])], key: "E_S", boardIdx: 5, span: (from: 5, to: 6), onLeg: true, now: -20)
        l2.observe(trains: [train("e1", key: "E_S", points: [(6, 190)])], key: "E_S", boardIdx: 5, span: (from: 5, to: 6), onLeg: true, now: 90)
        l2.observe(trains: [train("a1", key: "A_S", points: [(5, 180)])], key: "A_S", boardIdx: 5, span: (from: 5, to: 6), onLeg: true, now: 90)
        XCTAssertTrue(l2.departureCheck(at: 0).left)
        XCTAssertEqual(l2.lastPollTs, 90)
    }

    func testTheLogTakesUpATrainItNeverSawAtThePlatform() {
        var log = DepartureLog()
        let c = BoardingCandidate(trainId: "G_N|g1", key: "G_N", route: "G", boardTs: 900, alightTs: 1260, stopsToAlight: 3, chosen: false, onLeg: true,
                                  boardIdx: 0, alightIdx: 3, stopTs: [2: 1140, 3: 1260], progressIdx: 2)
        log.seed(c, now: 1000)
        XCTAssertEqual(log.refreshed([c]), [c])
        // the next poll moves it on: the feed's newer time at Hoyt, and its next stop
        let t = train("g1", key: "G_N", points: [(3, 1275), (4, 1400)])
        log.observe(trains: [t], key: "G_N", boardIdx: 0, span: (from: 0, to: 3), onLeg: true, now: 1150)
        let r = log.refreshed([c])[0]
        XCTAssertEqual(r.alightTs, 1275)
        XCTAssertEqual(r.progressIdx, 3)
        XCTAssertEqual(r.stopTs[2], 1140, "a stop already passed keeps the last time published for it")
        XCTAssertEqual(r.boardTs, 900)
        XCTAssertEqual(log.progressIdx(of: "G_N|g1"), 3)
    }
}
