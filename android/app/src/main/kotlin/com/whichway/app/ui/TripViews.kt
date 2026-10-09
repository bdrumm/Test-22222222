// The route in progress on the Go tab (the trip bar, the boarding prompt and the line chooser from
// PlannerView.swift; OnTrainSheet.swift; the departure board from PathViews.swift).
package com.whichway.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.whichway.app.store.AppData
import com.whichway.app.trip.TripSession
import com.whichway.app.trip.TripUi
import com.whichway.core.BoardingCandidate
import com.whichway.core.BoardingPrompt
import com.whichway.core.ClientSchedule
import com.whichway.core.Fmt
import com.whichway.core.Itinerary
import com.whichway.core.LineBelief
import com.whichway.core.PathOption
import com.whichway.core.PlatformTiming
import com.whichway.core.TripPhase
import com.whichway.core.pathTrips
import kotlin.math.abs
import kotlin.math.max

private fun phaseText(u: TripUi, originName: String, originDistance: Double?): String = when (u.phase) {
    TripPhase.approaching -> "Heading to $originName" + (originDistance?.let { " · ${Fmt.miles(it)}" } ?: "")
    TripPhase.atStation -> "At $originName"
    TripPhase.riding -> {
        val b = u.belief
        if (b != null && (b.settled || b.byHand) && b.route != null) {
            val c = b.chosenRoute
            if (b.verdict == LineBelief.Verdict.switched && c != null && c != b.route) "On the ${b.route}, not the $c"
            else if (b.assumed) "On the ${b.route}, presumably" else "On the ${b.route}"
        } else if (u.rideAssumed) "On the train, presumably" else "On the train"
    }
    TripPhase.arrived -> "Arrived"
    null -> ""
}

/** The route's phase and the way to end it, or the way to start it; on the train, the line the phone thinks the rider boarded. */
@Composable
fun TripBar(session: TripSession, u: TripUi, originName: String, originDistance: Double?, onOnTrain: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (u.phase != null) {
                Text(phaseText(u, originName, originDistance), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold, color = Color(0xFF2E7D32), modifier = Modifier.weight(1f), maxLines = 1)
                OutlinedButton(onClick = onOnTrain) { Text("Train") }
                OutlinedButton(onClick = { session.endTrip("hand") }) { Text("End") }
            } else {
                Caption(u.lastEndNote ?: "Starts by itself at $originName")
                Spacer(Modifier.weight(1f))
                OutlinedButton(onClick = onOnTrain) { Text("On a train?") }
                Button(onClick = { session.startTrip("hand") }) { Text("Start") }
            }
        }
        val pr = u.prompt
        if (pr != null) BoardingPromptView(session, pr, session.planner?.headline)
        else if (u.phase == TripPhase.riding && u.currentLegKeys.size > 1) LineChooser(session, u)
    }
}

