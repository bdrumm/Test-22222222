import Foundation

/// One use of the planner with both stations set: when, where the phone was, which way.
struct TripUse: Codable, Equatable {
    var origin: String
    var dest: String
    var ts: Double
    var hour: Double          // New York, fractional
    var weekday: Int          // 1 = Sunday … 7 = Saturday
    var lat: Double?
    var lon: Double?
}

/// The route the rider most likely wants right now, from their history.
struct HabitGuess: Equatable {
    var origin: String
    var dest: String
    var uses: Int
    var score: Double
}

/// A Home or Work the history points to: the station, and the spot the rider tends to set out from.
struct PlaceSuggestion: Equatable {
    var stationId: String
    var lat: Double?
    var lon: Double?
    var uses: Int
}

/// What this rider does with the app: which trips, at what hours, on which days, from where. Stays on the
/// phone. Suggests the likely route for the moment and the stations that look like Home and Work.
struct Habits: Codable, Equatable {
    var uses: [TripUse] = []
    static let cap = 400

    /// Records a use; the same trip within 20 minutes of the last record of it is the same use.
    mutating func record(origin: String, dest: String, ts: Double, lat: Double?, lon: Double?, timeZone: TimeZone) {
        guard !origin.isEmpty, !dest.isEmpty, origin != dest else { return }
        if let last = uses.last(where: { $0.origin == origin && $0.dest == dest }), ts - last.ts < 1200 { return }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let c = cal.dateComponents([.hour, .minute, .weekday], from: Date(timeIntervalSince1970: ts))
        uses.append(TripUse(origin: origin, dest: dest, ts: ts, hour: Double(c.hour ?? 0) + Double(c.minute ?? 0) / 60, weekday: c.weekday ?? 1, lat: lat, lon: lon))
        if uses.count > Habits.cap { uses.removeFirst(uses.count - Habits.cap) }
    }

    /// The trip the rider most likely wants now: every past use votes for its trip, weighted by how close it
    /// was in the hour, whether it was the same kind of day, and how near the phone was to where it is now.
    func likelyTrip(hour: Double, weekday: Int, lat: Double?, lon: Double?) -> HabitGuess? {
        var score: [String: (o: String, d: String, s: Double, n: Int)] = [:]
        let weekend = weekday == 1 || weekday == 7
        for u in uses {
            var dh = abs(u.hour - hour); dh = min(dh, 24 - dh)
            guard dh <= 2 else { continue }
            let tw = 1 - dh / 2
            let uw = u.weekday == 1 || u.weekday == 7
            let dw = u.weekday == weekday ? 1.0 : (uw == weekend ? 0.7 : 0.2)
            var lw = 0.8
            if let la = lat, let lo = lon, let ula = u.lat, let ulo = u.lon {
                let d = haversineM((la, lo), (ula, ulo))
                lw = d <= 300 ? 1.5 : (d <= 1500 ? 1.0 : 0.4)
            }
            let k = u.origin + ">" + u.dest
            var e = score[k] ?? (u.origin, u.dest, 0, 0)
            e.s += tw * dw * lw; e.n += 1
            score[k] = e
        }
        guard let best = score.values.max(by: { $0.s < $1.s }), best.n >= 2, best.s >= 1.5 else { return nil }
        return HabitGuess(origin: best.o, dest: best.d, uses: best.n, score: best.s)
    }

    /// Home: the station the rider sets out from in the morning and comes back to in the evening. Work: the
    /// reverse. Each needs three such uses; the pin is where the phone tended to be when leaving from there.
    func suggestedHome() -> PlaceSuggestion? { suggest(home: true, excluding: nil) }
    func suggestedWork(excluding home: String?) -> PlaceSuggestion? { suggest(home: false, excluding: home) }

    private func suggest(home: Bool, excluding: String?) -> PlaceSuggestion? {
        var votes: [String: Int] = [:]
        var from: [String: [(Double, Double)]] = [:]
        for u in uses {
            let morning = u.hour < 12, evening = u.hour >= 15
            // Home is a morning origin or an evening destination; Work the other way round
            let originVote = home ? morning : evening
            let destVote = home ? evening : morning
            if originVote {
                votes[u.origin, default: 0] += 1
                if let la = u.lat, let lo = u.lon { from[u.origin, default: []].append((la, lo)) }
            }
            if destVote { votes[u.dest, default: 0] += 1 }
        }
        if let x = excluding { votes[x] = nil }
        guard let (station, n) = votes.max(by: { $0.value < $1.value }), n >= 3 else { return nil }
        var pin: (Double, Double)? = nil
        if let pts = from[station], pts.count >= 2 {
            let la = pts.map { $0.0 }.reduce(0, +) / Double(pts.count), lo = pts.map { $0.1 }.reduce(0, +) / Double(pts.count)
            if pts.allSatisfy({ haversineM(($0.0, $0.1), (la, lo)) <= 400 }) { pin = (la, lo) }
        }
        return PlaceSuggestion(stationId: station, lat: pin?.0, lon: pin?.1, uses: n)
    }
}
