import Foundation

/// Trips and motion traces to the WhichWay relay (relay/ in the repository: a Cloudflare Worker that writes them
/// into the private data repository with a token only it holds), from any phone and any connection. The phone
/// carries the relay's address and a shared app key, both from the build (WHICHWAY_TRIP_RELAY and
/// WHICHWAY_RELAY_KEY in Config/Local.xcconfig, through Config/Info.plist); nothing is sent until both are set
/// and the rider has opted in to sharing trip motion. The relay decides where each file goes.
@MainActor
final class TripRelay {
    static let shared = TripRelay()

    static let baseURL: URL? = {
        guard let s = Bundle.main.object(forInfoDictionaryKey: "WhichWayTripRelay") as? String, s.hasPrefix("https://") else { return nil }
        return URL(string: s.hasSuffix("/") ? s : s + "/")
    }()
    static let key: String = (Bundle.main.object(forInfoDictionaryKey: "WhichWayRelayKey") as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)

    var configured: Bool { TripRelay.baseURL != nil && !TripRelay.key.isEmpty }
    var host: String { TripRelay.baseURL?.host ?? "" }
    private(set) var lastUpload: Date?
    private(set) var lastError: String?

    enum Kind: String { case trip = "trips", trace = "traces" }

    /// One document to the relay; the same document again is fine (the relay leaves an unchanged file alone).
    func put(_ kind: Kind, _ data: Data) async throws {
        guard let base = TripRelay.baseURL, !TripRelay.key.isEmpty else { throw URLError(.userAuthenticationRequired) }
        var req = URLRequest(url: base.appendingPathComponent("v1/\(kind.rawValue)"))
        req.httpMethod = "POST"
        req.timeoutInterval = 30
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(TripRelay.key, forHTTPHeaderField: "X-WhichWay-Key")
        req.setValue(Telemetry.shared.installId, forHTTPHeaderField: "X-WhichWay-Install")
        req.httpBody = data
        let (body, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 || code == 201 else {
            let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            let why = (json?["error"] as? String) ?? "HTTP \(code)"
            throw NSError(domain: "TripRelay", code: code, userInfo: [NSLocalizedDescriptionKey: code == 401 ? "the relay refused the app key" : why])
        }
    }

    func noteResult(_ error: Error?) {
        if let e = error { lastError = e.localizedDescription } else { lastUpload = Date(); lastError = nil }
    }
}

/// Where a finished trip goes from this phone: the relay when the build names one, else the rider's own GitHub
/// repository and token (Settings), else nowhere but the phone and the local server on the home network.
@MainActor
enum TripRepository {
    static var configured: Bool { TripRelay.shared.configured || GitHubUploader.shared.configured }
    static var viaRelay: Bool { TripRelay.shared.configured }
    static var lastUpload: Date? { viaRelay ? TripRelay.shared.lastUpload : GitHubUploader.shared.lastUpload }
    static var lastError: String? { viaRelay ? TripRelay.shared.lastError : GitHubUploader.shared.lastError }

    static func putTrip(createdTs: Double, id: String, data: Data, message: String) async throws {
        if viaRelay { try await TripRelay.shared.put(.trip, data) }
        else { try await GitHubUploader.shared.put(path: GitHubUploader.tripPath(createdTs: createdTs, id: id), data: data, message: message) }
    }

    static func putTrace(fileName: String, data: Data) async throws {
        if viaRelay { try await TripRelay.shared.put(.trace, data) }
        else { try await GitHubUploader.shared.put(path: "traces/\(fileName)", data: data, message: "motion trace") }
    }

    static func noteResult(_ error: Error?) {
        if viaRelay { TripRelay.shared.noteResult(error) } else { GitHubUploader.shared.noteResult(error) }
    }
}
