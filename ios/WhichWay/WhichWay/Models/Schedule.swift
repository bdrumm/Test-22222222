import Foundation

// MARK: - client_schedule.json (published by the site build)

struct LineTopology: Decodable {
    var stops: [String]
    var names: [String]
    var runSec: [Int?]
    var distM: [Int?]

    enum CodingKeys: String, CodingKey { case stops, names, runSec = "run_sec", distM = "dist_m" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        stops = try c.decode([String].self, forKey: .stops)
        names = try c.decode([String].self, forKey: .names)
        runSec = (try c.decodeIfPresent([Double?].self, forKey: .runSec) ?? []).map { $0.map { Int($0.rounded()) } }
        distM = (try c.decodeIfPresent([Double?].self, forKey: .distM) ?? []).map { $0.map { Int($0.rounded()) } }
    }

    /// Scheduled running time between stop indices a < b along this line (nil when a segment is unknown).
    func runBetween(_ a: Int, _ b: Int) -> Int? {
        guard a >= 0, b <= stops.count - 1, a <= b else { return nil }
        var total = 0
        var i = a
        while i < b {
            guard i < runSec.count, let r = runSec[i] else { return nil }
            total += r
            i += 1
        }
        return total
    }
}

struct TransferOption: Decodable {
    var line: String
    var stop: String
    var minSec: Int
    enum CodingKeys: String, CodingKey { case line, stop, minSec = "min_sec" }
}

struct JourneyLeg: Decodable {
    var fromStop: String
    var toStop: String
    var routes: [String]
    var fromName: String?
    var toName: String?
    var transferMin: Double?
    enum CodingKeys: String, CodingKey { case fromStop = "from_stop", toStop = "to_stop", routes, fromName = "from_name", toName = "to_name", transferMin = "transfer_min" }
}

struct Journey: Decodable {
    var id: String
    var label: String
    var legs: [JourneyLeg]
}

struct ScheduleConstants: Decodable {
    var holdSec: Double = 150
    var stallSlackSec: Double = 120
    var pastSlackSec: Double = 90
    var gapRatio: Double = 1.5
    var bunchingRatio: Double = 0.5
    var holdExtraSec: Double = 600
    var minHeadwaySec: Double = 90

    enum CodingKeys: String, CodingKey {
        case holdSec = "hold_sec", stallSlackSec = "stall_slack_sec", pastSlackSec = "past_slack_sec", gapRatio = "gap_ratio"
        case bunchingRatio = "bunching_ratio", holdExtraSec = "hold_extra_sec", minHeadwaySec = "min_headway_sec"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        holdSec = try c.decodeIfPresent(Double.self, forKey: .holdSec) ?? 150
        stallSlackSec = try c.decodeIfPresent(Double.self, forKey: .stallSlackSec) ?? 120
        pastSlackSec = try c.decodeIfPresent(Double.self, forKey: .pastSlackSec) ?? 90
        gapRatio = try c.decodeIfPresent(Double.self, forKey: .gapRatio) ?? 1.5
        bunchingRatio = try c.decodeIfPresent(Double.self, forKey: .bunchingRatio) ?? 0.5
        holdExtraSec = try c.decodeIfPresent(Double.self, forKey: .holdExtraSec) ?? 600
        minHeadwaySec = try c.decodeIfPresent(Double.self, forKey: .minHeadwaySec) ?? 90
    }
}

struct ClientSchedule: Decodable {
    var generatedAt: String?
    var serviceDate: String?
    var lines: [String: LineTopology]
    var feeds: [String: String]
    var targetFeeds: [String]
    var routeFeeds: [String: String]
    var transfers: [String: [TransferOption]]
    var journeys: [Journey]
    var constants: ScheduleConstants
    var demoNow: Double?
    var alertsUrl: String?

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at", serviceDate = "service_date", lines, feeds, targetFeeds = "target_feeds", routeFeeds = "route_feeds"
        case transfers, journeys, constants, demoNow = "demo_now", alertsUrl = "alerts_url"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt)
        serviceDate = try c.decodeIfPresent(String.self, forKey: .serviceDate)
        lines = try c.decode([String: LineTopology].self, forKey: .lines)
        feeds = try c.decodeIfPresent([String: String].self, forKey: .feeds) ?? [:]
        targetFeeds = try c.decodeIfPresent([String].self, forKey: .targetFeeds) ?? []
        routeFeeds = try c.decodeIfPresent([String: String].self, forKey: .routeFeeds) ?? [:]
        transfers = try c.decodeIfPresent([String: [TransferOption]].self, forKey: .transfers) ?? [:]
        journeys = try c.decodeIfPresent([Journey].self, forKey: .journeys) ?? []
        constants = try c.decodeIfPresent(ScheduleConstants.self, forKey: .constants) ?? ScheduleConstants()
        demoNow = try c.decodeIfPresent(Double.self, forKey: .demoNow)
        alertsUrl = try c.decodeIfPresent(String.self, forKey: .alertsUrl)
    }
}

