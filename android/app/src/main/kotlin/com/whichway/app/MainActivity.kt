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
import com.whichway.app.ui.GoScreen
import com.whichway.app.ui.LineScreen
import com.whichway.app.ui.SettingsScreen

class MainActivity : ComponentActivity() {
    private val data: AppData by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { WhichWayTheme { Tabs(data) } }
    }

    override fun onStart() { super.onStart(); data.start(); data.refreshIfStale() }
    override fun onStop() { super.onStop(); data.stop() }
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
