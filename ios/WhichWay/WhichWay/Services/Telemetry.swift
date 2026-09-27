import Foundation
import Observation

/// One trip, as the opted-in phone records it: the route and the predictions, then what the motion sensors
/// saw. No location, no raw sensor data, nothing that identifies the rider; a random per-install id groups a
/// phone's trips and is rotated when telemetry is switched off.
struct TripObservation: Codable, Identifiable {
    struct Leg: Codable { var line: String; var from: String; var to: String }
    var id: String
    var installId: String
    var appVersion: String
    var createdTs: Double
    var routeLabel: String
    var legs: [Leg]
    var transferStation: String?
    var transferWalkSec: Int?
    var predictedBoardTs: Double?
    var predictedArriveTs: Double?
    var expectedSec: Double
    var schedSec: Int
    var extraMin: Int
    var trainLateSec: Double?
    var trainHeld: Bool
    var offline: Bool
    var startedBy: String                 // "gps" | "hand"
    var events: [MotionEvent] = []
    var motionSeconds: Int = 0
    var endedTs: Double?
    var endedBy: String?                  // "hand" | "changed" | "left"
    /// The first departure the sensors saw within 5 minutes of the predicted boarding time.
    var corroboratedDepartureTs: Double?
    var uploaded: Bool?
}

@MainActor
@Observable
final class Telemetry {
    static let shared = Telemetry()
    static let optInKey = "telemetryOptIn"
    static let installKey = "telemetryInstallId"

    private(set) var optIn: Bool
    private(set) var installId: String
    private(set) var observations: [TripObservation] = []
    private(set) var current: TripObservation?
    private(set) var motionState: MotionState = .unknown
    private(set) var lastUpload: Date?
    private(set) var lastUploadError: String?
    @ObservationIgnored private var detector = BoardingDetector()
    @ObservationIgnored private let sampler = MotionSampler()
    @ObservationIgnored private let file: URL

    var pendingUpload: Int { observations.filter { $0.uploaded != true }.count }
    var sensorsAvailable: Bool { MotionSampler.isAvailable }

    init() {
        let d = UserDefaults.standard
        optIn = d.bool(forKey: Telemetry.optInKey)
        let id = d.string(forKey: Telemetry.installKey) ?? UUID().uuidString
        installId = id
        d.set(id, forKey: Telemetry.installKey)
        let root = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)) ?? FileManager.default.temporaryDirectory
        let dir = root.appendingPathComponent("WhichWay/telemetry", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        file = dir.appendingPathComponent("observations.json")
        if let data = try? Data(contentsOf: file), let obs = try? JSONDecoder().decode([TripObservation].self, from: data) { observations = obs }
    }

    private func save() {
        if let d = try? JSONEncoder().encode(observations) { try? d.write(to: file, options: .atomic) }
    }

    /// Switching off stops sampling, deletes everything collected and rotates the id.
    func setOptIn(_ on: Bool) {
        guard on != optIn else { return }
        optIn = on
        UserDefaults.standard.set(on, forKey: Telemetry.optInKey)
        if !on {
            endTrip(by: "off", api: nil)
            deleteAll()
            installId = UUID().uuidString
            UserDefaults.standard.set(installId, forKey: Telemetry.installKey)
        }
    }

    // MARK: - a trip

    func beginTrip(_ base: TripObservation) {
        guard optIn else { return }
        if current != nil { endTrip(by: "changed", api: nil) }
        var o = base
        o.id = UUID().uuidString; o.installId = installId; o.createdTs = Date().timeIntervalSince1970
        current = o
        detector = BoardingDetector()
        motionState = .unknown
        sampler.start { [weak self] second in
            Task { @MainActor in self?.ingest(second) }
        }
    }

    private func ingest(_ second: MotionSecond) {
        guard current != nil else { return }
        let event = detector.feed(second)
        motionState = detector.state
        current?.motionSeconds = detector.seconds
        guard let e = event else { return }
        current?.events.append(e)
        if e.kind == .departed, current?.corroboratedDepartureTs == nil, let b = current?.predictedBoardTs, abs(e.ts - b) <= 300 {
            current?.corroboratedDepartureTs = e.ts
        }
    }

    /// The prediction as it stands; frozen once the sensors have seen a departure, so the record keeps the
    /// forecast the rider acted on.
    func updatePrediction(boardTs: Double?, arriveTs: Double?, expectedSec: Double, extraMin: Int, trainLateSec: Double?, held: Bool, offline: Bool) {
        guard var o = current, o.events.isEmpty else { return }
        o.predictedBoardTs = boardTs; o.predictedArriveTs = arriveTs; o.expectedSec = expectedSec; o.extraMin = extraMin
        o.trainLateSec = trainLateSec; o.trainHeld = held; o.offline = offline
        current = o
    }

    func endTrip(by reason: String, api: URL?) {
        sampler.stop()
        guard var o = current else { return }
        o.endedTs = Date().timeIntervalSince1970; o.endedBy = reason
        current = nil
        motionState = .unknown
        // a trip the sensors never saw anything of is not worth keeping
        if !o.events.isEmpty || o.motionSeconds >= 60 {
            observations.append(o); save()
            if let api = api { Task { await upload(to: api) } }
        }
    }

    // MARK: - leaving the phone

    /// POSTs the observations not sent yet to the server's /api/telemetry. The published site cannot receive
    /// them, so with no server they stay on the phone (and can be exported).
    func upload(to api: URL?) async {
        guard let api = api else { return }
        let pending = observations.filter { $0.uploaded != true }
        guard !pending.isEmpty else { return }
        do {
            var req = URLRequest(url: api.appendingPathComponent("api/telemetry"))
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(pending)
            req.timeoutInterval = 20
            let (_, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
                throw URLError(.badServerResponse)
            }
            let sent = Set(pending.map { $0.id })
            for i in observations.indices where sent.contains(observations[i].id) { observations[i].uploaded = true }
            lastUpload = Date(); lastUploadError = nil
            save()
        } catch {
            lastUploadError = "Could not send: \(error.localizedDescription)"
        }
    }

    /// A file of everything collected, for the share sheet.
    func exportURL() -> URL? {
        guard !observations.isEmpty, let d = try? JSONEncoder().encode(observations) else { return nil }
        let u = FileManager.default.temporaryDirectory.appendingPathComponent("whichway-trips.json")
        return (try? d.write(to: u, options: .atomic)) != nil ? u : nil
    }

    func deleteAll() {
        observations = []
        lastUpload = nil; lastUploadError = nil
        try? FileManager.default.removeItem(at: file)
    }
}
