package com.whichway.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull

/**
 * The Kotlin predictor must agree with the Python reference (mta_delay_insights/realtime/client_model.py) on the
 * same fixture the iOS package tests against (ios/WhichWayCore/Tests/make_fixtures.py writes it).
 */
class PredictorTest {
    @Serializable
    data class Input(
        val trains: List<PredictorTrain>,
        val line: PredictorLine,
        val model: ClientModel,
        val now: Double,
        val scenarios: List<String>,
        val elapsed: List<Double>,
        val horizons: List<Double>,
    )

    private fun load(): Pair<Input, JsonObject> {
        val path = System.getProperty("whichway.fixture") ?: error("whichway.fixture not set (run through Gradle)")
        val top = WWJson.parseToJsonElement(File(path).readText()).jsonObject
        return Pair(WWJson.decodeFromJsonElement(Input.serializer(), top["input"]!!), top)
    }

    private fun num(e: JsonElement?): Double? = if (e == null || e is JsonNull) null else e.jsonPrimitive.doubleOrNull
    private fun int(e: JsonElement?): Int? = if (e == null || e is JsonNull) null else e.jsonPrimitive.intOrNull
    private fun close(expected: Double?, actual: Double, eps: Double, msg: String) {
        assertNotNull(expected, msg)
        assertEquals(expected, actual, eps, msg)
    }

    @Test
    fun predictLineMatchesPython() {
        val (inp, top) = load()
        val expected = top["expected"]!!.jsonObject
        assertEquals(5, inp.trains.size)
        for (sc in inp.scenarios) {
            val py = expected[sc]!!.jsonObject
            val out = Predictor.predictLine(inp.trains, inp.line, inp.model, inp.now, sc)
            val pyTrains = py["trains"]!!.jsonArray.map { it.jsonObject }
            assertEquals(pyTrains.map { it["trip_id"]!!.jsonPrimitive.content }, out.trains.map { it.tripId }, "$sc: order")
            for ((t, pt) in out.trains.zip(pyTrains)) {
                close(num(pt["hold_extra_sec"]), t.holdExtraSec, 1e-6, "$sc ${t.tripId} hold")
                close(num(pt["knock_on_sec"]), t.knockOnSec, 1e-6, "$sc ${t.tripId} knock")
                val pyPts = pt["points"]!!.jsonArray.map { it.jsonObject }
                assertEquals(pyPts.size, t.points.size, "$sc ${t.tripId} points")
                for ((p, pp) in t.points.zip(pyPts)) {
                    assertEquals(int(pp["idx"]), p.idx)
                    assertEquals(pp["source"]!!.jsonPrimitive.content, p.source)
                    close(num(pp["eta_ts"]), p.etaTs, 1e-6, "$sc ${t.tripId} eta ${p.idx}")
                    close(num(pp["lo_ts"]), p.loTs, 1e-6, "$sc ${t.tripId} lo ${p.idx}")
                    close(num(pp["hi_ts"]), p.hiTs, 1e-6, "$sc ${t.tripId} hi ${p.idx}")
                }
            }
            assertEquals(int(py["n_knock_on"]), out.nKnockOn, sc)
            close(num(py["knock_on_total_sec"]), out.knockOnTotalSec, 1e-6, sc)
            val pw = py["worst_gap"] as? JsonObject
            assertEquals(pw?.let { int(it["idx"]) }, out.worstGap?.idx, "$sc: worst gap stop")
            if (pw != null) close(num(pw["gap_sec"]), out.worstGap!!.gapSec, 1e-6, "$sc: worst gap")
            assertEquals(py["per_stop"]!!.jsonArray.map { int(it.jsonObject["n_arrivals"]) }, out.perStop.map { it.nArrivals }, sc)
        }
    }

    @Test
    fun remainingHoldAndCalibrationMatchPython() {
        val (inp, top) = load()
        for ((e, r) in inp.elapsed.zip(top["remaining"]!!.jsonArray.map { it.jsonObject })) {
            val mine = Predictor.remainingHold(inp.model.holdSurvival, e)
            close(num(r["expected"]), mine.expected, 1e-6, "expected $e")
            close(num(r["p50"]), mine.p50, 1e-6, "p50 $e")
            close(num(r["p90"]), mine.p90, 1e-6, "p90 $e")
            close(num(r["clears_2min"]), mine.clears2min, 1e-6, "clears $e")
        }
        for ((h, c) in inp.horizons.zip(top["calibration"]!!.jsonArray.map { it.jsonObject })) {
            val mine = Predictor.calibrationAt(inp.model, "6", h)
            close(num(c["bias"]), mine.bias, 1e-9, "bias $h")
            close(num(c["p10"]), mine.p10, 1e-9, "p10 $h")
            close(num(c["p90"]), mine.p90, 1e-9, "p90 $h")
            assertEquals(int(c["n"]), mine.n)
        }
        // no tables at all: physical priors
        assertEquals(Predictor.PRIOR_HOLD.expected, Predictor.remainingHold(null, 500.0).expected)
        assertEquals(0.0, Predictor.calibrationAt(null, "Q", 100.0).bias)
    }
}
