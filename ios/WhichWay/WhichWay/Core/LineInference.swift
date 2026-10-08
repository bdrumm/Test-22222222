import Foundation

// Which train did the rider actually board? The sensors feel the moment the train pulls away; the feeds say when
// each train of each line reached the platform. The plan's line is the prior (the rider usually takes the route
// they picked), the felt departure against each train's time at the stop is the first evidence, the number of
// station stops felt during the ride (an express makes fewer than a local) and the time the rider walked off
// against each train's arrival at the alighting stop corroborate it. The transfer leg runs the same way with a
// stronger prior for the plan's line. Everything here is pure and runs in the package tests.

/// A train the rider could have boarded on one leg of the route: the line it runs, the feed's time at the
/// boarding stop (the last one published before the train moved on), the station stops it makes to the alighting
/// stop and the feed's time there.
struct BoardingCandidate: Equatable {
    var trainId: String          // LiveTrain.id, "F_N|tripId"
    var key: String              // line key, "F_N"
    var route: String            // "F"
    var boardTs: Double          // the feed's time at the boarding stop
    var alightTs: Double?        // the feed's time at the alighting stop, when this line serves it
    var stopsToAlight: Int?      // station stops from the boarding stop to the alighting stop on this line
    var chosen: Bool             // the itinerary's own train
    var onLeg: Bool              // one of the leg's lines (else another line at the station, same direction)
    var boardIdx: Int = 0        // the boarding stop's index on this line
    var alightIdx: Int? = nil    // the alighting stop's index on this line, when the line serves it
    var stoppedAtBoardTs: Double? = nil   // the last poll at which the feed placed the train standing at the boarding stop
    var stopTs: [Int: Double] = [:]       // the feed's latest time at each stop past the boarding stop (kept once the train has passed)
    var progressIdx: Int? = nil           // the train's next stop index at the last poll
    var samePlatform: Bool = true         // boards at the leg's own platform (else another platform of the station: the 8 Av L beside the A/C/E)
}

/// The train and line a leg was ridden on, as kept in the trip's record and shared if the rider shares trips.
struct BoardedLeg: Codable, Equatable {
    var leg: Int
    var key: String
    var trainId: String?
    var chosenKey: String?
    var confidence: Double
    var verdict: LineBelief.Verdict
    var evidence: [String]
}

/// What the phone believes about the train the rider boarded on a leg, after each piece of evidence.
struct LineBelief: Codable, Equatable {
    enum Verdict: String, Codable { case onPlan, switched, unsure }

    var leg: Int
    var byTrain: [String: Double]    // posterior per candidate train id
    var byLine: [String: Double]     // the same, summed per line key
    var chosenKey: String?           // the plan's line for this leg
    var evidence: [String] = []      // departure, stops, alighting

    /// The share held by "none of these trains": a train the feed did not show, or no train at all.
    static let noneKey = "?"
    var noneShare: Double { byLine[LineBelief.noneKey] ?? 0 }
    private var lines: [String: Double] { byLine.filter { $0.key != LineBelief.noneKey } }

    var bestKey: String? { lines.max { a, b in a.value != b.value ? a.value < b.value : a.key > b.key }?.key }
    var bestTrain: String? {
        guard let k = bestKey else { return nil }
        return byTrain.filter { $0.key.hasPrefix(k + "|") }.max { a, b in a.value != b.value ? a.value < b.value : a.key > b.key }?.key
    }
    var confidence: Double { lines.values.max() ?? 0 }
    var route: String? { bestKey.map { String($0.split(separator: "_").first ?? "") } }
    var chosenRoute: String? { chosenKey.map { String($0.split(separator: "_").first ?? "") } }

    /// Settled enough to say so: the best line holds 60% and leads the runner-up, or "none of these", by 20 points.
    var settled: Bool {
        let sorted = lines.values.sorted(by: >)
        guard let top = sorted.first, top >= 0.6 else { return false }
        let runnerUp = max(sorted.count > 1 ? sorted[1] : 0, noneShare)
        return top - runnerUp >= 0.2
    }

