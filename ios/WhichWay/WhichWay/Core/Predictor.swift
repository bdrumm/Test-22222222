import Foundation

// The client prediction engine: a port of mta_delay_insights/realtime/client_model.py (predict_train /
// predict_line), tested against the Python reference with a fixture (ios/WhichWayCore). The tables come from
// data/client_model.json: the feed's ETA error by route and horizon, the remaining hold given the time already
// held, and how lateness carries k stops ahead.

struct CalBucket: Decodable, Equatable {
    var n: Int
    var bias: Double
    var p10: Double
    var p90: Double
}

struct EtaCalibration: Decodable {
    var horizons: [Double] = Predictor.horizonEdges
    var n: Int = 0
    var all: [CalBucket] = []
    var byRoute: [String: [CalBucket]] = [:]
    enum CodingKeys: String, CodingKey { case horizons, n, all, byRoute = "by_route" }
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        horizons = try c.decodeIfPresent([Double].self, forKey: .horizons) ?? Predictor.horizonEdges
        n = try c.decodeIfPresent(Int.self, forKey: .n) ?? 0
        all = try c.decodeIfPresent([CalBucket].self, forKey: .all) ?? []
        byRoute = try c.decodeIfPresent([String: [CalBucket]].self, forKey: .byRoute) ?? [:]
    }
}

struct HoldSurvival: Decodable {
    var elapsed: [Double] = []
    var n: [Int] = []
    var expected: [Double] = []
    var p50: [Double] = []
    var p90: [Double] = []
    var clears2min: [Double] = []
    var nHolds: Int = 0
    enum CodingKeys: String, CodingKey { case elapsed, n, expected, p50, p90, clears2min = "clears_2min", nHolds = "n_holds" }
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        elapsed = try c.decodeIfPresent([Double].self, forKey: .elapsed) ?? []
        n = try c.decodeIfPresent([Int].self, forKey: .n) ?? []
        expected = try c.decodeIfPresent([Double].self, forKey: .expected) ?? []
        p50 = try c.decodeIfPresent([Double].self, forKey: .p50) ?? []
        p90 = try c.decodeIfPresent([Double].self, forKey: .p90) ?? []
        clears2min = try c.decodeIfPresent([Double].self, forKey: .clears2min) ?? []
        nHolds = try c.decodeIfPresent(Int.self, forKey: .nHolds) ?? 0
    }
}

struct CarryTable: Decodable {
    var slope: [Double]
    var intercept: [Double]
    var residStd: [Double]
    var n: [Int]
    enum CodingKeys: String, CodingKey { case slope, intercept, residStd = "resid_std", n }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slope = try c.decodeIfPresent([Double].self, forKey: .slope) ?? []
        intercept = try c.decodeIfPresent([Double].self, forKey: .intercept) ?? []
        residStd = try c.decodeIfPresent([Double].self, forKey: .residStd) ?? []
        n = try c.decodeIfPresent([Int].self, forKey: .n) ?? []
    }
}

struct LatenessCarry: Decodable {
    var maxK: Int = 12
    var all: CarryTable? = nil
    var byRoute: [String: CarryTable] = [:]
    var n: Int = 0
    enum CodingKeys: String, CodingKey { case maxK = "max_k", all, byRoute = "by_route", n }
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        maxK = try c.decodeIfPresent(Int.self, forKey: .maxK) ?? 12
        all = try c.decodeIfPresent(CarryTable.self, forKey: .all)
        byRoute = try c.decodeIfPresent([String: CarryTable].self, forKey: .byRoute) ?? [:]
        n = try c.decodeIfPresent(Int.self, forKey: .n) ?? 0
    }
}

