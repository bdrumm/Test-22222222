// The Go tab (ios/WhichWay/WhichWay/Views/PlannerView.swift + the Now card in PathViews.swift), reduced to the
// planner: pick the two stations, every route ranked by expected and live time, the train to take with its
// countdown, and the selected route's next itineraries. The route-in-progress half is not ported yet.
package com.whichway.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.whichway.app.store.AppData
import com.whichway.app.store.AppState
import com.whichway.app.store.LocationService
import com.whichway.app.store.Stores
import com.whichway.core.CommutePreset
import com.whichway.core.NearbyStation
import com.whichway.core.Place
import com.whichway.core.haversineM
import com.whichway.core.nearestStations
import com.whichway.core.stationCoordinates
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.foundation.layout.size
import com.whichway.core.ClientSchedule
import com.whichway.core.Fmt
import com.whichway.core.Itinerary
import com.whichway.core.PathOption
import com.whichway.core.Reach
import com.whichway.core.Station
import com.whichway.core.StationIndex
import com.whichway.core.enumeratePaths
import com.whichway.core.evaluate
import com.whichway.core.pathTrips
import com.whichway.core.reachableStations
import kotlin.math.max

/** Routes with a train in the feeds first, by that itinerary's arrival; the rest by expected time. */
private fun ranked(paths: List<PathOption>): List<PathOption> = paths.sortedWith { a, b ->
    val x = a.live; val y = b.live
    when {
        x != null && y != null -> if (x.arriveTs != y.arriveTs) x.arriveTs.compareTo(y.arriveTs) else a.expectedSec.compareTo(b.expectedSec)
        x != null -> -1
        y != null -> 1
        else -> a.expectedSec.compareTo(b.expectedSec)
    }
}

@Composable
fun GoScreen(data: AppData, modifier: Modifier = Modifier) {
    val s by data.state.collectAsStateWithLifecycle()
    val sched = s.schedule
    val index = s.index
    Box(modifier) {
        when {
            sched != null && index != null -> Planner(data, s, sched, index)
            s.lastError != null -> Column(Modifier.padding(16.dp)) {
                Text("Could not load", style = MaterialTheme.typography.titleMedium)
                Caption(s.lastError ?: "")
                TextButton(onClick = { data.configure(s.baseUrl, s.pollSec) }) { Text("Retry") }
            }
            else -> Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.width(20.dp).height(20.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(10.dp))
                Text("Loading the schedule…")
            }
        }
    }
}