    var verdict: Verdict {
        guard settled, let b = bestKey else { return .unsure }
        return b == chosenKey ? .onPlan : .switched
    }

    var boarded: BoardedLeg? {
        guard let k = bestKey else { return nil }
        return BoardedLeg(leg: leg, key: k, trainId: bestTrain, chosenKey: chosenKey, confidence: confidence, verdict: verdict, evidence: evidence)
    }

    /// Taken from the plan because the boarding time passed with no departure felt (or none matched a train).
    var assumed: Bool { evidence.contains("schedule") || evidence.contains("plan") }
    /// The rider said so.
    var byHand: Bool { evidence.contains("hand") }

    /// The plan's own train and line, with nothing measured yet: the fallback when the sensors are off, missed
    /// the departure, or felt one that matched no train in the feed. `evidence` says which ("schedule", "plan").
    static func fromPlan(leg: Int, chosenKey: String?, chosenTrainId: String?, evidence: String) -> LineBelief? {
        guard let k = chosenKey else { return nil }
        return LineBelief(leg: leg, byTrain: chosenTrainId.map { [$0: 1.0] } ?? [:], byLine: [k: 1.0], chosenKey: k, evidence: [evidence])
    }

    /// The rider's own word: the line they boarded, with the candidate train of that line nearest the departure
    /// they felt (if any), and nothing later overrides it.
    static func byHand(leg: Int, key: String, chosenKey: String?, candidates: [BoardingCandidate], departedTs: Double?) -> LineBelief {
        let onLine = candidates.filter { $0.key == key }
        let train = departedTs.flatMap { ts in onLine.min { abs($0.boardTs - ts) < abs($1.boardTs - ts) } } ?? onLine.first
        return LineBelief(leg: leg, byTrain: train.map { [$0.trainId: 1.0] } ?? [:], byLine: [key: 1.0], chosenKey: chosenKey, evidence: ["hand"])
    }
}

/// Remembers, poll by poll, the feed's time at the boarding stop for every train of the lines at the platform,
/// so the train that just left can be matched after the feed has moved it on to its next stop.
struct DepartureLog: Equatable {
    struct Entry: Equatable {
        var trainId: String
        var key: String
        var route: String
        var boardTs: Double
        var alightTs: Double?
        var stops: Int?
        var onLeg: Bool
        var lastSeenTs: Double
        var boardIdx: Int = 0
        var alightIdx: Int? = nil
        var stoppedAtBoardTs: Double? = nil
        var stopTs: [Int: Double] = [:]
        var progressIdx: Int? = nil
        var samePlatform: Bool = true
    }

    private(set) var entries: [String: Entry] = [:]
    /// The last poll the log took in.
    private(set) var lastPollTs = 0.0
    var keepSec = 3600.0