struct ClientModel: Decodable {
    var version: Int = 0
    var generatedAt: String? = nil
    var etaCalibration: EtaCalibration? = nil
    var holdSurvival: HoldSurvival? = nil
    var latenessCarry: LatenessCarry? = nil
    enum CodingKeys: String, CodingKey { case version, generatedAt = "generated_at", etaCalibration = "eta_calibration", holdSurvival = "hold_survival", latenessCarry = "lateness_carry" }
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 0
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt)
        etaCalibration = try c.decodeIfPresent(EtaCalibration.self, forKey: .etaCalibration)
        holdSurvival = try c.decodeIfPresent(HoldSurvival.self, forKey: .holdSurvival)
        latenessCarry = try c.decodeIfPresent(LatenessCarry.self, forKey: .latenessCarry)
    }

    var summary: String {
        let cal = etaCalibration?.n ?? 0
        let routes = etaCalibration?.byRoute.count ?? 0
        let holds = holdSurvival?.nHolds ?? 0
        return "\(cal) ETA samples · \(routes) lines calibrated · \(holds) holds"
    }
}

struct RemainingHold {
    var expected: Double
    var p50: Double
    var p90: Double
    var clears2min: Double
}

// MARK: - predictor input (language-neutral, matches the fixture JSON)

struct PredictorPosition: Decodable {
    var status: String? = nil
    var sinceSec: Double = 0
    var holding: Bool = false
    var stalled: Bool = false
    enum CodingKeys: String, CodingKey { case status, sinceSec = "since_sec", holding, stalled }
    init(status: String?, sinceSec: Double, holding: Bool, stalled: Bool) { self.status = status; self.sinceSec = sinceSec; self.holding = holding; self.stalled = stalled }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = try c.decodeIfPresent(String.self, forKey: .status)
        sinceSec = try c.decodeIfPresent(Double.self, forKey: .sinceSec) ?? 0
        holding = try c.decodeIfPresent(Bool.self, forKey: .holding) ?? false
        stalled = try c.decodeIfPresent(Bool.self, forKey: .stalled) ?? false
    }
}

struct PredictorTrain: Decodable {
    var tripId: String
    var route: String
    var nextIdx: Int?
    var points: [[Double]]
    var latenessSec: Double?
    var effectiveLatenessSec: Double?
    var schedTs: Double?
    var position: PredictorPosition?
    enum CodingKeys: String, CodingKey { case tripId = "trip_id", route, nextIdx = "next_idx", points, latenessSec = "lateness_sec", effectiveLatenessSec = "effective_lateness_sec", schedTs = "sched_ts", position }
    init(tripId: String, route: String, nextIdx: Int?, points: [[Double]], latenessSec: Double?, effectiveLatenessSec: Double?, schedTs: Double?, position: PredictorPosition?) {
        self.tripId = tripId; self.route = route; self.nextIdx = nextIdx; self.points = points; self.latenessSec = latenessSec
        self.effectiveLatenessSec = effectiveLatenessSec; self.schedTs = schedTs; self.position = position
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tripId = try c.decodeIfPresent(String.self, forKey: .tripId) ?? ""
        route = try c.decodeIfPresent(String.self, forKey: .route) ?? ""
        nextIdx = try c.decodeIfPresent(Int.self, forKey: .nextIdx)
        points = (try c.decodeIfPresent([[Double?]].self, forKey: .points) ?? []).compactMap { p in p.count >= 2 && p[0] != nil && p[1] != nil ? [p[0]!, p[1]!] : nil }
        latenessSec = try c.decodeIfPresent(Double.self, forKey: .latenessSec)
        effectiveLatenessSec = try c.decodeIfPresent(Double.self, forKey: .effectiveLatenessSec)
        schedTs = try c.decodeIfPresent(Double.self, forKey: .schedTs)
        position = try c.decodeIfPresent(PredictorPosition.self, forKey: .position)
    }
}

