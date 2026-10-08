import SwiftUI
import CoreLocation

/// Settings: Home, Work and the rider's own places, each a station by name, optionally pinned to the spot
/// they set out from. Unset ones can take the station the rider's history points to.
struct PlacesSection: View {
    @Environment(DataService.self) private var data
    @Environment(PlaceStore.self) private var places
    @State private var editing: Place? = nil

    var body: some View {
        Section("Places") {
            ForEach([Place.Kind.home, .work], id: \.self) { kind in
                if let p = places.place(kind) {
                    row(p)
                } else {
                    unsetRow(kind)
                }
            }
            ForEach(places.custom) { p in row(p) }
                .onDelete { offsets in for i in offsets { places.remove(id: places.custom[i].id) } }
            Button("Add place") { editing = Place(kind: .custom, name: "", stationId: "") }
                .disabled(data.index == nil)
            Text("A station you call by name. Pin the spot you set out from and the app knows when you are there, learns how long it takes you to reach the station, and can suggest the trip you usually make.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .sheet(item: $editing) { p in
            PlaceEditorView(place: p) { places.update($0) } onDelete: { places.remove(id: $0) }
        }
    }

    private func row(_ p: Place) -> some View {
        Button { editing = p } label: {
            HStack(spacing: 10) {
                Image(systemName: p.symbol).foregroundStyle(Color.accentColor).frame(width: 22)
                VStack(alignment: .leading, spacing: 2) {
                    Text(p.name).foregroundStyle(Color.primary)
                    Text((data.index?.station(p.stationId)?.name ?? "station not set") + (p.isPinned ? " · pinned" : "")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if let st = data.index?.station(p.stationId) { RouteBullets(routes: st.routes, size: 16) }
            }
        }
    }

    /// Home or Work not set yet: the row opens the editor; when the history points to a station, a second row
    /// takes it in one tap.
    @ViewBuilder private func unsetRow(_ kind: Place.Kind) -> some View {
        let habits = HabitStore.shared.habits
        let sug = kind == .home ? habits.suggestedHome() : habits.suggestedWork(excluding: places.place(.home)?.stationId)
        Button { editing = Place(kind: kind, name: kind.title, stationId: "") } label: {
            HStack(spacing: 10) {
                Image(systemName: kind.symbol).foregroundStyle(.secondary).frame(width: 22)
                Text("Set \(kind.title)").foregroundStyle(Color.primary)
                Spacer()
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
            }
        }
        if let s = sug, let st = data.index?.station(s.stationId) {
            Button {
                places.update(Place(kind: kind, name: kind.title, stationId: st.id, lat: s.lat, lon: s.lon))
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "sparkles").foregroundStyle(Color.accentColor).frame(width: 22)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Use \(st.name) as \(kind.title)").foregroundStyle(Color.primary)
                        Text("\(s.uses) trips \(kind == .home ? "left from or came back to it" : "went to or left from it")" + (s.lat != nil ? ", from the same spot, which becomes the pin" : ""))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    RouteBullets(routes: st.routes, size: 16)
                }
            }
        }
    }
}

/// Name, station, and the pin.
struct PlaceEditorView: View {
    @Environment(DataService.self) private var data
    @Environment(LocationService.self) private var loc
    @Environment(PlaceStore.self) private var placeStore
    @Environment(\.dismiss) private var dismiss
    @State var place: Place
    let onSave: (Place) -> Void
    let onDelete: (UUID) -> Void
    @State private var picking = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Name") {
                    TextField(place.kind.title, text: $place.name)
                }
                Section("Station") {
                    StationButton(label: "At", station: data.index?.station(place.stationId)) { picking = true }
                }
                Section("Pin") {
                    if place.isPinned {
                        if let l = loc.location, let d = place.distanceM(toLat: l.coordinate.latitude, lon: l.coordinate.longitude) {
                            LabeledContent("Pinned", value: "\(Fmt.miles(d)) from here")
                        } else {
                            LabeledContent("Pinned", value: "yes")
                        }
                        Button("Move the pin here") { pin() }.disabled(loc.location == nil)
                        Button("Remove the pin", role: .destructive) { place.lat = nil; place.lon = nil }
                    } else {
                        Button("Pin my current location") { pin() }.disabled(loc.location == nil)
                        if let e = loc.error { Text(e).font(.caption).foregroundStyle(Color.red) }
                    }
                    Text("With a pin, a route started from here learns how long you take to reach the station, and the usual trip from here is suggested at the right hour. The pin stays on the phone.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if place.kind == .custom, placeStore.places.contains(where: { $0.id == place.id }) {
                    Section { Button("Delete place", role: .destructive) { onDelete(place.id); dismiss() } }
                }
            }
            .navigationTitle(place.name.isEmpty ? place.kind.title : place.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        var p = place
                        if p.name.trimmingCharacters(in: .whitespaces).isEmpty { p.name = p.kind.title }
                        onSave(p)
                        dismiss()
                    }
                    .disabled(place.stationId.isEmpty)
                }
            }
            .onAppear { loc.request() }
            .sheet(isPresented: $picking) {
                if let index = data.index {
                    StationPickerSheet(title: "Station", stations: index.sorted, reach: nil) { st in place.stationId = st.id }
                }
            }
        }
    }

    private func pin() {
        guard let l = loc.location else { return }
        place.lat = l.coordinate.latitude
        place.lon = l.coordinate.longitude
    }
}

/// Settings: the rider's own pace model.
struct PaceSection: View {
    @Environment(DataService.self) private var data
    private var pace: PersonalModelStore { PersonalModelStore.shared }

    var body: some View {
        Section("Learn my pace") {
            Toggle("Learn how long my trips really take", isOn: Binding(get: { pace.optIn }, set: { pace.setOptIn($0) }))
            Text("Off unless you turn it on. While a route is in progress the phone's location and motion sensors measure how fast you walk, how long you take from the street to the platform at each station, how long your changes take, and how long your places are from their stations. The predictions then use your figures instead of averages. This stays on the phone; it is never sent anywhere unless you also share anonymous trip motion below, and then only each trip's own measurements go, never your places or location.")
                .font(.caption).foregroundStyle(.secondary)
            if pace.optIn || pace.model.trips > 0 {
                LabeledContent("Learned so far", value: pace.summary)
                if pace.model.accessDefault.n > 0 { LabeledContent("Street to platform", value: "about \(Int((pace.model.accessDefault.mean / 60).rounded())) min on average") }
                if pace.model.transferDefault.n > 0 { LabeledContent("Changing trains", value: "about \(Fmt.mmss(pace.model.transferDefault.mean)) walking") }
                let habits = HabitStore.shared.habits
                LabeledContent("Trips noticed", value: "\(habits.uses.count)")
                Button("Forget what it learned", role: .destructive) { pace.reset(); HabitStore.shared.reset() }
                    .disabled(pace.model.trips == 0 && habits.uses.isEmpty)
            }
        }
    }
}
