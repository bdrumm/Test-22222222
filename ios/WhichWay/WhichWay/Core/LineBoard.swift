import Foundation

// Every started train of one line right now, from the feeds: projection, reported position, lateness against the
// timetable extract, holds and stalls, segment geometry and the last measured segment speed. Port of lineBoard()
// in site/rt-client.js.

func tripSuffix(_ id: String) -> String {
    let parts = id.split(separator: "_").map(String.init)
    return parts.count >= 3 ? parts.suffix(2).joined(separator: "_") : id
}

/// The suffix without its path code: 020300_L..N01R -> 020300_L..N (some feeds publish ids without the code).
func tripStem(_ id: String) -> String {
    let s = tripSuffix(id)
    guard let dots = s.range(of: "..") else { return s }
    let after = s[dots.upperBound...]
    guard let first = after.first, first == "N" || first == "S" else { return s }
    return String(s[..<dots.upperBound]) + String(first)
}

struct TrainPoint {
    let idx: Int
    let ts: Double
}

struct TrainPosition {
    var status: String
    var stopId: String
    var stopIdx: Int?
    var stopName: String
    var sinceSec: Double
    var holding: Bool
    var stalled: Bool
    var atTerminal: Bool
    var expectedRunSec: Double?
    var positionLatenessSec: Double?

    var text: String {
        let verb = status == "STOPPED_AT" ? "at" : (status == "INCOMING_AT" ? "arriving" : "→")
        var s = "\(verb) \(stopName)"
        if sinceSec >= 60 { s += " · \(Int(sinceSec / 60)) min" }
        return s
    }
}

struct SegmentInfo {
    var fromIdx: Int
    var toIdx: Int
    var distM: Double
    var schedRunSec: Double?
    var schedSpeedKmh: Double?
    var elapsedSec: Double
    var coveredM: Double?
}

struct LastRun {
    var fromStop: String
    var toStop: String
    var runSec: Double
    var distM: Double?
    var speedKmh: Double?
    var schedSpeedKmh: Double?
    var assumedFrom: Bool
}

struct LiveTrain: Identifiable {
    var id: String { key + "|" + tripId }
    var key: String            // line key this board belongs to
    var tripId: String
    var trainId: String?
    var route: String
    var points: [TrainPoint]
    var nextIdx: Int
    var nextName: String
    var etaTs: Double
    var schedTs: Double?
    var schedMethod: String?
    var latenessSec: Double?
    var effectiveLatenessSec: Double?
    var position: TrainPosition?
    var corroboration: String
    var trackChanged: Bool
    var started: Bool
    var segment: SegmentInfo?
    var lastRun: LastRun?

    var label: String { (trainId ?? tripId).trimmingCharacters(in: .whitespaces) }
    var isHeld: Bool { position?.holding == true || position?.stalled == true }
}

struct LineBoard {
    var key: String
    var route: String
    var direction: String
    var now: Double
    var trains: [LiveTrain]
    var nHolding: Int
    var nStalled: Int
    var nFeedOptimistic: Int
}

/// Progress of a train along its line as a fractional stop index, dead-reckoned `age` seconds after the board.
struct TrainProgress {
    var idx: Double
    var state: String       // moving | stopped | holding | stalled | terminal | unknown
    var since: Double?
}

func trainProgress(_ t: LiveTrain, age: Double, line: LineTopology) -> TrainProgress {
    guard let p = t.position, let j = p.stopIdx else { return TrainProgress(idx: max(0, Double(t.nextIdx) - 0.5), state: "unknown", since: nil) }
    let since = p.sinceSec + max(0, age)
    if p.status == "STOPPED_AT" { return TrainProgress(idx: Double(j), state: p.holding ? "holding" : (p.atTerminal ? "terminal" : "stopped"), since: since) }
    if j <= 0 { return TrainProgress(idx: 0, state: "moving", since: since) }
    let run = (j - 1) < line.runSec.count ? line.runSec[j - 1].map(Double.init) : nil
    var frac = run != nil && run! > 0 ? min(0.96, since / run!) : 0.5
    if p.status == "INCOMING_AT" { frac = max(frac, 0.85) }
    return TrainProgress(idx: Double(j - 1) + frac, state: p.stalled ? "stalled" : "moving", since: since)
}

