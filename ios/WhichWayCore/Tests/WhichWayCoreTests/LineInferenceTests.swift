import XCTest
@testable import WhichWayCore

final class LineInferenceTests: XCTestCase {
    /// These cases reason in the feed's own times (a train "pulls away 25 s after the feed's time"), as the inference
    /// did before it learned how late the feed runs at the platform; PlatformTiming is tested on its own below.
    private let inf: LineInference = { var i = LineInference(); i.recordedLag = { _ in 0 }; i.dwellSec = 25; i.stopLagSec = 10; return i }()

    /// A candidate at the platform: `board` is the feed's time at the boarding stop.
    private func cand(_ id: String, key: String, board: Double, alight: Double? = nil, stops: Int? = nil, chosen: Bool = false, onLeg: Bool = true) -> BoardingCandidate {
        BoardingCandidate(trainId: "\(key)|\(id)", key: key, route: String(key.split(separator: "_")[0]), boardTs: board, alightTs: alight,
                          stopsToAlight: stops, chosen: chosen, onLeg: onLeg)
    }

    func testThePlannedTrainThatLeftWhenTheSensorsFeltItIsTheAnswer() {
        // the F the rider planned reached the platform at 1000; a G came at 1180; the train pulled away at 1025
        let c = [cand("f1", key: "F_N", board: 1000, chosen: true), cand("g1", key: "G_N", board: 1180)]
        let b = inf.fromDeparture(c, departedTs: 1025, leg: 0, chosenKey: "F_N")
        XCTAssertEqual(b.bestKey, "F_N")
        XCTAssertEqual(b.verdict, .onPlan)
        XCTAssertGreaterThan(b.confidence, 0.9)
        XCTAssertEqual(b.evidence, ["departure"])
    }

    func testADepartureThatMatchesTheOtherLineOverridesThePrior() {
        // the plan was the F, due at 1200; the G reached the platform at 1000 and the train left at 1030
        let c = [cand("f1", key: "F_N", board: 1200, chosen: true), cand("g1", key: "G_N", board: 1000)]
        let b = inf.fromDeparture(c, departedTs: 1030, leg: 0, chosenKey: "F_N")
        XCTAssertEqual(b.bestKey, "G_N")
        XCTAssertEqual(b.verdict, .switched)
        XCTAssertEqual(b.route, "G")
        XCTAssertEqual(b.chosenRoute, "F")
    }

    func testTwoTrainsLeavingTogetherStayUnsureUntilTheRideTellsThemApart() {
        // a local and an express reached the platform within half a minute; the local makes 6 stops to the alighting stop, the express 2
        let c = [cand("r1", key: "R_N", board: 1000, alight: 1900, stops: 6, chosen: true), cand("n1", key: "N_N", board: 1020, alight: 1600, stops: 2)]
        var b = inf.fromDeparture(c, departedTs: 1035, leg: 0, chosenKey: "R_N")
        XCTAssertEqual(b.verdict, .unsure, "the departure alone cannot split them: \(b.byLine)")
        b = inf.withStops(b, candidates: c, stopsFelt: 2)
        XCTAssertEqual(b.bestKey, "N_N")
        XCTAssertEqual(b.verdict, .switched)
        b = inf.withAlighting(b, candidates: c, alightedTs: 1625)
        XCTAssertGreaterThan(b.confidence, 0.97)
        XCTAssertEqual(b.evidence, ["departure", "stops", "alighting"])
    }

    func testTheTransferLegTrustsThePlanMoreThanTheFirstLegDoes() {
        // the same modest mismatch on both legs: the plan's train 50 s off the felt departure, the other line 10 s off
        let c = [cand("a1", key: "A_S", board: 1000, chosen: true), cand("c1", key: "C_S", board: 1040)]
        let first = inf.fromDeparture(c, departedTs: 1075, leg: 0, chosenKey: "A_S")
        let later = inf.fromDeparture(c, departedTs: 1075, leg: 1, chosenKey: "A_S")
        XCTAssertGreaterThan(later.byLine["A_S"]!, first.byLine["A_S"]!)
        XCTAssertEqual(inf.priorChosen(leg: 0, learnedShare: nil), 0.5)
        XCTAssertEqual(inf.priorChosen(leg: 1, learnedShare: nil), 0.7)
    }

