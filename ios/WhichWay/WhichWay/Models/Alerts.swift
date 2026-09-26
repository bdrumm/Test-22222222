import Foundation

// Service alerts from the MTA Mercury JSON feed (the GTFS-Realtime alerts in JSON form). Mirrors parseAlerts()
// in site/rt-client.js: one entry per active period, unplanned delays first.

struct RouteAlert: Identifiable {
    var id: String
    var kind: String          // delay | planned | notice
    var type: String?
    var header: String
    var routes: [String]
    var start: Double?
    var end: Double?
}

enum Alerts {
    static let mercuryKey = "transit_realtime.mercury_alert"
    static let plannedPrefixes = ["planned", "weekend service", "buses replace trains", "no midday service", "no weekend service", "special schedule"]
    static let noticePrefixes = ["boarding change", "station notice", "extra service", "elevator", "escalator", "accessibility", "service reminder", "shuttle bus"]

    static func kind(type: String?, header: String) -> String {
        let t = (type ?? "").lowercased(), h = header.lowercased()
        if plannedPrefixes.contains(where: { t.hasPrefix($0) }) || h.contains("planned work") || h.contains("scheduled maintenance") { return "planned" }
        if noticePrefixes.contains(where: { t.hasPrefix($0) }) { return "notice" }
        return "delay"
    }

    static func parse(_ data: Data, now: Double) -> [RouteAlert] {
        guard let doc = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], let ents = doc["entity"] as? [[String: Any]] else { return [] }
        var out: [RouteAlert] = []
        for ent in ents {
            guard let a = ent["alert"] as? [String: Any] else { continue }
            let merc = (a[mercuryKey] as? [String: Any]) ?? [:]
            let header = text(a["header_text"])
            let type = merc["alert_type"] as? String
            let informed = (a["informed_entity"] as? [[String: Any]]) ?? []
            let routes = Array(Set(informed.compactMap { $0["route_id"] as? String })).sorted()
            let updated = num(merc["updated_at"])
            var periods = (a["active_period"] as? [[String: Any]]) ?? []
            if periods.isEmpty { periods = [[:]] }
            let id = (ent["id"] as? String) ?? UUID().uuidString
            for (i, p) in periods.enumerated() {
                let start = num(p["start"]), end = num(p["end"])
                var endEff: Double? = end
                if endEff == nil, let u = updated { endEff = u + 3 * 3600 }
                if endEff == nil, let s = start { endEff = s + 3 * 3600 }
                if let s = start, s > now { continue }
                if let e = endEff, e < now { continue }
                out.append(RouteAlert(id: "\(id)#\(i)", kind: kind(type: type, header: header), type: type, header: header, routes: routes, start: start, end: end))
            }
        }
        let rank = ["delay": 0, "planned": 1, "notice": 2]
        return out.sorted { x, y in
            let rx = rank[x.kind] ?? 3, ry = rank[y.kind] ?? 3
            if rx != ry { return rx < ry }
            return (x.start ?? 0) > (y.start ?? 0)
        }
    }

    private static func num(_ v: Any?) -> Double? {
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        if let s = v as? String { return Double(s) }
        return nil
    }

    private static func text(_ v: Any?) -> String {
        guard let t = v as? [String: Any], let tr = t["translation"] as? [[String: Any]] else { return "" }
        let en = tr.first(where: { ($0["language"] as? String) == "en" }) ?? tr.first
        return ((en?["text"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