struct PredictorLine: Decodable {
    var stops: [String]
    var runSec: [Double?]
    enum CodingKeys: String, CodingKey { case stops, runSec = "run_sec" }
    init(stops: [String], runSec: [Double?]) { self.stops = stops; self.runSec = runSec }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        stops = try c.decodeIfPresent([String].self, forKey: .stops) ?? []
        runSec = try c.decodeIfPresent([Double?].self, forKey: .runSec) ?? []
    }
    init(_ line: LineTopology) { stops = line.stops; runSec = line.runSec.map { $0.map(Double.init) } }
}

// MARK: - output

struct PredictedPoint {
    var idx: Int
    var feedTs: Double
    var etaTs: Double
    var loTs: Double
    var hiTs: Double
    var source: String
}

struct PredictedTrain {
    var tripId: String
    var points: [PredictedPoint]
    var holdExtraSec: Double
    var knockOnSec: Double
    var scenario: String

    func point(at idx: Int) -> PredictedPoint? { points.first { $0.idx == idx } }
}

struct StopPrediction {
    var idx: Int
    var nArrivals: Int
    var nextTs: Double?
    var maxHeadwaySec: Double?
}

struct WorstGap {
    var gapSec: Double
    var idx: Int
    var atTs: Double
}

struct LinePrediction {
    var scenario: String
    var now: Double
    var trains: [PredictedTrain]
    var perStop: [StopPrediction]
    var worstGap: WorstGap?
    var nKnockOn: Int
    var knockOnTotalSec: Double

    func train(_ tripId: String) -> PredictedTrain? { trains.first { $0.tripId == tripId } }
}

enum Predictor {
    static let horizonEdges: [Double] = [0, 120, 300, 600, 1200, 2400, 3600]
    static let scenarios = ["baseline", "hold_persists", "clears_now"]
    static let minStopGapSec = 30.0
    static let minHeadwaySec = 90.0
    static let priorHold = RemainingHold(expected: 300, p50: 180, p90: 720, clears2min: 0.35)

    static func priorSpread(_ h: Double) -> (Double, Double) { (-45.0 - 0.05 * h, 60.0 + 0.15 * h) }

    static func horizonIndex(_ h: Double) -> Int {
        var i = 0
        while i < horizonEdges.count - 1 {
            if horizonEdges[i] <= h && h < horizonEdges[i + 1] { return i }
            i += 1
        }
        return h < 0 ? 0 : horizonEdges.count - 2
    }

    static func calibrationAt(_ model: ClientModel?, route: String, horizon: Double) -> CalBucket {
        let i = horizonIndex(horizon)
        let cal = model?.etaCalibration
        let table = cal?.byRoute[route] ?? cal?.all
        if let t = table, i < t.count { return t[i] }
        let (p10, p90) = priorSpread(max(0, horizon))
        return CalBucket(n: 0, bias: 0, p10: p10, p90: p90)
    }

    static func carryAt(_ model: ClientModel?, route: String, k: Int) -> (slope: Double, intercept: Double, residStd: Double)? {
        let lc = model?.latenessCarry
        guard let t = lc?.byRoute[route] ?? lc?.all, k >= 1, k <= t.slope.count, k <= t.intercept.count, k <= t.residStd.count else { return nil }
        return (t.slope[k - 1], t.intercept[k - 1], t.residStd[k - 1])
    }

    static func remainingHold(_ sv: HoldSurvival?, elapsed: Double) -> RemainingHold {
        guard let sv = sv, !sv.elapsed.isEmpty, sv.expected.count == sv.elapsed.count, sv.p50.count == sv.elapsed.count, sv.p90.count == sv.elapsed.count, sv.clears2min.count == sv.elapsed.count else { return priorHold }
        let g = sv.elapsed
        func pick(_ i: Int) -> RemainingHold { RemainingHold(expected: sv.expected[i], p50: sv.p50[i], p90: sv.p90[i], clears2min: sv.clears2min[i]) }
        if elapsed <= g[0] { return pick(0) }
        if elapsed >= g[g.count - 1] { return pick(g.count - 1) }
        var i = 0
        while i < g.count - 1 {
            if g[i] <= elapsed && elapsed < g[i + 1] {
                let f = (elapsed - g[i]) / (g[i + 1] - g[i])
                return RemainingHold(expected: sv.expected[i] + f * (sv.expected[i + 1] - sv.expected[i]), p50: sv.p50[i] + f * (sv.p50[i + 1] - sv.p50[i]),
                                     p90: sv.p90[i] + f * (sv.p90[i + 1] - sv.p90[i]), clears2min: sv.clears2min[i] + f * (sv.clears2min[i + 1] - sv.clears2min[i]))
            }
            i += 1
        }
        return pick(g.count - 1)
    }

