// The Android counterpart of ios/WhichWay/WhichWay/Services/Telemetry.swift and TripRelay.swift: the opted-in
// phone's trip records, where they go (the relay the build names, or the local server on the home network) and
// the switch. Sharing is on unless the rider turns it off; the first launch says so.
package com.whichway.app.store

import android.content.Context
import com.whichway.app.BuildConfig
import com.whichway.core.TripObservation
import com.whichway.core.TripTimeline
import com.whichway.core.WWJson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/** Trips and motion traces to the relay: a Cloudflare Worker that writes them into the private data repository. */
object TripRelay {
    val baseUrl: String? = BuildConfig.TRIP_RELAY.takeIf { it.startsWith("https://") }?.let { if (it.endsWith("/")) it else "$it/" }
    val key: String = BuildConfig.RELAY_KEY.trim()
    val configured: Boolean get() = baseUrl != null && key.isNotEmpty()
    val host: String get() = baseUrl?.let { runCatching { URL(it).host }.getOrNull() } ?: ""
    val lastUpload = MutableStateFlow<Double?>(null)
    val lastError = MutableStateFlow<String?>(null)

    enum class Kind(val path: String) { trip("trips"), trace("traces") }

    /** One document to the relay; the same document again is fine. Throws with a readable reason. */
    fun put(kind: Kind, body: ByteArray, installId: String) {
        val base = baseUrl ?: throw IOException("no relay in this build")
        if (key.isEmpty()) throw IOException("no app key in this build")
        val c = URL(base + "v1/" + kind.path).openConnection() as HttpURLConnection
        c.requestMethod = "POST"; c.connectTimeout = 30_000; c.readTimeout = 30_000; c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json")
        c.setRequestProperty("X-WhichWay-Key", key)
        c.setRequestProperty("X-WhichWay-Install", installId)
        try {
            c.outputStream.use { it.write(body) }
            val code = c.responseCode
            if (code == 200 || code == 201) return
            val text = runCatching { (c.errorStream ?: c.inputStream).use { String(it.readBytes()) } }.getOrDefault("")
            val why = runCatching { Json.parseToJsonElement(text).jsonObject["error"]?.jsonPrimitive?.content }.getOrNull() ?: "HTTP $code"
            throw IOException(if (code == 401) "the relay refused the app key" else why)
        } finally { c.disconnect() }
    }

    fun noteResult(e: Throwable?) { if (e != null) lastError.value = e.message ?: "failed" else { lastUpload.value = System.currentTimeMillis() / 1000.0; lastError.value = null } }
}

class Telemetry private constructor(context: Context) {
    private val app = context.applicationContext
    private val p = app.getSharedPreferences("whichway", Context.MODE_PRIVATE)
    private val file = File(app.filesDir, "telemetry/observations.json").apply { parentFile?.mkdirs() }
    private val _optIn = MutableStateFlow(p.getBoolean("telemetryOptIn", true))
    val optIn: StateFlow<Boolean> = _optIn
    var installId: String = p.getString("telemetryInstallId", null) ?: UUID.randomUUID().toString().also { p.edit().putString("telemetryInstallId", it).apply() }
        private set
    private val _observations = MutableStateFlow(runCatching { WWJson.decodeFromString(ListSerializer(TripObservation.serializer()), file.readText()) }.getOrDefault(emptyList()))
    val observations: StateFlow<List<TripObservation>> = _observations
    var current: TripObservation? = null; private set
    val lastUpload = MutableStateFlow<Double?>(null)
    val lastUploadError = MutableStateFlow<String?>(null)
    /** The local trip server (the Mac on the home network), from Settings or the build; empty: the data server when it is one. */
    val server = MutableStateFlow(p.getString("tripServer", null) ?: BuildConfig.TRIP_SERVER.takeIf { it.startsWith("http") } ?: "")

    val pendingUpload: Int get() = _observations.value.count { it.uploaded != true }
    val pendingGitHub: Int get() = _observations.value.count { it.uploadedGitHub != true }

    private fun save() { runCatching { file.writeText(WWJson.encodeToString(ListSerializer(TripObservation.serializer()), _observations.value)) } }

    /** Switching off drops the trip in hand, deletes everything collected and rotates the id. */
    fun setOptIn(on: Boolean) {
        if (on == _optIn.value) return
        _optIn.value = on
        p.edit().putBoolean("telemetryOptIn", on).apply()
        if (!on) {
            current = null
            deleteAll()
            installId = UUID.randomUUID().toString()
            p.edit().putString("telemetryInstallId", installId).apply()
        }
    }

