// Port of ios/WhichWay/WhichWay/Models/GTFSRealtime.swift: a minimal protobuf wire decoder for the GTFS-Realtime
// subset the app reads (feed timestamp, trip updates with the NYCT train id and tracks, vehicle positions).
package com.whichway.core

data class RTTrip(
    val tripId: String = "",
    val startDate: String? = null,
    val routeId: String? = null,
    val trainId: String? = null,
    val isAssigned: Boolean? = null,
) {
    val key: String get() = "${startDate ?: ""}|$tripId"
}

data class RTStopTime(
    val stopId: String = "",
    val arrival: Double? = null,
    val departure: Double? = null,
    val schedTrack: String? = null,
    val actualTrack: String? = null,
) {
    val eta: Double? get() = arrival ?: departure
}

data class RTTripUpdate(val trip: RTTrip = RTTrip(), val stops: List<RTStopTime> = emptyList(), val timestamp: Double? = null)

/** status: INCOMING_AT | STOPPED_AT | IN_TRANSIT_TO (null = absent, GTFS default IN_TRANSIT_TO) */
data class RTVehicle(val trip: RTTrip = RTTrip(), val status: String? = null, val stopId: String? = null, val timestamp: Double? = null)

data class RTFeed(val timestamp: Double? = null, val trips: List<RTTripUpdate> = emptyList(), val vehicles: List<RTVehicle> = emptyList())

class ProtobufException(msg: String) : Exception(msg)

private sealed class Wire {
    class Varint(val v: Long) : Wire()
    class Bytes(val from: Int, val to: Int) : Wire()
    class Fixed32(val v: Int) : Wire()
    object Fixed64 : Wire()
}

private class Field(val number: Int, val value: Wire)

object GtfsRealtime {
    private fun readVarint(buf: ByteArray, pos: IntArray): Long {
        var value = 0L
        var shift = 0
        while (true) {
            if (pos[0] >= buf.size) throw ProtobufException("truncated")
            val b = buf[pos[0]].toInt() and 0xff
            pos[0]++
            if (shift < 64) value = value or ((b and 0x7f).toLong() shl shift)
            if (b and 0x80 == 0) break
            shift += 7
        }
        return value
    }

    private fun fields(buf: ByteArray, from: Int, to: Int): List<Field> {
        val out = ArrayList<Field>()
        val pos = intArrayOf(from)
        while (pos[0] < to) {
            val key = readVarint(buf, pos)
            val number = (key ushr 3).toInt()
            when (val wt = (key and 7).toInt()) {
                0 -> out.add(Field(number, Wire.Varint(readVarint(buf, pos))))
                1 -> { if (pos[0] + 8 > to) throw ProtobufException("truncated"); pos[0] += 8; out.add(Field(number, Wire.Fixed64)) }
                2 -> {
                    val len = readVarint(buf, pos).toInt()
                    if (len < 0 || pos[0] + len > to) throw ProtobufException("truncated")
                    out.add(Field(number, Wire.Bytes(pos[0], pos[0] + len)))
                    pos[0] += len
                }
                5 -> {
                    if (pos[0] + 4 > to) throw ProtobufException("truncated")
                    val p = pos[0]
                    val v = (buf[p].toInt() and 0xff) or ((buf[p + 1].toInt() and 0xff) shl 8) or ((buf[p + 2].toInt() and 0xff) shl 16) or ((buf[p + 3].toInt() and 0xff) shl 24)
                    pos[0] += 4
                    out.add(Field(number, Wire.Fixed32(v)))
                }
                else -> throw ProtobufException("unsupported wire type $wt")
            }
        }
        return out
    }

    private fun str(buf: ByteArray, b: Wire.Bytes) = String(buf, b.from, b.to - b.from, Charsets.UTF_8)

