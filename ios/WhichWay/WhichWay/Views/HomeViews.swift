import SwiftUI

/// The Go tab's home (the Oct 10 review's concept C): the countdown, the route's lines, when you arrive, the change
/// and the next best ways, as numbers. The line view is one swipe to the right, the other routes two.
struct HomeCard: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    /// The ride in progress (on the train: the train the rider is on and its connection), else the planner's next.
    let itinerary: Itinerary?
    /// The train after this one on the same route.
    let next: Itinerary?
    let now: Double
    let originName: String
    let destName: String
    var phase: TripPhase? = nil
    var onTrain = false
    var rideLeg = 0
    var ridingRoute: String? = nil
    var ridePresumed = false
    /// The walk to the station while on the way: the text and whether it fits in the countdown.
    var walk: (text: String, tight: Bool)? = nil
    /// The other routes, best first, and how many more there are beyond them.
    let alternatives: [PathOption]
    let moreCount: Int
    var onPick: (PathOption) -> Void
    var onMore: () -> Void
    /// The glow on the time to the train: blue with time to reach it, yellow as it nears, red as it leaves.
    var urgency: Color = .accentColor
    /// The glow on the arrival: blue on time, amber running late, red well behind.
    var delay: Color = .accentColor

    private let countdownFont = Font.system(size: 112, weight: .heavy)

    /// Where the rider leaves the train they are on: the change ahead, or the destination.
    private var offAt: String { option.legs.count > 1 && rideLeg == 0 ? (option.transfer?.station ?? "the change") : destName }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            countdown
            indicator.padding(.top, 14)
            boardsLine.padding(.top, 8)
            if let w = walk, phase == nil || phase == .approaching {
                Label(w.text, systemImage: w.tight ? "exclamationmark.triangle.fill" : "figure.walk")
                    .font(.subheadline.weight(w.tight ? .semibold : .regular))
                    .foregroundStyle(w.tight ? Color.orange : Color.secondary)
                    .lineLimit(1).padding(.top, 6)
            }
            JourneyBar(option: option).padding(.top, 14).shadow(color: delay.opacity(0.2), radius: 8)
            GlowRule(color: urgency).padding(.vertical, 22)
            arrive
            change.padding(.top, 20)
            GlowRule(color: delay).padding(.vertical, 22)
            others
        }
    }

    // MARK: the countdown, the lines, the train

    @ViewBuilder private var countdown: some View {
        if onTrain, let l0 = itinerary?.legs.first {
            VStack(alignment: .leading, spacing: 2) {
                Text(Fmt.mmss(max(0, l0.arriveTs - now))).font(countdownFont).tracking(-4).monospacedDigit().lineLimit(1).minimumScaleFactor(0.5)
                    .shadow(color: urgency.opacity(0.26), radius: 17)
                Text("\(ridePresumed ? "presumably on the" : "on the") \(ridingRoute ?? l0.train.route) · off at \(offAt) \(Fmt.hhmm(l0.arriveTs))")
                    .font(.title3.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.8)
            }
        } else if let it = itinerary {
            Text(Fmt.mmss(max(0, it.boardTs - now))).font(countdownFont).tracking(-4).monospacedDigit().lineLimit(1).minimumScaleFactor(0.5)
                .shadow(color: urgency.opacity(0.26), radius: 17)
        } else {
            VStack(alignment: .leading, spacing: 2) {
                Text(Fmt.minTxt(option.expectedSec)).font(.system(size: 72, weight: .heavy)).tracking(-2).lineLimit(1).minimumScaleFactor(0.5)
                    .shadow(color: urgency.opacity(0.26), radius: 17)
                Text("expected door to door").font(.title3.weight(.semibold)).foregroundStyle(.secondary)
            }
        }
    }

    /// The route's lines, the train's own where one is in the feeds: 2 → A C, change at Fulton St.
    private var indicator: some View {
        let legs = option.legs
        let first: TripCandidate? = rideLeg == 0 ? itinerary?.legs.first : nil
        let second: TripCandidate? = legs.count > 1 ? (rideLeg == 0 ? itinerary?.legs.dropFirst().first : itinerary?.legs.first) : nil
        let r0: [String] = first.map { [onTrain ? (ridingRoute ?? $0.train.route) : $0.train.route] } ?? legs[0].routes
        let r1: [String]? = legs.count > 1 ? (second.map { [$0.train.route] } ?? legs[1].routes) : nil
        return HStack(spacing: 10) {
            RouteBullets(routes: r0, size: 32).opacity(rideLeg > 0 ? 0.45 : 1)
            if let r1, let tr = option.transfer {
                Image(systemName: "arrow.right").font(.body.weight(.semibold)).foregroundStyle(.secondary)
                RouteBullets(routes: r1, size: 32)
                (Text(rideLeg > 0 ? "changed at " : "change at ").foregroundStyle(.secondary) + Text(tr.station).fontWeight(.semibold))
                    .font(.body).lineLimit(1).minimumScaleFactor(0.8)
            } else {
                Text("direct · \(legs[0].nStops) stops").font(.body).foregroundStyle(.secondary)
            }
        }
    }

    /// When it boards and where the train is now; on the train, the stops left.
    @ViewBuilder private var boardsLine: some View {
        if let it = itinerary, let l0 = it.legs.first {
            let whereNow = onTrain ? stopsToGo(l0, option: option, leg: rideLeg, offAt: offAt) : stopsAway(l0, option: option, leg: rideLeg)
            let pos = (l0.train.position?.text).map { " · \($0)" } ?? ""
            if onTrain {
                Text(whereNow.text + pos).font(.body).foregroundStyle(.secondary).lineLimit(1)
            } else {
                (Text("boards ").foregroundStyle(.secondary) + Text(Fmt.hhmm(it.boardTs)).fontWeight(.semibold) + Text(" · \(whereNow.text)\(pos)").foregroundStyle(.secondary))
                    .font(.body).lineLimit(2)
            }
        } else {
            Text(data.predictedBoards.isEmpty ? "waiting for the live feeds" : "no train for this route in the feeds yet").font(.body).foregroundStyle(.secondary)
        }
    }

    // MARK: arrive and change

    @ViewBuilder private var arrive: some View {
        if let it = itinerary {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(Fmt.hhmm(it.arriveTs)).font(.system(size: 46, weight: .bold)).tracking(-1.5).monospacedDigit()
                        .shadow(color: delay.opacity(0.2), radius: 10)
                    Text("arrive · \(Fmt.minTxt(it.totalSec))").font(.subheadline).foregroundStyle(.secondary)
                    let h = routeHealth(option, data: data)
                    if h.extraSec >= 90 { Text(h.label).font(.subheadline.weight(.semibold)).foregroundStyle(h.textColor) }
                }
                Text(minor(it)).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(Fmt.minTxt(option.expectedSec)).font(.system(size: 46, weight: .bold)).tracking(-1.5)
                    Text("expected to \(destName)").font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                }
                Text("\(Fmt.minTxt(Double(option.schedSec))) scheduled · with the waits and typical losses").font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }

    private func minor(_ it: Itinerary) -> String {
        var bits: [String] = []
        if let lo = it.legs.last?.arriveLoTs, let hi = it.legs.last?.arriveHiTs { bits.append("likely \(Fmt.hhmm(lo)) to \(Fmt.hhmm(hi))") }
        if let f = it.legs.last?.feedArriveTs, abs(f - it.arriveTs) >= 60 { bits.append("the feed says \(Fmt.hhmm(f))") }
        return bits.isEmpty ? "engine estimate" : bits.joined(separator: " · ")
    }

    @ViewBuilder private var change: some View {
        let legs = option.legs
        if legs.count > 1, let tr = option.transfer {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(tr.station).font(.title2.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.8)
                    if rideLeg > 0 {
                        Text("changed").font(.subheadline).foregroundStyle(.secondary)
                    } else if let it = itinerary, it.legs.count > 1 {
                        Text("off \(Fmt.hhmm(it.legs[0].arriveTs)) · on \(Fmt.hhmm(it.legs[1].boardTs))").font(.subheadline).foregroundStyle(.secondary)
                    } else {
                        Text("change").font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                Text(changeDetail(tr)).font(.subheadline).foregroundStyle(changeTight ? Color.red : Color.secondary).lineLimit(1)
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("Direct").font(.title2.weight(.semibold))
                    Text("\(legs[0].nStops) stops").font(.subheadline).foregroundStyle(.secondary)
                }
                Text("\(Fmt.minTxt(legs[0].schedRideSec.map(Double.init))) scheduled ride" + (legs[0].typicalSec.map { abs($0) >= 30 ? " · typically \(Fmt.signed($0))" : "" } ?? ""))
                    .font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }

    private var changeTight: Bool { rideLeg == 0 && (itinerary?.connectionMarginSec ?? 999) < 60 }

    private func changeDetail(_ tr: PathTransfer) -> String {
        let walk = tr.walkSec > 0 ? "\(Fmt.mmss(Double(tr.walkSec))) walk" : "same platform"
        if rideLeg > 0, let l = itinerary?.legs.first { return "on the \(l.train.route) · \(Fmt.minTxt(l.rideSec)) ride" }
        if let it = itinerary, it.legs.count > 1, let m = it.connectionMarginSec {
            return "\(Fmt.mmss(m)) margin · \(walk)" + (it.nextIfMissedSec.map { " · +\(Fmt.mmss($0)) if missed" } ?? "")
        }
        if onTrain { return "connection not in the feeds yet · \(walk)" }
        return walk + " between platforms"
    }

    // MARK: the other ways

    private var others: some View {
        VStack(alignment: .leading, spacing: 12) {
            if alternatives.isEmpty {
                Text("the only route with at most one change").font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(alternatives, id: \.id) { p in
                Button { onPick(p) } label: {
                    HStack(spacing: 4) {
                        RouteBullets(routes: p.legs[0].routes, size: 18)
                        if p.legs.count > 1 {
                            Image(systemName: "arrow.right").font(.caption2).foregroundStyle(.secondary).padding(.horizontal, 2)
                            RouteBullets(routes: p.legs[1].routes, size: 18)
                        }
                        Text(p.transfer.map { "at \($0.station)" } ?? "direct").font(.body).foregroundStyle(.secondary).lineLimit(1).padding(.leading, 6)
                        Spacer(minLength: 8)
                        let h = routeHealth(p, data: data)
                        Text(Fmt.minTxt(p.live?.totalSec ?? p.expectedSec)).font(.body.weight(.semibold)).monospacedDigit()
                            .foregroundStyle(h.level == .smooth ? Color.primary : h.textColor)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Take \(p.label), \(Fmt.minTxt(p.live?.totalSec ?? p.expectedSec))")
            }
            if !alternatives.isEmpty {
                Button(action: onMore) {
                    HStack(spacing: 4) {
                        Text(moreCount > 0 ? "\(moreCount) more way\(moreCount == 1 ? "" : "s")" : "all the ways").font(.subheadline)
                        Image(systemName: "chevron.right").font(.caption2)
                    }
                    .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
        }
    }
}

/// The route's door-to-door time as a strip: wait (grey), ride (line colour), walk at the change (dark), wait, ride.
struct JourneyBar: View {
    let option: PathOption
    var height: CGFloat = 14

    var body: some View {
        let segs = journeySegments(option)
        let total = max(1, segs.reduce(0) { $0 + $1.sec })
        GeometryReader { geo in
            let gaps = CGFloat(max(0, segs.count - 1)) * 3
            HStack(spacing: 3) {
                ForEach(Array(segs.enumerated()), id: \.offset) { _, s in
                    Rectangle().fill(s.color).frame(width: max(2, (geo.size.width - gaps) * CGFloat(s.sec / total)))
                }
            }
        }
        .frame(height: height)
        .clipShape(Capsule())
        .accessibilityHidden(true)
    }
}

/// The route as a thread down the screen (concept D): the train coming, the origin with the countdown, the change as
/// a switch of colour with the walk dotted between, the destination with its likely window; the other routes as faded
/// threads to tap; the five detail views and the insights behind two buttons.
struct StrandView: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    let itinerary: Itinerary?
    let next: Itinerary?
    let now: Double
    let originName: String
    let destName: String
    var onTrain = false
    var rideLeg = 0
    var ridingRoute: String? = nil
    var ridePresumed = false
    let alternatives: [PathOption]
    var onPick: (PathOption) -> Void
    var onDetails: () -> Void
    var onInsights: () -> Void
    /// The glows, as on the home card: the time to the train on the origin, the route's delay on the destination.
    var urgency: Color = .accentColor
    var delay: Color = .accentColor

    private var leg0Train: TripCandidate? { rideLeg == 0 ? itinerary?.legs.first : nil }
    private var leg1Train: TripCandidate? { option.legs.count > 1 ? (rideLeg == 0 ? itinerary?.legs.dropFirst().first : itinerary?.legs.first) : nil }
    private var route0: String { leg0Train.map { onTrain ? (ridingRoute ?? $0.train.route) : $0.train.route } ?? option.legs[0].primaryRoute }
    private var route1: String? { option.legs.count > 1 ? (leg1Train?.train.route ?? option.legs[1].primaryRoute) : nil }
    private var c0: Color { RouteStyle.color(route0) }
    private var c1: Color? { route1.map { RouteStyle.color($0) } }
    private var offAt: String { option.legs.count > 1 && rideLeg == 0 ? (option.transfer?.station ?? "the change") : destName }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            approach
            origin
            if option.legs.count > 1 {
                changeOff
                changeOn
            }
            destination
            if !alternatives.isEmpty { others.padding(.top, 24) }
            HStack(spacing: 10) {
                Button(action: onDetails) { Label("Track · Time · Board · Map · Hours", systemImage: "list.bullet.rectangle").lineLimit(1) }
                Button(action: onInsights) { Label("Insights", systemImage: "chart.bar.doc.horizontal").lineLimit(1) }
                Spacer(minLength: 0)
            }
            .font(.caption.weight(.semibold)).buttonStyle(.bordered).controlSize(.small)
            .padding(.top, 24)
        }
    }

    /// One row of the thread: the node (a ring, a filled dot at the end, or a small dot for the train coming), the
    /// line down to the next row, and the text beside it.
    private func row<Content: View>(node: Color?, filled: Bool = false, small: Bool = false, line: Color?, dotted: Bool = false,
                                    glow: Color? = nil, minHeight: CGFloat, @ViewBuilder content: () -> Content) -> some View {
        HStack(alignment: .top, spacing: 14) {
            ZStack(alignment: .top) {
                if let line {
                    GeometryReader { g in
                        Path { p in
                            p.move(to: CGPoint(x: g.size.width / 2, y: node == nil ? 0 : 10))
                            p.addLine(to: CGPoint(x: g.size.width / 2, y: g.size.height))
                        }
                        .stroke(line, style: StrokeStyle(lineWidth: dotted ? 2 : 6, lineCap: .round, dash: dotted ? [3, 6] : []))
                    }
                }
                if let node {
                    if small {
                        Circle().fill(node).frame(width: 10, height: 10).padding(.top, 5)
                    } else if filled {
                        Circle().fill(node).frame(width: 20, height: 20).shadow(color: (glow ?? .clear).opacity(0.6), radius: 9)
                    } else {
                        Circle().strokeBorder(node, lineWidth: 5).background(Circle().fill(Color(.systemBackground))).frame(width: 20, height: 20)
                            .shadow(color: (glow ?? .clear).opacity(0.35), radius: 7)
                    }
                }
            }
            .frame(width: 36)
            content().frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(minHeight: minHeight, alignment: .top)
    }

    @ViewBuilder private var approach: some View {
        if let l0 = itinerary?.legs.first {
            let whereNow = onTrain ? stopsToGo(l0, option: option, leg: rideLeg, offAt: offAt) : stopsAway(l0, option: option, leg: rideLeg)
            let pos = (l0.train.position?.text).map { " · \($0)" } ?? ""
            row(node: c0.opacity(0.8), small: true, line: c0.opacity(0.6), dotted: true, minHeight: 44) {
                Text("\(onTrain ? "on the" : "the") \(l0.train.route) · \(whereNow.text)\(pos)").font(.caption).foregroundStyle(.secondary).lineLimit(1).padding(.top, 2)
            }
        } else {
            row(node: nil, line: nil, minHeight: 24) {
                Text(data.predictedBoards.isEmpty ? "waiting for the live feeds" : "no train for this route in the feeds yet").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var origin: some View {
        row(node: c0, line: c0, glow: urgency, minHeight: 116) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(originName).font(.headline).lineLimit(1)
                    if rideLeg > 0 {
                        Text("ridden").font(.caption).foregroundStyle(.secondary)
                    } else if let it = itinerary {
                        Text(onTrain ? "left \(Fmt.hhmm(it.boardTs))" : "board \(Fmt.hhmm(it.boardTs))").font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack(spacing: 10) {
                    if rideLeg == 0, let it = itinerary, let l0 = it.legs.first {
                        Text(Fmt.mmss(max(0, (onTrain ? l0.arriveTs : it.boardTs) - now)))
                            .font(.system(size: 54, weight: .heavy)).tracking(-2).monospacedDigit().lineLimit(1).minimumScaleFactor(0.6)
                            .shadow(color: urgency.opacity(0.3), radius: 14)
                        RouteBullet(route: route0, size: 28)
                    } else {
                        RouteBullets(routes: rideLeg > 0 ? [route0] : option.legs[0].routes, size: 28).opacity(rideLeg > 0 ? 0.45 : 1)
                        if rideLeg == 0 { Text("no train yet").font(.title3).foregroundStyle(.secondary) }
                    }
                }
                if onTrain, rideLeg == 0 {
                    Text("off at \(offAt)").font(.caption).foregroundStyle(.secondary)
                } else if rideLeg == 0, let nx = next, let n0 = nx.legs.first {
                    Text("then \(n0.train.route) \(Fmt.hhmm(nx.boardTs))" + (itinerary?.nextIfMissedSec.map { " · +\(Fmt.mmss($0)) if missed" } ?? ""))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .padding(.top, -2)
        }
    }

    private var changeOff: some View {
        let tr = option.transfer
        let walk = (tr?.walkSec ?? 0) > 0 ? "\(Fmt.mmss(Double(tr!.walkSec))) walk" : "same platform"
        let margin = rideLeg == 0 ? itinerary?.connectionMarginSec : nil
        let tight = (margin ?? 999) < 60
        return row(node: c0, line: Color.secondary.opacity(0.8), dotted: true, minHeight: 58) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(tr?.station ?? "the change").font(.headline).lineLimit(1)
                    if rideLeg > 0 {
                        Text("changed").font(.caption).foregroundStyle(.secondary)
                    } else if let l0 = itinerary?.legs.first {
                        Text("off \(Fmt.hhmm(l0.arriveTs))").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text(walk + (margin.map { " · \(Fmt.mmss($0)) margin" } ?? "")).font(.caption).foregroundStyle(tight ? Color.red : Color.secondary)
            }
            .padding(.top, -2)
        }
    }

    private var changeOn: some View {
        let l1 = leg1Train
        let riding = onTrain && rideLeg > 0
        return row(node: c1 ?? c0, line: c1 ?? c0, minHeight: riding ? 100 : 58) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                    RouteBullets(routes: l1.map { [$0.train.route] } ?? option.legs[1].routes, size: 22)
                    if riding, let l1 {
                        Text(Fmt.mmss(max(0, l1.arriveTs - now))).font(.system(size: 40, weight: .heavy)).tracking(-1).monospacedDigit()
                    } else if let l1 {
                        Text("at \(Fmt.hhmm(l1.boardTs))").font(.subheadline.weight(.semibold))
                    } else {
                        Text("connection not in the feeds yet").font(.caption).foregroundStyle(.secondary)
                    }
                }
                if riding {
                    Text("off at \(destName)").font(.caption).foregroundStyle(.secondary)
                } else if let l1 {
                    Text("\(Fmt.minTxt(l1.rideSec)) ride").font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(.top, -2)
        }
    }

    private var destination: some View {
        row(node: c1 ?? c0, filled: true, line: nil, glow: delay, minHeight: 60) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    if let it = itinerary {
                        Text(Fmt.hhmm(it.arriveTs)).font(.system(size: 32, weight: .bold)).monospacedDigit()
                            .shadow(color: delay.opacity(0.25), radius: 9)
                    } else {
                        Text(Fmt.minTxt(option.expectedSec)).font(.system(size: 32, weight: .bold))
                    }
                    Text(destName).font(.headline).lineLimit(1).minimumScaleFactor(0.8)
                }
                Text(arrivalNote).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            .padding(.top, -6)
        }
    }

    private var arrivalNote: String {
        guard let it = itinerary else { return "expected · \(Fmt.minTxt(option.expectedSec)) door to door" }
        var bits: [String] = []
        if let lo = it.legs.last?.arriveLoTs, let hi = it.legs.last?.arriveHiTs { bits.append("likely \(Fmt.hhmm(lo)) to \(Fmt.hhmm(hi))") }
        bits.append("\(Fmt.minTxt(it.totalSec)) door to door")
        return bits.joined(separator: " · ")
    }

    private var others: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Other ways").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 14) {
                ForEach(alternatives.prefix(4), id: \.id) { p in
                    Button { onPick(p) } label: {
                        MiniStrand(option: p, total: Fmt.minTxt(p.live?.totalSec ?? p.expectedSec))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Take \(p.label), \(Fmt.minTxt(p.live?.totalSec ?? p.expectedSec))")
                }
            }
        }
    }
}

// MARK: - the whisper glow

/// The glow's colours beyond the accent: yellow and red for a train about to leave, amber for a route running late.
enum GlowColor {
    static let yellow = Color(red: 1.0, green: 0.84, blue: 0.04)
    static let amber = Color(red: 1.0, green: 0.70, blue: 0.25)
    static let red = Color(red: 1.0, green: 0.27, blue: 0.23)
}

/// The colour of the glow on the time to the train: blue while there is time to reach it, yellow as it nears, red
/// when it is about to leave. With a walk still to make, the slack after the walk counts (three minutes of slack is
/// comfortable, under a minute is not); at the platform, or on the train counting down to the stop to get off at,
/// the time itself (a minute and a half, half a minute).
func boardingUrgency(secondsLeft: Double, walkSec: Double?) -> Color {
    if let w = walkSec {
        let slack = secondsLeft - w
        return slack >= 180 ? .accentColor : (slack >= 60 ? GlowColor.yellow : GlowColor.red)
    }
    return secondsLeft >= 90 ? .accentColor : (secondsLeft >= 30 ? GlowColor.yellow : GlowColor.red)
}

/// The colour of the glow on the arrival: blue on time, amber running five to nine minutes late, red beyond.
func delayGlow(_ h: RouteHealth) -> Color {
    switch h.level {
    case .smooth: return .accentColor
    case .minor: return GlowColor.amber
    case .heavy: return GlowColor.red
    }
}

/// A hairline that fades from the glow colour to nothing, with a faint halo.
struct GlowRule: View {
    let color: Color
    var body: some View {
        Rectangle()
            .fill(LinearGradient(colors: [color.opacity(0.5), color.opacity(0.04)], startPoint: .leading, endPoint: .trailing))
            .frame(height: 1)
            .shadow(color: color.opacity(0.22), radius: 5)
    }
}

/// A soft wash of the glow colour from a page's top-left corner, barely there: centred on the corner itself, so
/// a quarter of it falls across the page and fades out before the numbers.
struct GlowWash: View {
    let color: Color
    var body: some View {
        Circle()
            .fill(RadialGradient(colors: [color.opacity(0.16), color.opacity(0)], center: .center, startRadius: 0, endRadius: 300))
            .frame(width: 800, height: 800)
            .offset(x: -400, y: -400)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

/// A route as a faded thread the height of its rides, with its lines at the top and its time below.
struct MiniStrand: View {
    let option: PathOption
    let total: String

    var body: some View {
        let l0 = option.legs[0]
        let r0 = Double(l0.schedRideSec ?? 0) + (l0.typicalSec ?? 0)
        let r1 = option.legs.count > 1 ? Double(option.legs[1].schedRideSec ?? 0) + (option.legs[1].typicalSec ?? 0) : 0
        let scale = 90 / max(60, r0 + r1)
        VStack(spacing: 6) {
            RouteBullets(routes: Array(l0.routes.prefix(2)), size: 14)
            Capsule().fill(RouteStyle.color(l0.primaryRoute)).frame(width: 4, height: max(10, r0 * scale))
            if option.legs.count > 1 {
                Capsule().fill(RouteStyle.color(option.legs[1].primaryRoute)).frame(width: 4, height: max(10, r1 * scale))
            }
            Text(total).font(.caption.weight(.semibold)).monospacedDigit().lineLimit(1)
        }
        .frame(width: 60)
        .opacity(0.7)
    }
}
