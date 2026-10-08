import XCTest
@testable import WhichWayCore

final class HabitsTests: XCTestCase {
    private let ny = TimeZone(identifier: "America/New_York")!
    private func ts(_ day: Int, _ hour: Int, _ minute: Int = 0) -> Double {
        var cal = Calendar(identifier: .gregorian); cal.timeZone = ny
        return cal.date(from: DateComponents(year: 2026, month: 9, day: day, hour: hour, minute: minute))!.timeIntervalSince1970
    }

    func testRecordsOncePerTwentyMinutesAndCaps() {
        var h = Habits()
        h.record(origin: "A", dest: "B", ts: ts(21, 8), lat: nil, lon: nil, timeZone: ny)
        h.record(origin: "A", dest: "B", ts: ts(21, 8, 10), lat: nil, lon: nil, timeZone: ny)
        h.record(origin: "A", dest: "B", ts: ts(21, 8, 30), lat: nil, lon: nil, timeZone: ny)
        h.record(origin: "A", dest: "A", ts: ts(21, 9), lat: nil, lon: nil, timeZone: ny)
        XCTAssertEqual(h.uses.count, 2)
        XCTAssertEqual(h.uses[0].weekday, 2)                      // a Monday
        XCTAssertEqual(h.uses[0].hour, 8, accuracy: 0.01)
        for i in 0..<Habits.cap + 50 { h.record(origin: "X", dest: "Y", ts: ts(1, 0) + Double(i) * 1300, lat: nil, lon: nil, timeZone: ny) }
        XCTAssertEqual(h.uses.count, Habits.cap)
    }

    func testLikelyTripFollowsHourDayAndPlace() {
        var h = Habits()
        let home = (40.6703, -73.9898), work = (40.7359, -73.9906)
        for d in 21...25 {                                         // a working week
            h.record(origin: "A", dest: "B", ts: ts(d, 8, 15), lat: home.0, lon: home.1, timeZone: ny)
            h.record(origin: "B", dest: "A", ts: ts(d, 17, 45), lat: work.0, lon: work.1, timeZone: ny)
        }
        h.record(origin: "A", dest: "C", ts: ts(26, 11), lat: home.0, lon: home.1, timeZone: ny)   // Saturday
        let g = h.likelyTrip(hour: 8.5, weekday: 3, lat: home.0, lon: home.1)
        XCTAssertEqual(g?.origin, "A"); XCTAssertEqual(g?.dest, "B"); XCTAssertEqual(g?.uses, 5)
        let e = h.likelyTrip(hour: 18, weekday: 5, lat: work.0, lon: work.1)
        XCTAssertEqual(e?.origin, "B"); XCTAssertEqual(e?.dest, "A")
        // at home at 18:00 nothing fits well: the evening trip starts from work, the morning one is hours away
        let odd = h.likelyTrip(hour: 18, weekday: 5, lat: home.0, lon: home.1)
        XCTAssertTrue(odd == nil || odd!.score < h.likelyTrip(hour: 18, weekday: 5, lat: work.0, lon: work.1)!.score)
        XCTAssertNil(h.likelyTrip(hour: 3, weekday: 2, lat: nil, lon: nil))
        XCTAssertNil(h.likelyTrip(hour: 11, weekday: 7, lat: nil, lon: nil), "one Saturday use is not a habit")
    }

    func testSuggestsHomeAndWorkWithPins() {
        var h = Habits()
        let home = (40.6703, -73.9898), work = (40.7359, -73.9906)
        for d in 21...23 {
            h.record(origin: "A", dest: "B", ts: ts(d, 8), lat: home.0, lon: home.1, timeZone: ny)
            h.record(origin: "B", dest: "A", ts: ts(d, 18), lat: work.0 + Double(d - 22) * 0.001, lon: work.1, timeZone: ny)
        }
        let sh = h.suggestedHome()
        XCTAssertEqual(sh?.stationId, "A"); XCTAssertEqual(sh?.uses, 6)
        XCTAssertEqual(sh!.lat!, home.0, accuracy: 1e-9)
        let sw = h.suggestedWork(excluding: "A")
        XCTAssertEqual(sw?.stationId, "B")
        XCTAssertEqual(sw!.lat!, work.0, accuracy: 1e-6, "evening departures 110 m apart still pin")
        XCTAssertNil(Habits().suggestedHome())
    }
}
