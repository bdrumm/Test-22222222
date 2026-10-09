package com.whichway.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.whichway.core.Fmt
import com.whichway.core.LatLon
import com.whichway.core.NearbyStation
import com.whichway.core.Place
import com.whichway.core.Reach
import com.whichway.core.StationIndex
import com.whichway.core.haversineM
import com.whichway.core.RouteStyle
import com.whichway.core.Station
import kotlinx.coroutines.delay

fun routeColor(route: String) = Color(RouteStyle.color(route) or (0xFF shl 24))

@Composable
fun RouteBullet(route: String, size: Dp = 22.dp) {
    Box(Modifier.size(size).background(routeColor(route), CircleShape), contentAlignment = Alignment.Center) {
        Text(route, color = if (RouteStyle.darkText(route)) Color(0xFF111111) else Color.White, fontWeight = FontWeight.Bold,
            fontSize = (size.value * 0.5f).sp, maxLines = 1)
    }
}

@Composable
fun RouteBullets(routes: List<String>, size: Dp = 22.dp) {
    Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) { routes.forEach { RouteBullet(it, size) } }
}

@Composable
fun Card(modifier: Modifier = Modifier, tint: Color? = null, content: @Composable () -> Unit) {
    Surface(modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp), color = tint ?: MaterialTheme.colorScheme.surfaceVariant) {
        Column(Modifier.padding(12.dp)) { content() }
    }
}

@Composable
fun Caption(text: String, color: Color = MaterialTheme.colorScheme.onSurfaceVariant) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = color)
}

/** Wall-clock seconds (plus the demo offset), ticking once a second for countdowns. */
@Composable
fun rememberNow(offset: Double): Double {
    var now by remember { mutableDoubleStateOf(System.currentTimeMillis() / 1000.0 + offset) }
    LaunchedEffect(offset) {
        while (true) { now = System.currentTimeMillis() / 1000.0 + offset; delay(1000) }
    }
    return now
}

@Composable
fun StationButton(label: String, station: Station?, enabled: Boolean = true, onClick: () -> Unit) {
    Surface(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).clickable(enabled = enabled, onClick = onClick), color = MaterialTheme.colorScheme.surfaceVariant) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Caption(label)
            Spacer(Modifier.width(12.dp))
            Text(station?.name ?: "Choose a station", modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis,
                color = if (station == null) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
            station?.let { RouteBullets(it.routes, 18.dp) }
        }
    }
}

data class PlacePick(val place: Place, val station: Station)
fun placePicks(places: List<Place>, index: StationIndex): List<PlacePick> = Place.ordered(places).mapNotNull { p -> index.station(p.stationId)?.let { PlacePick(p, it) } }

/** Searchable station list; with `reach` (the destination picker) only reachable stations, direct ones first. */
@Composable
fun StationPicker(title: String, stations: List<Station>, reach: Map<String, Reach>?, onDismiss: () -> Unit,
                  nearTo: Station? = null, coords: Map<String, LatLon> = emptyMap(), places: List<PlacePick> = emptyList(), onPick: (Station) -> Unit) {
    var query by remember { mutableStateOf("") }
    val q = query.trim().lowercase()
    var list = if (reach != null) stations.filter { reach.containsKey(it.id) } else stations
    if (q.isNotEmpty()) list = list.filter { st -> st.name.lowercase().contains(q) || st.routes.any { it.lowercase() == q } }
    if (reach != null) list = list.sortedWith(compareBy<Station> { reach[it.id]?.how != "direct" }.thenBy { it.name })
    // the listed stations closest to the commute's own, nearest first; none while searching
    val nearby: List<NearbyStation> = if (q.isNotEmpty() || nearTo == null) emptyList() else coords[nearTo.id]?.let { c ->
        stations.filter { it.id != nearTo.id && (reach == null || reach.containsKey(it.id)) }.mapNotNull { st -> coords[st.id]?.let { NearbyStation(st, haversineM(c, it)) } }
            .sortedBy { it.meters }.take(10)
    } ?: emptyList()
    val shownPlaces = if (q.isEmpty()) places.filter { reach == null || reach.containsKey(it.station.id) } else emptyList()
    Dialog(onDismissRequest = onDismiss) {
        Surface(Modifier.fillMaxWidth().fillMaxSize(0.9f), shape = RoundedCornerShape(16.dp)) {
            Column(Modifier.padding(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    TextButton(onClick = onDismiss) { Text("Cancel") }
                }
                OutlinedTextField(query, { query = it }, label = { Text("Station name or line") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                LazyColumn(Modifier.fillMaxSize()) {
                    if (shownPlaces.isNotEmpty()) item { Caption("Places", MaterialTheme.colorScheme.primary) }
                    items(shownPlaces, key = { "place-" + it.place.id }) { pp ->
                        Row(Modifier.fillMaxWidth().clickable { onPick(pp.station); onDismiss() }.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) { Text(pp.place.name); Caption(pp.station.name) }
                            RouteBullets(pp.station.routes, 18.dp)
                        }
                        HorizontalDivider()
                    }
                    if (nearby.isNotEmpty()) item { Caption("Near ${nearTo?.name}", MaterialTheme.colorScheme.primary) }
                    items(nearby, key = { "near-" + it.station.id }) { n ->
                        Column(Modifier.fillMaxWidth().clickable { onPick(n.station); onDismiss() }.padding(vertical = 8.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) { Text(n.station.name, modifier = Modifier.weight(1f)); RouteBullets(n.station.routes, 18.dp) }
                            Caption("${Fmt.miles(n.meters)} · about ${n.walkMinutes} min walk")
                        }
                        HorizontalDivider()
                    }
                    if (nearby.isNotEmpty()) item { Caption("All stations", MaterialTheme.colorScheme.primary) }
                    items(list, key = { it.id }) { st ->
                        Column(Modifier.fillMaxWidth().clickable { onPick(st); onDismiss() }.padding(vertical = 8.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(st.name, modifier = Modifier.weight(1f))
                                RouteBullets(st.routes, 18.dp)
                            }
                            reach?.get(st.id)?.let { r ->
                                Caption(r.summary, if (r.how == "direct") Color(0xFF2E7D32) else MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        HorizontalDivider()
                    }
                }
            }
        }
    }
}
