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
    @Environment(LocationService.self) private var loc
    let option: PathOption?
    var originId: String = ""
    /// Where the route in progress stands; nil before it starts. Past the approach, no walk is shown.
    var phase: TripPhase? = nil
    /// A pinned place the phone is at, and how long the rider usually takes from there to the station.
    var placeName: String? = nil
    var placeUsualSec: Double? = nil
    /// While the route is under way: on the train, the ride in progress (the train the rider is on and the
    /// connection it makes); between trains at the change, the connection. Shown in place of the planner's next
    /// itinerary, which moved on to the next train when the rider's left.
    var ride: Itinerary? = nil
    var rideLeg = 0
    var onTrain = false
    var ridingRoute: String? = nil
    var ridePresumed = false
    let originName: String
    let destName: String
    /// Opens the route's insights.
    var onInsights: (() -> Void)? = nil

    /// The walk from the phone to the origin station, when the phone's position and the station's are known.
    private var walk: NearbyStation? {
        guard !originId.isEmpty, let l = loc.location, let sched = data.schedule, let index = data.index, let geo = data.geometry,
              let st = index.stations[originId] else { return nil }
        guard let c = stationCoordinates(schedule: sched, index: index, geometry: geo)[originId] else { return nil }
        return NearbyStation(station: st, meters: haversineM((l.coordinate.latitude, l.coordinate.longitude), c))
    }

    /// The itinerary after the one on the card, on the same route: the train to take if this one is missed. On the
    /// train, the connection after the one the ride makes.
    private var nextItinerary: Itinerary? { nextItineraryAfter(ride: ride, onTrain: onTrain, option: option, data: data) }

    // The card keeps one fixed row structure whichever route is chosen and whether or not a train is in the
    // feeds, so its height never changes and the list under it never jumps: every row reserves its space.
    // Rows read as steps, most significant first: board, the route (change or direct), arrive with its minor
    // figures; then where you are (the walk, while on the way) and where the train is.
    var body: some View {
        let w = walk
        let nx = nextItinerary
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            let now = data.now
            VStack(alignment: .leading, spacing: 10) {
                if let p = option {
                    let it = ride ?? p.live
                    headerRow(p, it, now: now)
                    changeRow(p, it)
                    arriveRow(p, it)
                    minorRow(p, it, nx, now: now)
                    walkRow(w, boardTs: it?.boardTs, now: now)
                    stopsRow(p, it)
                    insightsButton
                } else {
                    Text("Pick where you are and where you're going.").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                ZStack {
                    RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground))
                    RoundedRectangle(cornerRadius: 12).fill(Color.accentColor.opacity(0.14))
                }
            }
        }
        .onAppear {
            data.requestGeometry()
            loc.request()
        }
    }

    /// Invisible text in a row's font: the row keeps its height when it has nothing to say.
    private func ghost(_ text: String, _ font: Font) -> some View {
        Text(text).font(font).opacity(0).accessibilityHidden(true)
    }

    private let countdownFont = Font.system(size: 34, weight: .bold, design: .rounded)
    private let stepFont = Font.subheadline.weight(.semibold)
    private let detailFont = Font.caption

    /// Where the rider leaves the train they are on: the change ahead, or the destination.
    private func offAt(_ p: PathOption) -> String {
        p.legs.count > 1 && rideLeg == 0 ? (p.transfer?.station ?? "the change") : destName
    }

    /// Step 1, the most prominent: the train to take and the countdown to it. On the train: the line the rider is
    /// on, where they get off it and the countdown to that.
    @ViewBuilder private func headerRow(_ p: PathOption, _ it: Itinerary?, now: Double) -> some View {
        HStack(alignment: .center, spacing: 8) {
            if onTrain, let l0 = it?.legs.first {
                Text(ridePresumed ? "Presumably on the" : "On the").font(.subheadline).foregroundStyle(.secondary)
                RouteBullet(route: ridingRoute ?? l0.train.route, size: 26)
                Text("off \(Fmt.hhmm(l0.arriveTs))").font(.title2.bold()).lineLimit(1).minimumScaleFactor(0.8)
                Spacer()
                Text(Fmt.mmss(max(0, l0.arriveTs - now))).font(countdownFont).monospacedDigit()
            } else if onTrain {
                Text(ridePresumed ? "Presumably on the" : "On the").font(.subheadline).foregroundStyle(.secondary)
                RouteBullets(routes: Array((ridingRoute.map { [$0] } ?? p.legs[min(rideLeg, p.legs.count - 1)].routes).prefix(1)), size: 26)
                Text("to \(offAt(p))").font(.title2.bold()).lineLimit(1).minimumScaleFactor(0.8)
                Spacer()
                ghost("0:00", countdownFont)
            } else if let it = it, let l0 = it.legs.first {
                Text("Take the").font(.subheadline).foregroundStyle(.secondary)
                RouteBullet(route: l0.train.route, size: 26)
                Text("at \(Fmt.hhmm(it.boardTs))").font(.title2.bold()).lineLimit(1)
                Spacer()
                Text(Fmt.mmss(max(0, it.boardTs - now))).font(countdownFont).monospacedDigit()
            } else {
                Text("Take the").font(.subheadline).foregroundStyle(.secondary)
                RouteBullets(routes: Array(p.legs[min(rideLeg, p.legs.count - 1)].routes.prefix(1)), size: 26)
                Text("from \(rideLeg > 0 ? (p.transfer?.station ?? originName) : originName)").font(.title2.bold()).lineLimit(1)
                Spacer()
                ghost("0:00", countdownFont)
            }
        }
        // the countdown makes this row taller than its text: let the next row sit up a little in that space
        .padding(.bottom, -5)
    }

    /// On the train: how many stops are left before the rider gets off, from the feed's progress.
    private func stopsToGo(_ c: TripCandidate, option p: PathOption) -> (text: String, fraction: Double) {
        WhichWay.stopsToGo(c, option: p, leg: rideLeg, offAt: offAt(p))
    }

    /// Where that train is: stops from your platform on one line, the stop it is at on the next, a small track to
    /// the right. On the train, the stops left before getting off.
    @ViewBuilder private func stopsRow(_ p: PathOption, _ it: Itinerary?) -> some View {
        HStack(alignment: .center, spacing: 6) {
            if let it = it, let l0 = it.legs.first {
                let away = onTrain ? stopsToGo(l0, option: p) : stopsAway(l0, option: p, leg: rideLeg)
                Image(systemName: "tram.fill").font(.caption).foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(away.text).font(stepFont).lineLimit(1)
                    Text(l0.train.position?.text ?? "position unknown").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 6)
                MiniTrack(fraction: away.fraction, color: RouteStyle.color(l0.train.route)).frame(width: 84, height: 10)
            } else {
                Image(systemName: "tram").font(.caption).foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(data.predictedBoards.isEmpty ? "Waiting for the live feeds…" : "No train for this path yet").font(stepFont).foregroundStyle(.secondary).lineLimit(1)
                    Text("the expected time above stands in").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 6)
                ghost("0", stepFont)
            }
        }
    }

    /// The walk to the station in the rider's own pace and time to the platform where learned, else 80 m a
    /// minute; whether it fits in the countdown, and how far it gets them if not.
    private func walkLine(_ w: NearbyStation, boardTs: Double?, now: Double) -> (text: String, tight: Bool) {
        walkLineText(w, originId: originId, placeName: placeName, placeUsualSec: placeUsualSec, boardTs: boardTs, now: now)
    }

    /// The step before boarding while the rider is still on the way: the walk to the station.
    @ViewBuilder private func walkRow(_ w: NearbyStation?, boardTs: Double?, now: Double) -> some View {
        if let w = w, phase == nil || phase == .approaching {
            let line = walkLine(w, boardTs: boardTs, now: now)
            HStack(spacing: 6) {
                Image(systemName: line.tight ? "exclamationmark.triangle.fill" : "figure.walk").font(.subheadline)
                Text("Walk").font(stepFont)
                Text(line.text).font(detailFont.weight(line.tight ? .semibold : .regular)).lineLimit(1)
            }
            .foregroundStyle(line.tight ? Color.orange : Color.primary)
        }
    }

    /// Step 2: the change to make, or that the route is direct, with its details on their own line so nothing is cut off.
    /// On the train with a change ahead, the connection it makes; past the change, that it is made.
    @ViewBuilder private func changeRow(_ p: PathOption, _ it: Itinerary?) -> some View {
        let pastChange = p.legs.count > 1 && rideLeg > 0
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                if p.legs.count > 1, let tr = p.transfer {
                    Image(systemName: pastChange ? "checkmark" : "arrow.triangle.swap").font(.caption).foregroundStyle(.secondary)
                    Text(pastChange ? "Changed at \(tr.station)" : "Change at \(tr.station)").font(stepFont).lineLimit(1)
                    Text(pastChange ? "" : "to the \(p.legs[1].routesLabel)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                } else {
                    Image(systemName: "arrow.right").font(.caption).foregroundStyle(.secondary)
                    Text("Direct").font(stepFont)
                    Text("\(p.legs[0].nStops) stops").font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            Group {
                if p.legs.count > 1, let tr = p.transfer {
                    if pastChange {
                        Text(it.map { "on the \($0.legs[0].train.route) · \(Fmt.minTxt($0.legs[0].rideSec)) ride" } ?? "the last leg").foregroundStyle(.secondary)
                    } else if onTrain, let it = it, it.legs.count > 1 {
                        // the connection the train in hand makes
                        let b = it.legs[1]
                        let m = it.connectionMarginSec ?? 0
                        Text("\(b.train.route) at \(Fmt.hhmm(b.boardTs)) · \(Fmt.mmss(m)) margin" + (tr.walkSec > 0 ? " · \(Fmt.mmss(Double(tr.walkSec))) walk" : " · same platform")
                             + (it.nextIfMissedSec.map { " · +\(Fmt.mmss($0)) if missed" } ?? ""))
                            .foregroundStyle(m < 60 ? Color.red : Color.secondary)
                    } else if onTrain {
                        Text("connection not in the feeds yet · " + (tr.walkSec > 0 ? "\(Fmt.mmss(Double(tr.walkSec))) walk between platforms" : "same platform")).foregroundStyle(.secondary)
                    } else if let it = it, let m = it.connectionMarginSec {
                        Text("\(Fmt.mmss(m)) margin" + (tr.walkSec > 0 ? " · \(Fmt.mmss(Double(tr.walkSec))) walk" : " · same platform")
                             + (it.nextIfMissedSec.map { " · +\(Fmt.mmss($0)) if missed" } ?? ""))
                            .foregroundStyle(m < 60 ? Color.red : Color.secondary)
                    } else {
                        Text(tr.walkSec > 0 ? "\(Fmt.mmss(Double(tr.walkSec))) walk between platforms" : "same platform").foregroundStyle(.secondary)
                    }
                } else {
                    Text("\(Fmt.minTxt(p.legs[0].schedRideSec.map(Double.init))) scheduled ride" + (p.legs[0].typicalSec.map { abs($0) >= 30 ? " · typically \(Fmt.signed($0))" : "" } ?? ""))
                        .foregroundStyle(.secondary)
                }
            }
            .font(.caption).lineLimit(1).padding(.leading, 20)
        }
    }

    /// Step 3: when you get there, with the standing against the timetable.
    @ViewBuilder private func arriveRow(_ p: PathOption, _ it: Itinerary?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            if let it = it {
                Text("Arrive \(destName) \(Fmt.hhmm(it.arriveTs))").font(.headline).lineLimit(1).minimumScaleFactor(0.85)
                Spacer()
                Text(Fmt.minTxt(it.totalSec)).font(.subheadline).foregroundStyle(.secondary)
                let h = routeHealth(p, data: data)
                if h.extraSec >= 90 { Text(h.label).font(.subheadline.weight(.semibold)).foregroundStyle(h.textColor) }
            } else {
                Text("Expected \(Fmt.minTxt(p.expectedSec)) to \(destName)").font(.headline).lineLimit(1).minimumScaleFactor(0.85)
                Spacer()
                Text("door to door").font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }

    private func minorText(_ it: Itinerary) -> String {
        var bits: [String] = []
        if let lo = it.legs.last?.arriveLoTs, let hi = it.legs.last?.arriveHiTs { bits.append("80% window \(Fmt.hhmm(lo))–\(Fmt.hhmm(hi))") }
        if let f = it.legs.last?.feedArriveTs, abs(f - it.arriveTs) >= 60 { bits.append("feed says \(Fmt.hhmm(f))") }
        return bits.isEmpty ? "engine estimate" : bits.joined(separator: " · ")
    }

    /// The minor figures, small: the engine's window and the raw feed time (two lines reserved, so they never cut
    /// off), and the train after this one on the right.
    @ViewBuilder private func minorRow(_ p: PathOption, _ it: Itinerary?, _ nx: Itinerary?, now: Double) -> some View {
        HStack(alignment: .top, spacing: 8) {
            if let it = it {
                Text(minorText(it).replacingOccurrences(of: " · ", with: "\n")).font(.caption2).foregroundStyle(.tertiary).lineLimit(2, reservesSpace: true)
                Spacer(minLength: 6)
                if let nx = nx, let n0 = nx.legs.first {
                    let mins = Int(max(0, nx.boardTs - now) / 60)
                    HStack(spacing: 4) {
                        if n0.train.route != it.legs[0].train.route { RouteBullet(route: n0.train.route, size: 12) }
                        Text("Next \(Fmt.hhmm(nx.boardTs)) · \(mins < 1 ? "<1" : "\(mins)") min").font(.caption2).foregroundStyle(.tertiary).monospacedDigit().lineLimit(1)
                    }
                } else {
                    ghost("Next 0:00 · 0 min", .caption2)
                }
            } else {
                Text("\(Fmt.minTxt(Double(p.schedSec))) scheduled\nexpected \(Fmt.minTxt(p.expectedSec)) with the waits and typical losses").font(.caption2).foregroundStyle(.tertiary).lineLimit(2, reservesSpace: true)
                Spacer(minLength: 6)
            }
        }
    }

    @ViewBuilder private var insightsButton: some View {
        if let open = onInsights {
            Button(action: open) {
                Label("Insights", systemImage: "chart.bar.doc.horizontal")
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Capsule().fill(Color.accentColor.opacity(0.18)))
            }
            .buttonStyle(.plain)
            .padding(.top, 2)
        }
    }

}

