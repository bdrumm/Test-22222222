// The Line tab (ios/WhichWay/WhichWay/Views/LineBoardView.swift), reduced to the board: one line direction, the
// engine's summary, the line's alerts and every started train with its feed and engine ETA, lateness and flags.
package com.whichway.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.whichway.app.store.AppData
import com.whichway.core.ClientSchedule
import com.whichway.core.Fmt
import com.whichway.core.LiveTrain

private fun sortedKeys(s: ClientSchedule) = s.lines.keys.sortedWith(compareBy<String> { it.substringBefore("_") }.thenBy { it.substringAfter("_") })
private fun title(key: String, s: ClientSchedule) = "${key.substringBefore("_")} → ${s.lines[key]?.names?.lastOrNull() ?: key.substringAfter("_")}"

@Composable
fun LineScreen(data: AppData, modifier: Modifier = Modifier) {
    val s by data.state.collectAsStateWithLifecycle()
    val sched = s.schedule ?: run { Box(modifier.padding(16.dp)) { Text("Loading the schedule…") }; return }
    var lineKey by rememberSaveable { mutableStateOf("") }
    val keys = sortedKeys(sched)
    if (lineKey !in sched.lines) lineKey = data.state.value.boards.keys.sorted().firstOrNull() ?: keys.firstOrNull() ?: return
    DisposableEffect(lineKey) {
        data.setWanted(setOf(lineKey), "line")
        onDispose { data.setWanted(emptySet(), "line") }
    }
    var menu by rememberSaveable { mutableStateOf(false) }
    val board = s.predictedBoards[lineKey] ?: s.boards[lineKey]
    val route = lineKey.substringBefore("_")
    Column(modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Box {
            OutlinedButton(onClick = { menu = true }) {
                RouteBullet(route, 20.dp)
                Spacer(Modifier.width(8.dp))
                Text(title(lineKey, sched))
            }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                keys.forEach { k -> DropdownMenuItem(text = { Text(title(k, sched)) }, onClick = { lineKey = k; menu = false }) }
            }
        }
        if (board == null) { Caption("Waiting for the feed…"); return@Column }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Tile("Trains", "${board.trains.size}", "started, in the feed", Modifier.weight(1f))
            Tile("Held / overdue", "${board.nHolding} / ${board.nStalled}", "at a stop / between stops", Modifier.weight(1f))
            Tile("Late ≥ 3 min", "${board.trains.count { (it.effectiveLatenessSec ?: 0.0) >= 180 }}", "vs the timetable", Modifier.weight(1f))
        }
        val lp = s.predictions[lineKey]?.let { it[data.scenario] ?: it["baseline"] }
        lp?.worstGap?.let { g ->
            val line = sched.lines[lineKey]
            Caption("Largest gap in the next hour: ${Fmt.mmss(g.gapSec)} at ${line?.name(g.idx) ?: ""} (${Fmt.hhmm(g.atTs)})" +
                if (lp.nKnockOn > 0) " · ${lp.nKnockOn} train${if (lp.nKnockOn == 1) "" else "s"} held back by the train ahead" else "")
        }
        val alerts = s.alerts.filter { route in it.routes }
        alerts.take(3).forEach { a ->
            Card(tint = if (a.kind == "delay") Color(0x33FF9800) else null) {
                Text(when (a.kind) { "delay" -> "Delays"; "planned" -> "Planned work"; else -> "Notice" }, fontWeight = FontWeight.SemiBold)
                Caption(a.header)
            }
        }
        board.trains.forEach { TrainRow(it) }
        if (board.trains.isEmpty()) Caption("No started train on this line in the feed.")
        s.lastError?.let { Caption(it, Color(0xFFD32F2F)) }
    }
}

@Composable
private fun Tile(title: String, value: String, sub: String, modifier: Modifier) {
    Box(modifier) {
        Card {
            Caption(title)
            Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Caption(sub)
        }
    }
}

@Composable
private fun TrainRow(t: LiveTrain) {
    Card {
        Row(verticalAlignment = Alignment.Top) {
            RouteBullet(t.route, 20.dp)
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Row {
                    Text(t.label, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                    Text("next ${t.nextName} ${Fmt.hhmm(t.feedPoints?.firstOrNull()?.ts ?: t.etaTs)}", style = MaterialTheme.typography.bodySmall, maxLines = 1)
                }
                val pr = t.pred
                val pt = pr?.point(t.nextIdx)
                if (pr != null && pt != null) {
                    var e = "engine ${Fmt.hhmm(pt.etaTs)} · ${Fmt.hhmm(pt.loTs)}–${Fmt.hhmm(pt.hiTs)}"
                    if (pr.holdExtraSec > 0) e += " · +${pr.holdExtraSec.toInt()} s expected hold"
                    if (pr.knockOnSec >= 60) e += " · held back ${Fmt.mmss(pr.knockOnSec)}"
                    Caption(e)
                }
                Caption(t.position?.text ?: "position unknown")
                val late = t.effectiveLatenessSec
                val flags = buildList {
                    add(Fmt.late(late) + if (t.schedMethod == "nearest") " ~" else "")
                    if (t.corroboration == "feed_optimistic") add("feed optimistic")
                    t.position?.let { if (it.holding) add("held ${Fmt.mmss(it.sinceSec)}"); if (it.stalled) add("overdue") }
                    if (t.trackChanged) add("track change")
                    t.lastRun?.speedKmh?.let { add("last run ${Fmt.mph(it)}") }
                }
                Caption(flags.joinToString(" · "), when {
                    late != null && late >= 300 -> Color(0xFFD32F2F)
                    late != null && late >= 120 -> Color(0xFFEF6C00)
                    else -> MaterialTheme.colorScheme.onSurfaceVariant
                })
            }
        }
    }
}
