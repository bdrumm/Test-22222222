// Port of the model in ios/WhichWay/WhichWay/Core/Places.swift: a station the rider calls by name, optionally
// pinned to the spot they set out from. The store is the app's (app/…/store/Stores.kt).
package com.whichway.core

import kotlinx.serialization.Serializable
import java.util.UUID

@Serializable
data class Place(
    val id: String = UUID.randomUUID().toString(),
    val kind: String,           // home | work | custom
    val name: String,
    val stationId: String,
    val lat: Double? = null,
    val lon: Double? = null,
) {
    val isPinned: Boolean get() = lat != null && lon != null
    fun distanceM(to: LatLon): Double? { val a = lat ?: return null; val b = lon ?: return null; return haversineM(LatLon(a, b), to) }

    companion object {
        const val HOME = "home"; const val WORK = "work"; const val CUSTOM = "custom"
        fun title(kind: String) = when (kind) { HOME -> "Home"; WORK -> "Work"; else -> "Place" }
        /** Home and Work first, then the rest by name. */
        fun ordered(places: List<Place>) = places.sortedWith(compareBy<Place> { rank(it) }.thenBy { it.name })
        private fun rank(p: Place) = when (p.kind) { HOME -> 0; WORK -> 1; else -> 2 }
        /** The pinned place within `withinM` of a point, nearest first. */
        fun nearest(places: List<Place>, p: LatLon, withinM: Double): Place? =
            places.mapNotNull { pl -> pl.distanceM(p)?.let { pl to it } }.filter { it.second <= withinM }.minByOrNull { it.second }?.first
    }
}