/// Observations across polls: the feed timestamp is when a state began, so "in transit to X" then "stopped at X"
/// times the segment exactly.
final class VehicleHistory {
    static let shared = VehicleHistory()
    private struct Obs { var status: String; var stopId: String; var ts: Double; var lastStopped: String?; var lastRun: LastRun? }
    private var obs: [String: Obs] = [:]

    func reset() { obs = [:] }

    func observe(key: String, vehicle v: RTVehicle, now: Double, line: LineTopology) -> LastRun? {
        guard let stop = v.stopId, let ts = v.timestamp, ts <= now + 60 else { return nil }
        let status = v.status ?? "IN_TRANSIT_TO"
        let prev = obs[key]
        var lastRun = prev?.lastRun
        var lastStopped = prev?.lastStopped
        if let prev = prev, status == "STOPPED_AT", prev.status != "STOPPED_AT", prev.stopId == stop, ts > prev.ts {
            var from: String? = (prev.lastStopped != nil && prev.lastStopped != stop) ? prev.lastStopped : nil
            var assumed = false
            if from == nil, let j = line.stops.firstIndex(of: stop), j > 0 { from = line.stops[j - 1]; assumed = true }
            if let from = from {
                var lr = LastRun(fromStop: from, toStop: stop, runSec: ts - prev.ts, distM: nil, speedKmh: nil, schedSpeedKmh: nil, assumedFrom: assumed)
                if let a = line.stops.firstIndex(of: from), let b = line.stops.firstIndex(of: stop), b == a + 1, a < line.distM.count, let d = line.distM[a] {
                    lr.distM = Double(d)
                    lr.speedKmh = Double(d) / lr.runSec * 3.6
                    if a < line.runSec.count, let r = line.runSec[a], r > 0 { lr.schedSpeedKmh = Double(d) / Double(r) * 3.6 }
                }
                lastRun = lr
            }
        }
        if status == "STOPPED_AT" { lastStopped = stop } else if let prev = prev, prev.status == "STOPPED_AT", prev.stopId != stop { lastStopped = prev.stopId }
        obs[key] = Obs(status: status, stopId: stop, ts: ts, lastStopped: lastStopped, lastRun: lastRun)
        return lastRun
    }
}

private func runBetween(_ line: LineTopology, _ a: Int, _ b: Int) -> Double? {
    line.runBetween(a, b).map(Double.init)
}

