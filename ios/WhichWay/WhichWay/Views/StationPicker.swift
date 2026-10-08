import SwiftUI

/// Searchable station list. With `reach` set (destination picker) only stations reachable from the origin are
/// listed, direct ones first, each with how it is reached (direct lines, or the change to make and where).
/// With `nearTo` and `coords` set (a commute is on) the stations closest to that station come first, nearest
/// first with the distance, so an alternative near the commute's own station is one tap away.
struct StationPickerSheet: View {
    let title: String
    let stations: [Station]
    let reach: [String: Reach]?
    var nearTo: Station? = nil
    var coords: [String: (lat: Double, lon: Double)] = [:]
    /// Home, Work and the rider's own places, on top.
    var places: [PlacePick] = []
    let onPick: (Station) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""

    /// The listed stations (reach applied) closest to `nearTo`, nearest first; none while searching.
    private var nearby: [NearbyStation] {
        guard query.trimmingCharacters(in: .whitespaces).isEmpty, let a = nearTo, let p = coords[a.id] else { return [] }
        var out: [NearbyStation] = []
        for st in stations where st.id != a.id {
            if let r = reach, r[st.id] == nil { continue }
            if let c = coords[st.id] { out.append(NearbyStation(station: st, meters: haversineM(p, c))) }
        }
        out.sort { $0.meters < $1.meters }
        return Array(out.prefix(10))
    }

    private var filtered: [Station] {
        var base = stations
        if let r = reach { base = base.filter { r[$0.id] != nil } }
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        if !q.isEmpty {
            base = base.filter { st in st.name.lowercased().contains(q) || st.routes.contains(where: { $0.lowercased() == q }) }
        }
        if let r = reach {
            base.sort { a, b in
                let da = r[a.id]?.how == "direct", db = r[b.id]?.how == "direct"
                if da != db { return da }
                return a.name < b.name
            }
        }
        return base
    }

    var body: some View {
        NavigationStack {
            List {
                let near = nearby
                let shownPlaces = query.trimmingCharacters(in: .whitespaces).isEmpty ? places.filter { reach == nil || reach?[$0.station.id] != nil } : []
                if !shownPlaces.isEmpty {
                    Section("Places") {
                        ForEach(shownPlaces) { pp in
                            Button {
                                onPick(pp.station)
                                dismiss()
                            } label: {
                                HStack(spacing: 10) {
                                    Image(systemName: pp.place.symbol).foregroundStyle(Color.accentColor).frame(width: 22)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(pp.place.name).foregroundStyle(Color.primary)
                                        Text(pp.station.name).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    RouteBullets(routes: pp.station.routes, size: 18)
                                }
                            }
                        }
                    }
                }
                if let a = nearTo, !near.isEmpty {
                    Section("Near \(a.name)") {
                        ForEach(near) { n in row(n.station, distance: n) }
                    }
                    Section("All stations") {
                        ForEach(filtered) { st in row(st, distance: nil) }
                    }
                } else {
                    ForEach(filtered) { st in row(st, distance: nil) }
                }
            }
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Station name or line")
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            .overlay {
                if filtered.isEmpty {
                    ContentUnavailableView.search(text: query)
                }
            }
        }
    }

    private func row(_ st: Station, distance: NearbyStation?) -> some View {
        Button {
            onPick(st)
            dismiss()
        } label: {
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(st.name).foregroundStyle(Color.primary)
                    Spacer()
                    RouteBullets(routes: st.routes, size: 18)
                }
                if let n = distance {
                    Text("\(Self.distanceText(n.meters)) · about \(n.walkMinutes) min walk").font(.caption).foregroundStyle(.secondary)
                }
                if let r = reach?[st.id] {
                    Text(r.summary).font(.caption).foregroundStyle(r.how == "direct" ? Color.green : Color.secondary)
                }
            }
        }
    }

    /// "300 ft" below a tenth of a mile, else "0.8 mi".
    static func distanceText(_ m: Double) -> String { Fmt.miles(m) }
}

struct StationButton: View {
    let label: String
    let station: Station?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack {
                Text(label).font(.caption).foregroundStyle(.secondary).frame(width: 40, alignment: .leading)
                Text(station?.name ?? "Choose a station").foregroundStyle(station == nil ? Color.secondary : Color.primary).lineLimit(1)
                Spacer()
                if let s = station { RouteBullets(routes: s.routes, size: 18) }
                Image(systemName: "chevron.down").font(.caption).foregroundStyle(.secondary)
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
        }
        .buttonStyle(.plain)
    }
}

/// A square icon button whose glyph keeps full contrast in light and dark mode.
struct IconButton: View {
    let systemImage: String
    let label: String
    let action: () -> Void
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.body.weight(.semibold))
                .foregroundStyle(isEnabled ? Color.primary : Color.secondary)
                .frame(width: 44, height: 44)
                .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.12), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .opacity(isEnabled ? 1 : 0.55)
        .accessibilityLabel(label)
    }
}

/// A place with the station it stands for, for the pickers.
struct PlacePick: Identifiable {
    var id: UUID { place.id }
    let place: Place
    let station: Station
}

extension PlaceStore {
    /// The saved places whose station the index knows, Home and Work first.
    func picks(_ index: StationIndex) -> [PlacePick] {
        ordered.compactMap { p in index.station(p.stationId).map { PlacePick(place: p, station: $0) } }
    }
}
