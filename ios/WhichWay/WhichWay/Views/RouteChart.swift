import SwiftUI

/// The engine's projection for the whole route as one time–distance chart: the first line's stops from before
/// the boarding stop to the transfer, then the next line's stops from the transfer to where you alight, with
/// every train of each line drawn over its own section, your trains thick, the transfer drawn as the walk and
/// wait between your arrival on one line and your departure on the next, and the final arrival written out.
struct RouteChart: View {
    let legs: [FocusLeg]
    let schedule: ClientSchedule
    let boards: [String: LineBoard]
    let predictions: [String: LinePrediction]

    private let labelW: CGFloat = 118
    private let rowH: CGFloat = 22
    private let axisH: CGFloat = 20
    private let precedingStops = 5
    private let sectionGap: Double = 1.1      // rows of space between one line and the next, for the connection


    private struct Row {
        let leg: Int
        let key: String
        let idx: Int
        let name: String
        let pos: Double                  // vertical position in row units (sections are separated by a gap)
    }

    private struct Section {
        let leg: Int
        let key: String
        let route: String
        let rows: Range<Int>            // row indices of this leg
        let boardRow: Int
        let alightRow: Int
        let rowOf: [Int: Int]            // stop index on the line -> row index
        let lo: Int                      // first stop index shown
        let pos0: Double                 // position of the first row
    }

    private func layout() -> ([Row], [Section]) {
        var rows: [Row] = []
        var sections: [Section] = []
        var pos = 0.0
        for (i, leg) in legs.enumerated() {
            guard let line = schedule.lines[leg.key], let span = leg.idx[leg.key] else { continue }
            let first = min(span.from, span.to), last = max(span.from, span.to)
            // a few stops before the boarding stop on the first leg; a later leg starts at the transfer station itself
            var lo = i == 0 ? max(0, first - precedingStops) : first
            if i == 0, let id = leg.trainId, let t = boards[leg.key]?.trains.first(where: { $0.id == id }) {
                lo = min(lo, max(0, t.nextIdx - 1))
            }
            let hi = min(line.stops.count - 1, last)
            guard lo <= hi else { continue }
            let start = rows.count
            if !sections.isEmpty { pos += sectionGap }
            let pos0 = pos
            var rowOf: [Int: Int] = [:]
            for idx in lo...hi {
                rowOf[idx] = rows.count
                rows.append(Row(leg: i, key: leg.key, idx: idx, name: idx < line.names.count ? line.names[idx] : line.stops[idx], pos: pos))
                pos += 1
            }
            sections.append(Section(leg: i, key: leg.key, route: String(leg.key.split(separator: "_").first ?? ""), rows: start..<rows.count,
                                    boardRow: rowOf[first] ?? start, alightRow: rowOf[last] ?? rows.count - 1, rowOf: rowOf, lo: lo, pos0: pos0))
        }
        return (rows, sections)
    }

    /// Your train on each leg: the one the Go tab picked, else the first train that can be caught after the
    /// previous leg's arrival plus the walk. Returns (train, prediction) per leg index.
    private func yourTrains(_ sections: [Section], now: Double) -> [Int: (LiveTrain, PredictedTrain)] {
        var out: [Int: (LiveTrain, PredictedTrain)] = [:]
        var earliest = now
        for s in sections {
            let leg = legs[s.leg]
            guard let board = boards[s.key], let pred = predictions[s.key], let span = leg.idx[s.key] else { continue }
            let boardIdx = min(span.from, span.to), alightIdx = max(span.from, span.to)
            var chosen: (LiveTrain, PredictedTrain)? = nil
            if let id = leg.trainId, let t = board.trains.first(where: { $0.id == id }), let p = pred.train(t.tripId) {
                chosen = (t, p)
            } else {
                // compared at the platform: each line's feed time runs late by its own amount (PlatformTiming)
                let candidates = board.trains.compactMap { t -> (LiveTrain, PredictedTrain, Double)? in
                    guard t.nextIdx <= boardIdx, let p = pred.train(t.tripId), let b = p.point(at: boardIdx) else { return nil }
                    let at = PlatformTiming.atPlatform(b.etaTs, route: t.route)
                    return at >= earliest + leg.walkSec ? (t, p, at) : nil
                }
                if let c = candidates.min(by: { $0.2 < $1.2 }) { chosen = (c.0, c.1) }
            }
            if let c = chosen {
                out[s.leg] = c
                if let a = c.1.point(at: alightIdx) { earliest = PlatformTiming.atPlatform(a.etaTs, route: c.0.route) }
            }
        }
        return out
    }

