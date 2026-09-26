import SwiftUI
import CoreLocation

/// The saved commutes as chips: the one whose window covers now carries a clock, the one on screen is outlined;
/// the last chip saves the current trip as a new commute.
struct PresetChips: View {
    let presets: [CommutePreset]
    let activeId: UUID?
    let currentId: UUID?
    let canAdd: Bool
    let onPick: (CommutePreset) -> Void
    let onAdd: () -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(presets) { p in
                    Button { onPick(p) } label: {
                        HStack(spacing: 4) {
                            if p.id == activeId { Image(systemName: "clock.fill").font(.caption2) }
                            if p.useNearestOrigin { Image(systemName: "location.fill").font(.caption2) }
                            Text(p.name).font(.caption.bold())
                            Text(p.windowText).font(.caption2).foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(Capsule().fill(p.id == currentId ? Color.accentColor.opacity(0.18) : Color(.secondarySystemBackground)))
                        .overlay(Capsule().stroke(p.id == currentId ? Color.accentColor : Color.clear, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                }
                Button(action: onAdd) {
                    Label(presets.isEmpty ? "Save as commute" : "Add commute", systemImage: "plus")
                        .font(.caption.bold())
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(Capsule().fill(Color(.secondarySystemBackground)))
                }
                .buttonStyle(.plain)
                .disabled(!canAdd)
            }
        }
    }
}

/// Create or edit a commute: name, stations (or the nearest station by location), the daily window, weekdays.
struct PresetEditorView: View {
    @Environment(DataService.self) private var data
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
                        StationButton(label: "From", station: data.index?.stations[preset.originId]) { pickingOrigin = true }
                    }
                    StationButton(label: "To", station: data.index?.stations[preset.destId]) { pickingDest = true }
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
                    StationPickerSheet(title: "From", stations: index.sorted, reach: nil) { st in preset.originId = st.id }
                }
            }
            .sheet(isPresented: $pickingDest) {
                if let index = data.index, let sched = data.schedule {
                    let reach: [String: Reach]? = (preset.useNearestOrigin || preset.originId.isEmpty) ? nil : reachableStations(schedule: sched, index: index, from: preset.originId)
                    StationPickerSheet(title: "To", stations: index.sorted, reach: reach) { st in preset.destId = st.id }
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
                                    Text("\(Int(n.meters.rounded())) m · about \(n.walkMinutes) min walk").font(.caption).foregroundStyle(.secondary)
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
                        Text("\(p.useNearestOrigin ? "nearest station" : (data.index?.stations[p.originId]?.name ?? p.originId)) → \(data.index?.stations[p.destId]?.name ?? p.destId) · \(p.windowText)")
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
