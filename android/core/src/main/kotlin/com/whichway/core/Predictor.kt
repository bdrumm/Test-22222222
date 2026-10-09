// Port of ios/WhichWay/WhichWay/Core/Predictor.swift (itself a port of mta_delay_insights/realtime/client_model.py):
// the feed's ETA error by route and horizon, the remaining hold given the time already held, and how lateness
// carries k stops ahead. Tested against the iOS package's fixture from the Python reference.
package com.whichway.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.ZoneId
import kotlin.math.max
import kotlin.math.pow
import kotlin.math.sqrt

@Serializable
data class CalBucket(val n: Int = 0, val bias: Double = 0.0, val p10: Double = 0.0, val p90: Double = 0.0)

@Serializable
data class EtaCalibration(
    val horizons: List<Double> = Predictor.HORIZON_EDGES,
    val n: Int = 0,
    val all: List<CalBucket> = emptyList(),
    @SerialName("by_route") val byRoute: Map<String, List<CalBucket>> = emptyMap(),
)

@Serializable
data class HoldSurvival(
    val elapsed: List<Double> = emptyList(),
    val n: List<Int> = emptyList(),
    val expected: List<Double> = emptyList(),
    val p50: List<Double> = emptyList(),
    val p90: List<Double> = emptyList(),
    @SerialName("clears_2min") val clears2min: List<Double> = emptyList(),
    @SerialName("n_holds") val nHolds: Int = 0,
)

@Serializable
data class CarryTable(
    val slope: List<Double> = emptyList(),
    val intercept: List<Double> = emptyList(),
    @SerialName("resid_std") val residStd: List<Double> = emptyList(),
    val n: List<Int> = emptyList(),
)

@Serializable
data class LatenessCarry(
    @SerialName("max_k") val maxK: Int = 12,
    val all: CarryTable? = null,
    @SerialName("by_route") val byRoute: Map<String, CarryTable> = emptyMap(),
    @SerialName("by_band") val byBand: Map<String, CarryTable> = emptyMap(),
    @SerialName("by_route_band") val byRouteBand: Map<String, Map<String, CarryTable>> = emptyMap(),
    val n: Int = 0,
)

/** data/client_model.json */
@Serializable
data class ClientModel(
    val version: Int = 0,
    @SerialName("generated_at") val generatedAt: String? = null,
    @SerialName("eta_calibration") val etaCalibration: EtaCalibration? = null,
    @SerialName("hold_survival") val holdSurvival: HoldSurvival? = null,
    @SerialName("lateness_carry") val latenessCarry: LatenessCarry? = null,
) {
    val summary: String
        get() = "${etaCalibration?.n ?: 0} ETA samples · ${etaCalibration?.byRoute?.size ?: 0} lines calibrated · ${holdSurvival?.nHolds ?: 0} holds"
}

data class RemainingHold(val expected: Double, val p50: Double, val p90: Double, val clears2min: Double)

// predictor input (language-neutral, matches the fixture JSON)

@Serializable
data class PredictorPosition(
    val status: String? = null,
    @SerialName("since_sec") val sinceSec: Double = 0.0,
    val holding: Boolean = false,
    val stalled: Boolean = false,
)

@Serializable
data class PredictorTrain(
    @SerialName("trip_id") val tripId: String = "",
    val route: String = "",
    @SerialName("next_idx") val nextIdx: Int? = null,
    @SerialName("points") private val pointsRaw: List<List<Double?>> = emptyList(),
    @SerialName("lateness_sec") val latenessSec: Double? = null,
    @SerialName("effective_lateness_sec") val effectiveLatenessSec: Double? = null,
    @SerialName("sched_ts") val schedTs: Double? = null,
    val position: PredictorPosition? = null,
) {
    /** [idx, ts] pairs with both present. */
    val points: List<Pair<Int, Double>>
        get() = pointsRaw.mapNotNull { p -> if (p.size >= 2 && p[0] != null && p[1] != null) Pair(p[0]!!.toInt(), p[1]!!) else null }

    companion object {
        fun of(tripId: String, route: String, nextIdx: Int?, points: List<Pair<Int, Double>>, latenessSec: Double?, effectiveLatenessSec: Double?,
               schedTs: Double?, position: PredictorPosition?) =
            PredictorTrain(tripId, route, nextIdx, points.map { listOf(it.first.toDouble(), it.second) }, latenessSec, effectiveLatenessSec, schedTs, position)
    }
}

