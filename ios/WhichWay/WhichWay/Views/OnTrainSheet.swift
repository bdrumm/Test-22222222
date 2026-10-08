import SwiftUI

/// The rider's own word on the train they are on: the trains that have left their station toward the destination
/// (most recent first), each with the route it leads to, and, with a change ahead, the line to take at it. Used
/// to start a route from a train already boarded, to put a route right when the phone guessed wrong or never
/// felt the pull-away, and to redirect the rest of the trip.
struct OnTrainSheet: View {
    struct TransferChoice: Identifiable {
        var key: String
        var label: String
        var id: String { key }
    }
    let stationName: String
    let candidates: [BoardingCandidate]
    let currentTrainId: String?
    let routeFor: (String) -> String?
    let describe: (BoardingCandidate) -> String
    let transfer: (station: String, choices: [TransferChoice])?
    let riding: Bool
    let onPick: (BoardingCandidate) -> Void
    let onTransfer: (String) -> Void
    let onNotOnTrain: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if candidates.isEmpty {
                        Text("No train has left \(stationName) toward your destination in the last 25 minutes, as far as the feeds show. Lines not on the list yet appear after the next refresh.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    ForEach(candidates, id: \.trainId) { c in
                        Button { onPick(c); dismiss() } label: {
                            HStack(spacing: 10) {
                                RouteBullet(route: c.route, size: 26)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("Left \(stationName) about \(Fmt.hhmm(PlatformTiming.pullsAway(c.boardTs, route: c.route)))").font(.subheadline.weight(.semibold))
                                    Text(describe(c)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                    if let r = routeFor(c.key) { Text(r).font(.caption2).foregroundStyle(.secondary).lineLimit(1) }
                                }
                                Spacer(minLength: 0)
                                if c.trainId == currentTrainId { Image(systemName: "checkmark").font(.body.bold()).foregroundStyle(Color.accentColor) }
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("I am on the \(c.route) that left \(stationName) at \(Fmt.hhmm(PlatformTiming.pullsAway(c.boardTs, route: c.route)))")
                    }
                } header: {
                    Text("Which train are you on?")
                } footer: {
                    Text("The route follows the train you pick: the change ahead, the arrival and the Live Activity.")
                }
                if let t = transfer, !t.choices.isEmpty {
                    Section("At \(t.station), take the") {
                        ForEach(t.choices) { ch in
                            Button { onTransfer(ch.key); dismiss() } label: {
                                HStack(spacing: 10) {
                                    RouteBullet(route: String(ch.key.split(separator: "_").first ?? ""), size: 26)
                                    Text(ch.label).font(.subheadline).lineLimit(2)
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                if riding {
                    Section {
                        Button("Not on a train", role: .destructive) { onNotOnTrain(); dismiss() }
                    }
                }
            }
            .navigationTitle("Your train")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}
