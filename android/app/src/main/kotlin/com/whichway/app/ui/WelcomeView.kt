// The first launch (ios/WhichWay/WhichWay/Views/WelcomeView.swift): what the app does. The iOS sheet is mostly
// about trip sharing, which needs ride tracking; that is not in the Android build yet, so the sheet says so
// instead of showing a switch that would control nothing.
package com.whichway.app.ui

import android.content.Context
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Place
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties

object Welcome {
    private const val KEY = "welcomeShown"
    fun shown(ctx: Context) = ctx.getSharedPreferences("whichway", Context.MODE_PRIVATE).getBoolean(KEY, false)
    fun markShown(ctx: Context) = ctx.getSharedPreferences("whichway", Context.MODE_PRIVATE).edit().putBoolean(KEY, true).apply()
}

@Composable
fun WelcomeSheet(onDone: () -> Unit) {
    // like iOS's interactiveDismissDisabled: only the button closes it
    Dialog(onDismissRequest = {}, properties = DialogProperties(dismissOnBackPress = false, dismissOnClickOutside = false)) {
        Surface(shape = RoundedCornerShape(20.dp)) {
            Column(Modifier.verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Place, null, tint = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.width(12.dp))
                    Column {
                        Text("Welcome to WhichWay", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                        Caption("Which train to take, when it really boards, when you really arrive.")
                    }
                }
                Text("WhichWay reads the MTA's live feeds and a model of where trains lose time, ranks every way to your destination and counts down to the train to take.")
                Card {
                    Text("Following your ride is coming", fontWeight = FontWeight.SemiBold)
                    Caption("On iPhone, WhichWay also follows the ride in progress and, if you share your trips, checks the predictions against the trains that actually ran. This Android version plans routes; it does not record or send any trips yet.")
                }
                Button(onClick = onDone, modifier = Modifier.fillMaxWidth()) { Text("Continue") }
            }
        }
    }
}
