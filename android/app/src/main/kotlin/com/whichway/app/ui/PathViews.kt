// The selected path's five views (PathViews.swift's PathViewsView, the LegDiagram and insights from PlannerView.swift,
// HoursView), the hold outlook and the scenario picker, the health badges and the route insights sheet.
package com.whichway.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.whichway.app.store.AppData
import com.whichway.app.store.AppState
import com.whichway.core.ClientSchedule
import com.whichway.core.Fmt
import com.whichway.core.HealthContext
import com.whichway.core.LineHealth
import com.whichway.core.PathLeg
import com.whichway.core.PathOption
import com.whichway.core.RouteHealth
import com.whichway.core.alertEvidence
import com.whichway.core.pathTrips
import com.whichway.core.routeHealth
import com.whichway.core.shortLabel
import com.whichway.core.trainProgress
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

fun healthContext(s: AppState, scenario: String) = HealthContext(s.now, s.boards, s.predictions, scenario, s.alerts)

fun RouteHealth.color(): Color = when (level) { RouteHealth.Level.smooth -> Color(0xFF9E9E9E); RouteHealth.Level.minor -> Color(0xFFFFC107); RouteHealth.Level.heavy -> Color(0xFFD32F2F) }
@Composable fun RouteHealth.textColor(): Color {
    val dark = androidx.compose.foundation.isSystemInDarkTheme()
    return when (level) {
        RouteHealth.Level.smooth -> if (dark) Color(0xFFB3B3BA) else Color(0xFF66666E)
        RouteHealth.Level.minor -> if (dark) Color(0xFFFFD959) else Color(0xFF947000)
        RouteHealth.Level.heavy -> if (dark) Color(0xFFFF7373) else Color(0xFFB81414)
    }
}
fun LineHealth.color(): Color = when (level) { LineHealth.Level.good -> Color(0xFF2E7D32); LineHealth.Level.minor -> Color(0xFFFFC107); LineHealth.Level.delays -> Color(0xFFFF9800); LineHealth.Level.severe -> Color(0xFFD32F2F) }

/** The dot and its word, on a tinted capsule. */
@Composable
fun HealthDot(h: RouteHealth) {
    Surface(shape = CircleShape, color = h.color().copy(alpha = 0.16f)) {
        Row(Modifier.padding(horizontal = 7.dp, vertical = 3.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(Modifier.size(7.dp).then(Modifier).let { it }) { Surface(shape = CircleShape, color = h.color(), modifier = Modifier.size(7.dp)) {} }
            Text(h.label, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold, color = h.textColor())
        }
    }
}

@Composable
fun LineHealthRow(h: LineHealth, route: String) {
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(10.dp), color = MaterialTheme.colorScheme.surfaceVariant,
        border = if (h.level == LineHealth.Level.good) null else BorderStroke(1.dp, h.color().copy(alpha = 0.5f))) {
        Row(Modifier.padding(10.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Surface(shape = CircleShape, color = h.color(), modifier = Modifier.size(10.dp).padding(top = 0.dp)) {}
            Column { Text("$route line: ${h.label}", fontWeight = FontWeight.Bold, color = if (h.level == LineHealth.Level.good) MaterialTheme.colorScheme.onSurface else h.color()); Caption(h.reason) }
        }
    }
}

enum class TravelMode(val title: String) { track("Track"), timeline("Time"), board("Board"), map("Map"), hours("Hours") }

/** The selected path in one of five views; the choice is remembered. */
@Composable
fun PathViewsView(data: AppData, s: AppState, option: PathOption, schedule: ClientSchedule, originName: String, destName: String, now: Double) {
    var mode by rememberSaveable { mutableStateOf(TravelMode.track.name) }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            TravelMode.entries.forEachIndexed { i, m ->
                SegmentedButton(selected = mode == m.name, onClick = { mode = m.name }, shape = SegmentedButtonDefaults.itemShape(i, TravelMode.entries.size)) { Text(m.title) }
            }
        }
        when (TravelMode.valueOf(mode)) {
            TravelMode.track -> option.legs.forEachIndexed { i, leg -> LegDiagram(s, leg, i + 1, option, schedule, now) }
            TravelMode.timeline -> MareyChart(option, schedule, s.predictedBoards, now)
            TravelMode.board -> DepartureBoard(data, option, schedule, originName, destName, now)
            TravelMode.map -> { val geo = s.geometry; if (geo != null) RouteMap(option, schedule, geo, s.predictedBoards, now) else { LaunchedEffect(Unit) { data.requestGeometry() }; Caption("Loading the map…") } }
            TravelMode.hours -> HoursView(s, option, schedule)
        }
    }
}

