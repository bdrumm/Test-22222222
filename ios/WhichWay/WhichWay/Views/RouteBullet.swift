import SwiftUI

struct Flag: View {
    let text: String
    let color: Color
    init(_ text: String, _ color: Color) { self.text = text; self.color = color }

    var body: some View {
        Text(text)
            .font(.system(size: 9, weight: .semibold))
            .padding(.horizontal, 5).padding(.vertical, 2)
            .background(Capsule().fill(color.opacity(0.18)))
            .foregroundStyle(color)
    }
}

struct Tile: View {
    let title: String
    let value: String
    let sub: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            Text(value).font(.title3.bold()).lineLimit(1).minimumScaleFactor(0.6)
            Text(sub).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
    }
}

/// Feed freshness in the navigation bar: green under 90 s since the last poll, orange after, grey before the first.
struct StatusDot: View {
    @Environment(DataService.self) private var data

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            let age: Double? = data.lastUpdate.map { Date().timeIntervalSince($0) }
            HStack(spacing: 4) {
                Circle().fill(dotColor(age)).frame(width: 8, height: 8)
                if data.offline {
                    Text("offline · \(data.lastFeedFetch.map { Fmt.hhmm($0.timeIntervalSince1970) } ?? "saved data")").font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text(age.map { "\(Int($0)) s" } ?? "…").font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                }
            }
        }
    }

    private func dotColor(_ age: Double?) -> Color {
        if data.offline { return Color.red }
        guard let a = age else { return Color.gray }
        return a < 90 ? Color.green : Color.orange
    }
}
