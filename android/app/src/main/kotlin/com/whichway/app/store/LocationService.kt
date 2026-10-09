// The Android counterpart of ios/WhichWay/WhichWay/Services/LocationService.swift: one-shot "where am I" for the
// nearest station, and continuous fixes while the app is open (a route in progress will need a foreground
// service; that comes with the trip tracker). No Play Services: the platform LocationManager is enough.
package com.whichway.app.store

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Looper
import androidx.core.content.ContextCompat
import com.whichway.core.LatLon
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

data class Fix(val lat: Double, val lon: Double, val accuracyM: Double, val ts: Double) {
    val latLon get() = LatLon(lat, lon)
    val ageSec get() = System.currentTimeMillis() / 1000.0 - ts
}

class LocationService private constructor(context: Context) {
    private val app = context.applicationContext
    private val lm = app.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private val _fix = MutableStateFlow<Fix?>(null)
    val fix: StateFlow<Fix?> = _fix
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error
    /** True while a permission prompt should be shown; the activity watches it. */
    val needsPermission = MutableStateFlow(false)
    private var tracking = false

    val authorized: Boolean
        get() = ContextCompat.checkSelfPermission(app, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(app, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    /** A new fix replaces the current one unless the current one is both fresh and more accurate (a coarse
     *  network fix must not overwrite a GPS fix from a moment ago). */
    private fun accept(l: Location) {
        val cur = _fix.value
        val f = Fix(l.latitude, l.longitude, l.accuracy.toDouble(), l.time / 1000.0)
        if (cur != null && cur.ageSec < 30 && cur.accuracyM < f.accuracyM && f.ts <= cur.ts + 30) return
        _fix.value = f
        _error.value = null
    }
    private val listener = LocationListener { accept(it) }

    /** The last fix if it is fresh and sure enough to act on. */
    fun fresh(maxAgeSec: Double = 30.0, maxAccuracyM: Double = 100.0): Fix? = _fix.value?.takeIf { it.ageSec <= maxAgeSec && it.accuracyM <= maxAccuracyM }

    /** Ask for a fix (and for permission first when it was never granted). A recent fix is kept. */
    @SuppressLint("MissingPermission")
    fun request() {
        _error.value = null
        if (!authorized) { needsPermission.value = true; return }
        _fix.value?.let { if (it.ageSec < 60) return }
        // the most accurate of the providers' recent last-known fixes
        val now = System.currentTimeMillis()
        providers().mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }.filter { now - it.time < 120_000 }
            .minByOrNull { it.accuracy }?.let { accept(it) }
        startTracking()
    }

    /** Fixes while the app is in front: a fix every ten metres moved, like the iOS tracking mode. */
    @SuppressLint("MissingPermission")
    fun startTracking() {
        if (!authorized) { needsPermission.value = true; return }
        if (tracking) return
        val provs = providers()
        if (provs.isEmpty()) { _error.value = "Location is off on this device. Turn it on in Settings to pick the nearest station."; return }
        for (pv in provs) runCatching { lm.requestLocationUpdates(pv, 5_000L, 10f, listener, Looper.getMainLooper()) }
        tracking = true
    }

    fun stopTracking() {
        if (!tracking) return
        runCatching { lm.removeUpdates(listener) }
        tracking = false
    }

    fun permissionResult(granted: Boolean) {
        needsPermission.value = false
        if (granted) { _error.value = null; request() }
        else _error.value = "Location access is off for WhichWay. Allow it in Settings to pick the nearest station."
    }

    private fun providers(): List<String> = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER).filter { runCatching { lm.isProviderEnabled(it) }.getOrDefault(false) }

    companion object {
        @Volatile private var instance: LocationService? = null
        fun get(context: Context): LocationService = instance ?: synchronized(this) { instance ?: LocationService(context).also { instance = it } }
        fun distanceM(a: Location, b: Location) = a.distanceTo(b).toDouble()
    }
}
