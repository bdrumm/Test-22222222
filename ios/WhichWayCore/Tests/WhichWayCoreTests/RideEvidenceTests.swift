import XCTest
@testable import WhichWayCore

/// The evidence that arrives after the departure: the feed's vehicle position at the pull-away, the timing of the
/// stops felt against each train's actual stop times, a fix after walking off, and the log that follows trains
/// after they have left the platform.
final class RideEvidenceTests: XCTestCase {
    /// These cases reason in the feed's own times (a train "pulls away 25 s after the feed's time"), as the inference
    /// did before it learned how late the feed runs at the platform; PlatformTiming is tested on its own below.
    private let inf: LineInference = { var i = LineInference(); i.recordedLag = { _ in 0 }; i.dwellSec = 25; i.stopLagSec = 10; return i }()

    private func cand(_ id: String, key: String, board: Double, boardIdx: Int = 0, alightIdx: Int? = nil, stoppedAt: Double? = nil,
                      stopTs: [Int: Double] = [:], chosen: Bool = false) -> BoardingCandidate {
        BoardingCandidate(trainId: "\(key)|\(id)", key: key, route: String(key.split(separator: "_")[0]), boardTs: board, alightTs: alightIdx.flatMap { stopTs[$0] },
                          stopsToAlight: alightIdx.map { $0 - boardIdx }, chosen: chosen, onLeg: true, boardIdx: boardIdx, alightIdx: alightIdx,
                          stoppedAtBoardTs: stoppedAt, stopTs: stopTs)
    }

    func testTheTrainTheFeedShowedStandingAtThePlatformIsPreferred() {
        // two trains reached the platform at the same time; the feed placed only one of them standing there just before the pull-away
        let c = [cand("f1", key: "F_N", board: 1000, stoppedAt: 1020, chosen: true), cand("g1", key: "G_N", board: 1000)]
        let b = inf.fromDeparture(c, departedTs: 1035, leg: 0, chosenKey: "F_N")
        XCTAssertEqual(b.bestKey, "F_N")
        XCTAssertGreaterThan(b.byLine["F_N"]!, 0.7)
        XCTAssertEqual(b.evidence, ["departure", "position"])
        // the same without positions: the prior alone decides, and the evidence says so
        let plain = inf.fromDeparture([cand("f1", key: "F_N", board: 1000, chosen: true), cand("g1", key: "G_N", board: 1000)], departedTs: 1035, leg: 0, chosenKey: "F_N")
        XCTAssertEqual(plain.evidence, ["departure"])
        XCTAssertEqual(plain.byLine["F_N"]!, plain.byLine["G_N"]!, accuracy: 1e-9)   // a coin toss between the two
        XCTAssertLessThan(plain.noneShare, 0.03)                                        // both fit the pull-away well
    }

    func testOneCandidateThatFitsNothingAfterTheDepartureIsNotTakenOnTrust() {
        // this morning: the phone was put on a train it never saw leave (the route started on the G two stops on); the
        // only train at the platform near the felt "departure" was an F, which then fitted neither the stop count, nor the
        // walk-off, nor where the phone was afterwards
        let f = cand("f1", key: "F_N", board: 1000, alightIdx: 10, stopTs: [10: 2100], chosen: true)
        let start = inf.fromDeparture([f], departedTs: 1030, leg: 0, chosenKey: "F_N")
        XCTAssertTrue(start.settled, "one train left right then: \(start.byLine)")
        var b = inf.withStops(start, candidates: [f], stopsFelt: 0)                 // the F makes ten stops; none were felt
        b = inf.withAlighting(b, candidates: [f], alightedTs: 1340)               // walked off 13 minutes before the F got there
        b = inf.withLocation(b, candidates: [f], distanceM: ["F_N|f1": 5200])     // and 5 km from where the F would have been
        XCTAssertFalse(b.settled, "nothing fits: \(b.byLine)")
        XCTAssertGreaterThan(b.noneShare, b.byLine["F_N"]!)
        XCTAssertEqual(b.verdict, .unsure)
    }

