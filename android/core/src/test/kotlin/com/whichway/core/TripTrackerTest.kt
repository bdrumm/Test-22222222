package com.whichway.core

import com.whichway.core.Ride.start
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class TripTrackerTest {
    private val D = MotionEvent.Kind.departed
    private val A = MotionEvent.Kind.alighted

    @Test
    fun approachLearnsStreetPaceAndArrivalStartsTheStationClock() {
        val t = TripTracker(start(), 900.0)
        assertEquals(TripPhase.approaching, t.phase)
        var d = 900.0; var ts = 1000.0
        while (d > 100) { t.location(ts, d, null, ts); d -= 39; ts += 30 }
        assertEquals(TripPhase.atStation, t.phase)
        assertEquals(ts - 30, t.timeline.arrivedStationTs)
        assertTrue(t.timeline.walkMeters > 600)
        assertEquals(78.0, t.timeline.walkSpeedMPerMin!!, 1.0)
        for (i in 0 until 40) t.motion(Ride.sec(ts + i, true))
        for (i in 40 until 70) t.motion(Ride.sec(ts + i, false))
        assertTrue(t.timeline.platformObserved)
        assertEquals(70.0, t.timeline.accessSec!!, 25.0)
    }

    @Test
    fun startAtTheStationThenRideAndAlightEndsTheTrip() {
        val t = TripTracker(start(), 40.0)
        assertEquals(TripPhase.atStation, t.phase)
        t.updateForecast(1100.0, 1700.0, 1000.0)
        val dr = Driver(t)
        dr.stand(60); dr.depart()
        assertEquals(TripPhase.riding, t.phase)
        assertEquals(1060.0, t.timeline.corroboratedDepartureTs)
        t.updateForecast(1300.0, 1750.0, 1070.0)
        assertEquals(1100.0, t.timeline.forecastBoardTs); assertEquals(1700.0, t.timeline.liveArriveTs)
        dr.ride(580); dr.stand(20); dr.walk(20)
        assertEquals(A, t.timeline.events.last().kind)
        t.tick(dr.ts)
        assertEquals(TripPhase.arrived, t.phase); assertEquals("alighted", t.timeline.endedBy)
    }

    @Test
    fun twoLegsMeasureTheChangeAndOnlyTheSecondAlightingEnds() {
        val t = TripTracker(start(legs = 2, transfer = "X"), 20.0)
        t.updateForecast(1030.0, 2100.0, 1000.0)
        val dr = Driver(t)
        dr.stand(30); dr.depart(); dr.ride(300); dr.stand(15); dr.walk(45); dr.stand(60); dr.depart()
        assertEquals(TripPhase.riding, t.phase)
        assertEquals(45.0, t.timeline.transferWalkSec!!, 2.0)
        t.tick(dr.ts)
        assertNotEquals(TripPhase.arrived, t.phase, "one alighting on a two-leg trip is the change, not the end")
        dr.ride(600); dr.stand(15); dr.walk(20)
        t.tick(dr.ts)
        assertEquals(TripPhase.arrived, t.phase)
    }

    @Test
    fun aPushInTheFirstSecondsOfSensingIsNotADeparture() {
        val t = TripTracker(start(), 20.0)
        val dr = Driver(t)
        dr.depart()
        assertTrue(t.timeline.events.isEmpty()); assertEquals(TripPhase.atStation, t.phase)
        dr.stand(30); dr.depart()
        assertEquals(listOf(D), t.timeline.events.map { it.kind }); assertEquals(TripPhase.riding, t.phase)
    }

    @Test
    fun wellPastTheArrivalAndNearTheDestinationEndsTheTrip() {
        val t = TripTracker(start(), 20.0)
        t.updateForecast(1100.0, 1700.0, 1000.0)
        val dr = Driver(t); dr.stand(30); dr.depart()
        assertEquals(TripPhase.riding, t.phase)
        t.location(1800.0, 6000.0, 540.0, 1800.0)
        assertNotEquals(TripPhase.arrived, t.phase)
        t.location(1830.0, 6000.0, 560.0, 1830.0)
        assertEquals(TripPhase.arrived, t.phase); assertEquals("arrived", t.timeline.endedBy)
    }

    @Test
    fun noSensorsAssumesTheRideAndEndsNearTheDestinationOrOnTimeout() {
        val t = TripTracker(start(), 30.0)
        t.updateForecast(1100.0, 1700.0, 1000.0)
        t.tick(1150.0); assertEquals(TripPhase.atStation, t.phase)
        t.tick(1230.0); assertEquals(TripPhase.riding, t.phase); assertTrue(t.timeline.rideAssumed)
        t.location(1350.0, 5000.0, 200.0, 1350.0); assertEquals(TripPhase.riding, t.phase)
        t.location(1450.0, 5200.0, 120.0, 1450.0); assertEquals(TripPhase.arrived, t.phase); assertEquals("arrived", t.timeline.endedBy)
        val u = TripTracker(start(), 30.0)
        u.updateForecast(1100.0, 1700.0, 1000.0)
        u.tick(2299.0); assertNotEquals(TripPhase.arrived, u.phase)
        u.tick(2300.0); assertEquals(TripPhase.arrived, u.phase); assertEquals("timeout", u.timeline.endedBy)
    }

    @Test
    fun forecastFreezesOnceTheBoardingTimePassesAtTheStation() {
        val t = TripTracker(start(), 30.0)
        t.updateForecast(1100.0, 1700.0, 1000.0)
        t.updateForecast(1110.0, 1710.0, 1050.0)
        assertEquals(1710.0, t.timeline.liveArriveTs)
        t.updateForecast(1900.0, 2500.0, 1111.0)
        assertEquals(1710.0, t.timeline.liveArriveTs); assertEquals(1110.0, t.timeline.forecastBoardTs)
        t.tick(1240.0); assertEquals(TripPhase.riding, t.phase)
        t.location(1420.0, 4000.0, 80.0, 1420.0); assertEquals(TripPhase.arrived, t.phase)
        val a = TripTracker(start(), 900.0)
        a.updateForecast(1100.0, 1700.0, 1000.0); a.updateForecast(1900.0, 2500.0, 1111.0)
        assertEquals(2500.0, a.timeline.liveArriveTs)
    }

    @Test
    fun aTrainGoneFromTheFeedJustBeforeItsTimeCountsAsDeparted() {
        // Android: the feed dropped the F sixteen seconds before its predicted platform moment (Oct 9, 3:00:49 against
        // 3:01:05), so the planner moved on and the forecast would have followed the next train for ever
        val t = TripTracker(start(), 30.0)
        t.updateForecast(1100.0, 1700.0, 1000.0)
        t.forecastTrainDeparted(1084.0)
        assertEquals(1084.0, t.timeline.forecastBoardTs)
        t.updateForecast(1500.0, 2100.0, 1085.0)             // the planner's next train: frozen out
        assertEquals(1084.0, t.timeline.forecastBoardTs); assertEquals(1700.0, t.timeline.liveArriveTs)
        t.tick(1203.0); assertEquals(TripPhase.atStation, t.phase)
        t.tick(1204.0); assertEquals(TripPhase.riding, t.phase); assertTrue(t.timeline.rideAssumed)
        // too early to be that train leaving: a train dropped five minutes ahead of time is not a departure
        val u = TripTracker(start(), 30.0)
        u.updateForecast(1100.0, 1700.0, 700.0)
        u.forecastTrainDeparted(800.0)
        assertEquals(1100.0, u.timeline.forecastBoardTs)
        // and on the way to the station nothing freezes
        val v = TripTracker(start(), 900.0)
        v.updateForecast(1100.0, 1700.0, 1000.0); v.forecastTrainDeparted(1084.0)
        assertEquals(1100.0, v.timeline.forecastBoardTs)
    }

    @Test
    fun standingStillAtTheDestinationEndsOnTheClock() {
        val t = TripTracker(start(), 30.0)
        t.updateForecast(1100.0, 1700.0, 1000.0)
        t.location(1380.0, 5000.0, 90.0, 1380.0)
        assertNotEquals(TripPhase.arrived, t.phase)
        t.tick(1399.0); assertNotEquals(TripPhase.arrived, t.phase)
        t.tick(1400.0); assertEquals(TripPhase.arrived, t.phase); assertEquals("arrived", t.timeline.endedBy)
        val u = TripTracker(start(), 30.0)
        u.updateForecast(1100.0, 1700.0, 1000.0); u.location(700.0, 5000.0, 90.0, 700.0); u.tick(1500.0)
        assertNotEquals(TripPhase.arrived, u.phase)
        val v = TripTracker(start(), 900.0)
        v.updateForecast(1100.0, 1700.0, 1000.0); v.location(1450.0, 4000.0, 100.0, 1450.0)
        assertEquals(TripPhase.arrived, v.phase)
    }

    @Test
    fun replanToATwoLegRouteWaitsForTheSecondAlighting() {
        val t = TripTracker(start(), 20.0)
        t.updateForecast(1030.0, 1700.0, 1000.0)
        val dr = Driver(t)
        dr.stand(30); dr.depart()
        assertEquals(TripPhase.riding, t.phase)
        t.replan(2, "X"); t.setLiveArrival(2400.0)
        assertEquals(2, t.timeline.legs); assertEquals("X", t.timeline.transferStation)
        dr.ride(300); dr.stand(15); dr.walk(45)
        assertEquals(A, t.timeline.events.last().kind)
        t.tick(dr.ts + 200); assertEquals(TripPhase.riding, t.phase)
        dr.stand(60); dr.depart(); dr.ride(600); dr.stand(15); dr.walk(45)
        assertEquals(2, t.timeline.events.count { it.kind == A })
        t.tick(dr.ts); assertEquals(TripPhase.riding, t.phase)
        t.tick(dr.ts + 200); assertEquals(TripPhase.arrived, t.phase); assertEquals("alighted", t.timeline.endedBy)
        assertNotNull(t.timeline.transferWalkSec)
    }

    @Test
    fun theLiveArrivalOfTheBoardedTrainMovesTheClockTheTripEndsBy() {
        val t = TripTracker(start(), 20.0)
        t.updateForecast(1030.0, 1700.0, 1000.0)
        val dr = Driver(t); dr.stand(30); dr.depart()
        t.setLiveArrival(2400.0)
        assertEquals(1700.0, t.timeline.forecastArriveTs); assertEquals(2400.0, t.timeline.liveArriveTs)
        t.tick(2310.0); assertEquals(TripPhase.riding, t.phase)
        t.tick(3001.0); assertEquals("timeout", t.timeline.endedBy)
    }

    @Test
    fun aFixTooUnsureToActOnChangesNothingAndTheStreetGuardNeedsASureOne() {
        val t = TripTracker(start(), null)
        assertEquals(TripPhase.approaching, t.phase)
        t.location(1000.0, 480.0, null, 1000.0, 350.0)
        val dr = Driver(t, 1001.0); dr.stand(30); dr.depart()
        assertEquals(TripPhase.riding, t.phase, "an unsure far fix does not hold back the departure")
        val s = TripTracker(start(), null)
        s.location(1000.0, 480.0, null, 1000.0, 20.0)
        val ds = Driver(s, 1001.0); ds.stand(30); ds.depart()
        assertEquals(TripPhase.approaching, s.phase)
        val a = TripTracker(start(), null)
        a.location(1000.0, 160.0, null, 1000.0, 40.0)
        assertEquals(TripPhase.atStation, a.phase)
    }

    @Test
    fun personalModelLearnsAndPredicts() {
        val m = PersonalModel()
        assertEquals(80.0, m.walkSpeedMPerMin); assertNull(m.accessSec("S1"))
        val tl = start().apply { walkMeters = 700.0; walkSeconds = 600.0; arrivedStationTs = 1600.0; platformTs = 1720.0; platformObserved = true; endedTs = 2500.0 }
        assertTrue(m.learn(tl))
        assertEquals(70.0, m.walkSpeedMPerMin, 0.1); assertEquals(120.0, m.accessSec("S1")!!); assertEquals(120.0, m.accessSec("S2")!!)
        assertEquals(600.0, m.placeToStationSec("home", "S1")!!); assertEquals(12, m.walkMinutes(700.0, "S1"))
        val tl2 = tl.copy(walkMeters = 900.0, walkSeconds = 600.0)
        m.learn(tl2)
        assertEquals(80.0, m.walkSpeedMPerMin, 0.1); assertEquals(2, m.trips)
        val x = start(legs = 2, transfer = "X")
        x.events.addAll(listOf(MotionEvent(D, 1.0), MotionEvent(A, 2.0), MotionEvent(D, 3.0)))
        x.walkingSecondsBetweenTrains = 50.0
        m.learn(x); assertNull(m.transferSec("X"))
        m.learn(x); assertEquals(50.0, m.transferSec("X")!!)
        assertFalse(m.learn(TripTimeline(startTs = 1.0, startedBy = "gps", originStation = "S", destStation = "D", legs = 1)))
        val jay = start(legs = 2, transfer = "Jay St-MetroTech")
        jay.events.addAll(x.events)
        assertEquals(180, m.plannedTransferSec("Jay St-MetroTech", 180))
        jay.walkingSecondsBetweenTrains = 17.0; m.learn(jay)
        jay.walkingSecondsBetweenTrains = 12.0; m.learn(jay)
        assertEquals(14.5, m.transferSec("Jay St-MetroTech")!!, 0.01)
        assertEquals(15, m.plannedTransferSec("Jay St-MetroTech", 180)); assertEquals(50, m.plannedTransferSec("X", 30))
        val fake = start(legs = 2, transfer = "Y"); fake.events.addAll(x.events); fake.walkingSecondsBetweenTrains = 5.0
        val before = m.transferDefault.n
        m.learn(fake)
        assertEquals(before, m.transferDefault.n)
    }

    @Test
    fun aWalkTheFeedVouchesForClosesTheRideWithoutTheTrainSeenAtRest() {
        val t = TripTracker(start(legs = 2, transfer = "S4"), 20.0)
        val dr = Driver(t)
        dr.stand(30); dr.depart()
        assertEquals(TripPhase.riding, t.phase); assertTrue(t.onTrain)
        dr.ride(200)
        assertEquals(dr.ts - 1, t.lastMovingTs!!, 0.5); assertNull(t.walking)
        dr.feed(10, true, shake = 0.03)
        assertEquals(listOf(D), t.timeline.events.map { it.kind })
        assertEquals(10, t.walking?.seconds); assertEquals(dr.ts - 10, t.walking!!.startTs, 0.5)
        val off = t.walking!!.startTs
        t.alightWalking(off)
        assertEquals(listOf(D, A), t.timeline.events.map { it.kind }); assertEquals(off, t.timeline.events.last().ts)
        assertEquals(listOf(0), t.timeline.rideStops); assertEquals(TripPhase.riding, t.phase); assertFalse(t.onTrain); assertEquals(1, t.timeline.legsRidden)
        dr.walk(20)
        assertTrue(t.timeline.walkingSecondsBetweenTrains >= 30)
        dr.stand(40); dr.depart()
        assertEquals(listOf(D, A, D), t.timeline.events.map { it.kind }); assertTrue(t.onTrain)
        assertEquals(30.0, t.timeline.transferWalkSec!!, 1.0)
    }

    // RideStopsTests

    @Test
    fun stationStopsAreCountedPerRideAndTheLastOneIsTheAlighting() {
        val t = TripTracker(start(legs = 1, distance = 20.0), 20.0)
        val dr = Driver(t)
        dr.stand(30); dr.depart()
        assertEquals(TripPhase.riding, t.phase)
        dr.ride(90); dr.stand(25); dr.ride(80); dr.stand(6); dr.ride(70); dr.stand(30); dr.ride(60); dr.stand(20)
        assertEquals(2, t.stopsFelt)
        dr.walk(20)
        assertEquals(A, t.timeline.events.last().kind); assertEquals(listOf(3), t.timeline.rideStops); assertEquals(0, t.stopsFelt)
        t.setBoarded(BoardedLeg(0, "F_N", "F_N|x", "F_N", 0.9, LineBelief.Verdict.onPlan, listOf("departure")))
        t.setBoarded(BoardedLeg(0, "F_N", "F_N|x", "F_N", 0.97, LineBelief.Verdict.onPlan, listOf("departure", "stops", "alighting")))
        assertEquals(1, t.timeline.boarded.size); assertEquals(0.97, t.timeline.boarded[0].confidence); assertEquals(1, t.timeline.legsRidden)
    }

    @Test
    fun thePersonalModelLearnsWhichLineWasTaken() {
        val m = PersonalModel()
        val tl = TripTimeline(startTs = 0.0, startedBy = "hand", originStation = "A", destStation = "B", legs = 1)
        assertNull(m.chosenLineShare("A", "B", 0, "F_N"))
        for (key in listOf("G_N", "G_N", "F_N")) {
            tl.boarded.clear(); tl.boarded.add(BoardedLeg(0, key, null, "F_N", 0.8, if (key == "F_N") LineBelief.Verdict.onPlan else LineBelief.Verdict.switched, listOf("departure")))
            assertTrue(m.learn(tl))
        }
        assertEquals(1.0 / 3.0, m.chosenLineShare("A", "B", 0, "F_N")!!, 1e-9)
        tl.boarded.clear(); tl.boarded.add(BoardedLeg(0, "F_N", null, "F_N", 0.5, LineBelief.Verdict.unsure, listOf("departure")))
        assertFalse(m.learn(tl), "an unsure leg teaches nothing")
        val decoded = WWJson.decodeFromString(PersonalModel.serializer(), """{"walkSpeed": {"mean": 80, "n": 2}, "access": {}, "accessDefault": {"mean": 0, "n": 0}, "transfer": {}, "transferDefault": {"mean": 0, "n": 0}, "placeToStation": {}, "trips": 2}""")
        assertNull(decoded.lineChoices); assertEquals(2, decoded.trips)
    }

    @Test
    fun stopTimesAreRecordedAndASamePlatformChangeClosesTheRide() {
        val t = TripTracker(start(legs = 2, transfer = "S4", distance = 20.0), 20.0)
        val dr = Driver(t)
        dr.stand(30); dr.depart()
        assertEquals(TripPhase.riding, t.phase)
        dr.ride(90); dr.stand(25); dr.ride(80)
        assertEquals(1, t.stopTimes.size); assertEquals(1000.0 + 36 + 90, t.stopTimes[0], 1.0)
        dr.stand(100)
        assertEquals(2, t.stopTimes.size); assertNotNull(t.standingSince); assertEquals(dr.ts - 100, t.standingSince!!, 1.0)
        t.alightStanding(t.standingSince!!)
        assertEquals(listOf(D, A), t.timeline.events.map { it.kind }); assertEquals(listOf(2), t.timeline.rideStops)
        assertEquals(2, t.lastRideStopTimes.size); assertTrue(t.stopTimes.isEmpty()); assertEquals(TripPhase.riding, t.phase)
        dr.stand(60); dr.depart()
        assertEquals(listOf(D, A, D), t.timeline.events.map { it.kind }); assertEquals(1, t.timeline.legsRidden)
    }

    // LineFallbackTests

    @Test
    fun withSensorsOnThePlannedRideIsAssumedOnceTheBoardingTimePassesAndAFeltDepartureTakesOver() {
        val t = TripTracker(start(distance = 40.0, by = "gps", place = null), 40.0)
        t.updateForecast(1100.0, 1700.0, 1000.0)
        val dr = Driver(t)
        dr.walk(30)
        t.tick(1230.0); assertEquals(TripPhase.atStation, t.phase)
        dr.stand(30)
        assertNotNull(t.timeline.platformTs)
        t.tick(1210.0); assertEquals(TripPhase.atStation, t.phase)
        t.tick(1225.0); assertEquals(TripPhase.riding, t.phase); assertTrue(t.timeline.rideAssumed)
        dr.ts = 1300.0; dr.depart()
        assertEquals(MotionEvent(D, 1300.0), t.timeline.events.first()); assertFalse(t.timeline.rideAssumed); assertEquals(TripPhase.riding, t.phase)
    }

    // OnTrainTests (the tracker half)

    @Test
    fun aRideBegunFromTheTrainEndsAtTheAlighting() {
        val t = TripTracker(start(distance = 2000.0, by = "onboard", place = null), 2000.0)
        assertEquals(TripPhase.approaching, t.phase)
        t.beginRiding(1000.0, 820.0)
        assertEquals(TripPhase.riding, t.phase); assertEquals(listOf(D), t.timeline.events.map { it.kind }); assertEquals(820.0, t.timeline.events[0].ts)
        assertEquals(820.0, t.timeline.arrivedStationTs); assertFalse(t.timeline.rideAssumed)
        t.setLiveArrival(1700.0)
        val dr = Driver(t)
        dr.ride(300); assertEquals(1, t.timeline.events.size)
        dr.stand(20); dr.ride(200); dr.stand(20); dr.walk(20)
        assertEquals(A, t.timeline.events.last().kind); assertEquals(listOf(2), t.timeline.rideStops)
        t.tick(1600.0)
        assertEquals(TripPhase.arrived, t.phase); assertEquals("alighted", t.timeline.endedBy)
        val r = TripTracker(start(distance = 10.0, place = null), 10.0)
        r.beginRiding(1000.0, 990.0); r.beginRiding(1100.0, 1090.0)
        assertEquals(1, r.timeline.events.size)
    }

    @Test
    fun pullingAwayFromTheStationAtATrainsPaceStartsTheRide() {
        val t = TripTracker(start(distance = 600.0, place = null), 600.0)
        var d = 600.0; var ts = 1000.0
        t.location(ts, d, null, ts)
        repeat(2) { ts += 10; d += 80; t.location(ts, d, null, ts) }
        assertEquals(TripPhase.approaching, t.phase)
        ts += 10; d += 80; t.location(ts, d, null, ts)
        assertEquals(TripPhase.riding, t.phase); assertEquals(listOf(D), t.timeline.events.map { it.kind })
        assertEquals(1000.0, t.timeline.events.first().ts, 1.0)
        val w = TripTracker(start(distance = 600.0, place = null), 600.0)
        d = 600.0; ts = 1000.0
        repeat(3) { ts += 10; d -= 20; w.location(ts, d, null, ts) }
        assertEquals(TripPhase.approaching, w.phase)
        val s = TripTracker(start(distance = 600.0, place = null), 600.0)
        d = 600.0; ts = 1000.0
        repeat(6) { ts += 10; d += 15; s.location(ts, d, null, ts) }
        assertEquals(TripPhase.approaching, s.phase)
    }
}
