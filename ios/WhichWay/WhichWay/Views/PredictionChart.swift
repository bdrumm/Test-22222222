import SwiftUI

/// The engine's projection for one line as a time–distance ("stringline") chart: stops down the side, the next
/// half hour across, and every train in the feed drawn as the path the published model predicts for it, stop by
/// stop. Your train is emphasised with its 80% range shaded and the feed's own ETA dashed beside it, so the
/// difference between what the MTA countdown says and what the engine expects is visible at a glance.
struct PredictionChart: View {
    let line: LineTopology
    let board: LineBoard             // the feed's own board (positions and feed ETAs)
    let prediction: LinePrediction   // the engine's projection for the current scenario
    let focusedTrainId: String?
    let span: StopSpan?
    let route: String

    private let labelW: CGFloat = 96
    private let rowH: CGFloat = 16
    private let axisH: CGFloat = 20

    /// Stops before the boarding stop that are always shown, so trains can be seen approaching.
    private let precedingStops = 6
    private let maxRows = 26

    /// Stop rows to show: from well before the boarding stop (and from wherever your train is now, if that is
    /// further back) to a little past the alighting stop; without a route, the stretch the trains are on.
    private var window: (lo: Int, hi: Int) {
        let n = line.stops.count
        guard n > 0 else { return (0, 0) }
        if let s = span {
            let first = min(s.from, s.to), last = max(s.from, s.to)
            var lo = max(0, first - precedingStops)
            if let id = focusedTrainId, let t = board.trains.first(where: { $0.id == id }) {
                lo = min(lo, max(0, t.nextIdx - 1))
            }
            let hi = min(n - 1, max(last + 1, lo + 10))
            lo = max(lo, hi - maxRows + 1)
            return (lo, hi)
        }
        let next = board.trains.map { $0.nextIdx }.filter { $0 >= 0 }
        let lo = max(0, (next.min() ?? 0) - 1)
        return (lo, min(n - 1, lo + 18))
    }

    private func horizon(_ now: Double) -> Double {
        var h = 1800.0
        if let id = focusedTrainId, let t = board.trains.first(where: { $0.id == id }), let p = prediction.train(t.tripId),
           let s = span, let alight = p.point(at: max(s.from, s.to)) {
            h = min(3600, max(h, alight.hiTs - now + 120))
        }
        return h
    }

    /// The predicted arrival of your train at the stop you alight: (eta, lo, hi, feed eta, stop name), or nil.
    func arrival() -> (eta: Double, lo: Double, hi: Double, feed: Double?, stop: String)? {
        guard let id = focusedTrainId, let s = span, let t = board.trains.first(where: { $0.id == id }), let p = prediction.train(t.tripId) else { return nil }
        let idx = max(s.from, s.to)
        guard let a = p.point(at: idx) else { return nil }
        let feed = (t.feedPoints ?? t.points).first { $0.idx == idx }?.ts
        let at = { (ts: Double) in PlatformTiming.atPlatform(ts, route: t.route) }      // the moment at the platform, as on the Go tab
        return (at(a.etaTs), at(a.loTs), at(a.hiTs), feed.map(at), idx < line.names.count ? line.names[idx] : line.stops[idx])
    }

    var body: some View {
        let w = window
        let rows = max(1, w.hi - w.lo + 1)
        TimelineView(.periodic(from: .now, by: 5)) { ctx in
            let now = ctx.date.timeIntervalSince1970
            Canvas { gc, size in
                draw(&gc, size: size, now: now, lo: w.lo, hi: w.hi)
            }
            .frame(height: axisH + CGFloat(rows) * rowH + 6)
        }
    }

