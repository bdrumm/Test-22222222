import Foundation
import Observation

/// The rider's own pace model: opt-in, learned from their trips, kept on the phone. Never uploaded; a trip's
/// own measurements go to the anonymous telemetry only when that is switched on too.
@MainActor
@Observable
final class PersonalModelStore {
    static let shared = PersonalModelStore()
    static let optInKey = "learnPace"

    private(set) var optIn: Bool
    private(set) var model: PersonalModel
    @ObservationIgnored private let file: URL

    init() {
        let on = UserDefaults.standard.bool(forKey: PersonalModelStore.optInKey)
        let root = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)) ?? FileManager.default.temporaryDirectory
        let dir = root.appendingPathComponent("WhichWay/personal", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let f = dir.appendingPathComponent("pace.json")
        let m = (try? Data(contentsOf: f)).flatMap { try? JSONDecoder().decode(PersonalModel.self, from: $0) } ?? PersonalModel()
        optIn = on
        model = m
        file = f
        NearbyStation.speedMPerMin = m.walkSpeedMPerMin
    }

    func setOptIn(_ on: Bool) {
        optIn = on
        UserDefaults.standard.set(on, forKey: PersonalModelStore.optInKey)
    }

    @discardableResult
    func learn(_ t: TripTimeline) -> Bool {
        guard optIn else { return false }
        let any = model.learn(t)
        if any {
            NearbyStation.speedMPerMin = model.walkSpeedMPerMin
            if let d = try? JSONEncoder().encode(model) { try? d.write(to: file, options: .atomic) }
        }
        return any
    }

    func reset() {
        model = PersonalModel()
        NearbyStation.speedMPerMin = PersonalModel.defaultWalkSpeed
        try? FileManager.default.removeItem(at: file)
    }

    /// "3.1 mph · 4 stations · 2 changes · 12 trips"
    var summary: String {
        var bits: [String] = []
        if model.walkSpeed.n > 0 { bits.append(String(format: "%.1f mph", model.walkSpeedMPerMin * 60 / 1609.344)) }
        if !model.access.isEmpty { bits.append("\(model.access.count) station\(model.access.count == 1 ? "" : "s")") }
        if !model.transfer.isEmpty { bits.append("\(model.transfer.count) change\(model.transfer.count == 1 ? "" : "s")") }
        bits.append("\(model.trips) trip\(model.trips == 1 ? "" : "s")")
        return bits.joined(separator: " · ")
    }
}