    /// Unconstrained projection of one train over its remaining stops.
    static func predictTrain(_ train: PredictorTrain, line: PredictorLine, model: ClientModel?, now: Double, scenario: String = "baseline") -> PredictedTrain {
        var out = PredictedTrain(tripId: train.tripId, points: [], holdExtraSec: 0, knockOnSec: 0, scenario: scenario)
        let pts = train.points.filter { $0.count >= 2 }.map { (Int($0[0]), $0[1]) }.sorted { a, b in a.0 != b.0 ? a.0 < b.0 : a.1 < b.1 }
        guard let first = pts.first else { return out }
        let nextIdx = train.nextIdx ?? first.0
        let lat = train.latenessSec
        let eff = train.effectiveLatenessSec ?? lat
        var optimistic = 0.0
        if let e = eff, let l = lat { optimistic = max(0, e - l) }
        let held = train.position?.holding == true || train.position?.stalled == true
        var extra = 0.0
        if held {
            let rem = remainingHold(model?.holdSurvival, elapsed: train.position?.sinceSec ?? 0)
            switch scenario {
            case "hold_persists": extra = rem.p90
            case "clears_now": extra = 0
            default: extra = rem.expected
            }
        }
        out.holdExtraSec = extra
        let run = line.runSec
        var prevT: Double? = nil
        for (idx, feed) in pts {
            let h = feed - now
            let cal = calibrationAt(model, route: train.route, horizon: h)
            let etaF = feed + cal.bias + optimistic
            let varF = max(pow((cal.p90 - cal.p10) / 2.56, 2), 1.0)
            var eta = etaF, lo = feed + cal.p10 + optimistic, hi = feed + cal.p90 + optimistic, source = "feed"
            let k = idx - nextIdx
            if let schedNext = train.schedTs, let e = eff, k >= 1 {
                var runSum = 0.0, ok = true
                var s = nextIdx
                while s < idx {
                    if s < run.count, let r = run[s] { runSum += r } else { ok = false; break }
                    s += 1
                }
                if ok, let carry = carryAt(model, route: train.route, k: k) {
                    let schedD = schedNext + runSum
                    let etaS = schedD + carry.intercept + carry.slope * e
                    let varS = max(carry.residStd * carry.residStd, 1.0)
                    let w = varF / (varF + varS)
                    eta = w * etaS + (1 - w) * etaF
                    let sd = (1.0 / (1.0 / varF + 1.0 / varS)).squareRoot()
                    lo = eta - 1.28 * sd
                    hi = eta + 1.28 * sd
                    source = "blend"
                }
            }
            eta += extra; lo += extra; hi += extra
            var t = max(eta, now)
            if let p = prevT, t < p + minStopGapSec { t = p + minStopGapSec }
            let shift = t - eta
            out.points.append(PredictedPoint(idx: idx, feedTs: feed, etaTs: t, loTs: lo + shift, hiTs: hi + shift, source: source))
            prevT = t
        }
        return out
    }

