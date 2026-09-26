import SwiftUI

enum TravelMode: String, CaseIterable {
    case track, timeline, board, map, hours
    var title: String {
        switch self {
        case .track: return "Track"
        case .timeline: return "Time"
        case .board: return "Board"
        case .map: return "Map"
        case .hours: return "Hours"
        }
    }
}

/// The selected path in one of five views: the track diagram, the Marey timeline, the departure board, the map,
/// and the hour-of-day profile. The choice is remembered.
struct PathViewsView: View {
    let option: PathOption
    let schedule: ClientSchedule
    let originName: String
    let destName: String
    @AppStorage("travelMode") private var modeRaw = TravelMode.track.rawValue
    private var mode: TravelMode { TravelMode(rawValue: modeRaw) ?? .track }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("View", selection: $modeRaw) {
                ForEach(TravelMode.allCases, id: \.rawValue) { m in Text(m.title).tag(m.rawValue) }
            }
            .pickerStyle(.segmented)
            switch mode {
            case .track:
                ForEach(Array(option.legs.enumerated()), id: \.offset) { i, leg in
                    LegDiagram(leg: leg, legNo: i + 1, option: option, schedule: schedule)
                }
            case .timeline:
                MareyChartView(option: option, schedule: schedule)
            case .board:
                DepartureBoardView(option: option, schedule: schedule, originName: originName, destName: destName)
            case .map:
                RouteMapView(option: option, schedule: schedule)
            case .hours:
                HoursView(option: option, schedule: schedule)
            }
        }
    }
}

/// The headline: which train to take, when it boards (counting down), when it gets you there and how sure the
/// engine is.
struct NowCard: View {
    @Environment(DataService.self) private var data
    let option: PathOption?
    let originName: String
    let destName: String

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            let now = data.now
            VStack(alignment: .leading, spacing: 6) {
                if let p = option, let it = p.live, let l0 = it.legs.first, let ln = it.legs.last {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("Take the").font(.subheadline).foregroundStyle(.secondary)
                        RouteBullet(route: l0.train.route, size: 26)
                        Text("at \(Fmt.hhmm(it.boardTs))").font(.title3.bold())
                        Spacer()
                        Text(Fmt.mmss(max(0, it.boardTs - now))).font(.system(size: 34, weight: .bold, design: .rounded)).monospacedDigit()
                    }
                    HStack(alignment: .firstTextBaseline) {
                        Text("Arrive \(destName) \(Fmt.hhmm(it.arriveTs))").font(.headline)
                        Spacer()
                        Text(Fmt.minTxt(it.totalSec)).font(.subheadline).foregroundStyle(.secondary)
                    }
                    if let rt = ln.rangeText { Text("80% window \(rt)").font(.caption).foregroundStyle(.secondary) }
                    Text(detailLine(p, it, l0)).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                } else if let p = option {
                    Text("From \(originName) to \(destName)").font(.headline)
                    Text(data.predictedBoards.isEmpty ? "Waiting for the live feeds…" : "No train for this path in the feeds right now.").font(.subheadline).foregroundStyle(.secondary)
                    Text("Expected \(Fmt.minTxt(p.expectedSec)) door to door · \(Fmt.minTxt(Double(p.schedSec))) scheduled").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Pick where you are and where you're going.").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color.accentColor.opacity(0.10)))
        }
    }

    private func detailLine(_ p: PathOption, _ it: Itinerary, _ l0: TripCandidate) -> String {
        var bits: [String] = []
        if let pos = l0.train.position { bits.append("train now \(pos.text)") }
        if let e = l0.train.effectiveLatenessSec, abs(e) >= 60 { bits.append(Fmt.late(e)) }
        if it.legs.count > 1, let m = it.connectionMarginSec, let tr = p.transfer { bits.append("change at \(tr.station): \(Fmt.mmss(m)) margin") }
        bits.append("expected \(Fmt.minTxt(p.expectedSec)) · scheduled \(Fmt.minTxt(Double(p.schedSec)))")
        if abs(p.typicalSec) >= 20 { bits.append("\(Fmt.signed(p.typicalSec)) typical at this hour") }
        return bits.joined(separator: " · ")
    }
}

/// Shown when a train on the path's lines is held: what the engine assumes about the hold.
struct ScenarioPicker: View {
    @Environment(DataService.self) private var data

    var body: some View {
        if data.anyHeld {
            VStack(alignment: .leading, spacing: 4) {
                Text("A train on these lines is held. Times assume the hold…").font(.caption).foregroundStyle(.secondary)
                Picker("Scenario", selection: Binding(get: { data.scenario }, set: { data.setScenario($0) })) {
                    Text("ends as usual").tag("baseline")
                    Text("drags on (p90)").tag("hold_persists")
                    Text("clears now").tag("clears_now")
                }
                .pickerStyle(.segmented)
            }
        }
    }
}

// MARK: - departure board

struct DepartureBoardView: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    let schedule: ClientSchedule
    let originName: String
    let destName: String

    var body: some View {
        let its = pathTrips(boards: data.predictedBoards, schedule: schedule, option: option, now: data.now, maxN: 6)
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Departures from \(originName)").font(.subheadline.bold())
                Spacer()
                Text("\(option.legs[0].routesLabel) toward \(destName)").font(.caption).foregroundStyle(.secondary)
            }
            if its.isEmpty {
                Text(data.predictedBoards.isEmpty ? "Waiting for the live feeds…" : "No train for this path in the feed yet.").font(.caption).foregroundStyle(.secondary)
            }
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                let now = data.now
                VStack(spacing: 4) {
                    ForEach(Array(its.enumerated()), id: \.element.id) { i, it in
                        DepartureRow(itinerary: it, option: option, now: now, first: i == 0)
                    }
                }
            }
            Text("Countdown to boarding at your platform; the arrival is the engine's estimate with its 80% window.").font(.caption2).foregroundStyle(.secondary)
        }
    }
}

