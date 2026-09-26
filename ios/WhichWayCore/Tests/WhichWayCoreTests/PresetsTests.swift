import XCTest
@testable import WhichWayCore

final class PresetsTests: XCTestCase {
    func testWindowsWeekdaysAndMidnight() {
        let am = CommutePreset(name: "am", originId: "a", destId: "b", startMinute: 6 * 60, endMinute: 10 * 60)
        XCTAssertTrue(am.isActive(minuteOfDay: 7 * 60, weekday: 3))
        XCTAssertFalse(am.isActive(minuteOfDay: 10 * 60, weekday: 3), "the end is exclusive")
        XCTAssertFalse(am.isActive(minuteOfDay: 7 * 60, weekday: 1), "weekdays only")
        let night = CommutePreset(name: "pm", originId: "a", destId: "b", startMinute: 22 * 60, endMinute: 2 * 60, weekdaysOnly: false)
        XCTAssertTrue(night.isActive(minuteOfDay: 23 * 60, weekday: 1))
        XCTAssertTrue(night.isActive(minuteOfDay: 60, weekday: 7))
        XCTAssertFalse(night.isActive(minuteOfDay: 12 * 60, weekday: 2))
        XCTAssertFalse(CommutePreset(name: "x", originId: "a", destId: "b", startMinute: 300, endMinute: 300).isActive(minuteOfDay: 300, weekday: 2))
        // a New York timestamp: Wednesday 2026-09-23 08:30 EDT
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = Fmt.ny
        let ts = cal.date(from: DateComponents(year: 2026, month: 9, day: 23, hour: 8, minute: 30))!.timeIntervalSince1970
        XCTAssertTrue(am.isActive(at: ts))
        XCTAssertFalse(night.isActive(at: ts))
        XCTAssertEqual(am.windowText, "06:00–10:00 weekdays")
        XCTAssertEqual(Fmt.dayStamp(ts), "2026-09-23")
        let w = PresetStore.suggestedWindow(at: ts)
        XCTAssertEqual(w.start, 7 * 60)
        XCTAssertEqual(w.end, 10 * 60)
        XCTAssertEqual(PresetStore.suggestedName(at: ts), "Morning commute")
    }

    func testStoreRoundTripAndActive() throws {
        let d = try XCTUnwrap(UserDefaults(suiteName: "whichway-tests-\(UUID().uuidString)"))
        let s = PresetStore(defaults: d)
        XCTAssertTrue(s.presets.isEmpty)
        let am = CommutePreset(name: "am", originId: "F23", destId: "A31", startMinute: 0, endMinute: 23 * 60 + 59, weekdaysOnly: false)
        let pm = CommutePreset(name: "pm", originId: "A31", destId: "F23", startMinute: 16 * 60, endMinute: 20 * 60, useNearestOrigin: true)
        s.add(am)
        s.add(pm)
        var edited = pm
        edited.name = "home"
        s.update(edited)
        let again = PresetStore(defaults: d)
        XCTAssertEqual(again.presets, [am, edited])
        XCTAssertEqual(again.active(at: Date().timeIntervalSince1970)?.name, "am", "the first matching window wins")
        again.remove(id: am.id)
        XCTAssertEqual(PresetStore(defaults: d).presets.map { $0.name }, ["home"])
    }

    func testReorderAndDeleteByOffsets() throws {
        let d = try XCTUnwrap(UserDefaults(suiteName: "whichway-tests-\(UUID().uuidString)"))
        let s = PresetStore(defaults: d)
        for n in ["a", "b", "c", "d"] { s.add(CommutePreset(name: n, originId: "x", destId: "y", startMinute: 0, endMinute: 60)) }
        // SwiftUI's onMove: dragging the first row below the third lands it after "c"
        s.move(from: IndexSet(integer: 0), to: 3)
        XCTAssertEqual(s.presets.map { $0.name }, ["b", "c", "a", "d"])
        s.move(from: IndexSet(integer: 3), to: 0)
        XCTAssertEqual(s.presets.map { $0.name }, ["d", "b", "c", "a"])
        s.remove(at: IndexSet([0, 2]))
        XCTAssertEqual(PresetStore(defaults: d).presets.map { $0.name }, ["b", "a"])
        s.move(from: IndexSet(integer: 9), to: 0)
        XCTAssertEqual(s.presets.map { $0.name }, ["b", "a"], "out-of-range offsets are ignored")
    }

    func testNearestStationsFromGeometry() throws {
        let schedJSON = """
        {"lines": {"6_N": {"stops": ["635N", "634N", "633N"], "names": ["14 St-Union Sq", "23 St", "28 St"], "run_sec": [90, 80], "dist_m": [700, 600]},
                   "L_N": {"stops": ["L03N", "L02N"], "names": ["14 St-Union Sq", "3 Av"], "run_sec": [60], "dist_m": [500]}},
         "transfers": {"635N": [{"line": "L_N", "stop": "L03N", "min_sec": 120}], "L03N": [{"line": "6_N", "stop": "635N", "min_sec": 120}]}}
        """
        let geoJSON = """
        {"lines": {"6_N": {"coords": [[40.7359, -73.9906], [40.7397, -73.9866], [40.7431, -73.9842]], "shape": []},
                   "L_N": {"coords": [[40.7347, -73.9906], [40.7327, -73.9860]], "shape": []}}}
        """
        let sched = try JSONDecoder().decode(ClientSchedule.self, from: Data(schedJSON.utf8))
        let geo = try JSONDecoder().decode(ClientGeometry.self, from: Data(geoJSON.utf8))
        let index = StationIndex(schedule: sched)
        let coords = stationCoordinates(schedule: sched, index: index, geometry: geo)
        // Union Sq is one complex (6 and L): its coordinate is the mean of its two platforms
        let usq = index.stationOf("635N")
        XCTAssertEqual(usq, index.stationOf("L03N"))
        XCTAssertEqual(coords[usq]!.lat, (40.7359 + 40.7347) / 2, accuracy: 1e-9)
        let near = nearestStations(to: (40.7400, -73.9870), coords: coords, index: index, n: 3)
        XCTAssertEqual(near.first?.station.name, "23 St")
        XCTAssertLessThan(near[0].meters, 100)
        XCTAssertEqual(near.map { $0.station.name }, ["23 St", "28 St", "14 St-Union Sq"])
        XCTAssertEqual(near[0].walkMinutes, 1)
        XCTAssertEqual(haversineM((40.7359, -73.9906), (40.7359, -73.9906)), 0)
        XCTAssertEqual(haversineM((40.0, -74.0), (41.0, -74.0)), 111_195, accuracy: 200)
    }
}
