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
    /// Fires when the next boarding time passes: the train that left drops out and the best route is selected afresh.
    @State private var boardingTimer: Task<Void, Never>? = nil
    /// The headline itinerary's boarding time at the last refresh; once it has passed, the next refresh reselects.
    @State private var shownBoardTs: Double? = nil
    /// A held train's effect on the headline route, when it makes a difference.
    @State private var outlook: HoldOutlook? = nil
    /// The route is in progress: GPS put the phone at the origin station, or the rider said so.
    @State private var routeStarted = false
    /// Ended by hand: GPS does not start it again for this trip.
    @State private var routeEndedByHand = false
    /// How the route in progress began: "gps" or "hand".
    @State private var routeStartedBy = "gps"
    @State private var showInsights = false

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
            if !routeStarted { TripActivityService.shared.endAll() }
            recompute()
            autoApply()
        }
        .onChange(of: originId) { _, _ in resetTrip(); recompute() }
        .onChange(of: destId) { _, _ in resetTrip(); recompute() }
        .onChange(of: data.staticVersion) { _, _ in
            recompute()
            autoApply()
            resolveNearest()
        }
        .onChange(of: data.tick) { _, _ in refreshLive(); pollLocation() }
        .onChange(of: data.scenario) { _, _ in refreshLive() }
        .onChange(of: selectedPath) { _, _ in updateFocus() }
        .onChange(of: loc.location?.timestamp) { _, _ in resolveNearest(); checkArrival() }
        .onChange(of: scenePhase) { _, phase in if phase == .active { autoApply() } }
        .onChange(of: routeStarted) { _, on in
            if on { startActivity(); beginTelemetry() }
            else { TripActivityService.shared.end(); Telemetry.shared.endTrip(by: routeEndedByHand ? "hand" : "changed", api: data.apiBase) }
        }
        .onDisappear { boardingTimer?.cancel() }
    }

    private func content(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                CommuteChip(presets: presets.presets, activeId: presets.active(at: data.now)?.id, currentId: currentPresetId,
                            onPick: { applyPreset($0, byHand: true) },
                            onEdit: { editing = $0 },
                            onAdd: { editing = newPresetFromCurrent() })
                pickers(index)
                if pendingNearest {
                    Text(loc.error ?? "Finding the nearest station…").font(.footnote).foregroundStyle(loc.error == nil ? Color.secondary : Color.red)
                } else if originId.isEmpty || destId.isEmpty {
                    SetupPrompt(originSet: !originId.isEmpty, destSet: !destId.isEmpty, reachable: reach.count) { editing = newPresetFromCurrent() }
                }
                if !paths.isEmpty {
                    NowCard(option: headline, originId: originId, atStation: routeStarted, originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "",
                            onInsights: { showInsights = true })
                    tripBar(originName: index.stations[originId]?.name ?? "the station")
                    if let o = outlook { HoldOutlookCard(outlook: o) }
                    if routeStarted, let sel = headline {
                        DepartureBoardView(option: sel, schedule: sched, originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "")
                    }
                    pathList
                    if let sel = paths.first(where: { $0.id == selectedPath }) {
                        let list = ranked
                        let pos = (list.firstIndex { $0.id == sel.id } ?? 0) + 1
                        Text("Route \(pos) of \(list.count) · swipe the route left or right to change")
                            .font(.caption).foregroundStyle(.secondary)
                        PathDetailView(option: sel, schedule: sched, index: index, originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "")
                            .contentShape(Rectangle())
                            .gesture(routeSwipe(enabled: true))
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
            StationPickerSheet(title: "From", stations: index.sorted, reach: nil,
                               nearTo: currentPreset.flatMap { index.station($0.originId) }, coords: commuteCoords(sched, index)) { st in
                originId = st.id
                pickedByHand()
            }
        }
        .sheet(isPresented: $pickingDest) {
            StationPickerSheet(title: "To", stations: index.sorted, reach: reach,
                               nearTo: currentPreset.flatMap { index.station($0.destId) }, coords: commuteCoords(sched, index)) { st in
                destId = st.id
                pickedByHand()
            }
        }
        .sheet(isPresented: $showInsights) {
            if let p = headline {
                RouteInsightsView(option: p, schedule: sched, originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "")
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
        guard let index = data.index, let p = presets.active(at: data.now) else { return }
        let stamp = "\(Fmt.dayStamp(data.now))|\(p.id.uuidString)"
        if appliedPreset == stamp {
            // applied earlier today (maybe in another launch): it is the trip on screen unless a station was picked by hand
            if currentPresetId == nil, destId == index.station(p.destId)?.id, p.useNearestOrigin || originId == index.station(p.originId)?.id {
                currentPresetId = p.id
            }
            return
        }
        appliedPreset = stamp
        applyPreset(p, byHand: false)
    }

    private func applyPreset(_ p: CommutePreset, byHand: Bool) {
        if byHand, let a = presets.active(at: data.now) { appliedPreset = "\(Fmt.dayStamp(data.now))|\(a.id.uuidString)" }
        currentPresetId = p.id
        data.requestGeometry()   // the pickers list the stations near the commute's own while it is on
        if p.useNearestOrigin {
            pendingDest = data.index?.station(p.destId)?.id ?? p.destId
            pendingNearest = true
            loc.request()
            resolveNearest()
        } else {
            pendingNearest = false
            originId = data.index?.station(p.originId)?.id ?? p.originId
            destId = data.index?.station(p.destId)?.id ?? p.destId
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

    /// The commute the planner is on (picking a station by hand leaves it).
    private var currentPreset: CommutePreset? {
        guard let id = currentPresetId else { return nil }
        return presets.presets.first { $0.id == id }
    }

    /// Station coordinates for the pickers' "near the commute's station" section: only with a commute on and
    /// the geometry loaded.
    private func commuteCoords(_ sched: ClientSchedule, _ index: StationIndex) -> [String: (lat: Double, lon: Double)] {
        guard currentPresetId != nil, let geo = data.geometry else { return [:] }
        return stationCoordinates(schedule: sched, index: index, geometry: geo)
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
                IconButton(systemImage: "location.fill", label: "Nearest station") { nearbySheet = true }
            }
            HStack(spacing: 8) {
                StationButton(label: "To", station: index.stations[destId]) { pickingDest = true }
                    .disabled(originId.isEmpty)
                IconButton(systemImage: "arrow.up.arrow.down", label: "Swap stations") {
                    let o = originId
                    originId = destId
                    destId = o
                }
                .disabled(originId.isEmpty || destId.isEmpty)
            }
        }
    }

    /// The route the headline card describes: the chosen one, else the best.
    private var headline: PathOption? { paths.first { $0.id == selectedPath } ?? ranked.first }

    /// Routes with a train in the feeds first, by that itinerary's arrival; the rest by expected time. A route
    /// nobody can board yet never outranks one with a train on its way.
    private var ranked: [PathOption] {
        paths.sorted { a, b in
            switch (a.live, b.live) {
            case let (x?, y?): return x.arriveTs != y.arriveTs ? x.arriveTs < y.arriveTs : a.expectedSec < b.expectedSec
            case (.some, .none): return true
            case (.none, .some): return false
            case (.none, .none): return a.expectedSec < b.expectedSec
            }
        }
    }

    private var pathList: some View {
        let list = ranked
        let maxSec = max(60, list.map { p in max(p.expectedSec, p.live?.totalSec ?? 0) }.max() ?? 60)
        return VStack(alignment: .leading, spacing: 6) {
            Text(routeStarted ? "Other ways" : "\(list.count) way\(list.count == 1 ? "" : "s") to get there").font(.headline)
            ForEach(Array(list.enumerated()), id: \.element.id) { i, p in
                PathRow(option: p, selected: p.id == selectedPath, maxSec: maxSec)
                    .onTapGesture { selectedPath = p.id }
                    .gesture(routeSwipe(enabled: p.id == selectedPath))
            }
            Text("Badge: expected extra minutes to your destination against the timetable — the engine's ride for the train to take, a wait beyond the usual headway, extra time at the change and the risk of missing it; without a train in the feeds, the time typically lost at this hour. Bars: expected door-to-door time — wait (grey), ride (line colour), walk at the change (dark). Routes with a train on its way come first, by arrival.")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    /// A horizontal swipe on the chosen route steps to the next or previous route in the ranked list.
    private func routeSwipe(enabled: Bool) -> some Gesture {
        DragGesture(minimumDistance: 24, coordinateSpace: .local).onEnded { v in
            guard enabled, abs(v.translation.width) > abs(v.translation.height) * 1.5 else { return }
            stepRoute(v.translation.width < 0 ? 1 : -1)
        }
    }

    private func stepRoute(_ delta: Int) {
        let list = ranked
        guard !list.isEmpty else { return }
        let i = list.firstIndex { $0.id == selectedPath } ?? 0
        let j = ((i + delta) % list.count + list.count) % list.count
        withAnimation(.easeInOut(duration: 0.2)) { selectedPath = list[j].id }
    }

    private func recompute() {
        guard let sched = data.schedule, let index = data.index else { return }
        shownBoardTs = nil
        // ids persisted from another launch or export: resolve them to today's station ids (onChange recomputes)
        if !originId.isEmpty, index.station(originId)?.id != originId { originId = index.station(originId)?.id ?? ""; return }
        if !destId.isEmpty, index.station(destId)?.id != destId { destId = index.station(destId)?.id ?? ""; return }
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
        // the countdown on screen reached zero since the last refresh (timer, poll, or coming back to this tab)
        let departed = shownBoardTs.map { $0 <= now } ?? false
        for i in paths.indices {
            paths[i].live = pathTrips(boards: data.predictedBoards, schedule: sched, option: paths[i], now: now, maxN: 1).first
        }
        // a departed train hands over to the best route, or to the next train on this route once it is in progress
        if departed, !routeStarted, let best = ranked.first?.id { selectedPath = best }
        shownBoardTs = headline?.live?.boardTs
        outlook = holdOutlook(sched)
        if outlook == nil, data.scenario != "baseline" { data.setScenario("baseline") }
        updateFocus()
        armBoardingTimer()
        if routeStarted { updateActivity(); updateTelemetry() }
    }

    // MARK: - opt-in trip motion

    private func beginTelemetry() {
        guard Telemetry.shared.optIn, let p = headline else { return }
        let h = routeHealth(p, data: data)
        let it = p.live
        let l0 = it?.legs.first?.train
        let base = TripObservation(
            id: "", installId: "", appVersion: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "", createdTs: 0,
            routeLabel: p.label, legs: p.legs.map { TripObservation.Leg(line: $0.primaryKey, from: $0.from, to: $0.to) },
            transferStation: p.transfer?.station, transferWalkSec: p.transfer?.walkSec,
            predictedBoardTs: it?.boardTs, predictedArriveTs: it?.arriveTs, expectedSec: p.expectedSec, schedSec: p.schedSec,
            extraMin: h.extraSec >= 90 ? h.minutes : 0, trainLateSec: l0?.effectiveLatenessSec, trainHeld: l0?.isHeld ?? false,
            offline: data.offline, startedBy: routeStartedBy)
        Telemetry.shared.beginTrip(base)
    }

    private func updateTelemetry() {
        guard Telemetry.shared.optIn, let p = headline else { return }
        let h = routeHealth(p, data: data)
        let l0 = p.live?.legs.first?.train
        Telemetry.shared.updatePrediction(boardTs: p.live?.boardTs, arriveTs: p.live?.arriveTs, expectedSec: p.expectedSec,
                                          extraMin: h.extraSec >= 90 ? h.minutes : 0, trainLateSec: l0?.effectiveLatenessSec,
                                          held: l0?.isHeld ?? false, offline: data.offline)
    }

    // MARK: - the Live Activity

    private func activityState(_ p: PathOption) -> TripActivityAttributes.ContentState {
        let h = routeHealth(p, data: data)
        let it = p.live
        let l0 = it?.legs.first
        var status: String
        if let l0 = l0 {
            var bits: [String] = []
            if let pos = l0.train.position { bits.append("train now \(pos.text)") }
            if let e = l0.train.effectiveLatenessSec, abs(e) >= 60 { bits.append(Fmt.late(e)) }
            status = bits.joined(separator: " · ")
        } else {
            status = data.predictedBoards.isEmpty ? "waiting for the live feeds" : "no train for this path in the feeds yet"
        }
        var next: Itinerary? = nil
        if let it = it, let sched = data.schedule {
            next = pathTrips(boards: data.predictedBoards, schedule: sched, option: p, now: data.now, maxN: 3).first { $0.boardTs > it.boardTs + 30 }
        }
        return TripActivityAttributes.ContentState(
            route: l0?.train.route ?? p.legs[0].primaryRoute, trainLabel: l0.map { shortLabel($0.train) } ?? "",
            boardTs: it?.boardTs ?? (data.now + p.wait1Sec), arriveTs: it?.arriveTs ?? (data.now + p.expectedSec),
            nextBoardTs: next?.boardTs, nextRoute: next?.legs.first?.train.route,
            changeAt: p.transfer?.station, changeRoutes: p.legs.count > 1 ? p.legs[1].routesLabel : nil,
            extraMin: h.extraSec >= 90 ? h.minutes : 0, level: h.level.rawValue, status: status, offline: data.offline)
    }

    private func startActivity() {
        guard let p = headline, let index = data.index else { return }
        let attrs = TripActivityAttributes(originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "", routeLabel: p.label)
        TripActivityService.shared.start(routeId: p.id, attributes: attrs, state: activityState(p))
    }

    /// Every refresh while the route is in progress; a different chosen route restarts it with the new label.
    private func updateActivity() {
        guard let p = headline else { return }
        if TripActivityService.shared.routeId != p.id { startActivity(); return }
        TripActivityService.shared.update(activityState(p))
    }

    /// The headline route's arrival under each assumption about a held train, when they differ by a minute or
    /// more; nil when no hold matters for it.
    private func holdOutlook(_ sched: ClientSchedule) -> HoldOutlook? {
        guard data.anyHeld, let p = headline else { return nil }
        let now = data.now
        func arrive(_ scenario: String) -> Double? {
            pathTrips(boards: data.predictedBoards(for: scenario), schedule: sched, option: p, now: now, maxN: 1).first?.arriveTs
        }
        let u = arrive("baseline"), d = arrive("hold_persists"), c = arrive("clears_now")
        let vals = [u, d, c].compactMap { $0 }
        guard let lo = vals.min(), let hi = vals.max(), hi - lo >= 60 else { return nil }
        var heldAt = "A train is held"
        for k in p.legs.flatMap({ $0.keys }) {
            if let b = data.boards[k], let t = b.trains.first(where: { $0.isHeld }) {
                heldAt = "\(b.route) held \(t.position?.text ?? "")"
                break
            }
        }
        return HoldOutlook(heldAt: heldAt, usual: u, dragsOn: d, clearsNow: c)
    }

    /// Wake just after the earliest boarding time on screen (the headline route's or the selected one's): that
    /// train is then in the past, the itineraries are recomputed and the best route becomes the selection.
    private func armBoardingTimer() {
        boardingTimer?.cancel()
        guard let next = [ranked.first?.live?.boardTs, headline?.live?.boardTs].compactMap({ $0 }).min() else { boardingTimer = nil; return }
        let delay = max(0.5, next - data.now + 0.5)
        boardingTimer = Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            if Task.isCancelled { return }
            refreshLive()
        }
    }

    // MARK: - the route in progress

    /// Start and end the route by hand; it starts on its own when GPS puts the phone at the origin station.
    private func tripBar(originName: String) -> some View {
        HStack(spacing: 10) {
            if routeStarted {
                Label("At \(originName)", systemImage: "figure.walk.circle.fill").font(.caption.weight(.semibold)).foregroundStyle(Color.green)
                Spacer()
                Button("End route") { routeStarted = false; routeEndedByHand = true }
                    .font(.caption.weight(.semibold)).buttonStyle(.bordered).controlSize(.small)
            } else {
                Text("Starts by itself at \(originName)").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Start route") { routeStartedBy = "hand"; withAnimation { routeStarted = true } }
                    .font(.caption.weight(.semibold)).buttonStyle(.borderedProminent).controlSize(.small)
            }
        }
    }

    /// Metres from the phone's last fix to the origin station, when both are known and the fix is recent.
    private var originDistanceM: Double? {
        guard !originId.isEmpty, let l = loc.location, Date().timeIntervalSince(l.timestamp) < 180,
              let sched = data.schedule, let index = data.index, let geo = data.geometry,
              let c = stationCoordinates(schedule: sched, index: index, geometry: geo)[originId] else { return nil }
        return haversineM((l.coordinate.latitude, l.coordinate.longitude), c)
    }

    /// Within 150 m of the origin station: the route starts.
    private func checkArrival() {
        guard !routeStarted, !routeEndedByHand, !destId.isEmpty, let d = originDistanceM, d <= 150 else { return }
        routeStartedBy = "gps"
        withAnimation { routeStarted = true }
    }

    /// A fresh fix every poll while a trip is on screen and not started yet (only once location is allowed).
    private func pollLocation() {
        guard !routeStarted, !originId.isEmpty, !destId.isEmpty, loc.authorized else { return }
        data.requestGeometry()
        loc.request()
        checkArrival()
    }

    private func resetTrip() {
        routeStarted = false
        routeEndedByHand = false
        TripActivityService.shared.end()
    }

    /// The Line tab follows the selected route: its legs, and the train it boards when the feeds have one.
    private func updateFocus() {
        guard let sel = paths.first(where: { $0.id == selectedPath }), !sel.legs.isEmpty else {
            if data.focus != nil { data.focus = nil }
            return
        }
        let it = sel.live
        var legs: [FocusLeg] = []
        for (i, leg) in sel.legs.enumerated() {
            var spans: [String: StopSpan] = [:]
            for (k, v) in leg.idx { spans[k] = StopSpan(from: v.from, to: v.to) }
            let tc: TripCandidate? = (it?.legs.indices.contains(i) ?? false) ? it?.legs[i] : nil
            legs.append(FocusLeg(key: tc?.key ?? leg.primaryKey, keys: leg.keys, idx: spans, trainId: tc?.train.id))
        }
        let l0 = it?.legs.first
        let f = TrainFocus(key: l0?.key ?? sel.legs[0].primaryKey, trainId: l0?.train.id ?? "", tripId: l0?.train.tripId ?? "", legs: legs)
        if data.focus != f { data.focus = f }
    }
}

private struct BarSeg {
    var sec: Double
    var color: Color
}

struct PathRow: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    let selected: Bool
    let maxSec: Double

    private var health: RouteHealth { routeHealth(option, data: data) }

    var body: some View {
        let h = health
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .center, spacing: 8) {
                legBullets
                Spacer(minLength: 8)
                HStack(spacing: 6) {
                    HealthDot(health: h).fixedSize()
                    Text(Fmt.minTxt(option.live?.totalSec ?? option.expectedSec)).font(.title3.bold()).monospacedDigit()
                }
                .layoutPriority(1)
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(option.label).font(.subheadline.weight(.medium)).foregroundStyle(.primary).lineLimit(1)
                Spacer(minLength: 8)
                Text(liveText).font(.caption).foregroundStyle(Color.primary.opacity(0.7)).lineLimit(1).layoutPriority(1)
            }
            // notes only once the route runs 5 min or more behind
            if h.level != .smooth, !h.reasons.isEmpty {
                Text(h.summary).font(.caption.weight(.medium)).foregroundStyle(h.textColor).lineLimit(2)
            }
            bar
        }
        .padding(10)
        // the tint sits on the same card grey as the other rows, so the selected row is the brighter one in dark mode too
        .background {
            ZStack {
                RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground))
                RoundedRectangle(cornerRadius: 12).fill(Color.accentColor.opacity(selected ? 0.16 : 0))
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(selected ? Color.accentColor : Color.primary.opacity(0.08), lineWidth: 1))
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
        out.append(BarSeg(sec: option.wait1Sec, color: Color.primary.opacity(0.22)))
        out.append(BarSeg(sec: Double(l0.schedRideSec ?? 0) + (l0.typicalSec ?? 0) + l0.holdRiskSec, color: RouteStyle.color(l0.primaryRoute)))
        if option.legs.count > 1, let tr = option.transfer {
            let l1 = option.legs[1]
            if tr.walkSec > 0 { out.append(BarSeg(sec: Double(tr.walkSec), color: Color.primary.opacity(0.7))) }
            out.append(BarSeg(sec: option.wait2Sec, color: Color.primary.opacity(0.22)))
            out.append(BarSeg(sec: Double(l1.schedRideSec ?? 0) + (l1.typicalSec ?? 0) + l1.holdRiskSec, color: RouteStyle.color(l1.primaryRoute)))
        }
        return out.map { BarSeg(sec: max(0, $0.sec), color: $0.color) }
    }

    private var bar: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 3).fill(Color.primary.opacity(0.06))
                HStack(spacing: 1.5) {
                    ForEach(Array(segments.enumerated()), id: \.offset) { _, s in
                        RoundedRectangle(cornerRadius: 3).fill(s.color).frame(width: max(2, w * CGFloat(s.sec / maxSec)))
                    }
                    Spacer(minLength: 0)
                }
            }
        }
        .frame(height: 12)
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
                        out.append("slowest measured stretch: \(s.fromName) → \(s.toName), \(Int(s.medianRunSec.rounded())) s vs \(Int((s.schedRunSec ?? 0).rounded())) s scheduled (\(Fmt.mph(s.speedKmh)))")
                    }
                }
            }
            for a in data.alertsFor(routes: leg.routes).prefix(2) {
                out.append("alert (\(a.kind)) on the \(a.routes.joined(separator: "/")): \(a.header) — \(alertEvidence(a, data: data).text)")
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
                                     layers: layers(line, key), trains: trains(line, key), colW: 56,
                                     scrollTo: focusStop(line, key) ?? max(0, ix.from - 1))
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
        out.append(DiagramLayer(id: "speed", name: measured ? "mph (measured where known)" : "mph (scheduled)", values: speeds, color: Color.teal, format: { "\(Int(($0 * 0.621371).rounded()))" }))
        return out
    }

    /// The stop to scroll to so the train this leg boards is in view (one stop before it), while it is on its way.
    private func focusStop(_ line: LineTopology, _ key: String) -> Int? {
        guard let l = option.live, l.legs.indices.contains(legNo - 1) else { return nil }
        let tc = l.legs[legNo - 1]
        guard let b = data.boards[tc.key], let t = b.trains.first(where: { $0.id == tc.train.id }), let bl = schedule.lines[tc.key] else { return nil }
        var idx = trainProgress(t, age: data.now - b.now, line: bl).idx
        if tc.key != key {
            let j = Int(idx.rounded(.down))
            guard j >= 0, j < bl.stops.count, let pj = line.stops.firstIndex(of: bl.stops[j]) else { return nil }
            idx = Double(pj)
        }
        return max(0, Int(idx.rounded(.down)) - 1)
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
                if sub.isEmpty, let lr = t.lastRun, let sp = lr.speedKmh { sub = Fmt.mph(sp) }
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
        if let seg = leg.train.segment, let sp = seg.schedSpeedKmh { s += " · \(Fmt.mph(sp)) sched" }
        if let lr = leg.train.lastRun, let sp = lr.speedKmh { s += " · last run \(Fmt.mph(sp))" }
        return s
    }
}
