// Port of ios/WhichWay/WhichWay/Views/PlacesViews.swift: Settings' places (Home, Work, the rider's own) with the
// editor and the pin, and the pace section.
package com.whichway.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.whichway.app.store.AppData
import com.whichway.app.store.LocationService
import com.whichway.app.store.Stores
import com.whichway.core.Fmt
import com.whichway.core.Place

@Composable
fun PlacesSection(data: AppData, stores: Stores, loc: LocationService) {
    val s by data.state.collectAsStateWithLifecycle()
    val places by stores.places.collectAsStateWithLifecycle()
    val habits by stores.habits.collectAsStateWithLifecycle()
    var editing by remember { mutableStateOf<Place?>(null) }
    val index = s.index
    Text("Places", style = MaterialTheme.typography.titleMedium)
    for (kind in listOf(Place.HOME, Place.WORK)) {
        val p = places.firstOrNull { it.kind == kind }
        if (p != null) PlaceRow(p, index?.station(p.stationId)?.name, index?.station(p.stationId)?.routes) { editing = p }
        else {
            Card(Modifier.clickable { editing = Place(kind = kind, name = Place.title(kind), stationId = "") }) { Text("Set ${Place.title(kind)}") }
            // the history points to a station: one tap takes it
            val sug = if (kind == Place.HOME) habits.suggestedHome() else habits.suggestedWork(places.firstOrNull { it.kind == Place.HOME }?.stationId)
            val st = sug?.let { index?.station(it.stationId) }
            if (sug != null && st != null) Card(Modifier.clickable { stores.updatePlace(Place(kind = kind, name = Place.title(kind), stationId = st.id, lat = sug.lat, lon = sug.lon)) }) {
                Text("Use ${st.name} as ${Place.title(kind)}")
                Caption("${sug.uses} trips ${if (kind == Place.HOME) "left from or came back to it" else "went to or left from it"}" + if (sug.lat != null) ", from the same spot, which becomes the pin" else "")
            }
        }
    }
    places.filter { it.kind == Place.CUSTOM }.forEach { p -> PlaceRow(p, index?.station(p.stationId)?.name, index?.station(p.stationId)?.routes) { editing = p } }
    TextButton(enabled = index != null, onClick = { editing = Place(kind = Place.CUSTOM, name = "", stationId = "") }) { Text("Add place") }
    Caption("A station you call by name. Pin the spot you set out from and the app knows when you are there, learns how long it takes you to reach the station, and can suggest the trip you usually make.")
    editing?.let { p -> PlaceEditor(data, stores, loc, p, { editing = null }, { stores.updatePlace(it) }, { stores.removePlace(it) }) }
}

@Composable
private fun PlaceRow(p: Place, stationName: String?, routes: List<String>?, onClick: () -> Unit) {
    Card(Modifier.clickable(onClick = onClick)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(p.name)
                Caption((stationName ?: "station not set") + if (p.isPinned) " · pinned" else "")
            }
            routes?.let { RouteBullets(it, 16.dp) }
        }
    }
}

@Composable
private fun PlaceEditor(data: AppData, stores: Stores, loc: LocationService, place: Place, onDismiss: () -> Unit, onSave: (Place) -> Unit, onDelete: (String) -> Unit) {
    val s by data.state.collectAsStateWithLifecycle()
    val fix by loc.fix.collectAsStateWithLifecycle()
    val err by loc.error.collectAsStateWithLifecycle()
    var p by remember { mutableStateOf(place) }
    var picking by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { loc.request() }
    val f = fix
    fun pin() { f?.let { p = p.copy(lat = it.lat, lon = it.lon) } }
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(16.dp)) {
            Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(if (p.name.isEmpty()) Place.title(p.kind) else p.name, style = MaterialTheme.typography.titleMedium)
                OutlinedTextField(p.name, { p = p.copy(name = it) }, label = { Text(Place.title(p.kind)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                StationButton("At", s.index?.station(p.stationId)) { picking = true }
                if (p.isPinned) {
                    val d = f?.let { p.distanceM(it.latLon) }
                    Caption("Pinned" + (d?.let { " · ${Fmt.miles(it)} from here" } ?: ""))
                    TextButton(enabled = f != null, onClick = { pin() }) { Text("Move the pin here") }
                    TextButton(onClick = { p = p.copy(lat = null, lon = null) }) { Text("Remove the pin") }
                } else {
                    TextButton(enabled = f != null, onClick = { pin() }) { Text("Pin my current location") }
                    err?.let { Caption(it) }
                }
                Caption("With a pin, a route started from here learns how long you take to reach the station, and the usual trip from here is suggested at the right hour. The pin stays on the phone.")
                Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
                    if (p.kind == Place.CUSTOM && stores.places.value.any { it.id == p.id }) TextButton(onClick = { onDelete(p.id); onDismiss() }) { Text("Delete") }
                    TextButton(onClick = onDismiss) { Text("Cancel") }
                    Button(enabled = p.stationId.isNotEmpty(), onClick = { onSave(if (p.name.isBlank()) p.copy(name = Place.title(p.kind)) else p); onDismiss() }) { Text("Save") }
                }
            }
        }
    }
    val index = s.index
    if (picking && index != null) StationPicker("Station", index.sorted, null, { picking = false }) { p = p.copy(stationId = it.id) }
}

/** Settings: the rider's own pace model. */
@Composable
fun PaceSection(stores: Stores) {
    val pace by stores.pace.collectAsStateWithLifecycle()
    val habits by stores.habits.collectAsStateWithLifecycle()
    var on by remember { mutableStateOf(stores.learnPace) }
    Text("Learn my pace", style = MaterialTheme.typography.titleMedium)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("Learn how long my trips really take", modifier = Modifier.weight(1f))
        Switch(on, { on = it; stores.learnPace = it })
    }
    Caption("On unless you turn it off. While a route is in progress the phone measures how fast you walk, how long you take from the street to the platform, how long your changes take, and how long your places are from their stations; the predictions then use your figures instead of averages. This stays on the phone. On Android the measuring arrives with ride tracking.")
    if (on || pace.trips > 0) {
        Row { Text("Learned so far", modifier = Modifier.weight(1f)); Caption(pace.summary) }
        Row { Text("Trips noticed", modifier = Modifier.weight(1f)); Caption("${habits.uses.size}") }
        TextButton(enabled = pace.trips > 0 || habits.uses.isNotEmpty(), onClick = { stores.resetPace(); stores.resetHabits() }) { Text("Forget what it learned") }
    }
}
