// Port of ios/WhichWay/WhichWay/Core/PlatformTiming.swift: how the feed's times relate to what a rider lives at
// the platform, by line (measured Oct 7 2026; see the Swift file for the derivation).
package com.whichway.core

object PlatformTiming {
    /** Seconds from the train reaching the platform to the feed's last time for that stop. */
    val recordedLagByRoute: Map<String, Double> = mapOf(
        "1" to 30.0, "2" to 30.0, "3" to 30.0, "4" to 30.0, "5" to 30.0, "6" to 30.0, "6X" to 30.0, "7" to 30.0, "7X" to 35.0, "GS" to 30.0,
        "L" to 30.0, "G" to 60.0,
        "A" to 75.0, "C" to 70.0, "E" to 80.0, "B" to 70.0, "D" to 80.0, "F" to 80.0, "FX" to 80.0, "M" to 85.0,
        "N" to 85.0, "Q" to 95.0, "R" to 90.0, "W" to 95.0, "J" to 75.0, "Z" to 75.0, "FS" to 30.0, "H" to 50.0, "SI" to 30.0,
    )
    const val DEFAULT_RECORDED_LAG = 60.0
    /** The doors stay open about this long. */
    const val DWELL_SEC = 40.0

    fun recordedLag(route: String) = recordedLagByRoute[route] ?: DEFAULT_RECORDED_LAG
    fun atPlatform(feedTs: Double, route: String) = feedTs - recordedLag(route)
    fun pullsAway(feedTs: Double, route: String) = atPlatform(feedTs, route) + DWELL_SEC
}
