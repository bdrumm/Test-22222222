package com.whichway.core

import java.io.File
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Assume.assumeTrue
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class StationsTest {
    @Test
    fun presetWindows() {
        val am = CommutePreset(name = "am", originId = "a", destId = "b", startMinute = 6 * 60, endMinute = 10 * 60)
        assertTrue(am.isActive(7 * 60, 3))
        assertFalse(am.isActive(10 * 60, 3), "the end is exclusive")
        assertFalse(am.isActive(7 * 60, 1), "weekdays only")
        val night = CommutePreset(name = "pm", originId = "a", destId = "b", startMinute = 22 * 60, endMinute = 2 * 60, weekdaysOnly = false)
        assertTrue(night.isActive(23 * 60, 1))
        assertTrue(night.isActive(60, 7))
        // Wednesday 2026-09-23 08:30 New York
        val ts = ZonedDateTime.of(2026, 9, 23, 8, 30, 0, 0, ZoneId.of("America/New_York")).toEpochSecond().toDouble()
        assertTrue(am.isActive(ts))
        assertFalse(night.isActive(ts))
        assertEquals("6:00–10:00 weekdays", am.windowText)
        assertEquals("2026-09-23", Fmt.dayStamp(ts))
        assertEquals("8:30", Fmt.hhmm(ts))
    }

    @Test
    fun nearestStationsFromGeometry() {
        val sched = ClientSchedule.parse("""
            {"lines": {"6_N": {"stops": ["635N", "634N", "633N"], "names": ["14 St-Union Sq", "23 St", "28 St"], "run_sec": [90, 80], "dist_m": [700, 600]},
                       "L_N": {"stops": ["L03N", "L02N"], "names": ["14 St-Union Sq", "3 Av"], "run_sec": [60], "dist_m": [500]}},
             "transfers": {"635N": [{"line": "L_N", "stop": "L03N", "min_sec": 120}], "L03N": [{"line": "6_N", "stop": "635N", "min_sec": 120}]}}
        """)
        val geo = WWJson.decodeFromString(ClientGeometry.serializer(), """
            {"lines": {"6_N": {"coords": [[40.7359, -73.9906], [40.7397, -73.9866], [40.7431, -73.9842]], "shape": []},
                       "L_N": {"coords": [[40.7347, -73.9906], [40.7327, -73.9860]], "shape": []}}}
        """)
        val index = StationIndex(sched)
        val coords = stationCoordinates(sched, index, geo)
        val usq = index.stationOf("635N")
        assertEquals(usq, index.stationOf("L03N"), "Union Sq is one complex (6 and L)")
        assertEquals((40.7359 + 40.7347) / 2, coords[usq]!!.lat, 1e-9)
        val near = nearestStations(LatLon(40.7400, -73.9870), coords, index, 3)
        assertEquals("23 St", near.first().station.name)
        // from Union Sq: 23 St and 28 St direct on the 6, 3 Av direct on the L
        val reach = reachableStations(sched, index, usq)
        assertEquals("direct", reach[index.stationOf("633N")]?.how)
        val paths = enumeratePaths(sched, index, usq, index.stationOf("633N"))
        assertEquals(1, paths.size)
        assertEquals("6 direct", paths[0].label)
        assertEquals(170, paths[0].schedSec)
    }

    /** The real published schedule, when a local site build is there (make site / make site-synthetic). */
    @Test
    fun publishedScheduleParsesAndPlans() {
        val dir = File(System.getProperty("whichway.site") ?: "")
        val f = File(dir, "client_schedule.json")
        assumeTrue("no local site build", f.exists())
        val sched = ClientSchedule.parse(f.readText())
        val index = StationIndex(sched)
        assertTrue(sched.lines.isNotEmpty() && index.stations.isNotEmpty())
        File(dir, "client_lines.json").takeIf { it.exists() }?.let { assertTrue(ClientLines.parse(it.readText()).isNotEmpty()) }
        // 4 Av-9 St to 14 St (A/C/E at 8 Av): the commute the iOS app was built around
        val from = index.stationOf("F23N")
        val to = index.stationOf("A31N")
        assumeTrue("stations not in this export", index.stations.containsKey(from) && index.stations.containsKey(to))
        val paths = enumeratePaths(sched, index, from, to)
        assertTrue(paths.isNotEmpty(), "a path from 4 Av-9 St to 14 St")
        assertTrue(paths.any { it.legs.size == 2 }, "with one change")
    }
}
