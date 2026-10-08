import SwiftUI
import CoreLocation

/// One chip for the commutes: the one on screen, else the one whose window covers now (with a clock), else an
/// invitation to add one. Tapping it opens the commute menu: switch, edit, add.
struct CommuteChip: View {
    let presets: [CommutePreset]
    let activeId: UUID?
    let currentId: UUID?
    let onPick: (CommutePreset) -> Void
    let onEdit: (CommutePreset) -> Void
    let onAdd: () -> Void

    private var shown: CommutePreset? { presets.first { $0.id == currentId } ?? presets.first { $0.id == activeId } }
    private var on: Bool { shown != nil && shown?.id == currentId }

    var body: some View {
        Menu {
            ForEach(presets) { p in
                Button { onPick(p) } label: {
                    if p.id == currentId {
                        Label("\(p.name) · \(p.windowText)", systemImage: "checkmark")
                    } else if p.id == activeId {
                        Label("\(p.name) · \(p.windowText)", systemImage: "clock")
                    } else {
                        Text("\(p.name) · \(p.windowText)")
                    }
                }
            }
            if !presets.isEmpty {
                Divider()
                Menu {
                    ForEach(presets) { p in Button(p.name) { onEdit(p) } }
                } label: {
                    Label("Edit…", systemImage: "pencil")
                }
            }
            Button(action: onAdd) { Label("Add commute", systemImage: "plus") }
        } label: {
            // one line always: the window text goes first when the row is tight
            ViewThatFits(in: .horizontal) {
                label(window: true)
                label(window: false)
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Capsule().fill(on ? Color.accentColor.opacity(0.18) : Color(.secondarySystemBackground)))
            .overlay(Capsule().stroke(on ? Color.accentColor : Color.clear, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }

    private func label(window: Bool) -> some View {
        HStack(spacing: 5) {
            Image(systemName: shown == nil ? "plus.circle" : (on ? "briefcase.fill" : "clock")).font(.caption)
            if let p = shown {
                Text(p.name).font(.caption.bold()).lineLimit(1)
                if window { Text(p.windowText).font(.caption2).foregroundStyle(.secondary).lineLimit(1) }
            } else {
                Text(presets.isEmpty ? "Add a commute" : "Commutes").font(.caption.bold()).lineLimit(1)
            }
            Image(systemName: "chevron.down").font(.caption2).foregroundStyle(.secondary)
        }
    }
}

/// Shown while the trip is incomplete: the way to get the Go tab to pick the trip on its own.
struct SetupPrompt: View {
    let originSet: Bool
    let destSet: Bool
    let reachable: Int
    let onAdd: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(!originSet && !destSet ? "Where are you going?" : (originSet ? "Pick a destination" : "Pick where you start")).font(.headline)
            Text(originSet && !destSet
                 ? "\(reachable) stations are reachable direct or with one change. Save the trip as a commute and the Go tab will switch to it by time of day."
                 : "Save a commute with your usual stations and the Go tab switches to it on its own by time of day; or pick stations above for a one-off trip.")
                .font(.footnote).foregroundStyle(.secondary)
            Button(action: onAdd) { Label("Add a commute", systemImage: "plus") }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground)))
    }
}

/// Create or edit a commute: name, stations (or the nearest station by location), the daily window, weekdays.
struct PresetEditorView: View {
    @Environment(DataService.self) private var data
    @Environment(PlaceStore.self) private var placeStore
    @Environment(\.dismiss) private var dismiss
    @State var preset: CommutePreset
    let onSave: (CommutePreset) -> Void
    @State private var pickingOrigin = false
    @State private var pickingDest = false
    @State private var start = Date()
    @State private var end = Date()

