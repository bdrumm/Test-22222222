import Foundation

// Trip candidates on a leg, itineraries on a path (one or two legs), scheduled headways and expected times.
// Port of segmentTrips / legTrips / pathTrips / schedHeadwayAt in site/rt-client.js.

struct TripCandidate: Identifiable {
    var id: String { train.id }
    var train: LiveTrain
    var key: String
    var boardTs: Double
    var arriveTs: Double
    var rideSec: Double
    var schedRideSec: Double?
    var stopsToOrigin: Int
    var arriveLoTs: Double? = nil
    var arriveHiTs: Double? = nil
    var feedArriveTs: Double? = nil
    var arriveSource: String? = nil
    var rideVsSchedSec: Double? { schedRideSec.map { rideSec - $0 } }

    /// "10:49–10:54 · feed says 10:49" when the engine's window and the feed's own time are known.
    var rangeText: String? {
        guard let lo = arriveLoTs, let hi = arriveHiTs else { return nil }
        var s = "\(Fmt.hhmm(lo))–\(Fmt.hhmm(hi))"
        if let f = feedArriveTs, abs(f - arriveTs) >= 60 { s += " · feed says \(Fmt.hhmm(f))" }
        return s
    }
}

struct Itinerary: Identifiable {
    var id: String { legs.map { $0.train.tripId }.joined(separator: "+") }
    var legs: [TripCandidate]
    var boardTs: Double
    var arriveTs: Double
    var totalSec: Double
    var walkSec: Double
    var waitAtTransferSec: Double?
    var connectionMarginSec: Double?
    var nextIfMissedSec: Double?
    var schedRideSec: Double
    var rideVsSchedSec: Double
}

/// Trains of a line board that carry a rider from fromIdx to toIdx (feed ETAs at both stops). The times are the
/// moments at the platform (PlatformTiming): the train pulling in to board, and pulling in at the far end, not
/// the feed's later time for the stop that the engine is calibrated on.
func segmentTrips(_ lb: LineBoard, line: LineTopology, fromIdx: Int, toIdx: Int, now: Double, maxN: Int = 6) -> [TripCandidate] {
    let sched = line.runBetween(fromIdx, toIdx).map(Double.init)
    var out: [TripCandidate] = []
    for t in lb.trains {
        guard let boardF = t.points.first(where: { $0.idx == fromIdx })?.ts, let arriveF = t.points.first(where: { $0.idx == toIdx })?.ts else { continue }
        let at = { (ts: Double) in PlatformTiming.atPlatform(ts, route: t.route) }
        let board = at(boardF), arrive = at(arriveF)
        // a train that has pulled away is gone: the countdown reaching zero (the train pulling in) keeps it for the dwell
        if board + PlatformTiming.dwellSec < now || arrive <= board { continue }
        var c = TripCandidate(train: t, key: lb.key, boardTs: board, arriveTs: arrive, rideSec: arrive - board, schedRideSec: sched, stopsToOrigin: max(1, fromIdx - t.nextIdx + 1))
        if let pt = t.pred?.point(at: toIdx) { c.arriveLoTs = at(pt.loTs); c.arriveHiTs = at(pt.hiTs); c.arriveSource = pt.source }
        c.feedArriveTs = t.feedPoints?.first(where: { $0.idx == toIdx }).map { at($0.ts) }
        out.append(c)
    }
    out.sort { $0.boardTs < $1.boardTs }
    return Array(out.prefix(maxN))
}

/// Candidates for a merged leg (parallel routes), merged by boarding time.
func legTrips(boards: [String: LineBoard], schedule: ClientSchedule, leg: PathLeg, now: Double, maxN: Int = 6) -> [TripCandidate] {
    var out: [TripCandidate] = []
    for k in leg.keys {
        guard let lb = boards[k], let line = schedule.lines[k], let ix = leg.idx[k] else { continue }
        out.append(contentsOf: segmentTrips(lb, line: line, fromIdx: ix.from, toIdx: ix.to, now: now, maxN: maxN))
    }
    out.sort { $0.boardTs < $1.boardTs }
    return Array(out.prefix(maxN))
}

