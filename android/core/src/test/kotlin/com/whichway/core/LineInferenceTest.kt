package com.whichway.core

import com.whichway.core.Ride.cand
import com.whichway.core.Ride.candIdx
import com.whichway.core.Ride.train
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class LineInferenceTest {
    private val inf = Ride.feedTimeInference()

    @Test
    fun thePlannedTrainThatLeftWhenTheSensorsFeltItIsTheAnswer() {
        val c = listOf(cand("f1", "F_N", 1000.0, chosen = true), cand("g1", "G_N", 1180.0))
        val b = inf.fromDeparture(c, 1025.0, 0, "F_N")
        assertEquals("F_N", b.bestKey); assertEquals(LineBelief.Verdict.onPlan, b.verdict)
        assertTrue(b.confidence > 0.9); assertEquals(listOf("departure"), b.evidence)
    }

    @Test
    fun aDepartureThatMatchesTheOtherLineOverridesThePrior() {
        val c = listOf(cand("f1", "F_N", 1200.0, chosen = true), cand("g1", "G_N", 1000.0))
        val b = inf.fromDeparture(c, 1030.0, 0, "F_N")
        assertEquals("G_N", b.bestKey); assertEquals(LineBelief.Verdict.switched, b.verdict)
        assertEquals("G", b.route); assertEquals("F", b.chosenRoute)
    }

    @Test
    fun twoTrainsLeavingTogetherStayUnsureUntilTheRideTellsThemApart() {
        val c = listOf(cand("r1", "R_N", 1000.0, 1900.0, 6, chosen = true), cand("n1", "N_N", 1020.0, 1600.0, 2))
        var b = inf.fromDeparture(c, 1035.0, 0, "R_N")
        assertEquals(LineBelief.Verdict.unsure, b.verdict, "${b.byLine}")
        b = inf.withStops(b, c, 2)
        assertEquals("N_N", b.bestKey); assertEquals(LineBelief.Verdict.switched, b.verdict)
        b = inf.withAlighting(b, c, 1625.0)
        assertTrue(b.confidence > 0.97); assertEquals(listOf("departure", "stops", "alighting"), b.evidence)
    }

    @Test
    fun theTransferLegTrustsThePlanMoreAndHabitsMoveThePrior() {
        val c = listOf(cand("a1", "A_S", 1000.0, chosen = true), cand("c1", "C_S", 1040.0))
        val first = inf.fromDeparture(c, 1075.0, 0, "A_S"); val later = inf.fromDeparture(c, 1075.0, 1, "A_S")
        assertTrue(later.byLine["A_S"]!! > first.byLine["A_S"]!!)
        assertEquals(0.5, inf.priorChosen(0, null)); assertEquals(0.7, inf.priorChosen(1, null))
        assertEquals(0.35, inf.priorChosen(0, 0.2), 1e-9); assertEquals(0.85, inf.priorChosen(1, 1.0), 1e-9)
        val c2 = listOf(cand("f1", "F_N", 1000.0, chosen = true), cand("g1", "G_N", 1030.0))
        assertTrue(inf.fromDeparture(c2, 1045.0, 0, "F_N", 0.1).byLine["F_N"]!! < inf.fromDeparture(c2, 1045.0, 0, "F_N").byLine["F_N"]!!)
    }

    @Test
    fun aLineAtThePlatformButNotOnTheLegCountsNearlyAsMuchAndNoCandidatesStaysUnsure() {
        val c = listOf(cand("f1", "F_N", 1000.0, chosen = true), cand("g1", "G_N", 1000.0), cand("d1", "D_N", 1000.0, onLeg = false))
        val p = inf.prior(c, 0, "F_N")
        assertEquals(0.1, p[LineBelief.NONE_KEY]!!, 1e-9)
        assertEquals(0.9 * 0.5, p["F_N|f1"]!!, 1e-9)
        assertEquals(0.9 * 0.5 * 1.0 / 1.8, p["G_N|g1"]!!, 1e-9)
        assertEquals(0.9 * 0.5 * 0.8 / 1.8, p["D_N|d1"]!!, 1e-9)
        val b = inf.fromDeparture(emptyList(), 1000.0, 0, "F_N")
        assertEquals(LineBelief.Verdict.unsure, b.verdict); assertNull(b.boarded); assertTrue(b.evidence.isEmpty())
    }

    @Test
    fun theLogKeepsATrainsTimeAtThePlatformAfterTheFeedMovesItOn() {
        val log = DepartureLog()
        log.observe(listOf(train("f1", "F_N", listOf(3 to 940.0, 4 to 1000.0, 9 to 1600.0))), "F_N", 4, Span(4, 9), true, 900.0)
        log.observe(listOf(train("f1", "F_N", listOf(5 to 1090.0, 9 to 1605.0))), "F_N", 4, Span(4, 9), true, 1030.0)
        val c = log.candidates(1025.0, 300.0, "F_N|f1")
        assertEquals(1, c.size); assertEquals(1000.0, c[0].boardTs); assertEquals(1605.0, c[0].alightTs); assertEquals(5, c[0].stopsToAlight); assertTrue(c[0].chosen)
        log.observe(listOf(train("f2", "F_N", listOf(4 to 1700.0))), "F_N", 4, Span(4, 9), true, 1030.0)
        assertEquals(1, log.candidates(1025.0, 300.0, null).size)
        log.reset()
        assertTrue(log.candidates(1025.0, 300.0, null).isEmpty())
    }

    @Test
    fun thePlanAndTheRidersWordAreBeliefsToo() {
        val plan = LineBelief.fromPlan(0, "F_N", "F_N|f1", "schedule")!!
        assertTrue(plan.assumed); assertEquals(LineBelief.Verdict.onPlan, plan.verdict); assertEquals("F_N|f1", plan.bestTrain)
        assertNull(LineBelief.fromPlan(0, null, null, "schedule"))
        val cands = listOf(BoardingCandidate("G_N|g1", "G_N", "G", 1000.0), BoardingCandidate("G_N|g2", "G_N", "G", 1400.0))
        val hand = LineBelief.byHand(0, "G_N", "F_N", cands, 1380.0)
        assertTrue(hand.byHand); assertFalse(hand.assumed); assertEquals(LineBelief.Verdict.switched, hand.verdict)
        assertEquals("G_N|g2", hand.bestTrain); assertEquals("G_N", hand.boarded?.key)
        val noTrain = LineBelief.byHand(1, "R_N", "R_N", emptyList(), null)
        assertEquals(LineBelief.Verdict.onPlan, noTrain.verdict); assertNull(noTrain.bestTrain)
    }

    // RideEvidenceTests

    @Test
    fun theTrainTheFeedShowedStandingAtThePlatformIsPreferred() {
        val c = listOf(candIdx("f1", "F_N", 1000.0, stoppedAt = 1020.0, chosen = true), candIdx("g1", "G_N", 1000.0))
        val b = inf.fromDeparture(c, 1035.0, 0, "F_N")
        assertEquals("F_N", b.bestKey); assertTrue(b.byLine["F_N"]!! > 0.7); assertEquals(listOf("departure", "position"), b.evidence)
        val plain = inf.fromDeparture(listOf(candIdx("f1", "F_N", 1000.0, chosen = true), candIdx("g1", "G_N", 1000.0)), 1035.0, 0, "F_N")
        assertEquals(listOf("departure"), plain.evidence)
        assertEquals(plain.byLine["G_N"]!!, plain.byLine["F_N"]!!, 1e-9)
        assertTrue(plain.noneShare < 0.03)
    }

    @Test
    fun oneCandidateThatFitsNothingAfterTheDepartureIsNotTakenOnTrust() {
        val f = candIdx("f1", "F_N", 1000.0, alightIdx = 10, stopTs = mapOf(10 to 2100.0), chosen = true)
        val start = inf.fromDeparture(listOf(f), 1030.0, 0, "F_N")
        assertTrue(start.settled, "${start.byLine}")
        var b = inf.withStops(start, listOf(f), 0)
        b = inf.withAlighting(b, listOf(f), 1340.0)
        b = inf.withLocation(b, listOf(f), mapOf("F_N|f1" to 5200.0))
        assertFalse(b.settled, "${b.byLine}"); assertTrue(b.noneShare > b.byLine["F_N"]!!); assertEquals(LineBelief.Verdict.unsure, b.verdict)
    }

    @Test
    fun aTrainLayingOverAtItsTerminalIsNotTakenForTheOneThatLeft() {
        val log = DepartureLog()
        log.observe(listOf(train("l1", "L_S", listOf(0 to 1000.0), at = 0, terminal = true)), "L_S", 0, null, false, 990.0, samePlatform = false)
        log.observe(listOf(train("e1", "E_S", listOf(5 to 1000.0), at = 5)), "E_S", 5, Span(5, 6), true, 990.0)
        val c = log.candidates(1010.0, 300.0, null)
        assertNull(c.first { it.key == "L_S" }.stoppedAtBoardTs, "a layover says nothing")
        assertNotNull(c.first { it.key == "E_S" }.stoppedAtBoardTs)
        assertEquals(false, c.first { it.key == "L_S" }.samePlatform)
        val b = inf.fromDeparture(c, 1010.0, 0, "E_S")
        assertEquals("E_S", b.bestKey); assertTrue(b.settled, "${b.byLine}")
    }

    @Test
    fun theEFromFourteenthStreetWithTheMeasuredPlatformTiming() {
        val real = LineInference()
        val e = BoardingCandidate("E_S|e1", "E_S", "E", 62.0, 220.0, 1, false, true, 5, 6, null, mapOf(6 to 220.0))
        val l = BoardingCandidate("L_S|l1", "L_S", "L", 0.0, null, null, false, false, 0, samePlatform = false, atOrigin = true)
        val a = BoardingCandidate("A_S|a1", "A_S", "A", 207.0, 330.0, 1, true, true, 5, 6, null, mapOf(6 to 330.0))
        val b0 = real.fromDeparture(listOf(l, e, a), 29.0, 0, "A_S")
        assertEquals("E_S", b0.bestKey, "${b0.byLine}"); assertTrue(b0.settled, "${b0.byLine}")
        val b1 = real.withAlighting(b0, listOf(l, e, a), 164.0)
        assertEquals("E_S", b1.bestKey); assertTrue(b1.confidence > 0.9)
        val lOld = l.copy(atOrigin = false)
        val old = inf.fromDeparture(listOf(lOld, e, a), 29.0, 0, "A_S")
        assertTrue(old.byLine["E_S"]!! < b0.byLine["E_S"]!!)
        assertTrue(old.byLine["L_S"]!! > 5 * b0.byLine["L_S"]!!)
        assertFalse(old.settled)
        assertTrue(PlatformTiming.recordedLag("E") > PlatformTiming.recordedLag("1"))
        assertEquals(1000 - PlatformTiming.recordedLag("1"), PlatformTiming.atPlatform(1000.0, "1"))
    }

    @Test
    fun stopTimingDuringTheRideTellsLocalFromExpress() {
        val local = candIdx("r1", "R_N", 1000.0, alightIdx = 4, stopTs = mapOf(1 to 1100.0, 2 to 1200.0, 3 to 1300.0, 4 to 1400.0), chosen = true)
        val express = candIdx("n1", "N_N", 1015.0, alightIdx = 4, stopTs = mapOf(2 to 1180.0, 4 to 1360.0))
        val c = listOf(local, express)
        val start = inf.fromDeparture(c, 1040.0, 0, "R_N")
        assertEquals(LineBelief.Verdict.unsure, start.verdict, "${start.byLine}")
        val onLocal = inf.withRide(start, c, listOf(1110.0, 1210.0), 1250.0)
        assertEquals("R_N", onLocal.bestKey); assertTrue(onLocal.settled, "${onLocal.byLine}"); assertEquals(listOf("departure", "ride"), onLocal.evidence)
        val onExpress = inf.withRide(start, c, listOf(1190.0), 1250.0)
        assertEquals("N_N", onExpress.bestKey); assertEquals(LineBelief.Verdict.switched, onExpress.verdict)
        assertEquals(start, inf.withRide(start, c, emptyList(), 1250.0))
        val blind = listOf(candIdx("r1", "R_N", 1000.0, chosen = true), candIdx("n1", "N_N", 1000.0))
        assertEquals(start.byLine, inf.withRide(start, blind, listOf(1110.0), 1250.0).byLine)
    }

    @Test
    fun aFixAfterWalkingOffPicksTheStationItIsAt() {
        val c = listOf(candIdx("a1", "A_S", 1000.0, alightIdx = 3, stopTs = mapOf(3 to 1500.0), chosen = true), candIdx("c1", "C_S", 1010.0, alightIdx = 5, stopTs = mapOf(5 to 1700.0)))
        val start = inf.fromDeparture(c, 1040.0, 0, "A_S")
        val near = inf.withLocation(start, c, mapOf("A_S|a1" to 900.0, "C_S|c1" to 40.0))
        assertEquals("C_S", near.bestKey); assertEquals(LineBelief.Verdict.switched, near.verdict); assertEquals(listOf("departure", "location"), near.evidence)
        val partial = inf.withLocation(start, c, mapOf("C_S|c1" to 40.0))
        assertEquals(start.byLine["C_S"]!!, partial.byLine["C_S"]!!, 0.02)
        assertEquals(start, inf.withLocation(start, c, emptyMap()))
    }

    @Test
    fun theLogFollowsATrainAfterItLeavesAndRefreshesCandidates() {
        val log = DepartureLog()
        log.observe(listOf(train("t1", "F_N", listOf(2 to 1000.0, 3 to 1100.0, 4 to 1200.0), at = 2)), "F_N", 2, Span(2, 4), true, 1005.0)
        var cands = log.candidates(1030.0, 300.0, null)
        assertEquals(1, cands.size); assertEquals(1005.0, cands[0].stoppedAtBoardTs); assertEquals(2, cands[0].boardIdx); assertEquals(4, cands[0].alightIdx)
        assertEquals(mapOf(3 to 1100.0, 4 to 1200.0), cands[0].stopTs)
        log.observe(listOf(train("t1", "F_N", listOf(3 to 1120.0, 4 to 1230.0))), "F_N", 2, Span(2, 4), true, 1065.0)
        cands = log.refreshed(cands)
        assertEquals(1000.0, cands[0].boardTs); assertEquals(mapOf(3 to 1120.0, 4 to 1230.0), cands[0].stopTs); assertEquals(3, cands[0].progressIdx); assertEquals(3, log.progressIdx("F_N|t1"))
        log.observe(listOf(train("t1", "F_N", listOf(4 to 1240.0))), "F_N", 2, Span(2, 4), true, 1150.0)
        assertEquals(mapOf(3 to 1120.0, 4 to 1240.0), log.refreshed(cands)[0].stopTs)
        log.observe(listOf(train("t2", "F_N", listOf(5 to 1300.0))), "F_N", 2, Span(2, 4), true, 1150.0)
        assertEquals(1, log.candidates(1030.0, 300.0, null).size)
    }

    @Test
    fun aTrainAtItsFirstStopIsAWeakerMatchThanOneTheFeedTrackedIn() {
        val e = candIdx("e1", "E_S", 1000.0, boardIdx = 5, chosen = true)
        val l = BoardingCandidate("L_S|l1", "L_S", "L", 1010.0, null, null, false, false, 0, samePlatform = false, atOrigin = true)
        val b = inf.fromDeparture(listOf(e, l), 1060.0, 0, "E_S")
        assertEquals("E_S", b.bestKey, "${b.byLine}"); assertTrue(b.settled, "${b.byLine}")
        val asTracked = inf.fromDeparture(listOf(e, l.copy(atOrigin = false)), 1060.0, 0, "E_S")
        assertTrue(asTracked.byLine["L_S"]!! > 2 * b.byLine["L_S"]!!)
        val log = DepartureLog()
        log.observe(listOf(train("l2", "L_S", listOf(0 to 1010.0, 1 to 1100.0))), "L_S", 0, null, false, 1000.0, samePlatform = false)
        log.observe(listOf(train("e2", "E_S", listOf(5 to 1000.0, 6 to 1150.0))), "E_S", 5, Span(5, 6), true, 1000.0)
        val c = log.candidates(1060.0, 300.0, null)
        assertEquals(true, c.first { it.key == "L_S" }.atOrigin); assertEquals(false, c.first { it.key == "E_S" }.atOrigin)
    }

    @Test
    fun aTrainOffThePlanIsJudgedOnItsOwnStopsAfterTheWalkOff() {
        val e = candIdx("e1", "E_S", 1000.0, boardIdx = 5, alightIdx = 6, stopTs = mapOf(6 to 1150.0), chosen = true)
        val l = BoardingCandidate("L_S|l1", "L_S", "L", 1005.0, null, null, false, false, 0, stopTs = mapOf(1 to 1080.0, 2 to 1210.0, 3 to 1330.0), samePlatform = false)
        val start = inf.fromDeparture(listOf(e, l), 1030.0, 0, "E_S")
        assertEquals(6, inf.alightingStop(e, 1160.0)); assertEquals(2, inf.alightingStop(l, 1160.0))
        assertEquals(1, inf.stopsMade(e, 1030.0, 1160.0)); assertEquals(1, inf.stopsMade(l, 1030.0, 1160.0))
        var b = inf.withStops(start, listOf(e, l), 1, 1030.0, 1160.0)
        b = inf.withAlighting(b, listOf(e, l), 1160.0)
        b = inf.withLocation(b, listOf(e, l), mapOf("E_S|e1" to 40.0, "L_S|l1" to 900.0))
        assertEquals("E_S", b.bestKey); assertTrue(b.settled, "${b.byLine}"); assertTrue(b.byLine["L_S"]!! < 0.1, "${b.byLine}")
        assertEquals(listOf("departure", "stops", "alighting", "location"), b.evidence)
        var old = inf.withStops(start, listOf(e, l), 1)
        old = inf.withAlighting(old, listOf(e, l), 1160.0)
        old = inf.withLocation(old, listOf(e, l), mapOf("E_S|e1" to 40.0))
        assertTrue(old.byLine["L_S"]!! > b.byLine["L_S"]!!)
        val far = inf.withStops(start, listOf(e, l), 2, 1030.0, 1630.0)
        assertEquals(3, inf.stopsMade(l, 1030.0, 1630.0))
        assertFalse(inf.withLocation(inf.withAlighting(far, listOf(e, l), 1630.0), listOf(e, l), mapOf("E_S|e1" to 3500.0, "L_S|l1" to 4000.0)).settled)
    }

    @Test
    fun aPullAwayNoTrainMadeIsToldFromOneThatHappened() {
        val log = DepartureLog()
        log.observe(listOf(train("r1", "R_N", listOf(4 to 125.0, 5 to 250.0))), "R_N", 4, Span(4, 8), true, -200.0)
        log.observe(listOf(train("f1", "F_N", listOf(4 to -150.0, 5 to -30.0))), "F_N", 4, null, false, -200.0)
        log.observe(listOf(train("f1", "F_N", listOf(5 to -30.0, 6 to 90.0))), "F_N", 4, null, false, 100.0)
        log.observe(listOf(train("r1", "R_N", listOf(4 to 125.0, 5 to 250.0))), "R_N", 4, Span(4, 8), true, 100.0)
        val felt = log.departureCheck(0.0)
        assertFalse(felt.left); assertTrue(felt.waiting)
        val l2 = DepartureLog()
        l2.observe(listOf(train("e1", "E_S", listOf(5 to 33.0, 6 to 190.0))), "E_S", 5, Span(5, 6), true, -20.0)
        l2.observe(listOf(train("e1", "E_S", listOf(6 to 190.0))), "E_S", 5, Span(5, 6), true, 90.0)
        l2.observe(listOf(train("a1", "A_S", listOf(5 to 180.0))), "A_S", 5, Span(5, 6), true, 90.0)
        assertTrue(l2.departureCheck(0.0).left); assertEquals(90.0, l2.lastPollTs)
    }

    @Test
    fun theLogTakesUpATrainItNeverSawAtThePlatform() {
        val log = DepartureLog()
        val c = BoardingCandidate("G_N|g1", "G_N", "G", 900.0, 1260.0, 3, false, true, 0, 3, null, mapOf(2 to 1140.0, 3 to 1260.0), 2)
        log.seed(c, 1000.0)
        assertEquals(listOf(c), log.refreshed(listOf(c)))
        log.observe(listOf(train("g1", "G_N", listOf(3 to 1275.0, 4 to 1400.0))), "G_N", 0, Span(0, 3), true, 1150.0)
        val r = log.refreshed(listOf(c))[0]
        assertEquals(1275.0, r.alightTs); assertEquals(3, r.progressIdx); assertEquals(1140.0, r.stopTs[2]); assertEquals(900.0, r.boardTs); assertEquals(3, log.progressIdx("G_N|g1"))
    }
}
