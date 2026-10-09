import XCTest
@testable import WhichWayCore

/// The itinerary of a ride in progress, from the train the phone believes the rider boarded: its own arrival at the
/// leg's last stop and the connection it makes, for the clock the trip ends by.
final class RidingItineraryTests: XCTestCase {
    /// The itinerary's times are moments at the platform: the feed's times less each line's lag.
    private let lagG = PlatformTiming.recordedLag(route: "G"), lagC = PlatformTiming.recordedLag(route: "C")

    private func schedule() throws -> ClientSchedule {
        let json = """
        {"lines": {"G_N": {"stops": ["g0","g1","g2","g3"], "names": ["a","b","Hoyt","d"], "run_sec": [120, 120, 120]},
                   "C_N": {"stops": ["c0","c1","c2","c3"], "names": ["x","Hoyt","y","14 St"], "run_sec": [150, 150, 150]}}}
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
    /// The G from g0 to Hoyt (idx 2), a 120 s walk, the C from Hoyt (idx 1) to 14 St (idx 3).
    private var option: PathOption {
        var p = PathOption(id: "G>C", legs: [PathLeg(from: "g0", to: "g2", keys: ["G_N"], routes: ["G"], idx: ["G_N": (0, 2)], schedRideSec: 240, nStops: 2),
                                            PathLeg(from: "c1", to: "c3", keys: ["C_N"], routes: ["C"], idx: ["C_N": (1, 3)], schedRideSec: 300, nStops: 2)],
                           transfer: PathTransfer(stop: "g2", stop2: "c1", station: "Hoyt", walkSec: 120))
        p.schedSec = 240 + 120 + 300
        p.wait2Sec = 300
        return p
    }
    /// The G boarded at 1000, as the departure log kept it: its time at the platform, and the feed's latest times since.
    private var boardedG: BoardingCandidate {
        BoardingCandidate(trainId: "G_N|g1", key: "G_N", route: "G", boardTs: 1000, alightTs: 1690, stopsToAlight: 2, chosen: false, onLeg: true,
                          boardIdx: 0, alightIdx: 2, stopTs: [1: 1400, 2: 1690])
    }

    func testTheBoardedTrainAndTheFirstConnectionItMakes() throws {
        let sched = try schedule()
        // the G has passed g0 and g1: the feed lists only Hoyt now; one C leaves Hoyt too soon for the walk, the next makes it
        // (feed times; the G pulls in at Hoyt at 1700 − lagG, the walk takes 120, the C pulls in at its feed time − lagC)
        let gAt = 1700 - lagG
        let boards = ["G_N": board("G_N", [train("g1", key: "G_N", points: [(2, 1700), (3, 1820)])]),
                      "C_N": board("C_N", [train("c1", key: "C_N", points: [(1, gAt + 120 + lagC - 30), (2, 1950), (3, 2100)]),
                                           train("c2", key: "C_N", points: [(1, gAt + 120 + lagC + 80), (2, 2050), (3, 2200)]),
                                           train("c3", key: "C_N", points: [(1, gAt + 120 + lagC + 280), (3, 2400)])])]
        let it = try XCTUnwrap(ridingItinerary(boards: boards, schedule: sched, option: option, leg: 0, boarded: boardedG, now: 1500))
        XCTAssertEqual(it.legs.count, 2)
        XCTAssertEqual(it.boardTs, 1000 - lagG)
        XCTAssertEqual(it.legs[0].arriveTs, gAt)                // the feed's latest for Hoyt, not the log's older 1690
        XCTAssertEqual(it.legs[1].train.tripId, "c2")
        XCTAssertEqual(it.arriveTs, 2200 - lagC)
        XCTAssertEqual(it.walkSec, 120)
        XCTAssertEqual(it.connectionMarginSec, 80)
        XCTAssertEqual(it.nextIfMissedSec, 200)
        XCTAssertEqual(it.legs[0].schedRideSec, 240)
    }

    func testWithoutAConnectingTrainTheScheduleStandsInAndWithTheTrainGoneThereIsNothing() throws {
        let sched = try schedule()
        var boards = ["G_N": board("G_N", [train("g1", key: "G_N", points: [(3, 1820)])]), "C_N": board("C_N", [])]
        // the G is past Hoyt already (the alighting not yet felt): the log's kept time at Hoyt stands, and the
        // connection is the schedule's: the walk from now, half a headway, the scheduled ride
        let it = try XCTUnwrap(ridingItinerary(boards: boards, schedule: sched, option: option, leg: 0, boarded: boardedG, now: 1750))
        XCTAssertEqual(it.legs.count, 1)
        XCTAssertEqual(it.legs[0].arriveTs, 1690 - lagG)
        XCTAssertEqual(it.arriveTs, max(1690 - lagG + 120, 1750) + 300 + 300)
        XCTAssertNil(it.connectionMarginSec)
        boards["G_N"] = board("G_N", [])
        XCTAssertNil(ridingItinerary(boards: boards, schedule: sched, option: option, leg: 0, boarded: boardedG, now: 1750))
    }

    func testOnTheLastLegTheTrainsOwnArrivalIsTheItinerary() throws {
        let sched = try schedule()
        let boards = ["C_N": board("C_N", [train("c2", key: "C_N", points: [(2, 2050), (3, 2210)])])]
        let c = BoardingCandidate(trainId: "C_N|c2", key: "C_N", route: "C", boardTs: 1900, alightTs: 2200, stopsToAlight: 2, chosen: true, onLeg: true,
                                  boardIdx: 1, alightIdx: 3, stopTs: [2: 2050, 3: 2200])
        let it = try XCTUnwrap(ridingItinerary(boards: boards, schedule: sched, option: option, leg: 1, boarded: c, now: 2000))
        XCTAssertEqual(it.legs.count, 1)
        XCTAssertEqual(it.boardTs, 1900 - lagC)
        XCTAssertEqual(it.arriveTs, 2210 - lagC)
        XCTAssertEqual(it.totalSec, 210 - lagC)
        XCTAssertEqual(it.rideVsSchedSec, 310 - 660)
    }


    func testThePlansTrainBecomesTheRideWhenNoneWasFeltAndTheConnectionStandsBetweenTrains() throws {
        // no pull-away felt or named: the train the planner's itinerary boards is the ride, as the log would have kept it
        let sched = try schedule()
        let g = train("g1", key: "G_N", points: [(0, 1000 + lagG), (1, 1400 + lagG), (2, 1690 + lagG)])
        let c = train("c1", key: "C_N", points: [(1, 1900 + lagC), (3, 2500 + lagC)])
        let c2 = train("c2", key: "C_N", points: [(1, 2200 + lagC), (3, 2800 + lagC)])
        let boards = ["G_N": board("G_N", [g]), "C_N": board("C_N", [c, c2])]
        let it = try XCTUnwrap(pathTrips(boards: boards, schedule: sched, option: option, now: 900).first)
        let pc = try XCTUnwrap(plannedCandidate(it, option: option, leg: 0))
        XCTAssertEqual(pc.trainId, "G_N|g1")
        XCTAssertEqual(pc.key, "G_N")
        XCTAssertEqual(pc.boardTs, 1000 + lagG, accuracy: 1e-6, "the feed's time at the boarding stop, as the log keeps it")
        XCTAssertEqual(pc.boardIdx, 0)
        XCTAssertEqual(pc.alightIdx, 2)
        XCTAssertEqual(pc.stopsToAlight, 2)
        XCTAssertEqual(pc.stopTs[2]!, 1690 + lagG, accuracy: 1e-6)
        XCTAssertTrue(pc.chosen)
        // the ride it describes: the G's own arrival at Hoyt and the C it makes there
        let ride = try XCTUnwrap(ridingItinerary(boards: boards, schedule: sched, option: option, leg: 0, boarded: pc, now: 1100))
        XCTAssertEqual(ride.legs.count, 2)
        XCTAssertEqual(ride.legs[0].arriveTs, 1690, accuracy: 1e-6)
        XCTAssertEqual(ride.legs[1].train.id, "C_N|c1")
        XCTAssertEqual(ride.nextIfMissedSec!, 300, accuracy: 1e-6)
        // an itinerary carrying only the leg in hand names its train at index 0
        XCTAssertEqual(plannedCandidate(ride, option: option, leg: 0)?.trainId, "G_N|g1")
        // walked off at Hoyt: the connection from there, with the one after it
        let conn = try XCTUnwrap(connectionItinerary(boards: boards, schedule: sched, option: option, leg: 1, now: 1750))
        XCTAssertEqual(conn.legs.count, 1)
        XCTAssertEqual(conn.legs[0].train.id, "C_N|c1")
        XCTAssertEqual(conn.boardTs, 1900, accuracy: 1e-6)
        XCTAssertEqual(conn.arriveTs, 2500, accuracy: 1e-6)
        XCTAssertEqual(conn.nextIfMissedSec!, 300, accuracy: 1e-6)
        // once the first C has pulled away the next one is the connection
        XCTAssertEqual(connectionItinerary(boards: boards, schedule: sched, option: option, leg: 1, now: 1900 + PlatformTiming.dwellSec + 1)?.legs[0].train.id, "C_N|c2")
    }
}
