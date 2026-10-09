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
        Card {
            Text("Commutes, places, your pace and trip sharing", style = MaterialTheme.typography.titleSmall)
            Caption("These come to Android with ride tracking. On iPhone they live on this page.")
        }
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

        Text("Build", style = MaterialTheme.typography.titleMedium)
        Status("Version", versionText())
        Status("Trips", "not recorded in the Android build")
    }
}

@Composable
private fun Status(label: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        Caption(value)
    }
}