struct DepartureRow: View {
    let itinerary: Itinerary
    let option: PathOption
    let now: Double
    let first: Bool

    var body: some View {
        let l0 = itinerary.legs[0]
        let ln = itinerary.legs[itinerary.legs.count - 1]
        HStack(alignment: .center, spacing: 10) {
            Text(Fmt.mmss(max(0, itinerary.boardTs - now)))
                .font(.system(size: 26, weight: .bold, design: .rounded)).monospacedDigit()
                .frame(width: 78, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    RouteBullet(route: l0.train.route, size: 18)
                    Text(shortLabel(l0.train)).font(.caption.monospaced())
                    Text("boards \(Fmt.hhmm(l0.boardTs))").font(.caption)
                    if l0.train.position?.holding == true { Flag("held", Color.orange) }
                    if l0.train.position?.stalled == true { Flag("overdue", Color.red) }
                    if l0.train.corroboration == "feed_optimistic" { Flag("feed optimistic", Color.orange) }
                }
                Text(subline).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 1) {
                Text(Fmt.hhmm(itinerary.arriveTs)).font(.title3.bold())
                Text(ln.rangeText ?? "arrive").font(.caption2).foregroundStyle(.secondary).lineLimit(2).multilineTextAlignment(.trailing)
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 10).fill(first ? Color.accentColor.opacity(0.12) : Color(.secondarySystemBackground)))
    }

    private var subline: String {
        var bits: [String] = []
        if let pos = itinerary.legs[0].train.position { bits.append(pos.text) }
        if itinerary.legs.count > 1, let m = itinerary.connectionMarginSec, let tr = option.transfer {
            bits.append("change at \(tr.station): \(m < 120 ? "tight, " : "")\(Fmt.mmss(m)) margin")
        }
        if abs(itinerary.rideVsSchedSec) >= 60 { bits.append("\(Fmt.signed(itinerary.rideVsSchedSec / 60, unit: "min")) vs schedule") }
        return bits.joined(separator: " · ")
    }
}

// MARK: - hour-of-day profile

struct HoursView: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    let schedule: ClientSchedule

    private func profile(_ leg: PathLeg) -> [Double?] {
        guard let dev = data.deviations[leg.primaryKey], let line = schedule.lines[leg.primaryKey], let ix = leg.idx[leg.primaryKey] else { return Array(repeating: nil, count: 24) }
        return (0..<24).map { hour in
            var sum = 0.0, any = false
            var s = ix.from + 1
            while s <= ix.to {
                if s < line.stops.count, let v = dev.typical(stopId: line.stops[s], hour: hour) { sum += max(0, v); any = true }
                s += 1
            }
            return any ? sum : nil
        }
    }

    var body: some View {
        let hour = nyHour(data.now)
        let profiles = option.legs.map { profile($0) }
        let total: [Double?] = (0..<24).map { h in
            let vals = profiles.compactMap { $0[h] }
            return vals.isEmpty ? nil : vals.reduce(0, +)
        }
        let maxV = max(30, total.compactMap { $0 }.max() ?? 30)
        VStack(alignment: .leading, spacing: 8) {
            Text("Time trains typically lose on this path, by hour").font(.subheadline.bold())
            ForEach(Array(option.legs.enumerated()), id: \.offset) { i, leg in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 5) {
                        RouteBullets(routes: leg.routes, size: 16)
                        Text("\(leg.nStops) stops").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Text(profiles[i][hour].map { "now \(Fmt.signed($0))" } ?? "no history yet").font(.caption).foregroundStyle(.secondary)
                    }
                    HourStrip(values: profiles[i], maxV: maxV, current: hour, color: RouteStyle.color(leg.primaryRoute))
                }
            }
            HStack(spacing: 5) {
                Text("0").font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                Text("6").font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                Text("12").font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                Text("18").font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                Text("23").font(.system(size: 9)).foregroundStyle(.secondary)
            }
            Text(bestText(total, hour: hour)).font(.caption).foregroundStyle(.secondary)
            Text("Each cell: seconds trains lose across the stretch at that hour (from the per-line deviation grids); darker is worse, the outlined cell is now.").font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func bestText(_ total: [Double?], hour: Int) -> String {
        var best: (Int, Double)? = nil
        var worst: (Int, Double)? = nil
        for d in 0..<6 {
            let h = (hour + d) % 24
            guard let v = total[h] else { continue }
            if best == nil || v < best!.1 { best = (h, v) }
            if worst == nil || v > worst!.1 { worst = (h, v) }
        }
        guard let b = best, let w = worst else { return "No hourly history for these stretches yet." }
        if w.1 - b.1 < 30 { return "The next six hours look alike on these stretches (\(Fmt.signed(b.1)) to \(Fmt.signed(w.1)))." }
        return "In the next six hours, leaving around \(String(format: "%02d:00", b.0)) loses the least (\(Fmt.signed(b.1))); around \(String(format: "%02d:00", w.0)) the most (\(Fmt.signed(w.1)))."
    }
}

struct HourStrip: View {
    let values: [Double?]
    let maxV: Double
    let current: Int
    let color: Color

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<24, id: \.self) { h in
                let v = h < values.count ? values[h] : nil
                RoundedRectangle(cornerRadius: 2)
                    .fill(v.map { color.opacity(0.15 + 0.85 * min(1, $0 / maxV)) } ?? Color.secondary.opacity(0.12))
                    .overlay(RoundedRectangle(cornerRadius: 2).stroke(h == current ? Color.primary : Color.clear, lineWidth: 1.5))
                    .frame(height: 18)
            }
        }
    }
}