/// A held train ahead changes when the headline route arrives: the arrival under each assumption about the
/// hold, one tap to plan for it. Shown only when the assumptions differ by a minute or more.
struct HoldOutlook: Equatable {
    var heldAt: String          // "F train at Church Av"
    var usual: Double?
    var dragsOn: Double?
    var clearsNow: Double?
}

struct HoldOutlookCard: View {
    @Environment(DataService.self) private var data
    let outlook: HoldOutlook

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(outlook.heldAt) · if the hold…").font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                choice("Ends as usual", "baseline", outlook.usual)
                choice("Drags on", "hold_persists", outlook.dragsOn)
                choice("Clears now", "clears_now", outlook.clearsNow)
            }
        }
    }

    private func choice(_ title: String, _ tag: String, _ arrive: Double?) -> some View {
        let on = data.scenario == tag
        return Button { data.setScenario(tag) } label: {
            VStack(spacing: 2) {
                Text(title).font(.caption.weight(.semibold)).foregroundStyle(.primary)
                Text(arrive.map { Fmt.hhmm($0) } ?? "–").font(.caption2).foregroundStyle(on ? Color.primary : Color.primary.opacity(0.7))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 9).fill(on ? Color.accentColor.opacity(0.18) : Color(.secondarySystemBackground)))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(on ? Color.accentColor : Color.primary.opacity(0.08), lineWidth: 1))
        }
        .buttonStyle(.plain)
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
        HStack(alignment: .center, spacing: 8) {
            Text(Fmt.mmss(max(0, itinerary.boardTs - now)))
                .font(.system(size: 24, weight: .bold, design: .rounded)).monospacedDigit()
                .lineLimit(1).minimumScaleFactor(0.7)
                .frame(width: 66, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 5) {
                    RouteBullet(route: l0.train.route, size: 16)
                    Text("boards \(Fmt.hhmm(l0.boardTs))").font(.caption)
                    Text(stopsAway(l0, option: option).text).font(.caption).foregroundStyle(.secondary)
                }
                .lineLimit(1)
                HStack(spacing: 4) {
                    if l0.train.position?.holding == true { Flag("held", Color.orange) }
                    if l0.train.position?.stalled == true { Flag("overdue", Color.red) }
                    if l0.train.corroboration == "feed_optimistic" { Flag("optimistic", Color.orange) }
                    if let s = subline { Text(s).font(.caption2).foregroundStyle(.secondary).lineLimit(1) }
                }
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 1) {
                Text(Fmt.hhmm(itinerary.arriveTs)).font(.title3.bold()).lineLimit(1)
                if let lo = ln.arriveLoTs, let hi = ln.arriveHiTs {
                    Text("\(Fmt.hhmm(lo))–\(Fmt.hhmm(hi))").font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .layoutPriority(1)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 10).fill(first ? Color.accentColor.opacity(0.12) : Color(.secondarySystemBackground)))
    }


    /// The change and the ride against the schedule, when there is something to say.
    private var subline: String? {
        var bits: [String] = []
        if itinerary.legs.count > 1, let m = itinerary.connectionMarginSec, let tr = option.transfer {
            bits.append("change at \(tr.station): \(m < 120 ? "tight, " : "")\(Fmt.mmss(m)) margin")
        }
        if abs(itinerary.rideVsSchedSec) >= 60 { bits.append("\(Fmt.signed(itinerary.rideVsSchedSec / 60, unit: "min")) vs schedule") }
        return bits.isEmpty ? nil : bits.joined(separator: " · ")
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

/// How far a train is from the rider's platform: the words, and a fraction along a ten-stop approach for a
/// small track (0 = ten or more stops out, 1 = at the platform).
/// The itinerary after `ride` or the option's next one, on the same route: the train to take if this one is
/// missed. On the train, the connection after the one the ride makes. Shared by the Now card and the home card.
@MainActor
func nextItineraryAfter(ride: Itinerary?, onTrain: Bool, option: PathOption?, data: DataService) -> Itinerary? {
    if let r = ride {
        guard !onTrain || r.legs.count > 1, let s = r.nextIfMissedSec else { return nil }
        var n = r
        n.boardTs = (onTrain ? r.legs[1].boardTs : r.boardTs) + s
        n.legs = onTrain ? Array(r.legs.dropFirst()) : r.legs
        return n
    }
    guard let p = option, let it = p.live, let sched = data.schedule else { return nil }
    return pathTrips(boards: data.predictedBoards, schedule: sched, option: p, now: data.now, maxN: 3).first { $0.boardTs > it.boardTs + 30 }
}

/// The walk to the station in the rider's own pace and time to the platform where learned, else 80 m a minute;
/// whether it fits in the countdown, and how far it gets them if not.
@MainActor
func walkLineText(_ w: NearbyStation, originId: String, placeName: String?, placeUsualSec: Double?, boardTs: Double?, now: Double) -> (text: String, tight: Bool) {
    let pm = PersonalModelStore.shared.model
    let access = pm.accessSec(station: originId) ?? 0
    let walkSec = placeUsualSec ?? (w.meters / pm.walkSpeedMPerMin * 60 + access)
    let mins = max(1, Int((walkSec / 60).rounded()))
    let left = boardTs.map { max(0, $0 - now) }
    let tight = left.map { walkSec > $0 } ?? false
    let reach = pm.walkSpeedMPerMin * max(0, (left ?? 0) - access) / 60
    var text = placeName.map { "\($0): " } ?? ""
    text += placeUsualSec != nil ? "usually \(mins) min" : "\(Fmt.miles(w.meters)) - \(mins) min"
    if placeUsualSec == nil, access >= 30 { text += " · \(Int((access / 60).rounded())) min to platform" }
    if tight { text += " · \(Fmt.miles(reach))" }
    return (text, tight)
}

/// On the train: how many stops are left before the rider gets off at `offAt`, from the feed's progress.
func stopsToGo(_ c: TripCandidate, option p: PathOption, leg: Int, offAt: String) -> (text: String, fraction: Double) {
    guard p.legs.indices.contains(leg), let ix = p.legs[leg].idx[c.key] else { return ("", 0) }
    let total = max(1, ix.to - ix.from)
    if let pos = c.train.position, !pos.derived, pos.status == "STOPPED_AT", pos.stopIdx == ix.to { return ("At \(offAt)", 1) }
    let n = ix.to - c.train.nextIdx + 1
    if n <= 0 { return ("Arriving at \(offAt)", 0.95) }
    return ("\(min(n, total)) stop\(n == 1 ? "" : "s") to go", max(0, 1 - Double(min(n, total)) / Double(total)))
}

func stopsAway(_ c: TripCandidate, option: PathOption, leg: Int = 0) -> (text: String, fraction: Double) {
    guard option.legs.indices.contains(leg), let from = option.legs[leg].idx[c.key]?.from else { return ("", 0) }
    if let p = c.train.position, !p.derived, p.status == "STOPPED_AT", p.stopIdx == from { return ("At the platform", 1) }
    let n = from - c.train.nextIdx
    if n <= 0 { return ("Arriving", 0.95) }
    return ("\(n) stop\(n == 1 ? "" : "s") away", max(0, 1 - Double(min(n, 10)) / 10))
}

/// A thin track with the train's marker along it and the platform at the right end.
struct MiniTrack: View {
    let fraction: Double
    let color: Color

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.15)).frame(height: 3).frame(maxHeight: .infinity, alignment: .center)
                Circle().fill(Color.primary.opacity(0.35)).frame(width: 5, height: 5).offset(x: w - 5).frame(maxHeight: .infinity, alignment: .center)
                RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 12, height: 7)
                    .offset(x: max(0, min(w - 12, w * fraction - 6))).frame(maxHeight: .infinity, alignment: .center)
            }
        }
        .accessibilityHidden(true)
    }
}