@Composable
private fun BoardingPromptView(session: TripSession, pr: BoardingPrompt, headline: PathOption?) {
    val seen = HashSet<String>()
    val options = pr.candidates.sortedWith(compareBy<BoardingCandidate> { it.trainId != pr.bestTrainId }.thenBy { it.key != pr.chosenKey }.thenBy { it.boardTs }).filter { seen.add(it.key) }
    val bestRoute = pr.bestKey?.substringBefore("_"); val planRoute = pr.chosenKey?.substringBefore("_")
    val question = when (pr.reason) {
        BoardingPrompt.Reason.switched -> "Looks like you boarded the ${bestRoute ?: "?"}${if (planRoute != null && planRoute != bestRoute) ", not the $planRoute" else ""}. Right?"
        BoardingPrompt.Reason.unsure -> "Which train did you board?"
        BoardingPrompt.Reason.assumed -> "Are you on the ${planRoute ?: ""}${pr.candidates.firstOrNull { it.key == pr.chosenKey }?.let { " that left ${Fmt.hhmm(PlatformTiming.pullsAway(it.boardTs, it.route))}" } ?: ""}?"
    }
    Card(tint = MaterialTheme.colorScheme.primary.copy(alpha = 0.08f)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("? $question", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            TextButton(onClick = { session.dismissPrompt() }) { Text("×") }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            options.forEach { c ->
                val best = c.trainId == pr.bestTrainId
                Surface(Modifier.clickable { session.confirm(c.trainId) }, shape = RoundedCornerShape(10.dp),
                    color = if (best) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f) else MaterialTheme.colorScheme.surfaceVariant,
                    border = if (best) BorderStroke(1.dp, MaterialTheme.colorScheme.primary) else null) {
                    Row(Modifier.padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        RouteBullet(c.route, 20.dp)
                        Column {
                            Text("left ${Fmt.hhmm(PlatformTiming.pullsAway(c.boardTs, c.route))}", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold)
                            Caption(if (best) "the phone's guess" else if (c.key == pr.chosenKey) "the plan" else "also at the platform")
                        }
                    }
                }
            }
            Surface(Modifier.clickable { session.notOnTrain() }, shape = RoundedCornerShape(10.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                Text("Not on a train", Modifier.padding(horizontal = 10.dp, vertical = 10.dp), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold)
            }
        }
        if (pr.reason == BoardingPrompt.Reason.switched && headline != null && bestRoute != null && headline.legs.getOrNull(pr.leg)?.routes?.contains(bestRoute) == true) Caption("Following ${headline.label} now.")
    }
}

@Composable
private fun LineChooser(session: TripSession, u: TripUi) {
    val b = u.belief
    val picked = if (b != null && (b.settled || b.byHand)) b.bestKey else null
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Caption(if (b?.byHand == true) "You said:" else if (picked == null) "Which train did you board?" else "On the")
        u.currentLegKeys.forEach { key ->
            val route = key.substringBefore("_")
            Surface(Modifier.clickable { session.setBoardedByHand(key) }, shape = RoundedCornerShape(8.dp),
                color = if (picked == key) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f) else MaterialTheme.colorScheme.surfaceVariant) {
                Row(Modifier.padding(horizontal = 6.dp, vertical = 3.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    RouteBullet(route, 18.dp)
                    if (picked == key) Text(if (b?.byHand == true) "☝" else "✓", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold)
                }
            }
        }
        if (b != null && picked != null && !b.byHand) Caption(if (b.assumed) "assumed from the schedule" else "${(b.confidence * 100).toInt()}% sure")
    }
}

/** The rider's own word on the train they are on. */
@Composable
fun OnTrainSheet(session: TripSession, data: AppData, stationName: String, onDismiss: () -> Unit) {
    LaunchedEffect(Unit) { session.prepareOnTrain() }
    val u = session.ui.value
    val candidates = session.trainsAheadNow()
    val current = u.belief?.takeIf { it.settled || it.byHand }?.bestTrain
    val transfer = session.transferChoices()
    val boards = data.state.value.boards
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(16.dp)) {
            Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Which train are you on?", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    TextButton(onClick = onDismiss) { Text("Done") }
                }
                if (candidates.isEmpty()) Caption("No train has left $stationName toward your destination in the last 25 minutes, as far as the feeds show. Lines not on the list yet appear after the next refresh.")
                candidates.forEach { c ->
                    Column(Modifier.fillMaxWidth().clickable { session.boardTrain(c); onDismiss() }.padding(vertical = 6.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            RouteBullet(c.route, 26.dp)
                            Column(Modifier.weight(1f)) {
                                Text("Left $stationName about ${Fmt.hhmm(PlatformTiming.pullsAway(c.boardTs, c.route))}", fontWeight = FontWeight.SemiBold)
                                val t = boards[c.key]?.trains?.firstOrNull { it.id == c.trainId }
                                Caption(t?.let { tr -> tr.position?.let { "now ${it.text}" } ?: "now approaching ${tr.nextName}" } ?: c.alightTs?.let { "gets off at ${Fmt.hhmm(it)}" } ?: "")
                                session.routeFor(c.key)?.let { Caption(it) }
                            }
                            if (c.trainId == current) Text("✓", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold)
                        }
                    }
                    HorizontalDivider()
                }
                Caption("The route follows the train you pick: the change ahead, the arrival and the notification.")
                if (transfer != null && transfer.choices.isNotEmpty()) {
                    Text("At ${transfer.station}, take the", style = MaterialTheme.typography.titleSmall)
                    transfer.choices.forEach { ch ->
                        Row(Modifier.fillMaxWidth().clickable { session.onTransfer(ch.key); onDismiss() }.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            RouteBullet(ch.key.substringBefore("_"), 26.dp)
                            Text(ch.label, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
                if (u.phase == TripPhase.riding) TextButton(onClick = { session.notOnTrain(); onDismiss() }) { Text("Not on a train", color = Color(0xFFD32F2F)) }
            }
        }
    }
}

