import SwiftUI

/// Overall health of one line direction right now, from the live board, the engine's projection and the alerts:
/// how late its trains run, whether any is held, the largest gap forming against the usual headway, and whether an
/// unplanned alert is active. Four levels, each with the reason spelled out.
struct LineHealth {
    enum Level: Int { case good = 0, minor, delays, severe }
    var level: Level
    var reason: String

    var label: String {
        switch level {
        case .good: return "Good service"
        case .minor: return "Minor delays"
        case .delays: return "Delays"
        case .severe: return "Severe delays"
        }
    }

    var color: Color {
        switch level {
        case .good: return .green
        case .minor: return .yellow
        case .delays: return .orange
        case .severe: return .red
        }
    }
}

@MainActor func lineHealth(key: String, data: DataService) -> LineHealth? {
    guard let b = data.boards[key] else { return nil }
    let route = String(key.split(separator: "_").first ?? "")
    let trains = b.trains
    guard !trains.isEmpty else { return LineHealth(level: .minor, reason: "no started train in the feed") }
    let lates = trains.compactMap { $0.effectiveLatenessSec }.map { max(0, $0) }.sorted()
    let median = lates.isEmpty ? 0 : lates[lates.count / 2]
    let nLate = trains.filter { ($0.effectiveLatenessSec ?? 0) >= 180 }.count
    let lateShare = Double(nLate) / Double(trains.count)
    let held = b.nHolding + b.nStalled
    let delayAlerts = data.alertsFor(routes: [route]).filter { $0.kind == "delay" }
    var gapRatio = 0.0
    var gapText = ""
    if let lp = data.predictions[key]?[data.scenario] ?? data.predictions[key]?["baseline"], let w = lp.worstGap, let sched = data.schedule,
       let line = sched.lines[key], w.idx < line.stops.count {
        let usual = schedHeadwayAt(schedule: sched, lineSched: data.lineSched, keys: [key], stop: line.stops[w.idx], now: data.now) ?? 0
        if usual > 0 {
            gapRatio = w.gapSec / usual
            gapText = "largest gap \(Fmt.minTxt(w.gapSec)) (usually \(Fmt.minTxt(usual)))"
        }
    }
    var level: LineHealth.Level = .good
    if median >= 600 || held >= 2 || gapRatio >= 3 { level = .severe }
    else if median >= 300 || lateShare >= 0.5 || gapRatio >= 2 || !delayAlerts.isEmpty { level = .delays }
    else if median >= 120 || lateShare >= 0.25 || gapRatio >= 1.5 || held >= 1 { level = .minor }
    var parts: [String] = []
    parts.append(nLate == 0 ? "no train 3+ min late" : "\(nLate) of \(trains.count) trains 3+ min late")
    if median >= 60 { parts.append("typically \(Fmt.minTxt(median)) behind") }
    if held > 0 { parts.append("\(held) held or overdue") }
    if gapRatio >= 1.5, !gapText.isEmpty { parts.append(gapText) }
    if !delayAlerts.isEmpty { parts.append("delay alert active") }
    return LineHealth(level: level, reason: parts.joined(separator: " · "))
}

/// The indicator: a coloured dot, the level, and the reason.
struct LineHealthRow: View {
    let health: LineHealth
    let route: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle().fill(health.color).frame(width: 10, height: 10).padding(.top, 5)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(route) line: \(health.label)").font(.subheadline.bold()).foregroundStyle(health.level == .good ? Color.primary : health.color)
                Text(health.reason).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(health.color.opacity(health.level == .good ? 0 : 0.5), lineWidth: 1))
    }
}
