import SwiftUI

/// The current route explained: what the timetable and history say, the train the feeds give, what the engine
/// did with it, the lines' state, the alerts and their evidence, the data behind it, and how it all fits.
struct RouteInsightsView: View {
    @Environment(DataService.self) private var data
    @Environment(\.dismiss) private var dismiss
    let option: PathOption
    let schedule: ClientSchedule
    let originName: String
    let destName: String

    var body: some View {
        NavigationStack {
            List {
                verdict
                expected
                if let it = option.live { live(it) } else { noTrain }
                lines
                alerts
                dataBehind
                howItWorks
            }
            .navigationTitle("Route insights")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }

    // MARK: - sections

    private var verdict: some View {
        let h = routeHealth(option, data: data)
        return Section {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    RouteBullets(routes: option.legs.flatMap { $0.routes }, size: 20)
                    Spacer()
                    HealthDot(health: h)
                }
                Text(option.label).font(.headline)
                Text("\(originName) → \(destName)").font(.caption).foregroundStyle(.secondary)
                if let it = option.live {
                    Text("Board \(Fmt.hhmm(it.boardTs)) · arrive \(Fmt.hhmm(it.arriveTs)) · \(Fmt.minTxt(it.totalSec)) door to door").font(.subheadline)
                } else {
                    Text("Expected \(Fmt.minTxt(option.expectedSec)) door to door · no train for this path in the feeds yet").font(.subheadline)
                }
                Text("\(h.label) against the timetable" + (h.slipSec >= 60 ? " · could slip \(Fmt.minTxt(h.slipSec)) more" : "")).font(.caption.weight(.semibold)).foregroundStyle(h.textColor)
                ForEach(Array(h.reasons.enumerated()), id: \.offset) { _, r in
                    Text("• \(r)").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var expected: some View {
        Section("Expected time, from the timetable and history") {
            row("Wait at \(originName)", Fmt.minTxt(option.wait1Sec), "half the scheduled headway around now, at most 15 min")
            ForEach(Array(option.legs.enumerated()), id: \.offset) { i, leg in
                row("Ride on the \(leg.routesLabel)", Fmt.minTxt(leg.schedRideSec.map(Double.init)), "\(leg.nStops) stops, scheduled running time")
                if let t = leg.typicalSec {
                    row("Typically at this hour", Fmt.signed(t), "time trains lose on this stretch at this hour, from the deviation grid")
                }
                if leg.holdRiskSec >= 1 {
                    row("Hold risk", Fmt.signed(leg.holdRiskSec), "holds per day at these stops × their median length ÷ trips per day, from the hold log")
                }
                if i == 0, option.legs.count > 1, let tr = option.transfer {
                    if tr.walkSec > 0 {
                        row("Walk at \(tr.station)", Fmt.mmss(Double(tr.walkSec)), "the MTA's minimum transfer time between these platforms")
                    } else {
                        row("Change at \(tr.station)", "same platform", "no walk: the MTA lists this change as 0 s")
                    }
                    row("Wait for the \(option.legs[1].routesLabel)", Fmt.minTxt(option.wait2Sec), "half its scheduled headway")
                }
            }
            row("Expected", Fmt.minTxt(option.expectedSec), "scheduled \(Fmt.minTxt(Double(option.schedSec))) plus the waits and the typical losses")
        }
    }

    private func live(_ it: Itinerary) -> some View {
        ForEach(Array(it.legs.enumerated()), id: \.offset) { i, tc in
            Section(it.legs.count > 1 ? "Leg \(i + 1): your \(tc.train.route) train" : "Your \(tc.train.route) train") {
                row("Train", "\(shortLabel(tc.train))", tc.train.trainId != nil ? "the MTA's own train id" : "trip id from the feed")
                row("Now", tc.train.position?.text ?? "position unknown", positionNote(tc.train))
                row("Vs timetable", Fmt.late(tc.train.effectiveLatenessSec), latenessNote(tc.train))
                row("Boards \(i == 0 ? originName : (option.transfer?.station ?? ""))", Fmt.hhmm(tc.boardTs), "\(tc.stopsToOrigin) stop\(tc.stopsToOrigin == 1 ? "" : "s") away")
                row("Arrives", Fmt.hhmm(tc.arriveTs), arrivalNote(tc))
                if let lo = tc.arriveLoTs, let hi = tc.arriveHiTs {
                    row("80% window", "\(Fmt.hhmm(lo))–\(Fmt.hhmm(hi))", "the engine's spread for this horizon on this line")
                }
                if let f = tc.feedArriveTs, abs(f - tc.arriveTs) >= 30 {
                    row("Feed says", Fmt.hhmm(f), "the raw ETA in the feed; the engine moved it by \(Fmt.signed(tc.arriveTs - f))")
                }
                row("Ride", Fmt.minTxt(tc.rideSec), tc.rideVsSchedSec.map { "\(Fmt.signed($0)) vs the scheduled ride" } ?? "no scheduled ride to compare")
                let cal = Predictor.calibrationAt(data.model, route: tc.train.route, horizon: tc.boardTs - data.now)
                row("Calibration", "bias \(Fmt.signed(cal.bias)) · spread \(Fmt.signed(cal.p10)) to \(Fmt.signed(cal.p90))",
                    cal.n > 0 ? "from \(cal.n) past ETAs at this horizon on the \(tc.train.route)" : "physical prior, no history at this horizon yet")
                if let p = tc.train.pred {
                    if p.holdExtraSec > 0 { row("Held", "+\(Fmt.mmss(p.holdExtraSec))", "expected remaining hold from the hold-survival table (\(scenarioText))") }
                    if p.knockOnSec >= 30 { row("Held back", "+\(Fmt.mmss(p.knockOnSec))", "pushed back by the train ahead to keep the minimum headway") }
                }
                if let lr = tc.train.lastRun, let sp = lr.speedKmh {
                    row("Last run", Fmt.mph(sp), lr.schedSpeedKmh.map { "scheduled \(Fmt.mph($0)) on that stretch" } ?? "measured between the last two stops")
                }
                if tc.train.trackChanged { row("Track", "changed", "the feed reports a different track from the scheduled one") }
                if i == 0, it.legs.count > 1 {
                    row("Change", "\(Fmt.mmss(it.waitAtTransferSec ?? 0)) on the platform", "margin \(Fmt.mmss(it.connectionMarginSec ?? 0)) after the walk" + (it.nextIfMissedSec.map { " · next if missed +\(Fmt.mmss($0))" } ?? ""))
                }
            }
        }
    }

    private var noTrain: some View {
        Section("Your train") {
            Text(data.predictedBoards.isEmpty ? "Waiting for the live feeds." : "No started train in the feeds has an ETA at both your stops yet; the expected time above stands in.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var lines: some View {
        var keys: [String] = []
        for k in option.legs.flatMap({ $0.keys }) where !keys.contains(k) { keys.append(k) }
        return Section("Lines right now") {
            ForEach(keys, id: \.self) { k in
                let title = "\(k.split(separator: "_").first ?? "") toward \(schedule.lines[k]?.names.last ?? "")"
                if let b = data.boards[k] {
                    let lp = data.predictions[k]?[data.scenario] ?? data.predictions[k]?["baseline"]
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title).font(.subheadline.weight(.semibold))
                        Text("\(b.trains.count) trains in the feed · \(b.nHolding) held · \(b.nStalled) overdue · \(b.nFeedOptimistic) with an optimistic feed ETA").font(.caption).foregroundStyle(.secondary)
                        if let lp = lp {
                            Text(engineText(lp, line: schedule.lines[k])).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                } else {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title).font(.subheadline.weight(.semibold))
                        Text("no board yet").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private var alerts: some View {
        let routes = Array(Set(option.legs.flatMap { $0.routes })).sorted()
        let list = data.alertsFor(routes: routes)
        return Section("Alerts on these lines") {
            if list.isEmpty { Text("None active.").font(.caption).foregroundStyle(.secondary) }
            ForEach(list) { a in
                let ev = alertEvidence(a, data: data)
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(a.kind): \(a.header)").font(.caption).foregroundStyle(a.kind == "delay" ? Color.red : Color.primary)
                    Text(ev.text).font(.caption2.weight(.medium)).foregroundStyle(ev.corroborated ? Color.red : Color.secondary)
                }
            }
        }
    }

    private var dataBehind: some View {
        Section("Data behind this") {
            row("Prediction engine", data.model?.summary ?? "physical priors", data.model?.generatedAt.map { "tables published \($0)" } ?? "no fitted tables loaded")
            row("Hold log", data.holds.map { "\($0.n) holds" } ?? "–", "holds at stops, per day and median length")
            row("Deviation grids", "\(option.legs.compactMap { data.deviations[$0.primaryKey] }.count) of \(option.legs.count) legs", "typical time lost per stop by hour")
            row("Segment runs", data.segments.map { "\($0.n) runs" } ?? "–", "measured running times between stops")
            row("Feeds", data.offline ? "offline, last seen \(data.lastFeedFetch.map { Fmt.hhmm($0.timeIntervalSince1970) } ?? "earlier")" : "live", "last poll \(data.lastUpdate.map { Fmt.hhmmss($0.timeIntervalSince1970) } ?? "–") · feed stamp \(Fmt.hhmmss(data.lastFeedTs))")
            row("Hold assumption", scenarioText, "changes the engine's ETAs for a held train and the trains behind it")
        }
    }

    private var howItWorks: some View {
        Section("How it works") {
            VStack(alignment: .leading, spacing: 8) {
                para("Expected time", "Half the scheduled headway as the wait at the origin, the scheduled rides, the time trains typically lose on those stretches at this hour from the deviation grids, a hold risk from the hold log, and the walk plus half a headway at any change.")
                para("Your train", "Every started train in the feeds with an ETA at both of your stops is a candidate. The itinerary boards the earliest one after now; with a change, the first connecting train reachable after the walk.")
                para("Engine ETA", "The feed's ETA is corrected by the bias measured for that horizon on that line, then blended with the timetable carried forward by the train's current lateness, each weighted by its spread. A held train adds the expected remaining hold from the hold-survival table, its 90th percentile if the hold drags on, nothing if it clears now. Trains behind a slow one are pushed back to keep the minimum headway.")
                para("Lateness", "Matched to the timetable by trip id, else the nearest scheduled trip within 15 minutes. Where the train is on the track gives a second estimate; the later of the two is used, and a feed that is more than a minute ahead of the position is flagged as optimistic.")
                para("Badge", "Expected extra minutes to your destination against the timetable: the ride slower than scheduled, any wait beyond the usual headway, extra time at the change and half the penalty of a tight connection. Grey under 5 minutes, yellow from 5, red from 10.")
                para("Ranking", "Routes with a train in the feeds first, by arrival; the rest by expected time. When your countdown ends, the next train on this route takes over.")
            }
        }
    }

    // MARK: - helpers

    private var scenarioText: String {
        switch data.scenario {
        case "hold_persists": return "hold drags on"
        case "clears_now": return "hold clears now"
        default: return "hold ends as usual"
        }
    }

    private func positionNote(_ t: LiveTrain) -> String {
        guard let p = t.position else { return "no vehicle position in the feed; only the trip's ETAs" }
        var bits: [String] = []
        if p.holding { bits.append("held at a stop for \(Fmt.mmss(p.sinceSec))") }
        if p.stalled { bits.append("overdue between stops") }
        if let e = p.expectedRunSec, p.status != "STOPPED_AT" { bits.append("\(Fmt.mmss(p.sinceSec)) into a \(Fmt.mmss(e)) run") }
        return bits.isEmpty ? "from the vehicle position in the feed" : bits.joined(separator: " · ")
    }

    private func latenessNote(_ t: LiveTrain) -> String {
        var bits: [String] = []
        if let m = t.schedMethod { bits.append(m == "trip_stem" ? "matched to its scheduled trip" : "nearest scheduled trip") }
        switch t.corroboration {
        case "agree": bits.append("feed and position agree")
        case "feed_optimistic": bits.append("feed looks optimistic; the position-based figure is used")
        default: bits.append("position unknown, feed only")
        }
        return bits.joined(separator: " · ")
    }

    private func arrivalNote(_ tc: TripCandidate) -> String {
        switch tc.arriveSource {
        case "blend": return "engine: feed ETA blended with the timetable carried by its lateness"
        case "feed": return "engine: calibrated feed ETA"
        default: return "from the feed"
        }
    }

    private func engineText(_ lp: LinePrediction, line: LineTopology?) -> String {
        var s = "Engine: "
        if let w = lp.worstGap {
            let name = line.map { w.idx < $0.names.count ? $0.names[w.idx] : "stop \(w.idx)" } ?? "stop \(w.idx)"
            s += "largest projected gap \(Fmt.minTxt(w.gapSec)) at \(name) around \(Fmt.hhmm(w.atTs))"
        } else {
            s += "no gap projected in the next hour"
        }
        if lp.nKnockOn > 0 { s += " · \(lp.nKnockOn) held back by the train ahead (\(Fmt.minTxt(lp.knockOnTotalSec)) in total)" }
        return s
    }

    private func row(_ label: String, _ value: String, _ note: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline) {
                Text(label).font(.subheadline)
                Spacer()
                Text(value).font(.subheadline.weight(.semibold)).multilineTextAlignment(.trailing)
            }
            Text(note).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func para(_ title: String, _ body: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption.weight(.semibold))
            Text(body).font(.caption).foregroundStyle(.secondary)
        }
    }
}
