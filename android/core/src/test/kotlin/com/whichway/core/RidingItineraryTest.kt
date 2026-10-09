package com.whichway.core

import com.whichway.core.Ride.board
import com.whichway.core.Ride.train
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class RidingItineraryTest {
    private val lagG = PlatformTiming.recordedLag("G"); private val lagC = PlatformTiming.recordedLag("C")
    private val sched = ClientSchedule.parse("""
        {"lines": {"G_N": {"stops": ["g0","g1","g2","g3"], "names": ["a","b","Hoyt","d"], "run_sec": [120, 120, 120]},
                   "C_N": {"stops": ["c0","c1","c2","c3"], "names": ["x","Hoyt","y","14 St"], "run_sec": [150, 150, 150]}}}
    """)
    private fun option(): PathOption {
        val p = PathOption("G>C", listOf(
            PathLeg("g0", "g2", mutableListOf("G_N"), mutableListOf("G"), mutableMapOf("G_N" to Span(0, 2)), 240, 2),
            PathLeg("c1", "c3", mutableListOf("C_N"), mutableListOf("C"), mutableMapOf("C_N" to Span(1, 3)), 300, 2)),
            PathTransfer("g2", "c1", "Hoyt", 120))
        p.schedSec = 240 + 120 + 300; p.wait2Sec = 300.0
        return p
    }
    private val boardedG = BoardingCandidate("G_N|g1", "G_N", "G", 1000.0, 1690.0, 2, false, true, 0, 2, null, mapOf(1 to 1400.0, 2 to 1690.0))

    @Test
    fun theBoardedTrainAndTheFirstConnectionItMakes() {
        val gAt = 1700 - lagG
        val boards = mapOf("G_N" to board("G_N", listOf(train("g1", "G_N", listOf(2 to 1700.0, 3 to 1820.0)))),
            "C_N" to board("C_N", listOf(train("c1", "C_N", listOf(1 to gAt + 120 + lagC - 30, 2 to 1950.0, 3 to 2100.0)),
                train("c2", "C_N", listOf(1 to gAt + 120 + lagC + 80, 2 to 2050.0, 3 to 2200.0)), train("c3", "C_N", listOf(1 to gAt + 120 + lagC + 280, 3 to 2400.0)))))
        val it = ridingItinerary(boards, sched, option(), 0, boardedG, 1500.0)!!
        assertEquals(2, it.legs.size); assertEquals(1000 - lagG, it.boardTs); assertEquals(gAt, it.legs[0].arriveTs)
        assertEquals("c2", it.legs[1].train.tripId); assertEquals(2200 - lagC, it.arriveTs); assertEquals(120.0, it.walkSec)
        assertEquals(80.0, it.connectionMarginSec!!, 1e-9); assertEquals(200.0, it.nextIfMissedSec!!, 1e-9); assertEquals(240.0, it.legs[0].schedRideSec)
    }

    @Test
    fun withoutAConnectingTrainTheScheduleStandsInAndWithTheTrainGoneThereIsNothing() {
        var boards = mapOf("G_N" to board("G_N", listOf(train("g1", "G_N", listOf(3 to 1820.0)))), "C_N" to board("C_N", emptyList()))
        val it = ridingItinerary(boards, sched, option(), 0, boardedG, 1750.0)!!
        assertEquals(1, it.legs.size); assertEquals(1690 - lagG, it.legs[0].arriveTs)
        assertEquals(maxOf(1690 - lagG + 120, 1750.0) + 300 + 300, it.arriveTs); assertNull(it.connectionMarginSec)
        boards = boards + ("G_N" to board("G_N", emptyList()))
        assertNull(ridingItinerary(boards, sched, option(), 0, boardedG, 1750.0))
    }

    @Test
    fun onTheLastLegTheTrainsOwnArrivalIsTheItinerary() {
        val boards = mapOf("C_N" to board("C_N", listOf(train("c2", "C_N", listOf(2 to 2050.0, 3 to 2210.0)))))
        val c = BoardingCandidate("C_N|c2", "C_N", "C", 1900.0, 2200.0, 2, true, true, 1, 3, null, mapOf(2 to 2050.0, 3 to 2200.0))
        val it = ridingItinerary(boards, sched, option(), 1, c, 2000.0)!!
        assertEquals(1, it.legs.size); assertEquals(1900 - lagC, it.boardTs); assertEquals(2210 - lagC, it.arriveTs)
        assertEquals(210 - lagC, it.totalSec); assertEquals(310.0 - 660, it.rideVsSchedSec)
    }

    @Test
    fun thePlansTrainBecomesTheRideWhenNoneWasFeltAndTheConnectionStandsBetweenTrains() {
        val g = train("g1", "G_N", listOf(0 to 1000 + lagG, 1 to 1400 + lagG, 2 to 1690 + lagG))
        val c = train("c1", "C_N", listOf(1 to 1900 + lagC, 3 to 2500 + lagC))
        val c2 = train("c2", "C_N", listOf(1 to 2200 + lagC, 3 to 2800 + lagC))
        val boards = mapOf("G_N" to board("G_N", listOf(g)), "C_N" to board("C_N", listOf(c, c2)))
        val opt = option()
        val it = pathTrips(boards, sched, opt, 900.0).first()
        val pc = plannedCandidate(it, opt, 0)!!
        assertEquals("G_N|g1", pc.trainId); assertEquals("G_N", pc.key); assertEquals(1000 + lagG, pc.boardTs, 1e-6)
        assertEquals(0, pc.boardIdx); assertEquals(2, pc.alightIdx); assertEquals(2, pc.stopsToAlight); assertEquals(1690 + lagG, pc.stopTs[2]!!, 1e-6); assertTrue(pc.chosen)
        val ride = ridingItinerary(boards, sched, opt, 0, pc, 1100.0)!!
        assertEquals(2, ride.legs.size); assertEquals(1690.0, ride.legs[0].arriveTs, 1e-6); assertEquals("C_N|c1", ride.legs[1].train.id); assertEquals(300.0, ride.nextIfMissedSec!!, 1e-6)
        assertEquals("G_N|g1", plannedCandidate(ride, opt, 0)?.trainId)
        val conn = connectionItinerary(boards, sched, opt, 1, 1750.0)!!
        assertEquals(1, conn.legs.size); assertEquals("C_N|c1", conn.legs[0].train.id); assertEquals(1900.0, conn.boardTs, 1e-6); assertEquals(2500.0, conn.arriveTs, 1e-6); assertEquals(300.0, conn.nextIfMissedSec!!, 1e-6)
        assertEquals("C_N|c2", connectionItinerary(boards, sched, opt, 1, 1900 + PlatformTiming.DWELL_SEC + 1)?.legs?.get(0)?.train?.id)
    }

    @Test
    fun theTrainsThatLeftTheStationRecentlyMostRecentFirst() {
        val sched2 = ClientSchedule.parse("""
            {"lines": {"G_N": {"stops": ["g0","g1","g2","g3","g4"], "names": ["4 Av","Smith","Bergen","Hoyt","Fulton"], "run_sec": [120, 120, 120, 120]},
                       "R_N": {"stops": ["r0","r1","r2","r3"], "names": ["4 Av","Union","Atlantic","DeKalb"], "run_sec": [150, 150, 150]}}}
        """)
        val now = 10_000.0
        val boards = mapOf("G_N" to board("G_N", listOf(train("g-left", "G_N", listOf(2 to now + 100, 3 to now + 220, 4 to now + 340)),
                train("g-here", "G_N", listOf(0 to now + 30, 1 to now + 150)), train("g-old", "G_N", listOf(4 to now + 10)), train("g-gone", "G_N", listOf(4 to now + 200)))),
            "R_N" to board("R_N", listOf(train("r-left", "R_N", listOf(1 to now + 20, 2 to now + 170)))))
        val out = trainsAhead(boards, sched2, mapOf("G_N" to 0, "R_N" to 0), mapOf("G_N" to 3, "R_N" to 3), setOf("R_N"), now)
        assertEquals(listOf("R_N|r-left", "G_N|g-left"), out.map { it.trainId })
        val g = out[1]
        assertEquals(now - 140, g.boardTs); assertEquals(now + 220, g.alightTs); assertEquals(3, g.stopsToAlight)
        assertEquals(mapOf(2 to now + 100, 3 to now + 220, 4 to now + 340), g.stopTs); assertEquals(2, g.progressIdx); assertFalse(g.onLeg); assertTrue(out[0].onLeg)
    }
}
