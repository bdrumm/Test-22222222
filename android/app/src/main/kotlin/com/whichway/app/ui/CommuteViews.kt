// Port of ios/WhichWay/WhichWay/Views/CommuteViews.swift: the commute chip and its menu, the setup prompt, the
// commute editor, the nearby-stations sheet and the Settings commutes section.
package com.whichway.app.ui

import android.app.TimePickerDialog
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.whichway.app.store.AppData
import com.whichway.app.store.LocationService
import com.whichway.app.store.Stores
import com.whichway.core.ClientSchedule
import com.whichway.core.CommutePreset
import com.whichway.core.Fmt
import com.whichway.core.NearbyStation
import com.whichway.core.Station
import com.whichway.core.StationIndex
import com.whichway.core.nearestStations
import com.whichway.core.reachableStations
import com.whichway.core.stationCoordinates

/** One chip: the commute on screen, else the one whose window covers now, else an invitation to add one. */
@Composable
fun CommuteChip(presets: List<CommutePreset>, activeId: String?, currentId: String?, onPick: (CommutePreset) -> Unit, onEdit: (CommutePreset) -> Unit, onAdd: () -> Unit) {
    val shown = presets.firstOrNull { it.id == currentId } ?: presets.firstOrNull { it.id == activeId }
    val on = shown != null && shown.id == currentId
    var menu by remember { mutableStateOf(false) }
    var editMenu by remember { mutableStateOf(false) }
    Box {
        Surface(Modifier.clickable { menu = true }, shape = CircleShape,
            color = if (on) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f) else MaterialTheme.colorScheme.surfaceVariant,
            border = if (on) BorderStroke(1.dp, MaterialTheme.colorScheme.primary) else null) {
            Row(Modifier.padding(horizontal = 12.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                Text(if (shown == null) "+" else if (on) "◆" else "◷", style = MaterialTheme.typography.bodySmall)
                if (shown != null) {
                    Text(shown.name, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Caption(shown.windowText)
                } else Text(if (presets.isEmpty()) "Add a commute" else "Commutes", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold)
                Caption("▾")
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            presets.forEach { p ->
                val mark = if (p.id == currentId) "✓ " else if (p.id == activeId) "◷ " else ""
                DropdownMenuItem(text = { Text("$mark${p.name} · ${p.windowText}") }, onClick = { menu = false; onPick(p) })
            }
            if (presets.isNotEmpty()) {
                HorizontalDivider()
                DropdownMenuItem(text = { Text("Edit…") }, onClick = { menu = false; editMenu = true })
            }
            DropdownMenuItem(text = { Text("Add commute") }, onClick = { menu = false; onAdd() })
        }
        DropdownMenu(expanded = editMenu, onDismissRequest = { editMenu = false }) {
            presets.forEach { p -> DropdownMenuItem(text = { Text(p.name) }, onClick = { editMenu = false; onEdit(p) }) }
        }
    }
}

/** Shown while the trip is incomplete. */
@Composable
fun SetupPrompt(originSet: Boolean, destSet: Boolean, reachable: Int, onAdd: () -> Unit) {
    Card {
        Text(if (!originSet && !destSet) "Where are you going?" else if (originSet) "Pick a destination" else "Pick where you start", style = MaterialTheme.typography.titleMedium)
        Caption(if (originSet && !destSet) "$reachable stations are reachable direct or with one change. Save the trip as a commute and the Go tab will switch to it by time of day."
            else "Save a commute with your usual stations and the Go tab switches to it on its own by time of day; or pick stations above for a one-off trip.")
        TextButton(onClick = onAdd) { Text("+ Add a commute") }
    }
}

/** Create or edit a commute: name, stations (or the nearest station by location), the daily window, weekdays. */
@Composable
fun PresetEditor(data: AppData, preset: CommutePreset, onDismiss: () -> Unit, onSave: (CommutePreset) -> Unit) {
    val s by data.state.collectAsStateWithLifecycle()
    val index = s.index
    val sched = s.schedule
    var p by remember { mutableStateOf(preset) }
    var picking by remember { mutableStateOf<String?>(null) }
    val ctx = LocalContext.current
    fun timePicker(minute: Int, set: (Int) -> Unit) = TimePickerDialog(ctx, { _, h, m -> set(h * 60 + m) }, minute / 60 % 24, minute % 60, false).show()
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(16.dp)) {
            Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(if (p.name.isEmpty()) "Commute" else p.name, style = MaterialTheme.typography.titleMedium)
                OutlinedTextField(p.name, { p = p.copy(name = it) }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Start from the nearest station (uses your location)", modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                    Switch(p.useNearestOrigin, { p = p.copy(useNearestOrigin = it) })
                }
                if (!p.useNearestOrigin) StationButton("From", index?.station(p.originId)) { picking = "from" }
                StationButton("To", index?.station(p.destId)) { picking = "to" }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Active", modifier = Modifier.weight(1f))
                    TextButton(onClick = { timePicker(p.startMinute) { p = p.copy(startMinute = it) } }) { Text(Fmt.clock(p.startMinute)) }
                    Text("–")
                    TextButton(onClick = { timePicker(p.endMinute) { p = p.copy(endMinute = it) } }) { Text(Fmt.clock(p.endMinute)) }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Weekdays only", modifier = Modifier.weight(1f))
                    Switch(p.weekdaysOnly, { p = p.copy(weekdaysOnly = it) })
                }
                Caption("Between these times the Go tab switches to this trip on its own (New York time; a window may cross midnight). Picking stations by hand keeps your choice for the rest of the day.")
                Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
                    TextButton(onClick = onDismiss) { Text("Cancel") }
                    Button(enabled = p.destId.isNotEmpty() && (p.useNearestOrigin || p.originId.isNotEmpty()),
                        onClick = { onSave(if (p.name.isBlank()) p.copy(name = "Commute") else p); onDismiss() }) { Text("Save") }
                }
            }
        }
    }
    if (index != null && sched != null) when (picking) {
        "from" -> StationPicker("From", index.sorted, null, { picking = null }) { p = p.copy(originId = it.id) }
        "to" -> {
            val reach = if (p.useNearestOrigin || p.originId.isEmpty()) null else reachableStations(sched, index, p.originId)
            StationPicker("To", index.sorted, reach, { picking = null }) { p = p.copy(destId = it.id) }
        }
    }
}

