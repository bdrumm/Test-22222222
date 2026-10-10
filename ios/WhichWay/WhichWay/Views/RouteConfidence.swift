import SwiftUI

/// How far the model trusts a route's arrival, from its own figures: the width of the engine's 80% window, how many
/// calibration samples stand behind each line at this horizon, whether a train is in the feeds at all, a held or
/// overdue train, a tight change, and how far the arrival would move between the hold clearing now and dragging on
/// (the scenarios the Go tab used to offer as choices). Three levels, with the reasons that set them.
struct RouteConfidence {
    enum Level { case high, fair, low }
    /// 1 is the model at its surest; the penalties below take from it.
    var score: Double
    /// Why the level is what it is: the weightiest penalties, or what the model has going for it.
    var reasons: [String]

    var level: Level { score >= 0.72 ? .high : (score >= 0.45 ? .fair : .low) }

    var label: String {
        switch level {
        case .high: return "High confidence"
        case .fair: return "Fair confidence"
        case .low: return "Low confidence"
        }
    }

    var color: Color {
        switch level {
        case .high: return Color(red: 0.19, green: 0.82, blue: 0.35)
        case .fair: return Color(red: 1.0, green: 0.84, blue: 0.04)
        case .low: return Color(red: 1.0, green: 0.62, blue: 0.04)
        }
    }

    /// The two weightiest reasons, for one line.
    var summary: String { reasons.prefix(2).joined(separator: ", ") }
}

@MainActor
func routeConfidence(_ option: PathOption, itinerary: Itinerary?, outlook: HoldOutlook?, data: DataService) -> RouteConfidence {
    guard let it = itinerary, let last = it.legs.last else {
        // nothing in the feeds: the timetable and the hour's typical losses are all there is
        let model = max(0, option.typicalSec) + option.holdRiskSec
        var reasons = ["no train in the feeds yet"]
        if model >= 60 { reasons.append("typically \(Fmt.minTxt(model)) lost at this hour") }
        return RouteConfidence(score: 0.35, reasons: reasons)
    }
    var score = 1.0
    var penalties: [(Double, String)] = []
    func take(_ penalty: Double, _ why: String) { score -= penalty; penalties.append((penalty, why)) }

    // the engine's own window: a tight one is the model being sure of itself
    var windowText = ""
    if let lo = last.arriveLoTs, let hi = last.arriveHiTs {
        let w = hi - lo
        windowText = "a \(Fmt.minTxt(w)) window"
        if w > 180 { take(min(0.4, (w - 180) / 900), windowText) }
    } else {
        take(0.15, "no window from the engine")
    }

    // the calibration behind each line at this horizon: few samples, less to stand on (no table at all says
    // nothing either way: the thin local build)
    if data.model?.etaCalibration != nil {
        let now = data.now
        var thin: [String] = []
        for leg in it.legs where Predictor.calibrationAt(data.model, route: leg.train.route, horizon: max(0, leg.boardTs - now)).n < 30 {
            thin.append(leg.train.route)
        }
        if !thin.isEmpty { take(0.08 * Double(thin.count), "few samples on the \(thin.joined(separator: "/"))") }
    }

    // the train itself
    let l0 = it.legs[0].train
    if l0.position?.holding == true { take(0.2, "your train is held") }
    else if l0.position?.stalled == true { take(0.2, "your train is overdue between stops") }
    if l0.corroboration == "feed_optimistic" { take(0.1, "the feed looks optimistic for your train") }
    else if l0.corroboration == "position_unknown" { take(0.1, "no position report for your train") }

    // the change
    if it.legs.count > 1, let m = it.connectionMarginSec {
        if m < 60 { take(0.25, "a tight change, \(Fmt.mmss(m)) margin") }
        else if m < 120 { take(0.1, "a close change, \(Fmt.mmss(m)) margin") }
    }

    // a hold ahead: how far the arrival moves between the hold clearing now and dragging on
    if let o = outlook {
        let vals = [o.usual, o.dragsOn, o.clearsNow].compactMap { $0 }
        if let lo = vals.min(), let hi = vals.max(), hi - lo >= 60 {
            take(min(0.3, (hi - lo) / 900), "a hold ahead could move the arrival \(Fmt.minTxt(hi - lo))")
        }
    }

    // the lines around the train
    var seen = Set<String>()
    var held = 0, knock = 0
    for leg in option.legs {
        for k in leg.keys where !seen.contains(k) {
            seen.insert(k)
            if let b = data.boards[k] { held += b.nHolding + b.nStalled }
            if let lp = data.predictions[k]?[data.scenario] ?? data.predictions[k]?["baseline"] { knock += lp.nKnockOn }
        }
    }
    if held > 0 { take(min(0.1, 0.05 * Double(held)), "\(held) train\(held == 1 ? "" : "s") held or overdue on the way") }
    if knock > 0 { take(min(0.1, 0.05 * Double(knock)), "\(knock) held back by the train ahead") }

    score = max(0, min(1, score))
    let why = penalties.filter { $0.0 > 0 }.sorted { $0.0 > $1.0 }.map { $0.1 }
    if why.isEmpty {
        return RouteConfidence(score: score, reasons: ["train in the feeds", windowText].filter { !$0.isEmpty })
    }
    return RouteConfidence(score: score, reasons: why)
}
