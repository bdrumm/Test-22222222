import SwiftUI

/// How a route is doing, as the expected extra time to the destination against the timetable: with a train in
/// the feeds, the engine's ride against the scheduled ride, a wait beyond the usual headway, extra time at the
/// change and the risk of missing it; without one, the time the model says is typically lost at this hour.
/// The reasons carry the context: alerts and whether the feeds bear them out, held trains, knock-on.
struct RouteHealth {
    /// Under 5 min: quiet grey, no notes. 5 to 9 min: yellow. 10 min or more: red.
    enum Level: Int { case smooth = 0, minor = 1, heavy = 2 }
    /// Expected extra seconds to the destination against the timetable.
    var extraSec: Double
    /// How much further the arrival could slip (the engine's 80% window).
    var slipSec: Double
    var reasons: [String]
    /// A train in the feeds backs the estimate.
    var live: Bool

    var level: Level {
        if extraSec < 300 { return .smooth }
        if extraSec < 600 { return .minor }
        return .heavy
    }

    var minutes: Int { Int((extraSec / 60).rounded()) }

    /// "on time" under a minute and a half, else the extra minutes.
    var label: String { extraSec < 90 ? (live ? "on time" : "as scheduled") : "+\(minutes) min" }

    var color: Color {
        switch level {
        case .smooth: return .gray
        case .minor: return .yellow
        case .heavy: return .red
        }
    }

    /// Text in the level's hue that stays legible on the row background, in light and dark mode.
    var textColor: Color {
        func c(_ light: (CGFloat, CGFloat, CGFloat), _ dark: (CGFloat, CGFloat, CGFloat)) -> Color {
            Color(uiColor: UIColor { tc in
                let v = tc.userInterfaceStyle == .dark ? dark : light
                return UIColor(red: v.0, green: v.1, blue: v.2, alpha: 1)
            })
        }
        switch level {
        case .smooth: return c((0.40, 0.40, 0.43), (0.70, 0.70, 0.73))
        case .minor: return c((0.58, 0.44, 0.00), (1.00, 0.85, 0.35))
        case .heavy: return c((0.72, 0.08, 0.08), (1.00, 0.45, 0.45))
        }
    }

    /// The two weightiest reasons.
    var summary: String { reasons.prefix(2).joined(separator: " · ") }
}

/// Whether an alert shows in the live data on its routes right now: the boards' lateness, held or overdue
/// trains and the engine's knock-on. Many alerts describe service that runs to time regardless.
struct AlertEvidence {
    var corroborated: Bool
    /// "seen in the feeds: trains 8 min late on average" | "not seen in the feeds right now: trains on time"
    var text: String
}

@MainActor
func alertEvidence(_ a: RouteAlert, data: DataService) -> AlertEvidence {
    let routes = a.routes.joined(separator: "/")
    let boards = data.boards.values.filter { a.routes.contains($0.route) }
    guard !boards.isEmpty else { return AlertEvidence(corroborated: false, text: "no \(routes) feed loaded yet") }
    var lates: [Double] = []
    var held = 0, knock = 0
    for b in boards {
        lates += b.trains.compactMap { $0.effectiveLatenessSec }
        held += b.nHolding + b.nStalled
        if let lp = data.predictions[b.key]?[data.scenario] ?? data.predictions[b.key]?["baseline"] { knock += lp.nKnockOn }
    }
    guard !lates.isEmpty else { return AlertEvidence(corroborated: false, text: "no \(routes) train in the feed right now") }
    let median = lates.sorted()[lates.count / 2]
    let nLate = lates.filter { $0 >= 180 }.count
    var bits: [String] = []
    if median >= 180 { bits.append("trains \(Fmt.late(median)) on average") } else if nLate >= 2 { bits.append("\(nLate) trains 3+ min late") }
    if held > 0 { bits.append("\(held) held or overdue") }
    if knock > 0 { bits.append("\(knock) held back by the train ahead") }
    if bits.isEmpty { return AlertEvidence(corroborated: false, text: "not seen in the feeds right now: trains \(Fmt.late(median))") }
    return AlertEvidence(corroborated: true, text: "seen in the feeds: " + bits.joined(separator: ", "))
}

