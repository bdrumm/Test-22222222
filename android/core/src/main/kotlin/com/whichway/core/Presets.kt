// Port of the model half of ios/WhichWay/WhichWay/Core/Presets.swift: a saved trip the Go tab switches to on its
// own during a time window (a commute). Storage is the app's (SharedPreferences there, UserDefaults on iOS).
package com.whichway.core

import kotlinx.serialization.Serializable
import java.time.Instant
import java.util.UUID

@Serializable
data class CommutePreset(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val originId: String,
    val destId: String,
    val startMinute: Int,          // minutes after midnight, New York time
    val endMinute: Int,
    val weekdaysOnly: Boolean = true,
    val useNearestOrigin: Boolean = false,
) {
    /** weekday: 1 = Sunday … 7 = Saturday, as iOS's Calendar numbers them. */
    fun isActive(minuteOfDay: Int, weekday: Int): Boolean {
        if (weekdaysOnly && (weekday == 1 || weekday == 7)) return false
        if (startMinute == endMinute) return false
        if (startMinute < endMinute) return minuteOfDay in startMinute until endMinute
        return minuteOfDay >= startMinute || minuteOfDay < endMinute
    }

    fun isActive(ts: Double): Boolean {
        val z = Instant.ofEpochMilli((ts * 1000).toLong()).atZone(Fmt.NY)
        return isActive(z.hour * 60 + z.minute, z.dayOfWeek.value % 7 + 1)
    }

    val windowText: String get() = "${Fmt.clock(startMinute)}–${Fmt.clock(endMinute)}${if (weekdaysOnly) " weekdays" else ""}"
}
