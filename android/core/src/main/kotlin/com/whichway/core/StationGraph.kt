// Port of ios/WhichWay/WhichWay/Core/StationGraph.swift: station complexes, reachable destinations and paths with
// up to one transfer (from site/rt-client.js).
package com.whichway.core

import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

fun parentOf(stopId: String): String = if (stopId.endsWith("N") || stopId.endsWith("S")) stopId.dropLast(1) else stopId

data class StationMember(val key: String, val route: String, val dir: String, val stop: String, val idx: Int)

data class Station(val id: String, val name: String, val routes: List<String>, val members: List<StationMember>) {
    override fun equals(other: Any?) = other is Station && other.id == id
    override fun hashCode() = id.hashCode()
}

class StationIndex(schedule: ClientSchedule) {
    val stations: Map<String, Station>
    private val root = HashMap<String, String>()

    init {
        // union-find over parent stations joined by the exported transfers; the smaller parent id becomes the
        // complex's id, so persisted station ids do not change from one launch to the next
        fun find(x: String): String {
            var r = x
            while (true) { val n = root[r]; if (n == null || n == r) break; r = n }
            if (root[x] == null) root[x] = x
            return r
        }
        fun union(a: String, b: String) {
            val ra = find(a); val rb = find(b)
            if (ra != rb) { if (ra < rb) root[rb] = ra else root[ra] = rb }
        }
        for ((_, line) in schedule.lines) for (sid in line.stops) {
            find(parentOf(sid))
            for (o in schedule.transfers[sid] ?: emptyList()) union(parentOf(sid), parentOf(o.stop))
        }
        val names = HashMap<String, HashMap<String, Int>>()
        val routes = HashMap<String, MutableSet<String>>()
        val members = LinkedHashMap<String, MutableList<StationMember>>()
        for ((key, line) in schedule.lines) {
            val parts = key.split("_")
            if (parts.size != 2) continue
            line.stops.forEachIndexed { idx, sid ->
                val id = find(parentOf(sid))
                val nm = line.names.getOrElse(idx) { sid }
                val m = names.getOrPut(id) { HashMap() }
                m[nm] = (m[nm] ?: 0) + 1
                routes.getOrPut(id) { HashSet() }.add(parts[0])
                members.getOrPut(id) { ArrayList() }.add(StationMember(key, parts[0], parts[1], sid, idx))
            }
        }
        stations = members.mapValues { (id, ms) ->
            val name = names[id]?.entries?.sortedWith(compareByDescending<Map.Entry<String, Int>> { it.value }.thenBy { it.key })?.firstOrNull()?.key ?: id
            Station(id, name, (routes[id] ?: emptySet()).sorted(), ms)
        }
    }

    fun stationOf(stopId: String): String {
        var r = parentOf(stopId)
        while (true) { val n = root[r]; if (n == null || n == r) break; r = n }
        return r
    }

    /** The station for a persisted id: its own, or the complex any of its stops now belongs to. */
    fun station(id: String): Station? = if (id.isEmpty()) null else stations[id] ?: stations[stationOf(id)]

    val sorted: List<Station> by lazy { stations.values.sortedWith(compareBy<Station> { it.name }.thenBy { it.routes.joinToString("") }) }
}

// reachable destinations

data class ReachVia(val r1: String, val r2s: MutableList<String>, val station: String)

class Reach {
    var how = "transfer"      // "direct" | "transfer"
    val direct = ArrayList<String>()
    var via = ArrayList<ReachVia>()

    val summary: String
        get() {
            fun v(x: ReachVia) = "${x.r1} → ${x.r2s.joinToString("/")} at ${x.station}"
            if (direct.isNotEmpty()) {
                var s = "direct on the ${direct.joinToString("/")}"
                if (via.isNotEmpty()) {
                    s += " · or via " + via.take(2).joinToString(", ") { v(it) }
                    if (via.size > 2) s += ", …"
                }
                return s
            }
            var s = "via " + via.take(3).joinToString(" · ") { v(it) }
            if (via.size > 3) s += " · +${via.size - 3} more"
            return s
        }
}

