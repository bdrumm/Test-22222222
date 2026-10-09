// The Android counterpart of ios/WhichWay/WhichWay/Services/DataService.swift (+ DiskCache.swift): loads the
// published data, polls the MTA feeds the visible screens need aligned to their 30-second publication, runs the
// prediction engine, and keeps the last good copy of every file on disk for use without signal.
package com.whichway.app.store

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.whichway.app.BuildConfig
import com.whichway.core.Alerts
import com.whichway.core.ClientGeometry
import com.whichway.core.ClientLines
import com.whichway.core.ClientModel
import com.whichway.core.ClientSchedule
import com.whichway.core.Fmt
import com.whichway.core.GtfsRealtime
import com.whichway.core.HoldsSummary
import com.whichway.core.LineBoard
import com.whichway.core.LineDeviation
import com.whichway.core.LineHistory
import com.whichway.core.LinePrediction
import com.whichway.core.LineSchedEntry
import com.whichway.core.Predictor
import com.whichway.core.RTFeed
import com.whichway.core.RouteAlert
import com.whichway.core.StationIndex
import com.whichway.core.TrainPoint
import com.whichway.core.VehicleHistory
import com.whichway.core.WWJson
import com.whichway.core.lineBoard
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Everything the screens read, as one immutable snapshot. */
data class AppState(
    val baseUrl: String,
    val pollSec: Double,
    val schedule: ClientSchedule? = null,
    val index: StationIndex? = null,
    val lineSched: Map<String, List<LineSchedEntry>> = emptyMap(),
    val holds: HoldsSummary? = null,
    val model: ClientModel? = null,
    val deviations: Map<String, LineDeviation> = emptyMap(),
    /** Stop coordinates and tracks (client_geometry.json), fetched the first time something needs it. */
    val geometry: ClientGeometry? = null,
    val feeds: Map<String, RTFeed> = emptyMap(),
    val boards: Map<String, LineBoard> = emptyMap(),
    val predictions: Map<String, Map<String, LinePrediction>> = emptyMap(),
    /** Boards whose points are the engine's ETAs for the scenario (the planner reads these). */
    val predictedBoards: Map<String, LineBoard> = emptyMap(),
    val alerts: List<RouteAlert> = emptyList(),
    val demoOffset: Double? = null,
    val lastUpdateTs: Double? = null,
    val lastFeedTs: Double = 0.0,
    val nextPollSec: Double = 30.0,
    val lastError: String? = null,
    val offline: Boolean = false,
    val loading: Boolean = false,
    /** Bumps after every poll; the screens recompute live itineraries on it. */
    val tick: Int = 0,
    /** Bumps when static data (re)loads; the screens recompute paths on it. */
    val staticVersion: Int = 0,
) {
    val now: Double get() = System.currentTimeMillis() / 1000.0 + (demoOffset ?: 0.0)
    val isDemo: Boolean get() = demoOffset != null
}

/** The last successful fetch of each file, one folder per data source (base URL). */
class DiskCache(context: Context, baseUrl: String) {
    val dir: File = File(context.filesDir, "cache/" + sha8(baseUrl)).apply { mkdirs() }
    private fun file(path: String) = File(dir, path.replace("/", "__").replace(":", "_"))
    fun write(path: String, data: ByteArray) {
        runCatching { val tmp = File(dir, ".tmp-" + file(path).name); tmp.writeBytes(data); tmp.renameTo(file(path)) }
    }
    fun read(path: String): ByteArray? = runCatching { file(path).takeIf { it.exists() }?.readBytes() }.getOrNull()
    fun clear() { dir.deleteRecursively(); dir.mkdirs() }
    val sizeBytes: Long get() = dir.listFiles()?.sumOf { it.length() } ?: 0L

    companion object {
        fun sha8(s: String): String = MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).take(8).joinToString("") { "%02x".format(it) }
    }
}