    fun beginTrip(base: TripObservation) {
        if (!_optIn.value) return
        current = base.copy(id = UUID.randomUUID().toString(), installId = installId, createdTs = System.currentTimeMillis() / 1000.0)
    }

    /** The forecast as it stands; frozen once the sensors have seen a departure. */
    fun updatePrediction(boardTs: Double?, arriveTs: Double?, expectedSec: Double, extraMin: Int, trainLateSec: Double?, held: Boolean, offline: Boolean, departed: Boolean) {
        val o = current ?: return
        if (departed) return
        o.predictedBoardTs = boardTs; o.predictedArriveTs = arriveTs; o.expectedSec = expectedSec; o.extraMin = extraMin
        o.trainLateSec = trainLateSec; o.trainHeld = held; o.offline = offline
    }

    fun updateRoute(label: String, legs: List<TripObservation.Leg>, transferStation: String?, transferWalkSec: Int?) {
        val o = current ?: return
        o.routeLabel = label; o.legs = legs; o.transferStation = transferStation; o.transferWalkSec = transferWalkSec
    }

    /** The trip is over: kept when it saw anything; returns whether it was. The uploads are the caller's. */
    fun endTrip(tl: TripTimeline?): Boolean {
        val o = current ?: return false
        current = null
        tl?.let { o.complete(it) }
        if (!o.worthKeeping) return false
        _observations.value = _observations.value + o
        save()
        return true
    }

    fun setServer(s: String) { server.value = s.trim(); p.edit().putString("tripServer", server.value).apply() }

    /** The server to send to: the trip server when one is set, else the data server (null on the published site). */
    fun uploadUrl(fallbackApi: String?): String? {
        val s = server.value
        if (s.isEmpty()) return fallbackApi
        var u = s
        if (!u.startsWith("http")) u = "http://$u"
        if (!u.endsWith("/")) u += "/"
        return u
    }

    /** POSTs the observations not sent yet to the server's /api/telemetry. */
    suspend fun upload(api: String?) {
        val base = api ?: return
        val pending = _observations.value.filter { it.uploaded != true }
        if (pending.isEmpty()) return
        val r = withContext(Dispatchers.IO) {
            runCatching {
                val c = URL(base + "api/telemetry").openConnection() as HttpURLConnection
                c.requestMethod = "POST"; c.connectTimeout = 20_000; c.readTimeout = 20_000; c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                try {
                    c.outputStream.use { it.write(WWJson.encodeToString(ListSerializer(TripObservation.serializer()), pending).toByteArray()) }
                    if (c.responseCode != 200) throw IOException("HTTP ${c.responseCode}")
                } finally { c.disconnect() }
            }
        }
        r.onSuccess {
            val sent = pending.map { it.id }.toSet()
            _observations.value.forEach { if (it.id in sent) it.uploaded = true }
            _observations.value = _observations.value.toList()
            lastUpload.value = System.currentTimeMillis() / 1000.0; lastUploadError.value = null
            save()
        }.onFailure { lastUploadError.value = "Could not send: ${it.message}" }
    }

    /** Every trip not yet in the data repository, one file each, through the relay. */
    suspend fun uploadToRelay() {
        if (!_optIn.value || !TripRelay.configured) return
        val pretty = Json(WWJson) { prettyPrint = true }
        for (o in _observations.value.filter { it.uploadedGitHub != true }) {
            val copy = o.copy(uploaded = null, uploadedGitHub = null)
            val r = withContext(Dispatchers.IO) { runCatching { TripRelay.put(TripRelay.Kind.trip, pretty.encodeToString(TripObservation.serializer(), copy).toByteArray(), installId) } }
            if (r.isFailure) { TripRelay.noteResult(r.exceptionOrNull()); return }
            o.uploadedGitHub = true
            _observations.value = _observations.value.toList()
            save()
            TripRelay.noteResult(null)
        }
    }

    fun deleteAll() {
        _observations.value = emptyList()
        lastUpload.value = null; lastUploadError.value = null
        file.delete()
    }

    companion object {
        @Volatile private var instance: Telemetry? = null
        fun get(context: Context): Telemetry = instance ?: synchronized(this) { instance ?: Telemetry(context).also { instance = it } }
    }
}
