import Foundation
import Observation

/// A saved trip the Go tab switches to on its own during a time window (a commute), optionally starting from
/// whichever station is nearest right now.
struct CommutePreset: Codable, Identifiable, Equatable {
    var id: UUID = UUID()
    var name: String
    var originId: String
    var destId: String
    var startMinute: Int              // minutes after midnight, New York time
    var endMinute: Int
    var weekdaysOnly: Bool = true
    var useNearestOrigin: Bool = false

    /// True when the window covers this minute of the day (a window may cross midnight) on this weekday
    /// (1 = Sunday … 7 = Saturday, as Calendar numbers them).
    func isActive(minuteOfDay m: Int, weekday: Int) -> Bool {
        if weekdaysOnly && (weekday == 1 || weekday == 7) { return false }
        if startMinute == endMinute { return false }
        if startMinute < endMinute { return m >= startMinute && m < endMinute }
        return m >= startMinute || m < endMinute
    }

    func isActive(at ts: Double, timeZone: TimeZone = Fmt.ny) -> Bool {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let c = cal.dateComponents([.hour, .minute, .weekday], from: Date(timeIntervalSince1970: ts))
        return isActive(minuteOfDay: (c.hour ?? 0) * 60 + (c.minute ?? 0), weekday: c.weekday ?? 2)
    }

    var windowText: String { "\(Fmt.clock(startMinute))–\(Fmt.clock(endMinute))\(weekdaysOnly ? " weekdays" : "")" }
}

/// The saved commutes, persisted as JSON in UserDefaults.
@Observable
final class PresetStore {
    static let key = "commutePresets"
    private(set) var presets: [CommutePreset] = []
    @ObservationIgnored private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if let data = defaults.data(forKey: PresetStore.key), let p = try? JSONDecoder().decode([CommutePreset].self, from: data) { presets = p }
    }

    private func save() {
        if let d = try? JSONEncoder().encode(presets) { defaults.set(d, forKey: PresetStore.key) }
    }

    func add(_ p: CommutePreset) { presets.append(p); save() }

    func update(_ p: CommutePreset) {
        if let i = presets.firstIndex(where: { $0.id == p.id }) { presets[i] = p; save() } else { add(p) }
    }

    func remove(id: UUID) { presets.removeAll { $0.id == id }; save() }

    func remove(at offsets: IndexSet) {
        for i in offsets.sorted(by: >) where i < presets.count { presets.remove(at: i) }
        save()
    }

    /// SwiftUI's onMove semantics: `to` is the destination in the list's positions before the move.
    func move(from: IndexSet, to: Int) {
        let moving = from.sorted().filter { $0 < presets.count }.map { presets[$0] }
        var rest = presets
        for i in from.sorted(by: >) where i < rest.count { rest.remove(at: i) }
        let removedBefore = from.filter { $0 < to }.count
        let target = max(0, min(rest.count, to - removedBefore))
        rest.insert(contentsOf: moving, at: target)
        presets = rest
        save()
    }

    /// The first preset whose window covers this moment.
    func active(at ts: Double) -> CommutePreset? { presets.first { $0.isActive(at: ts) } }

    /// A window around now for a new preset: from an hour before to two hours after, within the day.
    static func suggestedWindow(at ts: Double, timeZone: TimeZone = Fmt.ny) -> (start: Int, end: Int) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let h = cal.component(.hour, from: Date(timeIntervalSince1970: ts))
        return (max(0, h - 1) * 60, min(24, h + 2) * 60 == 1440 ? 1439 : min(24, h + 2) * 60)
    }

    static func suggestedName(at ts: Double, timeZone: TimeZone = Fmt.ny) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let h = cal.component(.hour, from: Date(timeIntervalSince1970: ts))
        if h < 12 { return "Morning commute" }
        if h < 16 { return "Afternoon trip" }
        return "Evening commute"
    }
}
