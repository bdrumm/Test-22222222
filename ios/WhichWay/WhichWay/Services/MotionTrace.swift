import Foundation

/// A developer's record of what the motion sensors felt during each route: one summarised second (step energy,
/// push, vibration) per line, with the events the detector drew from them. On in Debug builds unless switched
/// off in Settings, off in Release; kept on this phone (Application Support/WhichWay/traces, the last 30 routes),
/// copied off by `make trips`, and written to the rider's private GitHub data repository when that is set up, for
/// tuning the detector's thresholds against real rides.
@MainActor
final class MotionTrace {
    static let shared = MotionTrace()
    static let key = "keepMotionTraces"
    /// On by default in Debug builds (the developer's own phone, tuning the detector); off in Release.
    var enabled: Bool {
        get {
            #if DEBUG
            return UserDefaults.standard.object(forKey: MotionTrace.key) as? Bool ?? true
            #else
            return UserDefaults.standard.bool(forKey: MotionTrace.key)
            #endif
        }
        set { UserDefaults.standard.set(newValue, forKey: MotionTrace.key) }
    }
    private var samples: [[Double]] = []
    private var startTs: Double?
    private let keep = 30

    private var dir: URL {
        let root = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)) ?? FileManager.default.temporaryDirectory
        return root.appendingPathComponent("WhichWay/traces", isDirectory: true)
    }

    func begin(ts: Double) {
        samples = []
        startTs = enabled ? ts : nil
    }

    func add(_ m: MotionSecond) {
        guard startTs != nil else { return }
        func r(_ x: Double, _ p: Double) -> Double { (x * p).rounded() / p }
        samples.append([r(m.ts, 10), r(m.stepEnergy, 10000), r(m.pushG, 10000), r(m.shakeG, 10000)])
    }

    /// The route is over: its seconds and what the tracker made of them go to a file named by its start.
    func end(_ tl: TripTimeline?) {
        guard let s = startTs, !samples.isEmpty else { startTs = nil; samples = []; return }
        var doc: [String: Any] = ["startTs": s, "columns": ["ts", "stepEnergy", "pushG", "shakeG"], "seconds": samples]
        if let tl = tl {
            doc["events"] = tl.events.map { ["kind": $0.kind.rawValue, "ts": $0.ts] }
            doc["withdrawnDepartures"] = tl.withdrawnDepartures ?? []
            doc["endedBy"] = tl.endedBy ?? ""
            doc["origin"] = tl.originStation
            doc["dest"] = tl.destStation
        }
        startTs = nil; samples = []
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("trace-\(Int(s)).json")
        if let data = try? JSONSerialization.data(withJSONObject: doc) { try? data.write(to: url, options: .atomic) }
        // only the newest routes are kept
        let files = ((try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.lastPathComponent.hasPrefix("trace-") }.sorted { $0.lastPathComponent > $1.lastPathComponent }
        for f in files.dropFirst(keep) { try? FileManager.default.removeItem(at: f) }
        Task { await uploadPending() }
    }

    /// Traces not yet in the GitHub data repository (traces/trace-<start>.json there), oldest first.
    func uploadPending() async {
        let gh = GitHubUploader.shared
        guard gh.configured else { return }
        var sent = Set(UserDefaults.standard.stringArray(forKey: "tracesUploaded") ?? [])
        let files = ((try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.lastPathComponent.hasPrefix("trace-") && !sent.contains($0.lastPathComponent) }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        for f in files {
            guard let data = try? Data(contentsOf: f) else { continue }
            do {
                try await gh.put(path: "traces/\(f.lastPathComponent)", data: data, message: "motion trace")
                sent.insert(f.lastPathComponent)
                UserDefaults.standard.set(Array(sent), forKey: "tracesUploaded")
            } catch {
                gh.noteResult(error)
                return
            }
        }
    }
}