    func testWhatTheRiderActuallyDoesBeforeMovesThePrior() {
        // on this trip the rider has taken the planned line only one time in five: the prior comes down to meet that
        XCTAssertEqual(inf.priorChosen(leg: 0, learnedShare: 0.2), 0.35, accuracy: 1e-9)
        XCTAssertEqual(inf.priorChosen(leg: 1, learnedShare: 1.0), 0.85, accuracy: 1e-9)
        let c = [cand("f1", key: "F_N", board: 1000, chosen: true), cand("g1", key: "G_N", board: 1030)]
        let habit = inf.fromDeparture(c, departedTs: 1045, leg: 0, chosenKey: "F_N", learnedShare: 0.1)
        let none = inf.fromDeparture(c, departedTs: 1045, leg: 0, chosenKey: "F_N")
        XCTAssertLessThan(habit.byLine["F_N"]!, none.byLine["F_N"]!)
    }

    func testALineAtThePlatformButNotOnTheLegCountsNearlyAsMuchAndNoCandidatesStaysUnsure() {
        let c = [cand("f1", key: "F_N", board: 1000, chosen: true), cand("g1", key: "G_N", board: 1000), cand("d1", key: "D_N", board: 1000, onLeg: false)]
        let p = inf.prior(c, leg: 0, chosenKey: "F_N")
        // 10% is kept for "none of these trains"; the rest splits as before
        XCTAssertEqual(p[LineBelief.noneKey]!, 0.1, accuracy: 1e-9)
        XCTAssertEqual(p["F_N|f1"]!, 0.9 * 0.5, accuracy: 1e-9)
        XCTAssertEqual(p["G_N|g1"]!, 0.9 * 0.5 * 1.0 / 1.8, accuracy: 1e-9)
        XCTAssertEqual(p["D_N|d1"]!, 0.9 * 0.5 * 0.8 / 1.8, accuracy: 1e-9)
        let b = inf.fromDeparture([], departedTs: 1000, leg: 0, chosenKey: "F_N")
        XCTAssertEqual(b.verdict, .unsure)
        XCTAssertNil(b.boarded)
        XCTAssertTrue(b.evidence.isEmpty)
    }

    private func train(_ id: String, key: String, points: [(Int, Double)]) -> LiveTrain {
        LiveTrain(key: key, tripId: id, trainId: nil, route: String(key.split(separator: "_")[0]), points: points.map { TrainPoint(idx: $0.0, ts: $0.1) },
                  nextIdx: points.first?.0 ?? 0, nextName: "", etaTs: points.first?.1 ?? 0, schedTs: nil, schedMethod: nil, latenessSec: nil,
                  effectiveLatenessSec: nil, position: nil, corroboration: "position_unknown", trackChanged: false, started: true, segment: nil, lastRun: nil)
    }

    func testTheLogKeepsATrainsTimeAtThePlatformAfterTheFeedMovesItOn() {
        var log = DepartureLog()
        // poll at 900: the F lists the platform (idx 4) at 1000 and the alighting stop (idx 9) at 1600
        log.observe(trains: [train("f1", key: "F_N", points: [(3, 940), (4, 1000), (9, 1600)])], key: "F_N", boardIdx: 4, span: (from: 4, to: 9), onLeg: true, now: 900)
        // poll at 1030: the train has left the platform; its first listed stop is now idx 5
        log.observe(trains: [train("f1", key: "F_N", points: [(5, 1090), (9, 1605)])], key: "F_N", boardIdx: 4, span: (from: 4, to: 9), onLeg: true, now: 1030)
        let c = log.candidates(departedTs: 1025, windowSec: 300, chosenTrainId: "F_N|f1")
        XCTAssertEqual(c.count, 1)
        XCTAssertEqual(c[0].boardTs, 1000)
        XCTAssertEqual(c[0].alightTs, 1605, "the alighting time follows the feed as the train approaches (the last published time stands once it has passed)")
        XCTAssertEqual(c[0].stopsToAlight, 5)
        XCTAssertTrue(c[0].chosen)
        // a train far outside the window is not a candidate; a reset forgets the leg
        log.observe(trains: [train("f2", key: "F_N", points: [(4, 1700)])], key: "F_N", boardIdx: 4, span: (from: 4, to: 9), onLeg: true, now: 1030)
        XCTAssertEqual(log.candidates(departedTs: 1025, windowSec: 300, chosenTrainId: nil).count, 1)
        log.reset()
        XCTAssertTrue(log.candidates(departedTs: 1025, windowSec: 300, chosenTrainId: nil).isEmpty)
    }
}

