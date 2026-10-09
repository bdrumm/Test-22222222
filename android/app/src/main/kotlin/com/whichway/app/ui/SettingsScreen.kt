// Settings (ios/WhichWay/WhichWay/Views/SettingsView.swift): what a rider sets on the first page, the data
// sources and the feed status under Developer. Commutes, places, pace and trip sharing are not ported yet.
package com.whichway.app.ui

import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.whichway.app.store.AppData
import com.whichway.app.store.LocationService
import com.whichway.app.store.Stores
import com.whichway.core.Fmt

/** "0.1 (1)", as iOS shows CFBundleShortVersionString (CFBundleVersion). */
@Composable
private fun versionText(): String {
    val ctx = LocalContext.current
    return remember(ctx) {
        runCatching {
            val info = ctx.packageManager.getPackageInfo(ctx.packageName, PackageManager.PackageInfoFlags.of(0))
            "${info.versionName} (${info.longVersionCode})"
        }.getOrDefault("?")
    }
}

@Composable
fun SettingsScreen(data: AppData, modifier: Modifier = Modifier) {
    var developer by rememberSaveable { mutableStateOf(false) }
    if (developer) {
        BackHandler { developer = false }
        DeveloperScreen(data, modifier) { developer = false }
        return
    }
    Column(modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Settings", style = MaterialTheme.typography.headlineMedium)
        val ctx = LocalContext.current.applicationContext
        val stores = remember(ctx) { Stores.get(ctx) }
        val loc = remember(ctx) { LocationService.get(ctx) }
        CommutesSection(data, stores)
        PlacesSection(data, stores, loc)
        PaceSection(stores)
        SharingSection(stores)
        Card(Modifier.clickable { developer = true }) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Developer", modifier = Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null)
            }
            Caption("Data sources, the feed status and the offline copy.")
        }
        Text("About", style = MaterialTheme.typography.titleMedium)
        Caption("WhichWay reads the MTA GTFS-Realtime feeds directly and layers the published delay analysis on top: the timetable extract for lateness, the hold log for hold risk, per-line deviation grids for the time trains typically lose at this hour. Times are New York local. This Android build is a periodic port of the iOS app, which leads.")
        Status("Version", versionText())
    }
}