@Composable
private fun LegDiagram(s: AppState, leg: PathLeg, legNo: Int, option: PathOption, schedule: ClientSchedule, now: Double) {
    val key = leg.primaryKey
    val line = schedule.lines[key] ?: return
    val ix = leg.idx[key] ?: return
    if (ix.from >= line.names.size || ix.to >= line.names.size) return
    val hour = Fmt.nyHour(now)
    val layers = ArrayList<DiagramLayer>()
    s.deviations[key]?.let { dev -> layers.add(DiagramLayer("typical", "typical +s at this hour", line.stops.map { sid -> dev.typical(sid, hour)?.let { max(0.0, it) } }, Color(0xFF1E88E5)) { "+${it.roundToInt()}" }) }
    s.holds?.takeIf { it.byStop.isNotEmpty() }?.let { h -> val m = h.byStop.associate { it.stopId to it.perDay }; layers.add(DiagramLayer("holds", "holds/day", line.stops.map { m[it] }, Color(0xFFFF9800)) { String.format(java.util.Locale.US, "%.1f", it) }) }
    val speeds = ArrayList<Double?>(); speeds.add(null)
    for (i in 1 until line.stops.size) {
        val d = line.distM.getOrNull(i - 1); val r = line.runSec.getOrNull(i - 1)
        speeds.add(if (d != null && r != null && r > 0) d.toDouble() / r * 3.6 else null)
    }
    layers.add(DiagramLayer("speed", "mph (scheduled)", speeds, Color(0xFF00897B)) { "${(it * 0.621371).roundToInt()}" })
    val trains = ArrayList<DiagramTrain>()
    for (k in leg.keys) {
        val b = s.boards[k] ?: continue; val bl = schedule.lines[k] ?: continue
        val age = now - b.now
        for (t in b.trains) {
            val p = trainProgress(t, age, bl)
            var idx = p.idx
            if (k != key) {
                val j = idx.toInt(); if (j < 0 || j >= bl.stops.size) continue
                val pj = line.stops.indexOf(bl.stops[j]); if (pj < 0) continue
                val pn = if (j + 1 < bl.stops.size) line.stops.indexOf(bl.stops[j + 1]) else -1
                idx = if (pn == pj + 1) pj + (idx - j) else pj.toDouble()
            }
            val tc = option.live?.legs?.getOrNull(legNo - 1)
            val emphasis = if (tc != null && tc.train.id == t.id) (if (legNo == 1) "origin" else "connection") else null
            var sub = ""
            t.effectiveLatenessSec?.let { if (abs(it) >= 60) sub = Fmt.late(it) }
            if (sub.isEmpty()) t.lastRun?.speedKmh?.let { sub = Fmt.mph(it) }
            trains.add(DiagramTrain(t.id, idx, p.state, t.route, shortLabel(t), sub, emphasis))
        }
    }
    // scroll so the train this leg boards is in view, while it is on its way
    val focus = option.live?.legs?.getOrNull(legNo - 1)?.let { tc ->
        val b = s.boards[tc.key] ?: return@let null; val t = b.trains.firstOrNull { it.id == tc.train.id } ?: return@let null; val bl = schedule.lines[tc.key] ?: return@let null
        var idx = trainProgress(t, now - b.now, bl).idx
        if (tc.key != key) { val j = idx.toInt(); val pj = if (j in bl.stops.indices) line.stops.indexOf(bl.stops[j]) else -1; if (pj < 0) return@let null; idx = pj.toDouble() }
        max(0, idx.toInt() - 1)
    }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Leg $legNo", style = MaterialTheme.typography.titleSmall)
            RouteBullets(leg.routes, 18.dp)
            Caption("${line.names[ix.from]} → ${line.names[ix.to]} · ${leg.nStops} stops · ${Fmt.minTxt(leg.schedRideSec?.toDouble())} scheduled")
        }
        TrackDiagram(line, leg.primaryRoute, ix.from, ix.to, layers, trains, focus ?: max(0, ix.from - 1))
    }
}

