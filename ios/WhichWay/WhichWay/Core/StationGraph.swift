import Foundation

// Station complexes and paths with up to one transfer, ported from site/rt-client.js.

func parentOf(_ stopId: String) -> String {
    if let last = stopId.last, last == "N" || last == "S" { return String(stopId.dropLast()) }
    return stopId
}

struct StationMember: Hashable {
    let key: String      // "<route>_<N|S>"
    let route: String
    let dir: String
    let stop: String
    let idx: Int
}

struct Station: Identifiable, Hashable {
    let id: String
    var name: String
    var routes: [String]
    var members: [StationMember]

    static func == (a: Station, b: Station) -> Bool { a.id == b.id }
    func hash(into h: inout Hasher) { h.combine(id) }
}

struct StationIndex {
    private(set) var stations: [String: Station] = [:]
    private var root: [String: String] = [:]

    init(schedule: ClientSchedule) {
        // union-find over parent stations joined by the exported transfers
        var root: [String: String] = [:]
        func find(_ x: String) -> String {
            var r = x
            while let n = root[r], n != r { r = n }
            if root[x] == nil { root[x] = x }
            return r
        }
        func union(_ a: String, _ b: String) {
            let ra = find(a), rb = find(b)
            if ra != rb { root[ra] = rb }
        }
        for (_, line) in schedule.lines {
            for sid in line.stops {
                _ = find(parentOf(sid))
                for o in schedule.transfers[sid] ?? [] { union(parentOf(sid), parentOf(o.stop)) }
            }
        }
        var names: [String: [String: Int]] = [:]
        var routes: [String: Set<String>] = [:]
        var members: [String: [StationMember]] = [:]
        for (key, line) in schedule.lines {
            let parts = key.split(separator: "_").map(String.init)
            guard parts.count == 2 else { continue }
            for (idx, sid) in line.stops.enumerated() {
                let id = find(parentOf(sid))
                let nm = idx < line.names.count ? line.names[idx] : sid
                names[id, default: [:]][nm, default: 0] += 1
                routes[id, default: []].insert(parts[0])
                members[id, default: []].append(StationMember(key: key, route: parts[0], dir: parts[1], stop: sid, idx: idx))
            }
        }
        var stations: [String: Station] = [:]
        for (id, ms) in members {
            let name = (names[id] ?? [:]).max { $0.value < $1.value }?.key ?? id
            stations[id] = Station(id: id, name: name, routes: Array(routes[id] ?? []).sorted(), members: ms)
        }
        self.stations = stations
        self.root = root
    }

    func stationOf(_ stopId: String) -> String {
        var r = parentOf(stopId)
        while let n = root[r], n != r { r = n }
        return r
    }

    var sorted: [Station] {
        stations.values.sorted { a, b in
            if a.name != b.name { return a.name < b.name }
            return a.routes.joined() < b.routes.joined()
        }
    }
}

// MARK: - reachable destinations

struct ReachVia: Hashable {
    let r1: String
    var r2s: [String]
    let station: String
}

struct Reach {
    var how: String = "transfer"      // "direct" | "transfer"
    var direct: [String] = []
    var via: [ReachVia] = []

    var summary: String {
        if !direct.isEmpty {
            var s = "direct on the \(direct.joined(separator: "/"))"
            if !via.isEmpty {
                s += " · or via " + via.prefix(2).map { "\($0.r1) → \($0.r2s.joined(separator: "/")) at \($0.station)" }.joined(separator: ", ")
                if via.count > 2 { s += ", …" }
            }
            return s
        }
        var s = "via " + via.prefix(3).map { "\($0.r1) → \($0.r2s.joined(separator: "/")) at \($0.station)" }.joined(separator: " · ")
        if via.count > 3 { s += " · +\(via.count - 3) more" }
        return s
    }
}

func reachableStations(schedule: ClientSchedule, index: StationIndex, from oId: String) -> [String: Reach] {
    guard let o = index.stations[oId] else { return [:] }
    var out: [String: Reach] = [:]
    var viaKeys: [String: [String: Int]] = [:]   // station -> "r1|xst" -> index in via
    for m1 in o.members {
        guard let l1 = schedule.lines[m1.key] else { continue }
        var x = m1.idx + 1
        while x < l1.stops.count {
            let xs = l1.stops[x]
            let st = index.stationOf(xs)
            if st != oId {
                var e = out[st] ?? Reach()
                e.how = "direct"
                if !e.direct.contains(m1.route) { e.direct.append(m1.route) }
                out[st] = e
            }
            let xName = index.stations[st]?.name ?? xs
            for opt in schedule.transfers[xs] ?? [] {
                let r2 = String(opt.line.split(separator: "_").first ?? "")
                if r2 == m1.route { continue }
                guard let l2 = schedule.lines[opt.line], let x2 = l2.stops.firstIndex(of: opt.stop) else { continue }
                var y = x2 + 1
                while y < l2.stops.count {
                    let st2 = index.stationOf(l2.stops[y])
                    if st2 != oId && st2 != st {
                        var e = out[st2] ?? Reach()
                        let vk = "\(m1.route)|\(st)"
                        if let i = viaKeys[st2]?[vk] {
                            if !e.via[i].r2s.contains(r2) { e.via[i].r2s.append(r2) }
                        } else {
                            e.via.append(ReachVia(r1: m1.route, r2s: [r2], station: xName))
                            viaKeys[st2, default: [:]][vk] = e.via.count - 1
                        }
                        out[st2] = e
                    }
                    y += 1
                }
            }
            x += 1
        }
    }
    for (k, var e) in out {
        e.via = e.via.filter { !e.direct.contains($0.r1) }
        e.direct.sort()
        out[k] = e
    }
    return out
}

