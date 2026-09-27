import Foundation
import CryptoKit

/// The last successful fetch of each file, kept under Application Support so the app keeps working without
/// signal: the schedule and the other published tables, the last snapshot of each feed, the alerts. One folder
/// per data source (base URL), one file per path.
struct DiskCache: Sendable {
    let dir: URL

    init(baseURL: String) {
        let hash = SHA256.hash(data: Data(baseURL.utf8)).prefix(8).map { String(format: "%02x", $0) }.joined()
        let root = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true))
            ?? FileManager.default.temporaryDirectory
        dir = root.appendingPathComponent("WhichWay/cache/\(hash)", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    private func file(_ path: String) -> URL {
        dir.appendingPathComponent(path.replacingOccurrences(of: "/", with: "__").replacingOccurrences(of: ":", with: "_"))
    }

    func write(_ path: String, _ data: Data) { try? data.write(to: file(path), options: .atomic) }

    func read(_ path: String) -> Data? { try? Data(contentsOf: file(path)) }

    /// When that path was last fetched.
    func date(_ path: String) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: file(path).path))?[.modificationDate] as? Date
    }

    func clear() {
        try? FileManager.default.removeItem(at: dir)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    var sizeBytes: Int {
        let items = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.fileSizeKey])) ?? []
        return items.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
    }
}