    private func draw(_ gc: inout GraphicsContext, size: CGSize, now: Double, lo: Int, hi: Int) {
        let horizon = horizon(now)
        let chart = CGRect(x: labelW, y: axisH, width: max(10, size.width - labelW), height: CGFloat(hi - lo + 1) * rowH)
        func x(_ ts: Double) -> CGFloat { chart.minX + CGFloat((ts - now) / horizon) * chart.width }
        func y(_ idx: Double) -> CGFloat { chart.minY + CGFloat(idx - Double(lo)) * rowH + rowH / 2 }
        let grid = Color(.separator)
        let faint = Color(.separator).opacity(0.45)
        let secondary = Color(.secondaryLabel)
        let routeColor = RouteStyle.color(route)

        // stop rows: labels, guide lines, board/alight bands
        for i in lo...hi {
            let yy = y(Double(i))
            let isMark = span.map { i == $0.from || i == $0.to } ?? false
            if isMark {
                gc.fill(Path(CGRect(x: 0, y: yy - rowH / 2, width: size.width, height: rowH)), with: .color(Color.accentColor.opacity(0.10)))
            }
            gc.stroke(Path { p in p.move(to: CGPoint(x: chart.minX, y: yy)); p.addLine(to: CGPoint(x: chart.maxX, y: yy)) }, with: .color(isMark ? grid : faint), lineWidth: 0.5)
            let name = i < line.names.count ? line.names[i] : line.stops[i]
            var label = gc.resolve(Text(name).font(isMark ? .caption2.bold() : .caption2))
            label.shading = .color(isMark ? Color.primary : secondary)
            let sz = label.measure(in: CGSize(width: labelW - 8, height: rowH))
            gc.draw(label, in: CGRect(x: 0, y: yy - sz.height / 2, width: min(sz.width, labelW - 8), height: sz.height))
        }
        // time axis: now, then every five minutes
        let tickEvery = horizon > 2400 ? 600.0 : 300.0
        var t = ceil(now / tickEvery) * tickEvery
        var nowLabel = gc.resolve(Text("now").font(.caption2.bold()))
        nowLabel.shading = .color(Color.primary)
        gc.draw(nowLabel, at: CGPoint(x: chart.minX + 2, y: axisH / 2), anchor: .leading)
        while t < now + horizon {
            let xx = x(t)
            if xx > chart.minX + 44 {
                gc.stroke(Path { p in p.move(to: CGPoint(x: xx, y: chart.minY)); p.addLine(to: CGPoint(x: xx, y: chart.maxY)) }, with: .color(faint), lineWidth: 0.5)
                var tl = gc.resolve(Text(Fmt.hhmm(t)).font(.caption2))
                tl.shading = .color(secondary)
                gc.draw(tl, at: CGPoint(x: xx, y: axisH / 2), anchor: .center)
            }
            t += tickEvery
        }
        gc.stroke(Path { p in p.move(to: CGPoint(x: chart.minX, y: chart.minY)); p.addLine(to: CGPoint(x: chart.minX, y: chart.maxY)) }, with: .color(grid), lineWidth: 1)

        // trains: the engine's path for each, the focused one on top with its range and the feed's own line
        var clipped = gc
        clipped.clip(to: Path(chart))
        let age = now - board.now
        let ordered = board.trains.sorted { ($0.id == focusedTrainId ? 1 : 0) < ($1.id == focusedTrainId ? 1 : 0) }
        for tr in ordered {
            let focused = tr.id == focusedTrainId
            let prog = trainProgress(tr, age: age, line: line)
            guard let pred = prediction.train(tr.tripId) else { continue }
            let pts = pred.points.filter { $0.idx >= lo - 1 && $0.idx <= hi + 1 }.sorted { $0.idx < $1.idx }
            guard !pts.isEmpty else { continue }
            let startVisible = prog.idx >= Double(lo) - 0.5 && prog.idx <= Double(hi) + 0.5
            if focused {
                // 80% range as a band between the p10 and p90 arrival at each stop
                var band = Path()
                var first = true
                for p in pts {
                    let pt = CGPoint(x: x(p.loTs), y: y(Double(p.idx)))
                    if first { band.move(to: pt); first = false } else { band.addLine(to: pt) }
                }
                for p in pts.reversed() { band.addLine(to: CGPoint(x: x(p.hiTs), y: y(Double(p.idx)))) }
                band.closeSubpath()
                clipped.fill(band, with: .color(routeColor.opacity(0.26)))
                // the feed's own ETAs, dashed
                let feed = (tr.feedPoints ?? tr.points).filter { $0.idx >= lo - 1 && $0.idx <= hi + 1 }.sorted { $0.idx < $1.idx }
                if feed.count >= 1 {
                    var fp = Path()
                    fp.move(to: CGPoint(x: chart.minX, y: y(prog.idx)))
                    for p in feed { fp.addLine(to: CGPoint(x: x(p.ts), y: y(Double(p.idx)))) }
                    clipped.stroke(fp, with: .color(secondary), style: StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                }
            }
            var path = Path()
            path.move(to: CGPoint(x: chart.minX, y: y(startVisible ? prog.idx : Double(pts[0].idx))))
            for p in pts { path.addLine(to: CGPoint(x: x(p.etaTs), y: y(Double(p.idx)))) }
            clipped.stroke(path, with: .color(focused ? routeColor : routeColor.opacity(0.5)), style: StrokeStyle(lineWidth: focused ? 3 : 1.5, lineCap: .round, lineJoin: .round))
            for p in pts where p.idx >= lo && p.idx <= hi {
                let c = CGPoint(x: x(p.etaTs), y: y(Double(p.idx)))
                clipped.fill(Path(ellipseIn: CGRect(x: c.x - 2, y: c.y - 2, width: 4, height: 4)), with: .color(focused ? routeColor : routeColor.opacity(0.6)))
            }
            // where the train is now
            if startVisible {
                let c = CGPoint(x: chart.minX, y: y(prog.idx))
                let r: CGFloat = focused ? 5 : 3.5
                let dot = Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
                if tr.isHeld {
                    gc.fill(dot, with: .color(Color(.systemBackground)))
                    gc.stroke(dot, with: .color(Color.red), lineWidth: 2)
                } else {
                    gc.fill(dot, with: .color(routeColor))
                }
            }
            // mark where you alight; the time itself is written under the chart
            if focused, let s = span, let alight = pts.first(where: { $0.idx == max(s.from, s.to) }) {
                let c = CGPoint(x: x(alight.etaTs), y: y(Double(alight.idx)))
                clipped.stroke(Path(ellipseIn: CGRect(x: c.x - 5, y: c.y - 5, width: 10, height: 10)), with: .color(routeColor), lineWidth: 2)
            }
        }
    }
}

/// The chart in its card with a title and a one-line key.
struct PredictionCard: View {
    let line: LineTopology
    let board: LineBoard
    let prediction: LinePrediction
    let focusedTrainId: String?
    let span: StopSpan?
    let route: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("What the engine projects").font(.subheadline.bold())
                Spacer()
                Text(prediction.scenario == "baseline" ? "" : prediction.scenario.replacingOccurrences(of: "_", with: " ")).font(.caption2).foregroundStyle(.secondary)
            }
            let chart = PredictionChart(line: line, board: board, prediction: prediction, focusedTrainId: focusedTrainId, span: span, route: route)
            chart
            if let a = chart.arrival() {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Spacer()
                    Text("Arrive \(a.stop)").font(.caption).foregroundStyle(.secondary)
                    Text(Fmt.hhmm(a.eta)).font(.title3.bold())
                    Text("\(Fmt.hhmm(a.lo))–\(Fmt.hhmm(a.hi))" + ((a.feed.map { abs($0 - a.eta) >= 30 } ?? false) ? " · feed \(Fmt.hhmm(a.feed!))" : ""))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 12) {
                key(Rectangle().fill(RouteStyle.color(route)).frame(width: 14, height: 3), focusedTrainId != nil ? "your train (thick) and the rest" : "predicted arrival")
                if focusedTrainId != nil {
                    key(Rectangle().fill(RouteStyle.color(route).opacity(0.2)).frame(width: 14, height: 8), "80% range")
                    key(Rectangle().fill(Color(.secondaryLabel)).frame(width: 14, height: 1.5).mask(HStack(spacing: 2) { ForEach(0..<3) { _ in Rectangle() } }), "feed's ETA")
                }
                key(Circle().stroke(Color.red, lineWidth: 2).frame(width: 8, height: 8), "held")
            }
            .font(.caption2).foregroundStyle(.secondary)
            Text("Each path is the model's arrival time at every stop ahead: the feed's ETA calibrated for its horizon, blended with how lateness carries on this line at this time of day, then corrected for holds and the headway to the train ahead.")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
    }

    private func key<S: View>(_ swatch: S, _ label: String) -> some View {
        HStack(spacing: 4) { swatch; Text(label).lineLimit(1) }
    }
}
