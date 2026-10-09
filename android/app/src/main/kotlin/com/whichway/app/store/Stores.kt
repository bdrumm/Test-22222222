// The rider's own data on the phone, as on iOS (UserDefaults and Application Support there): the trip on
// screen, the saved commutes (Core/Presets.swift's PresetStore), the places (Core/Places.swift's PlaceStore),
// the trip history (Services/HabitStore.swift) and the pace model (Services/PersonalModelStore.swift).
package com.whichway.app.store

import android.content.Context
import com.whichway.core.CommutePreset
import com.whichway.core.Fmt
import com.whichway.core.HabitGuess
import com.whichway.core.Habits
import com.whichway.core.NearbyStation
import com.whichway.core.PersonalModel
import com.whichway.core.Place
import com.whichway.core.WWJson
import com.whichway.core.nyFractionalHour
import com.whichway.core.nyWeekday
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.builtins.ListSerializer
import java.io.File

/** One per process; the screens read the flows, the stores write the files. */
class Stores private constructor(context: Context) {
    private val app = context.applicationContext
    private val p = app.getSharedPreferences("whichway", Context.MODE_PRIVATE)
    private val personalDir = File(app.filesDir, "personal").apply { mkdirs() }

    // the trip on screen

    var originId: String
        get() = p.getString("originId", "") ?: ""
        set(v) = p.edit().putString("originId", v).apply()
    var destId: String
        get() = p.getString("destId", "") ?: ""
        set(v) = p.edit().putString("destId", v).apply()
    /** "<day>|<preset id>" of the last commute applied automatically: once per window per day. */
    var appliedPreset: String
        get() = p.getString("appliedPreset", "") ?: ""
        set(v) = p.edit().putString("appliedPreset", v).apply()
    /** When a station was last picked by hand: the history does not override a choice for two hours. */
    var pickedByHandTs: Double
        get() = p.getFloat("pickedByHandTs", 0f).toDouble()
        set(v) = p.edit().putFloat("pickedByHandTs", v.toFloat()).apply()

    // commutes

    private val _presets = MutableStateFlow(
        p.getString("commutePresets", null)?.let { runCatching { WWJson.decodeFromString(ListSerializer(CommutePreset.serializer()), it) }.getOrNull() } ?: emptyList()
    )
    val presets: StateFlow<List<CommutePreset>> = _presets
    private fun savePresets(v: List<CommutePreset>) { _presets.value = v; p.edit().putString("commutePresets", WWJson.encodeToString(ListSerializer(CommutePreset.serializer()), v)).apply() }
    fun updatePreset(c: CommutePreset) {
        val list = _presets.value
        savePresets(if (list.any { it.id == c.id }) list.map { if (it.id == c.id) c else it } else list + c)
    }
    fun removePreset(id: String) = savePresets(_presets.value.filter { it.id != id })
    fun movePreset(id: String, delta: Int) {
        val list = _presets.value.toMutableList()
        val i = list.indexOfFirst { it.id == id }
        val j = i + delta
        if (i < 0 || j < 0 || j >= list.size) return
        list.add(j, list.removeAt(i))
        savePresets(list)
    }
    fun activePreset(ts: Double): CommutePreset? = CommutePreset.active(_presets.value, ts)

    // places

    private val _places = MutableStateFlow(
        p.getString("places", null)?.let { runCatching { WWJson.decodeFromString(ListSerializer(Place.serializer()), it) }.getOrNull() } ?: emptyList()
    )
    val places: StateFlow<List<Place>> = _places
    private fun savePlaces(v: List<Place>) { _places.value = v; p.edit().putString("places", WWJson.encodeToString(ListSerializer(Place.serializer()), v)).apply() }
    fun place(kind: String): Place? = if (kind == Place.CUSTOM) null else _places.value.firstOrNull { it.kind == kind }
    fun updatePlace(pl: Place) {
        val list = _places.value
        savePlaces(when {
            list.any { it.id == pl.id } -> list.map { if (it.id == pl.id) pl else it }
            pl.kind != Place.CUSTOM && list.any { it.kind == pl.kind } -> list.map { if (it.kind == pl.kind) pl else it }
            else -> list + pl
        })
    }
    fun removePlace(id: String) = savePlaces(_places.value.filter { it.id != id })

    // habits

    private val habitsFile = File(personalDir, "habits.json")
    private val _habits = MutableStateFlow(runCatching { WWJson.decodeFromString(Habits.serializer(), habitsFile.readText()) }.getOrDefault(Habits()))
    val habits: StateFlow<Habits> = _habits
    fun recordUse(origin: String, dest: String, ts: Double, lat: Double?, lon: Double?) {
        val next = _habits.value.record(origin, dest, ts, lat, lon)
        if (next !== _habits.value) { _habits.value = next; runCatching { habitsFile.writeText(WWJson.encodeToString(Habits.serializer(), next)) } }
    }
    fun likelyTrip(now: Double, lat: Double?, lon: Double?): HabitGuess? = _habits.value.likelyTrip(nyFractionalHour(now), nyWeekday(now), lat, lon)
    fun resetHabits() { _habits.value = Habits(); habitsFile.delete() }

    // the pace model (read side; learning comes with the trip tracker)

    private val paceFile = File(personalDir, "pace.json")
    private val _pace = MutableStateFlow(runCatching { WWJson.decodeFromString(PersonalModel.serializer(), paceFile.readText()) }.getOrDefault(PersonalModel()))
    val pace: StateFlow<PersonalModel> = _pace
    /** On unless switched off: the pace model never leaves the phone. */
    var learnPace: Boolean
        get() = p.getBoolean("learnPace", true)
        set(v) = p.edit().putBoolean("learnPace", v).apply()
    /** Learns what a finished trip measured, when learning is on. */
    fun learnPace(t: com.whichway.core.TripTimeline): Boolean {
        if (!learnPace) return false
        val m = _pace.value
        val any = m.learn(t)
        if (any) { _pace.value = m.copy(); NearbyStation.speedMPerMin = m.walkSpeedMPerMin; runCatching { paceFile.writeText(WWJson.encodeToString(PersonalModel.serializer(), m)) } }
        return any
    }

    fun resetPace() { _pace.value = PersonalModel(); paceFile.delete(); NearbyStation.speedMPerMin = PersonalModel.DEFAULT_WALK_SPEED }

    init { NearbyStation.speedMPerMin = _pace.value.walkSpeedMPerMin }

    companion object {
        @Volatile private var instance: Stores? = null
        fun get(context: Context): Stores = instance ?: synchronized(this) { instance ?: Stores(context).also { instance = it } }
    }
}
