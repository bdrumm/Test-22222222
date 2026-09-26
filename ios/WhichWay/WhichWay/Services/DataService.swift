import Foundation
import Observation

/// Loads the published data (client schedule, timetable extract, hold log, segment run times, per-line deviation
/// grids) from the site and polls the MTA GTFS-Realtime feeds the visible screens need, every `pollSec` seconds.
@MainActor
@Observable
final class DataService {
    static let defaultBase = "https://bdrumm.github.io/Test-22222222/data/"

    private(set) var baseURL: String
    private(set) var pollSec: Double

    private(set) var schedule: ClientSchedule?
    private(set) var lineSched: [String: [LineSchedEntry]] = [:]
    private(set) var holds: HoldsSummary?
    private(set) var segments: SegmentsSummary?
    private(set) var deviations: [String: LineDeviation] = [:]
    private(set) var index: StationIndex?
    private(set) var feeds: [String: RTFeed] = [:]
    private(set) var boards: [String: LineBoard] = [:]
    private(set) var alerts: [RouteAlert] = []
    private(set) var lastUpdate: Date?
    private(set) var lastError: String?
    private(set) var loading = false
    /// Bumps when the schedule, the hold log, the segment stats or a deviation grid (re)load.
    private(set) var staticVersion = 0
    /// Bumps after every feed poll.
    private(set) var tick = 0
    /// Line keys ("F_N") the visible screens need boards for.
    private(set) var wanted: Set<String> = []

    @ObservationIgnored private var wantedBy: [String: Set<String>] = [:]
    @ObservationIgnored private var demoOffset: Double? = nil
    @ObservationIgnored private var pollTask: Task<Void, Never>? = nil
    @ObservationIgnored private var deviationRequested: Set<String> = []
    @ObservationIgnored private var pollCount = 0

    init() {
        baseURL = UserDefaults.standard.string(forKey: "baseURL") ?? DataService.defaultBase
        let p = UserDefaults.standard.double(forKey: "pollSec")
        pollSec = p >= 10 ? p : 30
    }

    /// True when the schedule pins the clock (the synthetic preview's demo_now).
    var isDemo: Bool { demoOffset != nil }

    /// Wall clock, shifted to the schedule's demo clock when it has one.
    var now: Double { Date().timeIntervalSince1970 + (demoOffset ?? 0) }

    func url(_ path: String) -> URL? {
        if path.hasPrefix("http://") || path.hasPrefix("https://") { return URL(string: path) }
        var base = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if !base.hasSuffix("/") { base += "/" }
        return URL(string: base + path)
    }

    private func fetch(_ path: String) async throws -> Data {
        guard let u = url(path) else { throw URLError(.badURL) }
        var req = URLRequest(url: u)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        req.timeoutInterval = 25
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, http.statusCode >= 400 { throw URLError(.badServerResponse) }
        return data
    }

    private func fetchJSON<T: Decodable>(_ type: T.Type, _ path: String) async throws -> T {
        let data = try await fetch(path)
        return try JSONDecoder().decode(T.self, from: data)
    }

    // MARK: - static data

    func loadStatic() async {
        loading = true
        defer { loading = false }
        do {
            let sched = try await fetchJSON(ClientSchedule.self, "client_schedule.json")
            schedule = sched
            index = StationIndex(schedule: sched)
            demoOffset = sched.demoNow.map { $0 - Date().timeIntervalSince1970 }
            lastError = nil
        } catch {
            lastError = "Could not load the schedule from \(baseURL): \(error.localizedDescription)"
            return
        }
        lineSched = (try? await fetchJSON(ClientLines.self, "client_lines.json"))?.lines ?? [:]
        holds = try? await fetchJSON(HoldsSummary.self, "holds.json")
        segments = try? await fetchJSON(SegmentsSummary.self, "segments.json")
        deviations = [:]
        deviationRequested = []
        for k in wanted { requestDeviation(k) }
        staticVersion += 1
    }

    /// The deviation grid of one line (typical time lost per stop by hour), fetched once per line on demand.
    func requestDeviation(_ key: String) {
        if deviationRequested.contains(key) { return }
        deviationRequested.insert(key)
        Task { [weak self] in
            guard let self = self else { return }
            if let h = try? await self.fetchJSON(LineHistory.self, "lines/\(key).json"), let d = h.deviation {
                self.deviations[key] = d
                self.staticVersion += 1
            }
        }
    }

