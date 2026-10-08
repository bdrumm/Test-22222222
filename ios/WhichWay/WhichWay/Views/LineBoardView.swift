import SwiftUI

/// Line tab: one line direction with every started train on the track, then the train list with lateness,
/// position, holds, stalls, track changes and the last measured segment speed.
struct LineBoardView: View {
    @Environment(DataService.self) private var data
    @AppStorage("lineKey") private var lineKey = ""

    var body: some View {
        NavigationStack {
            Group {
                if let sched = data.schedule {
                    content(sched)
                } else {
                    ProgressView("Loading the schedule…")
                }
            }
            .navigationTitle("Line")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { StatusDot() } }
        }
        .onAppear { follow(); register() }
        .onChange(of: lineKey) { _, _ in register() }
        .onChange(of: data.staticVersion) { _, _ in register() }
        .onChange(of: data.focus) { _, _ in follow() }
    }

    /// The Go tab's selected route: this tab shows its line, with the train to take marked.
    private func follow() {
        if let f = data.focus, data.schedule?.lines[f.key] != nil, lineKey != f.key { lineKey = f.key }
    }

    private func register() {
        guard let s = data.schedule else { return }
        if lineKey.isEmpty || s.lines[lineKey] == nil { lineKey = sortedKeys(s).first ?? "" }
        data.setWanted(lineKey.isEmpty ? [] : [lineKey], for: "line")
    }

    /// The selected route's leg on the line shown, if it uses this line.
    private var focusLeg: FocusLeg? { data.focus?.legs.first { $0.keys.contains(lineKey) } }

    /// The train the route takes on the line shown: the leg's train, else the boarding train when its board is this line.
    private var focusedTrainId: String? {
        if let id = focusLeg?.trainId { return id }
        guard let f = data.focus, f.key == lineKey, !f.trainId.isEmpty else { return nil }
        return f.trainId
    }

    /// A horizontal swipe outside the track steps between the lines of the selected route's legs.
    private func stepLine(_ delta: Int) {
        guard let legs = data.focus?.legs, legs.count > 1 else { return }
        let i = legs.firstIndex { $0.keys.contains(lineKey) } ?? 0
        let j = ((i + delta) % legs.count + legs.count) % legs.count
        withAnimation(.easeInOut(duration: 0.2)) { lineKey = legs[j].key }
    }


    /// One stop before the train to take, so the diagram opens on it.
    private func focusStop(_ line: LineTopology) -> Int? {
        guard let id = focusedTrainId, let b = data.boards[lineKey], let t = b.trains.first(where: { $0.id == id }) else { return nil }
        return max(0, Int(trainProgress(t, age: data.now - b.now, line: line).idx.rounded(.down)) - 1)
    }

    private func sortedKeys(_ s: ClientSchedule) -> [String] {
        s.lines.keys.sorted { a, b in
            let pa = a.split(separator: "_").map(String.init), pb = b.split(separator: "_").map(String.init)
            if pa.first != pb.first { return (pa.first ?? "") < (pb.first ?? "") }
            return (pa.last ?? "") < (pb.last ?? "")
        }
    }

    private func title(_ key: String, _ s: ClientSchedule) -> String {
        let parts = key.split(separator: "_").map(String.init)
        let route = parts.first ?? key
        let terminal = s.lines[key]?.names.last ?? (parts.last ?? "")
        return "\(route) → \(terminal)"
    }

    private func content(_ sched: ClientSchedule) -> some View {
        ScrollViewReader { proxy in
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let legs = data.focus?.legs, !legs.isEmpty {
                    // one tab per line of the selected route; each is its own page (a swipe steps between them too)
                    HStack(alignment: .top, spacing: 8) {
                        LegTabs(legs: legs, schedule: sched, lineKey: $lineKey)
                        Menu {
                            Picker("Other lines", selection: $lineKey) {
                                ForEach(sortedKeys(sched), id: \.self) { k in Text(title(k, sched)).tag(k) }
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle").font(.title3).foregroundStyle(.secondary).frame(width: 36, height: 44)
                        }
                        .accessibilityLabel("Other lines")
                    }
                    if focusLeg == nil, let line = sched.lines[lineKey] {
                        Text("\(title(lineKey, sched)) is not on your route · \(line.names.first ?? "") to \(line.names.last ?? "")").font(.caption).foregroundStyle(.secondary)
                    }
                    // alerts that touch this route directly come first: station and entrance notices at its stops, skipped
                    // stops, and line-wide delays on its lines
                    let direct = data.alertsAffecting(legs: legs, schedule: sched)
                    if !direct.isEmpty {
                        Text("Affects your route").font(.subheadline.bold()).padding(.top, 2)
                        ForEach(direct.prefix(4), id: \.alert.id) { item in
                            AlertCard(alert: item.alert, evidence: alertEvidence(item.alert, data: data), station: item.station)
                        }
                    }
                } else {
                    Picker("Line", selection: $lineKey) {
                        ForEach(sortedKeys(sched), id: \.self) { k in Text(title(k, sched)).tag(k) }
                    }
                    .pickerStyle(.menu)
                }
                if let line = sched.lines[lineKey] {
                    let route = String(lineKey.split(separator: "_").first ?? "")
                    if let h = lineHealth(key: lineKey, data: data) {
                        LineHealthRow(health: h, route: route)
                    }
                    if let id = focusedTrainId, let b = data.boards[lineKey], let t = b.trains.first(where: { $0.id == id }) {
                        Text("Your train: \(shortLabel(t)), \(t.position?.text ?? "position unknown"). From the route selected on the Go tab.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    let span = focusLeg?.idx[lineKey]
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        TrackDiagramView(line: line, route: route, fromIdx: span?.from, toIdx: span?.to, trains: trains(line), colW: 52,
                                         scrollTo: focusStop(line) ?? span.map { max(0, $0.from - 1) })
                    }
                    if let legs = data.focus?.legs, legs.count > 1, legs.allSatisfy({ data.boards[$0.key] != nil }) {
                        var preds: [String: LinePrediction] = [:]
                        let _ = legs.forEach { l in if let p = data.predictions[l.key]?[data.scenario] ?? data.predictions[l.key]?["baseline"] { preds[l.key] = p } }
                        RouteChartCard(legs: legs, schedule: sched, boards: data.boards, predictions: preds)
                    }
                    if let b = data.predictedBoards[lineKey] ?? data.boards[lineKey] {
                        HStack(spacing: 8) {
                            Tile(title: "Trains", value: "\(b.trains.count)", sub: "started, in the feed")
                            Tile(title: "Held / overdue", value: "\(b.nHolding) / \(b.nStalled)", sub: "≥ \(Int(sched.constants.holdSec)) s at a stop / late between stops")
                            Tile(title: "Late ≥ 3 min", value: "\(b.trains.filter { ($0.effectiveLatenessSec ?? 0) >= 180 }.count)", sub: "vs the timetable")
                        }
                        if let lp = data.predictions[lineKey]?[data.scenario] ?? data.predictions[lineKey]?["baseline"] {
                            if let raw = data.boards[lineKey], !raw.trains.isEmpty {
                                PredictionCard(line: line, board: raw, prediction: lp, focusedTrainId: focusedTrainId, span: span, route: route)
                            }
                            EngineCards(prediction: lp, line: line)
                        }
                        ScenarioPicker()
                        let directIds = Set((data.focus?.legs).map { data.alertsAffecting(legs: $0, schedule: sched).map { $0.alert.id } } ?? [])
                        let alerts = data.alertsFor(routes: [route]).filter { !directIds.contains($0.id) }
                        if !alerts.isEmpty {
                            Text(directIds.isEmpty ? "Alerts on the \(route)" : "Other alerts on the \(route)").font(.subheadline.bold()).padding(.top, 2)
                            ForEach(alerts.prefix(3)) { a in AlertCard(alert: a, evidence: alertEvidence(a, data: data)) }
                        }
                        ForEach(b.trains) { t in TrainRow(train: t, focused: t.id == focusedTrainId).id("train-\(t.id)") }
                        if b.trains.isEmpty { Text("No started train on this line in the feed.").font(.caption).foregroundStyle(.secondary) }
                    } else {
                        Text("Waiting for the feed…").font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let err = data.lastError { Text(err).font(.caption2).foregroundStyle(Color.red) }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
            .background(SwipeCatcher { stepLine($0) })
        }
        .onChange(of: data.focus) { _, f in
            if let f = f, f.key == lineKey { withAnimation { proxy.scrollTo("train-\(f.trainId)", anchor: .center) } }
        }
        }
    }

    private func trains(_ line: LineTopology) -> [DiagramTrain] {
        guard let b = data.boards[lineKey] else { return [] }
        let age = data.now - b.now
        let focus = focusedTrainId
        return b.trains.map { t in
            let p = trainProgress(t, age: age, line: line)
            var sub = ""
            if let e = t.effectiveLatenessSec, abs(e) >= 60 { sub = Fmt.late(e) }
            return DiagramTrain(id: t.id, idx: p.idx, state: p.state, route: t.route, label: shortLabel(t), sub: sub, emphasis: t.id == focus ? "origin" : nil)
        }
    }
}

struct TrainRow: View {
    let train: LiveTrain
    var focused: Bool = false

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            RouteBullet(route: train.route, size: 20)
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(shortLabel(train)).font(.caption.monospaced().bold())
                    Spacer()
                    Text("next \(train.nextName) \(Fmt.hhmm(train.feedPoints?.first?.ts ?? train.etaTs))").font(.caption).lineLimit(1)
                }
                if let text = engineText {
                    Text(text).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                }
                Text(train.position?.text ?? "position unknown").font(.caption2).foregroundStyle(.secondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        if focused { Flag("your train", Color.green) }
                        Text(Fmt.late(train.effectiveLatenessSec) + (train.schedMethod == "nearest" ? " ~" : ""))
                            .font(.caption2).foregroundStyle(latenessColor)
                        if train.corroboration == "feed_optimistic" { Flag("feed optimistic", Color.orange) }
                        if let p = train.position, p.holding { Flag("held \(Fmt.mmss(p.sinceSec))", Color.orange) }
                        if let p = train.position, p.stalled { Flag("overdue", Color.red) }
                        if train.trackChanged { Flag("track change", Color.purple) }
                        if let lr = train.lastRun, let sp = lr.speedKmh {
                            Text("last run \(Fmt.mph(sp))" + (lr.schedSpeedKmh.map { " (sched \(Fmt.mph($0)))" } ?? ""))
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(focused ? Color.accentColor.opacity(0.12) : Color(.secondarySystemBackground)))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(focused ? Color.accentColor : Color.clear, lineWidth: 1))
    }

    private var engineText: String? {
        guard let pr = train.pred, let pt = pr.point(at: train.nextIdx) else { return nil }
        var s = "engine \(Fmt.hhmm(pt.etaTs)) · \(Fmt.hhmm(pt.loTs))–\(Fmt.hhmm(pt.hiTs))"
        if pr.holdExtraSec > 0 { s += " · +\(Int(pr.holdExtraSec.rounded())) s expected hold" }
        if pr.knockOnSec >= 60 { s += " · held back \(Fmt.mmss(pr.knockOnSec))" }
        return s
    }

    private var latenessColor: Color {
        guard let e = train.effectiveLatenessSec else { return Color.secondary }
        if e >= 300 { return Color.red }
        if e >= 120 { return Color.orange }
        return Color.secondary
    }
}