final class LineFallbackTests: XCTestCase {
    private func sec(_ ts: Double, walking: Bool, push: Double = 0.005, shake: Double = 0.005) -> MotionSecond {
        MotionSecond(ts: ts, stepEnergy: walking ? 0.05 : 0.002, pushG: push, shakeG: shake)
    }

    func testWithSensorsOnThePlannedRideIsAssumedOnceTheBoardingTimePassesAndAFeltDepartureTakesOver() {
        var t = TripTracker(TripTimeline(startTs: 1000, startedBy: "gps", startDistanceM: 40, placeId: nil, originStation: "S1", destStation: "S9",
                                         transferStation: nil, legs: 1), distanceToOriginM: 40)
        t.updateForecast(boardTs: 1100, arriveTs: 1700, now: 1000)
        var ts = 1000.0
        for _ in 0..<30 { t.motion(sec(ts, walking: true)); ts += 1 }        // down to the platform
        t.tick(now: 1230)
        XCTAssertEqual(t.phase, .atStation, "still walking when the boarding time passed: not put on a train")
        for _ in 0..<30 { t.motion(sec(ts, walking: false)); ts += 1 }       // standing on the platform
        XCTAssertNotNil(t.timeline.platformTs)
        t.tick(now: 1210)
        XCTAssertEqual(t.phase, .atStation, "the grace after the boarding time has not run out")
        t.tick(now: 1225)
        XCTAssertEqual(t.phase, .riding)
        XCTAssertTrue(t.timeline.rideAssumed)
        // the phone was in a bag; the pull-away is felt late, on the next train
        ts = 1300
        for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 }
        XCTAssertEqual(t.timeline.events.first, MotionEvent(kind: .departed, ts: 1300))
        XCTAssertFalse(t.timeline.rideAssumed, "a departure felt turns the assumed ride into a measured one")
        XCTAssertEqual(t.phase, .riding)
    }

    func testThePlanAndTheRidersWordAreBeliefsToo() {
        let plan = LineBelief.fromPlan(leg: 0, chosenKey: "F_N", chosenTrainId: "F_N|f1", evidence: "schedule")!
        XCTAssertTrue(plan.assumed)
        XCTAssertEqual(plan.verdict, .onPlan)
        XCTAssertEqual(plan.bestTrain, "F_N|f1")
        XCTAssertNil(LineBelief.fromPlan(leg: 0, chosenKey: nil, chosenTrainId: nil, evidence: "schedule"))
        let cands = [
            BoardingCandidate(trainId: "G_N|g1", key: "G_N", route: "G", boardTs: 1000, alightTs: nil, stopsToAlight: nil, chosen: false, onLeg: true),
            BoardingCandidate(trainId: "G_N|g2", key: "G_N", route: "G", boardTs: 1400, alightTs: nil, stopsToAlight: nil, chosen: false, onLeg: true),
        ]
        let hand = LineBelief.byHand(leg: 0, key: "G_N", chosenKey: "F_N", candidates: cands, departedTs: 1380)
        XCTAssertTrue(hand.byHand)
        XCTAssertFalse(hand.assumed)
        XCTAssertEqual(hand.verdict, .switched)
        XCTAssertEqual(hand.bestTrain, "G_N|g2", "the train of that line nearest the departure felt")
        XCTAssertEqual(hand.boarded?.key, "G_N")
        let noTrain = LineBelief.byHand(leg: 1, key: "R_N", chosenKey: "R_N", candidates: [], departedTs: nil)
        XCTAssertEqual(noTrain.verdict, .onPlan)
        XCTAssertNil(noTrain.bestTrain)
    }
}
