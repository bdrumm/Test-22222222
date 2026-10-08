import XCTest
@testable import WhichWayCore

final class RideStopsTests: XCTestCase {
    private func sec(_ ts: Double, walking: Bool, push: Double = 0.005, shake: Double = 0.005) -> MotionSecond {
        MotionSecond(ts: ts, stepEnergy: walking ? 0.05 : 0.002, pushG: push, shakeG: shake)
    }

    func testStationStopsAreCountedPerRideAndTheLastOneIsTheAlighting() {
        var t = TripTracker(TripTimeline(startTs: 1000, startedBy: "hand", startDistanceM: 20, placeId: nil, originStation: "S1", destStation: "S9",
                                         transferStation: nil, legs: 1), distanceToOriginM: 20)
        var ts = 1000.0
        func ride(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: false, shake: 0.04)); ts += 1 } }
        func stand(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: false)); ts += 1 } }
        func depart() { for _ in 0..<6 { t.motion(sec(ts, walking: false, push: 0.08, shake: 0.03)); ts += 1 } }
        func walk(_ n: Int) { for _ in 0..<n { t.motion(sec(ts, walking: true)); ts += 1 } }
        stand(30); depart()
        XCTAssertEqual(t.phase, .riding)
        ride(90); stand(25)            // first station: a 25 s dwell
        ride(80); stand(6)             // a signal check too short to be a station
        ride(70); stand(30)            // second station
        ride(60); stand(20)            // the rider's stop: the train stands, then they walk off
        XCTAssertEqual(t.stopsFelt, 2, "two stops felt so far; the one in hand is not counted until motion resumes")
        walk(20)
        XCTAssertEqual(t.timeline.events.last?.kind, .alighted)
        XCTAssertEqual(t.timeline.rideStops, [3], "three station stops on the ride, the alighting stop included")
        XCTAssertEqual(t.stopsFelt, 0)
        let b = BoardedLeg(leg: 0, key: "F_N", trainId: "F_N|x", chosenKey: "F_N", confidence: 0.9, verdict: .onPlan, evidence: ["departure"])
        t.setBoarded(b)
        t.setBoarded(BoardedLeg(leg: 0, key: "F_N", trainId: "F_N|x", chosenKey: "F_N", confidence: 0.97, verdict: .onPlan, evidence: ["departure", "stops", "alighting"]))
        XCTAssertEqual(t.timeline.boarded.count, 1, "the latest word per leg replaces the earlier one")
        XCTAssertEqual(t.timeline.boarded[0].confidence, 0.97)
        XCTAssertEqual(t.timeline.legsRidden, 1)
    }

    func testThePersonalModelLearnsWhichLineWasTaken() {
        var m = PersonalModel()
        var tl = TripTimeline(startTs: 0, startedBy: "hand", startDistanceM: nil, placeId: nil, originStation: "A", destStation: "B", transferStation: nil, legs: 1)
        XCTAssertNil(m.chosenLineShare(origin: "A", dest: "B", leg: 0, chosenKey: "F_N"))
        for key in ["G_N", "G_N", "F_N"] {
            tl.boarded = [BoardedLeg(leg: 0, key: key, trainId: nil, chosenKey: "F_N", confidence: 0.8, verdict: key == "F_N" ? .onPlan : .switched, evidence: ["departure"])]
            XCTAssertTrue(m.learn(tl))
        }
        XCTAssertEqual(m.chosenLineShare(origin: "A", dest: "B", leg: 0, chosenKey: "F_N")!, 1.0 / 3.0, accuracy: 1e-9)
        tl.boarded = [BoardedLeg(leg: 0, key: "F_N", trainId: nil, chosenKey: "F_N", confidence: 0.5, verdict: .unsure, evidence: ["departure"])]
        XCTAssertFalse(m.learn(tl), "an unsure leg teaches nothing")
        // a model saved before line choices existed still decodes
        let old = try! JSONSerialization.data(withJSONObject: ["walkSpeed": ["mean": 80, "n": 2], "access": [:], "accessDefault": ["mean": 0, "n": 0], "transfer": [:],
                                                                  "transferDefault": ["mean": 0, "n": 0], "placeToStation": [:], "trips": 2])
        let decoded = try! JSONDecoder().decode(PersonalModel.self, from: old)
        XCTAssertNil(decoded.lineChoices)
        XCTAssertEqual(decoded.trips, 2)
    }
}
