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
                PlacesSection()
                PaceSection()
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
            Text("Off unless you turn it on. While a route is in progress, the phone's motion sensors are summarised once a second to notice when your train pulls away and when you walk off it, so the predictions can be checked against real boardings and changes. Kept: the route's stations and lines, the predicted and observed times, the train's lateness, those moments, and the trip's own measurements (walking pace, time to the platform, time changing trains). Never kept: your location, your places, raw sensor data, or anything that identifies you. A random id groups this phone's trips; switching this off deletes what was collected and resets the id.")
                .font(.caption).foregroundStyle(.secondary)
            if !tele.sensorsAvailable { Text("This device has no motion sensors.").font(.caption).foregroundStyle(.secondary) }
            if tele.optIn {
                LabeledContent("Motion now", value: TripRecorder.shared.phase == nil ? "not on a route" : TripRecorder.shared.motionState.rawValue)
                LabeledContent("Trips recorded", value: "\(tele.observations.count) · \(tele.pendingUpload) to send")
                TextField("Trip server, e.g. http://my-mac.local:8000/", text: Binding(get: { tele.server }, set: { tele.setServer($0) }))
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                Text("The local server on your Mac (make serve), reached on the home network. Trips are sent when they end and when the app opens; the Mac reviews each one against the trains (data/trips/trip_review.md).")
                    .font(.caption).foregroundStyle(.secondary)
                LabeledContent("Sent to", value: tele.uploadURL(fallback: data.apiBase)?.host ?? "nowhere: the published site cannot receive, trips stay on the phone")
                if let t = tele.lastUpload { LabeledContent("Last sent", value: Fmt.hhmmss(t.timeIntervalSince1970)) }
                if let e = tele.lastUploadError { Text(e).font(.caption).foregroundStyle(Color.red) }
                Button("Send now") { Task { await tele.upload(to: tele.uploadURL(fallback: data.apiBase)) } }
                    .disabled(tele.uploadURL(fallback: data.apiBase) == nil || tele.pendingUpload == 0)
                if let u = tele.exportURL() { ShareLink("Export as JSON", item: u) }
                #if DEBUG
                Toggle("Keep motion traces (developer)", isOn: Binding(get: { MotionTrace.shared.enabled }, set: { MotionTrace.shared.enabled = $0 }))
                Text("Keeps each route's summarised motion, one line a second, on this phone (the last 30 routes), to tune when a train is felt pulling away. Copied off with make trips, and written to your GitHub data repository when that is set up below.")
                    .font(.caption).foregroundStyle(.secondary)
                #endif
                Button("Delete collected data", role: .destructive) { tele.deleteAll() }
                    .disabled(tele.observations.isEmpty)
            }
        }
        if tele.optIn {
            if TripRelay.shared.configured { RelayUploadSection() } else { GitHubUploadSection() }
        }
    }
}

/// Trips to the WhichWay data repository through the relay the build names: nothing to set up on the phone.
struct RelayUploadSection: View {
    private var relay: TripRelay { TripRelay.shared }
    private var tele: Telemetry { Telemetry.shared }
    @State private var sending = false
    @State private var refresh = 0

    var body: some View {
        Section("Trips to WhichWay") {
            LabeledContent("Sent through", value: relay.host)
            Text("Each trip you share, and its motion trace, goes to the WhichWay data repository when it ends and whenever the app opens, over Wi-Fi or cellular, so the predictions can be checked against real rides. It carries the route, the times and the trip's own measurements under this phone's random id; never your location or anything that identifies you.")
                .font(.caption).foregroundStyle(.secondary)
            LabeledContent("Waiting to send", value: "\(tele.pendingGitHub) trip\(tele.pendingGitHub == 1 ? "" : "s")")
            if let t = relay.lastUpload { LabeledContent("Last sent", value: Fmt.hhmmss(t.timeIntervalSince1970)) }
            if let e = relay.lastError { Text(e).font(.caption).foregroundStyle(Color.red) }
            Button(sending ? "Sending…" : "Send now") {
                sending = true
                Task { await tele.uploadToGitHub(); sending = false; refresh += 1 }
            }
            .disabled(sending || tele.pendingGitHub == 0)
        }
        .id(refresh)
    }
}

/// Trips to a private GitHub repository the rider owns, from any connection: the repository and a fine-grained
/// token for it alone (Contents: read and write), kept in the Keychain.
struct GitHubUploadSection: View {
    private var gh: GitHubUploader { GitHubUploader.shared }
    private var tele: Telemetry { Telemetry.shared }
    @State private var repo = GitHubUploader.shared.repo
    @State private var tokenField = ""
    @State private var hasToken = GitHubUploader.shared.hasToken
    @State private var sending = false
    @State private var refresh = 0

    var body: some View {
        Section("Upload trips to GitHub") {
            TextField("owner/repository", text: $repo)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .onSubmit { gh.repo = repo }
            if hasToken {
                LabeledContent("Token", value: "saved in the Keychain")
                Button("Remove token", role: .destructive) { gh.setToken(nil); hasToken = false }
            } else {
                SecureField("Fine-grained token for this repository", text: $tokenField)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                Button("Save token") { gh.repo = repo; gh.setToken(tokenField); tokenField = ""; hasToken = gh.hasToken }
                    .disabled(tokenField.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            Text("Each trip, and its motion trace, becomes a file in this repository when it ends and whenever the app opens, over Wi-Fi or cellular. Keep the repository private. Make the token at github.com › Settings › Developer settings › Fine-grained tokens, for this repository only, with Contents: read and write.")
                .font(.caption).foregroundStyle(.secondary)
            LabeledContent("Waiting to upload", value: "\(tele.pendingGitHub) trip\(tele.pendingGitHub == 1 ? "" : "s")")
            if let t = gh.lastUpload { LabeledContent("Last upload", value: Fmt.hhmmss(t.timeIntervalSince1970)) }
            if let e = gh.lastError { Text(e).font(.caption).foregroundStyle(Color.red) }
            Button(sending ? "Uploading…" : "Upload now") {
                gh.repo = repo
                sending = true
                Task { await tele.uploadToGitHub(); sending = false; refresh += 1 }
            }
            .disabled(!hasToken || sending)
        }
        .id(refresh)
    }
}
