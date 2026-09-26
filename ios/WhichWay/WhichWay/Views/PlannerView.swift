import SwiftUI
import CoreLocation

/// Go tab: origin and destination, every viable path (direct or one change) ranked by expected time and by the
/// next live itinerary, and the selected path's live track diagram, itineraries and insights. Saved commutes
/// switch the trip on their own by time of day; the origin can come from the phone's location.
struct PlannerView: View {
    @Environment(DataService.self) private var data
    @Environment(PresetStore.self) private var presets
    @Environment(LocationService.self) private var loc
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("originId") private var originId = ""
    @AppStorage("destId") private var destId = ""
    /// "<day>|<preset id>" of the last commute applied automatically: once per window per day.
    @AppStorage("appliedPreset") private var appliedPreset = ""
    @State private var selectedPath: String? = nil
    @State private var pickingOrigin = false
    @State private var pickingDest = false
    @State private var nearbySheet = false
    @State private var editing: CommutePreset? = nil
    @State private var currentPresetId: UUID? = nil
    @State private var pendingNearest = false
    @State private var pendingDest = ""
    @State private var paths: [PathOption] = []
    @State private var reach: [String: Reach] = [:]

    var body: some View {
        NavigationStack {
            Group {
                if let sched = data.schedule, let index = data.index {
                    content(sched, index)
                } else if let err = data.lastError {
                    ContentUnavailableView {
                        Label("Could not load", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(err)
                    } actions: {
                        Button("Retry") { data.restart() }
                    }
                } else {
                    ProgressView("Loading the schedule…")
                }
            }
            .navigationTitle("Which way?")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { StatusDot() } }
        }
        .onAppear {
            recompute()
            autoApply()
        }
        .onChange(of: originId) { _, _ in recompute() }
        .onChange(of: destId) { _, _ in recompute() }
        .onChange(of: data.staticVersion) { _, _ in
            recompute()
            autoApply()
            resolveNearest()
        }
        .onChange(of: data.tick) { _, _ in refreshLive() }
        .onChange(of: loc.location?.timestamp) { _, _ in resolveNearest() }
        .onChange(of: scenePhase) { _, phase in if phase == .active { autoApply() } }
    }

