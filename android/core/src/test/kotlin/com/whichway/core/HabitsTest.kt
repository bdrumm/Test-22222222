package com.whichway.core

import java.time.ZoneId
import java.time.ZonedDateTime
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** ios/WhichWayCore/Tests/WhichWayCoreTests/HabitsTests.swift */
class HabitsTest {
    private val ny = ZoneId.of("America/New_York")
    private fun ts(day: Int, hour: Int, minute: Int = 0) = ZonedDateTime.of(2026, 9, day, hour, minute, 0, 0, ny).toEpochSecond().toDouble()
    private val home = LatLon(40.6703, -73.9898)
    private val work = LatLon(40.7359, -73.9906)

    @Test
    fun recordsOncePerTwentyMinutesAndCaps() {
        var h = Habits()
        h = h.record("A", "B", ts(21, 8), null, null)
        h = h.record("A", "B", ts(21, 8, 10), null, null)
        h = h.record("A", "B", ts(21, 8, 30), null, null)
        h = h.record("A", "A", ts(21, 9), null, null)
        assertEquals(2, h.uses.size)
        assertEquals(2, h.uses[0].weekday, "a Monday")
        assertEquals(8.0, h.uses[0].hour, 0.01)
        for (i in 0 until Habits.CAP + 50) h = h.record("X", "Y", ts(1, 0) + i * 1300.0, null, null)
        assertEquals(Habits.CAP, h.uses.size)
    }

    @Test
    fun likelyTripFollowsHourDayAndPlace() {
        var h = Habits()
        for (d in 21..25) {
            h = h.record("A", "B", ts(d, 8, 15), home.lat, home.lon)
            h = h.record("B", "A", ts(d, 17, 45), work.lat, work.lon)
        }
        h = h.record("A", "C", ts(26, 11), home.lat, home.lon)
        val g = h.likelyTrip(8.5, 3, home.lat, home.lon)
        assertEquals("A", g?.origin); assertEquals("B", g?.dest); assertEquals(5, g?.uses)
        val e = h.likelyTrip(18.0, 5, work.lat, work.lon)
        assertEquals("B", e?.origin); assertEquals("A", e?.dest)
        val odd = h.likelyTrip(18.0, 5, home.lat, home.lon)
        assertTrue(odd == null || odd.score < e!!.score)
        assertNull(h.likelyTrip(3.0, 2, null, null))
        assertNull(h.likelyTrip(11.0, 7, null, null), "one Saturday use is not a habit")
    }

    @Test
    fun suggestsHomeAndWorkWithPins() {
        var h = Habits()
        for (d in 21..23) {
            h = h.record("A", "B", ts(d, 8), home.lat, home.lon)
            h = h.record("B", "A", ts(d, 18), work.lat + (d - 22) * 0.001, work.lon)
        }
        val sh = h.suggestedHome()
        assertEquals("A", sh?.stationId); assertEquals(6, sh?.uses)
        assertEquals(home.lat, sh!!.lat!!, 1e-9)
        val sw = h.suggestedWork("A")
        assertEquals("B", sw?.stationId)
        assertEquals(work.lat, sw!!.lat!!, 1e-6, "evening departures 110 m apart still pin")
        assertNull(Habits().suggestedHome())
        // presets: the suggested window and name around a Wednesday 08:30
        val w = CommutePreset.suggestedWindow(ts(23, 8, 30))
        assertEquals(7 * 60, w.first); assertEquals(10 * 60, w.second)
        assertEquals("Morning commute", CommutePreset.suggestedName(ts(23, 8, 30)))
    }
}