@Serializable
data class PredictorLine(val stops: List<String> = emptyList(), @SerialName("run_sec") val runSec: List<Double?> = emptyList()) {
    companion object { fun of(line: LineTopology) = PredictorLine(line.stops, line.runSec.map { it?.toDouble() }) }
}

// output

data class PredictedPoint(val idx: Int, val feedTs: Double, var etaTs: Double, var loTs: Double, var hiTs: Double, val source: String)

data class PredictedTrain(val tripId: String, var points: List<PredictedPoint>, var holdExtraSec: Double, var knockOnSec: Double, val scenario: String) {
    fun point(idx: Int): PredictedPoint? = points.firstOrNull { it.idx == idx }
}

data class StopPrediction(val idx: Int, val nArrivals: Int, val nextTs: Double?, val maxHeadwaySec: Double?)
data class WorstGap(val gapSec: Double, val idx: Int, val atTs: Double)

data class LinePrediction(
    val scenario: String,
    val now: Double,
    val trains: List<PredictedTrain>,
    val perStop: List<StopPrediction>,
    val worstGap: WorstGap?,
    val nKnockOn: Int,
    val knockOnTotalSec: Double,
) {
    fun train(tripId: String): PredictedTrain? = trains.firstOrNull { it.tripId == tripId }
}

object Predictor {
    val HORIZON_EDGES = listOf(0.0, 120.0, 300.0, 600.0, 1200.0, 2400.0, 3600.0)
    val SCENARIOS = listOf("baseline", "hold_persists", "clears_now")
    const val MIN_STOP_GAP_SEC = 30.0
    const val MIN_HEADWAY_SEC = 90.0
    val PRIOR_HOLD = RemainingHold(300.0, 180.0, 720.0, 0.35)
    val BAND_NAMES = listOf("night", "am_peak", "midday", "pm_peak", "evening", "weekend_day", "weekend_night")
    private val NY: ZoneId = ZoneId.of("America/New_York")

    fun priorSpread(h: Double) = Pair(-45.0 - 0.05 * h, 60.0 + 0.15 * h)

    fun horizonIndex(h: Double): Int {
        for (i in 0 until HORIZON_EDGES.size - 1) if (HORIZON_EDGES[i] <= h && h < HORIZON_EDGES[i + 1]) return i
        return if (h < 0) 0 else HORIZON_EDGES.size - 2
    }

    fun calibrationAt(model: ClientModel?, route: String, horizon: Double): CalBucket {
        val i = horizonIndex(horizon)
        val cal = model?.etaCalibration
        val table = cal?.byRoute?.get(route) ?: cal?.all
        if (table != null && i < table.size) return table[i]
        val (p10, p90) = priorSpread(max(0.0, horizon))
        return CalBucket(0, 0.0, p10, p90)
    }

    /** Time band index for a New York local hour and weekday (Monday = 0); mirrors client_model.band_of_hour. */
    fun bandOfHour(hour: Int, weekday: Int): Int {
        if (weekday >= 5) return if (hour in 7..21) 5 else 6
        if (hour < 6) return 0
        if (hour <= 9) return 1
        if (hour <= 15) return 2
        if (hour <= 19) return 3
        return 4
    }

    fun bandAt(ts: Double): String {
        val z = Instant.ofEpochMilli((ts * 1000).toLong()).atZone(NY)
        return BAND_NAMES[bandOfHour(z.hour, z.dayOfWeek.value - 1)]
    }

    data class Carry(val slope: Double, val intercept: Double, val residStd: Double)

