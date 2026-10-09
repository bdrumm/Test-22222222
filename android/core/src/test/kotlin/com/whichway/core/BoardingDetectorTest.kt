package com.whichway.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class BoardingDetectorTest {
    private val d = BoardingDetector()
    private val events = ArrayList<MotionEvent>()
    private var t = 1000.0
    private fun run(n: Int, step: Double, push: Double, shake: Double) { repeat(n) { d.feed(MotionSecond(t, step, push, shake))?.let { events.add(it) }; t += 1 } }

    @Test
    fun departureThenAlighting() {
        run(30, 0.05, 0.01, 0.01)
        assertEquals(MotionState.walking, d.state)
        run(60, 0.002, 0.005, 0.005)
        assertEquals(MotionState.still, d.state)
        assertTrue(events.isEmpty())
        run(6, 0.002, 0.08, 0.03)
        assertEquals(listOf(MotionEvent(MotionEvent.Kind.departed, 1090.0)), events)
        assertEquals(MotionState.riding, d.state)
        run(90, 0.002, 0.01, 0.04); run(30, 0.002, 0.0, 0.0)
        assertEquals(MotionState.riding, d.state)
        run(60, 0.002, 0.02, 0.04); run(3, 0.05, 0.01, 0.01); run(5, 0.002, 0.0, 0.04)
        assertEquals(1, events.size, "steps inside the car do not end the ride")
        run(12, 0.05, 0.01, 0.01); run(10, 0.002, 0.0, 0.04)
        assertEquals(1, events.size, "walking inside a moving car does not end the ride either")
        run(8, 0.002, 0.0, 0.005)
        val off = t
        run(20, 0.05, 0.01, 0.0)
        assertEquals(2, events.size)
        assertEquals(MotionEvent(MotionEvent.Kind.alighted, off), events[1])
        assertEquals(MotionState.walking, d.state)
    }

    @Test
    fun aLongWalkEndsTheRideEvenWhenTheStopWentUnfelt() {
        run(10, 0.002, 0.005, 0.005); run(6, 0.002, 0.08, 0.03); run(120, 0.002, 0.01, 0.04)
        assertEquals(1, events.size)
        run(29, 0.05, 0.01, 0.0)
        assertEquals(1, events.size)
        run(1, 0.05, 0.01, 0.0)
        assertEquals(2, events.size)
        assertEquals(MotionEvent.Kind.alighted, events[1].kind)
    }

    @Test
    fun passingTrainOnThePlatformIsNotARideAndWalkingNeverStartsOne() {
        run(20, 0.002, 0.005, 0.005); run(5, 0.002, 0.01, 0.03); run(20, 0.002, 0.005, 0.005)
        assertTrue(events.isEmpty()); assertEquals(MotionState.still, d.state)
        val w = BoardingDetector(); var any = false
        repeat(30) { if (w.feed(MotionSecond(2000.0 + it, 0.05, 0.09, 0.05)) != null) any = true }
        assertTrue(!any); assertEquals(MotionState.walking, w.state)
    }

    @Test
    fun sustainedVibrationAloneStartsARide() {
        run(10, 0.002, 0.005, 0.005); run(12, 0.002, 0.01, 0.03)
        assertEquals(listOf(MotionEvent(MotionEvent.Kind.departed, 1010.0)), events)
    }

    @Test
    fun threeQuietSecondsBeforeTheStepsAreEnoughToStepOff() {
        run(30, 0.002, 0.005, 0.005); run(6, 0.002, 0.08, 0.03); run(120, 0.002, 0.01, 0.04); run(3, 0.002, 0.01, 0.01)
        val off = t
        run(10, 0.05, 0.03, 0.03)
        assertEquals(listOf(MotionEvent(MotionEvent.Kind.departed, 1030.0), MotionEvent(MotionEvent.Kind.alighted, off)), events)
        assertEquals(MotionState.walking, d.state)
    }
}