    func testATrainLayingOverAtItsTerminalIsNotTakenForTheOneThatLeft() {
        // this afternoon: the E left 14 St; an L sat at its 8 Av terminal, on another platform of the station
        func lt(_ id: String, key: String, idx: Int, ts: Double, terminal: Bool) -> LiveTrain {
            var t = train(id, key: key, points: [(idx, ts)])
            t.position = TrainPosition(status: "STOPPED_AT", stopId: "", stopIdx: idx, stopName: "", sinceSec: 240, holding: false, stalled: false,
                                       atTerminal: terminal, expectedRunSec: nil, positionLatenessSec: nil)
            return t
        }
        var log = DepartureLog()
        log.observe(trains: [lt("l1", key: "L_S", idx: 0, ts: 1000, terminal: true)], key: "L_S", boardIdx: 0, span: nil, onLeg: false, now: 990, samePlatform: false)
        log.observe(trains: [lt("e1", key: "E_S", idx: 5, ts: 1000, terminal: false)], key: "E_S", boardIdx: 5, span: (from: 5, to: 6), onLeg: true, now: 990)
        let c = log.candidates(departedTs: 1010, windowSec: 300, chosenTrainId: nil)
        XCTAssertNil(c.first { $0.key == "L_S" }?.stoppedAtBoardTs, "a layover says nothing")
        XCTAssertNotNil(c.first { $0.key == "E_S" }?.stoppedAtBoardTs)
        XCTAssertEqual(c.first { $0.key == "L_S" }?.samePlatform, false)
        let b = inf.fromDeparture(c, departedTs: 1010, leg: 0, chosenKey: "E_S")
        XCTAssertEqual(b.bestKey, "E_S")
        XCTAssertTrue(b.settled, "\(b.byLine)")
    }

    func testThisAfternoonsEFromFourteenthStreetWithTheMeasuredPlatformTiming() {
        // Oct 7, 14 St southbound, seconds after 17:41:30: an L recorded leaving its 8 Av terminal at 0 (another
        // platform), the E recorded at 14 St at 62, the A at 207; the phone felt the pull-away at 29 and the walk-off at
        // W 4 St at 164, where the E is recorded at 220. Under the old assumption (pulls away 25 s after the feed's time)
        // the L fitted best; with the feed's measured lateness at the platform the E does.
        let real = LineInference()
        let e = BoardingCandidate(trainId: "E_S|e1", key: "E_S", route: "E", boardTs: 62, alightTs: 220, stopsToAlight: 1, chosen: false, onLeg: true,
                                  boardIdx: 5, alightIdx: 6, stopTs: [6: 220])
        let l = BoardingCandidate(trainId: "L_S|l1", key: "L_S", route: "L", boardTs: 0, alightTs: nil, stopsToAlight: nil, chosen: false, onLeg: false,
                                  boardIdx: 0, samePlatform: false)
        let a = BoardingCandidate(trainId: "A_S|a1", key: "A_S", route: "A", boardTs: 207, alightTs: 330, stopsToAlight: 1, chosen: true, onLeg: true,
                                  boardIdx: 5, alightIdx: 6, stopTs: [6: 330])
        let b0 = real.fromDeparture([l, e, a], departedTs: 29, leg: 0, chosenKey: "A_S")
        XCTAssertEqual(b0.bestKey, "E_S", "\(b0.byLine)")
        XCTAssertTrue(b0.settled, "\(b0.byLine)")
        let b1 = real.withAlighting(b0, candidates: [l, e, a], alightedTs: 164)
        XCTAssertEqual(b1.bestKey, "E_S")
        XCTAssertGreaterThan(b1.confidence, 0.9)
        // the old timing, for the record: the L
        let old = inf.fromDeparture([l, e, a], departedTs: 29, leg: 0, chosenKey: "A_S")
        XCTAssertNotEqual(old.bestKey, "E_S")
        // the timing table itself: the lettered lines run later than the numbered ones
        XCTAssertGreaterThan(PlatformTiming.recordedLag(route: "E"), PlatformTiming.recordedLag(route: "1"))
        XCTAssertEqual(PlatformTiming.atPlatform(1000, route: "1"), 1000 - PlatformTiming.recordedLag(route: "1"))
    }