    // MARK: - polling

    func start() {
        if pollTask != nil { return }
        pollTask = Task { [weak self] in
            guard let self = self else { return }
            while !Task.isCancelled {
                if self.schedule == nil { await self.loadStatic() }
                if self.schedule != nil { await self.poll() }
                let secs = max(10, self.pollSec)
                try? await Task.sleep(nanoseconds: UInt64(secs * 1_000_000_000))
            }
        }
    }

    func stop() {
        pollTask?.cancel()
        pollTask = nil
    }

    /// Change the data source or the interval, persist both and reload.
    func configure(baseURL: String, pollSec: Double) {
        self.baseURL = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        self.pollSec = max(10, pollSec)
        UserDefaults.standard.set(self.baseURL, forKey: "baseURL")
        UserDefaults.standard.set(self.pollSec, forKey: "pollSec")
        restart()
    }

    /// Drop everything and reload (the base URL or the interval changed).
    func restart() {
        stop()
        feeds = [:]
        boards = [:]
        alerts = []
        lastUpdate = nil
        lastError = nil
        VehicleHistory.shared.reset()
        schedule = nil
        index = nil
        start()
    }

    /// A screen declares the line keys it shows; boards are rebuilt from the cached feeds at once and any feed not
    /// fetched yet is polled immediately.
    func setWanted(_ keys: Set<String>, for screen: String) {
        wantedBy[screen] = keys
        var all = Set<String>()
        for s in wantedBy.values { all.formUnion(s) }
        if all == wanted { return }
        wanted = all
        for k in all { requestDeviation(k) }
        rebuildBoards()
        if !missingFeeds().isEmpty { Task { await poll() } }
    }

    func neededFeedKeys() -> Set<String> {
        guard let s = schedule else { return [] }
        if wanted.isEmpty { return Set(s.targetFeeds) }
        var out = Set<String>()
        for k in wanted {
            let route = String(k.split(separator: "_").first ?? "")
            if let f = s.routeFeeds[route] { out.insert(f) }
        }
        return out
    }

    private func missingFeeds() -> Set<String> { neededFeedKeys().filter { feeds[$0] == nil } }

    func poll() async {
        guard let sched = schedule else { return }
        var jobs: [(String, URL)] = []
        for k in neededFeedKeys() {
            if let p = sched.feeds[k], let u = url(p) { jobs.append((k, u)) }
        }
        var got: [String: RTFeed] = [:]
        var errs: [String] = []
        await withTaskGroup(of: (String, RTFeed?, String?).self) { group in
            for (k, u) in jobs {
                group.addTask {
                    do {
                        var req = URLRequest(url: u)
                        req.cachePolicy = .reloadIgnoringLocalCacheData
                        req.timeoutInterval = 20
                        let (data, resp) = try await URLSession.shared.data(for: req)
                        if let http = resp as? HTTPURLResponse, http.statusCode >= 400 { return (k, nil, "\(k): HTTP \(http.statusCode)") }
                        return (k, try GTFSRealtime.parse(data), nil)
                    } catch {
                        return (k, nil, "\(k): \(error.localizedDescription)")
                    }
                }
            }
            for await (k, feed, err) in group {
                if let feed = feed { got[k] = feed }
                if let err = err { errs.append(err) }
            }
        }
        for (k, f) in got { feeds[k] = f }
        pollCount += 1
        if pollCount % 4 == 1, let au = sched.alertsUrl, let u = url(au) {
            if let r = try? await URLSession.shared.data(from: u) { alerts = Alerts.parse(r.0, now: now) }
        }
        rebuildBoards()
        lastUpdate = Date()
        lastError = errs.isEmpty ? nil : errs.joined(separator: " · ")
        tick += 1
    }

    func rebuildBoards() {
        guard let sched = schedule else { return }
        let t = now
        var out: [String: LineBoard] = [:]
        for k in wanted {
            if let b = lineBoard(schedule: sched, lineSched: lineSched[k] ?? [], feeds: feeds, key: k, now: t) { out[k] = b }
        }
        boards = out
    }

    func alertsFor(routes: [String]) -> [RouteAlert] {
        alerts.filter { a in a.routes.contains(where: { routes.contains($0) }) }
    }
}