/// Live itineraries for a path option, earliest arrival first.
func pathTrips(boards: [String: LineBoard], schedule: ClientSchedule, option: PathOption, now: Double, maxN: Int = 5) -> [Itinerary] {
    let sched = Double(option.schedSec)
    if option.legs.count == 1 {
        let its = legTrips(boards: boards, schedule: schedule, leg: option.legs[0], now: now, maxN: maxN + 4).map { t in
            Itinerary(legs: [t], boardTs: t.boardTs, arriveTs: t.arriveTs, totalSec: t.arriveTs - now, walkSec: 0, waitAtTransferSec: nil, connectionMarginSec: nil,
                      nextIfMissedSec: nil, schedRideSec: sched, rideVsSchedSec: t.rideSec - sched)
        }
        return Array(its.sorted { $0.arriveTs < $1.arriveTs }.prefix(maxN))
    }
    guard let transfer = option.transfer, option.legs.count == 2 else { return [] }
    let walk = Double(transfer.walkSec)
    let firsts = legTrips(boards: boards, schedule: schedule, leg: option.legs[0], now: now, maxN: maxN + 2)
    let seconds = legTrips(boards: boards, schedule: schedule, leg: option.legs[1], now: now, maxN: 40)
    var out: [Itinerary] = []
    var seen = Set<String>()
    for a in firsts {
        guard let b = seconds.first(where: { $0.boardTs >= a.arriveTs + walk }), !seen.contains(b.train.tripId) else { continue }
        seen.insert(b.train.tripId)
        let next = seconds.first(where: { $0.boardTs > b.boardTs })
        out.append(Itinerary(legs: [a, b], boardTs: a.boardTs, arriveTs: b.arriveTs, totalSec: b.arriveTs - now, walkSec: walk,
                             waitAtTransferSec: b.boardTs - a.arriveTs, connectionMarginSec: b.boardTs - a.arriveTs - walk,
                             nextIfMissedSec: next.map { $0.boardTs - b.boardTs }, schedRideSec: sched, rideVsSchedSec: (b.arriveTs - a.boardTs) - sched))
    }
    return Array(out.sorted { $0.arriveTs < $1.arriveTs }.prefix(maxN))
}

