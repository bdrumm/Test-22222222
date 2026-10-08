import Foundation

/// A running estimate that leans toward recent trips: the first few samples average, later ones blend in at a
/// quarter weight each, so a change of habit shows within a few trips.
struct PaceStat: Codable, Equatable {
    var mean = 0.0
    var n = 0

    mutating func add(_ v: Double) {
        mean = n == 0 ? v : mean + (v - mean) * max(0.25, 1.0 / Double(n + 1))
        n += 1
    }
}

/// What this phone has learned about its rider: how fast they walk on the street, how long they take from the
/// street to the platform at each station, how long their changes take, and how long their usual places are
/// from their stations. Lives on the phone; nothing here is shared unless the rider opts into telemetry, and
/// then only each trip's own measurements go.
struct PersonalModel: Codable, Equatable {
    var walkSpeed = PaceStat()                        // metres per minute toward the station
    var access: [String: PaceStat] = [:]              // origin station id -> seconds from the radius to the platform
    var accessDefault = PaceStat()
    var transfer: [String: PaceStat] = [:]            // transfer station name -> seconds walking between trains
    var transferDefault = PaceStat()
    var placeToStation: [String: PaceStat] = [:]      // "placeId|stationId" -> seconds from leaving the place to the station
    /// "origin|dest|leg" -> line key -> rides the sensors put on that line. Optional so models saved before it
    /// still load.
    var lineChoices: [String: [String: Int]]? = nil
    var trips = 0
    var lastLearnedTs: Double?

    static let defaultWalkSpeed = 80.0

    var walkSpeedMPerMin: Double { walkSpeed.n > 0 ? min(130, max(45, walkSpeed.mean)) : PersonalModel.defaultWalkSpeed }

    /// Seconds from the street to the platform at a station: its own figure, else the rider's average anywhere.
    func accessSec(station: String) -> Double? {
        if let s = access[station], s.n > 0 { return s.mean }
        return accessDefault.n > 0 ? accessDefault.mean : nil
    }

    /// Seconds the rider's changes take at a station, once seen twice there (else the average, once seen three times).
    func transferSec(station: String) -> Double? {
        if let s = transfer[station], s.n >= 2 { return s.mean }
        return transferDefault.n >= 3 ? transferDefault.mean : nil
    }

    func placeToStationSec(place: String, station: String) -> Double? {
        guard let s = placeToStation["\(place)|\(station)"], s.n > 0 else { return nil }
        return s.mean
    }

    /// How often this rider has actually ridden the plan's line on a leg of this trip, once three rides are
    /// known; the line inference pulls its prior toward it. nil until then.
    func chosenLineShare(origin: String, dest: String, leg: Int, chosenKey: String) -> Double? {
        guard let m = lineChoices?["\(origin)|\(dest)|\(leg)"] else { return nil }
        let n = m.values.reduce(0, +)
        guard n >= 3 else { return nil }
        return Double(m[chosenKey] ?? 0) / Double(n)
    }

    /// Minutes from a point this far away to standing on the platform at the station.
    func walkMinutes(meters: Double, station: String?) -> Int {
        let sec = meters / walkSpeedMPerMin * 60 + (station.flatMap { accessSec(station: $0) } ?? 0)
        return max(1, Int((sec / 60).rounded()))
    }

    /// Learns what a finished trip measured. Returns whether anything was learned.
    @discardableResult
    mutating func learn(_ t: TripTimeline) -> Bool {
        var any = false
        if let v = t.walkSpeedMPerMin { walkSpeed.add(min(150, max(30, v))); any = true }
        if let a = t.accessSec, a >= 15, a <= 900 {
            access[t.originStation, default: PaceStat()].add(a)
            accessDefault.add(a)
            any = true
        }
        if let x = t.transferStation, let s = t.transferWalkSec, s >= 30, s <= 900 {    // under half a minute is a false alighting, not a change
            transfer[x, default: PaceStat()].add(s)
            transferDefault.add(s)
            any = true
        }
        if let p = t.placeId, let arr = t.arrivedStationTs, arr - t.startTs >= 30, arr - t.startTs <= 3600 {
            placeToStation["\(p)|\(t.originStation)", default: PaceStat()].add(arr - t.startTs)
            any = true
        }
        for b in t.boarded where b.verdict != .unsure {
            var m = lineChoices ?? [:]
            m["\(t.originStation)|\(t.destStation)|\(b.leg)", default: [:]][b.key, default: 0] += 1
            lineChoices = m
            any = true
        }
        if any { trips += 1; lastLearnedTs = t.endedTs }
        return any
    }
}
