import Foundation
import Observation

/// Which trips this rider makes, when and from where: on the phone only. Suggests the likely route for the
/// moment and the stations that look like Home and Work.
@MainActor
@Observable
final class HabitStore {
    static let shared = HabitStore()
    private(set) var habits: Habits
    @ObservationIgnored private let file: URL

    init() {
        let root = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)) ?? FileManager.default.temporaryDirectory
        let dir = root.appendingPathComponent("WhichWay/personal", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let f = dir.appendingPathComponent("habits.json")
        habits = (try? Data(contentsOf: f)).flatMap { try? JSONDecoder().decode(Habits.self, from: $0) } ?? Habits()
        file = f
    }

    func record(origin: String, dest: String, ts: Double, lat: Double?, lon: Double?) {
        let before = habits.uses.count
        habits.record(origin: origin, dest: dest, ts: ts, lat: lat, lon: lon, timeZone: Fmt.ny)
        if habits.uses.count != before, let d = try? JSONEncoder().encode(habits) { try? d.write(to: file, options: .atomic) }
    }

    func likelyTrip(now: Double, lat: Double?, lon: Double?) -> HabitGuess? {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = Fmt.ny
        let c = cal.dateComponents([.hour, .minute, .weekday], from: Date(timeIntervalSince1970: now))
        return habits.likelyTrip(hour: Double(c.hour ?? 0) + Double(c.minute ?? 0) / 60, weekday: c.weekday ?? 1, lat: lat, lon: lon)
    }

    func reset() {
        habits = Habits()
        try? FileManager.default.removeItem(at: file)
    }
}