    /** Lateness-carry coefficients for k stops ahead: route × band, else route, else band, else all routes. */
    fun carryAt(model: ClientModel?, route: String, k: Int, band: String? = null): Carry? {
        val lc = model?.latenessCarry
        var t: CarryTable? = null
        if (band != null) t = lc?.byRouteBand?.get(route)?.get(band)
        if (t == null) t = lc?.byRoute?.get(route)
        if (t == null && band != null) t = lc?.byBand?.get(band)
        if (t == null) t = lc?.all
        if (t == null || k < 1 || k > t.slope.size || k > t.intercept.size || k > t.residStd.size) return null
        return Carry(t.slope[k - 1], t.intercept[k - 1], t.residStd[k - 1])
    }

    fun remainingHold(sv: HoldSurvival?, elapsed: Double): RemainingHold {
        if (sv == null || sv.elapsed.isEmpty()) return PRIOR_HOLD
        val g = sv.elapsed
        if (sv.expected.size != g.size || sv.p50.size != g.size || sv.p90.size != g.size || sv.clears2min.size != g.size) return PRIOR_HOLD
        fun pick(i: Int) = RemainingHold(sv.expected[i], sv.p50[i], sv.p90[i], sv.clears2min[i])
        if (elapsed <= g[0]) return pick(0)
        if (elapsed >= g[g.size - 1]) return pick(g.size - 1)
        for (i in 0 until g.size - 1) {
            if (g[i] <= elapsed && elapsed < g[i + 1]) {
                val f = (elapsed - g[i]) / (g[i + 1] - g[i])
                fun lerp(a: List<Double>) = a[i] + f * (a[i + 1] - a[i])
                return RemainingHold(lerp(sv.expected), lerp(sv.p50), lerp(sv.p90), lerp(sv.clears2min))
            }
        }
        return pick(g.size - 1)
    }

    /** Unconstrained projection of one train over its remaining stops. */
    fun predictTrain(train: PredictorTrain, line: PredictorLine, model: ClientModel?, now: Double, scenario: String = "baseline"): PredictedTrain {
        val out = PredictedTrain(train.tripId, emptyList(), 0.0, 0.0, scenario)
        val pts = train.points.sortedWith(compareBy<Pair<Int, Double>> { it.first }.thenBy { it.second })
        val first = pts.firstOrNull() ?: return out
        val nextIdx = train.nextIdx ?: first.first
        val lat = train.latenessSec
        val eff = train.effectiveLatenessSec ?: lat
        val optimistic = if (eff != null && lat != null) max(0.0, eff - lat) else 0.0
        val held = train.position?.holding == true || train.position?.stalled == true
        var extra = 0.0
        if (held) {
            val rem = remainingHold(model?.holdSurvival, train.position.sinceSec)
            extra = when (scenario) { "hold_persists" -> rem.p90; "clears_now" -> 0.0; else -> rem.expected }
        }
        out.holdExtraSec = extra
        val run = line.runSec
        val band = bandAt(now)
        var prevT: Double? = null
        val points = ArrayList<PredictedPoint>()
        for ((idx, feed) in pts) {
            val h = feed - now
            val cal = calibrationAt(model, train.route, h)
            val etaF = feed + cal.bias + optimistic
            val varF = max(((cal.p90 - cal.p10) / 2.56).pow(2), 1.0)
            var eta = etaF
            var lo = feed + cal.p10 + optimistic
            var hi = feed + cal.p90 + optimistic
            var source = "feed"
            val k = idx - nextIdx
            val schedNext = train.schedTs
            if (schedNext != null && eff != null && k >= 1) {
                var runSum = 0.0
                var ok = true
                for (s in nextIdx until idx) {
                    val r = run.getOrNull(s)
                    if (r != null) runSum += r else { ok = false; break }
                }
                val carry = if (ok) carryAt(model, train.route, k, band) else null
                if (carry != null) {
                    val etaS = schedNext + runSum + carry.intercept + carry.slope * eff
                    val varS = max(carry.residStd * carry.residStd, 1.0)
                    val w = varF / (varF + varS)
                    eta = w * etaS + (1 - w) * etaF
                    val sd = sqrt(1.0 / (1.0 / varF + 1.0 / varS))
                    lo = eta - 1.28 * sd
                    hi = eta + 1.28 * sd
                    source = "blend"
                }
            }
            eta += extra; lo += extra; hi += extra
            var t = max(eta, now)
            val p = prevT
            if (p != null && t < p + MIN_STOP_GAP_SEC) t = p + MIN_STOP_GAP_SEC
            val shift = t - eta
            points.add(PredictedPoint(idx, feed, t, lo + shift, hi + shift, source))
            prevT = t
        }
        out.points = points
        return out
    }

