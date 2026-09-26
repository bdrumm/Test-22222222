import XCTest
@testable import WhichWayCore

/// The Swift predictor must agree with the Python reference (mta_delay_insights/realtime/client_model.py) on
/// the fixture that Tests/make_fixtures.py generates from it.
final class PredictorTests: XCTestCase {
    struct Input: Decodable {
        var trains: [PredictorTrain]
        var line: PredictorLine
        var model: ClientModel
        var now: Double
        var scenarios: [String]
        var elapsed: [Double]
        var horizons: [Double]
    }

    private func loadFixture() throws -> (Input, [String: Any]) {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "predictor_fixture", withExtension: "json", subdirectory: "Fixtures"))
        let data = try Data(contentsOf: url)
        let top = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let inputData = try JSONSerialization.data(withJSONObject: try XCTUnwrap(top["input"]))
        return (try JSONDecoder().decode(Input.self, from: inputData), top)
    }

    private func num(_ v: Any?) -> Double? {
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        return nil
    }

    func testPredictLineMatchesPython() throws {
        let (inp, top) = try loadFixture()
        let expected = try XCTUnwrap(top["expected"] as? [String: Any])
        XCTAssertEqual(inp.trains.count, 5)
        for sc in inp.scenarios {
            let py = try XCTUnwrap(expected[sc] as? [String: Any], sc)
            let out = Predictor.predictLine(inp.trains, line: inp.line, model: inp.model, now: inp.now, scenario: sc)
            let pyTrains = try XCTUnwrap(py["trains"] as? [[String: Any]])
            XCTAssertEqual(out.trains.map { $0.tripId }, pyTrains.map { $0["trip_id"] as? String ?? "" }, "\(sc): order")
            for (t, pt) in zip(out.trains, pyTrains) {
                XCTAssertEqual(t.holdExtraSec, num(pt["hold_extra_sec"]) ?? -1, accuracy: 1e-6, "\(sc) \(t.tripId) hold")
                XCTAssertEqual(t.knockOnSec, num(pt["knock_on_sec"]) ?? -1, accuracy: 1e-6, "\(sc) \(t.tripId) knock")
                let pyPts = try XCTUnwrap(pt["points"] as? [[String: Any]])
                XCTAssertEqual(t.points.count, pyPts.count, "\(sc) \(t.tripId) points")
                for (p, pp) in zip(t.points, pyPts) {
                    XCTAssertEqual(p.idx, pp["idx"] as? Int ?? -1)
                    XCTAssertEqual(p.source, pp["source"] as? String ?? "")
                    XCTAssertEqual(p.etaTs, num(pp["eta_ts"]) ?? -1, accuracy: 1e-6, "\(sc) \(t.tripId) eta \(p.idx)")
                    XCTAssertEqual(p.loTs, num(pp["lo_ts"]) ?? -1, accuracy: 1e-6, "\(sc) \(t.tripId) lo \(p.idx)")
                    XCTAssertEqual(p.hiTs, num(pp["hi_ts"]) ?? -1, accuracy: 1e-6, "\(sc) \(t.tripId) hi \(p.idx)")
                }
            }
            XCTAssertEqual(out.nKnockOn, py["n_knock_on"] as? Int ?? -1, sc)
            XCTAssertEqual(out.knockOnTotalSec, num(py["knock_on_total_sec"]) ?? -1, accuracy: 1e-6, sc)
            let pyWorst = py["worst_gap"] as? [String: Any]
            XCTAssertEqual(out.worstGap?.idx, pyWorst?["idx"] as? Int, "\(sc): worst gap stop")
            if let w = out.worstGap, let pw = pyWorst { XCTAssertEqual(w.gapSec, num(pw["gap_sec"]) ?? -1, accuracy: 1e-6) }
            let pyStops = try XCTUnwrap(py["per_stop"] as? [[String: Any]])
            XCTAssertEqual(out.perStop.map { $0.nArrivals }, pyStops.map { $0["n_arrivals"] as? Int ?? -1 }, sc)
        }
    }

    func testRemainingHoldAndCalibrationMatchPython() throws {
        let (inp, top) = try loadFixture()
        let rem = try XCTUnwrap(top["remaining"] as? [[String: Any]])
        for (e, r) in zip(inp.elapsed, rem) {
            let mine = Predictor.remainingHold(inp.model.holdSurvival, elapsed: e)
            XCTAssertEqual(mine.expected, num(r["expected"]) ?? -1, accuracy: 1e-6)
            XCTAssertEqual(mine.p50, num(r["p50"]) ?? -1, accuracy: 1e-6)
            XCTAssertEqual(mine.p90, num(r["p90"]) ?? -1, accuracy: 1e-6)
            XCTAssertEqual(mine.clears2min, num(r["clears_2min"]) ?? -1, accuracy: 1e-6)
        }
        let cal = try XCTUnwrap(top["calibration"] as? [[String: Any]])
        for (h, c) in zip(inp.horizons, cal) {
            let mine = Predictor.calibrationAt(inp.model, route: "6", horizon: h)
            XCTAssertEqual(mine.bias, num(c["bias"]) ?? -1, accuracy: 1e-9)
            XCTAssertEqual(mine.p10, num(c["p10"]) ?? -1, accuracy: 1e-9)
            XCTAssertEqual(mine.p90, num(c["p90"]) ?? -1, accuracy: 1e-9)
            XCTAssertEqual(mine.n, c["n"] as? Int ?? -1)
        }
        // no tables at all: physical priors
        XCTAssertEqual(Predictor.remainingHold(nil, elapsed: 500).expected, Predictor.priorHold.expected)
        XCTAssertEqual(Predictor.calibrationAt(nil, route: "Q", horizon: 100).bias, 0)
    }

    func testDecoderAndBoardRoundTrip() throws {
        // a tiny feed: one trip with two stops and its vehicle, through the wire decoder and the board
        var bytes: [UInt8] = []
        func varint(_ v: UInt64) -> [UInt8] { var out: [UInt8] = []; var x = v; repeat { var b = UInt8(x & 0x7f); x >>= 7; if x != 0 { b |= 0x80 }; out.append(b) } while x != 0; return out }
        func field(_ n: Int, bytes b: [UInt8]) -> [UInt8] { varint(UInt64(n << 3 | 2)) + varint(UInt64(b.count)) + b }
        func field(_ n: Int, varint v: UInt64) -> [UInt8] { varint(UInt64(n << 3 | 0)) + varint(v) }
        func str(_ n: Int, _ s: String) -> [UInt8] { field(n, bytes: Array(s.utf8)) }
        let trip = str(1, "010000_6..N") + str(3, "20260101") + str(5, "6")
        let stu1 = str(4, "635N") + field(2, bytes: field(2, varint: 1_000_100))
        let stu2 = str(4, "634N") + field(2, bytes: field(2, varint: 1_000_220))
        let tu = field(1, bytes: trip) + field(2, bytes: stu1) + field(2, bytes: stu2)
        let veh = field(1, bytes: trip) + field(4, varint: 2) + field(5, varint: 999_980) + str(7, "635N")
        bytes += field(1, bytes: field(3, varint: 1_000_000))
        bytes += field(2, bytes: str(1, "e1") + field(3, bytes: tu))
        bytes += field(2, bytes: str(1, "e2") + field(4, bytes: veh))
        let feed = try GTFSRealtime.parse(Data(bytes))
        XCTAssertEqual(feed.timestamp, 1_000_000)
        XCTAssertEqual(feed.trips.count, 1)
        XCTAssertEqual(feed.trips[0].stops.map { $0.stopId }, ["635N", "634N"])
        XCTAssertEqual(feed.vehicles.first?.status, "IN_TRANSIT_TO")
        XCTAssertEqual(tripStem("010000_6..N01R"), "010000_6..N")
    }
}
