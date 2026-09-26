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
                    Button("Reset to the published site") {
                        base = DataService.defaultBase
                        poll = 30
                    }
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
                    if data.isDemo { Text("Demo clock: the schedule pins the current time (demo_now).").font(.caption) }
                    if let e = data.lastError { Text(e).font(.caption).foregroundStyle(Color.red) }
                }
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