    private func content(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                PresetChips(presets: presets.presets, activeId: presets.active(at: data.now)?.id, currentId: currentPresetId,
                            canAdd: !originId.isEmpty && !destId.isEmpty,
                            onPick: { applyPreset($0, byHand: true) },
                            onAdd: { editing = newPresetFromCurrent() })
                pickers(index)
                if pendingNearest {
                    Text(loc.error ?? "Finding the nearest station…").font(.footnote).foregroundStyle(loc.error == nil ? Color.secondary : Color.red)
                }
                if originId.isEmpty {
                    Text("Pick where you are and where you're going. Paths come from the timetable; which train to take, when it gets you there and where it is right now come from the live MTA feeds.")
                        .font(.footnote).foregroundStyle(.secondary)
                } else if destId.isEmpty {
                    Text("\(reach.count) stations are reachable direct or with one change. Pick a destination.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if !paths.isEmpty {
                    NowCard(option: ranked.first, originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "")
                    ScenarioPicker()
                    pathList
                    if let sel = paths.first(where: { $0.id == selectedPath }) {
                        PathDetailView(option: sel, schedule: sched, index: index, originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "")
                    }
                } else if !originId.isEmpty && !destId.isEmpty {
                    Text("No path with at most one change between these stations.").font(.footnote).foregroundStyle(.secondary)
                }
                if let err = data.lastError { Text(err).font(.caption2).foregroundStyle(Color.red) }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .sheet(isPresented: $pickingOrigin) {
            StationPickerSheet(title: "From", stations: index.sorted, reach: nil) { st in
                originId = st.id
                pickedByHand()
            }
        }
        .sheet(isPresented: $pickingDest) {
            StationPickerSheet(title: "To", stations: index.sorted, reach: reach) { st in
                destId = st.id
                pickedByHand()
            }
        }
        .sheet(isPresented: $nearbySheet) {
            NearbyStationsSheet { st in
                originId = st.id
                pickedByHand()
            }
        }
        .sheet(item: $editing) { p in
            PresetEditorView(preset: p) { saved in
                presets.update(saved)
                applyPreset(saved, byHand: true)
            }
        }
    }

    // MARK: - commutes and location

    /// The first commute whose window covers now, once per window per day; stations picked by hand keep.
    private func autoApply() {
        guard data.index != nil, let p = presets.active(at: data.now) else { return }
        let stamp = "\(Fmt.dayStamp(data.now))|\(p.id.uuidString)"
        if appliedPreset == stamp { return }
        appliedPreset = stamp
        applyPreset(p, byHand: false)
    }

    private func applyPreset(_ p: CommutePreset, byHand: Bool) {
        if byHand, let a = presets.active(at: data.now) { appliedPreset = "\(Fmt.dayStamp(data.now))|\(a.id.uuidString)" }
        currentPresetId = p.id
        if p.useNearestOrigin {
            pendingDest = p.destId
            pendingNearest = true
            data.requestGeometry()
            loc.request()
            resolveNearest()
        } else {
            pendingNearest = false
            originId = p.originId
            destId = p.destId
        }
    }

    /// With a fix and the station coordinates: the nearest station from which the destination is reachable with
    /// at most one change (else simply the nearest) becomes the origin.
    private func resolveNearest() {
        guard pendingNearest, let l = loc.location, let sched = data.schedule, let index = data.index, let geo = data.geometry else { return }
        let coords = stationCoordinates(schedule: sched, index: index, geometry: geo)
        let near = nearestStations(to: (l.coordinate.latitude, l.coordinate.longitude), coords: coords, index: index, n: 6)
        let dest = pendingDest
        let pick = near.first(where: { dest.isEmpty || reachableStations(schedule: sched, index: index, from: $0.station.id)[dest] != nil }) ?? near.first
        pendingNearest = false
        guard let s = pick else { return }
        originId = s.station.id
        if !dest.isEmpty { destId = dest }
    }

    private func pickedByHand() {
        currentPresetId = nil
        pendingNearest = false
        if let a = presets.active(at: data.now) { appliedPreset = "\(Fmt.dayStamp(data.now))|\(a.id.uuidString)" }
    }

    private func newPresetFromCurrent() -> CommutePreset {
        let w = PresetStore.suggestedWindow(at: data.now)
        return CommutePreset(name: PresetStore.suggestedName(at: data.now), originId: originId, destId: destId, startMinute: w.start, endMinute: w.end)
    }

    private func pickers(_ index: StationIndex) -> some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                StationButton(label: "From", station: index.stations[originId]) { pickingOrigin = true }
                Button { nearbySheet = true } label: {
                    Image(systemName: "location.fill")
                }
                .buttonStyle(.bordered)
                .accessibilityLabel("Nearest station")
            }
            HStack(spacing: 8) {
                StationButton(label: "To", station: index.stations[destId]) { pickingDest = true }
                    .disabled(originId.isEmpty)
                Button {
                    let o = originId
                    originId = destId
                    destId = o
                } label: {
                    Image(systemName: "arrow.up.arrow.down")
                }
                .buttonStyle(.bordered)
                .disabled(originId.isEmpty || destId.isEmpty)
            }
        }
    }

    private var ranked: [PathOption] {
        let now = data.now
        return paths.sorted { a, b in
            let ta = a.live?.arriveTs ?? (now + a.expectedSec)
            let tb = b.live?.arriveTs ?? (now + b.expectedSec)
            return ta < tb
        }
    }

    private var pathList: some View {
        let list = ranked
        let maxSec = max(60, list.map { p in max(p.expectedSec, p.live?.totalSec ?? 0) }.max() ?? 60)
        return VStack(alignment: .leading, spacing: 6) {
            Text("\(list.count) way\(list.count == 1 ? "" : "s") to get there").font(.headline)
            ForEach(Array(list.enumerated()), id: \.element.id) { i, p in
                PathRow(option: p, rank: i + 1, selected: p.id == selectedPath, maxSec: maxSec)
                    .onTapGesture { selectedPath = p.id }
            }
            Text("Bars: expected door-to-door time — wait (grey), ride (line colour, including the time trains typically lose on that stretch at this hour and the hold risk), walk at the change (dark). Ranked by the next itinerary's arrival once the feeds are in.")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func recompute() {
        guard let sched = data.schedule, let index = data.index else { return }
        if !originId.isEmpty && index.stations[originId] == nil { originId = "" }
        reach = originId.isEmpty ? [:] : reachableStations(schedule: sched, index: index, from: originId)
        if originId.isEmpty {
            paths = []
            return
        }
        if !destId.isEmpty && reach[destId] == nil {
            destId = ""
            paths = []
            return
        }
        var ps = (originId.isEmpty || destId.isEmpty) ? [] : enumeratePaths(schedule: sched, index: index, from: originId, to: destId)
        let now = data.now
        let hour = nyHour(now)
        for i in ps.indices {
            evaluate(&ps[i], schedule: sched, lineSched: data.lineSched, now: now, holds: data.holds, deviations: data.deviations, hour: hour)
        }
        paths = ps
        var keys = Set<String>()
        for p in ps { for l in p.legs { keys.formUnion(l.keys) } }
        data.setWanted(keys, for: "planner")
        if selectedPath == nil || !ps.contains(where: { $0.id == selectedPath }) { selectedPath = ps.first?.id }
        refreshLive()
    }

    private func refreshLive() {
        guard let sched = data.schedule else { return }
        let now = data.now
        for i in paths.indices {
            paths[i].live = pathTrips(boards: data.predictedBoards, schedule: sched, option: paths[i], now: now, maxN: 1).first
        }
    }
}

private struct BarSeg {
    var sec: Double
    var color: Color
}

struct PathRow: View {
    let option: PathOption
    let rank: Int
    let selected: Bool
    let maxSec: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                Text("\(rank)")
                    .font(.caption.bold())
                    .foregroundStyle(selected ? Color.white : Color.primary)
                    .frame(width: 18, height: 18)
                    .background(Circle().fill(selected ? Color.accentColor : Color.secondary.opacity(0.3)))
                legBullets
                Spacer()
                VStack(alignment: .trailing, spacing: 0) {
                    Text(Fmt.minTxt(option.live?.totalSec ?? option.expectedSec)).font(.subheadline.bold())
                    Text(liveText).font(.caption2).foregroundStyle(.secondary)
                }
            }
            Text(option.label).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            bar
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 10).fill(selected ? Color.accentColor.opacity(0.12) : Color(.secondarySystemBackground)))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(selected ? Color.accentColor : Color.clear, lineWidth: 1))
        .contentShape(Rectangle())
    }

    private var liveText: String {
        if let l = option.live { return "next \(Fmt.hhmm(l.boardTs)) → \(Fmt.hhmm(l.arriveTs))" }
        return "expected · \(Fmt.minTxt(Double(option.schedSec))) scheduled ride"
    }

    private var legBullets: some View {
        HStack(spacing: 4) {
            ForEach(Array(option.legs.enumerated()), id: \.offset) { i, leg in
                if i > 0 { Image(systemName: "arrow.right").font(.caption2).foregroundStyle(.secondary) }
                RouteBullets(routes: leg.routes, size: 20)
            }
        }
    }

    private var segments: [BarSeg] {
        var out: [BarSeg] = []
        let l0 = option.legs[0]
        out.append(BarSeg(sec: option.wait1Sec, color: Color.secondary.opacity(0.45)))
        out.append(BarSeg(sec: Double(l0.schedRideSec ?? 0) + (l0.typicalSec ?? 0) + l0.holdRiskSec, color: RouteStyle.color(l0.primaryRoute)))
        if option.legs.count > 1, let tr = option.transfer {
            let l1 = option.legs[1]
            out.append(BarSeg(sec: Double(tr.walkSec), color: Color.primary.opacity(0.7)))
            out.append(BarSeg(sec: option.wait2Sec, color: Color.secondary.opacity(0.45)))
            out.append(BarSeg(sec: Double(l1.schedRideSec ?? 0) + (l1.typicalSec ?? 0) + l1.holdRiskSec, color: RouteStyle.color(l1.primaryRoute)))
        }
        return out.map { BarSeg(sec: max(0, $0.sec), color: $0.color) }
    }

    private var bar: some View {
        GeometryReader { geo in
            let w = geo.size.width
            HStack(spacing: 1) {
                ForEach(Array(segments.enumerated()), id: \.offset) { _, s in
                    RoundedRectangle(cornerRadius: 2).fill(s.color).frame(width: max(2, w * CGFloat(s.sec / maxSec)))
                }
                Spacer(minLength: 0)
            }
        }
        .frame(height: 10)
    }
}