/// The itinerary of a ride in progress: leg `leg` is the train the rider is on (the departure log's time at the
/// platform, the feed's latest time at the leg's last stop), and the leg after it, if any, the first train that
/// makes the connection, as for a fresh itinerary. The planner's own itineraries moved on to the next train when
/// this one left, so they no longer say when this ride ends; this does. Nil when the train is gone from the feed
/// with no time kept for the alighting stop. Without a connecting train in the feeds yet, the arrival is the
/// schedule's (half a headway's wait and the scheduled ride) and the itinerary carries only the leg in hand.
func ridingItinerary(boards: [String: LineBoard], schedule: ClientSchedule, option: PathOption, leg: Int, boarded c: BoardingCandidate, now: Double) -> Itinerary? {
    guard option.legs.indices.contains(leg), let ix = option.legs[leg].idx[c.key], let lb = boards[c.key],
          let t = lb.trains.first(where: { $0.id == c.trainId }) else { return nil }
    guard let arriveF = t.points.first(where: { $0.idx == ix.to })?.ts ?? c.stopTs[ix.to] else { return nil }
    let at = { (ts: Double) in PlatformTiming.atPlatform(ts, route: t.route) }
    let board = at(c.boardTs)
    let arriveTs = max(at(arriveF), board + 1)
    var a = TripCandidate(train: t, key: c.key, boardTs: board, arriveTs: arriveTs, rideSec: arriveTs - board,
                          schedRideSec: schedule.lines[c.key]?.runBetween(ix.from, ix.to).map(Double.init), stopsToOrigin: 0)
    if let pt = t.pred?.point(at: ix.to) { a.arriveLoTs = at(pt.loTs); a.arriveHiTs = at(pt.hiTs); a.arriveSource = pt.source }
    a.feedArriveTs = t.feedPoints?.first(where: { $0.idx == ix.to }).map { at($0.ts) }
    let sched = Double(option.schedSec)
    if leg == option.legs.count - 1 {
        return Itinerary(legs: [a], boardTs: board, arriveTs: a.arriveTs, totalSec: a.arriveTs - now, walkSec: 0, waitAtTransferSec: nil, connectionMarginSec: nil,
                         nextIfMissedSec: nil, schedRideSec: sched, rideVsSchedSec: a.rideSec - sched)
    }
    guard leg == 0, option.legs.count == 2, let transfer = option.transfer else { return nil }
    let walk = Double(transfer.walkSec)
    // the connection: the first train at the transfer platform after the walk, or after now when the first leg is already behind
    let earliest = max(a.arriveTs + walk, now)
    // trains still standing at the platform when the rider gets there are listed too; the connection is one that
    // pulls in after the rider does
    let seconds = legTrips(boards: boards, schedule: schedule, leg: option.legs[1], now: earliest, maxN: 40).filter { $0.boardTs >= earliest }
    if let b = seconds.first {
        let next = seconds.first(where: { $0.boardTs > b.boardTs })
        return Itinerary(legs: [a, b], boardTs: board, arriveTs: b.arriveTs, totalSec: b.arriveTs - now, walkSec: walk,
                         waitAtTransferSec: b.boardTs - a.arriveTs, connectionMarginSec: b.boardTs - a.arriveTs - walk,
                         nextIfMissedSec: next.map { $0.boardTs - b.boardTs }, schedRideSec: sched, rideVsSchedSec: (b.arriveTs - board) - sched)
    }
    let ride2 = Double(option.legs[1].schedRideSec ?? 0)
    let arrive2 = earliest + option.wait2Sec + ride2
    return Itinerary(legs: [a], boardTs: board, arriveTs: arrive2, totalSec: arrive2 - now, walkSec: walk,
                     waitAtTransferSec: earliest + option.wait2Sec - a.arriveTs, connectionMarginSec: nil,
                     nextIfMissedSec: nil, schedRideSec: sched, rideVsSchedSec: (arrive2 - board) - sched)
}

/// The train the planner's itinerary boards on leg `leg` of `option`, as a boarding candidate the departure log
/// could have made for it: the feed's time at the boarding stop (the itinerary's platform moment plus the line's
/// lag), the leg's stops and the train's times at them. The ride the plan has the rider on, when the sensors have
/// not named a train (or the rider has not), so the card and the Live Activity follow it from the boarding time
/// on instead of the planner's next train. `it` is the planner's full itinerary, or one carrying only this leg.
func plannedCandidate(_ it: Itinerary, option: PathOption, leg: Int) -> BoardingCandidate? {
    let i = it.legs.count == option.legs.count ? leg : 0
    guard it.legs.indices.contains(i), option.legs.indices.contains(leg) else { return nil }
    let tc = it.legs[i]
    guard let ix = option.legs[leg].idx[tc.key] else { return nil }
    let lag = PlatformTiming.recordedLag(route: tc.train.route)
    var stopTs: [Int: Double] = [:]
    for p in tc.train.feedPoints ?? tc.train.points where p.idx > ix.from { stopTs[p.idx] = p.ts }
    return BoardingCandidate(trainId: tc.train.id, key: tc.key, route: tc.train.route, boardTs: tc.boardTs + lag, alightTs: tc.arriveTs + lag,
                             stopsToAlight: ix.to - ix.from, chosen: true, onLeg: true, boardIdx: ix.from, alightIdx: ix.to, stopTs: stopTs,
                             progressIdx: tc.train.nextIdx)
}