    /// One poll of one line's board. A train is taken up while it still lists the boarding stop; once known it
    /// keeps being followed after it has left: its next stop, the feed's time at every stop past the boarding stop
    /// (the last published time stands as the arrival once the stop has gone from its list), and the last poll at
    /// which the feed placed it standing at the platform. `span` is the leg's stop indices on this line (nil for a
    /// line that is at the platform but not on the leg).
    mutating func observe(trains: [LiveTrain], key: String, boardIdx: Int, span: (from: Int, to: Int)?, onLeg: Bool, now: Double, samePlatform: Bool = true) {
        let route = String(key.split(separator: "_").first ?? "")
        lastPollTs = max(lastPollTs, now)
        for t in trains {
            let pts = t.feedPoints ?? t.points
            let atBoard = pts.first(where: { $0.idx == boardIdx })?.ts
            var e: Entry
            if let known = entries[t.id] {
                e = known
            } else {
                guard let b = atBoard else { continue }
                e = Entry(trainId: t.id, key: key, route: route, boardTs: b, alightTs: nil, stops: nil, onLeg: onLeg, lastSeenTs: now, boardIdx: boardIdx, samePlatform: samePlatform)
            }
            if let b = atBoard { e.boardTs = b }
            e.lastSeenTs = now
            e.progressIdx = t.nextIdx
            for pt in pts where pt.idx > boardIdx { e.stopTs[pt.idx] = pt.ts }
            if let s = span {
                e.alightTs = pts.first(where: { $0.idx == s.to })?.ts ?? e.alightTs
                e.stops = s.to - s.from
                e.alightIdx = s.to
            }
            // standing at the platform just before the pull-away says which train left; a train laying over at its
            // terminal always stands there, so it says nothing
            if let pos = t.position, pos.status == "STOPPED_AT", pos.stopIdx == boardIdx, !pos.atTerminal, boardIdx > 0 { e.stoppedAtBoardTs = now }
            entries[t.id] = e
        }
        entries = entries.filter { now - $0.value.lastSeenTs <= keepSec }
    }

    private func candidate(_ e: Entry, chosenTrainId: String?) -> BoardingCandidate {
        BoardingCandidate(trainId: e.trainId, key: e.key, route: e.route, boardTs: e.boardTs, alightTs: e.alightTs,
                          stopsToAlight: e.stops, chosen: e.trainId == chosenTrainId, onLeg: e.onLeg, boardIdx: e.boardIdx, alightIdx: e.alightIdx,
                          stoppedAtBoardTs: e.stoppedAtBoardTs, stopTs: e.stopTs, progressIdx: e.progressIdx, samePlatform: e.samePlatform)
    }

    /// The trains whose time at the boarding stop fell within `windowSec` of a departure felt at `ts`.
    func candidates(departedTs ts: Double, windowSec: Double, chosenTrainId: String?) -> [BoardingCandidate] {
        entries.values
            .filter { abs($0.boardTs - ts) <= windowSec }
            .map { candidate($0, chosenTrainId: chosenTrainId) }
            .sorted { $0.boardTs != $1.boardTs ? $0.boardTs < $1.boardTs : $0.trainId < $1.trainId }
    }

    /// The same candidates with what the log has learned about them since (progress, stop times, arrival).
    func refreshed(_ cands: [BoardingCandidate]) -> [BoardingCandidate] {
        cands.map { c in
            guard let e = entries[c.trainId] else { return c }
            var r = candidate(e, chosenTrainId: nil)
            r.chosen = c.chosen
            return r
        }
    }

    func progressIdx(of trainId: String) -> Int? { entries[trainId]?.progressIdx }

    /// A train the rider says they are on, taken up after it has left the platform (the log never saw it there):
    /// from here on the feed's polls keep its stop times and progress like any other entry.
    mutating func seed(_ c: BoardingCandidate, now: Double) {
        if var e = entries[c.trainId] {
            e.lastSeenTs = now
            entries[c.trainId] = e
            return
        }
        entries[c.trainId] = Entry(trainId: c.trainId, key: c.key, route: c.route, boardTs: c.boardTs, alightTs: c.alightTs, stops: c.stopsToAlight, onLeg: c.onLeg,
                                   lastSeenTs: now, boardIdx: c.boardIdx, alightIdx: c.alightIdx, stoppedAtBoardTs: c.stoppedAtBoardTs, stopTs: c.stopTs, progressIdx: c.progressIdx,
                                   samePlatform: c.samePlatform)
    }

    mutating func reset() { entries = [:]; lastPollTs = 0 }