@Composable
private fun Planner(data: AppData, s: AppState, sched: ClientSchedule, index: StationIndex) {
    val ctx = LocalContext.current.applicationContext
    val stores = remember(ctx) { Stores.get(ctx) }
    val loc = remember(ctx) { LocationService.get(ctx) }
    val presets by stores.presets.collectAsStateWithLifecycle()
    val places by stores.places.collectAsStateWithLifecycle()
    val pace by stores.pace.collectAsStateWithLifecycle()
    val fix by loc.fix.collectAsStateWithLifecycle()
    val locError by loc.error.collectAsStateWithLifecycle()
    var originId by remember { mutableStateOf(stores.originId) }
    var destId by remember { mutableStateOf(stores.destId) }
    var picking by remember { mutableStateOf<String?>(null) }
    var selected by remember { mutableStateOf<String?>(null) }
    var editing by remember { mutableStateOf<CommutePreset?>(null) }
    var nearbySheet by remember { mutableStateOf(false) }
    /** The commute the planner is on (picking a station by hand leaves it). */
    var currentPresetId by remember { mutableStateOf<String?>(null) }
    /** A commute with "nearest station" waits for a fix and the geometry. */
    var pendingNearest by remember { mutableStateOf(false) }
    var pendingDest by remember { mutableStateOf("") }
    var habitNote by remember { mutableStateOf<String?>(null) }

    fun setTrip(o: String, d: String) {
        originId = o; destId = d
        stores.originId = o; stores.destId = d
    }
    fun pickedByHand() {
        currentPresetId = null
        pendingNearest = false
        stores.pickedByHandTs = s.now
        habitNote = null
        stores.activePreset(s.now)?.let { stores.appliedPreset = "${Fmt.dayStamp(s.now)}|${it.id}" }
    }
    fun applyPreset(p: CommutePreset, byHand: Boolean) {
        if (byHand) stores.activePreset(s.now)?.let { stores.appliedPreset = "${Fmt.dayStamp(s.now)}|${it.id}" }
        currentPresetId = p.id
        data.requestGeometry()
        if (p.useNearestOrigin) {
            pendingDest = index.station(p.destId)?.id ?: p.destId
            pendingNearest = true
            loc.request()
        } else {
            pendingNearest = false
            setTrip(index.station(p.originId)?.id ?: p.originId, index.station(p.destId)?.id ?: p.destId)
        }
    }
    /** The first commute whose window covers now, once per window per day; stations picked by hand keep. */
    fun autoApply() {
        val p = stores.activePreset(s.now) ?: return
        val stamp = "${Fmt.dayStamp(s.now)}|${p.id}"
        if (stores.appliedPreset == stamp) {
            if (currentPresetId == null && destId == index.station(p.destId)?.id && (p.useNearestOrigin || originId == index.station(p.originId)?.id)) currentPresetId = p.id
            return
        }
        stores.appliedPreset = stamp
        applyPreset(p, byHand = false)
    }
    /** With no commute window covering now and no station picked by hand in the last two hours, the usual trip at this hour. */
    fun applyHabit() {
        if (stores.activePreset(s.now) != null || s.now - stores.pickedByHandTs <= 7200) return
        val l = fix?.takeIf { it.ageSec < 900 }
        val g = stores.likelyTrip(s.now, l?.lat, l?.lon) ?: return
        val o = index.station(g.origin) ?: return
        val d = index.station(g.dest) ?: return
        fun name(st: Station) = places.firstOrNull { it.stationId == st.id }?.name ?: st.name
        habitNote = "Your usual trip at this hour: ${name(o)} → ${name(d)}"
        if (originId == o.id && destId == d.id) return
        currentPresetId = null
        setTrip(o.id, d.id)
    }
    // the nearest station from which the destination is reachable with at most one change (else the nearest)
    LaunchedEffect(pendingNearest, fix, s.geometry) {
        val f = fix; val geo = s.geometry
        if (!pendingNearest || f == null || geo == null) return@LaunchedEffect
        val coords = stationCoordinates(sched, index, geo)
        val near = nearestStations(f.latLon, coords, index, 6)
        val dest = pendingDest
        val pick = near.firstOrNull { dest.isEmpty() || reachableStations(sched, index, it.station.id).containsKey(dest) } ?: near.firstOrNull()
        pendingNearest = false
        if (pick != null) setTrip(pick.station.id, if (dest.isEmpty()) destId else dest)
    }
    LaunchedEffect(s.staticVersion) { if (loc.authorized) loc.startTracking(); autoApply(); applyHabit() }

    // ids persisted from another export resolve to today's complex ids
    val origin: Station? = index.station(originId)
    val dest: Station? = index.station(destId)
    val reach: Map<String, Reach> = remember(origin?.id, s.staticVersion) { origin?.let { reachableStations(sched, index, it.id) } ?: emptyMap() }
    val reachableDest = dest?.takeIf { reach.containsKey(it.id) }

    // the routes: enumerated when the stations or the static data change, evaluated against this hour
    val paths: List<PathOption> = remember(origin?.id, reachableDest?.id, s.staticVersion) {
        if (origin == null || reachableDest == null) emptyList() else {
            val now = s.now
            enumeratePaths(sched, index, origin.id, reachableDest.id).onEach {
                evaluate(it, sched, s.lineSched, now, s.holds, s.deviations, Fmt.nyHour(now))
                // the rider's own changes, where learned at the station, replace the MTA's minimum
                val tr = it.transfer
                if (tr != null) { val sec = pace.plannedTransferSec(tr.station, tr.walkSec); if (sec != tr.walkSec) { it.schedSec += sec - tr.walkSec; tr.walkSec = sec } }
            }
        }
    }
    LaunchedEffect(paths) {
        data.setWanted(paths.flatMap { p -> p.legs.flatMap { it.keys } }.toSet(), "planner")
        if (selected == null || paths.none { it.id == selected }) selected = paths.firstOrNull()?.id
        if (paths.isNotEmpty()) { val l = fix?.takeIf { it.ageSec < 600 }; stores.recordUse(originId, destId, s.now, l?.lat, l?.lon) }
    }
    // live itineraries after every poll
    val live: Map<String, List<Itinerary>> = remember(paths, s.tick, s.predictedBoards) {
        paths.associate { it.id to pathTrips(s.predictedBoards, sched, it, s.now, 5) }
    }
    paths.forEach { it.live = live[it.id]?.firstOrNull() }
    val list = ranked(paths)
    val headline = list.firstOrNull { it.id == selected } ?: list.firstOrNull()
    val now = rememberNow(s.demoOffset ?: 0.0)
    // the walk from the phone to the origin station, when both positions are known
    val walk: NearbyStation? = remember(fix, origin?.id, s.geometry) {
        val f = fix; val geo = s.geometry; val o = origin
        if (f == null || geo == null || o == null) null
        else stationCoordinates(sched, index, geo)[o.id]?.let { NearbyStation(o, haversineM(f.latLon, it)) }
    }
    val here: Place? = remember(fix, places) { fix?.let { Place.nearest(places, it.latLon, 150.0) } }
    val commuteCoords = remember(currentPresetId, s.geometry) { val geo = s.geometry; if (currentPresetId != null && geo != null) stationCoordinates(sched, index, geo) else emptyMap() }
    val currentPreset = presets.firstOrNull { it.id == currentPresetId }

    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Which way?", fontSize = 32.sp, fontWeight = FontWeight.Bold)
        Row(verticalAlignment = Alignment.CenterVertically) {
            CommuteChip(presets, stores.activePreset(s.now)?.id, currentPresetId, onPick = { applyPreset(it, byHand = true) }, onEdit = { editing = it },
                onAdd = { val w = CommutePreset.suggestedWindow(s.now); editing = CommutePreset(name = CommutePreset.suggestedName(s.now), originId = originId, destId = destId, startMinute = w.first, endMinute = w.second) })
            Spacer(Modifier.weight(1f))
            Caption(if (s.offline) "offline" else if (s.lastUpdateTs == null) "connecting" else "live")
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.weight(1f)) { StationButton("From", origin) { picking = "from" } }
            IconButton(onClick = { nearbySheet = true }) { Icon(Icons.Filled.LocationOn, "Nearest station") }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.weight(1f)) { StationButton("To", reachableDest, enabled = origin != null) { picking = "to" } }
            IconButton(enabled = origin != null && reachableDest != null, onClick = { val o = originId; setTrip(destId, o); pickedByHand() }) { Icon(Icons.Filled.Refresh, "Swap stations") }
        }
        habitNote?.let { Caption("◷ $it") }
        when {
            pendingNearest -> Caption(locError ?: "Finding the nearest station…")
            origin == null || reachableDest == null -> SetupPrompt(origin != null, reachableDest != null, reach.size) {
                val w = CommutePreset.suggestedWindow(s.now); editing = CommutePreset(name = CommutePreset.suggestedName(s.now), originId = originId, destId = destId, startMinute = w.first, endMinute = w.second)
            }
            paths.isEmpty() -> Caption("No path with at most one change between these stations.")
        }
        if (headline != null && reachableDest != null) {
            NowCard(headline, headline.live, now, origin?.name ?: "", reachableDest.name, walk, here, pace)
            Text(if (list.size == 1) "1 way to get there" else "${list.size} ways to get there", style = MaterialTheme.typography.titleMedium)
            val maxSec = max(60.0, list.maxOf { max(it.expectedSec, it.live?.totalSec ?: 0.0) })
            // the live itinerary is passed on its own: the row must recompose when the poll changes it
            list.forEach { p -> PathRow(p, p.live, p.id == headline.id, maxSec) { selected = p.id } }
            Caption("Bars: expected door-to-door time: wait (grey), ride (line colour), walk at the change (dark). Routes with a train on its way come first, by arrival.")
            val its = live[headline.id].orEmpty()
            if (its.isNotEmpty()) {
                Text("Next trains on ${headline.label}", style = MaterialTheme.typography.titleSmall)
                its.forEach { ItineraryRow(it) }
            }
        }
        s.lastError?.let { Caption(it, Color(0xFFD32F2F)) }
    }

    when (picking) {
        "from" -> StationPicker("From", index.sorted, null, { picking = null }, nearTo = currentPreset?.let { index.station(it.originId) }, coords = commuteCoords, places = placePicks(places, index)) { setTrip(it.id, destId); pickedByHand() }
        "to" -> StationPicker("To", index.sorted, reach, { picking = null }, nearTo = currentPreset?.let { index.station(it.destId) }, coords = commuteCoords, places = placePicks(places, index)) { setTrip(originId, it.id); pickedByHand() }
    }
    if (nearbySheet) NearbyStationsSheet(data, loc, { nearbySheet = false }) { setTrip(it.id, destId); pickedByHand() }
    editing?.let { p -> PresetEditor(data, p, { editing = null }) { saved -> stores.updatePreset(saved); applyPreset(saved, byHand = true) } }
}