// MARK: - selected path

struct PathDetailView: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    let schedule: ClientSchedule
    let index: StationIndex
    let originName: String
    let destName: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(option.label).font(.headline)
            PathViewsView(option: option, schedule: schedule, originName: originName, destName: destName)
            itineraries
            insights
        }
    }

    private var itineraries: some View {
        let its = pathTrips(boards: data.predictedBoards, schedule: schedule, option: option, now: data.now)
        return VStack(alignment: .leading, spacing: 6) {
            Text("Next itineraries").font(.subheadline.bold())
            if its.isEmpty {
                Text(data.predictedBoards.isEmpty ? "Waiting for the live feeds…" : "No train in the feeds covers this path right now.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(its) { it in ItineraryRow(itinerary: it) }
        }
    }

    private var insights: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Insights").font(.subheadline.bold())
            ForEach(Array(insightLines.enumerated()), id: \.offset) { _, s in
                HStack(alignment: .top, spacing: 6) {
                    Text("•")
                    Text(s)
                }
                .font(.caption)
            }
        }
    }

    private var insightLines: [String] {
        var out: [String] = []
        let hour = nyHour(data.now)
        for leg in option.legs {
            if let t = leg.typicalSec, abs(t) >= 20 {
                out.append("\(leg.routesLabel) stretch typically \(t >= 0 ? "loses" : "gains") \(Int(abs(t).rounded())) s at this hour")
            }
            if leg.holdRiskSec >= 10 { out.append("\(Int(leg.holdRiskSec.rounded())) s expected hold risk on the \(leg.routesLabel)") }
            if let line = schedule.lines[leg.primaryKey], let ix = leg.idx[leg.primaryKey] {
                if let dev = data.deviations[leg.primaryKey] {
                    var worst: (Double, Int)? = nil
                    var i = ix.from + 1
                    while i <= ix.to {
                        if let v = dev.typical(stopId: line.stops[i], hour: hour), v > (worst?.0 ?? 0) { worst = (v, i) }
                        i += 1
                    }
                    if let w = worst, w.1 < line.names.count {
                        out.append("\(leg.routesLabel): trains lose the most time arriving at \(line.names[w.1]) (+\(Int(w.0.rounded())) s per train)")
                    }
                }
                if let segs = data.segments {
                    var slow: SegmentStat? = nil
                    var i = ix.from + 1
                    while i <= ix.to {
                        if let s = segs.byKey["\(leg.primaryKey)|\(line.stops[i])"], let r = s.ratio, r > (slow?.ratio ?? 1.0) { slow = s }
                        i += 1
                    }
                    if let s = slow, let r = s.ratio, r >= 1.15 {
                        out.append("slowest measured stretch: \(s.fromName) → \(s.toName), \(Int(s.medianRunSec.rounded())) s vs \(Int((s.schedRunSec ?? 0).rounded())) s scheduled (\(Fmt.kmh(s.speedKmh)))")
                    }
                }
            }
            for a in data.alertsFor(routes: leg.routes).prefix(2) {
                out.append("alert (\(a.kind)) on the \(a.routes.joined(separator: "/")): \(a.header)")
            }
        }
        if let b = data.boards[option.legs[0].primaryKey] {
            if b.nHolding > 0 || b.nStalled > 0 { out.append("on the \(b.route) right now: \(b.nHolding) held at a stop, \(b.nStalled) overdue between stops") }
            if b.nFeedOptimistic > 0 { out.append("\(b.nFeedOptimistic) train(s) whose feed ETA looks optimistic given where they are") }
        }
        if out.isEmpty { out.append("Nothing unusual on this path right now.") }
        return out
    }
}

