import SwiftUI

struct SettingsView: View {
    @Environment(DataService.self) private var data
    @State private var base = ""
    @State private var poll = 30.0

    var body: some View {
        NavigationStack {
            Form {
                Section("Data source") {
                    TextField("Base URL of the published data", text: $base)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Stepper("Poll the feeds every \(Int(poll)) s", value: $poll, in: 10...120, step: 5)
                    Button("Apply and reload") { data.configure(baseURL: base, pollSec: poll) }
                    Button("Use the local server (localhost:8000)") { base = DataService.localBase }
                    Button("Use the published site") { base = DataService.publishedBase }
                    Text("`make serve` in the repository serves the built site on port 8000 with live data refreshed from the feeds. The Simulator reaches localhost; a device needs your Mac's address on the local network, e.g. http://192.168.1.20:8000/data/.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Status") {
                    LabeledContent("Schedule", value: data.schedule.map { "\($0.lines.count) line directions · \($0.serviceDate ?? "")" } ?? "not loaded")
                    LabeledContent("Timetable extract", value: "\(data.lineSched.values.reduce(0) { $0 + $1.count }) trips")
                    LabeledContent("Hold log", value: data.holds.map { "\($0.n) holds" } ?? "–")
                    LabeledContent("Segment runs", value: data.segments.map { "\($0.n) runs · \($0.byKey.count) segments" } ?? "–")
                    LabeledContent("Deviation grids", value: "\(data.deviations.count) lines")
                    LabeledContent("Prediction engine", value: data.model?.summary ?? "no tables yet (physical priors)")
                    LabeledContent("Next poll", value: data.isDemo ? "demo clock" : "in \(Int(data.nextPollSec.rounded())) s, aligned to the feed")
                    LabeledContent("Feeds", value: data.feeds.keys.sorted().joined(separator: ", "))
                    LabeledContent("Alerts", value: "\(data.alerts.count) active")
                    LabeledContent("Last poll", value: data.lastUpdate.map { Fmt.hhmmss($0.timeIntervalSince1970) } ?? "–")
                    LabeledContent("Offline copy", value: data.cacheSummary)
                    Button("Clear the offline copy") { data.clearCache() }
                    Text("Every file fetched is kept on the phone: without signal the app keeps planning from the timetable and the saved tables, with the trains where they were last seen.").font(.caption).foregroundStyle(.secondary)
                    if data.isDemo { Text("Demo clock: the schedule pins the current time (demo_now).").font(.caption) }
                    if let e = data.lastError { Text(e).font(.caption).foregroundStyle(Color.red) }
                }
                CommutesSection()
                TelemetrySection()
                Section("About") {
                    Text("WhichWay reads the MTA GTFS-Realtime feeds directly and layers the published delay analysis on top: the timetable extract for lateness, the hold log for hold risk, per-line deviation grids for the time trains typically lose at this hour, and measured segment run times for speeds. Times are New York local.")
                        .font(.caption)
                }
            }
            .navigationTitle("Settings")
            .onAppear {
                base = data.baseURL
                poll = data.pollSec
            }
        }
    }
}

/// Opt-in, anonymous trip motion: what it is, what is kept, and the controls over it.
struct TelemetrySection: View {
    @Environment(DataService.self) private var data
    private var tele: Telemetry { Telemetry.shared }

    var body: some View {
        Section("Improve the predictions") {
            Toggle("Share anonymous trip motion", isOn: Binding(get: { tele.optIn }, set: { tele.setOptIn($0) }))
                .disabled(!tele.sensorsAvailable)
            Text("Off unless you turn it on. While a route is in progress, the phone's motion sensors are summarised once a second to notice when your train pulls away and when you walk off it, so the predictions can be checked against real boardings and changes. Kept: the route's stations and lines, the predicted and observed times, the train's lateness, and those moments. Never kept: your location, raw sensor data, or anything that identifies you. A random id groups this phone's trips; switching this off deletes what was collected and resets the id.")
                .font(.caption).foregroundStyle(.secondary)
            if !tele.sensorsAvailable { Text("This device has no motion sensors.").font(.caption).foregroundStyle(.secondary) }
            if tele.optIn {
                LabeledContent("Motion now", value: tele.current == nil ? "not on a route" : tele.motionState.rawValue)
                LabeledContent("Trips recorded", value: "\(tele.observations.count) · \(tele.pendingUpload) to send")
                LabeledContent("Sent to", value: data.apiBase?.host ?? "nowhere: the published site cannot receive, trips stay on the phone")
                if let t = tele.lastUpload { LabeledContent("Last sent", value: Fmt.hhmmss(t.timeIntervalSince1970)) }
                if let e = tele.lastUploadError { Text(e).font(.caption).foregroundStyle(Color.red) }
                Button("Send now") { Task { await tele.upload(to: data.apiBase) } }
                    .disabled(data.apiBase == nil || tele.pendingUpload == 0)
                if let u = tele.exportURL() { ShareLink("Export as JSON", item: u) }
                Button("Delete collected data", role: .destructive) { tele.deleteAll() }
                    .disabled(tele.observations.isEmpty)
            }
        }
    }
}