/// The lines of the selected route as tabs: the line's bullet, where it is boarded and left, and the train taken.
struct LegTabs: View {
    @Environment(DataService.self) private var data
    let legs: [FocusLeg]
    let schedule: ClientSchedule
    @Binding var lineKey: String

    var body: some View {
        HStack(spacing: 8) {
            ForEach(Array(legs.enumerated()), id: \.offset) { i, leg in
                let route = String(leg.key.split(separator: "_").first ?? "")
                let selected = leg.keys.contains(lineKey)
                let names = schedule.lines[leg.key]?.names ?? []
                let span = leg.idx[leg.key]
                let board = span.flatMap { $0.from < names.count ? names[$0.from] : nil } ?? ""
                let alight = span.flatMap { $0.to < names.count ? names[$0.to] : nil } ?? ""
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { lineKey = leg.key }
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            RouteBullets(routes: [route], size: 20)
                            Text(legs.count > 1 ? "Line \(i + 1)" : "Your line").font(.caption.bold()).foregroundStyle(selected ? Color.primary : Color.secondary)
                            Spacer(minLength: 0)
                            if let h = lineHealth(key: leg.key, data: data) {
                                Circle().fill(h.color).frame(width: 8, height: 8).accessibilityLabel(h.label)
                            }
                        }
                        Text(board.isEmpty || alight.isEmpty ? "the whole line" : "\(board) → \(alight)")
                            .font(.caption2).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.8)
                    }
                    .padding(.horizontal, 10).padding(.vertical, 7)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 10).fill(selected ? Color.accentColor.opacity(0.14) : Color(.secondarySystemBackground)))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(selected ? Color.accentColor : Color.clear, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(route) line, \(board) to \(alight)")
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
    }
}