class AppData(app: Application) : AndroidViewModel(app) {
    companion object {
        val PUBLISHED_BASE: String = BuildConfig.PUBLISHED_URL
        /** Where a fresh install starts: the published site, or in a Debug build the local server named in local.properties. */
        val DEFAULT_BASE: String = BuildConfig.DEFAULT_URL
        /** `make serve` on the Mac, reached through `adb reverse tcp:8000 tcp:8000` (scripts/emulator.sh). */
        const val EMULATOR_LOCAL_BASE = "http://localhost:8000/data/"
        const val FEED_PERIOD_SEC = 30.0
        /** The live view model, for the trip session (one per process). */
        @Volatile var current: AppData? = null
        /**
         * Where the published data has been reachable. Pages may publish the source branch instead of gh-pages
         * (then bdrumm.github.io/whichway/data/ is a 404 while the data sits on gh-pages); a 404 from one of these
         * is retried on the others, and the one that answers is used for the session. iOS keeps the same kind of
         * list (DataService.publishedBases) for the repository rename.
         */
        val PUBLISHED_BASES = listOf(PUBLISHED_BASE, "https://raw.githubusercontent.com/bdrumm/whichway/gh-pages/data/", "https://bdrumm.github.io/Test-22222222/data/")
    }

    class HttpStatus(val code: Int) : IOException("HTTP $code")

    private val prefs = app.getSharedPreferences("whichway", Context.MODE_PRIVATE)
    private val _state = MutableStateFlow(
        AppState(baseUrl = prefs.getString("baseURL", null) ?: DEFAULT_BASE, pollSec = prefs.getFloat("pollSec", 30f).toDouble().coerceAtLeast(10.0))
    )
    val state: StateFlow<AppState> = _state

    private var cache = DiskCache(app, cacheKey(_state.value.baseUrl))

    /** The published bases share one cache folder, so a switch between them keeps the offline copy. */
    private fun cacheKey(base: String): String {
        var b = base.trim()
        if (!b.endsWith("/")) b += "/"
        return if (b in PUBLISHED_BASES) PUBLISHED_BASE else b
    }
    private val history = VehicleHistory()
    private val wantedBy = HashMap<String, Set<String>>()
    private var wanted: Set<String> = emptySet()
    private val deviationRequested = HashSet<String>()
    private var pollJob: Job? = null
    private var pollCount = 0
    private var staleRuns = 0
    var scenario: String = "baseline"
        private set

    init { start(); current = this }

    // network

    private fun url(path: String): String {
        if (path.startsWith("http://") || path.startsWith("https://")) return path
        var base = _state.value.baseUrl.trim()
        if (!base.endsWith("/")) base += "/"
        return base + path
    }

    private fun get(u: String, timeoutMs: Int = 25_000): ByteArray {
        val c = URL(u).openConnection() as HttpURLConnection
        c.connectTimeout = timeoutMs
        c.readTimeout = timeoutMs
        c.useCaches = false
        try {
            if (c.responseCode >= 400) throw HttpStatus(c.responseCode)
            return c.inputStream.use { it.readBytes() }
        } finally { c.disconnect() }
    }

    /** A published file, or without signal the copy from the last successful fetch. */
    private suspend fun fetch(path: String): ByteArray = withContext(Dispatchers.IO) {
        try {
            val d = try { get(url(path)) } catch (e: HttpStatus) { if (e.code == 404) fetchMoved(path) ?: throw e else throw e }
            d.also { cache.write(path, it); _state.update { s -> s.copy(offline = false) } }
        } catch (e: Exception) {
            val d = cache.read(path) ?: throw e
            _state.update { s -> s.copy(offline = true) }
            d
        }
    }

    /** The published data answered 404: try where else it has been reachable, and stay there for the session. */
    private fun fetchMoved(path: String): ByteArray? {
        var base = _state.value.baseUrl.trim()
        if (!base.endsWith("/")) base += "/"
        if (base !in PUBLISHED_BASES || path.startsWith("http")) return null
        for (other in PUBLISHED_BASES) {
            if (other == base) continue
            val d = runCatching { get(other + path) }.getOrNull() ?: continue
            _state.update { it.copy(baseUrl = other) }
            return d
        }
        return null
    }

    private suspend fun fetchText(path: String) = String(fetch(path), Charsets.UTF_8)

    // static data