    private fun parseTrip(buf: ByteArray, r: Wire.Bytes): RTTrip {
        var t = RTTrip()
        for (f in fields(buf, r.from, r.to)) {
            val v = f.value
            when {
                f.number == 1 && v is Wire.Bytes -> t = t.copy(tripId = str(buf, v))
                f.number == 3 && v is Wire.Bytes -> t = t.copy(startDate = str(buf, v))
                f.number == 5 && v is Wire.Bytes -> t = t.copy(routeId = str(buf, v))
                f.number == 1001 && v is Wire.Bytes -> for (e in fields(buf, v.from, v.to)) {
                    val ev = e.value
                    if (e.number == 1 && ev is Wire.Bytes) t = t.copy(trainId = str(buf, ev))
                    if (e.number == 2 && ev is Wire.Varint) t = t.copy(isAssigned = ev.v != 0L)
                }
            }
        }
        return t
    }

    private fun parseTripUpdate(buf: ByteArray, r: Wire.Bytes): RTTripUpdate {
        var trip = RTTrip()
        var ts: Double? = null
        val stops = ArrayList<RTStopTime>()
        for (f in fields(buf, r.from, r.to)) {
            val v = f.value
            when {
                f.number == 1 && v is Wire.Bytes -> trip = parseTrip(buf, v)
                f.number == 4 && v is Wire.Varint -> ts = v.v.toDouble()
                f.number == 2 && v is Wire.Bytes -> {
                    var st = RTStopTime()
                    for (e in fields(buf, v.from, v.to)) {
                        val ev = e.value
                        when {
                            e.number == 4 && ev is Wire.Bytes -> st = st.copy(stopId = str(buf, ev))
                            (e.number == 2 || e.number == 3) && ev is Wire.Bytes -> for (x in fields(buf, ev.from, ev.to)) {
                                val xv = x.value
                                if (x.number == 2 && xv is Wire.Varint) st = if (e.number == 2) st.copy(arrival = xv.v.toDouble()) else st.copy(departure = xv.v.toDouble())
                            }
                            e.number == 1001 && ev is Wire.Bytes -> for (x in fields(buf, ev.from, ev.to)) {
                                val xv = x.value
                                if (x.number == 1 && xv is Wire.Bytes) st = st.copy(schedTrack = str(buf, xv))
                                if (x.number == 2 && xv is Wire.Bytes) st = st.copy(actualTrack = str(buf, xv))
                            }
                        }
                    }
                    stops.add(st)
                }
            }
        }
        return RTTripUpdate(trip, stops, ts)
    }

    private val statuses = listOf("INCOMING_AT", "STOPPED_AT", "IN_TRANSIT_TO")

    private fun parseVehicle(buf: ByteArray, r: Wire.Bytes): RTVehicle {
        var veh = RTVehicle()
        for (f in fields(buf, r.from, r.to)) {
            val v = f.value
            when {
                f.number == 1 && v is Wire.Bytes -> veh = veh.copy(trip = parseTrip(buf, v))
                f.number == 4 && v is Wire.Varint -> veh = veh.copy(status = statuses.getOrNull(v.v.toInt()) ?: "${v.v}")
                f.number == 5 && v is Wire.Varint -> veh = veh.copy(timestamp = v.v.toDouble())
                f.number == 7 && v is Wire.Bytes -> veh = veh.copy(stopId = str(buf, v))
            }
        }
        return veh
    }

    /** Parse a FeedMessage. */
    fun parse(buf: ByteArray): RTFeed {
        var ts: Double? = null
        val trips = ArrayList<RTTripUpdate>()
        val vehicles = ArrayList<RTVehicle>()
        for (f in fields(buf, 0, buf.size)) {
            val v = f.value as? Wire.Bytes ?: continue
            when (f.number) {
                1 -> for (h in fields(buf, v.from, v.to)) { val hv = h.value; if (h.number == 3 && hv is Wire.Varint) ts = hv.v.toDouble() }
                2 -> for (e in fields(buf, v.from, v.to)) {
                    val ev = e.value as? Wire.Bytes ?: continue
                    if (e.number == 3) trips.add(parseTripUpdate(buf, ev))
                    if (e.number == 4) vehicles.add(parseVehicle(buf, ev))
                }
            }
        }
        return RTFeed(ts, trips, vehicles)
    }
}
