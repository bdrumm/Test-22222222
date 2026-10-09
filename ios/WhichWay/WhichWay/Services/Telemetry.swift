import Foundation
import Observation

/// One trip, as the opted-in phone records it: the route and the forecast, then what the sensors saw. No
/// location, no raw sensor data, nothing that identifies the rider; a random per-install id groups a phone's
/// trips and is rotated when telemetry is switched off.
struct TripObservation: Codable, Identifiable {
    struct Leg: Codable { var line: String; var from: String; var to: String }
    var id: String
    var installId: String
    var appVersion: String
    var createdTs: Double
    var routeLabel: String
    var legs: [Leg]
    var transferStation: String?
    var transferWalkSec: Int?             // the planner's figure for the change
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
    var endedBy: String?
    /// The first departure the sensors saw within 5 minutes of the predicted boarding time.
    var corroboratedDepartureTs: Double?
    // what the trip measured (the same figures the rider's own pace model learns from)
    var startDistanceM: Double?           // to the origin station when the route started, to the nearest 50 m
    var arrivedStationTs: Double?
    var platformTs: Double?
    var accessSec: Double?                // from the station radius to standing on the platform
    var measuredTransferSec: Double?      // seconds walking between the two trains
    var walkSpeedMPerMin: Double?         // on the street, toward the station
    var rideAssumed: Bool?
    /// The train and line each leg was ridden on, as far as the sensors and the feeds could tell.
    var boarded: [BoardedLeg]?
    /// Station stops felt on each ride, the alighting stop included.
    var rideStops: [Int]?
    /// Pull-aways felt that no train made, withdrawn (the platform shaking).
    var withdrawnDepartures: [Double]?
    var uploaded: Bool?
    /// Written to the private GitHub data repository.
    var uploadedGitHub: Bool?
}

@MainActor
@Observable
final class Telemetry {
    static let shared = Telemetry()
    static let optInKey = "telemetryOptIn"
    static let installKey = "telemetryInstallId"
    static let serverKey = "tripServer"
    /// The Debug build can name the Mac that receives trips through Config/Local.xcconfig (WHICHWAY_TRIP_SERVER,
    /// carried into the generated Info.plist), e.g. http://my-mac.local:8000/ on the home network.
    static let defaultServer: String = {
        if let s = Bundle.main.object(forInfoDictionaryKey: "WhichWayTripServer") as? String, s.hasPrefix("http") { return s }
        return ""
    }()

    private(set) var optIn: Bool
    private(set) var installId: String
    private(set) var observations: [TripObservation] = []
    private(set) var current: TripObservation?
    private(set) var lastUpload: Date?
    private(set) var lastUploadError: String?
    /// Where the trips go: the local server on the home network, set here or by the build; empty means the data
    /// server, when that is one (the published site cannot receive).
    private(set) var server: String
    @ObservationIgnored private let file: URL

    var pendingUpload: Int { observations.filter { $0.uploaded != true }.count }
    var pendingGitHub: Int { observations.filter { $0.uploadedGitHub != true }.count }
    var sensorsAvailable: Bool { MotionSampler.isAvailable }

    /// Whether the rider has ever set the switch themselves (else sharing is on, and the first launch says so).
    static var decided: Bool { UserDefaults.standard.object(forKey: Telemetry.optInKey) != nil }

