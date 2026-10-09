// Port of the logic in ios/WhichWay/WhichWay/Views/RouteHealth.swift: how a route is doing, as the expected
// extra time to the destination against the timetable, with the reasons. The colours are the app's.
package com.whichway.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

data class RouteHealth(val extraSec: Double, val slipSec: Double, val reasons: List<String>, val live: Boolean) {
    enum class Level { smooth, minor, heavy }
    val level: Level get() = if (extraSec < 300) Level.smooth else if (extraSec < 600) Level.minor else Level.heavy
    val minutes: Int get() = (extraSec / 60).roundToInt()
    val label: String get() = if (extraSec < 90) (if (live) "on time" else "as scheduled") else "+$minutes min"
    val summary: String get() = reasons.take(2).joinToString(" · ")
}

data class AlertEvidence(val corroborated: Boolean, val text: String)

/** What the health needs from the live data. */
class HealthContext(
    val now: Double,
    val boards: Map<String, LineBoard>,
    val predictions: Map<String, Map<String, LinePrediction>>,
    val scenario: String,
    val alerts: List<RouteAlert>,
) {
    fun prediction(key: String): LinePrediction? = predictions[key]?.let { it[scenario] ?: it["baseline"] }
    fun alertsFor(routes: List<String>) = alerts.filter { a -> a.routes.any { it in routes } }
}

/** Whether an alert shows in the live data on its routes right now. */
fun alertEvidence(a: RouteAlert, ctx: HealthContext): AlertEvidence {
    val routes = a.routes.joinToString("/")
    val boards = ctx.boards.values.filter { it.route in a.routes }
    if (boards.isEmpty()) return AlertEvidence(false, "no $routes feed loaded yet")
    val lates = ArrayList<Double>(); var held = 0; var knock = 0
    for (b in boards) {
        lates.addAll(b.trains.mapNotNull { it.effectiveLatenessSec })
        held += b.nHolding + b.nStalled
        ctx.prediction(b.key)?.let { knock += it.nKnockOn }
    }
    if (lates.isEmpty()) return AlertEvidence(false, "no $routes train in the feed right now")
    val median = lates.sorted()[lates.size / 2]
    val nLate = lates.count { it >= 180 }
    val bits = ArrayList<String>()
    if (median >= 180) bits.add("trains ${Fmt.late(median)} on average") else if (nLate >= 2) bits.add("$nLate trains 3+ min late")
    if (held > 0) bits.add("$held held or overdue")
    if (knock > 0) bits.add("$knock held back by the train ahead")
    if (bits.isEmpty()) return AlertEvidence(false, "not seen in the feeds right now: trains ${Fmt.late(median)}")
    return AlertEvidence(true, "seen in the feeds: " + bits.joinToString(", "))
}

fun routeHealth(option: PathOption, ctx: HealthContext): RouteHealth {
    var extra = 0.0; var slip = 0.0
    val reasons = ArrayList<Pair<Int, String>>()
    fun add(weight: Int, why: String) { reasons.add(Pair(weight, why)) }
    val it = option.live
    if (it != null) {
        val ride = it.legs.sumOf { it.rideVsSchedSec ?: 0.0 }
        if (ride >= 60) { extra += ride; add(3, "ride ${Fmt.minTxt(ride)} slower than scheduled") }
        else if (ride <= -60) add(0, "ride ${Fmt.minTxt(-ride)} faster than scheduled")
        val headway = max(120.0, 2 * option.wait1Sec)
        val waitExtra = (it.boardTs - ctx.now) - headway
        if (waitExtra >= 60) { extra += waitExtra; add(3, "${Fmt.minTxt(waitExtra)} longer wait than the usual headway") }
        val m = it.connectionMarginSec
        if (it.legs.size > 1 && m != null) {
            val transferExtra = m - 2 * option.wait2Sec
            if (transferExtra >= 60) { extra += transferExtra; add(2, "${Fmt.minTxt(transferExtra)} extra at the change") }
            val n = it.nextIfMissedSec
            if (m < 60 && n != null) { extra += n / 2; add(2, "tight change, ${Fmt.mmss(m)} margin: +${Fmt.minTxt(n)} if missed") }
        }
        it.legs.lastOrNull()?.arriveHiTs?.let { hi -> slip = max(0.0, hi - it.arriveTs); if (slip >= 120) add(1, "could slip to ${Fmt.hhmm(hi)}") }
        val l0 = it.legs[0].train
        l0.position?.let { p -> if (p.holding) add(2, "your train is held at ${p.stopName}") }
        if (l0.position?.stalled == true) add(2, "your train is overdue between stops")
        if (l0.corroboration == "feed_optimistic") add(1, "the feed looks optimistic for your train")
    } else {
        val model = max(0.0, option.typicalSec) + option.holdRiskSec
        if (model >= 60) { extra += model; add(2, "typically ${Fmt.minTxt(model)} lost at this hour") }
        add(0, "no train for this path in the feeds yet")
    }
    val seenAlerts = HashSet<String>(); val seenKeys = HashSet<String>()
    for (leg in option.legs) {
        for (a in ctx.alertsFor(leg.routes)) {
            if (!seenAlerts.add(a.id)) continue
            val ev = alertEvidence(a, ctx)
            val routes = a.routes.joinToString("/")
            if (a.kind == "delay") add(if (ev.corroborated) 2 else 0, "delay alert on the $routes")
            else if (a.kind == "planned") add(if (ev.corroborated) 1 else 0, "planned work on the $routes")
        }
        for (k in leg.keys) {
            if (!seenKeys.add(k)) continue
            ctx.boards[k]?.let { b -> val n = b.nHolding + b.nStalled; if (n > 0) add(1, "$n ${b.route} train${if (n == 1) "" else "s"} held or overdue") }
            ctx.prediction(k)?.let { lp -> if (lp.nKnockOn > 0) add(1, "${lp.nKnockOn} held back by the train ahead") }
        }
    }
    val weighted = reasons.filter { it.first > 0 }
    val shown = ArrayList<String>()
    for (r in (if (weighted.isEmpty()) reasons else weighted).sortedByDescending { it.first }.map { it.second }) if (r !in shown) shown.add(r)
    return RouteHealth(extra, slip, shown, option.live != null)
}

/** "1F 1248": the NYCT train id's first two words, else the trip id's stem. */
fun shortLabel(t: LiveTrain): String {
    val id = t.trainId
    if (id != null) { val parts = id.split(" ").filter { it.isNotEmpty() }; return if (parts.size >= 2) parts[0] + " " + parts[1] else id }
    return tripSuffix(t.tripId).take(10)
}