// MARK: - paths

struct PathLeg {
    var from: String
    var to: String
    var keys: [String] = []
    var routes: [String] = []
    var idx: [String: (from: Int, to: Int)] = [:]
    var schedRideSec: Int? = nil
    var nStops: Int = 0
    var typicalSec: Double? = nil
    var holdRiskSec: Double = 0

    var routesLabel: String { routes.joined(separator: "/") }
    var primaryKey: String { keys.first ?? "" }
    var primaryRoute: String { routes.first ?? "" }
}

struct PathTransfer {
    var stop: String
    var stop2: String
    var station: String
    var walkSec: Int
}

struct PathOption: Identifiable {
    var id: String
    var legs: [PathLeg]
    var transfer: PathTransfer?
    var schedSec: Int = 0
    var label: String = ""
    var wait1Sec: Double = 300
    var wait2Sec: Double = 0
    var expectedSec: Double = 0
    var live: Itinerary? = nil

    var typicalSec: Double { legs.reduce(0) { $0 + ($1.typicalSec ?? 0) } }
    var holdRiskSec: Double { legs.reduce(0) { $0 + $1.holdRiskSec } }
}

private struct RawPath {
    var legs: [(key: String, from: String, fromIdx: Int, to: String, toIdx: Int)]
    var transfer: PathTransfer?
}

func enumeratePaths(schedule: ClientSchedule, index: StationIndex, from oId: String, to dId: String, maxOptions: Int = 8) -> [PathOption] {
    guard let o = index.stations[oId], let d = index.stations[dId] else { return [] }
    var destByKey: [String: StationMember] = [:]
    for m in d.members { destByKey[m.key] = m }
    var raw: [RawPath] = []
    var directRoutes = Set<String>()
    for m1 in o.members { if let dm = destByKey[m1.key], dm.idx > m1.idx { directRoutes.insert(m1.route) } }
    for m1 in o.members {
        guard let l1 = schedule.lines[m1.key] else { continue }
        if let dm = destByKey[m1.key], dm.idx > m1.idx {
            raw.append(RawPath(legs: [(m1.key, m1.stop, m1.idx, dm.stop, dm.idx)], transfer: nil))
        }
        if directRoutes.contains(m1.route) { continue }
        var x = m1.idx + 1
        while x < l1.stops.count {
            let xs = l1.stops[x]
            let xst = index.stationOf(xs)
            if xst == dId { break }
            if xst != oId {
                for opt in schedule.transfers[xs] ?? [] {
                    guard let dm2 = destByKey[opt.line] else { continue }
                    let r2 = String(opt.line.split(separator: "_").first ?? "")
                    if r2 == m1.route { continue }
                    guard let l2 = schedule.lines[opt.line], let x2 = l2.stops.firstIndex(of: opt.stop), dm2.idx > x2 else { continue }
                    if l2.stops[(x2 + 1)..<dm2.idx].contains(where: { index.stationOf($0) == oId }) { continue }
                    let station = index.stations[xst]?.name ?? xs
                    raw.append(RawPath(legs: [(m1.key, m1.stop, m1.idx, xs, x), (opt.line, opt.stop, x2, dm2.stop, dm2.idx)],
                                       transfer: PathTransfer(stop: xs, stop2: opt.stop, station: station, walkSec: opt.minSec > 0 ? opt.minSec : 120)))
                }
            }
            x += 1
        }
    }
    // merge parallel routes over the same stop pattern
    var order: [String] = []
    var merged: [String: PathOption] = [:]
    for p in raw {
        let sig = p.legs.map { "\($0.from)>\($0.to)" }.joined(separator: "|")
        if merged[sig] == nil {
            merged[sig] = PathOption(id: sig, legs: p.legs.map { PathLeg(from: $0.from, to: $0.to) }, transfer: p.transfer)
            order.append(sig)
        }
        var opt = merged[sig]!
        for (i, l) in p.legs.enumerated() where !opt.legs[i].keys.contains(l.key) {
            opt.legs[i].keys.append(l.key)
            opt.legs[i].routes.append(String(l.key.split(separator: "_").first ?? ""))
            opt.legs[i].idx[l.key] = (l.fromIdx, l.toIdx)
        }
        merged[sig] = opt
    }
    var out: [PathOption] = []
    for sig in order {
        guard var opt = merged[sig] else { continue }
        for i in opt.legs.indices {
            var best: Int? = nil
            var nStops = Int.max
            for k in opt.legs[i].keys {
                guard let line = schedule.lines[k], let ix = opt.legs[i].idx[k] else { continue }
                if let r = line.runBetween(ix.from, ix.to) { best = min(best ?? r, r) }
                nStops = min(nStops, ix.to - ix.from)
            }
            opt.legs[i].schedRideSec = best
            opt.legs[i].nStops = nStops == Int.max ? 0 : nStops
        }
        opt.schedSec = opt.legs.reduce(0) { $0 + ($1.schedRideSec ?? 0) } + (opt.transfer?.walkSec ?? 0)
        opt.label = opt.legs.map { $0.routesLabel }.joined(separator: " → ") + (opt.transfer.map { " at \($0.station)" } ?? " direct")
        out.append(opt)
    }
    out.sort { $0.schedSec < $1.schedSec }
    return Array(out.prefix(maxOptions))
}
