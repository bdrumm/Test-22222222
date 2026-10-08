import Foundation
import Security

/// Trip records and motion traces to a private GitHub repository (the Contents API: one file per trip), from
/// anywhere the phone has a connection: the Mac only pulls, so it never has to be reachable. The token is the
/// rider's own fine-grained token for that one repository (Contents: read and write), pasted into Settings and
/// kept in the Keychain; nothing is sent until both the repository and the token are set.
@MainActor
final class GitHubUploader {
    static let shared = GitHubUploader()
    static let repoKey = "githubDataRepo"
    static let defaultRepo = "bdrumm/whichway-data"
    private let service = "whichway.github"
    private let account = "token"

    var repo: String {
        get { UserDefaults.standard.string(forKey: GitHubUploader.repoKey) ?? GitHubUploader.defaultRepo }
        set { UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: GitHubUploader.repoKey) }
    }
    var configured: Bool { repo.contains("/") && hasToken }
    var hasToken: Bool { token != nil }
    private(set) var lastUpload: Date?
    private(set) var lastError: String?

    // MARK: - the token, in the Keychain

    private var token: String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account,
                                kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data,
              let s = String(data: d, encoding: .utf8), !s.isEmpty else { return nil }
        return s
    }

    /// Stores the token (or removes it, given nothing). Readable after the first unlock, so uploads at the end of a
    /// route run with the screen locked.
    func setToken(_ t: String?) {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
        SecItemDelete(base as CFDictionary)
        guard let t = t?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return }
        var add = base
        add[kSecValueData as String] = Data(t.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(add as CFDictionary, nil)
    }

    // MARK: - writing a file

    private func call(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> (Int, [String: Any]) {
        guard let token = token, let u = URL(string: "https://api.github.com/repos/\(repo)/contents/\(path)") else {
            throw URLError(.userAuthenticationRequired)
        }
        var req = URLRequest(url: u)
        req.httpMethod = method
        req.timeoutInterval = 30
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("2022-11-28", forHTTPHeaderField: "X-GitHub-Api-Version")
        if let body = body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        return (code, json)
    }

    /// Creates or replaces one file; a file already there with the same content is left alone.
    func put(path: String, data: Data, message: String) async throws {
        let (getCode, existing) = try await call("GET", path)
        if getCode == 401 || getCode == 403 {
            throw NSError(domain: "GitHub", code: getCode, userInfo: [NSLocalizedDescriptionKey: "the token was refused (\(getCode)): it needs Contents read and write on \(repo)"])
        }
        var body: [String: Any] = ["message": message, "content": data.base64EncodedString()]
        if getCode == 200, let sha = existing["sha"] as? String {
            if let c = existing["content"] as? String, Data(base64Encoded: c.replacingOccurrences(of: "\n", with: "")) == data { return }
            body["sha"] = sha
        }
        let (code, json) = try await call("PUT", path, body: body)
        guard code == 200 || code == 201 else {
            throw NSError(domain: "GitHub", code: code, userInfo: [NSLocalizedDescriptionKey: (json["message"] as? String) ?? "HTTP \(code)"])
        }
    }

    /// Where a trip goes in the repository: trips/<year>/<month>/<day>/<start>-<id>.json, by the New York date.
    static func tripPath(createdTs: Double, id: String) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York") ?? .current
        let c = cal.dateComponents([.year, .month, .day], from: Date(timeIntervalSince1970: createdTs))
        return String(format: "trips/%04d/%02d/%02d/%d-%@.json", c.year ?? 0, c.month ?? 0, c.day ?? 0, Int(createdTs), id)
    }

    func noteResult(_ error: Error?) {
        if let e = error { lastError = e.localizedDescription } else { lastUpload = Date(); lastError = nil }
    }
}