    private suspend fun loadStatic() {
        _state.update { it.copy(loading = true) }
        val sched = try {
            ClientSchedule.parse(fetchText("client_schedule.json"))
        } catch (e: Exception) {
            _state.update { it.copy(loading = false, lastError = "Could not load the schedule from ${it.baseUrl}: ${e.message}") }
            return
        }
        val index = withContext(Dispatchers.Default) { StationIndex(sched) }
        val lines = runCatching { ClientLines.parse(fetchText("client_lines.json")) }.getOrDefault(emptyMap())
        val holds = runCatching { WWJson.decodeFromString(HoldsSummary.serializer(), fetchText("holds.json")) }.getOrNull()
        val model = runCatching { WWJson.decodeFromString(ClientModel.serializer(), fetchText("client_model.json")) }.getOrNull()
        deviationRequested.clear()
        _state.update {
            it.copy(schedule = sched, index = index, lineSched = lines, holds = holds, model = model, deviations = emptyMap(), loading = false, lastError = null,
                demoOffset = sched.demoNow?.let { d -> d - System.currentTimeMillis() / 1000.0 }, staticVersion = it.staticVersion + 1)
        }
        wanted.forEach(::requestDeviation)
    }

    private var geometryRequested = false

    fun requestGeometry() {
        if (geometryRequested) return
        geometryRequested = true
        viewModelScope.launch {
            val g = runCatching { WWJson.decodeFromString(ClientGeometry.serializer(), fetchText("client_geometry.json")) }.getOrNull()
            if (g == null) { geometryRequested = false; return@launch }
            _state.update { it.copy(geometry = g, staticVersion = it.staticVersion + 1) }
        }
    }

    /** The deviation grid of one line (typical time lost per stop by hour), fetched once per line on demand. */
    private fun requestDeviation(key: String) {
        if (!deviationRequested.add(key)) return
        viewModelScope.launch {
            val dev = runCatching { WWJson.decodeFromString(LineHistory.serializer(), fetchText("lines/$key.json")).deviation }.getOrNull() ?: return@launch
            _state.update { it.copy(deviations = it.deviations + (key to dev), staticVersion = it.staticVersion + 1) }
        }
    }

    // polling

    private fun nextDelay(): Double {
        val s = _state.value
        val interval = maxOf(10.0, s.pollSec)
        val wall = System.currentTimeMillis() / 1000.0
        if (s.lastFeedTs == 0.0 || s.isDemo || wall - s.lastFeedTs > 120) return interval
        if (staleRuns in 1..4) return 5.0
        return (s.lastFeedTs + FEED_PERIOD_SEC + 1.5 - wall).coerceIn(4.0, interval)
    }

    fun start() {
        if (pollJob?.isActive == true) return
        pollJob = viewModelScope.launch {
            while (isActive) {
                if (_state.value.schedule == null) loadStatic()
                if (_state.value.schedule != null) poll()
                val secs = if (_state.value.schedule == null) 15.0 else nextDelay()
                _state.update { it.copy(nextPollSec = secs) }
                delay((secs * 1000).toLong())
            }
        }
    }

    fun stop() { pollJob?.cancel(); pollJob = null }

    /** Coming back to the foreground: poll at once when the last poll is older than half a feed period. */
    fun refreshIfStale() {
        val s = _state.value
        if (s.schedule == null) return
        val last = s.lastUpdateTs
        if (last != null && System.currentTimeMillis() / 1000.0 - last < FEED_PERIOD_SEC / 2) return
        viewModelScope.launch { poll() }
    }

    /** Change the data source or the interval, persist both and reload. */
    fun configure(baseUrl: String, pollSec: Double) {
        val b = baseUrl.trim()
        prefs.edit().putString("baseURL", b).putFloat("pollSec", pollSec.toFloat()).apply()
        stop()
        cache = DiskCache(getApplication(), cacheKey(b))
        history.reset()
        staleRuns = 0
        geometryRequested = false
        _state.value = AppState(baseUrl = b, pollSec = maxOf(10.0, pollSec))
        start()
    }

    fun clearCache() = cache.clear()
    val cacheSizeKb: Long get() = cache.sizeBytes / 1024

    /** A screen declares the line keys it shows; feeds not fetched yet are polled at once. */
    fun setWanted(keys: Set<String>, screen: String) {
        wantedBy[screen] = keys
        val all = wantedBy.values.flatten().toSet()
        if (all == wanted) return
        wanted = all
        all.forEach(::requestDeviation)
        rebuild()
        if (neededFeedKeys().any { it !in _state.value.feeds }) viewModelScope.launch { poll() }
    }

    fun setScenario(s: String) {
        if (s == scenario) return
        scenario = s
        _state.update { it.copy(predictedBoards = predictedBoards(it.boards, it.predictions, s)) }
    }