/// What the engine projects for the line in the next hour, as two tiles: the largest gap, and the knock-on
/// from the train ahead.
struct EngineCards: View {
    let prediction: LinePrediction
    let line: LineTopology

    var body: some View {
        HStack(spacing: 8) {
            if let w = prediction.worstGap {
                let name = w.idx < line.names.count ? line.names[w.idx] : "stop \(w.idx)"
                Tile(title: "Largest gap ahead", value: Fmt.minTxt(w.gapSec), sub: "at \(name) · around \(Fmt.hhmm(w.atTs))")
            } else {
                Tile(title: "Largest gap ahead", value: "none", sub: "no gap projected in the next hour")
            }
            if prediction.nKnockOn > 0 {
                Tile(title: "Held back", value: "\(prediction.nKnockOn) train\(prediction.nKnockOn == 1 ? "" : "s")", sub: "by the train ahead · \(Fmt.minTxt(prediction.knockOnTotalSec)) in total")
            } else {
                Tile(title: "Held back", value: "none", sub: "no train slowed by the one ahead")
            }
        }
    }
}

/// One alert as a card: its kind, what it says, and whether the live boards bear it out.
struct AlertCard: View {
    let alert: RouteAlert
    let evidence: AlertEvidence
    var station: String? = nil      // the station on your route this alert names

    private var kindColor: Color {
        switch alert.kind {
        case "delay": return .red
        case "planned": return .orange
        default: return .secondary
        }
    }

    private var kindTitle: String {
        switch alert.kind {
        case "delay": return "Delays"
        case "planned": return "Planned work"
        default: return "Notice"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Flag(kindTitle, kindColor)
                RouteBullets(routes: alert.routes, size: 16)
                if let st = station { Flag("at \(st)", Color.accentColor) }
                Spacer()
            }
            Text(alert.header).font(.subheadline).lineLimit(station == nil ? 3 : 5)
            HStack(alignment: .top, spacing: 6) {
                Circle().fill(evidence.corroborated ? Color.red : Color.secondary.opacity(0.6)).frame(width: 7, height: 7).padding(.top, 5)
                Text(evidence.text).font(.caption).foregroundStyle(evidence.corroborated ? Color.red : Color.secondary).lineLimit(2)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(kindColor.opacity(alert.kind == "delay" ? 0.5 : 0.25), lineWidth: 1))
    }
}