    /// The predicted arrival at the end of the route: (eta, lo, hi, feed eta, stop name), or nil.
    func arrival(now: Double) -> (eta: Double, lo: Double, hi: Double, feed: Double?, stop: String)? {
        let (rows, sections) = layout()
        guard let last = sections.last, let y = yourTrains(sections, now: now)[last.leg], let a = y.1.point(at: rows[last.alightRow].idx) else { return nil }
        let feed = (y.0.feedPoints ?? y.0.points).first { $0.idx == a.idx }?.ts
        let at = { (ts: Double) in PlatformTiming.atPlatform(ts, route: y.0.route) }      // the moment at the platform, as on the Go tab
        return (at(a.etaTs), at(a.loTs), at(a.hiTs), feed.map(at), rows[last.alightRow].name)
    }

    var body: some View {
        let (rows, sections) = layout()
        TimelineView(.periodic(from: .now, by: 5)) { ctx in
            let now = ctx.date.timeIntervalSince1970
            Canvas { gc, size in
                draw(&gc, size: size, now: now, rows: rows, sections: sections)
            }
            .frame(height: axisH + CGFloat((rows.last?.pos ?? 0) + 1) * rowH + 6)
        }
    }

    private func draw(_ gc: inout GraphicsContext, size: CGSize, now: Double, rows: [Row], sections: [Section]) {
        guard !rows.isEmpty else { return }
        let yours = yourTrains(sections, now: now)
        // horizon: the whole itinerary plus a little, between 30 and 60 minutes
        var horizon = 1800.0
        if let last = sections.last, let y = yours[last.leg], let a = y.1.point(at: rows[last.alightRow].idx) {
            horizon = min(3600, max(horizon, a.hiTs - now + 120))
        }
        let chart = CGRect(x: labelW, y: axisH, width: max(10, size.width - labelW), height: CGFloat((rows.last?.pos ?? 0) + 1) * rowH)
        func x(_ ts: Double) -> CGFloat { chart.minX + CGFloat((ts - now) / horizon) * chart.width }
        func y(_ pos: Double) -> CGFloat { chart.minY + CGFloat(pos) * rowH + rowH / 2 }
        func rowPos(_ i: Int) -> Double { rows[i].pos }
        let grid = Color(.separator), faint = Color(.separator).opacity(0.45), secondary = Color(.secondaryLabel)

        // sections: a colour strip and the bullet letter in the label gutter, the board / alight rows tinted
        for s in sections {
            let color = RouteStyle.color(s.route)
            let top = y(rowPos(s.rows.lowerBound)) - rowH / 2, bottom = y(rowPos(s.rows.upperBound - 1)) + rowH / 2
            gc.fill(Path(roundedRect: CGRect(x: 2, y: top + 1, width: 4, height: bottom - top - 2), cornerRadius: 2), with: .color(color))
            var badge = gc.resolve(Text(s.route).font(.system(size: 9, weight: .bold)))
            badge.shading = .color(RouteStyle.text(s.route))
            let bc = CGPoint(x: 15, y: top + 9)
            gc.fill(Path(ellipseIn: CGRect(x: bc.x - 7, y: bc.y - 7, width: 14, height: 14)), with: .color(color))
            gc.draw(badge, at: bc, anchor: .center)
            for r in [s.boardRow, s.alightRow] {
                gc.fill(Path(CGRect(x: 0, y: y(rowPos(r)) - rowH / 2, width: size.width, height: rowH)), with: .color(Color.accentColor.opacity(0.10)))
            }
            if s.leg > 0 {
                // the change: a dashed rule through the middle of the gap
                let mid = top - CGFloat(sectionGap) * rowH / 2
                gc.stroke(Path { p in p.move(to: CGPoint(x: 0, y: mid)); p.addLine(to: CGPoint(x: size.width, y: mid)) }, with: .color(grid), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
            }
        }
        for (i, r) in rows.enumerated() {
            let yy = y(r.pos)
            let s = sections.first { $0.leg == r.leg }
            let isMark = s.map { i == $0.boardRow || i == $0.alightRow } ?? false
            gc.stroke(Path { p in p.move(to: CGPoint(x: chart.minX, y: yy)); p.addLine(to: CGPoint(x: chart.maxX, y: yy)) }, with: .color(isMark ? grid : faint), lineWidth: 0.5)
            var label = gc.resolve(Text(r.name).font(isMark ? .caption.bold() : .caption))
            label.shading = .color(isMark ? Color.primary : secondary)
            let sz = label.measure(in: CGSize(width: labelW - 34, height: rowH))
            gc.draw(label, in: CGRect(x: 26, y: yy - sz.height / 2, width: min(sz.width, labelW - 34), height: sz.height))
        }
        // time axis
        let tickEvery = horizon > 2400 ? 600.0 : 300.0
        var nowLabel = gc.resolve(Text("now").font(.caption2.bold())); nowLabel.shading = .color(Color.primary)
        gc.draw(nowLabel, at: CGPoint(x: chart.minX + 2, y: axisH / 2), anchor: .leading)
        var t = ceil(now / tickEvery) * tickEvery
        while t < now + horizon {
            let xx = x(t)
            if xx > chart.minX + 44 {
                gc.stroke(Path { p in p.move(to: CGPoint(x: xx, y: chart.minY)); p.addLine(to: CGPoint(x: xx, y: chart.maxY)) }, with: .color(faint), lineWidth: 0.5)
                var tl = gc.resolve(Text(Fmt.hhmm(t)).font(.caption2)); tl.shading = .color(secondary)
                gc.draw(tl, at: CGPoint(x: xx, y: axisH / 2), anchor: .center)
            }
            t += tickEvery
        }
        gc.stroke(Path { p in p.move(to: CGPoint(x: chart.minX, y: chart.minY)); p.addLine(to: CGPoint(x: chart.minX, y: chart.maxY)) }, with: .color(grid), lineWidth: 1)

        var clipped = gc
        clipped.clip(to: Path(chart))
        // every train of each line over its own section; yours thick with the range band and the feed's line
        var transferFrom: CGPoint? = nil
        var transferTs: Double? = nil
        for s in sections {
            guard let board = boards[s.key], let pred = predictions[s.key], let line = schedule.lines[s.key] else { continue }
            let color = RouteStyle.color(s.route)
            let age = now - board.now
            let mineId = yours[s.leg]?.0.id
            let ordered = board.trains.sorted { ($0.id == mineId ? 1 : 0) < ($1.id == mineId ? 1 : 0) }
            for tr in ordered {
                let focused = tr.id == mineId
                guard let p = pred.train(tr.tripId) else { continue }
                let pts = p.points.filter { s.rowOf[$0.idx] != nil }.sorted { $0.idx < $1.idx }
                let prog = trainProgress(tr, age: age, line: line)
                let lo = Double(s.lo), hi = Double(rows[s.rows.upperBound - 1].idx)
                let startVisible = prog.idx >= lo - 0.5 && prog.idx <= hi + 0.5
                guard !pts.isEmpty || startVisible else { continue }
                func rowY(_ idx: Double) -> CGFloat { y(s.pos0 + (idx - lo)) }
                if focused {
                    var band = Path(); var first = true
                    for q in pts { let pt = CGPoint(x: x(q.loTs), y: rowY(Double(q.idx))); if first { band.move(to: pt); first = false } else { band.addLine(to: pt) } }
                    for q in pts.reversed() { band.addLine(to: CGPoint(x: x(q.hiTs), y: rowY(Double(q.idx)))) }
                    band.closeSubpath()
                    clipped.fill(band, with: .color(color.opacity(0.26)))
                    let feed = (tr.feedPoints ?? tr.points).filter { s.rowOf[$0.idx] != nil }.sorted { $0.idx < $1.idx }
                    if !feed.isEmpty {
                        var fp = Path()
                        if startVisible { fp.move(to: CGPoint(x: chart.minX, y: rowY(prog.idx))) } else { fp.move(to: CGPoint(x: x(feed[0].ts), y: rowY(Double(feed[0].idx)))) }
                        for q in feed { fp.addLine(to: CGPoint(x: x(q.ts), y: rowY(Double(q.idx)))) }
                        clipped.stroke(fp, with: .color(secondary), style: StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                    }
                }
                var path = Path()
                if startVisible { path.move(to: CGPoint(x: chart.minX, y: rowY(prog.idx))) } else if let f = pts.first { path.move(to: CGPoint(x: x(f.etaTs), y: rowY(Double(f.idx)))) }
                for q in pts { path.addLine(to: CGPoint(x: x(q.etaTs), y: rowY(Double(q.idx)))) }
                clipped.stroke(path, with: .color(focused ? color : color.opacity(0.45)), style: StrokeStyle(lineWidth: focused ? 3 : 1.5, lineCap: .round, lineJoin: .round))
                for q in pts {
                    let c = CGPoint(x: x(q.etaTs), y: rowY(Double(q.idx)))
                    clipped.fill(Path(ellipseIn: CGRect(x: c.x - 2, y: c.y - 2, width: 4, height: 4)), with: .color(focused ? color : color.opacity(0.6)))
                }
                if startVisible {
                    let c = CGPoint(x: chart.minX, y: rowY(prog.idx)); let r: CGFloat = focused ? 5 : 3.5
                    let dot = Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
                    if tr.isHeld { gc.fill(dot, with: .color(Color(.systemBackground))); gc.stroke(dot, with: .color(Color.red), lineWidth: 2) } else { gc.fill(dot, with: .color(color)) }
                }
                if focused {
                    // the transfer: from your arrival on this line to your departure on the next
                    if let from = transferFrom, let fromTs = transferTs, let b = p.point(at: rows[s.boardRow].idx) {
                        let to = CGPoint(x: x(b.etaTs), y: y(rowPos(s.boardRow)))
                        clipped.stroke(Path { q in q.move(to: from); q.addLine(to: to) }, with: .color(secondary), style: StrokeStyle(lineWidth: 1.5, dash: [2, 3]))
                        let wait = b.etaTs - fromTs
                        let walk = legs[s.leg].walkSec
                        // the walk and margin, centred in the space between the two lines
                        var tl = gc.resolve(Text(walk > 0 ? "walk \(Fmt.mmss(walk)) · \(Fmt.mmss(max(0, wait - walk))) margin" : "\(Fmt.mmss(max(0, wait))) on the platform").font(.caption2.bold()))
                        tl.shading = .color(Color.primary)
                        let sz = tl.measure(in: CGSize(width: 240, height: 20))
                        let gapMid = y(s.pos0) - rowH / 2 - CGFloat(sectionGap) * rowH / 2
                        let origin = CGPoint(x: chart.midX - sz.width / 2, y: gapMid - sz.height / 2)
                        gc.fill(Path(roundedRect: CGRect(origin: origin, size: sz).insetBy(dx: -6, dy: -2), cornerRadius: 6), with: .color(Color(.secondarySystemBackground)))
                        gc.draw(tl, in: CGRect(origin: origin, size: sz))
                    }
                    if let a = p.point(at: rows[s.alightRow].idx) {
                        transferFrom = CGPoint(x: x(a.etaTs), y: y(rowPos(s.alightRow))); transferTs = a.etaTs
                        if s.leg == sections.count - 1 {
                            // mark the arrival; the time itself is written under the chart
                            let c = transferFrom!
                            gc.stroke(Path(ellipseIn: CGRect(x: c.x - 5, y: c.y - 5, width: 10, height: 10)), with: .color(color), lineWidth: 2)
                        }
                    }
                }
            }
        }
    }
}

/// The route chart in its card.
struct RouteChartCard: View {
    let legs: [FocusLeg]
    let schedule: ClientSchedule
    let boards: [String: LineBoard]
    let predictions: [String: LinePrediction]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Your route, end to end").font(.subheadline.bold())
            let chart = RouteChart(legs: legs, schedule: schedule, boards: boards, predictions: predictions)
            chart
            TimelineView(.periodic(from: .now, by: 5)) { ctx in
                if let a = chart.arrival(now: ctx.date.timeIntervalSince1970) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Spacer()
                        Text("Arrive \(a.stop)").font(.caption).foregroundStyle(.secondary)
                        Text(Fmt.hhmm(a.eta)).font(.title3.bold())
                        Text("\(Fmt.hhmm(a.lo))–\(Fmt.hhmm(a.hi))" + ((a.feed.map { abs($0 - a.eta) >= 30 } ?? false) ? " · feed \(Fmt.hhmm(a.feed!))" : ""))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Text("Each line's trains over its own stretch of your route; your trains thick with their 80% range, the feed's ETA dashed. The dotted link at the change is your walk and the wait for the next train the engine expects you to catch.")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
    }
}