/// Between trains at the change: the next train of leg `leg` from its platform, the one the rider can still make
/// (a train standing there counts until it pulls away), with the one after it in case it is missed.
func connectionItinerary(boards: [String: LineBoard], schedule: ClientSchedule, option: PathOption, leg: Int, now: Double) -> Itinerary? {
    guard option.legs.indices.contains(leg) else { return nil }
    let trains = legTrips(boards: boards, schedule: schedule, leg: option.legs[leg], now: now, maxN: 6)
    guard let b = trains.first else { return nil }
    let next = trains.first { $0.boardTs > b.boardTs }
    let sched = Double(option.legs[leg].schedRideSec ?? 0)
    return Itinerary(legs: [b], boardTs: b.boardTs, arriveTs: b.arriveTs, totalSec: b.arriveTs - now, walkSec: 0, waitAtTransferSec: nil, connectionMarginSec: nil,
                     nextIfMissedSec: next.map { $0.boardTs - b.boardTs }, schedRideSec: sched, rideVsSchedSec: b.rideSec - sched)
}

/// Scheduled headway (s) of a leg's routes at a stop around `now`, from the per-line schedules.
func schedHeadwayAt(schedule: ClientSchedule, lineSched: [String: [LineSchedEntry]], keys: [String], stop: String, now: Double, windowSec: Double = 1800) -> Double? {
    var rate = 0.0
    for k in keys {
        guard let line = schedule.lines[k], let i = line.stops.firstIndex(of: stop), let entries = lineSched[k], !entries.isEmpty else { continue }
        var n = 0
        for e in entries {
            if e.lastIdx < i { continue }
            guard let run = line.runBetween(i, e.lastIdx) else { continue }
            if abs((e.ts - Double(run)) - now) <= windowSec { n += 1 }
        }
        if n >= 1 { rate += Double(n) / (2 * windowSec) }
    }
    return rate > 0 ? 1 / rate : nil
}

/// Expected time: half the scheduled headway as the wait at the origin and at the change, scheduled rides, the typical
/// time lost on each stretch at this hour, and a hold risk from the hold log.
func evaluate(_ option: inout PathOption, schedule: ClientSchedule, lineSched: [String: [LineSchedEntry]], now: Double,
              holds: HoldsSummary?, deviations: [String: LineDeviation], hour: Int) {
    let hw1 = schedHeadwayAt(schedule: schedule, lineSched: lineSched, keys: option.legs[0].keys, stop: option.legs[0].from, now: now)
    option.wait1Sec = hw1.map { min($0 / 2, 900) } ?? 300
    if option.legs.count > 1 {
        let hw2 = schedHeadwayAt(schedule: schedule, lineSched: lineSched, keys: option.legs[1].keys, stop: option.legs[1].from, now: now)
        option.wait2Sec = hw2.map { min($0 / 2, 900) } ?? 300
    } else {
        option.wait2Sec = 0
    }
    var holdMap: [String: HoldStop] = [:]
    for h in holds?.byStop ?? [] { holdMap[h.stopId] = h }
    for i in option.legs.indices {
        let leg = option.legs[i]
        guard let line = schedule.lines[leg.primaryKey], let ix = leg.idx[leg.primaryKey] else { continue }
        var typical: Double? = nil
        if let dev = deviations[leg.primaryKey] {
            var sum = 0.0
            var any = false
            var s = ix.from + 1
            while s <= ix.to { if let v = dev.typical(stopId: line.stops[s], hour: hour) { sum += v; any = true }; s += 1 }
            typical = any ? sum : nil
        }
        let trips = max(60, (lineSched[leg.primaryKey] ?? []).count)
        var risk = 0.0
        var s = ix.from + 1
        while s <= ix.to { if let h = holdMap[line.stops[s]] { risk += h.perDay * h.medianSec / Double(trips) }; s += 1 }
        option.legs[i].typicalSec = typical
        option.legs[i].holdRiskSec = risk
    }
    option.expectedSec = option.wait1Sec + Double(option.schedSec) + option.typicalSec + option.holdRiskSec + option.wait2Sec
}

func nyHour(_ ts: Double) -> Int {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "America/New_York") ?? .current
    return cal.component(.hour, from: Date(timeIntervalSince1970: ts))
}