@Composable
private fun HoursView(s: AppState, option: PathOption, schedule: ClientSchedule) {
    val hour = Fmt.nyHour(s.now)
    fun profile(leg: PathLeg): List<Double?> {
        val dev = s.deviations[leg.primaryKey] ?: return List(24) { null }; val line = schedule.lines[leg.primaryKey] ?: return List(24) { null }; val ix = leg.idx[leg.primaryKey] ?: return List(24) { null }
        return (0 until 24).map { h -> var sum = 0.0; var any = false; for (st in ix.from + 1..ix.to) line.stops.getOrNull(st)?.let { sid -> dev.typical(sid, h)?.let { sum += max(0.0, it); any = true } }; if (any) sum else null }
    }
    val profiles = option.legs.map { profile(it) }
    val total = (0 until 24).map { h -> val vals = profiles.mapNotNull { it[h] }; if (vals.isEmpty()) null else vals.sum() }
    val maxV = max(30.0, total.filterNotNull().maxOrNull() ?: 30.0)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Time trains typically lose on this path, by hour", style = MaterialTheme.typography.titleSmall)
        option.legs.forEachIndexed { i, leg ->
            Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    RouteBullets(leg.routes, 16.dp); Caption("${leg.nStops} stops"); Spacer(Modifier.weight(1f))
                    Caption(profiles[i][hour]?.let { "now ${Fmt.signed(it)}" } ?: "no history yet")
                }
                HourStrip(profiles[i], maxV, hour, routeColor(leg.primaryRoute))
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { listOf("0", "6", "12", "18", "23").forEach { Caption(it) } }
        var best: Pair<Int, Double>? = null; var worst: Pair<Int, Double>? = null
        for (d in 0 until 6) { val h = (hour + d) % 24; val v = total[h] ?: continue; if (best == null || v < best.second) best = Pair(h, v); if (worst == null || v > worst.second) worst = Pair(h, v) }
        val b = best; val w = worst
        Caption(when {
            b == null || w == null -> "No hourly history for these stretches yet."
            w.second - b.second < 30 -> "The next six hours look alike on these stretches (${Fmt.signed(b.second)} to ${Fmt.signed(w.second)})."
            else -> "In the next six hours, leaving around ${String.format(java.util.Locale.US, "%02d:00", b.first)} loses the least (${Fmt.signed(b.second)}); around ${String.format(java.util.Locale.US, "%02d:00", w.first)} the most (${Fmt.signed(w.second)})."
        })
        Caption("Each cell: seconds trains lose across the stretch at that hour (from the per-line deviation grids); darker is worse, the outlined cell is now.")
    }
}

/** A held train ahead changes when the headline route arrives: the arrival under each assumption, one tap to plan for it. */
data class HoldOutlook(val heldAt: String, val usual: Double?, val dragsOn: Double?, val clearsNow: Double?)

fun holdOutlook(data: AppData, s: AppState, option: PathOption, schedule: ClientSchedule): HoldOutlook? {
    if (!s.predictions.values.any { it.containsKey("hold_persists") }) return null
    fun arrive(sc: String) = pathTrips(data.predictedBoardsFor(sc), schedule, option, s.now, 1).firstOrNull()?.arriveTs
    val u = arrive("baseline"); val d = arrive("hold_persists"); val c = arrive("clears_now")
    val vals = listOfNotNull(u, d, c)
    val lo = vals.minOrNull() ?: return null; val hi = vals.maxOrNull() ?: return null
    if (hi - lo < 60) return null
    var heldAt = "A train is held"
    for (k in option.legs.flatMap { it.keys }) { val b = s.boards[k] ?: continue; val t = b.trains.firstOrNull { it.isHeld } ?: continue; heldAt = "${b.route} held ${t.position?.text ?: ""}"; break }
    return HoldOutlook(heldAt, u, d, c)
}

@Composable
fun HoldOutlookCard(data: AppData, outlook: HoldOutlook) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Caption("${outlook.heldAt} · if the hold…")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(Triple("Ends as usual", "baseline", outlook.usual), Triple("Drags on", "hold_persists", outlook.dragsOn), Triple("Clears now", "clears_now", outlook.clearsNow)).forEach { (title, tag, arrive) ->
                val on = data.scenario == tag
                Surface(Modifier.weight(1f).clickable { data.setScenario(tag) }, shape = RoundedCornerShape(9.dp),
                    color = if (on) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f) else MaterialTheme.colorScheme.surfaceVariant,
                    border = BorderStroke(1.dp, if (on) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.08f))) {
                    Column(Modifier.padding(vertical = 7.dp), horizontalAlignment = Alignment.CenterHorizontally) { Text(title, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold); Caption(arrive?.let { Fmt.hhmm(it) } ?: "–") }
                }
            }
        }
    }
}