    /// Did a train leave the boarding platform about when a pull-away was felt at `ts`? `left`: one went past the
    /// platform and pulled away within `windowSec` of it (by the feed's time, put back to the platform moment);
    /// `waiting`: some train the log follows has still not reached the platform, so the feed is alive here.
    func departureCheck(at ts: Double, windowSec: Double = 120) -> (left: Bool, waiting: Bool) {
        var left = false, waiting = false
        for e in entries.values {
            let passed = (e.progressIdx ?? e.boardIdx) > e.boardIdx
            if passed, abs(PlatformTiming.pullsAway(e.boardTs, route: e.route) - ts) <= windowSec { left = true }
            if !passed { waiting = true }
        }
        return (left, waiting)
    }
}

/// The estimator. Likelihoods are a Gaussian on a floor, so one odd reading never rules a train out for good.
struct LineInference {
    var priorChosenFirstLeg = 0.5          // the plan is a coin toss against the other trains at the platform: the sensors decide
    var priorChosenLaterLeg = 0.7          // once the trip is under way the plan's transfer line is the stronger bet
    var otherLineAtPlatformWeight = 0.8    // a line that is not one of the leg's (a G beside the F) is a real choice, nearly on a par
    var otherPlatformWeight = 0.4          // a line at another platform of the station (the 8 Av L from the A/C/E) needs a walk the plan did not have
    var priorNone = 0.1                    // "none of these trains": one the feed did not show, or a departure that was not a train
    var noneLike = 0.2                     // how well "none of these" fits each piece of evidence: a candidate about 1.8 sigma off
    var dwellSec = PlatformTiming.dwellSec // the train pulls away about this long after it really reached the platform
    /// From a feed time at a stop to the moment the train really reached the platform (PlatformTiming); the feed's
    /// time runs 30 to 90 s late depending on the line. Tests that reason in feed time set this to zero.
    var recordedLag: (String) -> Double = { PlatformTiming.recordedLag(route: $0) }
    var departureSigmaSec = 40.0
    var departureWindowSec = 300.0
    var alightLagSec = 20.0                // the rider walks off about this long after the train really reached the platform
    var alightSigmaSec = 75.0
    var stopMiscountFactor = 0.35          // each station stop felt too many or too few costs this factor
    var floor = 0.04                       // every candidate keeps this share of the peak likelihood
    var stoppedAtSigmaSec = 60.0           // the feed placed the train at the platform this long before the felt pull-away
    var stoppedAtFloor = 0.3               // a candidate the feed never showed standing at the platform keeps this much
    var rideSigmaSec = 90.0                // a felt station stop against the feed's time at the stop
    var stopLagSec = 0.0                   // a felt station stop begins when the train really reaches the platform
    var locationSigmaM = 150.0             // a fix after walking off against the alighting stop's position

    /// The plan's line's share of the prior: the default for the leg, pulled halfway toward what this rider has
    /// actually done on this trip before, once that is known.
    func priorChosen(leg: Int, learnedShare: Double?) -> Double {
        let base = leg == 0 ? priorChosenFirstLeg : priorChosenLaterLeg
        guard let s = learnedShare else { return base }
        return min(0.95, max(0.3, 0.5 * base + 0.5 * s))
    }

    /// The prior over candidates: the plan's line gets its share, the leg's other lines split the rest, a line
    /// at the platform that is not on the leg counts half; within a line the trains are equal.
    func prior(_ cands: [BoardingCandidate], leg: Int, chosenKey: String?, learnedShare: Double? = nil) -> [String: Double] {
        guard !cands.isEmpty else { return [:] }
        let pc = priorChosen(leg: leg, learnedShare: learnedShare)
        var lineWeight: [String: Double] = [:]
        for c in cands where c.key != chosenKey { lineWeight[c.key] = c.onLeg ? 1.0 : (c.samePlatform ? otherLineAtPlatformWeight : otherPlatformWeight) }
        let hasChosen = chosenKey != nil && cands.contains { $0.key == chosenKey }
        let otherTotal = lineWeight.values.reduce(0, +)
        var lineMass: [String: Double] = [:]
        if hasChosen, let ck = chosenKey {
            lineMass[ck] = otherTotal > 0 ? pc : 1.0
            for (k, w) in lineWeight { lineMass[k] = (1 - pc) * w / otherTotal }
        } else {
            for (k, w) in lineWeight { lineMass[k] = otherTotal > 0 ? w / otherTotal : 0 }
        }
        var countByLine: [String: Int] = [:]
        for c in cands { countByLine[c.key, default: 0] += 1 }
        var out: [String: Double] = [:]
        for c in cands { out[c.trainId] = (1 - priorNone) * (lineMass[c.key] ?? 0) / Double(countByLine[c.key] ?? 1) }
        out[LineBelief.noneKey] = priorNone
        return out
    }