/** The stations nearest to the phone, with the walk to each; picking one makes it the origin. */
@Composable
fun NearbyStationsSheet(data: AppData, loc: LocationService, onDismiss: () -> Unit, onPick: (Station) -> Unit) {
    val s by data.state.collectAsStateWithLifecycle()
    val fix by loc.fix.collectAsStateWithLifecycle()
    val err by loc.error.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { data.requestGeometry(); loc.request() }
    val sched = s.schedule
    val index = s.index
    val geo = s.geometry
    val f = fix
    val nearby: List<NearbyStation> = remember(f, geo, s.staticVersion) {
        if (f == null || sched == null || index == null || geo == null) emptyList()
        else nearestStations(f.latLon, stationCoordinates(sched, index, geo), index, 8)
    }
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(16.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Near you", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    TextButton(onClick = onDismiss) { Text("Cancel") }
                }
                when {
                    err != null -> { Caption(err ?: ""); TextButton(onClick = { loc.request() }) { Text("Try again") } }
                    f == null -> Caption("Finding you…")
                    geo == null -> Caption("Loading station locations…")
                    else -> nearby.forEach { n ->
                        Column(Modifier.fillMaxWidth().clickable { onPick(n.station); onDismiss() }.padding(vertical = 6.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(n.station.name, modifier = Modifier.weight(1f))
                                RouteBullets(n.station.routes, 18.dp)
                            }
                            Caption("${Fmt.miles(n.meters)} · about ${n.walkMinutes} min walk")
                        }
                    }
                }
            }
        }
    }
}

/** Settings: the saved commutes with edit, reorder and delete. */
@Composable
fun CommutesSection(data: AppData, stores: Stores) {
    val s by data.state.collectAsStateWithLifecycle()
    val presets by stores.presets.collectAsStateWithLifecycle()
    var editing by remember { mutableStateOf<CommutePreset?>(null) }
    val index = s.index
    Text("Commutes", style = MaterialTheme.typography.titleMedium)
    presets.forEachIndexed { i, p ->
        Card(Modifier.clickable { editing = p }) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(p.name)
                    Caption("${if (p.useNearestOrigin) "nearest station" else index?.station(p.originId)?.name ?: p.originId} → ${index?.station(p.destId)?.name ?: p.destId} · ${p.windowText}")
                }
                TextButton(onClick = { stores.movePreset(p.id, -1) }, enabled = i > 0) { Text("↑") }
                TextButton(onClick = { stores.movePreset(p.id, 1) }, enabled = i < presets.size - 1) { Text("↓") }
                TextButton(onClick = { stores.removePreset(p.id) }) { Text("Delete") }
            }
        }
    }
    TextButton(enabled = index != null, onClick = {
        val w = CommutePreset.suggestedWindow(s.now)
        editing = CommutePreset(name = CommutePreset.suggestedName(s.now), originId = "", destId = "", startMinute = w.first, endMinute = w.second)
    }) { Text("Add commute") }
    Caption("The first commute whose window covers the current time is applied when the Go tab opens; the order decides when windows overlap.")
    editing?.let { p -> PresetEditor(data, p, { editing = null }) { stores.updatePreset(it) } }
}