/** Shown when a train on the lines is held: what the engine assumes about the hold. */
@Composable
fun ScenarioPicker(data: AppData, s: AppState) {
    if (!s.predictions.values.any { it.containsKey("hold_persists") }) return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Caption("A train on these lines is held. Times assume the hold…")
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            listOf("baseline" to "ends as usual", "hold_persists" to "drags on (p90)", "clears_now" to "clears now").forEachIndexed { i, (tag, label) ->
                SegmentedButton(selected = data.scenario == tag, onClick = { data.setScenario(tag) }, shape = SegmentedButtonDefaults.itemShape(i, 3)) { Text(label, style = MaterialTheme.typography.labelSmall) }
            }
        }
    }
}

/** The lines the insights have to say about the path. */
fun insightLines(s: AppState, option: PathOption, schedule: ClientSchedule, scenario: String): List<String> {
    val out = ArrayList<String>()
    val hour = Fmt.nyHour(s.now)
    val ctx = healthContext(s, scenario)
    for (leg in option.legs) {
        leg.typicalSec?.let { t -> if (abs(t) >= 20) out.add("${leg.routesLabel} stretch typically ${if (t >= 0) "loses" else "gains"} ${abs(t).roundToInt()} s at this hour") }
        if (leg.holdRiskSec >= 10) out.add("${leg.holdRiskSec.roundToInt()} s expected hold risk on the ${leg.routesLabel}")
        val line = schedule.lines[leg.primaryKey]; val ix = leg.idx[leg.primaryKey]
        if (line != null && ix != null) s.deviations[leg.primaryKey]?.let { dev ->
            var worst: Pair<Double, Int>? = null
            for (i in ix.from + 1..ix.to) dev.typical(line.stops[i], hour)?.let { v -> if (v > (worst?.first ?: 0.0)) worst = Pair(v, i) }
            worst?.let { w -> if (w.second < line.names.size) out.add("${leg.routesLabel}: trains lose the most time arriving at ${line.names[w.second]} (+${w.first.roundToInt()} s per train)") }
        }
        for (a in ctx.alertsFor(leg.routes).take(2)) out.add("alert (${a.kind}) on the ${a.routes.joinToString("/")}: ${a.header} — ${alertEvidence(a, ctx).text}")
    }
    s.boards[option.legs[0].primaryKey]?.let { b ->
        if (b.nHolding > 0 || b.nStalled > 0) out.add("on the ${b.route} right now: ${b.nHolding} held at a stop, ${b.nStalled} overdue between stops")
        if (b.nFeedOptimistic > 0) out.add("${b.nFeedOptimistic} train(s) whose feed ETA looks optimistic given where they are")
    }
    if (out.isEmpty()) out.add("Nothing unusual on this path right now.")
    return out
}