/** Countdowns to boarding at the platform, each train's position and flags, the arrival with its window. */
@Composable
fun DepartureBoard(data: AppData, option: PathOption, schedule: ClientSchedule, originName: String, destName: String, now: Double) {
    val s = data.state.value
    val its = pathTrips(s.predictedBoards, schedule, option, s.now, 6)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row {
            Text("Departures from $originName", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            Caption("${option.legs[0].routesLabel} toward $destName")
        }
        if (its.isEmpty()) Caption(if (s.predictedBoards.isEmpty()) "Waiting for the live feeds…" else "No train for this path in the feed yet.")
        its.forEachIndexed { i, it -> DepartureRow(it, option, now, i == 0) }
        Caption("Countdown to boarding at your platform; the arrival is the engine's estimate with its 80% window.")
    }
}

@Composable
private fun DepartureRow(itin: Itinerary, option: PathOption, now: Double, first: Boolean) {
    val l0 = itin.legs[0]; val ln = itin.legs.last()
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(10.dp), color = if (first) MaterialTheme.colorScheme.primary.copy(alpha = 0.12f) else MaterialTheme.colorScheme.surfaceVariant) {
        Row(Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(Fmt.mmss(max(0.0, itin.boardTs - now)), fontSize = 24.sp, fontWeight = FontWeight.Bold, modifier = Modifier.width(66.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    RouteBullet(l0.train.route, 16.dp)
                    Caption("boards ${Fmt.hhmm(l0.boardTs)} · ${stopsAwayText(l0.train.nextIdx, option, l0.key)}")
                }
                val flags = buildList {
                    l0.train.position?.let { if (it.holding) add("held"); if (it.stalled) add("overdue") }
                    if (l0.train.corroboration == "feed_optimistic") add("optimistic")
                    val m = itin.connectionMarginSec; val tr = option.transfer
                    if (itin.legs.size > 1 && m != null && tr != null) add("change at ${tr.station}: ${if (m < 120) "tight, " else ""}${Fmt.mmss(m)} margin")
                    if (abs(itin.rideVsSchedSec) >= 60) add("${Fmt.signed(itin.rideVsSchedSec / 60, "min")} vs schedule")
                }
                if (flags.isNotEmpty()) Caption(flags.joinToString(" · "))
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(Fmt.hhmm(itin.arriveTs), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                val lo = ln.arriveLoTs; val hi = ln.arriveHiTs
                if (lo != null && hi != null) Caption("${Fmt.hhmm(lo)}–${Fmt.hhmm(hi)}")
            }
        }
    }
}

/** "3 stops away", "at your platform". */
fun stopsAwayText(nextIdx: Int, option: PathOption, key: String): String {
    val ix = option.legs.firstOrNull { it.idx.containsKey(key) }?.idx?.get(key) ?: return ""
    val n = ix.from - nextIdx + 1
    return if (n <= 0) "at your platform" else "$n stop${if (n == 1) "" else "s"} away"
}