struct LegDiagram: View {
    @Environment(DataService.self) private var data
    let leg: PathLeg
    let legNo: Int
    let option: PathOption
    let schedule: ClientSchedule

    var body: some View {
        let key = leg.primaryKey
        if let line = schedule.lines[key], let ix = leg.idx[key], ix.from < line.names.count, ix.to < line.names.count {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text("Leg \(legNo)").font(.subheadline.bold())
                    RouteBullets(routes: leg.routes, size: 18)
                    Text("\(line.names[ix.from]) → \(line.names[ix.to]) · \(leg.nStops) stops · \(Fmt.minTxt(leg.schedRideSec.map(Double.init))) scheduled")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                TimelineView(.periodic(from: .now, by: 1)) { _ in
                    TrackDiagramView(line: line, route: leg.primaryRoute, fromIdx: ix.from, toIdx: ix.to,
                                     layers: layers(line, key), trains: trains(line, key), colW: 56, scrollTo: max(0, ix.from - 1))
                }
            }
        }
    }

    private func layers(_ line: LineTopology, _ key: String) -> [DiagramLayer] {
        var out: [DiagramLayer] = []
        let hour = nyHour(data.now)
        if let dev = data.deviations[key] {
            let vals: [Double?] = line.stops.map { sid in dev.typical(stopId: sid, hour: hour).map { v in max(0, v) } }
            out.append(DiagramLayer(id: "typical", name: "typical +s at this hour", values: vals, color: Color.blue, format: { "+\(Int($0.rounded()))" }))
        }
        if let h = data.holds, !h.byStop.isEmpty {
            var m: [String: Double] = [:]
            for s in h.byStop { m[s.stopId] = s.perDay }
            out.append(DiagramLayer(id: "holds", name: "holds/day", values: line.stops.map { m[$0] }, color: Color.orange, format: { String(format: "%.1f", $0) }))
        }
        var speeds: [Double?] = [nil]
        var i = 1
        while i < line.stops.count {
            var v: Double? = nil
            if let s = data.segments?.byKey["\(key)|\(line.stops[i])"], let sp = s.speedKmh {
                v = sp
            } else if i - 1 < line.distM.count, i - 1 < line.runSec.count, let d = line.distM[i - 1], let r = line.runSec[i - 1], r > 0 {
                v = Double(d) / Double(r) * 3.6
            }
            speeds.append(v)
            i += 1
        }
        let measured = (data.segments?.n ?? 0) > 0
        out.append(DiagramLayer(id: "speed", name: measured ? "km/h (measured where known)" : "km/h (scheduled)", values: speeds, color: Color.teal, format: { "\(Int($0.rounded()))" }))
        return out
    }

    private func trains(_ line: LineTopology, _ key: String) -> [DiagramTrain] {
        var out: [DiagramTrain] = []
        let nowTs = data.now
        for k in leg.keys {
            guard let b = data.boards[k], let bl = schedule.lines[k] else { continue }
            let age = nowTs - b.now
            for t in b.trains {
                let p = trainProgress(t, age: age, line: bl)
                var idx = p.idx
                if k != key {
                    // a parallel route's train, placed on this line's diagram by stop id
                    let j = Int(idx.rounded(.down))
                    guard j >= 0, j < bl.stops.count, let pj = line.stops.firstIndex(of: bl.stops[j]) else { continue }
                    if j + 1 < bl.stops.count, let pn = line.stops.firstIndex(of: bl.stops[j + 1]), pn == pj + 1 {
                        idx = Double(pj) + (idx - Double(j))
                    } else {
                        idx = Double(pj)
                    }
                }
                var emphasis: String? = nil
                if let l = option.live, l.legs.indices.contains(legNo - 1), l.legs[legNo - 1].train.id == t.id {
                    emphasis = legNo == 1 ? "origin" : "connection"
                }
                var sub = ""
                if let e = t.effectiveLatenessSec, abs(e) >= 60 { sub = Fmt.late(e) }
                if sub.isEmpty, let lr = t.lastRun, let sp = lr.speedKmh { sub = Fmt.kmh(sp) }
                out.append(DiagramTrain(id: t.id, idx: idx, state: p.state, route: t.route, label: shortLabel(t), sub: sub, emphasis: emphasis))
            }
        }
        return out
    }
}