    private func gaussian(_ delta: Double, sigma: Double) -> Double {
        floor + (1 - floor) * exp(-0.5 * (delta / sigma) * (delta / sigma))
    }

    private func belief(leg: Int, chosenKey: String?, weights: [String: Double], cands: [BoardingCandidate], evidence: [String]) -> LineBelief {
        let total = weights.values.reduce(0, +)
        var byTrain: [String: Double] = [:]
        var byLine: [String: Double] = [:]
        if total > 0 {
            for (id, w) in weights { byTrain[id] = w / total }
            for c in cands { byLine[c.key, default: 0] += byTrain[c.trainId] ?? 0 }
            if let n = byTrain[LineBelief.noneKey] { byLine[LineBelief.noneKey] = n }
        }
        return LineBelief(leg: leg, byTrain: byTrain, byLine: byLine, chosenKey: chosenKey, evidence: evidence)
    }

    /// "None of these" against one piece of evidence: `noneLike` when the evidence said something about the
    /// candidates, unchanged when it said nothing.
    private func none(_ b: LineBelief, informative: Bool) -> [String: Double] {
        guard let n = b.byTrain[LineBelief.noneKey] else { return [:] }
        return [LineBelief.noneKey: n * (informative ? noneLike : 1)]
    }

    /// The belief right after the sensors felt the train leave at `departedTs`: each train's time at the stop
    /// against that moment, and, when the feed reported vehicle positions, whether it had the train standing at
    /// the platform just before (a train never seen standing there keeps `stoppedAtFloor`).
    func fromDeparture(_ cands: [BoardingCandidate], departedTs: Double, leg: Int, chosenKey: String?, learnedShare: Double? = nil) -> LineBelief {
        let p = prior(cands, leg: leg, chosenKey: chosenKey, learnedShare: learnedShare)
        let positions = cands.contains { $0.stoppedAtBoardTs != nil }
        var w: [String: Double] = [:]
        for c in cands {
            let delta = departedTs - (c.boardTs - recordedLag(c.route) + dwellSec)
            var like = gaussian(delta, sigma: departureSigmaSec)
            if positions {
                like *= c.stoppedAtBoardTs.map { stoppedAtFloor + (1 - stoppedAtFloor) * exp(-0.5 * pow(max(0, departedTs - $0 - 15) / stoppedAtSigmaSec, 2)) } ?? stoppedAtFloor
            }
            w[c.trainId] = (p[c.trainId] ?? 0) * like
        }
        if let n = p[LineBelief.noneKey], !cands.isEmpty { w[LineBelief.noneKey] = n * noneLike * (positions ? stoppedAtFloor : 1) }
        var evidence: [String] = cands.isEmpty ? [] : ["departure"]
        if positions { evidence.append("position") }
        return belief(leg: leg, chosenKey: chosenKey, weights: w, cands: cands, evidence: evidence)
    }