    /// All trains of one line direction, furthest along first, with the headway cascade applied.
    static func predictLine(_ trains: [PredictorTrain], line: PredictorLine, model: ClientModel?, now: Double, scenario: String = "baseline",
                            minHeadwaySec: Double = Predictor.minHeadwaySec, horizonSec: Double = 3600) -> LinePrediction {
        func firstTs(_ t: PredictorTrain) -> Double {
            let ts = t.points.filter { $0.count >= 2 }.map { $0[1] }
            return ts.min() ?? now
        }
        let order = trains.enumerated().sorted { a, b in
            let na = -(a.element.nextIdx ?? 0), nb = -(b.element.nextIdx ?? 0)
            if na != nb { return na < nb }
            let fa = firstTs(a.element), fb = firstTs(b.element)
            if fa != fb { return fa < fb }
            return a.offset < b.offset
        }.map { $0.element }
        var projs: [PredictedTrain] = []
        var lastAt: [Int: Double] = [:]
        for t in order {
            var p = predictTrain(t, line: line, model: model, now: now, scenario: scenario)
            var shift = 0.0, knock = 0.0
            for i in p.points.indices {
                var want = p.points[i].etaTs + shift
                if let ahead = lastAt[p.points[i].idx], want < ahead + minHeadwaySec {
                    let delta = ahead + minHeadwaySec - want
                    shift += delta; knock += delta
                    want = ahead + minHeadwaySec
                }
                p.points[i].etaTs = want
                p.points[i].loTs += shift
                p.points[i].hiTs += shift
                lastAt[p.points[i].idx] = want
            }
            p.knockOnSec = knock
            p.points = p.points.filter { $0.etaTs <= now + horizonSec + 900 }
            projs.append(p)
        }
        var perStop: [StopPrediction] = []
        var worst: WorstGap? = nil
        for i in 0..<line.stops.count {
            let arr = projs.flatMap { p in p.points.filter { $0.idx == i && $0.etaTs <= now + horizonSec }.map { $0.etaTs } }.sorted()
            var hws: [Double] = []
            if arr.count >= 2 { for j in 1..<arr.count { hws.append(arr[j] - arr[j - 1]) } }
            let gap = hws.max()
            if let g = gap, g > 0, worst == nil || g > worst!.gapSec, let j = hws.firstIndex(of: g) {
                worst = WorstGap(gapSec: g, idx: i, atTs: arr[j + 1])
            }
            perStop.append(StopPrediction(idx: i, nArrivals: arr.count, nextTs: arr.first, maxHeadwaySec: gap))
        }
        return LinePrediction(scenario: scenario, now: now, trains: projs, perStop: perStop, worstGap: worst,
                              nKnockOn: projs.filter { $0.knockOnSec >= 60 }.count, knockOnTotalSec: projs.reduce(0) { $0 + $1.knockOnSec })
    }

    /// A board's trains in the predictor's input form.
    static func input(_ board: LineBoard) -> [PredictorTrain] {
        board.trains.map { t in
            PredictorTrain(tripId: t.tripId, route: t.route, nextIdx: t.nextIdx, points: t.points.map { [Double($0.idx), $0.ts] },
                           latenessSec: t.latenessSec, effectiveLatenessSec: t.effectiveLatenessSec, schedTs: t.schedTs,
                           position: t.position.map { PredictorPosition(status: $0.status, sinceSec: $0.sinceSec, holding: $0.holding, stalled: $0.stalled) })
        }
    }

    /// Predictions for a board: baseline always, the hold scenarios when a train is held.
    static func predictBoard(_ board: LineBoard, line: LineTopology, model: ClientModel?, now: Double) -> [String: LinePrediction] {
        let inp = input(board)
        let pl = PredictorLine(line)
        var out = ["baseline": predictLine(inp, line: pl, model: model, now: now, scenario: "baseline")]
        if inp.contains(where: { $0.position?.holding == true || $0.position?.stalled == true }) {
            out["hold_persists"] = predictLine(inp, line: pl, model: model, now: now, scenario: "hold_persists")
            out["clears_now"] = predictLine(inp, line: pl, model: model, now: now, scenario: "clears_now")
        }
        return out
    }
}