    func testStopTimingDuringTheRideTellsLocalFromExpressBeforeAnyoneGetsOff() {
        // a local (R) and an express (N) left together; the local calls at every stop, the express at 2 and 4
        let local = cand("r1", key: "R_N", board: 1000, alightIdx: 4, stopTs: [1: 1100, 2: 1200, 3: 1300, 4: 1400], chosen: true)
        let express = cand("n1", key: "N_N", board: 1015, alightIdx: 4, stopTs: [2: 1180, 4: 1360])
        let c = [local, express]
        let start = inf.fromDeparture(c, departedTs: 1040, leg: 0, chosenKey: "R_N")
        XCTAssertEqual(start.verdict, .unsure, "the departure alone cannot split them: \(start.byLine)")
        // the phone felt a stop begin at 1110 and another at 1210: the local's
        let onLocal = inf.withRide(start, candidates: c, stopTimes: [1110, 1210], now: 1250)
        XCTAssertEqual(onLocal.bestKey, "R_N")
        XCTAssertTrue(onLocal.settled, "\(onLocal.byLine)")
        XCTAssertEqual(onLocal.evidence, ["departure", "ride"])
        // one stop felt at 1190 while the local has already called twice: the express
        let onExpress = inf.withRide(start, candidates: c, stopTimes: [1190], now: 1250)
        XCTAssertEqual(onExpress.bestKey, "N_N")
        XCTAssertEqual(onExpress.verdict, .switched)
        // nothing felt yet, or no stop times known: the belief stands
        XCTAssertEqual(inf.withRide(start, candidates: c, stopTimes: [], now: 1250), start)
        let blind = [cand("r1", key: "R_N", board: 1000, chosen: true), cand("n1", key: "N_N", board: 1000)]
        XCTAssertEqual(inf.withRide(start, candidates: blind, stopTimes: [1110], now: 1250).byLine, start.byLine)
    }

    func testAFixAfterWalkingOffPicksTheStationItIsAt() {
        let c = [cand("a1", key: "A_S", board: 1000, alightIdx: 3, stopTs: [3: 1500], chosen: true), cand("c1", key: "C_S", board: 1010, alightIdx: 5, stopTs: [5: 1700])]
        let start = inf.fromDeparture(c, departedTs: 1040, leg: 0, chosenKey: "A_S")
        let near = inf.withLocation(start, candidates: c, distanceM: ["A_S|a1": 900, "C_S|c1": 40])
        XCTAssertEqual(near.bestKey, "C_S")
        XCTAssertEqual(near.verdict, .switched)
        XCTAssertEqual(near.evidence, ["departure", "location"])
        // a candidate whose stop position is unknown is not judged: a fix right at the other's stop changes little
        let partial = inf.withLocation(start, candidates: c, distanceM: ["C_S|c1": 40])
        XCTAssertEqual(partial.byLine["C_S"]!, start.byLine["C_S"]!, accuracy: 0.02)
        XCTAssertEqual(partial.evidence, ["departure", "location"])
        XCTAssertEqual(inf.withLocation(start, candidates: c, distanceM: [:]), start)
    }

    private func train(_ id: String, key: String, points: [(Int, Double)], at: Int? = nil) -> LiveTrain {
        let pos = at.map { TrainPosition(status: "STOPPED_AT", stopId: "s\($0)", stopIdx: $0, stopName: "", sinceSec: 10, holding: false, stalled: false,
                                         atTerminal: false, expectedRunSec: nil, positionLatenessSec: nil) }
        return LiveTrain(key: key, tripId: id, trainId: nil, route: String(key.split(separator: "_")[0]), points: points.map { TrainPoint(idx: $0.0, ts: $0.1) },
                         nextIdx: points.first?.0 ?? 0, nextName: "", etaTs: points.first?.1 ?? 0, schedTs: nil, schedMethod: nil, latenessSec: nil,
                         effectiveLatenessSec: nil, position: pos, corroboration: "", trackChanged: false, started: true, segment: nil, lastRun: nil)
    }