    private var cal: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = Fmt.ny
        return c
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Name") {
                    TextField("Morning commute", text: $preset.name)
                }
                Section("Stations") {
                    Toggle("Start from the nearest station (uses your location)", isOn: $preset.useNearestOrigin)
                    if !preset.useNearestOrigin {
                        StationButton(label: "From", station: data.index?.station(preset.originId)) { pickingOrigin = true }
                    }
                    StationButton(label: "To", station: data.index?.station(preset.destId)) { pickingDest = true }
                }
                Section("Active") {
                    DatePicker("From", selection: $start, displayedComponents: .hourAndMinute)
                    DatePicker("Until", selection: $end, displayedComponents: .hourAndMinute)
                    Toggle("Weekdays only", isOn: $preset.weekdaysOnly)
                    Text("Between these times the Go tab switches to this trip on its own (New York time; a window may cross midnight). Picking stations by hand keeps your choice for the rest of the day.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .environment(\.timeZone, Fmt.ny)
            .navigationTitle(preset.name.isEmpty ? "Commute" : preset.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        var p = preset
                        p.startMinute = minutes(start)
                        p.endMinute = minutes(end)
                        if p.name.trimmingCharacters(in: .whitespaces).isEmpty { p.name = "Commute" }
                        onSave(p)
                        dismiss()
                    }
                    .disabled(preset.destId.isEmpty || (!preset.useNearestOrigin && preset.originId.isEmpty))
                }
            }
            .onAppear {
                start = date(preset.startMinute)
                end = date(preset.endMinute)
            }
            .sheet(isPresented: $pickingOrigin) {
                if let index = data.index {
                    StationPickerSheet(title: "From", stations: index.sorted, reach: nil, places: placeStore.picks(index)) { st in preset.originId = st.id }
                }
            }
            .sheet(isPresented: $pickingDest) {
                if let index = data.index, let sched = data.schedule {
                    let reach: [String: Reach]? = (preset.useNearestOrigin || preset.originId.isEmpty) ? nil : reachableStations(schedule: sched, index: index, from: preset.originId)
                    StationPickerSheet(title: "To", stations: index.sorted, reach: reach, places: placeStore.picks(index)) { st in preset.destId = st.id }
                }
            }
        }
    }

    private func date(_ m: Int) -> Date { cal.date(bySettingHour: (m / 60) % 24, minute: m % 60, second: 0, of: Date()) ?? Date() }

    private func minutes(_ d: Date) -> Int {
        let c = cal.dateComponents([.hour, .minute], from: d)
        return (c.hour ?? 0) * 60 + (c.minute ?? 0)
    }
}

/// The stations nearest to the phone, with the walk to each; picking one makes it the origin.
struct NearbyStationsSheet: View {
    @Environment(DataService.self) private var data
    @Environment(LocationService.self) private var loc
    @Environment(\.dismiss) private var dismiss
    let onPick: (Station) -> Void

    private var nearby: [NearbyStation] {
        guard let l = loc.location, let sched = data.schedule, let index = data.index, let geo = data.geometry else { return [] }
        let coords = stationCoordinates(schedule: sched, index: index, geometry: geo)
        return nearestStations(to: (l.coordinate.latitude, l.coordinate.longitude), coords: coords, index: index, n: 8)
    }

    var body: some View {
        NavigationStack {
            Group {
                if let err = loc.error {
                    ContentUnavailableView {
                        Label("No location", systemImage: "location.slash")
                    } description: {
                        Text(err)
                    } actions: {
                        Button("Try again") { loc.request() }
                    }
                } else if loc.location == nil || data.geometry == nil {
                    ProgressView(loc.location == nil ? "Finding you…" : "Loading station locations…")
                } else {
                    List(nearby) { n in
                        Button {
                            onPick(n.station)
                            dismiss()
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(n.station.name).foregroundStyle(Color.primary)
                                    Text("\(Fmt.miles(n.meters)) · about \(n.walkMinutes) min walk").font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                RouteBullets(routes: n.station.routes, size: 18)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Near you")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            .onAppear {
                data.requestGeometry()
                loc.request()
            }
        }
    }
}

/// Settings: the saved commutes with edit, reorder and delete.
struct CommutesSection: View {
    @Environment(DataService.self) private var data
    @Environment(PresetStore.self) private var presets
    @State private var editing: CommutePreset? = nil

    var body: some View {
        Section("Commutes") {
            ForEach(presets.presets) { p in
                Button { editing = p } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(p.name).foregroundStyle(Color.primary)
                        Text("\(p.useNearestOrigin ? "nearest station" : (data.index?.station(p.originId)?.name ?? p.originId)) → \(data.index?.station(p.destId)?.name ?? p.destId) · \(p.windowText)")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .onDelete { presets.remove(at: $0) }
            .onMove { presets.move(from: $0, to: $1) }
            Button("Add commute") {
                let w = PresetStore.suggestedWindow(at: data.now)
                editing = CommutePreset(name: PresetStore.suggestedName(at: data.now), originId: "", destId: "", startMinute: w.start, endMinute: w.end)
            }
            .disabled(data.index == nil)
            Text("The first commute whose window covers the current time is applied when the Go tab opens; the order decides when windows overlap.").font(.caption).foregroundStyle(.secondary)
        }
        .sheet(item: $editing) { p in
            PresetEditorView(preset: p) { saved in presets.update(saved) }
        }
    }
}