fun reachableStations(schedule: ClientSchedule, index: StationIndex, from: String): Map<String, Reach> {
    val o = index.stations[from] ?: return emptyMap()
    val out = LinkedHashMap<String, Reach>()
    val viaKeys = HashMap<String, HashMap<String, Int>>()
    for (m1 in o.members) {
        val l1 = schedule.lines[m1.key] ?: continue
        for (x in m1.idx + 1 until l1.stops.size) {
            val xs = l1.stops[x]
            val st = index.stationOf(xs)
            if (st != from) {
                val e = out.getOrPut(st) { Reach() }
                e.how = "direct"
                if (m1.route !in e.direct) e.direct.add(m1.route)
            }
            val xName = index.stations[st]?.name ?: xs
            for (opt in schedule.transfers[xs] ?: emptyList()) {
                val r2 = opt.line.substringBefore("_")
                if (r2 == m1.route) continue
                val l2 = schedule.lines[opt.line] ?: continue
                val x2 = l2.stops.indexOf(opt.stop)
                if (x2 < 0) continue
                for (y in x2 + 1 until l2.stops.size) {
                    val st2 = index.stationOf(l2.stops[y])
                    if (st2 == from || st2 == st) continue
                    val e = out.getOrPut(st2) { Reach() }
                    val vk = "${m1.route}|$st"
                    val i = viaKeys[st2]?.get(vk)
                    if (i != null) { if (r2 !in e.via[i].r2s) e.via[i].r2s.add(r2) }
                    else {
                        e.via.add(ReachVia(m1.route, mutableListOf(r2), xName))
                        viaKeys.getOrPut(st2) { HashMap() }[vk] = e.via.size - 1
                    }
                }
            }
        }
    }
    for (e in out.values) {
        e.via = ArrayList(e.via.filter { it.r1 !in e.direct })
        e.direct.sort()
    }
    return out
}

// paths

data class Span(val from: Int, val to: Int)

data class PathLeg(
    val from: String,
    val to: String,
    val keys: MutableList<String> = ArrayList(),
    val routes: MutableList<String> = ArrayList(),
    val idx: MutableMap<String, Span> = LinkedHashMap(),
    var schedRideSec: Int? = null,
    var nStops: Int = 0,
    var typicalSec: Double? = null,
    var holdRiskSec: Double = 0.0,
) {
    val routesLabel get() = routes.joinToString("/")
    val primaryKey get() = keys.firstOrNull() ?: ""
    val primaryRoute get() = routes.firstOrNull() ?: ""
}

data class PathTransfer(val stop: String, val stop2: String, val station: String, var walkSec: Int)

data class PathOption(
    val id: String,
    val legs: List<PathLeg>,
    var transfer: PathTransfer?,
    var schedSec: Int = 0,
    var label: String = "",
    var wait1Sec: Double = 300.0,
    var wait2Sec: Double = 0.0,
    var expectedSec: Double = 0.0,
    var live: Itinerary? = null,
) {
    val typicalSec get() = legs.sumOf { it.typicalSec ?: 0.0 }
    val holdRiskSec get() = legs.sumOf { it.holdRiskSec }
}

private class RawLeg(val key: String, val from: String, val fromIdx: Int, val to: String, val toIdx: Int)
private class RawPath(val legs: List<RawLeg>, val transfer: PathTransfer?)