    init() {
        let d = UserDefaults.standard
        // on unless the rider switched it off: the first launch tells them, with the switch to hand
        let on = d.object(forKey: Telemetry.optInKey) == nil ? true : d.bool(forKey: Telemetry.optInKey)
        let id = d.string(forKey: Telemetry.installKey) ?? UUID().uuidString
        d.set(id, forKey: Telemetry.installKey)
        let root = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)) ?? FileManager.default.temporaryDirectory
        let dir = root.appendingPathComponent("WhichWay/telemetry", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let f = dir.appendingPathComponent("observations.json")
        optIn = on
        installId = id
        server = d.string(forKey: Telemetry.serverKey) ?? Telemetry.defaultServer
        file = f
        if let data = try? Data(contentsOf: f), let obs = try? JSONDecoder().decode([TripObservation].self, from: data) { observations = obs }
    }

    private func save() {
        if let d = try? JSONEncoder().encode(observations) { try? d.write(to: file, options: .atomic) }
    }

    /// Switching off drops the trip in hand, deletes everything collected and rotates the id.
    func setOptIn(_ on: Bool) {
        guard on != optIn else { return }
        optIn = on
        UserDefaults.standard.set(on, forKey: Telemetry.optInKey)
        if !on {
            current = nil
            deleteAll()
            installId = UUID().uuidString
            UserDefaults.standard.set(installId, forKey: Telemetry.installKey)
        }
    }

    // MARK: - a trip

    func beginTrip(_ base: TripObservation) {
        guard optIn else { return }
        var o = base
        o.id = UUID().uuidString; o.installId = installId; o.createdTs = Date().timeIntervalSince1970
        current = o
    }

    /// The forecast as it stands; frozen once the sensors have seen a departure, so the record keeps the
    /// forecast the rider acted on.
    func updatePrediction(boardTs: Double?, arriveTs: Double?, expectedSec: Double, extraMin: Int, trainLateSec: Double?, held: Bool, offline: Bool, departed: Bool) {
        guard var o = current, !departed else { return }
        o.predictedBoardTs = boardTs; o.predictedArriveTs = arriveTs; o.expectedSec = expectedSec; o.extraMin = extraMin
        o.trainLateSec = trainLateSec; o.trainHeld = held; o.offline = offline
        current = o
    }

    /// The route changed under the rider (they boarded a line off the plan): the record follows the route they
    /// are actually riding, so its legs and change are the ones the trip measured.
    func updateRoute(label: String, legs: [TripObservation.Leg], transferStation: String?, transferWalkSec: Int?) {
        guard var o = current else { return }
        o.routeLabel = label; o.legs = legs; o.transferStation = transferStation; o.transferWalkSec = transferWalkSec
        current = o
    }

    /// The trip is over: what the tracker measured joins the record, which is kept when it saw anything.
    func endTrip(timeline tl: TripTimeline?, api: URL?) {
        guard var o = current else { return }
        current = nil
        if let tl = tl {
            o.events = tl.events; o.motionSeconds = tl.motionSeconds
            o.endedTs = tl.endedTs; o.endedBy = tl.endedBy
            o.corroboratedDepartureTs = tl.corroboratedDepartureTs
            o.startDistanceM = tl.startDistanceM.map { ($0 / 50).rounded() * 50 }
            o.arrivedStationTs = tl.arrivedStationTs; o.platformTs = tl.platformObserved ? tl.platformTs : nil
            o.accessSec = tl.accessSec; o.measuredTransferSec = tl.transferWalkSec
            o.walkSpeedMPerMin = tl.walkSpeedMPerMin; o.rideAssumed = tl.rideAssumed
            o.boarded = tl.boarded.isEmpty ? nil : tl.boarded
            o.rideStops = tl.rideStops.isEmpty ? nil : tl.rideStops
            o.withdrawnDepartures = tl.withdrawnDepartures
            if o.predictedBoardTs == nil { o.predictedBoardTs = tl.forecastBoardTs; o.predictedArriveTs = tl.forecastArriveTs }
        }
        guard !o.events.isEmpty || o.motionSeconds >= 60 || o.accessSec != nil || o.walkSpeedMPerMin != nil else { return }
        observations.append(o); save()
        Task {
            if let api = api { await upload(to: api) }
            await uploadToGitHub()
        }
    }

    /// Every trip not yet in the private data repository, one file each, from any connection: through the relay
    /// when the build names one, else with the rider's own GitHub token; the motion traces waiting on the phone
    /// follow.
    func uploadToGitHub() async {
        guard optIn, TripRepository.configured else { return }
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys, .prettyPrinted]
        for o in observations where o.uploadedGitHub != true {
            var copy = o
            copy.uploaded = nil
            copy.uploadedGitHub = nil
            do {
                try await TripRepository.putTrip(createdTs: o.createdTs, id: o.id, data: try enc.encode(copy), message: "trip: \(o.routeLabel)")
                if let i = observations.firstIndex(where: { $0.id == o.id }) { observations[i].uploadedGitHub = true }
                save()
                TripRepository.noteResult(nil)
            } catch {
                TripRepository.noteResult(error)
                return
            }
        }
        await MotionTrace.shared.uploadPending()
    }

    // MARK: - leaving the phone

    func setServer(_ s: String) {
        server = s.trimmingCharacters(in: .whitespacesAndNewlines)
        UserDefaults.standard.set(server, forKey: Telemetry.serverKey)
    }

    /// The server to send to: the trip server when one is set, else the data server (nil on the published site).
    func uploadURL(fallback api: URL?) -> URL? {
        guard !server.isEmpty else { return api }
        var s = server
        if !s.hasPrefix("http") { s = "http://" + s }
        if !s.hasSuffix("/") { s += "/" }
        return URL(string: s) ?? api
    }

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
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { throw URLError(.badServerResponse) }
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
