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
        .onAppear { register() }
        .onChange(of: lineKey) { _, _ in register() }
        .onChange(of: data.staticVersion) { _, _ in register() }
    }

    private func register() {
        guard let s = data.schedule else { return }
        if lineKey.isEmpty || s.lines[lineKey] == nil { lineKey = sortedKeys(s).first ?? "" }
        data.setWanted(lineKey.isEmpty ? [] : [lineKey], for: "line")
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
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Picker("Line", selection: $lineKey) {
                    ForEach(sortedKeys(sched), id: \.self) { k in Text(title(k, sched)).tag(k) }
                }
                .pickerStyle(.menu)
                if let line = sched.lines[lineKey] {
                    let route = String(lineKey.split(separator: "_").first ?? "")
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        TrackDiagramView(line: line, route: route, trains: trains(line), colW: 52)
                    }
                    if let b = data.predictedBoards[lineKey] ?? data.boards[lineKey] {
                        HStack(spacing: 8) {
                            Tile(title: "Trains", value: "\(b.trains.count)", sub: "started, in the feed")
                            Tile(title: "Held / overdue", value: "\(b.nHolding) / \(b.nStalled)", sub: "≥ \(Int(sched.constants.holdSec)) s at a stop / late between stops")
                            Tile(title: "Late ≥ 3 min", value: "\(b.trains.filter { ($0.effectiveLatenessSec ?? 0) >= 180 }.count)", sub: "vs the timetable")
                        }
                        if let lp = data.predictions[lineKey]?[data.scenario] ?? data.predictions[lineKey]?["baseline"] {
                            EngineSummary(prediction: lp, line: line)
                        }
                        ScenarioPicker()
                        let alerts = data.alertsFor(routes: [route])
                        ForEach(alerts.prefix(3)) { a in
                            Text("\(a.kind): \(a.header)").font(.caption).foregroundStyle(a.kind == "delay" ? Color.red : Color.secondary).lineLimit(3)
                        }
                        ForEach(b.trains) { t in TrainRow(train: t) }
                        if b.trains.isEmpty { Text("No started train on this line in the feed.").font(.caption).foregroundStyle(.secondary) }
                    } else {
                        Text("Waiting for the feed…").font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let err = data.lastError { Text(err).font(.caption2).foregroundStyle(Color.red) }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
    }

    private func trains(_ line: LineTopology) -> [DiagramTrain] {
        guard let b = data.boards[lineKey] else { return [] }
        let age = data.now - b.now
        return b.trains.map { t in
            let p = trainProgress(t, age: age, line: line)
            var sub = ""
            if let e = t.effectiveLatenessSec, abs(e) >= 60 { sub = Fmt.late(e) }
            return DiagramTrain(id: t.id, idx: p.idx, state: p.state, route: t.route, label: shortLabel(t), sub: sub)
        }
    }
}

struct TrainRow: View {
    let train: LiveTrain

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
                        Text(Fmt.late(train.effectiveLatenessSec) + (train.schedMethod == "nearest" ? " ~" : ""))
                            .font(.caption2).foregroundStyle(latenessColor)
                        if train.corroboration == "feed_optimistic" { Flag("feed optimistic", Color.orange) }
                        if let p = train.position, p.holding { Flag("held \(Fmt.mmss(p.sinceSec))", Color.orange) }
                        if let p = train.position, p.stalled { Flag("overdue", Color.red) }
                        if train.trackChanged { Flag("track change", Color.purple) }
                        if let lr = train.lastRun, let sp = lr.speedKmh {
                            Text("last run \(Fmt.kmh(sp))" + (lr.schedSpeedKmh.map { " (sched \(Fmt.kmh($0)))" } ?? ""))
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color(.secondarySystemBackground)))
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


/// What the engine projects for the line in the next hour: the largest gap and the knock-on from held trains.
struct EngineSummary: View {
    let prediction: LinePrediction
    let line: LineTopology

    private var text: String {
        guard let w = prediction.worstGap else { return "Engine: no gap projected in the next hour." }
        let name = w.idx < line.names.count ? line.names[w.idx] : "stop \(w.idx)"
        var s = "Engine: largest projected gap \(Fmt.minTxt(w.gapSec)) at \(name) around \(Fmt.hhmm(w.atTs))"
        if prediction.nKnockOn > 0 {
            let plural = prediction.nKnockOn == 1 ? "" : "s"
            s += " · \(prediction.nKnockOn) train\(plural) held back by the train ahead (\(Fmt.minTxt(prediction.knockOnTotalSec)) in total)"
        }
        return s
    }

    var body: some View {
        Text(text).font(.caption).foregroundStyle(.secondary)
    }
}