    func testTheLogFollowsATrainAfterItLeavesAndRefreshesCandidates() {
        var log = DepartureLog()
        // poll 1: the train lists the boarding stop (idx 2) and the stops beyond; the feed has it standing at the platform
        log.observe(trains: [train("t1", key: "F_N", points: [(2, 1000), (3, 1100), (4, 1200)], at: 2)], key: "F_N", boardIdx: 2, span: (2, 4), onLeg: true, now: 1005)
        var cands = log.candidates(departedTs: 1030, windowSec: 300, chosenTrainId: nil)
        XCTAssertEqual(cands.count, 1)
        XCTAssertEqual(cands[0].stoppedAtBoardTs, 1005)
        XCTAssertEqual(cands[0].boardIdx, 2)
        XCTAssertEqual(cands[0].alightIdx, 4)
        XCTAssertEqual(cands[0].stopTs, [3: 1100, 4: 1200])
        // poll 2: the train has moved on (the boarding stop is gone from its list) and its times have shifted
        log.observe(trains: [train("t1", key: "F_N", points: [(3, 1120), (4, 1230)])], key: "F_N", boardIdx: 2, span: (2, 4), onLeg: true, now: 1065)
        cands = log.refreshed(cands)
        XCTAssertEqual(cands[0].boardTs, 1000, "the time at the boarding stop is kept once the stop is gone")
        XCTAssertEqual(cands[0].stopTs, [3: 1120, 4: 1230])
        XCTAssertEqual(cands[0].progressIdx, 3)
        XCTAssertEqual(log.progressIdx(of: "F_N|t1"), 3)
        // poll 3: past stop 3 as well; the last published time at 3 stands as its arrival
        log.observe(trains: [train("t1", key: "F_N", points: [(4, 1240)])], key: "F_N", boardIdx: 2, span: (2, 4), onLeg: true, now: 1150)
        XCTAssertEqual(log.refreshed(cands)[0].stopTs, [3: 1120, 4: 1240])
        // a train that never listed the boarding stop is not a candidate
        log.observe(trains: [train("t2", key: "F_N", points: [(5, 1300)])], key: "F_N", boardIdx: 2, span: (2, 4), onLeg: true, now: 1150)
        XCTAssertEqual(log.candidates(departedTs: 1030, windowSec: 300, chosenTrainId: nil).count, 1)
    }

    func testStopTimesAreRecordedAndASamePlatformChangeClosesTheRide() {
        var t = TripTracker(TripTimeline(startTs: 1000, startedBy: "hand", startDistanceM: 20, placeId: nil, originStation: "S1", destStation: "S9",
                                         transferStation: "S4", legs: 2), distanceToOriginM: 20)
        var ts = 1000.0
        func sec(_ walking: Bool, push: Double = 0.005, shake: Double = 0.005) -> MotionSecond { MotionSecond(ts: ts, stepEnergy: walking ? 0.05 : 0.002, pushG: push, shakeG: shake) }
        func ride(_ n: Int) { for _ in 0..<n { t.motion(sec(false, shake: 0.04)); ts += 1 } }
        func stand(_ n: Int) { for _ in 0..<n { t.motion(sec(false)); ts += 1 } }
        func depart() { for _ in 0..<6 { t.motion(sec(false, push: 0.08, shake: 0.03)); ts += 1 } }
        stand(30); depart()
        XCTAssertEqual(t.phase, .riding)
        ride(90); stand(25); ride(80)
        XCTAssertEqual(t.stopTimes.count, 1)
        XCTAssertEqual(t.stopTimes[0], 1000 + 36 + 90, accuracy: 1.0, "the stop is timed from when the train came to rest")
        stand(100)                                                  // at the transfer, standing a long time
        XCTAssertEqual(t.stopTimes.count, 2)
        XCTAssertNotNil(t.standingSince)
        XCTAssertEqual(t.standingSince!, ts - 100, accuracy: 1.0)
        // the feed says the believed train has gone on without us: the recorder closes the ride
        t.alightStanding(at: t.standingSince!)
        XCTAssertEqual(t.timeline.events.map(\.kind), [.departed, .alighted])
        XCTAssertEqual(t.timeline.rideStops, [2])
        XCTAssertEqual(t.lastRideStopTimes.count, 2)
        XCTAssertEqual(t.stopTimes, [])
        XCTAssertEqual(t.phase, .riding, "the trip goes on: the next train is still to come")
        // the next train pulls away under us: a new departure, with no walking in between
        stand(60); depart()
        XCTAssertEqual(t.timeline.events.map(\.kind), [.departed, .alighted, .departed])
        XCTAssertEqual(t.timeline.legsRidden, 1)
    }
}