/** The data sources, the feed status and the offline copy. */
@Composable
private fun DeveloperScreen(data: AppData, modifier: Modifier, onBack: () -> Unit) {
    val s by data.state.collectAsStateWithLifecycle()
    var base by remember { mutableStateOf(s.baseUrl) }
    var poll by remember { mutableDoubleStateOf(s.pollSec) }
    Column(modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
            Text("Developer", style = MaterialTheme.typography.headlineMedium)
        }
        Text("Data source", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(base, { base = it }, label = { Text("Base URL of the published data") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Poll the feeds every ${poll.toInt()} s", modifier = Modifier.weight(1f))
            TextButton(onClick = { poll = (poll - 5).coerceAtLeast(10.0) }) { Text("−") }
            TextButton(onClick = { poll = (poll + 5).coerceAtMost(120.0) }) { Text("+") }
        }
        Button(onClick = { data.configure(base, poll) }) { Text("Apply and reload") }
        Row {
            TextButton(onClick = { base = AppData.EMULATOR_LOCAL_BASE }) { Text("Local server (emulator)") }
            TextButton(onClick = { base = AppData.PUBLISHED_BASE }) { Text("Published site") }
        }
        Caption("`make serve` in the repository serves the built site on port 8000; scripts/emulator.sh tunnels the device's localhost:8000 to it with adb reverse.")

        Text("Status", style = MaterialTheme.typography.titleMedium)
        val sched = s.schedule
        Status("Schedule", sched?.let { "${it.lines.size} line directions · ${it.serviceDate ?: ""}" } ?: "not loaded")
        Status("Timetable extract", "${s.lineSched.values.sumOf { it.size }} trips")
        Status("Hold log", s.holds?.let { "${it.n} holds" } ?: "–")
        Status("Deviation grids", "${s.deviations.size} lines")
        Status("Prediction engine", s.model?.summary ?: "no tables yet (physical priors)")
        Status("Next poll", if (s.isDemo) "demo clock" else "in ${s.nextPollSec.toInt()} s, aligned to the feed")
        Status("Feeds", s.feeds.keys.sorted().joinToString(", "))
        Status("Alerts", "${s.alerts.size} active")
        Status("Last poll", s.lastUpdateTs?.let { Fmt.hhmmss(it) } ?: "–")
        Status("Offline copy", "${data.cacheSizeKb} KB")
        TextButton(onClick = { data.clearCache() }) { Text("Clear the offline copy") }
        Caption("Every file fetched is kept on the phone: without signal the app keeps planning from the timetable and the saved tables, with the trains where they were last seen.")
        if (s.isDemo) Caption("Demo clock: the schedule pins the current time (demo_now).")
        s.lastError?.let { Caption(it, Color(0xFFD32F2F)) }

        Text("Trips leaving the phone", style = MaterialTheme.typography.titleMedium)
        val session = com.whichway.app.trip.TripSession.get(LocalContext.current.applicationContext)
        val tele = session.telemetry
        val optIn by tele.optIn.collectAsStateWithLifecycle()
        val obs by tele.observations.collectAsStateWithLifecycle()
        val server by tele.server.collectAsStateWithLifecycle()
        val lastSent by tele.lastUpload.collectAsStateWithLifecycle()
        val sendErr by tele.lastUploadError.collectAsStateWithLifecycle()
        var serverField by remember(server) { mutableStateOf(server) }
        Status("Sharing", if (optIn) "on" else "off (Settings › Improve the predictions)")
        Status("Trips recorded", "${obs.size} · ${tele.pendingUpload} to send to a local server · ${tele.pendingGitHub} to the repository")
        OutlinedTextField(serverField, { serverField = it }, label = { Text("Trip server, e.g. http://my-mac.local:8000/") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        TextButton(onClick = { tele.setServer(serverField) }) { Text("Save trip server") }
        Caption("The local server on your Mac (make serve), reached on the home network. Trips are sent when they end and when the app opens; the Mac reviews each one against the trains (data/trips/trip_review.md).")
        Status("Local server", tele.uploadUrl(data.apiBase)?.let { runCatching { java.net.URL(it).host }.getOrNull() } ?: "none")
        lastSent?.let { Status("Last sent there", Fmt.hhmmss(it)) }
        sendErr?.let { Caption(it, Color(0xFFD32F2F)) }
        TextButton(enabled = optIn && tele.uploadUrl(data.apiBase) != null && tele.pendingUpload > 0, onClick = { session.uploadPending() }) { Text("Send to the local server now") }
        if (com.whichway.app.store.TripRelay.configured) {
            val relayLast by com.whichway.app.store.TripRelay.lastUpload.collectAsStateWithLifecycle()
            val relayErr by com.whichway.app.store.TripRelay.lastError.collectAsStateWithLifecycle()
            Text("Trips to WhichWay", style = MaterialTheme.typography.titleMedium)
            Status("Sent through", com.whichway.app.store.TripRelay.host)
            Caption("Each shared trip goes to the WhichWay data repository when it ends and whenever the app opens, over Wi-Fi or cellular, so the predictions can be checked against real rides. It carries the route, the times and the trip's own measurements under this phone's random id; never your location or anything that identifies you.")
            Status("Waiting to send", "${tele.pendingGitHub} trip${if (tele.pendingGitHub == 1) "" else "s"}")
            relayLast?.let { Status("Last sent", Fmt.hhmmss(it)) }
            relayErr?.let { Caption(it, Color(0xFFD32F2F)) }
            TextButton(enabled = optIn && tele.pendingGitHub > 0, onClick = { session.uploadPending() }) { Text("Send now") }
        }
        Text("Route in progress", style = MaterialTheme.typography.titleMedium)
        val trip by session.ui.collectAsStateWithLifecycle()
        Status("Phase", trip.phase?.name ?: "not on a route")
        Status("Motion now", if (trip.phase == null) "–" else "${trip.motionState} · ${trip.motionSeconds} s")
        trip.lastMotion?.let { m -> Status("Last second", String.format(java.util.Locale.US, "step %.4f · push %.3f g · shake %.3f g", m.stepEnergy, m.pushG, m.shakeG)) }
        Status("Platform", trip.platformTs?.let { Fmt.hhmmss(it) } ?: "–")
        Status("Forecast boarding", trip.forecastBoardTs?.let { Fmt.hhmmss(it) } ?: "–")
        Status("Events", "${trip.events}")
        Text("Build", style = MaterialTheme.typography.titleMedium)
        Status("Version", versionText())
        Status("Relay", if (com.whichway.app.store.TripRelay.configured) com.whichway.app.store.TripRelay.host else "none in this build (whichway.tripRelay in local.properties)")
    }
}

@Composable
private fun Status(label: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        Caption(value)
    }
}


/** Sharing trip data: on unless the rider switches it off, and the first launch says so. */
@Composable
fun SharingSection(stores: Stores) {
    val ctx = LocalContext.current.applicationContext
    val session = remember(ctx) { com.whichway.app.trip.TripSession.get(ctx) }
    val tele = session.telemetry
    val optIn by tele.optIn.collectAsStateWithLifecycle()
    val obs by tele.observations.collectAsStateWithLifecycle()
    val relayLast by com.whichway.app.store.TripRelay.lastUpload.collectAsStateWithLifecycle()
    Text("Improve the predictions", style = MaterialTheme.typography.titleMedium)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("Share my trips", modifier = Modifier.weight(1f))
        androidx.compose.material3.Switch(optIn, { tele.setOptIn(it) })
    }
    Caption(SHARING_EXPLANATION)
    if (optIn) {
        Status("Trips shared", "${obs.size - tele.pendingGitHub} sent · ${tele.pendingGitHub} waiting")
        relayLast?.let { Status("Last sent", Fmt.hhmmss(it)) }
        TextButton(enabled = obs.isNotEmpty(), onClick = { tele.deleteAll() }) { Text("Delete collected data", color = Color(0xFFD32F2F)) }
    }
}

const val SHARING_EXPLANATION = "On unless you turn it off. While a route is in progress the phone's motion is summarised once a second to notice when your train pulls away and when you walk off it, so the predictions can be checked against real boardings and changes. Shared: the route's stations and lines, the predicted and observed times, the train's lateness, those moments, and the trip's own measurements (walking pace, time to the platform, time changing trains). Never shared: your location, your places, raw sensor data, or anything that identifies you. A random id groups this phone's trips; switching this off deletes what was collected and resets the id."
