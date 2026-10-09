import SwiftUI

/// The first launch: what the app does, that sharing trips is on, what that means, and the switch to turn it
/// off right here. Shown once; Settings keeps the switch.
struct WelcomeView: View {
    static let shownKey = "welcomeShown"
    static var shown: Bool {
        get { UserDefaults.standard.bool(forKey: shownKey) }
        set { UserDefaults.standard.set(newValue, forKey: shownKey) }
    }

    @Environment(\.dismiss) private var dismiss
    private var tele: Telemetry { Telemetry.shared }
    @State private var sharing = Telemetry.shared.optIn

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HStack(spacing: 12) {
                        Image(systemName: "tram.fill").font(.largeTitle).foregroundStyle(Color.accentColor)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Welcome to WhichWay").font(.title2.bold())
                            Text("Which train to take, when it really boards, when you really arrive.").font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                    Text("WhichWay reads the MTA's live feeds and a model of where trains lose time, ranks every way to your destination, counts down to the train to take and follows your ride.")
                        .font(.body)
                    GroupBox {
                        VStack(alignment: .leading, spacing: 10) {
                            Toggle(isOn: $sharing) {
                                Text(sharing ? "Sharing your trips is on" : "Sharing your trips is off").font(.headline)
                            }
                            .onChange(of: sharing) { _, on in tele.setOptIn(on) }
                            Text("Your trips help the predictions improve: each route you ride is checked against the trains that actually ran.")
                                .font(.subheadline)
                            VStack(alignment: .leading, spacing: 4) {
                                row("checkmark.circle", "Shared: the stations and lines, the predicted and observed times, the moments your train pulled away and you walked off, your walking pace and change times.")
                                row("xmark.circle", "Never shared: your location, your places, raw sensor data, or anything that identifies you.")
                                row("arrow.counterclockwise", "Switching it off deletes what was collected. The switch is in Settings whenever you want it.")
                            }
                            .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Button(sharing ? "Keep sharing on" : "Continue without sharing") { finish() }
                        .buttonStyle(.borderedProminent).frame(maxWidth: .infinity)
                    if sharing {
                        Button("Turn sharing off") { sharing = false }
                            .frame(maxWidth: .infinity)
                    } else {
                        Button("Turn sharing on") { sharing = true }
                            .frame(maxWidth: .infinity)
                    }
                }
                .padding(20)
            }
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled()
        }
    }

    private func row(_ symbol: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: symbol).frame(width: 16)
            Text(text)
        }
    }

    private func finish() {
        WelcomeView.shown = true
        dismiss()
    }
}
