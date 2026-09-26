import SwiftUI

/// Marey timeline of the selected path: stops down the side (both legs, the change shared), time across, one
/// line per train: the feed's projection dashed, the engine's projection dotted (red when held or held back),
/// the recommended itinerary as a red path (wait, ride, change, ride). Redrawn every second for the "now" line.
struct MareyChartView: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    let schedule: ClientSchedule
    var backSec: Double = 300
    var horizonSec: Double = 2700

    private struct Row { var name: String; var leg: Int; var idx: Int }
    private struct LegSpan { var key: String; var keys: [String]; var start: Int; var end: Int; var from: Int; var to: Int; var route: String }

    private var spans: [LegSpan] {
        var out: [LegSpan] = []
        for (li, leg) in option.legs.enumerated() {
            guard let ix = leg.idx[leg.primaryKey] else { continue }
            out.append(LegSpan(key: leg.primaryKey, keys: leg.keys, start: max(0, ix.from - (li == 0 ? 3 : 2)), end: ix.to, from: ix.from, to: ix.to, route: leg.primaryRoute))
        }
        return out
    }

    private func rows(_ spans: [LegSpan]) -> ([Row], [Int]) {
        var rows: [Row] = []
        var offsets: [Int] = []
        for (li, sp) in spans.enumerated() {
            guard let line = schedule.lines[sp.key] else { offsets.append(rows.count); continue }
            offsets.append(rows.count)
            for i in sp.start...sp.end {
                let name = i < line.names.count ? line.names[i] : line.stops[i]
                if li > 0 && i == sp.start, let last = rows.last {
                    rows[rows.count - 1] = Row(name: last.name == name ? name : "\(last.name) / \(name)", leg: last.leg, idx: last.idx)
                    offsets[li] = rows.count - 1
                    continue
                }
                rows.append(Row(name: name, leg: li, idx: i))
            }
        }
        return (rows, offsets)
    }

    var body: some View {
        let spans = self.spans
        let (rows, offsets) = self.rows(spans)
        let rowH: CGFloat = 16
        let left: CGFloat = 118, right: CGFloat = 10, top: CGFloat = 14, bottom: CGFloat = 22
        let height = top + CGFloat(max(1, rows.count - 1)) * rowH + bottom
        VStack(alignment: .leading, spacing: 4) {
            Text("This path over the next \(Int(horizonSec / 60)) minutes").font(.subheadline.bold())
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                let now = data.now
                Canvas { ctx, size in
                    let w = size.width - left - right
                    let t0 = now - backSec, t1 = now + horizonSec
                    func xp(_ ts: Double) -> CGFloat { left + CGFloat((ts - t0) / (t1 - t0)) * w }
                    func yp(_ r: Int) -> CGFloat { rows.count > 1 ? top + CGFloat(r) / CGFloat(rows.count - 1) * (height - top - bottom) : height / 2 }
                    func rowOf(_ leg: Int, _ idx: Int) -> Int? {
                        guard leg < spans.count else { return nil }
                        let sp = spans[leg]
                        guard idx >= sp.start, idx <= sp.end else { return nil }
                        return offsets[leg] + (idx - sp.start)
                    }
                    // grid
                    for (r, row) in rows.enumerated() {
                        let y = yp(r)
                        ctx.stroke(Path { p in p.move(to: CGPoint(x: left, y: y)); p.addLine(to: CGPoint(x: left + w, y: y)) }, with: .color(Color.secondary.opacity(0.18)), lineWidth: 1)
                        let label = row.name.count > 20 ? String(row.name.prefix(19)) + "…" : row.name
                        ctx.draw(Text(label).font(.system(size: 9)).foregroundStyle(.secondary), at: CGPoint(x: left - 6, y: y), anchor: .trailing)
                    }
                    let tick = 600.0
                    var t = (t0 / tick).rounded(.up) * tick
                    while t <= t1 {
                        ctx.stroke(Path { p in p.move(to: CGPoint(x: xp(t), y: top)); p.addLine(to: CGPoint(x: xp(t), y: height - bottom)) }, with: .color(Color.secondary.opacity(0.18)), lineWidth: 1)
                        ctx.draw(Text(Fmt.hhmm(t)).font(.system(size: 9)).foregroundStyle(.secondary), at: CGPoint(x: xp(t), y: height - 8), anchor: .center)
                        t += tick
                    }
                    ctx.stroke(Path { p in p.move(to: CGPoint(x: xp(now), y: top)); p.addLine(to: CGPoint(x: xp(now), y: height - bottom)) }, with: .color(Color.primary.opacity(0.6)), lineWidth: 1.5)
                    ctx.draw(Text("now").font(.system(size: 9)).foregroundStyle(.secondary), at: CGPoint(x: xp(now) + 3, y: top + 4), anchor: .leading)
                    // trains: feed projection dashed, engine projection dotted
                    let mine = Set((option.live?.legs ?? []).map { $0.train.tripId })
                    for (li, sp) in spans.enumerated() {
                        for k in sp.keys {
                            guard let b = data.predictedBoards[k], let bl = schedule.lines[k], let pl = schedule.lines[sp.key] else { continue }
                            let color = RouteStyle.color(sp.route)
                            for tr in b.trains {
                                func mapIdx(_ i: Int) -> Int? {
                                    guard i < bl.stops.count else { return nil }
                                    let pi = k == sp.key ? i : (pl.stops.firstIndex(of: bl.stops[i]) ?? -1)
                                    return pi >= 0 ? rowOf(li, pi) : nil
                                }
                                let held = tr.isHeld
                                let hi = mine.contains(tr.tripId)
                                let feedPts: [CGPoint] = (tr.feedPoints ?? tr.points).compactMap { pt in
                                    guard pt.ts >= t0, pt.ts <= t1, let r = mapIdx(pt.idx) else { return nil }
                                    return CGPoint(x: xp(pt.ts), y: yp(r))
                                }
                                if feedPts.count >= 2 {
                                    ctx.stroke(Path { p in p.move(to: feedPts[0]); for q in feedPts.dropFirst() { p.addLine(to: q) } },
                                               with: .color(held ? Color.red : (hi ? color : color.opacity(0.55))), style: StrokeStyle(lineWidth: hi ? 2.5 : 1.3, lineCap: .round, lineJoin: .round, dash: [5, 4]))
                                }
                                if let pr = tr.pred {
                                    let predPts: [CGPoint] = pr.points.compactMap { pt in
                                        guard pt.etaTs >= t0, pt.etaTs <= t1, let r = mapIdx(pt.idx) else { return nil }
                                        return CGPoint(x: xp(pt.etaTs), y: yp(r))
                                    }
                                    if predPts.count >= 2 {
                                        let c: Color = (pr.holdExtraSec > 0 || pr.knockOnSec >= 60) ? Color.red : (hi ? color : color.opacity(0.7))
                                        ctx.stroke(Path { p in p.move(to: predPts[0]); for q in predPts.dropFirst() { p.addLine(to: q) } },
                                                   with: .color(c), style: StrokeStyle(lineWidth: hi ? 2.5 : 1.4, lineCap: .round, dash: [1.5, 3.5]))
                                    }
                                }
                                // position now
                                let prog = trainProgress(tr, age: now - b.now, line: bl)
                                let j = Int(prog.idx.rounded(.down))
                                if let r0 = mapIdx(j) {
                                    let r1 = mapIdx(min(bl.stops.count - 1, j + 1)) ?? r0
                                    let y = yp(r0) + (yp(r1) - yp(r0)) * CGFloat(prog.idx - Double(j))
                                    let c = RouteStyle.stateColor(prog.state, route: sp.route)
                                    ctx.fill(Path(ellipseIn: CGRect(x: xp(now) - 4, y: y - 4, width: 8, height: 8)), with: .color(c))
                                }
                            }
                        }
                    }
                    // the recommended itinerary
                    if let it = option.live, let s0 = spans.first, let r0 = rowOf(0, s0.from), let r1 = rowOf(0, s0.to) {
                        var pts = [CGPoint(x: xp(now), y: yp(r0)), CGPoint(x: xp(it.legs[0].boardTs), y: yp(r0)), CGPoint(x: xp(it.legs[0].arriveTs), y: yp(r1))]
                        if it.legs.count > 1, spans.count > 1, let r2 = rowOf(1, spans[1].from), let r3 = rowOf(1, spans[1].to) {
                            pts.append(CGPoint(x: xp(it.legs[1].boardTs), y: yp(r2))); pts.append(CGPoint(x: xp(it.legs[1].arriveTs), y: yp(r3)))
                        }
                        ctx.stroke(Path { p in p.move(to: pts[0]); for q in pts.dropFirst() { p.addLine(to: q) } }, with: .color(Color.red), style: StrokeStyle(lineWidth: 2.5, lineJoin: .round, dash: [6, 4]))
                        for q in pts { ctx.fill(Path(ellipseIn: CGRect(x: q.x - 3.5, y: q.y - 3.5, width: 7, height: 7)), with: .color(Color.red)) }
                    }
                }
                .frame(height: height)
            }
            Text("Dots: trains now. Dashed: the feed's projection; dotted: the prediction engine (red: held, or held back by the train ahead). The red path is your recommended itinerary.").font(.caption2).foregroundStyle(.secondary)
        }
    }
}
