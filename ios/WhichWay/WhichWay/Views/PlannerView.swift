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
    /// Ended by hand: GPS does not start it again for this trip.
    @State private var routeEndedByHand = false
    /// Checks the route in progress against the clock every 15 s.
    @State private var tripTimer: Task<Void, Never>? = nil
    /// Why the last route ended on its own, shown until the next one.
    @State private var lastEndNote: String? = nil
    /// The trip the rider's history put on screen for this hour.
    @State private var habitNote: String? = nil
    /// When a station was last picked by hand: the history does not override a choice for two hours.
    @AppStorage("pickedByHandTs") private var pickedByHandTs = 0.0
    @State private var showInsights = false
    @Environment(PlaceStore.self) private var places

    private var trip: TripRecorder { TripRecorder.shared }
    private var routeStarted: Bool { trip.phase != nil }
    @State private var approachFixes: [(ts: Double, d: Double)] = []
    /// Routes found for a line the rider boarded off the plan, beyond the listed options; kept while the route is on.
    @State private var extraPaths: [PathOption] = []
    /// When the route last switched to the line the phone believes the rider boarded: one automatic switch a minute.
    @State private var lastSwitchTs = 0.0
    /// While the route is on: on the train, the itinerary of the train the rider is on (the one the phone settled
    /// on, else the plan's) and the connection it makes; between trains at the change, the connection from there.
    /// Nil on the way to the origin and at its platform, where the planner's next itinerary stands.
    @State private var rideItinerary: Itinerary? = nil
    /// Per leg, the train the plan has the rider on: the planner's itinerary's train for the leg, kept up until the
    /// rider is at that leg's platform and its boarding time has passed (they presumably took it), then frozen.
    @State private var plannedTrains: [Int: BoardingCandidate] = [:]
    /// The train the recorder's forecast names (the planner's live train, as of the last poll the forecast followed it).
    /// When the planner moves on from it while the rider stands at the platform, that train has left.
    @State private var forecastTrainId: String? = nil
    /// Legs whose plan's train froze because the feed dropped it just before its time (`forecastTrainDeparted`), ahead
    /// of the moment the time-based freeze in `followPlan` would have reached.
    @State private var frozenLegs: Set<Int> = []
    /// The phone's belief in a line off the plan must be at least this sure before the route switches on its own.
    private let autoSwitchConfidence = 0.75
    /// The rider's own word on the train they are on (and the line to take at the change).
    @State private var onTrainSheet = false
    /// Per leg, the line the rider said they will take where a leg allows several (the A rather than the C).
    @State private var preferredKeys: [Int: String] = [:]
    /// The earlier Go tab: the title, the pickers, the full card, the route list and the five views on one page.
    @AppStorage("classicGo") private var classicGo = false
    /// The paged Go tab's page: home, the line view, the routes.
    @State private var page = 0
    /// A route was picked on the routes page: the next swipe back goes straight home, skipping the line view.
    @State private var routeChosen = false
    /// The five detail views, the itineraries and the departure board, as a sheet from the line view.
    @State private var showDetails = false

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
            .toolbar(.hidden, for: .navigationBar)
        }
        .onAppear {
            if loc.authorized { loc.startTracking() }
            if !routeStarted { TripActivityService.shared.endAll() }
            recompute()
            applyHabit()
            autoApply()
        }
        .onChange(of: originId) { _, _ in resetTrip(); recompute() }
        .onChange(of: destId) { _, _ in resetTrip(); recompute() }
        .onChange(of: data.staticVersion) { _, _ in
            recompute()
            autoApply()
            applyHabit()
            resolveNearest()
        }
        .onChange(of: data.tick) { _, _ in refreshLive(); pollLocation() }
        .onChange(of: data.scenario) { _, _ in refreshLive() }
        .onChange(of: selectedPath) { _, _ in
            updateFocus()
            if routeStarted { adoptSelectedRoute() }     // a route picked while on the way is the one the trip follows from here
        }
        .onChange(of: loc.location?.timestamp) { _, _ in resolveNearest(); feedLocation() }
        .onChange(of: loc.status) { _, _ in if loc.authorized { loc.startTracking() } }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                autoApply(); applyHabit()
                if loc.authorized { loc.startTracking() }         // the walk to the station is watched from the moment the app opens
                if routeStarted { trip.tick(now: data.now) }
                // trips recorded off the home network go to the Mac the next time the app opens on it
                if Telemetry.shared.pendingUpload > 0, let u = Telemetry.shared.uploadURL(fallback: data.apiBase) {
                    Task { await Telemetry.shared.upload(to: u) }
                }
                // and to the private data repository (through the relay, or the rider's own token), from wherever the phone is
                if TripRepository.configured { Task { await Telemetry.shared.uploadToGitHub() } }
            case .background:
                if !routeStarted { loc.stopTracking() }           // a route in progress keeps the fixes coming
            default: break
            }
        }
        .onChange(of: trip.beliefs) { _, _ in followBoarded() }
        .onChange(of: trip.offPlanAlighting) { _, a in if let a { followAlighting(a) } }
        .onChange(of: trip.stayedOn) { _, s in if let s { followStayingOn(s) } }
        .sheet(isPresented: $onTrainSheet) { onTrainSheetView }
        .onChange(of: trip.phase) { _, ph in
            if ph == .arrived { endTrip(by: trip.endReason ?? "arrived") }
            // on a train by the phone's own reading, with no departure to name it: ask which
            if ph == .riding, trip.needsTrainPick, !onTrainSheet { openOnTrain() }
        }
        .onDisappear { boardingTimer?.cancel() }
    }

    private func content(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        Group {
            if classicGo { classicContent(sched, index) } else { pagedContent(sched, index) }
        }
        .sheet(isPresented: $pickingOrigin) {
            StationPickerSheet(title: "From", stations: index.sorted, reach: nil,
                               nearTo: currentPreset.flatMap { index.station($0.originId) }, coords: commuteCoords(sched, index), places: places.picks(index)) { st in
                originId = st.id
                pickedByHand()
            }
        }
        .sheet(isPresented: $pickingDest) {
            StationPickerSheet(title: "To", stations: index.sorted, reach: reach,
                               nearTo: currentPreset.flatMap { index.station($0.destId) }, coords: commuteCoords(sched, index), places: places.picks(index)) { st in
                destId = st.id
                pickedByHand()
            }
        }
        .sheet(isPresented: $showInsights) {
            if let p = headline {
                RouteInsightsView(option: p, schedule: sched, originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "")
            }
        }
        .sheet(isPresented: $showDetails) { detailsSheet(sched, index) }
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

    // MARK: - the paged Go tab

    /// Three pages side by side: home (the countdown and the numbers), the line view, the routes. A route picked
    /// on the routes page takes the line view out of the stack, so the swipe back lands on home.
    private func pagedContent(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        TabView(selection: $page) {
            homePage(sched, index).tag(0)
            if !routeChosen { linePage(sched, index).tag(1) }
            routesPage(sched, index).tag(2)
        }
        .tabViewStyle(.page(indexDisplayMode: .never))
        .background(alignment: .topLeading) { cornerGlow }
        .onChange(of: page) { _, p in if p == 0 { routeChosen = false } }
    }

    private func homePage(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        let originName = index.stations[originId]?.name ?? ""
        let destName = index.stations[destId]?.name ?? ""
        return ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                homeHeader(index)
                if let n = habitNote, !routeStarted {
                    Label(n, systemImage: "clock.arrow.circlepath").font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                if pendingNearest {
                    Text(loc.error ?? "Finding the nearest station…").font(.footnote).foregroundStyle(loc.error == nil ? Color.secondary : Color.red)
                } else if originId.isEmpty || destId.isEmpty {
                    SetupPrompt(originSet: !originId.isEmpty, destSet: !destId.isEmpty, reachable: reach.count) { editing = newPresetFromCurrent() }
                }
                if let p = headline {
                    let here = nearbyPlace()
                    let placeUsualSec = here.flatMap { PersonalModelStore.shared.model.placeToStationSec(place: $0.id.uuidString, station: originId) }
                    let ride = routeStarted ? rideItinerary : nil
                    let others = ranked.filter { $0.id != p.id }
                    let delay = delayGlow(routeHealth(p, data: data))
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        let now = data.now
                        let it = ride ?? p.live
                        let urgency = glowUrgency(it, now: now, placeUsualSec: placeUsualSec)
                        HomeCard(option: p, itinerary: it, next: nextItineraryAfter(ride: ride, onTrain: trip.onTrain, option: p, data: data), now: now,
                                 originName: originName, destName: destName, phase: trip.phase, onTrain: trip.onTrain, rideLeg: trip.currentLeg,
                                 ridingRoute: ridingRoute, ridePresumed: ridePresumed,
                                 walk: walkToOrigin.map { walkLineText($0, originId: originId, placeName: here?.name, placeUsualSec: placeUsualSec, boardTs: it?.boardTs, now: now) },
                                 alternatives: Array(others.prefix(3)), moreCount: max(0, others.count - 3),
                                 onPick: { selectedPath = $0.id }, onMore: { withAnimation { page = 2 } },
                                 urgency: urgency, delay: delay)
                    }
                    .padding(.top, 10)
                    tripBar(originName: originName.isEmpty ? "the station" : originName, glow: .accentColor).padding(.top, 8)
                    if let o = outlook { HoldOutlookCard(outlook: o) }
                } else if !originId.isEmpty && !destId.isEmpty {
                    Text("No path with at most one change between these stations.").font(.footnote).foregroundStyle(.secondary)
                }
                if let err = data.lastError { Text(err).font(.caption2).foregroundStyle(Color.red) }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 24)
        }
        .onAppear {
            data.requestGeometry()
            loc.request()
        }
    }

    /// The whisper glow from the page's top-left corner, in the colour of the time to the train, kept up every second.
    private var cornerGlow: some View {
        let p = headline
        let ride = routeStarted ? rideItinerary : nil
        let placeUsualSec = nearbyPlace().flatMap { PersonalModelStore.shared.model.placeToStationSec(place: $0.id.uuidString, station: originId) }
        return TimelineView(.periodic(from: .now, by: 1)) { _ in
            GlowWash(color: glowUrgency(ride ?? p?.live, now: data.now, placeUsualSec: placeUsualSec), level: page == 0 ? .whisper : .soft)
        }
        .animation(.easeInOut(duration: 0.35), value: page)
        .ignoresSafeArea()
    }

    /// The glow on the chosen route's row: the time to its train, as on the home page; none on the classic page.
    private var routeRowGlow: Color? {
        guard !classicGo else { return nil }
        let placeUsualSec = nearbyPlace().flatMap { PersonalModelStore.shared.model.placeToStationSec(place: $0.id.uuidString, station: originId) }
        return glowUrgency((routeStarted ? rideItinerary : nil) ?? headline?.live, now: data.now, placeUsualSec: placeUsualSec)
    }

    /// Where from and where to on one line, each a tap to change, with swap and nearest beside; the commute and
    /// the feed's freshness under.
    private func homeHeader(_ index: StationIndex) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 8) {
                Button { pickingOrigin = true } label: {
                    Text(index.stations[originId]?.name ?? "Choose a station").font(.title3.weight(.semibold))
                        .foregroundStyle(originId.isEmpty ? Color.accentColor : Color.primary).lineLimit(1).minimumScaleFactor(0.75)
                }
                .buttonStyle(.plain).accessibilityLabel("From")
                Text("to").foregroundStyle(.secondary)
                Button { pickingDest = true } label: {
                    Text(index.stations[destId]?.name ?? "where?").font(.title3.weight(.semibold))
                        .foregroundStyle(destId.isEmpty ? Color.accentColor : Color.primary).lineLimit(1).minimumScaleFactor(0.75)
                }
                .buttonStyle(.plain).disabled(originId.isEmpty).accessibilityLabel("To")
                Spacer(minLength: 8)
                Button {
                    let o = originId
                    originId = destId
                    destId = o
                } label: {
                    Image(systemName: "arrow.up.arrow.down").font(.subheadline.weight(.semibold)).foregroundStyle(Color.primary)
                        .frame(width: 34, height: 34).background(Circle().fill(Color(.secondarySystemBackground)))
                }
                .buttonStyle(.plain).disabled(originId.isEmpty || destId.isEmpty).accessibilityLabel("Swap stations")
                Button { nearbySheet = true } label: {
                    Image(systemName: "location.fill").font(.subheadline.weight(.semibold)).foregroundStyle(Color.primary)
                        .frame(width: 34, height: 34).background(Circle().fill(Color(.secondarySystemBackground)))
                }
                .buttonStyle(.plain).accessibilityLabel("Nearest station")
            }
            HStack(alignment: .center, spacing: 12) {
                CommuteChip(presets: presets.presets, activeId: presets.active(at: data.now)?.id, currentId: currentPresetId,
                            onPick: { applyPreset($0, byHand: true) },
                            onEdit: { editing = $0 },
                            onAdd: { editing = newPresetFromCurrent() })
                Spacer(minLength: 8)
                StatusDot(chip: true).fixedSize()
            }
        }
        .padding(.top, 12)
    }

    /// The line and routes pages' title: where from and where to, and a word on the right.
    private func pageTitle(_ index: StationIndex, caption: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(index.stations[originId]?.name ?? "").font(.title3.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.75)
            Text("to").foregroundStyle(.secondary)
            Text(index.stations[destId]?.name ?? "").font(.title3.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.75)
            Spacer(minLength: 8)
            Text(caption).font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(.top, 12)
    }

    private func linePage(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        let originName = index.stations[originId]?.name ?? ""
        let destName = index.stations[destId]?.name ?? ""
        return ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                pageTitle(index, caption: headline.map { Fmt.minTxt($0.live?.totalSec ?? $0.expectedSec) } ?? "")
                if let p = headline {
                    let ride = routeStarted ? rideItinerary : nil
                    let placeUsualSec = nearbyPlace().flatMap { PersonalModelStore.shared.model.placeToStationSec(place: $0.id.uuidString, station: originId) }
                    let delay = delayGlow(routeHealth(p, data: data))
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        let it = ride ?? p.live
                        StrandView(option: p, itinerary: it, next: nextItineraryAfter(ride: ride, onTrain: trip.onTrain, option: p, data: data), now: data.now,
                                   originName: originName, destName: destName, onTrain: trip.onTrain, rideLeg: trip.currentLeg,
                                   ridingRoute: ridingRoute, ridePresumed: ridePresumed,
                                   alternatives: Array(ranked.filter { $0.id != p.id }.prefix(4)),
                                   onPick: { selectedPath = $0.id }, onDetails: { showDetails = true }, onInsights: { showInsights = true },
                                   urgency: glowUrgency(it, now: data.now, placeUsualSec: placeUsualSec), delay: delay)
                    }
                    .padding(.top, 10)
                } else {
                    Text("Pick where you are and where you're going.").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 24)
        }
    }

    private func routesPage(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                pageTitle(index, caption: "")
                Text("Tap a route to take it, then swipe back.").font(.caption).foregroundStyle(.secondary)
                if !paths.isEmpty {
                    pathList
                } else if !originId.isEmpty && !destId.isEmpty {
                    Text("No path with at most one change between these stations.").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 24)
        }
    }

    /// The route's five views, its next itineraries and insights, and the departure board while a route is on: the
    /// classic page's detail, as a sheet.
    private func detailsSheet(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        let originName = index.stations[originId]?.name ?? ""
        let destName = index.stations[destId]?.name ?? ""
        return NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let sel = headline {
                        if routeStarted { DepartureBoardView(option: sel, schedule: sched, originName: originName, destName: destName) }
                        PathDetailView(option: sel, schedule: sched, index: index, originName: originName, destName: destName)
                    }
                }
                .padding(16)
            }
            .navigationTitle("Route detail")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { showDetails = false } } }
        }
    }

    /// The colour of the glow on the countdown: from the time to the train (on the train, to the stop to get off at)
    /// less the walk still to make, while on the way; blue when nothing is known.
    private func glowUrgency(_ it: Itinerary?, now: Double, placeUsualSec: Double?) -> Color {
        guard let it, let l0 = it.legs.first else { return .accentColor }
        let left = max(0, (trip.onTrain ? l0.arriveTs : it.boardTs) - now)
        var walkSec: Double? = nil
        if trip.phase == nil || trip.phase == .approaching, let w = walkToOrigin {
            let pm = PersonalModelStore.shared.model
            walkSec = placeUsualSec ?? (w.meters / pm.walkSpeedMPerMin * 60 + (pm.accessSec(station: originId) ?? 0))
        }
        return boardingUrgency(secondsLeft: left, walkSec: walkSec)
    }

    /// The walk from the phone to the origin station, when the phone's position and the station's are known.
    private var walkToOrigin: NearbyStation? {
        guard !originId.isEmpty, let l = loc.location, let sched = data.schedule, let index = data.index, let geo = data.geometry,
              let st = index.stations[originId], let c = stationCoordinates(schedule: sched, index: index, geometry: geo)[originId] else { return nil }
        return NearbyStation(station: st, meters: haversineM((l.coordinate.latitude, l.coordinate.longitude), c))
    }

    // MARK: - the classic Go tab

    /// The earlier Go tab, kept whole: the title, the pickers, the full card, the route list and the five views on
    /// one page, a sideways swipe stepping through the routes (Settings → Go tab → Classic).
    private func classicContent(_ sched: ClientSchedule, _ index: StationIndex) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Which way?").font(.largeTitle.bold()).lineLimit(1).minimumScaleFactor(0.8).padding(.top, 8)
                HStack(alignment: .center, spacing: 12) {
                    CommuteChip(presets: presets.presets, activeId: presets.active(at: data.now)?.id, currentId: currentPresetId,
                                onPick: { applyPreset($0, byHand: true) },
                                onEdit: { editing = $0 },
                                onAdd: { editing = newPresetFromCurrent() })
                    Spacer(minLength: 8)
                    StatusDot(chip: true).fixedSize()
                }
                pickers(index)
                if let n = habitNote, !routeStarted {
                    Label(n, systemImage: "clock.arrow.circlepath").font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                if pendingNearest {
                    Text(loc.error ?? "Finding the nearest station…").font(.footnote).foregroundStyle(loc.error == nil ? Color.secondary : Color.red)
                } else if originId.isEmpty || destId.isEmpty {
                    SetupPrompt(originSet: !originId.isEmpty, destSet: !destId.isEmpty, reachable: reach.count) { editing = newPresetFromCurrent() }
                }
                if !paths.isEmpty {
                    let here = nearbyPlace()
                    NowCard(option: headline, originId: originId, phase: trip.phase,
                            placeName: here?.name, placeUsualSec: here.flatMap { PersonalModelStore.shared.model.placeToStationSec(place: $0.id.uuidString, station: originId) },
                            ride: routeStarted ? rideItinerary : nil, rideLeg: trip.currentLeg, onTrain: trip.onTrain, ridingRoute: ridingRoute, ridePresumed: ridePresumed,
                            originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "",
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
                        Text("Route \(pos) of \(list.count) · swipe left or right to change")
                            .font(.caption).foregroundStyle(.secondary)
                        PathDetailView(option: sel, schedule: sched, index: index, originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "")
                    }
                } else if !originId.isEmpty && !destId.isEmpty {
                    Text("No path with at most one change between these stations.").font(.footnote).foregroundStyle(.secondary)
                }
                if let err = data.lastError { Text(err).font(.caption2).foregroundStyle(Color.red) }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
            // a sideways swipe anywhere on the page steps through the routes; scrolling is untouched
            .background(SwipeCatcher { stepRoute($0) })
        }
    }

    // MARK: - commutes and location

    /// The first commute whose window covers now, once per window per day; stations picked by hand keep.
    private func autoApply() {
        // never under a route in progress: re-picking the stations ends it (Oct 7: a commute's "nearest station"
        // moved the origin to Jay St while the rider was on the F home, and the route ended 8 minutes early). It
        // applies the next time the app comes back with no route on.
        guard !routeStarted, let index = data.index, let p = presets.active(at: data.now) else { return }
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
        guard pendingNearest, !routeStarted, let l = loc.location, let sched = data.schedule, let index = data.index, let geo = data.geometry else { return }
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
        pickedByHandTs = data.now
        habitNote = nil
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
        let glow = routeRowGlow
        return VStack(alignment: .leading, spacing: 6) {
            Text(routeStarted ? "Other ways" : "\(list.count) way\(list.count == 1 ? "" : "s") to get there").font(.headline)
            ForEach(Array(list.enumerated()), id: \.element.id) { i, p in
                Button { selectedPath = p.id; routeChosen = true } label: {
                    PathRow(option: p, selected: p.id == selectedPath, maxSec: maxSec, glow: glow)
                }
                .buttonStyle(.plain)
            }
            Text("Badge: expected extra minutes to your destination against the timetable — the engine's ride for the train to take, a wait beyond the usual headway, extra time at the change and the risk of missing it; without a train in the feeds, the time typically lost at this hour. Bars: expected door-to-door time — wait (grey), ride (line colour), walk at the change (dark). Routes with a train on its way come first, by arrival.")
                .font(.caption2).foregroundStyle(.secondary)
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
        // a route found for the line the rider boarded stays listed for the rest of that trip
        if routeStarted { for e in extraPaths where !ps.contains(where: { $0.id == e.id }) { ps.append(e) } } else { extraPaths = [] }
        applyPersonalTransfers(&ps)
        let now = data.now
        if !ps.isEmpty {
            let l = loc.location.flatMap { Date().timeIntervalSince($0.timestamp) < 600 ? $0 : nil }
            HabitStore.shared.record(origin: originId, dest: destId, ts: now, lat: l?.coordinate.latitude, lon: l?.coordinate.longitude)
        }
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

    /// The rider's own changes, where learned at the station, replace the MTA's minimum either way: longer when
    /// they take longer, shorter where the timetable's figure is a station-wide allowance (Oct 8: 3:00 booked at
    /// Jay St against 0:17 measured, so every route through it ran three minutes late and ranked behind W 4 St).
    private func applyPersonalTransfers(_ ps: inout [PathOption]) {
        let pm = PersonalModelStore.shared.model
        for i in ps.indices {
            guard var tr = ps[i].transfer else { continue }
            let sec = pm.plannedTransferSec(station: tr.station, scheduled: tr.walkSec)
            if sec != tr.walkSec { ps[i].schedSec += sec - tr.walkSec; tr.walkSec = sec; ps[i].transfer = tr }
        }
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
        if routeStarted {
            // the planner moved on from the train the forecast named while the rider stood at the platform: it left (the
            // feed drops a train a few seconds before its predicted platform moment, Oct 9 at 3:00:49 against 3:01:05),
            // so the forecast freezes on it and the plan's train for the leg is kept as it was
            let liveId = headline?.live?.legs.first?.train.id
            if let fid = forecastTrainId, liveId != fid, trip.phase == .atStation, trip.forecastTrainDeparted(now: now) {
                frozenLegs.insert(trip.currentLeg)
            }
            followPlan(now: now)
            trip.observeBoards(data.boards, now: now)
            refreshRideArrival()
            updateActivity(); updateTelemetry()
            trip.updateForecast(boardTs: headline?.live?.boardTs, arriveTs: headline?.live?.arriveTs, now: now)
            if trip.forecastBoardTs == headline?.live?.boardTs { forecastTrainId = liveId }
            trip.tick(now: now)
        }
    }

    /// The train the plan has the rider on, per leg, as the planner's itinerary stands: it keeps up while the rider
    /// is on the way to a leg's platform, and freezes once they are at it and the train's boarding time has passed
    /// (the planner moves on to the next train; the rider presumably took this one). The recorder's plan follows,
    /// so a ride it assumes from the timetable is this train, not the one that was next when the route began.
    private func followPlan(now: Double) {
        guard let p = headline, let it = p.live else { return }
        let leg = trip.currentLeg
        for i in p.legs.indices where i >= leg {
            if i == leg, trip.onTrain { continue }
            let atPlatform = i == leg && (trip.phase == .atStation || (i > 0 && trip.phase == .riding))
            if atPlatform, let kept = plannedTrains[i], frozenLegs.contains(i) || now >= PlatformTiming.atPlatform(kept.boardTs, route: kept.route) { continue }
            if let c = plannedCandidate(it, option: p, leg: i) { plannedTrains[i] = c }
        }
        var named: [Int: (key: String, trainId: String)] = [:]
        for (i, c) in plannedTrains { named[i] = (c.key, c.trainId) }
        trip.updatePlannedTrains(named, now: now)
    }

    /// The line the rider is on, as the phone knows it: the belief's, else the plan's train's.
    private var ridingRoute: String? {
        guard trip.onTrain else { return nil }
        if let b = trip.currentBelief, b.settled || b.byHand, let r = b.route { return r }
        return plannedTrains[trip.currentLeg]?.route ?? rideItinerary?.legs.first?.train.route
    }

    /// The ride stands on the timetable alone: no train felt, named or settled on.
    private var ridePresumed: Bool {
        guard trip.onTrain else { return false }
        if let b = trip.currentBelief, b.settled || b.byHand { return b.assumed }
        return true
    }

    // MARK: - opt-in trip motion

    private func observationBase(_ p: PathOption, startedBy: String) -> TripObservation {
        let h = routeHealth(p, data: data)
        let it = p.live
        let l0 = it?.legs.first?.train
        let base = TripObservation(
            id: "", installId: "", appVersion: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "", createdTs: 0,
            routeLabel: p.label, legs: p.legs.map { TripObservation.Leg(line: $0.primaryKey, from: $0.from, to: $0.to) },
            transferStation: p.transfer?.station, transferWalkSec: p.transfer?.walkSec,
            predictedBoardTs: it?.boardTs, predictedArriveTs: it?.arriveTs, expectedSec: p.expectedSec, schedSec: p.schedSec,
            extraMin: h.extraSec >= 90 ? h.minutes : 0, trainLateSec: l0?.effectiveLatenessSec, trainHeld: l0?.isHeld ?? false,
            offline: data.offline, startedBy: startedBy)
        return base
    }

    private func updateTelemetry() {
        guard Telemetry.shared.optIn, let p = headline else { return }
        let h = routeHealth(p, data: data)
        let l0 = p.live?.legs.first?.train
        Telemetry.shared.updatePrediction(boardTs: p.live?.boardTs, arriveTs: p.live?.arriveTs, expectedSec: p.expectedSec,
                                          extraMin: h.extraSec >= 90 ? h.minutes : 0, trainLateSec: l0?.effectiveLatenessSec,
                                          held: l0?.isHeld ?? false, offline: data.offline, departed: trip.phase == .riding)
    }

    // MARK: - the Live Activity

    private func activityState(_ p: PathOption) -> TripActivityAttributes.ContentState {
        let h = routeHealth(p, data: data)
        // on the train, the ride in progress (its own arrival and connection); between trains, the connection;
        // before the route is under way, the planner's next itinerary
        let it = rideItinerary ?? p.live
        let l0 = it?.legs.first
        let leg = trip.currentLeg
        let onTrain = trip.onTrain
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
        if !onTrain, let it = it, let sched = data.schedule {
            next = leg == 0 ? pathTrips(boards: data.predictedBoards, schedule: sched, option: p, now: data.now, maxN: 3).first { $0.boardTs > it.boardTs + 30 }
                            : it.nextIfMissedSec.map { s in var n = it; n.boardTs = it.boardTs + s; return n }
        }
        // on the train: the line the rider is on leads, and the countdown runs to the connection (or the arrival)
        var route = l0?.train.route ?? p.legs[leg].primaryRoute
        var boardTs = it?.boardTs ?? (data.now + p.wait1Sec)
        var offAt: String? = nil, offTs: Double? = nil
        if onTrain, let l0 = l0 {
            route = ridingRoute ?? l0.train.route
            let changeAhead = p.legs.count > 1 && leg == 0
            offAt = changeAhead ? p.transfer?.station : (data.index?.stations[destId]?.name ?? "your stop")
            offTs = l0.arriveTs
            if let it = it, it.legs.count > 1 { boardTs = it.legs[1].boardTs } else { boardTs = l0.arriveTs }
            let word = ridePresumed ? "On the \(route), presumably" : "On the \(route)"
            status = status.isEmpty ? word : "\(word) · \(status)"
        }
        let changeAhead = p.legs.count > 1 && leg == 0
        return TripActivityAttributes.ContentState(
            route: route, trainLabel: l0.map { shortLabel($0.train) } ?? "",
            boardTs: boardTs, arriveTs: it?.arriveTs ?? (data.now + p.expectedSec),
            nextBoardTs: next?.boardTs, nextRoute: next?.legs.first?.train.route,
            changeAt: changeAhead ? p.transfer?.station : nil, changeRoutes: changeAhead ? p.legs[1].routesLabel : nil,
            extraMin: h.extraSec >= 90 ? h.minutes : 0, level: h.level.rawValue, status: status, offline: data.offline, routeLabel: p.label,
            riding: onTrain, offAt: offAt, offTs: offTs, presumed: ridePresumed)
    }

    private func startActivity() {
        guard let p = headline, let index = data.index else { return }
        let attrs = TripActivityAttributes(originName: index.stations[originId]?.name ?? "", destName: index.stations[destId]?.name ?? "", routeLabel: p.label)
        TripActivityService.shared.start(routeId: p.id, attributes: attrs, state: activityState(p))
    }

    /// Every refresh while the route is in progress. A route that changes on the way travels in the state (an
    /// activity cannot be started afresh from the background, so it is never restarted for that); one that could
    /// not start (the route began in the background) is started at the next chance.
    private func updateActivity() {
        guard let p = headline else { return }
        if !TripActivityService.shared.isRunning { startActivity(); return }
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

    /// The route's phase and the way to end it, or the way to start it with why the last one ended on its own.
    /// On the train, the line the phone thinks the rider boarded, and the route's other lines to say otherwise.
    private func tripBar(originName: String, glow: Color? = nil) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                if let ph = trip.phase {
                    Label(phaseText(ph, originName: originName), systemImage: phaseSymbol(ph))
                        .font(.caption.weight(.semibold)).foregroundStyle(Color.green).lineLimit(1)
                    Spacer()
                    Button { openOnTrain() } label: { Label("Train", systemImage: "tram.fill") }
                        .font(.caption.weight(.semibold)).buttonStyle(.bordered).controlSize(.small)
                        .accessibilityLabel("Which train am I on")
                    Button("End route") { endTrip(by: "hand") }
                        .font(.caption.weight(.semibold)).buttonStyle(.bordered).controlSize(.small)
                } else {
                    Text(lastEndNote ?? "Starts by itself at \(originName)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Button("On a train?") { openOnTrain() }
                        .font(.caption.weight(.semibold)).buttonStyle(.bordered).controlSize(.small)
                    Button("Start route") { startTrip(by: "hand") }
                        .font(.caption.weight(.semibold)).buttonStyle(.borderedProminent).controlSize(.small)
                        .shadow(color: (glow ?? .clear).opacity(0.35), radius: 11)
                }
            }
            if let pr = trip.prompt {
                boardingPrompt(pr)
            } else if trip.phase == .riding, trip.currentLegKeys.count > 1 {
                lineChooser
            }
        }
    }

    /// The question when the phone is not sure which train the rider boarded, believes they boarded one off the
    /// plan, or has only assumed the ride: the trains that were at the platform, by line and time, to tap.
    private func boardingPrompt(_ pr: BoardingPrompt) -> some View {
        var seen = Set<String>()
        let options = pr.candidates.sorted { a, b in
            // the phone's best guess first, then the plan's line, then by time
            if (a.trainId == pr.bestTrainId) != (b.trainId == pr.bestTrainId) { return a.trainId == pr.bestTrainId }
            if (a.key == pr.chosenKey) != (b.key == pr.chosenKey) { return a.key == pr.chosenKey }
            return a.boardTs < b.boardTs
        }.filter { seen.insert($0.key).inserted }          // one train per line: the likeliest of that line
        let bestRoute = pr.bestKey.map { String($0.split(separator: "_").first ?? "") }
        let planRoute = pr.chosenKey.map { String($0.split(separator: "_").first ?? "") }
        let question: String = {
            switch pr.reason {
            case .switched: return "Looks like you boarded the \(bestRoute ?? "?")\(planRoute != nil && planRoute != bestRoute ? ", not the \(planRoute!)" : ""). Right?"
            case .unsure: return "Which train did you board?"
            case .assumed: return "Are you on the \(planRoute ?? "")\(pr.candidates.first(where: { $0.key == pr.chosenKey }).map { " that left \(Fmt.hhmm(PlatformTiming.pullsAway($0.boardTs, route: $0.route)))" } ?? "")?"
            }
        }()
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "questionmark.circle.fill").foregroundStyle(Color.accentColor)
                Text(question).font(.subheadline.weight(.semibold))
                Spacer(minLength: 0)
                Button { trip.dismissPrompt() } label: { Image(systemName: "xmark").font(.caption.bold()).foregroundStyle(.secondary) }.buttonStyle(.plain).accessibilityLabel("Dismiss")
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(options, id: \.trainId) { c in
                        Button { trip.confirm(trainId: c.trainId); followBoarded() } label: {
                            HStack(spacing: 5) {
                                RouteBullet(route: c.route, size: 20)
                                VStack(alignment: .leading, spacing: 0) {
                                    Text("left \(Fmt.hhmm(PlatformTiming.pullsAway(c.boardTs, route: c.route)))").font(.caption.weight(.semibold))
                                    Text(c.trainId == pr.bestTrainId ? "the phone's guess" : (c.key == pr.chosenKey ? "the plan" : "also at the platform")).font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(RoundedRectangle(cornerRadius: 10).fill(c.trainId == pr.bestTrainId ? Color.accentColor.opacity(0.18) : Color(.secondarySystemBackground)))
                            .overlay(RoundedRectangle(cornerRadius: 10).stroke(c.trainId == pr.bestTrainId ? Color.accentColor : Color.clear, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("I boarded the \(c.route) that left \(Fmt.hhmm(PlatformTiming.pullsAway(c.boardTs, route: c.route)))")
                    }
                    Button { trip.notOnTrain() } label: {
                        Text("Not on a train").font(.caption.weight(.semibold)).padding(.horizontal, 10).padding(.vertical, 10)
                            .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
                    }
                    .buttonStyle(.plain)
                }
            }
            if pr.reason == .switched, let alt = headline, let r = bestRoute, alt.legs.indices.contains(pr.leg), alt.legs[pr.leg].routes.contains(r) {
                Text("Following \(alt.label) now.").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.accentColor.opacity(0.08)))
    }

    /// The sensors and the feeds say the rider boarded a line off the plan: the route switches to the option that
    /// rides that line from here (the G to Hoyt-Schermerhorn instead of the F to Jay St), and the recorder's legs,
    /// the tracker's count of legs and change, the telemetry and the Live Activity follow, so the change ahead and
    /// the destination arrival are the right ones. The phone's own guess must be sure (a settled belief still moves
    /// with each poll's evidence) and switches at most once a minute; the rider's own word switches at once.
    private func followBoarded() {
        guard trip.phase == .riding, let sel = headline else { return }
        let leg = trip.currentLeg
        guard let b = trip.belief(leg: leg), let key = b.bestKey, sel.legs.indices.contains(leg), !sel.legs[leg].keys.contains(key) else { updateFocus(); return }
        guard b.byHand || (b.settled && b.confidence >= autoSwitchConfidence) else { updateFocus(); return }
        if !b.byHand, data.now - lastSwitchTs < 60 { updateFocus(); return }
        guard let alt = routeRiding(key, leg: leg, like: sel) else { updateFocus(); return }
        lastSwitchTs = data.now
        switchRoute(to: alt)
    }

    /// The rider walked off somewhere else than the route's change or destination (the G to Hoyt-Schermerhorn when
    /// the plan changed at W 4 St): the route becomes the one that rides the same line to there and changes there,
    /// and the trains that already left that platform are handed to the recorder, in case the rider stepped
    /// straight onto one.
    private func followAlighting(_ a: OffPlanAlighting) {
        guard let sched = data.schedule, let index = data.index, let sel = headline, let line = sched.lines[a.key], a.stopIdx < line.stops.count else { return }
        let here = index.stationOf(line.stops[a.stopIdx])
        if sel.legs.count > a.leg + 1, index.stationOf(sel.legs[a.leg + 1].from) == here { return }   // the plan's own change after all
        if index.stationOf(sel.legs[a.leg].to) == here { return }
        let origin = index.stationOf(sel.legs[0].from)
        func fits(_ p: PathOption) -> Bool {
            p.legs.count == a.leg + 2 && p.legs[a.leg].keys.contains(a.key) && index.stationOf(p.legs[a.leg].to) == here
                && index.stationOf(p.legs[0].from) == origin
        }
        var alt = paths.first(where: fits)
        if alt == nil {
            var more = enumeratePaths(schedule: sched, index: index, from: originId, to: destId, maxOptions: 24).filter { fits($0) && !paths.map(\.id).contains($0.id) }
            applyPersonalTransfers(&more)
            let now = data.now, hour = nyHour(now)
            for i in more.indices {
                evaluate(&more[i], schedule: sched, lineSched: data.lineSched, now: now, holds: data.holds, deviations: data.deviations, hour: hour)
                more[i].live = pathTrips(boards: data.predictedBoards, schedule: sched, option: more[i], now: now, maxN: 1).first
            }
            if let p = more.min(by: { $0.expectedSec < $1.expectedSec }) {
                extraPaths.append(p); paths.append(p); alt = p
                data.setWanted(Set(paths.flatMap { $0.legs.flatMap { $0.keys } }), for: "planner")
            }
        }
        guard let alt else { return }
        switchRoute(to: alt)
        // the next leg's trains that left the change platform in the last five minutes
        let next = alt.legs[a.leg + 1]
        var boardIdx: [String: Int] = [:], alightIdx: [String: Int] = [:]
        for (k, ix) in next.idx { boardIdx[k] = ix.from; alightIdx[k] = ix.to }
        let gone = trainsAhead(boards: data.boards, schedule: sched, boardIdx: boardIdx, alightIdx: alightIdx, onLegKeys: Set(next.keys), now: data.now, maxAgoSec: 300)
        trip.seedCurrentLeg(gone, now: data.now)
    }

    /// The route in progress becomes `alt`: selecting it makes the trip adopt it (below).
    private func switchRoute(to alt: PathOption) {
        if selectedPath != alt.id { selectedPath = alt.id } else { adoptSelectedRoute() }
    }

    /// The trip follows the selected route from here: the recorder's legs, the tracker's count of legs and change,
    /// the telemetry record and the Live Activity.
    private func adoptSelectedRoute() {
        guard routeStarted, let p = headline else { return }
        forecastTrainId = nil        // another route's train is a choice, not the forecast's train leaving
        let plans = legPlans(p)
        data.setWanted(Set(plans.flatMap { $0.platformKeys.keys }), for: "trip")
        trip.replan(legs: plans, transferStation: p.transfer?.station)
        Telemetry.shared.updateRoute(label: p.label, legs: p.legs.map { TripObservation.Leg(line: $0.primaryKey, from: $0.from, to: $0.to) },
                                     transferStation: p.transfer?.station, transferWalkSec: p.transfer?.walkSec)
        refreshRideArrival()
        updateActivity()
        updateFocus()
    }

    // MARK: - the rider's own word on the train

    /// Opens the sheet; the lines leaving the boarding station are asked of the feeds so their trains show.
    private func openOnTrain() {
        guard let sched = data.schedule, let index = data.index else { return }
        var keys = Set<String>()
        for p in onTrainOptions(sched, index) { for l in p.legs { keys.formUnion(l.keys) } }
        data.setWanted(keys, for: "onTrain")
        onTrainSheet = true
    }

    /// The leg in hand: the one being ridden, or the first before the route starts.
    private var onTrainLeg: Int { routeStarted ? trip.currentLeg : 0 }

    /// Every route whose leg in hand starts where the rider boards it: the listed ones first, then the planner's
    /// further options in the same direction (a route that sets off the other way is not what a rider on a train
    /// from here meant).
    private func onTrainOptions(_ sched: ClientSchedule, _ index: StationIndex) -> [PathOption] {
        guard let sel = headline, sel.legs.indices.contains(onTrainLeg) else { return [] }
        let leg = onTrainLeg
        let here = index.stationOf(sel.legs[leg].from)
        let dir = String(sel.legs[leg].primaryKey.split(separator: "_").last ?? "")
        var out = paths.filter { $0.legs.indices.contains(leg) && index.stationOf($0.legs[leg].from) == here }
        let seen = Set(out.map { $0.id })
        for p in enumeratePaths(schedule: sched, index: index, from: originId, to: destId, maxOptions: 24)
        where !seen.contains(p.id) && p.legs.indices.contains(leg) && index.stationOf(p.legs[leg].from) == here
            && p.legs[leg].keys.allSatisfy({ $0.hasSuffix("_" + dir) }) {
            out.append(p)
        }
        return out
    }

    /// The trains that have left the boarding station toward the destination, on every line with a route from there.
    private func trainsAheadNow() -> [BoardingCandidate] {
        guard let sched = data.schedule, let index = data.index, let sel = headline, sel.legs.indices.contains(onTrainLeg) else { return [] }
        let leg = onTrainLeg
        var boardIdx: [String: Int] = [:], alightIdx: [String: Int] = [:]
        for p in onTrainOptions(sched, index) {
            for (k, ix) in p.legs[leg].idx where boardIdx[k] == nil { boardIdx[k] = ix.from; alightIdx[k] = ix.to }
        }
        return trainsAhead(boards: data.boards, schedule: sched, boardIdx: boardIdx, alightIdx: alightIdx, onLegKeys: Set(sel.legs[leg].keys), now: data.now)
    }

    /// With a change ahead on the leg in hand: the lines the rider could take at it, each with its route.
    private func transferChoices() -> (station: String, choices: [OnTrainSheet.TransferChoice])? {
        guard let sched = data.schedule, let index = data.index, let sel = headline, onTrainLeg == 0, sel.legs.count == 2, let tr = sel.transfer else { return nil }
        var choices: [OnTrainSheet.TransferChoice] = []
        var seen = Set<String>()
        let firstKeys = Set(sel.legs[0].keys)
        var options = paths
        let listed = Set(paths.map { $0.id })
        options += enumeratePaths(schedule: sched, index: index, from: originId, to: destId, maxOptions: 24).filter { !listed.contains($0.id) }
        for p in options where p.legs.count == 2 && index.stationOf(p.legs[1].from) == index.stationOf(sel.legs[1].from) && !firstKeys.isDisjoint(with: p.legs[0].keys) {
            for k in p.legs[1].keys where !seen.contains(k) {
                seen.insert(k)
                choices.append(OnTrainSheet.TransferChoice(key: k, label: p.label))
            }
        }
        return (tr.station, choices)
    }

    private var onTrainSheetView: some View {
        let sel = headline
        let leg = onTrainLeg
        let stationName = sel.flatMap { p in p.legs.indices.contains(leg) ? data.index?.stations[data.index?.stationOf(p.legs[leg].from) ?? ""]?.name : nil } ?? "the station"
        let current = trip.belief(leg: leg).flatMap { $0.settled || $0.byHand ? $0.bestTrain : nil }
        return OnTrainSheet(
            stationName: stationName, candidates: trainsAheadNow(), currentTrainId: current,
            routeFor: { key in
                guard let sel = sel else { return nil }
                if sel.legs.indices.contains(leg), sel.legs[leg].keys.contains(key) { return sel.label }
                return routeRiding(key, leg: leg, like: sel, adopt: false)?.label
            },
            describe: { c in
                if let t = data.boards[c.key]?.trains.first(where: { $0.id == c.trainId }) {
                    if let pos = t.position { return "now \(pos.text)" }
                    return "now approaching \(t.nextName)"
                }
                return c.alightTs.map { "gets off at \(Fmt.hhmm($0))" } ?? ""
            },
            transfer: transferChoices(), riding: trip.phase == .riding,
            onPick: { boardTrain($0) },
            onTransfer: { key in
                guard let sel = headline, let alt = routeRiding(key, leg: 1, like: sel) else { return }
                preferredKeys[1] = key
                switchRoute(to: alt)
            },
            onNotOnTrain: { trip.notOnTrain() })
    }

    /// The rider is on this train: the route starts from it if it has not started, switches to the route that
    /// rides its line if the plan had another, and the recorder takes the train as the leg's own, by hand.
    private func boardTrain(_ c: BoardingCandidate) {
        if !routeStarted { startTrip(by: "onboard") }
        guard routeStarted, let sel = headline else { return }
        let leg = trip.currentLeg
        if sel.legs.indices.contains(leg), !sel.legs[leg].keys.contains(c.key), let alt = routeRiding(c.key, leg: leg, like: sel) {
            lastSwitchTs = data.now
            switchRoute(to: alt)
        }
        trip.setOnTrain(c, now: data.now)
        refreshRideArrival()
        updateActivity()
        updateFocus()
    }

    /// The route that rides `key` on leg `leg` and matches the route in hand up to there: the same stations ridden
    /// on the legs before (on the line the phone settled on, where it did), the same change. Among the listed
    /// routes first; failing that, among more of the planner's options, the best of which joins the list for the
    /// rest of the trip.
    private func routeRiding(_ key: String, leg: Int, like sel: PathOption, adopt: Bool = true) -> PathOption? {
        guard let sched = data.schedule, let index = data.index else { return nil }
        func fits(_ p: PathOption) -> Bool {
            guard p.legs.indices.contains(leg), p.legs[leg].keys.contains(key) else { return false }
            if leg > 0, index.stationOf(p.legs[leg].from) != index.stationOf(sel.legs[leg].from) { return false }
            for i in 0..<leg {
                guard index.stationOf(p.legs[i].from) == index.stationOf(sel.legs[i].from), index.stationOf(p.legs[i].to) == index.stationOf(sel.legs[i].to) else { return false }
                if let r = trip.belief(leg: i).flatMap({ $0.settled || $0.byHand ? $0.bestKey : nil }) {
                    if !p.legs[i].keys.contains(r) { return false }
                } else if Set(p.legs[i].keys).isDisjoint(with: sel.legs[i].keys) { return false }
            }
            return true
        }
        if let p = paths.first(where: fits) { return p }
        var more = enumeratePaths(schedule: sched, index: index, from: originId, to: destId, maxOptions: 24).filter { p in fits(p) && !paths.contains { $0.id == p.id } }
        guard !more.isEmpty else { return nil }
        applyPersonalTransfers(&more)
        let now = data.now, hour = nyHour(now)
        for i in more.indices {
            evaluate(&more[i], schedule: sched, lineSched: data.lineSched, now: now, holds: data.holds, deviations: data.deviations, hour: hour)
            more[i].live = pathTrips(boards: data.predictedBoards, schedule: sched, option: more[i], now: now, maxN: 1).first
        }
        more.sort { $0.expectedSec < $1.expectedSec }
        let p = more[0]
        guard adopt else { return p }
        extraPaths.append(p)
        paths.append(p)
        data.setWanted(Set(paths.flatMap { $0.legs.flatMap { $0.keys } }), for: "planner")
        return p
    }

    /// While the route is under way: on the train, the arrival as the train the rider is actually on makes it (the
    /// one the phone settled on or the rider named, else the plan's train for the leg), with the connection it
    /// makes; between trains at the change, the connection from there. For the card, the Live Activity and the
    /// clock the trip ends by. The planner's own itinerary moved on to the next train when this one left, so it
    /// no longer says when this ride ends; this does. A train the feed has lost keeps the last itinerary.
    private func refreshRideArrival() {
        guard trip.phase == .riding, let sel = headline, let sched = data.schedule else { rideItinerary = nil; return }
        let leg = trip.currentLeg
        if trip.onTrain {
            guard let c = trip.boardedCandidate ?? plannedTrains[leg],
                  let it = ridingItinerary(boards: data.predictedBoards, schedule: sched, option: sel, leg: leg, boarded: c, now: data.now) else { return }
            rideItinerary = it
            trip.setLiveArrival(it.arriveTs)
        } else if leg > 0 {
            guard let it = connectionItinerary(boards: data.predictedBoards, schedule: sched, option: sel, leg: leg, now: data.now) else { return }
            rideItinerary = it
            trip.setLiveArrival(it.arriveTs)
        } else {
            rideItinerary = nil
        }
    }

    /// The rider stayed on past the stop where the route had them leave the train: the route becomes the one that
    /// rides the same line on to a later change (the A on to Jay St for the F, rather than the F from W 4 St), or
    /// straight to the destination, whichever the train they are on makes arrive first. The legs ridden before
    /// and the leg in hand keep their lines; the belief about the train in hand is kept.
    private func followStayingOn(_ s: StayedOn) {
        guard let sched = data.schedule, let index = data.index, let sel = headline, sel.legs.indices.contains(s.leg),
              let line = sched.lines[s.key], let c = trip.boardedCandidate, c.trainId == s.trainId else { return }
        let progress = c.progressIdx ?? s.pastIdx + 1
        func fits(_ p: PathOption) -> Bool {
            guard p.legs.indices.contains(s.leg), let ix = p.legs[s.leg].idx[s.key], ix.to > s.pastIdx, ix.to >= progress, ix.to < line.stops.count else { return false }
            if index.stationOf(p.legs[s.leg].from) != index.stationOf(sel.legs[s.leg].from) { return false }
            for i in 0..<s.leg {
                guard index.stationOf(p.legs[i].from) == index.stationOf(sel.legs[i].from), index.stationOf(p.legs[i].to) == index.stationOf(sel.legs[i].to),
                      !Set(p.legs[i].keys).isDisjoint(with: sel.legs[i].keys) else { return false }
            }
            return true
        }
        var options = paths.filter(fits)
        let listed = Set(paths.map { $0.id })
        var more = enumeratePaths(schedule: sched, index: index, from: originId, to: destId, maxOptions: 24).filter { fits($0) && !listed.contains($0.id) }
        applyPersonalTransfers(&more)
        let now = data.now, hour = nyHour(now)
        for i in more.indices {
            evaluate(&more[i], schedule: sched, lineSched: data.lineSched, now: now, holds: data.holds, deviations: data.deviations, hour: hour)
            more[i].live = pathTrips(boards: data.predictedBoards, schedule: sched, option: more[i], now: now, maxN: 1).first
        }
        options += more
        // the option the train in hand gets the rider to the destination soonest on
        func arrival(_ p: PathOption) -> Double {
            ridingItinerary(boards: data.predictedBoards, schedule: sched, option: p, leg: s.leg, boarded: c, now: now)?.arriveTs ?? (now + p.expectedSec)
        }
        guard let alt = options.min(by: { arrival($0) < arrival($1) }) else { return }
        if !listed.contains(alt.id) {
            extraPaths.append(alt); paths.append(alt)
            data.setWanted(Set(paths.flatMap { $0.legs.flatMap { $0.keys } }), for: "planner")
        }
        lastSwitchTs = data.now
        switchRoute(to: alt)
    }

    /// Before a route starts: a walk closing on the origin station at a walking pace, over the last minute or so,
    /// starts the route on the way (the sensors then see the walk, the platform and the pull-away in order).
    private func checkApproach() {
        guard trip.phase == nil, !routeEndedByHand, !destId.isEmpty, let l = loc.fix(maxAgeSec: 30, maxAccuracyM: 65), let d = tripDistanceM(to: originId) else { return }
        let ts = l.timestamp.timeIntervalSince1970
        if let last = approachFixes.last, ts <= last.ts { return }
        approachFixes.append((ts, d))
        approachFixes = approachFixes.filter { ts - $0.ts <= 180 }
        guard d > 150, d <= 1500, let first = approachFixes.first, ts - first.ts >= 45 else { return }
        let closed = first.d - d, dt = ts - first.ts
        if closed >= 40, closed / dt >= 0.5, closed / dt <= 2.5 { startTrip(by: "gps") }
    }

    /// Which of the route's lines the rider is on: the phone's estimate is marked; a tap says otherwise, and the
    /// rider's word stands for the rest of the leg.
    private var lineChooser: some View {
        let b = trip.currentBelief
        let picked = (b?.settled ?? false) || (b?.byHand ?? false) ? b?.bestKey : nil
        return HStack(spacing: 8) {
            Text(b?.byHand == true ? "You said:" : (picked == nil ? "Which train did you board?" : "On the"))
                .font(.caption).foregroundStyle(.secondary)
            ForEach(trip.currentLegKeys, id: \.self) { key in
                let route = String(key.split(separator: "_").first ?? "")
                Button { trip.setBoardedByHand(key: key) } label: {
                    HStack(spacing: 4) {
                        RouteBullet(route: route, size: 18)
                        if picked == key { Image(systemName: b?.byHand == true ? "hand.tap.fill" : "checkmark").font(.caption2.bold()) }
                    }
                    .padding(.horizontal, 6).padding(.vertical, 3)
                    .background(RoundedRectangle(cornerRadius: 8).fill(picked == key ? Color.accentColor.opacity(0.18) : Color(.secondarySystemBackground)))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(picked == key ? "On the \(route)" : "I boarded the \(route)")
            }
            if let b = b, picked != nil, !b.byHand {
                Text(b.assumed ? "assumed from the schedule" : "\(Int((b.confidence * 100).rounded()))% sure").font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
        }
    }

    private func phaseText(_ ph: TripPhase, originName: String) -> String {
        switch ph {
        case .approaching:
            var s = "Heading to \(originName)"
            if let d = distanceM(to: originId) { s += " · \(Fmt.miles(d))" }
            return s
        case .atStation: return "At \(originName)"
        case .riding:
            if let b = trip.currentBelief, b.settled || b.byHand, let r = b.route {
                if b.verdict == .switched, let c = b.chosenRoute, c != r { return "On the \(r), not the \(c)" }
                return b.assumed ? "On the \(r), presumably" : "On the \(r)"
            }
            return trip.rideAssumed ? "On the train, presumably" : "On the train"
        case .arrived: return "Arrived"
        }
    }

    private func phaseSymbol(_ ph: TripPhase) -> String {
        switch ph {
        case .approaching: return "figure.walk"
        case .atStation: return "figure.walk.circle.fill"
        case .riding: return "tram.fill"
        case .arrived: return "checkmark.circle.fill"
        }
    }

    /// Metres from the phone's last fix to a station, when both are known and the fix is recent.
    private func distanceM(to stationId: String) -> Double? {
        guard !stationId.isEmpty, let l = loc.location, Date().timeIntervalSince(l.timestamp) < 180,
              let sched = data.schedule, let index = data.index, let geo = data.geometry,
              let c = stationCoordinates(schedule: sched, index: index, geometry: geo)[stationId] else { return nil }
        return haversineM((l.coordinate.latitude, l.coordinate.longitude), c)
    }

    private var originDistanceM: Double? { distanceM(to: originId) }

    /// The distance to a station from a fix fresh and sure enough to act on (a route's start, its auto-start, the
    /// tracker): never the stale or coarse first fix the phone hands over when the app comes back.
    private func tripDistanceM(to stationId: String) -> Double? {
        guard !stationId.isEmpty, let l = loc.fix(), let sched = data.schedule, let index = data.index, let geo = data.geometry,
              let c = stationCoordinates(schedule: sched, index: index, geometry: geo)[stationId] else { return nil }
        return haversineM((l.coordinate.latitude, l.coordinate.longitude), c)
    }

    /// A pinned place the phone is at (within 150 m).
    private func nearbyPlace() -> Place? {
        guard let l = loc.location, Date().timeIntervalSince(l.timestamp) < 300 else { return nil }
        return places.nearest(toLat: l.coordinate.latitude, lon: l.coordinate.longitude, withinM: 150)
    }

    /// The route starts: anywhere by hand, at the station by GPS. The recorder takes the sensors from here.
    private func startTrip(by: String) {
        guard !originId.isEmpty, !destId.isEmpty, trip.phase == nil else { return }
        routeEndedByHand = false
        lastEndNote = nil
        let d = tripDistanceM(to: originId)
        let p = headline
        var tl = TripTimeline(startTs: data.now, startedBy: by, startDistanceM: d, placeId: nearbyPlace()?.id.uuidString,
                              originStation: originId, destStation: destId, transferStation: p?.transfer?.station, legs: p?.legs.count ?? 1)
        tl.forecastBoardTs = p?.live?.boardTs; tl.forecastArriveTs = p?.live?.arriveTs; tl.liveArriveTs = p?.live?.arriveTs
        let obs = (Telemetry.shared.optIn && p != nil) ? observationBase(p!, startedBy: by) : nil
        let plans = legPlans(p)
        data.setWanted(Set(plans.flatMap { $0.platformKeys.keys }), for: "trip")
        extraPaths = []; lastSwitchTs = 0; rideItinerary = nil; plannedTrains = [:]; preferredKeys = [:]; forecastTrainId = nil; frozenLegs = []
        trip.stopCoordinate = { [weak data] key, idx in data?.geometry?.lines[key]?.coord(idx) }
        withAnimation { trip.begin(tl, distanceToOriginM: d, observation: obs, legs: plans, now: data.now) }
        data.requestGeometry()
        loc.startTracking()
        startActivity()
        armTripTimer()
        // started by hand well away from the origin while moving at a vehicle's pace (on a sure fix): the rider is
        // most likely on a train already. A start from home, far but still, is a walk to come.
        if by == "hand", let d = d, d > 400, let l = loc.fix(maxAgeSec: 20, maxAccuracyM: 65), l.speed >= 5 { openOnTrain() }
    }

    /// The plan's legs for the line inference: each leg's lines and stop spans, the train and line the itinerary
    /// boards, and the other same-direction lines at the boarding platform.
    private func legPlans(_ p: PathOption?) -> [LegPlan] {
        guard let p = p, let index = data.index else { return [] }
        var plans: [LegPlan] = []
        for (i, leg) in p.legs.enumerated() {
            let tc: TripCandidate? = (p.live?.legs.indices.contains(i) ?? false) ? p.live?.legs[i] : nil
            let preferred = preferredKeys[i].flatMap { leg.keys.contains($0) ? $0 : nil }
            var plan = LegPlan(keys: leg.keys, idx: leg.idx, chosenKey: preferred ?? tc?.key ?? leg.primaryKey,
                               chosenTrainId: preferred == nil || preferred == tc?.key ? tc?.train.id : nil, origin: originId, dest: destId)
            plan.walkSec = i == 0 ? 0 : (p.live?.walkSec ?? Double(p.transfer?.walkSec ?? 0))
            let dir = String((plan.chosenKey ?? "").split(separator: "_").last ?? "")
            if let st = index.stations[index.stationOf(leg.from)] {
                for m in st.members where m.dir == dir && !leg.keys.contains(m.key) {
                    plan.platformKeys[m.key] = m.idx
                    if m.stop != leg.from { plan.otherPlatformKeys.insert(m.key) }
                }
            }
            plans.append(plan)
        }
        return plans
    }

    /// The route ends, by hand or on its own; what it measured goes to the pace model and the telemetry.
    private func endTrip(by: String) {
        guard trip.phase != nil || trip.endReason != nil else { return }
        if by == "hand" { routeEndedByHand = true }
        tripTimer?.cancel(); tripTimer = nil
        data.setWanted([], for: "trip")
        data.setWanted([], for: "onTrain")
        loc.stopTracking()
        rideItinerary = nil; plannedTrains = [:]; forecastTrainId = nil; frozenLegs = []
        TripActivityService.shared.end()
        let tl = trip.end(by: by, api: Telemetry.shared.uploadURL(fallback: data.apiBase), now: data.now)
        let destName = data.index?.stations[destId]?.name ?? "your stop"
        let at = Fmt.hhmm(tl?.endedTs ?? data.now)
        switch by {
        case "arrived": lastEndNote = "Ended \(at): reached \(destName)"
        case "alighted", "walked": lastEndNote = "Ended \(at): you got off the train"
        case "timeout": lastEndNote = "Ended \(at): long past the expected arrival"
        default: lastEndNote = nil
        }
    }

    /// Within 150 m of the origin station with no route in progress: it starts by itself.
    private func checkArrival() {
        guard trip.phase == nil, !routeEndedByHand, !destId.isEmpty, let d = tripDistanceM(to: originId), d <= 150 else { return }
        startTrip(by: "gps")
    }

    /// Every fix: before a route, the auto-start at the station; during one, the tracker.
    private func feedLocation() {
        guard trip.phase != nil else { checkArrival(); checkApproach(); return }
        guard let l = loc.location else { return }
        trip.location(ts: l.timestamp.timeIntervalSince1970, toOriginM: originDistanceM, toDestM: distanceM(to: destId), now: data.now,
                      coordinate: (l.coordinate.latitude, l.coordinate.longitude), accuracyM: l.horizontalAccuracy)
    }

    /// A fresh fix every poll while a trip is on screen and not started yet (only once location is allowed).
    private func pollLocation() {
        guard trip.phase == nil, !originId.isEmpty, !destId.isEmpty, loc.authorized else { return }
        data.requestGeometry()
        loc.request()
        checkArrival()
    }

    private func armTripTimer() {
        tripTimer?.cancel()
        tripTimer = Task { @MainActor in
            while !Task.isCancelled, trip.phase != nil {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                if Task.isCancelled { break }
                trip.tick(now: data.now)
            }
        }
    }

    private func resetTrip() {
        if trip.phase != nil { endTrip(by: "changed") }
        routeEndedByHand = false
        lastEndNote = nil
    }

    // MARK: - the trip the rider usually makes

    /// With no commute window covering now, no route in progress and no station picked by hand in the last
    /// two hours, the trip the rider usually makes at this hour, from here, goes on screen.
    private func applyHabit() {
        guard let index = data.index, trip.phase == nil, presets.active(at: data.now) == nil, data.now - pickedByHandTs > 7200 else { return }
        let l = loc.location.flatMap { Date().timeIntervalSince($0.timestamp) < 900 ? $0 : nil }
        guard let g = HabitStore.shared.likelyTrip(now: data.now, lat: l?.coordinate.latitude, lon: l?.coordinate.longitude),
              let o = index.station(g.origin), let d = index.station(g.dest) else { return }
        let name = { (st: Station) in self.places.places.first { $0.stationId == st.id }?.name ?? st.name }
        habitNote = "Your usual trip at this hour: \(name(o)) → \(name(d))"
        guard originId != o.id || destId != d.id else { return }
        currentPresetId = nil
        originId = o.id
        destId = d.id
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
            let walk = i == 0 ? 0.0 : (it?.walkSec ?? Double(sel.transfer?.walkSec ?? 0))
            legs.append(FocusLeg(key: tc?.key ?? leg.primaryKey, keys: leg.keys, idx: spans, trainId: tc?.train.id, walkSec: walk))
        }
        // once the sensors and the feeds agree on the train the rider is actually on, the Line tab follows that one
        for i in legs.indices {
            guard let b = trip.belief(leg: i), b.settled || b.byHand, let k = b.bestKey, let tid = b.bestTrain, legs[i].idx[k] != nil else { continue }
            legs[i].key = k; legs[i].trainId = tid
        }
        let l0 = it?.legs.first
        var key0 = l0?.key ?? sel.legs[0].primaryKey, train0 = l0?.train.id ?? "", trip0 = l0?.train.tripId ?? ""
        if let f0 = legs.first, let tid = f0.trainId, let b = trip.belief(leg: 0), b.settled || b.byHand, b.bestTrain == tid {
            key0 = f0.key; train0 = tid; trip0 = String(tid.split(separator: "|", maxSplits: 1).last ?? "")
        }
        let f = TrainFocus(key: key0, trainId: train0, tripId: trip0, legs: legs)
        if data.focus != f { data.focus = f }
    }
}

struct BarSeg {
    var sec: Double
    var color: Color
}

/// The route's door-to-door time as a bar: wait (grey), ride (line colour), walk at the change (dark), wait, ride.
func journeySegments(_ option: PathOption) -> [BarSeg] {
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

struct PathRow: View {
    @Environment(DataService.self) private var data
    let option: PathOption
    let selected: Bool
    let maxSec: Double
    /// The chosen row carries the soft glow in this colour, the time to its train (the paged Go tab); nil: none.
    var glow: Color? = nil

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
        .shadow(color: (glow ?? .clear).opacity(selected ? GlowLevel.soft.row.opacity : 0), radius: GlowLevel.soft.row.radius)
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

    private var segments: [BarSeg] { journeySegments(option) }

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