// MARK: - client_lines.json: per line, each trip's scheduled time at the last canonical stop it serves

struct LineSchedEntry: Decodable {
    var stem: String
    var lastIdx: Int
    var ts: Double

    init(from decoder: Decoder) throws {
        var c = try decoder.unkeyedContainer()
        stem = try c.decode(String.self)
        lastIdx = try c.decode(Int.self)
        ts = try c.decode(Double.self)
    }
}

struct ClientLines: Decodable {
    var lines: [String: [LineSchedEntry]]
}

// MARK: - holds.json / segments.json (subsets the app uses)

struct HoldStop: Decodable {
    var stopId: String
    var name: String
    var perDay: Double
    var medianSec: Double
    enum CodingKeys: String, CodingKey { case stopId = "stop_id", name, perDay = "per_day", medianSec = "median_sec" }
}

struct HoldsSummary: Decodable {
    var n: Int
    var byStop: [HoldStop]
    enum CodingKeys: String, CodingKey { case n, byStop = "by_stop" }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        n = try c.decodeIfPresent(Int.self, forKey: .n) ?? 0
        byStop = try c.decodeIfPresent([HoldStop].self, forKey: .byStop) ?? []
    }
}

struct SegmentStat: Decodable {
    var fromName: String
    var toName: String
    var n: Int
    var medianRunSec: Double
    var schedRunSec: Double?
    var ratio: Double?
    var distM: Double?
    var speedKmh: Double?
    var schedSpeedKmh: Double?
    var byHourRunSec: [Double?]
    enum CodingKeys: String, CodingKey {
        case fromName = "from_name", toName = "to_name", n, medianRunSec = "median_run_sec", schedRunSec = "sched_run_sec", ratio, distM = "dist_m"
        case speedKmh = "speed_kmh", schedSpeedKmh = "sched_speed_kmh", byHourRunSec = "by_hour_run_sec"
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        fromName = try c.decodeIfPresent(String.self, forKey: .fromName) ?? ""
        toName = try c.decodeIfPresent(String.self, forKey: .toName) ?? ""
        n = try c.decodeIfPresent(Int.self, forKey: .n) ?? 0
        medianRunSec = try c.decodeIfPresent(Double.self, forKey: .medianRunSec) ?? 0
        schedRunSec = try c.decodeIfPresent(Double.self, forKey: .schedRunSec)
        ratio = try c.decodeIfPresent(Double.self, forKey: .ratio)
        distM = try c.decodeIfPresent(Double.self, forKey: .distM)
        speedKmh = try c.decodeIfPresent(Double.self, forKey: .speedKmh)
        schedSpeedKmh = try c.decodeIfPresent(Double.self, forKey: .schedSpeedKmh)
        byHourRunSec = try c.decodeIfPresent([Double?].self, forKey: .byHourRunSec) ?? []
    }
}

struct SegmentsSummary: Decodable {
    var n: Int
    var byKey: [String: SegmentStat]
    enum CodingKeys: String, CodingKey { case n, byKey = "by_key" }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        n = try c.decodeIfPresent(Int.self, forKey: .n) ?? 0
        byKey = try c.decodeIfPresent([String: SegmentStat].self, forKey: .byKey) ?? [:]
    }
}

// MARK: - lines/<key>.json deviation grid (typical time lost per stop by hour)

struct DeviationStop: Decodable {
    var stopId: String
    var name: String
    enum CodingKeys: String, CodingKey { case stopId = "stop_id", name }
}

struct LineDeviation: Decodable {
    var stops: [DeviationStop]
    var grid: [[Double?]]
    var nTrips: Int
    enum CodingKeys: String, CodingKey { case stops, grid, nTrips = "n_trips" }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        stops = try c.decodeIfPresent([DeviationStop].self, forKey: .stops) ?? []
        grid = try c.decodeIfPresent([[Double?]].self, forKey: .grid) ?? []
        nTrips = try c.decodeIfPresent(Int.self, forKey: .nTrips) ?? 0
    }

    /// Mean lateness change (s) arriving at a stop at an hour of day, if known.
    func typical(stopId: String, hour: Int) -> Double? {
        guard let r = stops.firstIndex(where: { $0.stopId == stopId }), r < grid.count, hour >= 0, hour < grid[r].count else { return nil }
        return grid[r][hour]
    }
}

struct LineHistory: Decodable {
    var deviation: LineDeviation?
}
