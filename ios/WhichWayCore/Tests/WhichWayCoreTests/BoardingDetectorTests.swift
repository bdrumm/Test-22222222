import XCTest
@testable import WhichWayCore

final class BoardingDetectorTests: XCTestCase {
    private var d = BoardingDetector()
    private var events: [MotionEvent] = []
    private var t = 1000.0

    private func run(_ n: Int, step: Double, push: Double, shake: Double) {
        for _ in 0..<n {
            if let e = d.feed(MotionSecond(ts: t, stepEnergy: step, pushG: push, shakeG: shake)) { events.append(e) }
            t += 1
        }
    }

    func testDepartureThenAlighting() {
        run(30, step: 0.05, push: 0.01, shake: 0.01)        // walking to the platform
        XCTAssertEqual(d.state, .walking)
        run(60, step: 0.002, push: 0.005, shake: 0.005)     // standing on the platform, t = 1030...1089
        XCTAssertEqual(d.state, .still)
        XCTAssertTrue(events.isEmpty)
        run(6, step: 0.002, push: 0.08, shake: 0.03)        // the train pulls away at t = 1090
        XCTAssertEqual(events, [MotionEvent(kind: .departed, ts: 1090)])
        XCTAssertEqual(d.state, .riding)
        run(90, step: 0.002, push: 0.01, shake: 0.04)       // rolling
        run(30, step: 0.002, push: 0.0, shake: 0.0)         // stopped at a station: still riding
        XCTAssertEqual(d.state, .riding)
        run(60, step: 0.002, push: 0.02, shake: 0.04)       // rolling again
        run(3, step: 0.05, push: 0.01, shake: 0.01)         // a few steps inside the car
        run(5, step: 0.002, push: 0.0, shake: 0.04)
        XCTAssertEqual(events.count, 1, "steps inside the car do not end the ride")
        run(12, step: 0.05, push: 0.01, shake: 0.01)        // a longer walk to the far door while the train still rolls
        run(10, step: 0.002, push: 0.0, shake: 0.04)
        XCTAssertEqual(events.count, 1, "walking inside a moving car does not end the ride either")
        run(8, step: 0.002, push: 0.0, shake: 0.005)        // the train stands at the station
        let off = t                                          // walks off at t = 1314
        run(20, step: 0.05, push: 0.01, shake: 0.0)
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events[1], MotionEvent(kind: .alighted, ts: off))
        XCTAssertEqual(d.state, .walking)
    }

    func testALongWalkEndsTheRideEvenWhenTheStopWentUnfelt() {
        run(10, step: 0.002, push: 0.005, shake: 0.005)
        run(6, step: 0.002, push: 0.08, shake: 0.03)
        run(120, step: 0.002, push: 0.01, shake: 0.04)
        XCTAssertEqual(events.count, 1)
        run(29, step: 0.05, push: 0.01, shake: 0.0)         // walking straight out of "rolling": not yet
        XCTAssertEqual(events.count, 1)
        run(1, step: 0.05, push: 0.01, shake: 0.0)          // half a minute of walking is an alighting whatever came before
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events[1].kind, .alighted)
    }

    func testPassingTrainOnThePlatformIsNotARide() {
        run(20, step: 0.002, push: 0.005, shake: 0.005)
        run(5, step: 0.002, push: 0.01, shake: 0.03)        // a train passes: brief vibration, no push
        run(20, step: 0.002, push: 0.005, shake: 0.005)
        XCTAssertTrue(events.isEmpty)
        XCTAssertEqual(d.state, .still)
    }

    func testWalkingNeverStartsARide() {
        run(30, step: 0.05, push: 0.09, shake: 0.05)        // a brisk walk shakes and pushes the phone too
        XCTAssertTrue(events.isEmpty)
        XCTAssertEqual(d.state, .walking)
    }

    func testSustainedVibrationAloneStartsARide() {
        run(10, step: 0.002, push: 0.005, shake: 0.005)
        run(12, step: 0.002, push: 0.01, shake: 0.03)       // a gentle departure: no clear push, but rolling from t = 1010
        XCTAssertEqual(events, [MotionEvent(kind: .departed, ts: 1010)])
    }


    func testThreeQuietSecondsBeforeTheStepsAreEnoughToStepOff() {
        // a standing train shakes a little as people board: Oct 8 at Jay St the A read as at rest for 4 s before the rider
        // stepped off; the old 5 s let the change go unseen
        run(30, step: 0.002, push: 0.005, shake: 0.005)
        run(6, step: 0.002, push: 0.08, shake: 0.03)         // departs at 1030
        run(120, step: 0.002, push: 0.01, shake: 0.04)       // rolling
        run(3, step: 0.002, push: 0.01, shake: 0.01)         // at rest, 3 s
        let off = t
        run(10, step: 0.05, push: 0.03, shake: 0.03)         // off and across the platform
        XCTAssertEqual(events, [MotionEvent(kind: .departed, ts: 1030), MotionEvent(kind: .alighted, ts: off)])
        XCTAssertEqual(d.state, .walking)
    }
}
