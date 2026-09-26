import Foundation

// client_geometry.json: [lat, lon] per canonical stop and the simplified track of each exported line.

struct LineGeometry: Decodable {
    var coords: [[Double]?]
    var shape: [[Double]]
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        coords = try c.decodeIfPresent([[Double]?].self, forKey: .coords) ?? []
        shape = try c.decodeIfPresent([[Double]].self, forKey: .shape) ?? []
    }
    enum CodingKeys: String, CodingKey { case coords, shape }

    func coord(_ i: Int) -> (lat: Double, lon: Double)? {
        guard i >= 0, i < coords.count, let c = coords[i], c.count >= 2 else { return nil }
        return (c[0], c[1])
    }
}

struct ClientGeometry: Decodable {
    var generatedAt: String?
    var lines: [String: LineGeometry]
    enum CodingKeys: String, CodingKey { case generatedAt = "generated_at", lines }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt)
        lines = try c.decodeIfPresent([String: LineGeometry].self, forKey: .lines) ?? [:]
    }
}