func shortLabel(_ t: LiveTrain) -> String {
    if let id = t.trainId {
        let parts = id.split(separator: " ").map(String.init)
        if parts.count >= 2 { return parts[0] + " " + parts[1] }
        return id
    }
    return String(tripSuffix(t.tripId).prefix(10))
}

struct ItineraryRow: View {
    let itinerary: Itinerary

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text("\(Fmt.hhmm(itinerary.boardTs)) → \(Fmt.hhmm(itinerary.arriveTs))").font(.subheadline.bold())
                Spacer()
                Text(Fmt.minTxt(itinerary.totalSec)).font(.subheadline)
                Text(Fmt.signed(itinerary.rideVsSchedSec) + " vs sched")
                    .font(.caption2)
                    .foregroundStyle(itinerary.rideVsSchedSec > 120 ? Color.red : Color.secondary)
            }
            if let last = itinerary.legs.last, let rt = last.rangeText {
                Text("80% window \(rt)").font(.caption2).foregroundStyle(.secondary)
            }
            ForEach(Array(itinerary.legs.enumerated()), id: \.offset) { i, leg in
                HStack(spacing: 6) {
                    RouteBullet(route: leg.train.route, size: 16)
                    Text(shortLabel(leg.train)).font(.caption.monospaced())
                    Text(positionText(leg)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    if let e = leg.train.effectiveLatenessSec {
                        Text(Fmt.late(e)).font(.caption2).foregroundStyle(abs(e) >= 180 ? Color.orange : Color.secondary)
                    }
                }
                if i == 0, let m = itinerary.connectionMarginSec {
                    Text(changeText(margin: m)).font(.caption2).foregroundStyle(m < 60 ? Color.red : Color.secondary)
                }
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color(.secondarySystemBackground)))
    }

    private func changeText(margin: Double) -> String {
        var s = "change: \(Fmt.mmss(itinerary.waitAtTransferSec ?? 0)) on the platform · margin \(Fmt.mmss(margin))"
        if let n = itinerary.nextIfMissedSec { s += " · next if missed +\(Fmt.mmss(n))" }
        return s
    }

    private func positionText(_ leg: TripCandidate) -> String {
        var s = leg.train.position?.text ?? "position unknown"
        if let seg = leg.train.segment, let sp = seg.schedSpeedKmh { s += " · \(Fmt.kmh(sp)) sched" }
        if let lr = leg.train.lastRun, let sp = lr.speedKmh { s += " · last run \(Fmt.kmh(sp))" }
        return s
    }
}
