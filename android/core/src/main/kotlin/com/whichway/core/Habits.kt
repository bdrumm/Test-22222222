// Port of ios/WhichWay/WhichWay/Core/Habits.swift: which trips this rider makes, at what hours, on which days,
// from where. Stays on the phone; suggests the likely route for the moment and the stations that look like
// Home and Work.
package com.whichway.core

import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.ZoneId
import kotlin.math.abs
import kotlin.math.min

/** One use of the planner with both stations set: when, where the phone was, which way. */
@Serializable
data class TripUse(
    val origin: String,
    val dest: String,
    val ts: Double,
    val hour: Double,          // New York, fractional
    val weekday: Int,          // 1 = Sunday … 7 = Saturday
    val lat: Double? = null,
    val lon: Double? = null,
)

data class HabitGuess(val origin: String, val dest: String, val uses: Int, val score: Double)

/** A Home or Work the history points to: the station, and the spot the rider tends to set out from. */
data class PlaceSuggestion(val stationId: String, val lat: Double?, val lon: Double?, val uses: Int)

/** Sunday = 1 … Saturday = 7, as iOS's Calendar numbers weekdays. */
fun nyWeekday(ts: Double, zone: ZoneId = Fmt.NY): Int = Instant.ofEpochMilli((ts * 1000).toLong()).atZone(zone).dayOfWeek.value % 7 + 1
fun nyFractionalHour(ts: Double, zone: ZoneId = Fmt.NY): Double { val z = Instant.ofEpochMilli((ts * 1000).toLong()).atZone(zone); return z.hour + z.minute / 60.0 }

@Serializable
data class Habits(val uses: List<TripUse> = emptyList()) {
    companion object { const val CAP = 400 }

    /** Records a use; the same trip within 20 minutes of the last record of it is the same use. Returns the new history (same object when nothing changed). */
    fun record(origin: String, dest: String, ts: Double, lat: Double?, lon: Double?, zone: ZoneId = Fmt.NY): Habits {
        if (origin.isEmpty() || dest.isEmpty() || origin == dest) return this
        val last = uses.lastOrNull { it.origin == origin && it.dest == dest }
        if (last != null && ts - last.ts < 1200) return this
        var next = uses + TripUse(origin, dest, ts, nyFractionalHour(ts, zone), nyWeekday(ts, zone), lat, lon)
        if (next.size > CAP) next = next.drop(next.size - CAP)
        return Habits(next)
    }

    /** The trip the rider most likely wants now: every past use votes, weighted by hour, kind of day and place. */
    fun likelyTrip(hour: Double, weekday: Int, lat: Double?, lon: Double?): HabitGuess? {
        class Tally(val o: String, val d: String, var s: Double = 0.0, var n: Int = 0)
        val score = HashMap<String, Tally>()
        val weekend = weekday == 1 || weekday == 7
        for (u in uses) {
            var dh = abs(u.hour - hour); dh = min(dh, 24 - dh)
            if (dh > 2) continue
            val tw = 1 - dh / 2
            val uw = u.weekday == 1 || u.weekday == 7
            val dw = if (u.weekday == weekday) 1.0 else if (uw == weekend) 0.7 else 0.2
            var lw = 0.8
            if (lat != null && lon != null && u.lat != null && u.lon != null) {
                val d = haversineM(LatLon(lat, lon), LatLon(u.lat, u.lon))
                lw = if (d <= 300) 1.5 else if (d <= 1500) 1.0 else 0.4
            }
            val e = score.getOrPut(u.origin + ">" + u.dest) { Tally(u.origin, u.dest) }
            e.s += tw * dw * lw; e.n += 1
        }
        val best = score.values.maxByOrNull { it.s } ?: return null
        if (best.n < 2 || best.s < 1.5) return null
        return HabitGuess(best.o, best.d, best.n, best.s)
    }

    fun suggestedHome(): PlaceSuggestion? = suggest(home = true, excluding = null)
    fun suggestedWork(excludingHome: String?): PlaceSuggestion? = suggest(home = false, excluding = excludingHome)

    /** Home: the morning origin and the evening destination. Work: the reverse. Three such uses; the pin is where the phone tended to be when leaving. */
    private fun suggest(home: Boolean, excluding: String?): PlaceSuggestion? {
        val votes = HashMap<String, Int>()
        val from = HashMap<String, MutableList<LatLon>>()
        for (u in uses) {
            val morning = u.hour < 12
            val evening = u.hour >= 15
            val originVote = if (home) morning else evening
            val destVote = if (home) evening else morning
            if (originVote) {
                votes[u.origin] = (votes[u.origin] ?: 0) + 1
                if (u.lat != null && u.lon != null) from.getOrPut(u.origin) { ArrayList() }.add(LatLon(u.lat, u.lon))
            }
            if (destVote) votes[u.dest] = (votes[u.dest] ?: 0) + 1
        }
        if (excluding != null) votes.remove(excluding)
        val (station, n) = votes.entries.maxByOrNull { it.value }?.let { it.key to it.value } ?: return null
        if (n < 3) return null
        var pin: LatLon? = null
        val pts = from[station]
        if (pts != null && pts.size >= 2) {
            val c = LatLon(pts.sumOf { it.lat } / pts.size, pts.sumOf { it.lon } / pts.size)
            if (pts.all { haversineM(it, c) <= 400 }) pin = c
        }
        return PlaceSuggestion(station, pin?.lat, pin?.lon, n)
    }
}