    private fun neededFeedKeys(): Set<String> {
        val s = _state.value.schedule ?: return emptySet()
        if (wanted.isEmpty()) return s.targetFeeds.toSet()
        return wanted.mapNotNull { s.routeFeeds[it.substringBefore("_")] }.toSet()
    }

    private suspend fun poll() {
        val sched = _state.value.schedule ?: return
        val jobs = neededFeedKeys().mapNotNull { k -> sched.feeds[k]?.let { k to url(it) } }
        val results = withContext(Dispatchers.IO) {
            jobs.map { (k, u) ->
                async {
                    try {
                        val data = get(u, 20_000)
                        Triple(k, GtfsRealtime.parse(data), null as String?).also { cache.write("feed/$k", data) }
                    } catch (e: Exception) { Triple(k, null, "$k: ${e.message}") }
                }
            }.awaitAll()
        }
        val got = HashMap<String, RTFeed>()
        val errs = ArrayList<String>()
        var fetched = 0
        for ((k, f, err) in results) { if (f != null) { got[k] = f; fetched++ }; if (err != null) errs.add(err) }
        // without signal: the last snapshot on disk for any feed never fetched this session
        for ((k, _) in jobs) if (k !in got && k !in _state.value.feeds) {
            cache.read("feed/$k")?.let { d -> runCatching { GtfsRealtime.parse(d) }.getOrNull()?.let { got[k] = it } }
        }
        val offline = fetched == 0 && jobs.isNotEmpty()
        val maxTs = got.values.mapNotNull { it.timestamp }.maxOrNull() ?: 0.0
        val prevTs = _state.value.lastFeedTs
        if (maxTs > 0) staleRuns = if (maxTs <= prevTs) staleRuns + 1 else 0
        pollCount++
        var alerts = _state.value.alerts
        val au = sched.alertsUrl
        if (pollCount % 4 == 1 && au != null) {
            val now = _state.value.now
            val d = withContext(Dispatchers.IO) { runCatching { get(url(au)).also { cache.write("alerts", it) } }.getOrNull() } ?: if (alerts.isEmpty()) cache.read("alerts") else null
            if (d != null) alerts = Alerts.parse(String(d, Charsets.UTF_8), now)
        }
        _state.update {
            it.copy(feeds = it.feeds + got, lastFeedTs = maxOf(it.lastFeedTs, maxTs), alerts = alerts, offline = offline,
                lastUpdateTs = System.currentTimeMillis() / 1000.0,
                lastError = if (offline) "No signal · trains as last seen; times come from the timetable and the saved tables." else errs.takeIf { e -> e.isNotEmpty() }?.joinToString(" · "))
        }
        rebuild()
        _state.update { it.copy(tick = it.tick + 1) }
    }

    private fun rebuild() {
        val s = _state.value
        val sched = s.schedule ?: return
        val now = s.now
        val boards = HashMap<String, LineBoard>()
        for (k in wanted) lineBoard(sched, s.lineSched[k] ?: emptyList(), s.feeds, k, now, history)?.let { boards[k] = it }
        val preds = boards.mapNotNull { (k, b) -> sched.lines[k]?.let { k to Predictor.predictBoard(b, it, s.model, now) } }.toMap()
        _state.update { it.copy(boards = boards, predictions = preds, predictedBoards = predictedBoards(boards, preds, scenario)) }
    }

    /** Each train's points replaced by the engine's ETAs for a scenario, keeping the feed's own. */
    private fun predictedBoards(boards: Map<String, LineBoard>, preds: Map<String, Map<String, LinePrediction>>, scenario: String): Map<String, LineBoard> =
        boards.mapValues { (k, b) ->
            val lp = preds[k]?.let { it[scenario] ?: it["baseline"] } ?: return@mapValues b
            b.copy(trains = b.trains.map { t ->
                val p = lp.train(t.tripId)
                if (p == null || p.points.isEmpty()) t
                else t.copy(feedPoints = t.points, points = p.points.map { TrainPoint(it.idx, it.etaTs) }, pred = p)
            })
        }

    fun statusLine(): String {
        val s = _state.value
        return "Last poll ${s.lastUpdateTs?.let { Fmt.hhmmss(it) } ?: "–"} · ${s.feeds.size} feeds · ${s.alerts.size} alerts"
    }
}