/// Build the board for one line key ("6_N") from the parsed feeds.
func lineBoard(schedule: ClientSchedule, lineSched: [LineSchedEntry], feeds: [String: RTFeed], key: String, now: Double) -> LineBoard? {
    guard let line = schedule.lines[key] else { return nil }
    let parts = key.split(separator: "_").map(String.init)
    guard parts.count == 2 else { return nil }
    let route = parts[0], direction = parts[1]
    let c = schedule.constants
    var idx: [String: Int] = [:]
    for (i, s) in line.stops.enumerated() { idx[s] = i }
    var vehicles: [String: RTVehicle] = [:]
    for fd in feeds.values { for v in fd.vehicles where !v.trip.tripId.isEmpty { vehicles[v.trip.key] = v } }
    var trains: [LiveTrain] = []
    for fd in feeds.values {
        for tu in fd.trips {
            guard tu.trip.routeId == route, let first = tu.stops.first, first.stopId.hasSuffix(direction) else { continue }
            var points: [TrainPoint] = []
            for s in tu.stops { if let i = idx[s.stopId], let t = s.eta { points.append(TrainPoint(idx: i, ts: t)) } }
            guard let firstPoint = points.first else { continue }
            let veh = vehicles[tu.trip.key]
            let hasPos = veh != nil && veh!.stopId != nil && veh!.timestamp != nil && veh!.timestamp! <= now + 60
            let lastRun = hasPos ? VehicleHistory.shared.observe(key: tu.trip.key, vehicle: veh!, now: now, line: line) : nil
            let started = hasPos || tu.trip.isAssigned == true
            if !started { continue }
            let j = firstPoint.idx, eta = firstPoint.ts
            // schedule at the next stop: this trip's time at its last canonical stop minus the canonical running time,
            // by stem, else the nearest scheduled trip within 15 min
            var sched: Double? = nil
            var schedMethod: String? = nil
            let stem = tripStem(tu.trip.tripId)
            var bestStem: (Int, Double)? = nil
            var near: Double? = nil
            for e in lineSched {
                if e.lastIdx < j || abs(e.ts - eta) > 4 * 3600 { continue }
                if e.stem == stem {
                    if bestStem == nil || abs(e.ts - eta) < abs(bestStem!.1 - eta) { bestStem = (e.lastIdx, e.ts) }
                    continue
                }
                if let run = runBetween(line, j, e.lastIdx) {
                    let at = e.ts - run
                    if abs(at - eta) <= 900, near == nil || abs(at - eta) < abs(near! - eta) { near = at }
                }
            }
            if let b = bestStem, let run = runBetween(line, j, b.0) { sched = b.1 - run; schedMethod = "trip_stem" }
            if sched == nil, let n = near { sched = n; schedMethod = "nearest" }
            let lateness: Double? = sched.map { eta - $0 }
            var pos: TrainPosition? = nil
            var corroboration = "position_unknown"
            var effective = lateness
            if hasPos, let v = veh, let vstop = v.stopId, let vts = v.timestamp {
                let status = v.status ?? "IN_TRANSIT_TO"
                let pj = idx[vstop]
                let since = max(0, now - vts)
                var holding = false, stalled = false
                var expectedRun: Double? = nil
                var plate: Double? = nil
                let atTerminal = pj == 0 || (pj != nil && pj! == line.stops.count - 1)
                if status == "STOPPED_AT" {
                    holding = since >= c.holdSec && !atTerminal
                } else if let pj = pj, pj > 0, pj - 1 < line.runSec.count, let r = line.runSec[pj - 1] {
                    expectedRun = Double(r)
                    stalled = since > Double(r) + c.stallSlackSec
                }
                if let sched = sched, let pj = pj, pj <= j, let run = runBetween(line, pj, j) {
                    let remaining = status == "STOPPED_AT" ? 0 : (expectedRun.map { max(0, $0 - since) } ?? 0)
                    plate = now + remaining - (sched - run)
                }
                pos = TrainPosition(status: status, stopId: vstop, stopIdx: pj, stopName: pj != nil && pj! < line.names.count ? line.names[pj!] : vstop,
                                    sinceSec: since, holding: holding, stalled: stalled, atTerminal: atTerminal, expectedRunSec: expectedRun, positionLatenessSec: plate)
                if let plate = plate, let lateness = lateness {
                    corroboration = plate - lateness > 60 ? "feed_optimistic" : "agree"
                    effective = max(lateness, plate)
                }
            }
            var segment: SegmentInfo? = nil
            if let p = pos, let pj = p.stopIdx, p.status != "STOPPED_AT", pj > 0, pj - 1 < line.distM.count, let d = line.distM[pj - 1] {
                let run = pj - 1 < line.runSec.count ? line.runSec[pj - 1].map(Double.init) : nil
                let frac: Double? = (run != nil && run! > 0) ? min(0.96, p.sinceSec / run!) : nil
                segment = SegmentInfo(fromIdx: pj - 1, toIdx: pj, distM: Double(d), schedRunSec: run, schedSpeedKmh: run.map { Double(d) / $0 * 3.6 },
                                      elapsedSec: p.sinceSec, coveredM: frac.map { Double(d) * $0 })
            }
            let trackChanged = first.actualTrack != nil && first.schedTrack != nil && first.actualTrack != first.schedTrack
            trains.append(LiveTrain(key: key, tripId: tu.trip.tripId, trainId: tu.trip.trainId, route: route, points: points, nextIdx: j,
                                    nextName: j < line.names.count ? line.names[j] : line.stops[j], etaTs: eta, schedTs: sched, schedMethod: schedMethod,
                                    latenessSec: lateness, effectiveLatenessSec: effective, position: pos, corroboration: corroboration,
                                    trackChanged: trackChanged, started: started, segment: segment, lastRun: lastRun))
        }
    }
    trains.sort { a, b in a.nextIdx != b.nextIdx ? a.nextIdx > b.nextIdx : a.etaTs < b.etaTs }
    return LineBoard(key: key, route: route, direction: direction, now: now, trains: trains,
                     nHolding: trains.filter { $0.position?.holding == true }.count,
                     nStalled: trains.filter { $0.position?.stalled == true }.count,
                     nFeedOptimistic: trains.filter { $0.corroboration == "feed_optimistic" }.count)
}
