package com.whichway.app.store

import android.content.Context
import com.whichway.core.CommutePreset
import com.whichway.core.WWJson
import kotlinx.serialization.builtins.ListSerializer

/** The trip on screen and the saved commutes, as on iOS (UserDefaults there). */
class Prefs(context: Context) {
    private val p = context.getSharedPreferences("whichway", Context.MODE_PRIVATE)

    var originId: String
        get() = p.getString("originId", "") ?: ""
        set(v) = p.edit().putString("originId", v).apply()

    var destId: String
        get() = p.getString("destId", "") ?: ""
        set(v) = p.edit().putString("destId", v).apply()

    var presets: List<CommutePreset>
        get() = p.getString("commutePresets", null)?.let { runCatching { WWJson.decodeFromString(ListSerializer(CommutePreset.serializer()), it) }.getOrNull() } ?: emptyList()
        set(v) = p.edit().putString("commutePresets", WWJson.encodeToString(ListSerializer(CommutePreset.serializer()), v)).apply()
}
