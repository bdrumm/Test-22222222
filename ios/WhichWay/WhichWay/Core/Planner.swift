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

/// Trains of a line board that carry a rider from fromIdx to toIdx (feed ETAs at both stops).
func segmentTrips(_ lb: LineBoard, line: LineTopology, fromIdx: Int, toIdx: Int, now: Double, maxN: Int = 6) -> [TripCandidate] {
    let sched = line.runBetween(fromIdx, toIdx).map(Double.init)
    var out: [TripCandidate] = []
    for t in lb.trains {
        guard let board = t.points.first(where: { $0.idx == fromIdx })?.ts, let arrive = t.points.first(where: { $0.idx == toIdx })?.ts else { continue }
        if board < now - 60 || arrive <= board { continue }
        var c = TripCandidate(train: t, key: lb.key, boardTs: board, arriveTs: arrive, rideSec: arrive - board, schedRideSec: sched, stopsToOrigin: max(1, fromIdx - t.nextIdx + 1))
        if let pt = t.pred?.point(at: toIdx) { c.arriveLoTs = pt.loTs; c.arriveHiTs = pt.hiTs; c.arriveSource = pt.source }
        c.feedArriveTs = t.feedPoints?.first(where: { $0.idx == toIdx })?.ts
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