    /** All trains of one line direction, furthest along first, with the headway cascade applied. */
    fun predictLine(trains: List<PredictorTrain>, line: PredictorLine, model: ClientModel?, now: Double, scenario: String = "baseline",
                    minHeadwaySec: Double = MIN_HEADWAY_SEC, horizonSec: Double = 3600.0): LinePrediction {
        fun firstTs(t: PredictorTrain) = t.points.minOfOrNull { it.second } ?: now
        val order = trains.withIndex().sortedWith(
            compareBy<IndexedValue<PredictorTrain>> { -(it.value.nextIdx ?: 0) }.thenBy { firstTs(it.value) }.thenBy { it.index }
        ).map { it.value }
        val projs = ArrayList<PredictedTrain>()
        val lastAt = HashMap<Int, Double>()
        for (t in order) {
            val p = predictTrain(t, line, model, now, scenario)
            var shift = 0.0
            var knock = 0.0
            for (pt in p.points) {
                var want = pt.etaTs + shift
                val ahead = lastAt[pt.idx]
                if (ahead != null && want < ahead + minHeadwaySec) {
                    val delta = ahead + minHeadwaySec - want
                    shift += delta; knock += delta
                    want = ahead + minHeadwaySec
                }
                pt.etaTs = want
                pt.loTs += shift
                pt.hiTs += shift
                lastAt[pt.idx] = want
            }
            p.knockOnSec = knock
            p.points = p.points.filter { it.etaTs <= now + horizonSec + 900 }
            projs.add(p)
        }
        val perStop = ArrayList<StopPrediction>()
        var worst: WorstGap? = null
        for (i in line.stops.indices) {
            val arr = projs.flatMap { p -> p.points.filter { it.idx == i && it.etaTs <= now + horizonSec }.map { it.etaTs } }.sorted()
            val hws = (1 until arr.size).map { arr[it] - arr[it - 1] }
            val gap = hws.maxOrNull()
            if (gap != null && gap > 0 && (worst == null || gap > worst.gapSec)) {
                val j = hws.indexOf(gap)
                worst = WorstGap(gap, i, arr[j + 1])
            }
            perStop.add(StopPrediction(i, arr.size, arr.firstOrNull(), gap))
        }
        return LinePrediction(scenario, now, projs, perStop, worst, projs.count { it.knockOnSec >= 60 }, projs.sumOf { it.knockOnSec })
    }

    /** A board's trains in the predictor's input form. */
    fun input(board: LineBoard): List<PredictorTrain> = board.trains.map { t ->
        PredictorTrain.of(t.tripId, t.route, t.nextIdx, t.points.map { Pair(it.idx, it.ts) }, t.latenessSec, t.effectiveLatenessSec, t.schedTs,
            t.position?.let { PredictorPosition(it.status, it.sinceSec, it.holding, it.stalled) })
    }

    /** Predictions for a board: baseline always, the hold scenarios when a train is held. */
    fun predictBoard(board: LineBoard, line: LineTopology, model: ClientModel?, now: Double): Map<String, LinePrediction> {
        val inp = input(board)
        val pl = PredictorLine.of(line)
        val out = linkedMapOf("baseline" to predictLine(inp, pl, model, now, "baseline"))
        if (inp.any { it.position?.holding == true || it.position?.stalled == true }) {
            out["hold_persists"] = predictLine(inp, pl, model, now, "hold_persists")
            out["clears_now"] = predictLine(inp, pl, model, now, "clears_now")
        }
        return out
    }
}