/** The train to take, the countdown to it, the change, and the arrival with the engine's 80% window. */
@Composable
private fun NowCard(p: PathOption, itin: Itinerary?, now: Double, originName: String, destName: String, walk: NearbyStation?, here: Place?, pace: com.whichway.core.PersonalModel) {
    Card(tint = MaterialTheme.colorScheme.primary.copy(alpha = 0.14f)) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Caption("Take the ")
                if (itin != null) {
                    RouteBullet(itin.legs[0].train.route, 26.dp)
                    Text(" at ${Fmt.hhmm(itin.boardTs)}", fontSize = 22.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    Text(Fmt.mmss(max(0.0, itin.boardTs - now)), fontSize = 32.sp, fontWeight = FontWeight.Bold)
                } else {
                    RouteBullets(p.legs[0].routes.take(1), 26.dp)
                    Text(" from $originName", fontSize = 22.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            val tr = p.transfer
            if (p.legs.size > 1 && tr != null) {
                Text("Change at ${tr.station} to the ${p.legs[1].routesLabel}", fontWeight = FontWeight.SemiBold)
                val walk = if (tr.walkSec > 0) "${Fmt.mmss(tr.walkSec.toDouble())} walk" else "same platform"
                val m = itin?.connectionMarginSec
                val missed = itin?.nextIfMissedSec?.let { n -> " · +${Fmt.mmss(n)} if missed" } ?: ""
                Caption(if (m != null) "${Fmt.mmss(m)} margin · $walk$missed" else walk,
                    if (m != null && m < 60) Color(0xFFD32F2F) else MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                Text("Direct · ${p.legs[0].nStops} stops", fontWeight = FontWeight.SemiBold)
            }
            if (itin != null) {
                Row(verticalAlignment = Alignment.Bottom) {
                    Text("Arrive $destName ${Fmt.hhmm(itin.arriveTs)}", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Caption(Fmt.minTxt(itin.totalSec))
                }
                itin.legs.last().rangeText?.let { r -> Caption("80% window $r") }
                val l0 = itin.legs[0]
                Caption("${l0.train.position?.text ?: "position unknown"} · ${Fmt.late(l0.train.effectiveLatenessSec)}")
            } else {
                Text("Expected ${Fmt.minTxt(p.expectedSec)} to $destName", style = MaterialTheme.typography.titleMedium)
                Caption("No train for this path in the feeds yet; the expected time stands in.")
            }
            // the walk to the station in the rider's own pace, and whether it fits in the countdown
            if (walk != null) {
                val access = pace.accessSec(walk.station.id) ?: 0.0
                val usual = here?.let { pace.placeToStationSec(it.id, walk.station.id) }
                val walkSec = usual ?: (walk.meters / pace.walkSpeedMPerMin * 60 + access)
                val mins = max(1, (walkSec / 60).toInt())
                val left = itin?.let { max(0.0, it.boardTs - now) }
                val tight = left != null && walkSec > left
                var text = (here?.let { "${it.name}: " } ?: "") + if (usual != null) "usually $mins min" else "${Fmt.miles(walk.meters)} - $mins min"
                if (usual == null && access >= 30) text += " · ${(access / 60).toInt()} min to platform"
                if (tight && left != null) text += " · ${Fmt.miles(pace.walkSpeedMPerMin * max(0.0, left - access) / 60)}"
                Caption((if (tight) "⚠ Walk " else "Walk ") + text, if (tight) Color(0xFFEF6C00) else MaterialTheme.colorScheme.onSurface)
            }
        }
    }
}

@Composable
private fun PathRow(p: PathOption, live: Itinerary?, selected: Boolean, maxSec: Double, onClick: () -> Unit) {
    val border = if (selected) BorderStroke(1.dp, MaterialTheme.colorScheme.primary) else null
    Surface(Modifier.fillMaxWidth().clickable(onClick = onClick), shape = RoundedCornerShape(12.dp), border = border,
        color = if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.12f) else MaterialTheme.colorScheme.surfaceVariant) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                p.legs.forEachIndexed { i, leg ->
                    if (i > 0) Text(" → ", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    RouteBullets(leg.routes, 20.dp)
                }
                Spacer(Modifier.weight(1f))
                Text(Fmt.minTxt(live?.totalSec ?: p.expectedSec), fontWeight = FontWeight.Bold, fontSize = 18.sp)
            }
            Row {
                Text(p.label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Caption(live?.let { "next ${Fmt.hhmm(it.boardTs)} → ${Fmt.hhmm(it.arriveTs)}" } ?: "expected")
            }
            TimeBar(p, maxSec)
        }
    }
}

/** wait (grey), ride (line colour), walk at the change (dark), wait, ride: as on iOS. */
@Composable
private fun TimeBar(p: PathOption, maxSec: Double) {
    val grey = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.22f)
    val segs = ArrayList<Pair<Double, Color>>()
    val l0 = p.legs[0]
    segs.add(p.wait1Sec to grey)
    segs.add(((l0.schedRideSec ?: 0) + (l0.typicalSec ?: 0.0) + l0.holdRiskSec) to routeColor(l0.primaryRoute))
    val tr = p.transfer
    if (p.legs.size > 1 && tr != null) {
        val l1 = p.legs[1]
        if (tr.walkSec > 0) segs.add(tr.walkSec.toDouble() to MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f))
        segs.add(p.wait2Sec to grey)
        segs.add(((l1.schedRideSec ?: 0) + (l1.typicalSec ?: 0.0) + l1.holdRiskSec) to routeColor(l1.primaryRoute))
    }
    // each segment's share of the longest route's time; the rest of the row stays empty
    val shares = segs.map { (sec, c) -> (max(0.0, sec) / maxSec).toFloat().coerceAtLeast(0.004f) to c }
    val rest = 1f - shares.sumOf { it.first.toDouble() }.toFloat()
    Row(Modifier.fillMaxWidth().height(12.dp), horizontalArrangement = Arrangement.spacedBy(1.5.dp)) {
        shares.forEach { (f, c) -> Box(Modifier.weight(f).fillMaxHeight().background(c, RoundedCornerShape(3.dp))) }
        if (rest > 0.001f) Spacer(Modifier.weight(rest))
    }
}

@Composable
private fun ItineraryRow(itin: Itinerary) {
    Card {
        Row {
            Text("${Fmt.hhmm(itin.boardTs)} → ${Fmt.hhmm(itin.arriveTs)}", fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            Text(Fmt.minTxt(itin.totalSec))
            Spacer(Modifier.width(6.dp))
            Caption(Fmt.signed(itin.rideVsSchedSec) + " vs sched", if (itin.rideVsSchedSec > 120) Color(0xFFD32F2F) else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        itin.legs.forEachIndexed { i, leg ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                RouteBullet(leg.train.route, 16.dp)
                Spacer(Modifier.width(6.dp))
                Caption("${leg.train.label} · ${leg.train.position?.text ?: "position unknown"}")
            }
            val m = itin.connectionMarginSec
            if (i == 0 && m != null) Caption("change: ${Fmt.mmss(itin.waitAtTransferSec ?: 0.0)} on the platform · margin ${Fmt.mmss(m)}",
                if (m < 60) Color(0xFFD32F2F) else MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