    /// The belief during the ride: the station stops felt so far (`stopTimes`, when each began) against each
    /// train's actual times at the stops past the boarding stop, and how many stops the train has reached by
    /// `now` against how many were felt. Recomputed from the departure belief at every poll, so it never
    /// compounds.
    func withRide(_ b: LineBelief, candidates cands: [BoardingCandidate], stopTimes: [Double], now: Double) -> LineBelief {
        guard !b.byTrain.isEmpty, !stopTimes.isEmpty, cands.contains(where: { !$0.stopTs.isEmpty }) else { return b }
        var w: [String: Double] = none(b, informative: true)
        if let n = w[LineBelief.noneKey] { w[LineBelief.noneKey] = n * pow(noneLike, Double(max(0, stopTimes.count - 1))) }
        for c in cands {
            let lag = recordedLag(c.route)
            let expected = c.stopTs.filter { $0.key > c.boardIdx }.sorted { $0.key < $1.key }.map { $0.value - lag + stopLagSec }
            guard !expected.isEmpty else { w[c.trainId] = b.byTrain[c.trainId] ?? 0; continue }
            let reached = expected.filter { $0 <= now }.count
            var like = floor + (1 - floor) * pow(stopMiscountFactor, Double(abs(stopTimes.count - reached)))
            for felt in stopTimes {
                let nearest = expected.map { abs(felt - $0) }.min() ?? Double.infinity
                like *= gaussian(nearest, sigma: rideSigmaSec)
            }
            w[c.trainId] = (b.byTrain[c.trainId] ?? 0) * like
        }
        return belief(leg: b.leg, chosenKey: b.chosenKey, weights: w, cands: cands, evidence: b.evidence + ["ride"])
    }

    /// The belief after a location fix following the walk-off: `distanceM` is the fix's distance to each
    /// candidate's alighting stop (a candidate whose stop position is unknown keeps its weight).
    func withLocation(_ b: LineBelief, candidates cands: [BoardingCandidate], distanceM: [String: Double]) -> LineBelief {
        guard !b.byTrain.isEmpty, !distanceM.isEmpty else { return b }
        var w: [String: Double] = none(b, informative: true)
        for c in cands {
            let like = distanceM[c.trainId].map { gaussian($0, sigma: locationSigmaM) } ?? 1.0
            w[c.trainId] = (b.byTrain[c.trainId] ?? 0) * like
        }
        return belief(leg: b.leg, chosenKey: b.chosenKey, weights: w, cands: cands, evidence: b.evidence + ["location"])
    }

    /// The belief after the ride: `stopsFelt` station stops were felt between the departure and walking off.
    func withStops(_ b: LineBelief, candidates cands: [BoardingCandidate], stopsFelt: Int) -> LineBelief {
        guard !b.byTrain.isEmpty, cands.contains(where: { $0.stopsToAlight != nil }) else { return b }
        var w: [String: Double] = none(b, informative: true)
        for c in cands {
            let like = c.stopsToAlight.map { floor + (1 - floor) * pow(stopMiscountFactor, Double(abs(stopsFelt - $0))) } ?? 1.0
            w[c.trainId] = (b.byTrain[c.trainId] ?? 0) * like
        }
        return belief(leg: b.leg, chosenKey: b.chosenKey, weights: w, cands: cands, evidence: b.evidence + ["stops"])
    }

    /// The belief after the rider walked off at `alightedTs`, against each train's arrival at the alighting stop.
    /// A train that does not serve that stop keeps only the floor.
    func withAlighting(_ b: LineBelief, candidates cands: [BoardingCandidate], alightedTs: Double) -> LineBelief {
        guard !b.byTrain.isEmpty, cands.contains(where: { $0.alightTs != nil }) else { return b }
        var w: [String: Double] = none(b, informative: true)
        for c in cands {
            let like = c.alightTs.map { gaussian(alightedTs - ($0 - recordedLag(c.route) + alightLagSec), sigma: alightSigmaSec) } ?? floor
            w[c.trainId] = (b.byTrain[c.trainId] ?? 0) * like
        }
        return belief(leg: b.leg, chosenKey: b.chosenKey, weights: w, cands: cands, evidence: b.evidence + ["alighting"])
    }
}
