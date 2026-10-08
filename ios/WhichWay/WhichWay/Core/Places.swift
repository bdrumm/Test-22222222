import Foundation
import Observation

/// A station the rider calls by name: Home, Work, or their own. Optionally pinned to the spot they set out
/// from, so the app knows when they are there and can learn how long that place is from its station.
struct Place: Codable, Identifiable, Equatable {
    enum Kind: String, Codable, CaseIterable {
        case home, work, custom
        var title: String { self == .home ? "Home" : (self == .work ? "Work" : "Place") }
        var symbol: String { self == .home ? "house.fill" : (self == .work ? "briefcase.fill" : "mappin.circle.fill") }
    }
    var id: UUID = UUID()
    var kind: Kind
    var name: String
    var stationId: String
    var lat: Double? = nil
    var lon: Double? = nil

    var isPinned: Bool { lat != nil && lon != nil }
    var symbol: String { kind.symbol }

    func distanceM(toLat la: Double, lon lo: Double) -> Double? {
        guard let a = lat, let b = lon else { return nil }
        return haversineM((a, b), (la, lo))
    }
}

/// The saved places, persisted as JSON in UserDefaults.
@Observable
final class PlaceStore {
    static let key = "places"
    private(set) var places: [Place] = []
    @ObservationIgnored private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if let data = defaults.data(forKey: PlaceStore.key), let p = try? JSONDecoder().decode([Place].self, from: data) { places = p }
    }

    private func save() {
        if let d = try? JSONEncoder().encode(places) { defaults.set(d, forKey: PlaceStore.key) }
    }

    func place(_ kind: Place.Kind) -> Place? { kind == .custom ? nil : places.first { $0.kind == kind } }
    var custom: [Place] { places.filter { $0.kind == .custom } }
    /// Home and Work first, then the rest in the order they were added.
    var ordered: [Place] { places.sorted { a, b in rank(a) != rank(b) ? rank(a) < rank(b) : a.name < b.name } }
    private func rank(_ p: Place) -> Int { p.kind == .home ? 0 : (p.kind == .work ? 1 : 2) }

    func update(_ p: Place) {
        if let i = places.firstIndex(where: { $0.id == p.id }) { places[i] = p }
        else if p.kind != .custom, let i = places.firstIndex(where: { $0.kind == p.kind }) { places[i] = p }
        else { places.append(p) }
        save()
    }

    func remove(id: UUID) { places.removeAll { $0.id == id }; save() }

    /// The pinned place within `withinM` of a point, nearest first.
    func nearest(toLat lat: Double, lon: Double, withinM: Double) -> Place? {
        places.compactMap { p in p.distanceM(toLat: lat, lon: lon).map { (p, $0) } }
            .filter { $0.1 <= withinM }
            .min { $0.1 < $1.1 }?.0
    }
}
