package com.whichway.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import com.whichway.app.ui.Welcome
import com.whichway.app.ui.WelcomeSheet
import com.whichway.app.store.AppData
import com.whichway.app.store.LocationService
import com.whichway.app.trip.TripSession
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.whichway.app.ui.GoScreen
import com.whichway.app.ui.LineScreen
import com.whichway.app.ui.SettingsScreen

class MainActivity : ComponentActivity() {
    private val data: AppData by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { WhichWayTheme { Tabs(data) } }
        lifecycleScope.launch { loc.fix.collect { f -> if (f != null) session.onFix(f) } }
    }

    private val loc by lazy { LocationService.get(this) }

    private val session by lazy { TripSession.get(this) }

    override fun onStart() { super.onStart(); data.start(); data.refreshIfStale(); if (loc.authorized) loc.startTracking() }
    // a route in progress keeps polling and the fixes coming (the foreground service holds them)
    override fun onStop() { super.onStop(); if (!session.started) { data.stop(); loc.stopTracking() } }
}

/** The iOS accent colour (AccentColor.colorset): MTA blue, lighter in dark mode. */
@Composable
fun WhichWayTheme(content: @Composable () -> Unit) {
    val scheme = if (isSystemInDarkTheme()) darkColorScheme(primary = Color(0xFF61A3FF)) else lightColorScheme(primary = Color(0xFF0039A6))
    MaterialTheme(colorScheme = scheme, content = content)
}

@Composable
private fun Tabs(data: AppData) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    // the first launch: the welcome sheet, once
    val ctx = LocalContext.current
    var welcome by rememberSaveable { mutableStateOf(!Welcome.shown(ctx)) }
    if (welcome) WelcomeSheet { Welcome.markShown(ctx); welcome = false }
    // the location permission, asked the first time a screen needs a fix
    val loc = LocationService.get(ctx)
    val needs by loc.needsPermission.collectAsStateWithLifecycle()
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { r -> loc.permissionResult(r.values.any { it }) }
    LaunchedEffect(needs) { if (needs) ask.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)) }
    // the notification permission, asked when the first route starts
    val session = TripSession.get(ctx)
    val needsNotif by session.needsNotificationPermission.collectAsStateWithLifecycle()
    val askNotif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { session.needsNotificationPermission.value = false }
    LaunchedEffect(needsNotif) { if (needsNotif && android.os.Build.VERSION.SDK_INT >= 33) askNotif.launch(Manifest.permission.POST_NOTIFICATIONS) }
    Scaffold(bottomBar = {
        NavigationBar {
            NavigationBarItem(selected = tab == 0, onClick = { tab = 0 }, icon = { Icon(Icons.Filled.Place, null) }, label = { Text("Go") })
            NavigationBarItem(selected = tab == 1, onClick = { tab = 1 }, icon = { Icon(Icons.AutoMirrored.Filled.List, null) }, label = { Text("Line") })
            NavigationBarItem(selected = tab == 2, onClick = { tab = 2 }, icon = { Icon(Icons.Filled.Settings, null) }, label = { Text("Settings") })
        }
    }) { pad ->
        val m = Modifier.padding(pad)
        when (tab) {
            0 -> GoScreen(data, m)
            1 -> LineScreen(data, m)
            else -> SettingsScreen(data, m)
        }
    }
}