/** The route explained: the verdict, the expected time's parts, the train the feeds give, the lines, the alerts, the data behind it. */
@Composable
fun RouteInsightsSheet(data: AppData, option: PathOption, schedule: ClientSchedule, originName: String, destName: String, onDismiss: () -> Unit) {
    val s by data.state.collectAsStateWithLifecycle()
    val h = routeHealth(option, healthContext(s, data.scenario))
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(16.dp)) {
            Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) { Text("Route insights", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f)); TextButton(onClick = onDismiss) { Text("Done") } }
                Row(verticalAlignment = Alignment.CenterVertically) { RouteBullets(option.legs.flatMap { it.routes }, 20.dp); Spacer(Modifier.weight(1f)); HealthDot(h) }
                Text(option.label, style = MaterialTheme.typography.titleSmall); Caption("$originName → $destName")
                val it = option.live
                Text(if (it != null) "Board ${Fmt.hhmm(it.boardTs)} · arrive ${Fmt.hhmm(it.arriveTs)} · ${Fmt.minTxt(it.totalSec)} door to door" else "Expected ${Fmt.minTxt(option.expectedSec)} door to door · no train for this path in the feeds yet", style = MaterialTheme.typography.bodyMedium)
                Caption("${h.label} against the timetable" + if (h.slipSec >= 60) " · could slip ${Fmt.minTxt(h.slipSec)} more" else "", h.textColor())
                h.reasons.forEach { Caption("• $it") }
                Text("Expected time, from the timetable and history", style = MaterialTheme.typography.titleSmall)
                InsightRow("Wait at $originName", Fmt.minTxt(option.wait1Sec), "half the scheduled headway around now, at most 15 min")
                option.legs.forEachIndexed { i, leg ->
                    InsightRow("Ride on the ${leg.routesLabel}", Fmt.minTxt(leg.schedRideSec?.toDouble()), "${leg.nStops} stops, scheduled running time")
                    leg.typicalSec?.let { InsightRow("Typically at this hour", Fmt.signed(it), "time trains lose on this stretch at this hour, from the deviation grid") }
                    if (leg.holdRiskSec >= 1) InsightRow("Hold risk", Fmt.signed(leg.holdRiskSec), "holds per day at these stops × their median length ÷ trips per day")
                    val tr = option.transfer
                    if (i == 0 && option.legs.size > 1 && tr != null) {
                        if (tr.walkSec > 0) InsightRow("Walk at ${tr.station}", Fmt.mmss(tr.walkSec.toDouble()), "the MTA's minimum transfer time, or your usual change here") else InsightRow("Change at ${tr.station}", "same platform", "no walk: the MTA lists this change as 0 s")
                        InsightRow("Wait for the ${option.legs[1].routesLabel}", Fmt.minTxt(option.wait2Sec), "half its scheduled headway")
                    }
                }
                InsightRow("Expected", Fmt.minTxt(option.expectedSec), "scheduled ${Fmt.minTxt(option.schedSec.toDouble())} plus the waits and the typical losses")
                if (it != null) it.legs.forEachIndexed { i, tc ->
                    Text(if (it.legs.size > 1) "Leg ${i + 1}: your ${tc.train.route} train" else "Your ${tc.train.route} train", style = MaterialTheme.typography.titleSmall)
                    InsightRow("Train", shortLabel(tc.train), if (tc.train.trainId != null) "the MTA's own train id" else "trip id from the feed")
                    InsightRow("Now", tc.train.position?.text ?: "position unknown", "from the vehicle position in the feed")
                    InsightRow("Vs timetable", Fmt.late(tc.train.effectiveLatenessSec), when (tc.train.corroboration) { "agree" -> "feed and position agree"; "feed_optimistic" -> "feed looks optimistic; the position-based figure is used"; else -> "position unknown, feed only" })
                    InsightRow("Boards", Fmt.hhmm(tc.boardTs), "${tc.stopsToOrigin} stop${if (tc.stopsToOrigin == 1) "" else "s"} away")
                    InsightRow("Arrives", Fmt.hhmm(tc.arriveTs), when (tc.arriveSource) { "blend" -> "engine: feed ETA blended with the timetable carried by its lateness"; "feed" -> "engine: calibrated feed ETA"; else -> "from the feed" })
                    val lo = tc.arriveLoTs; val hi = tc.arriveHiTs
                    if (lo != null && hi != null) InsightRow("80% window", "${Fmt.hhmm(lo)}–${Fmt.hhmm(hi)}", "the engine's spread for this horizon on this line")
                    tc.feedArriveTs?.let { f -> if (abs(f - tc.arriveTs) >= 30) InsightRow("Feed says", Fmt.hhmm(f), "the raw ETA in the feed; the engine moved it by ${Fmt.signed(tc.arriveTs - f)}") }
                    InsightRow("Ride", Fmt.minTxt(tc.rideSec), tc.rideVsSchedSec?.let { "${Fmt.signed(it)} vs the scheduled ride" } ?: "no scheduled ride to compare")
                }
                Text("Insights", style = MaterialTheme.typography.titleSmall)
                insightLines(s, option, schedule, data.scenario).forEach { Caption("• $it") }
                Text("Data behind this", style = MaterialTheme.typography.titleSmall)
                InsightRow("Prediction engine", s.model?.summary ?: "physical priors", s.model?.generatedAt?.let { "tables published $it" } ?: "no fitted tables loaded")
                InsightRow("Hold log", s.holds?.let { "${it.n} holds" } ?: "–", "holds at stops, per day and median length")
                InsightRow("Deviation grids", "${option.legs.count { s.deviations.containsKey(it.primaryKey) }} of ${option.legs.size} legs", "typical time lost per stop by hour")
                InsightRow("Feeds", if (s.offline) "offline" else "live", "last poll ${s.lastUpdateTs?.let { Fmt.hhmmss(it) } ?: "–"} · feed stamp ${Fmt.hhmmss(s.lastFeedTs)}")
            }
        }
    }
}

@Composable
private fun InsightRow(label: String, value: String, note: String) {
    Column { Row { Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f)); Text(value, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold) }; Caption(note) }
}