fun enumeratePaths(schedule: ClientSchedule, index: StationIndex, from: String, to: String, maxOptions: Int = 8): List<PathOption> {
    val o = index.stations[from] ?: return emptyList()
    val d = index.stations[to] ?: return emptyList()
    val destByKey = HashMap<String, StationMember>()
    for (m in d.members) destByKey[m.key] = m
    val raw = ArrayList<RawPath>()
    val directRoutes = HashSet<String>()
    for (m1 in o.members) { val dm = destByKey[m1.key]; if (dm != null && dm.idx > m1.idx) directRoutes.add(m1.route) }
    for (m1 in o.members) {
        val l1 = schedule.lines[m1.key] ?: continue
        val dm = destByKey[m1.key]
        if (dm != null && dm.idx > m1.idx) raw.add(RawPath(listOf(RawLeg(m1.key, m1.stop, m1.idx, dm.stop, dm.idx)), null))
        if (m1.route in directRoutes) continue
        for (x in m1.idx + 1 until l1.stops.size) {
            val xs = l1.stops[x]
            val xst = index.stationOf(xs)
            if (xst == to) break
            if (xst == from) continue
            for (opt in schedule.transfers[xs] ?: emptyList()) {
                val dm2 = destByKey[opt.line] ?: continue
                val r2 = opt.line.substringBefore("_")
                if (r2 == m1.route) continue
                val l2 = schedule.lines[opt.line] ?: continue
                val x2 = l2.stops.indexOf(opt.stop)
                if (x2 < 0 || dm2.idx <= x2) continue
                if (l2.stops.subList(x2 + 1, dm2.idx).any { index.stationOf(it) == from }) continue
                val station = index.stations[xst]?.name ?: xs
                // the feed's minimum transfer time; 0 is a same-platform change and stays 0
                raw.add(RawPath(listOf(RawLeg(m1.key, m1.stop, m1.idx, xs, x), RawLeg(opt.line, opt.stop, x2, dm2.stop, dm2.idx)),
                    PathTransfer(xs, opt.stop, station, opt.minSec)))
            }
        }
    }
    // merge parallel routes over the same stop pattern
    val merged = LinkedHashMap<String, PathOption>()
    for (p in raw) {
        val sig = p.legs.joinToString("|") { "${it.from}>${it.to}" }
        val opt = merged.getOrPut(sig) { PathOption(sig, p.legs.map { PathLeg(it.from, it.to) }, p.transfer) }
        p.legs.forEachIndexed { i, l ->
            val leg = opt.legs[i]
            if (l.key !in leg.keys) {
                leg.keys.add(l.key)
                leg.routes.add(l.key.substringBefore("_"))
                leg.idx[l.key] = Span(l.fromIdx, l.toIdx)
            }
        }
    }
    val out = ArrayList<PathOption>()
    for (opt in merged.values) {
        for (leg in opt.legs) {
            var best: Int? = null
            var nStops = Int.MAX_VALUE
            for (k in leg.keys) {
                val line = schedule.lines[k] ?: continue
                val ix = leg.idx[k] ?: continue
                line.runBetween(ix.from, ix.to)?.let { r -> best = min(best ?: r, r) }
                nStops = min(nStops, ix.to - ix.from)
            }
            leg.schedRideSec = best
            leg.nStops = if (nStops == Int.MAX_VALUE) 0 else nStops
        }
        opt.schedSec = opt.legs.sumOf { it.schedRideSec ?: 0 } + (opt.transfer?.walkSec ?: 0)
        opt.label = opt.legs.joinToString(" → ") { it.routesLabel } + (opt.transfer?.let { " at ${it.station}" } ?: " direct")
        out.add(opt)
    }
    out.sortBy { it.schedSec }
    return out.take(maxOptions)
}

// locations

fun haversineM(a: LatLon, b: LatLon): Double {
    val r = 6_371_000.0
    val dLat = Math.toRadians(b.lat - a.lat)
    val dLon = Math.toRadians(b.lon - a.lon)
    val h = sin(dLat / 2) * sin(dLat / 2) + cos(Math.toRadians(a.lat)) * cos(Math.toRadians(b.lat)) * sin(dLon / 2) * sin(dLon / 2)
    return 2 * r * asin(min(1.0, sqrt(h)))
}

/** Each station complex's coordinate: the mean of its member stops' coordinates from the geometry file. */
fun stationCoordinates(schedule: ClientSchedule, index: StationIndex, geometry: ClientGeometry): Map<String, LatLon> {
    val sum = HashMap<String, DoubleArray>()
    for ((key, g) in geometry.lines) {
        val line = schedule.lines[key] ?: continue
        line.stops.forEachIndexed { i, sid ->
            val c = g.coord(i) ?: return@forEachIndexed
            val s = sum.getOrPut(index.stationOf(sid)) { DoubleArray(3) }
            s[0] += c.lat; s[1] += c.lon; s[2] += 1.0
        }
    }
    return sum.filterValues { it[2] > 0 }.mapValues { (_, s) -> LatLon(s[0] / s[2], s[1] / s[2]) }
}

data class NearbyStation(val station: Station, val meters: Double) {
    /** At 80 m a minute until a personal pace is learned. */
    val walkMinutes: Int get() = maxOf(1, (meters / 80.0).roundToInt())
}

/** The n stations nearest to a point, nearest first. */
fun nearestStations(p: LatLon, coords: Map<String, LatLon>, index: StationIndex, n: Int = 6): List<NearbyStation> =
    coords.mapNotNull { (id, c) -> index.stations[id]?.let { NearbyStation(it, haversineM(p, c)) } }.sortedBy { it.meters }.take(n)