@MainActor
func routeHealth(_ option: PathOption, data: DataService) -> RouteHealth {
    var extra = 0.0, slip = 0.0
    var reasons: [(Int, String)] = []
    func add(_ weight: Int, _ why: String) { reasons.append((weight, why)) }
    if let it = option.live {
        let ride = it.legs.reduce(0.0) { $0 + ($1.rideVsSchedSec ?? 0) }
        if ride >= 60 { extra += ride; add(3, "ride \(Fmt.minTxt(ride)) slower than scheduled") }
        else if ride <= -60 { add(0, "ride \(Fmt.minTxt(-ride)) faster than scheduled") }
        let headway = max(120, 2 * option.wait1Sec)
        let waitExtra = (it.boardTs - data.now) - headway
        if waitExtra >= 60 { extra += waitExtra; add(3, "\(Fmt.minTxt(waitExtra)) longer wait than the usual headway") }
        if it.legs.count > 1, let m = it.connectionMarginSec {
            let transferExtra = m - 2 * option.wait2Sec
            if transferExtra >= 60 { extra += transferExtra; add(2, "\(Fmt.minTxt(transferExtra)) extra at the change") }
            if m < 60, let n = it.nextIfMissedSec { extra += n / 2; add(2, "tight change, \(Fmt.mmss(m)) margin: +\(Fmt.minTxt(n)) if missed") }
        }
        if let hi = it.legs.last?.arriveHiTs {
            slip = max(0, hi - it.arriveTs)
            if slip >= 120 { add(1, "could slip to \(Fmt.hhmm(hi))") }
        }
        let l0 = it.legs[0].train
        if let p = l0.position, p.holding { add(2, "your train is held at \(p.stopName)") }
        if l0.position?.stalled == true { add(2, "your train is overdue between stops") }
        if l0.corroboration == "feed_optimistic" { add(1, "the feed looks optimistic for your train") }
    } else {
        let model = max(0, option.typicalSec) + option.holdRiskSec
        if model >= 60 { extra += model; add(2, "typically \(Fmt.minTxt(model)) lost at this hour") }
        add(0, "no train for this path in the feeds yet")
    }
    var seenAlerts = Set<String>()
    var seenKeys = Set<String>()
    for leg in option.legs {
        for a in data.alertsFor(routes: leg.routes) where !seenAlerts.contains(a.id) {
            seenAlerts.insert(a.id)
            let ev = alertEvidence(a, data: data)
            let routes = a.routes.joined(separator: "/")
            // an alert the feeds bear out carries weight; one they do not is context only
            if a.kind == "delay" { add(ev.corroborated ? 2 : 0, "delay alert on the \(routes)") }
            else if a.kind == "planned" { add(ev.corroborated ? 1 : 0, "planned work on the \(routes)") }
        }
        for k in leg.keys where !seenKeys.contains(k) {
            seenKeys.insert(k)
            if let b = data.boards[k], b.nHolding + b.nStalled > 0 {
                let n = b.nHolding + b.nStalled
                add(1, "\(n) \(b.route) train\(n == 1 ? "" : "s") held or overdue")
            }
            if let lp = data.predictions[k]?[data.scenario] ?? data.predictions[k]?["baseline"], lp.nKnockOn > 0 {
                add(1, "\(lp.nKnockOn) held back by the train ahead")
            }
        }
    }
    // reasons that carry weight, weightiest first; the context-only ones appear when nothing else does
    let weighted = reasons.filter { $0.0 > 0 }
    var shown: [String] = []
    for r in (weighted.isEmpty ? reasons : weighted).sorted(by: { $0.0 > $1.0 }).map({ $0.1 }) where !shown.contains(r) { shown.append(r) }
    return RouteHealth(extraSec: extra, slipSec: slip, reasons: shown, live: option.live != nil)
}

/// The dot and its word, on a tinted capsule.
struct HealthDot: View {
    let health: RouteHealth

    var body: some View {
        HStack(spacing: 4) {
            Circle().fill(health.color).frame(width: 7, height: 7)
            Text(health.label).font(.caption2.weight(.semibold)).foregroundStyle(health.textColor)
        }
        .padding(.horizontal, 7).padding(.vertical, 3)
        .background(Capsule().fill(health.color.opacity(0.16)))
        .accessibilityLabel("Route \(health.label)")
    }
}
