import Foundation

// A minimal protobuf wire decoder for the GTFS-Realtime subset the app reads: feed timestamp, trip updates
// (trip descriptor with the NYCT train id / assignment, stop time updates with scheduled and actual track)
// and vehicle positions (status, stop, timestamp). No dependency; mirrors site/rt-client.js.

struct RTTrip {
    var tripId: String = ""
    var startDate: String? = nil
    var routeId: String? = nil
    var trainId: String? = nil
    var isAssigned: Bool? = nil

    var key: String { "\(startDate ?? "")|\(tripId)" }
}

struct RTStopTime {
    var stopId: String = ""
    var arrival: Double? = nil
    var departure: Double? = nil
    var schedTrack: String? = nil
    var actualTrack: String? = nil
    var eta: Double? { arrival ?? departure }
}

struct RTTripUpdate {
    var trip = RTTrip()
    var stops: [RTStopTime] = []
    var timestamp: Double? = nil
}

struct RTVehicle {
    var trip = RTTrip()
    var status: String? = nil        // INCOMING_AT | STOPPED_AT | IN_TRANSIT_TO (nil = absent, GTFS default IN_TRANSIT_TO)
    var stopId: String? = nil
    var timestamp: Double? = nil
}

struct RTFeed {
    var timestamp: Double? = nil
    var trips: [RTTripUpdate] = []
    var vehicles: [RTVehicle] = []
}

enum ProtobufError: Error { case truncated, unsupportedWireType(Int) }

private enum WireValue {
    case varint(UInt64)
    case bytes(Range<Int>)
    case fixed32(UInt32)
    case fixed64
}

private struct Field {
    let number: Int
    let value: WireValue
}

private func readVarint(_ buf: [UInt8], _ pos: inout Int) throws -> UInt64 {
    var value: UInt64 = 0
    var shift: UInt64 = 0
    while true {
        guard pos < buf.count else { throw ProtobufError.truncated }
        let b = buf[pos]
        pos += 1
        if shift < 64 { value |= UInt64(b & 0x7f) << shift }
        if b & 0x80 == 0 { break }
        shift += 7
    }
    return value
}

private func decodeFields(_ buf: [UInt8], _ range: Range<Int>) throws -> [Field] {
    var out: [Field] = []
    var pos = range.lowerBound
    let end = range.upperBound
    while pos < end {
        let key = try readVarint(buf, &pos)
        let number = Int(key >> 3)
        let wt = Int(key & 7)
        switch wt {
        case 0:
            out.append(Field(number: number, value: .varint(try readVarint(buf, &pos))))
        case 1:
            guard pos + 8 <= end else { throw ProtobufError.truncated }
            pos += 8
            out.append(Field(number: number, value: .fixed64))
        case 2:
            let len = Int(try readVarint(buf, &pos))
            guard pos + len <= end else { throw ProtobufError.truncated }
            out.append(Field(number: number, value: .bytes(pos..<(pos + len))))
            pos += len
        case 5:
            guard pos + 4 <= end else { throw ProtobufError.truncated }
            let v = UInt32(buf[pos]) | UInt32(buf[pos + 1]) << 8 | UInt32(buf[pos + 2]) << 16 | UInt32(buf[pos + 3]) << 24
            pos += 4
            out.append(Field(number: number, value: .fixed32(v)))
        default:
            throw ProtobufError.unsupportedWireType(wt)
        }
    }
    return out
}

private func string(_ buf: [UInt8], _ r: Range<Int>) -> String {
    String(decoding: buf[r], as: UTF8.self)
}

private func parseTrip(_ buf: [UInt8], _ r: Range<Int>) throws -> RTTrip {
    var t = RTTrip()
    for f in try decodeFields(buf, r) {
        switch (f.number, f.value) {
        case (1, .bytes(let s)): t.tripId = string(buf, s)
        case (3, .bytes(let s)): t.startDate = string(buf, s)
        case (5, .bytes(let s)): t.routeId = string(buf, s)
        case (1001, .bytes(let s)):
            for e in try decodeFields(buf, s) {
                switch (e.number, e.value) {
                case (1, .bytes(let v)): t.trainId = string(buf, v)
                case (2, .varint(let v)): t.isAssigned = v != 0
                default: break
                }
            }
        default: break
        }
    }
    return t
}

private func parseTripUpdate(_ buf: [UInt8], _ r: Range<Int>) throws -> RTTripUpdate {
    var tu = RTTripUpdate()
    for f in try decodeFields(buf, r) {
        switch (f.number, f.value) {
        case (1, .bytes(let s)): tu.trip = try parseTrip(buf, s)
        case (4, .varint(let v)): tu.timestamp = Double(v)
        case (2, .bytes(let s)):
            var st = RTStopTime()
            for e in try decodeFields(buf, s) {
                switch (e.number, e.value) {
                case (4, .bytes(let v)): st.stopId = string(buf, v)
                case (2, .bytes(let v)), (3, .bytes(let v)):
                    for ev in try decodeFields(buf, v) where ev.number == 2 {
                        if case .varint(let t) = ev.value {
                            if e.number == 2 { st.arrival = Double(t) } else { st.departure = Double(t) }
                        }
                    }
                case (1001, .bytes(let v)):
                    for ev in try decodeFields(buf, v) {
                        switch (ev.number, ev.value) {
                        case (1, .bytes(let x)): st.schedTrack = string(buf, x)
                        case (2, .bytes(let x)): st.actualTrack = string(buf, x)
                        default: break
                        }
                    }
                default: break
                }
            }
            tu.stops.append(st)
        default: break
        }
    }
    return tu
}

private func parseVehicle(_ buf: [UInt8], _ r: Range<Int>) throws -> RTVehicle {
    var v = RTVehicle()
    let statuses = ["INCOMING_AT", "STOPPED_AT", "IN_TRANSIT_TO"]
    for f in try decodeFields(buf, r) {
        switch (f.number, f.value) {
        case (1, .bytes(let s)): v.trip = try parseTrip(buf, s)
        case (4, .varint(let x)): v.status = Int(x) < statuses.count ? statuses[Int(x)] : "\(x)"
        case (5, .varint(let x)): v.timestamp = Double(x)
        case (7, .bytes(let s)): v.stopId = string(buf, s)
        default: break
        }
    }
    return v
}

enum GTFSRealtime {
    /// Parse a FeedMessage.
    static func parse(_ data: Data) throws -> RTFeed {
        let buf = [UInt8](data)
        var feed = RTFeed()
        for f in try decodeFields(buf, 0..<buf.count) {
            switch (f.number, f.value) {
            case (1, .bytes(let s)):
                for h in try decodeFields(buf, s) where h.number == 3 {
                    if case .varint(let t) = h.value { feed.timestamp = Double(t) }
                }
            case (2, .bytes(let s)):
                for e in try decodeFields(buf, s) {
                    switch (e.number, e.value) {
                    case (3, .bytes(let v)): feed.trips.append(try parseTripUpdate(buf, v))
                    case (4, .bytes(let v)): feed.vehicles.append(try parseVehicle(buf, v))
                    default: break
                    }
                }
            default: break
            }
        }
        return feed
    }
}
